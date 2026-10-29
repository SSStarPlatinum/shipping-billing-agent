"""端到端流水线。

    文件 → 解析（EML/PDF/图片/Excel/Word，附件递归）
         → 抽取（规则 ∥ LLM，交叉校验）
         → 标准化（费用名/币种/付款方式）
         → 校验（金额/币种/合计/OCR 混淆/重复）
         → 匹配（4 级规则）
         → 记账方向（AR/AP）
         → 增量写入台账
         → 标准 JSON + 审计链 + 运行报告
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .agent import build_record_chain
from .config import Settings
from .extract.document_extractor import DocumentExtractor
from .extract.llm_extractor import LlmExtractor
from .harness.report import build_markdown, write_markdown
from .harness.resilience import retry
from .harness.runlog import FileLog, RunLog
from .ingest import OcrClient, collect_input_files, load_document
from .io_utils import get_logger, write_json
from .matching.ledger import BillLine, Ledger, norm_key
from .models import ExtractedDocument, OutputRecord
from .pipeline_utils import RecordAssessment
from .validation.validators import Issue, summarize, validate_document

logger = get_logger("pipeline")


@dataclass
class PipelineResult:
    records: list[OutputRecord] = field(default_factory=list)
    assessments: list[RecordAssessment] = field(default_factory=list)
    documents: list[ExtractedDocument] = field(default_factory=list)
    ledger: Ledger | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    run_log: RunLog | None = None
    failures: list[dict[str, Any]] = field(default_factory=list)
    settings: Settings | None = None

    def to_json_list(self) -> list[dict[str, Any]]:
        return [record.to_json_dict() for record in self.records]

    def operations(self) -> dict[str, int]:
        return self.summary.get("operations", {})


class BillingAgent:
    """智能账单助手（对外唯一入口）。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        ledger: Ledger | None = None,
        ocr_client: OcrClient | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.ocr = ocr_client or OcrClient(self.settings)
        self.llm = LlmExtractor(self.settings)
        self.extractor = DocumentExtractor(self.settings, self.llm)
        self.ledger = ledger if ledger is not None else Ledger.load(
            self.settings.ledger_path or "", entity=self.settings.accounting_entity
        )
        self.record_chain = build_record_chain(
            {"ledger": self.ledger, "settings": self.settings}
        )
        self._doc_bill_ids: dict[int, str] = {}
        self._duplicates: dict[tuple, str] = {}

    # ------------------------------------------------------------------ #
    def run(self, inputs: Sequence[str | Path]) -> PipelineResult:
        run_log = RunLog()
        run_log.start(time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
        started = time.perf_counter()

        files = collect_input_files(list(inputs))
        logger.info("待处理文件 %d 个", len(files))

        result = PipelineResult(ledger=self.ledger, settings=self.settings, run_log=run_log)
        self._doc_bill_ids.clear()
        self._duplicates.clear()

        load_with_retry = retry(times=2, base_delay=0.4, logger=logger)(
            lambda path: load_document(
                path, ocr_client=self.ocr, work_dir=self.settings.work_dir
            )
        )

        for path in files:
            entry = FileLog(file=path.name)
            file_started = time.perf_counter()
            try:
                raw = load_with_retry(path)
            except Exception as exc:  # noqa: BLE001 - 单文件失败不影响整批
                entry.status = "failed"
                entry.error = f"{type(exc).__name__}: {exc}"
                entry.duration_ms = int((time.perf_counter() - file_started) * 1000)
                run_log.add_file(entry)
                run_log.bump("files_failed")
                result.failures.append({"file": path.name, "stage": "ingest", "error": entry.error})
                logger.exception("解析失败: %s", path)
                continue

            entry.kind = raw.kind
            entry.ocr_source = raw.ocr_source
            entry.warnings = list(raw.warnings)

            try:
                documents = self.extractor.extract(raw)
            except Exception as exc:  # noqa: BLE001
                entry.status = "failed"
                entry.error = f"extract: {type(exc).__name__}: {exc}"
                entry.duration_ms = int((time.perf_counter() - file_started) * 1000)
                run_log.add_file(entry)
                run_log.bump("files_failed")
                result.failures.append({"file": path.name, "stage": "extract", "error": entry.error})
                logger.exception("抽取失败: %s", path)
                continue

            entry.documents = len(documents)
            for doc in documents:
                result.documents.append(doc)
                doc_issues = validate_document(
                    doc, tolerance_ratio=self.settings.total_tolerance_ratio
                )
                for charge in doc.charges:
                    duplicate_of = self._lookup_duplicate(doc, charge)
                    payload = {
                        "document": doc,
                        "charge": charge,
                        "doc_issues": doc_issues,
                        "duplicate_of": duplicate_of,
                        "settings": self.settings,
                    }
                    try:
                        assessment: RecordAssessment = self.record_chain.invoke(payload)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("记录装配失败: %s", path)
                        result.failures.append(
                            {
                                "file": path.name,
                                "stage": "assemble",
                                "error": f"{type(exc).__name__}: {exc}",
                                "charge": charge.charge_item,
                            }
                        )
                        continue
                    self._apply_to_ledger(assessment, doc, charge)
                    self._register_duplicate(doc, charge, assessment)
                    result.records.append(assessment.record)
                    result.assessments.append(assessment)
                    entry.records += 1
                    entry.operations[assessment.operation] = (
                        entry.operations.get(assessment.operation, 0) + 1
                    )

            entry.duration_ms = int((time.perf_counter() - file_started) * 1000)
            run_log.add_file(entry)
            run_log.bump("files_ok")

        run_log.stop()

        operations: dict[str, int] = {}
        postings = {"AR": 0, "AP": 0, "unknown": 0}
        all_issues: list[Issue] = []
        for assessment in result.assessments:
            operations[assessment.operation] = operations.get(assessment.operation, 0) + 1
            posting = assessment.record.financial_attributes.posting_type
            postings[posting or "unknown"] = postings.get(posting or "unknown", 0) + 1
            all_issues.extend(assessment.issues)

        result.summary = {
            "files": len(files),
            "files_failed": len(result.failures),
            "documents": len(result.documents),
            "records": len(result.records),
            "operations": operations,
            "posting": postings,
            "issues": summarize(all_issues),
            "duration_s": round(time.perf_counter() - started, 3),
            "run_id": run_log.run_id,
            "ledger": self.ledger.stats(),
            "ocr": dict(self.ocr.stats),
            "llm": self.extractor.describe(),
        }
        run_log.counters.update(
            {
                "records": len(result.records),
                "documents": len(result.documents),
                **{f"op_{k}": v for k, v in operations.items()},
            }
        )
        run_log.meta = {
            "entity": self.settings.accounting_entity,
            "ocr_mode": self.settings.ocr_mode,
            "llm_enabled": self.llm.enabled,
        }
        logger.info(
            "处理完成：%d 文件 / %d 单据 / %d 记录，耗时 %.2fs",
            len(files),
            len(result.documents),
            len(result.records),
            result.summary["duration_s"],
        )
        return result

    # ------------------------------------------------------------------ #
    def _lookup_duplicate(self, doc: ExtractedDocument, charge) -> str | None:
        key = self._duplicate_key(doc, charge)
        if key is None:
            return None
        return self._duplicates.get(key)

    def _register_duplicate(self, doc: ExtractedDocument, charge, assessment: RecordAssessment) -> None:
        key = self._duplicate_key(doc, charge)
        if key is None or key in self._duplicates:
            return
        self._duplicates[key] = (
            f"{doc.source.filename if doc.source else '?'}:{charge.charge_item}@{charge.amount}"
        )

    def _duplicate_key(self, doc: ExtractedDocument, charge) -> tuple | None:
        reference = doc.effective_reference()
        if not reference:
            return None
        return (
            norm_key(reference),
            (charge.charge_item or "").strip().lower(),
            round(charge.amount or 0.0, 2),
            (charge.currency or "").upper(),
        )

    def _apply_to_ledger(self, assessment: RecordAssessment, doc: ExtractedDocument, charge) -> None:
        if assessment.operation == "CREATE":
            bill_id = self._bill_id_for(doc)
            existing = [line for line in self.ledger.lines if line.bill_id == bill_id]
            line = BillLine(
                bill_id=bill_id,
                line_id=f"{bill_id}-L{len(existing) + 1}",
                charge_item=charge.charge_item,
                amount=charge.signed_amount,
                currency=charge.currency,
                payment_term=charge.payment_term,
                document_type=doc.document_type,
                reference_no=doc.reference_no,
                document_date=doc.document_date,
                issuer=doc.issuer,
                recipient=doc.recipient,
                mbl_no=doc.identifiers.mbl_no,
                hbl_no=doc.identifiers.hbl_no,
                bl_no=doc.identifiers.bl_no,
                booking_no=doc.identifiers.booking_no,
                vessel=doc.identifiers.vessel,
                containers=list(doc.identifiers.container_nos),
                posting_type=assessment.record.financial_attributes.posting_type,
                accounting_entity=self.settings.accounting_entity,
                source=(doc.source.filename if doc.source else None),
                note="由智能账单助手自动新增",
            )
            self.ledger.add(line)
        elif assessment.operation in {"UPDATE", "NO_CHANGE"} and assessment.bill_line is not None:
            if assessment.updates:
                assessment.bill_line.apply_changes(assessment.updates)
            if not assessment.bill_line.posting_type:
                assessment.bill_line.posting_type = (
                    assessment.record.financial_attributes.posting_type
                )
            assessment.bill_line.source = doc.source.filename if doc.source else assessment.bill_line.source

    def _bill_id_for(self, doc: ExtractedDocument) -> str:
        key = id(doc)
        if key not in self._doc_bill_ids:
            self._doc_bill_ids[key] = self.ledger.next_bill_id()
        return self._doc_bill_ids[key]

    # ------------------------------------------------------------------ #
    def write_outputs(self, result: PipelineResult, out_dir: Path | str | None = None) -> dict[str, Path]:
        target = Path(out_dir or self.settings.output_dir or "output")
        target.mkdir(parents=True, exist_ok=True)

        results_path = write_json(target / "results.json", result.to_json_list())
        ledger_path = write_json(target / "ledger_after.json", self.ledger.model_dump(mode="json"))
        run_log_path = write_json(
            target / "run_log.json",
            result.run_log.to_dict() if result.run_log else {},
        )
        report = build_markdown(
            records=result.records,
            summary=result.summary,
            settings=self.settings,
            run_log=result.run_log.to_dict() if result.run_log else None,
            documents=result.documents,
            deterministic=not self.llm.enabled,
        )
        report_path = write_markdown(target / "report.md", report)

        if self.settings.ledger_path and result.records:
            # 只写回台账副本，避免污染原始台账（需要时用 --write-ledger 显式开启）
            pass
        return {
            "results": results_path,
            "ledger": ledger_path,
            "run_log": run_log_path,
            "report": report_path,
        }


__all__ = ["BillingAgent", "PipelineResult"]
