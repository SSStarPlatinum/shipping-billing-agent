"""数据校验。

对应题目"7. 数据校验"的五项要求：
* 金额是否为有效数字；
* 币种是否符合 ISO 4217；
* 明细金额与合计金额是否一致；
* OCR 是否存在 ``0/O``、``1/I`` 等识别错误；
* 同一费用是否被重复提取。

校验只产出 **Issue**（error / warning / info），由上层决定是入账、降级还是转人工。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import ChargeLine, ExtractedDocument
from ..normalize.currency import is_valid_currency

ERROR = "error"
WARNING = "warning"
INFO = "info"

#: 数字位上的字母（OCR 混淆）
_OCR_LETTER_IN_DIGITS = re.compile(r"(?<=\d)[OoIlSsBZzGQ](?=\d|[.,])|(?<![A-Za-z])[OoIl](?=\d)")
_DOC_NO_RE = re.compile(r"\b[A-Z0-9][A-Z0-9\-/]{5,}\b")

#: ISO 6346 箱号字母取值
_LETTER_VALUES: dict[str, int] = {}


def _build_letter_values() -> None:
    value = 10
    for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        while value % 11 == 0:
            value += 1
        _LETTER_VALUES[char] = value
        value += 1


_build_letter_values()

CONTAINER_RE = re.compile(r"^[A-Z]{4}\d{7}$")


@dataclass
class Issue:
    level: str
    code: str
    message: str
    field: str | None = None

    def as_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.message, "field": self.field}


def is_container_valid(container: str) -> bool:
    """ISO 6346 校验位验证。"""
    code = (container or "").strip().upper()
    if not CONTAINER_RE.match(code):
        return False
    total = 0
    for index, char in enumerate(code[:10]):
        if char.isdigit():
            total += int(char) * (2**index)
        else:
            total += _LETTER_VALUES.get(char, 0) * (2**index)
    check = (total % 11) % 10
    return check == int(code[10])


def _looks_like_ocr_confusion(value: str | None) -> bool:
    if not value:
        return False
    return bool(_OCR_LETTER_IN_DIGITS.search(value))


def validate_charge(charge: ChargeLine, doc: ExtractedDocument | None = None) -> list[Issue]:
    issues: list[Issue] = []

    # 1) 金额
    if charge.amount is None:
        issues.append(Issue(ERROR, "AMOUNT_MISSING", "费用金额缺失，无法入账", "amount"))
    else:
        if charge.amount < 0:
            issues.append(Issue(WARNING, "AMOUNT_NEGATIVE", "金额为负数，按贷记行处理", "amount"))
        if charge.amount == 0:
            issues.append(Issue(WARNING, "AMOUNT_ZERO", "金额为 0，请确认是否为有效费用", "amount"))
        if charge.amount > 10_000_000:
            issues.append(Issue(WARNING, "AMOUNT_TOO_LARGE", "金额异常偏大，请复核", "amount"))

    # 2) 币种
    if not charge.currency:
        issues.append(Issue(WARNING, "CURRENCY_MISSING", "币种缺失", "currency"))
    elif not is_valid_currency(charge.currency):
        issues.append(
            Issue(ERROR, "CURRENCY_INVALID", f"币种 {charge.currency} 不符合 ISO 4217", "currency")
        )

    # 3) 单价 × 数量 = 金额
    if charge.unit_rate is not None and charge.quantity is not None and charge.amount is not None:
        expected = round(charge.unit_rate * charge.quantity, 2)
        if abs(expected - charge.amount) > max(0.02, abs(charge.amount) * 0.01):
            issues.append(
                Issue(
                    WARNING,
                    "AMOUNT_CALC_MISMATCH",
                    f"单价 {charge.unit_rate} × 数量 {charge.quantity} = {expected}，与金额 {charge.amount} 不一致",
                    "amount",
                )
            )

    # 4) OCR 混淆
    if _looks_like_ocr_confusion(charge.source_line):
        issues.append(
            Issue(
                WARNING,
                "OCR_CONFUSION",
                "费用行存在 0/O、1/I 等易混淆字符，需人工核对",
                "charge_item",
            )
        )
    if charge.currency and len(charge.currency) == 3 and charge.currency[0] in "0O":
        issues.append(Issue(WARNING, "OCR_CURRENCY", "币种可能存在 OCR 误识别", "currency"))

    # 5) 归属单据
    if doc is not None:
        if not doc.effective_reference():
            issues.append(Issue(WARNING, "REFERENCE_MISSING", "单据编号缺失", "reference_no"))
        if not doc.issuer or not doc.recipient:
            issues.append(Issue(WARNING, "PARTY_MISSING", "交易双方信息不完整", "transaction_party"))

    return issues


def validate_identifiers(doc: ExtractedDocument) -> list[Issue]:
    issues: list[Issue] = []
    for container in doc.identifiers.container_nos:
        code = container.strip().upper()
        if len(code) != 11:
            issues.append(
                Issue(
                    INFO,
                    "CONTAINER_LENGTH",
                    f"箱号 {container} 长度为 {len(code)}，标准箱号为 4 字母+7 数字，疑似 OCR 缺字符",
                    "container_nos",
                )
            )
        elif CONTAINER_RE.match(code) and not is_container_valid(code):
            issues.append(
                Issue(
                    INFO,
                    "CONTAINER_CHECK_DIGIT",
                    f"箱号 {container} 校验位不符合 ISO 6346，可能是 OCR 误识别",
                    "container_nos",
                )
            )
    for field_name in ("mbl_no", "hbl_no", "bl_no", "booking_no", "reference_no"):
        value = getattr(doc.identifiers, field_name) or (
            doc.reference_no if field_name == "reference_no" else None
        )
        if _looks_like_ocr_confusion(value):
            issues.append(
                Issue(
                    WARNING,
                    "OCR_ID_CONFUSION",
                    f"{field_name} '{value}' 中存在疑似 OCR 混淆字符（0/O、1/I），需人工确认",
                    field_name,
                )
            )
    return issues


def validate_document(doc: ExtractedDocument, *, tolerance_ratio: float = 0.005) -> list[Issue]:
    issues: list[Issue] = []

    if not doc.charges:
        issues.append(Issue(WARNING, "NO_CHARGES", "单据未抽取到任何费用明细", "charges"))
        return issues

    # 明细合计 vs 单据总计
    if doc.total is not None:
        computed = doc.charge_total()
        tolerance = max(0.02, abs(doc.total) * tolerance_ratio)
        if abs(computed - doc.total) > tolerance:
            issues.append(
                Issue(
                    WARNING,
                    "TOTAL_MISMATCH",
                    f"明细合计 {computed:.2f} 与单据总计 {doc.total:.2f} 不一致"
                    f"（差额 {computed - doc.total:+.2f}）",
                    "total",
                )
            )

    # 单据内重复费用
    seen: dict[tuple, int] = {}
    for charge in doc.charges:
        key = (
            (charge.charge_item or "").strip().lower(),
            round(charge.amount or 0, 2),
            (charge.currency or "").upper(),
        )
        seen[key] = seen.get(key, 0) + 1
    for key, count in seen.items():
        if count > 1:
            issues.append(
                Issue(
                    WARNING,
                    "DUPLICATE_CHARGE_IN_DOC",
                    f"单据内费用 {key[0]} {key[1]:.2f} {key[2]} 出现 {count} 次，可能存在重复提取",
                    "charges",
                )
            )

    # 关键字段完整性（缺失只提示，不阻断）
    if not doc.document_date:
        issues.append(Issue(INFO, "DATE_MISSING", "未识别到单据日期", "document_date"))
    if not doc.document_type:
        issues.append(Issue(INFO, "TYPE_UNKNOWN", "未能确定单据类型", "document_type"))

    issues.extend(validate_identifiers(doc))
    return issues


def has_blocking(issues: list[Issue]) -> bool:
    return any(issue.level == ERROR for issue in issues)


def summarize(issues: list[Issue]) -> dict[str, int]:
    summary = {ERROR: 0, WARNING: 0, INFO: 0}
    for issue in issues:
        summary[issue.level] = summary.get(issue.level, 0) + 1
    return summary


__all__ = [
    "ERROR",
    "INFO",
    "WARNING",
    "Issue",
    "has_blocking",
    "is_container_valid",
    "summarize",
    "validate_charge",
    "validate_document",
    "validate_identifiers",
]
