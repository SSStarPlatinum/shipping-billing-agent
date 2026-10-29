"""记录装配：把"单据 + 明细 + 匹配 + 记账方向 + 校验"组装成标准输出记录。

这里集中体现了题目"七、处理要求"的硬约束：
1. 不虚构：所有字段都来自输入材料或台账继承，缺失一律 null；
2. 金额是数字类型；
3. 币种三位代码；
4. 每个费用项目一条记录；
5. 更新时不覆盖未明确要求修改的字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .finance.posting import PostingDecision
from .io_utils import company_similarity
from .matching.ledger import BillLine
from .matching.matcher import MatchResult
from .models import (
    AuditTrail,
    ChargeDetails,
    ChargeLine,
    DocumentInfo,
    ExtractedDocument,
    FinancialAttributes,
    OperationType,
    OutputRecord,
    TransactionParty,
)
from .validation.confidence import score_record
from .validation.validators import ERROR, INFO, Issue, has_blocking


@dataclass
class RecordAssessment:
    """一条明细的完整处理结论（记录 + 需要写回台账的字段）。"""

    record: OutputRecord
    operation: OperationType
    bill_line: BillLine | None = None
    updates: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    issues: list[Issue] = field(default_factory=list)
    duplicate_of: str | None = None


_COMPARABLE_TEXT_FIELDS: tuple[tuple[str, str], ...] = (
    ("document_type", "单据类型"),
    ("document_date", "单据日期"),
    ("issuer", "签发方"),
    ("recipient", "接收方"),
)


def compute_field_changes(
    doc: ExtractedDocument, charge: ChargeLine, line: BillLine, settings: Settings
) -> list[dict[str, Any]]:
    """挑出"输入材料明确给出且与原值不同"的字段。

    ``kind`` 区分两种性质：
    * ``overwrite``：台账已有值，被输入材料改写 —— 只有这种情况才算真正的"更新"；
    * ``enrich``：台账该字段为空，用输入材料补全 —— 属数据补全，不构成覆盖。
    """
    changes: list[dict[str, Any]] = []
    tolerance = settings.amount_tolerance

    def add(field: str, old: Any, new: Any, reason: str) -> None:
        changes.append(
            {
                "field": field,
                "old": old,
                "new": new,
                "reason": reason,
                "kind": "enrich" if old in (None, "") else "overwrite",
            }
        )

    if charge.amount is not None:
        old_amount = line.amount
        if old_amount is None:
            add("amount", old_amount, round(charge.amount, 2), "台账金额为空，按输入材料补全")
        elif abs(old_amount - charge.amount) > tolerance:
            add("amount", old_amount, round(charge.amount, 2), "输入材料金额与台账不一致")

    if charge.currency and charge.currency != (line.currency or ""):
        add(
            "currency",
            line.currency,
            charge.currency,
            "输入材料币种与台账不一致" if line.currency else "台账币种为空",
        )
    if charge.payment_term and charge.payment_term != (line.payment_term or ""):
        add(
            "payment_term",
            line.payment_term,
            charge.payment_term,
            "输入材料付款方式与台账不一致" if line.payment_term else "台账付款方式为空",
        )

    for attribute, label in _COMPARABLE_TEXT_FIELDS:
        new_value = getattr(doc, attribute, None)
        if not new_value:
            continue
        old_value = getattr(line, attribute, None)
        if not old_value:
            add(attribute, old_value, new_value, f"台账{label}为空")
            continue
        if attribute in {"issuer", "recipient"}:
            # 一方是邮箱、另一方是公司名时不可比，直接跳过，避免误判"改名"
            if "@" in str(old_value) or "@" in str(new_value):
                continue
            if company_similarity(old_value, new_value) < 0.85:
                add(attribute, old_value, new_value, f"输入材料{label}与台账不一致")
        elif str(old_value).strip().lower() != str(new_value).strip().lower():
            add(attribute, old_value, new_value, f"输入材料{label}与台账不一致")

    if doc.reference_no and not line.reference_no:
        add("reference_no", None, doc.reference_no, "台账编号为空")
    return changes


def _decide_operation(
    *,
    match: MatchResult,
    posting: PostingDecision,
    issues: list[Issue],
    confidence: float,
    changes: list[dict[str, Any]],
    duplicate_of: str | None,
    settings: Settings,
    charge: ChargeLine,
) -> tuple[OperationType, str]:
    if duplicate_of:
        return "PENDING_REVIEW", f"与本次已处理的记录 {duplicate_of} 重复，转人工确认"
    if has_blocking(issues):
        blocking = next(i for i in issues if i.level == ERROR)
        return "PENDING_REVIEW", f"存在阻断性问题（{blocking.code}），不能自动入账"
    if posting.posting_type is None or posting.requires_review:
        return "PENDING_REVIEW", "无法结合记账主体判断应收应付，转人工复核"
    if match.matched and match.conflict:
        return "PENDING_REVIEW", f"匹配到台账但存在冲突：{'; '.join(match.conflicts)}"
    if confidence < settings.review_confidence_threshold:
        return "PENDING_REVIEW", f"综合置信度 {confidence:.2f} 低于阈值，转人工复核"
    if charge.amount is None:
        return "PENDING_REVIEW", "金额缺失"
    if not match.matched:
        return "CREATE", "未匹配到已有账单，按新增处理"
    overwrites = [change for change in changes if change.get("kind") == "overwrite"]
    if overwrites:
        return "UPDATE", "匹配到已有账单且输入材料明确变更了字段，按增量更新处理"
    if changes:
        return "NO_CHANGE", "匹配到已有账单，金额/币种/双方一致；仅补全台账空字段，不构成更新"
    return "NO_CHANGE", "匹配到已有账单且所有字段一致，无需变更"


def assemble_record(payload: dict[str, Any]) -> RecordAssessment:
    doc: ExtractedDocument = payload["document"]
    charge: ChargeLine = payload["charge"]
    match: MatchResult = payload["match"]
    posting: PostingDecision = payload["posting"]
    issues: list[Issue] = list(payload.get("issues") or [])
    settings: Settings = payload["settings"]
    duplicate_of: str | None = payload.get("duplicate_of")

    line = match.line
    changes = compute_field_changes(doc, charge, line, settings) if line is not None else []

    confidence, factors = score_record(
        doc=doc, charge=charge, posting=posting, matched=match.matched, issues=issues
    )
    if duplicate_of:
        confidence = round(max(0.05, confidence - 0.2), 2)

    operation, operation_reason = _decide_operation(
        match=match,
        posting=posting,
        issues=issues,
        confidence=confidence,
        changes=changes,
        duplicate_of=duplicate_of,
        settings=settings,
        charge=charge,
    )

    # ---------------- 审计链 ----------------
    reasoning: list[str] = []
    provenance = charge.provenance
    if provenance:
        ocr_note = f"，来源 {provenance.ocr_source}" if provenance.ocr_source else ""
        reasoning.append(
            f"从 {provenance.source_file}（{provenance.kind}{'，' + provenance.locator if provenance.locator else ''}）"
            f"解析出费用：{charge.raw_name}{ocr_note}"
        )
    else:
        reasoning.append(f"从 {doc.source.filename if doc.source else '未知来源'} 解析出费用：{charge.raw_name}")

    if charge.raw_name.strip().lower() != (charge.charge_item or "").strip().lower():
        reasoning.append(
            f"费用名称标准化：'{charge.raw_name}' → '{charge.charge_item}'"
            + ("" if charge.standardized else "（未收录到标准词表，保留原文规范化结果）")
        )
    reasoning.extend(posting.reasoning)
    reasoning.extend(match.notes)
    reasoning.append(operation_reason)
    if changes:
        for change in changes:
            if change.get("kind") == "overwrite":
                reasoning.append(
                    f"字段 {change['field']} 由 {change['old']} 更新为 {change['new']}（{change['reason']}）"
                )
            else:
                reasoning.append(
                    f"台账字段 {change['field']} 为空，按输入材料补全为 {change['new']}（补全，不构成覆盖）"
                )
        if operation == "PENDING_REVIEW":
            reasoning.append("若人工确认无误，将按上述变更写回台账")
        else:
            reasoning.append("其他字段未被输入材料明确要求修改，保留台账原值")
    if operation != "CREATE" and line is not None and not changes:
        reasoning.append("台账字段与输入材料一致，未做任何覆盖")

    warnings: list[str] = []
    warnings.extend(charge.warnings)
    warnings.extend(issue.message for issue in issues if issue.level != INFO)
    warnings.extend(match.conflicts)

    audit = AuditTrail(
        ai_reasoning=reasoning,
        source={
            "file": provenance.source_file if provenance else None,
            "role": provenance.source_role if provenance else "primary",
            "kind": provenance.kind if provenance else None,
            "locator": provenance.locator if provenance else None,
            "ocr_source": provenance.ocr_source if provenance else None,
            "extractor": provenance.extractor if provenance else "rules",
            "document_sha256": doc.source.sha256 if doc.source else None,
        },
        confidence=confidence,
        warnings=warnings,
        matched_bill_id=line.bill_id if line is not None else None,
        match_rule=match.rule_label if match.matched else None,
        field_changes=changes,
    )

    record = OutputRecord(
        operation=operation,
        document_info=DocumentInfo(
            document_type=doc.document_type,
            reference_no=doc.reference_no,
            document_date=doc.document_date,
        ),
        transaction_party=TransactionParty(issuer=doc.issuer, recipient=doc.recipient),
        charge_details=ChargeDetails(
            charge_item=charge.charge_item,
            amount=round(charge.signed_amount, 2) if charge.signed_amount is not None else None,
            currency=charge.currency,
            payment_term=charge.payment_term,
        ),
        financial_attributes=FinancialAttributes(
            accounting_entity=settings.accounting_entity,
            posting_type=posting.posting_type,
        ),
        audit_trail=audit,
    )

    updates: dict[str, Any] = {}
    if line is not None and operation in {"UPDATE", "NO_CHANGE"}:
        for change in changes:
            updates[change["field"]] = change["new"]

    return RecordAssessment(
        record=record,
        operation=operation,
        bill_line=line,
        updates=updates,
        confidence=confidence,
        issues=issues,
        duplicate_of=duplicate_of,
    )


__all__ = ["RecordAssessment", "assemble_record", "compute_field_changes"]
