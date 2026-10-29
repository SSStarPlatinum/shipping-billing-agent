"""评测：用"金标准"用例给整条链路打分（回归测试的骨架）。

指标：
* 明细召回率 / 精确率（按 文件+费用名+金额 对齐）；
* 操作类型准确率（CREATE / UPDATE / NO_CHANGE / PENDING_REVIEW）；
* 应收应付准确率；
* 关键字段准确率（单据编号、日期、币种）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..io_utils import read_json


@dataclass
class EvalCase:
    file: str
    charge_item: str
    amount: float
    currency: str | None = None
    operation: str | None = None
    posting_type: str | None = None
    reference_no: str | None = None
    document_date: str | None = None
    document_type: str | None = None


@dataclass
class EvalResult:
    total: int = 0
    matched: int = 0
    recall: float = 0.0
    precision: float = 0.0
    operation_accuracy: float = 0.0
    posting_accuracy: float = 0.0
    field_accuracy: dict[str, float] = field(default_factory=dict)
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    unexpected: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_expected": self.total,
            "matched": self.matched,
            "recall": round(self.recall, 4),
            "precision": round(self.precision, 4),
            "operation_accuracy": round(self.operation_accuracy, 4),
            "posting_accuracy": round(self.posting_accuracy, 4),
            "field_accuracy": {k: round(v, 4) for k, v in self.field_accuracy.items()},
            "mismatches": self.mismatches,
            "unexpected": self.unexpected,
        }


def load_expectations(path: Path | str) -> list[EvalCase]:
    data = read_json(path, {}) or {}
    cases: list[EvalCase] = []
    for item in data.get("cases", []):
        cases.append(
            EvalCase(
                file=item.get("file", ""),
                charge_item=item.get("charge_item", ""),
                amount=float(item.get("amount", 0)),
                currency=item.get("currency"),
                operation=item.get("operation"),
                posting_type=item.get("posting_type"),
                reference_no=item.get("reference_no"),
                document_date=item.get("document_date"),
                document_type=item.get("document_type"),
            )
        )
    return cases


def _record_file(record) -> str:
    return (record.audit_trail.source or {}).get("file") or ""


def evaluate(records: list[Any], cases: list[EvalCase]) -> EvalResult:
    result = EvalResult(total=len(cases))
    used: set[int] = set()

    for case in cases:
        match_index: int | None = None
        for index, record in enumerate(records):
            if index in used:
                continue
            if _record_file(record) != case.file:
                continue
            if (record.charge_details.charge_item or "").lower() != case.charge_item.lower():
                continue
            amount = record.charge_details.amount or 0
            if abs(abs(amount) - abs(case.amount)) > 0.01:
                continue
            match_index = index
            break

        if match_index is None:
            result.mismatches.append(
                {
                    "type": "missing",
                    "expected": case.__dict__,
                    "reason": "未找到对应输出记录",
                }
            )
            continue

        used.add(match_index)
        record = records[match_index]
        result.matched += 1
        checks = {
            "operation": (record.operation, case.operation),
            "posting_type": (record.financial_attributes.posting_type, case.posting_type),
            "reference_no": (record.document_info.reference_no, case.reference_no),
            "document_date": (record.document_info.document_date, case.document_date),
            "currency": (record.charge_details.currency, case.currency),
        }
        for field_name, (actual, expected) in checks.items():
            if expected is None:
                continue
            result.field_accuracy[field_name] = result.field_accuracy.get(field_name, 0) + (
                1 if actual == expected else 0
            )
            if actual != expected:
                result.mismatches.append(
                    {
                        "type": "field_mismatch",
                        "file": case.file,
                        "charge_item": case.charge_item,
                        "field": field_name,
                        "expected": expected,
                        "actual": actual,
                    }
                )
        if case.operation:
            result.operation_accuracy += 1 if record.operation == case.operation else 0
        if case.posting_type:
            result.posting_accuracy += (
                1 if record.financial_attributes.posting_type == case.posting_type else 0
            )

    # 归一化
    op_cases = sum(1 for c in cases if c.operation)
    post_cases = sum(1 for c in cases if c.posting_type)
    field_cases: dict[str, int] = {}
    for case in cases:
        for name, value in (
            ("operation", case.operation),
            ("posting_type", case.posting_type),
            ("reference_no", case.reference_no),
            ("document_date", case.document_date),
            ("currency", case.currency),
        ):
            if value is not None:
                field_cases[name] = field_cases.get(name, 0) + 1

    result.recall = result.matched / result.total if result.total else 0.0
    result.precision = result.matched / len(records) if records else 0.0
    result.operation_accuracy = result.operation_accuracy / op_cases if op_cases else 0.0
    result.posting_accuracy = result.posting_accuracy / post_cases if post_cases else 0.0
    for name in result.field_accuracy:
        denominator = field_cases.get(name, 0)
        if denominator:
            result.field_accuracy[name] /= denominator

    for index, record in enumerate(records):
        if index in used:
            continue
        result.unexpected.append(
            {
                "file": _record_file(record),
                "charge_item": record.charge_details.charge_item,
                "amount": record.charge_details.amount,
                "operation": record.operation,
            }
        )
    return result


__all__ = ["EvalCase", "EvalResult", "evaluate", "load_expectations"]
