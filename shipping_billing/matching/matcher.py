"""账单匹配。

严格按题目给出的四类匹配条件，**按优先级依次尝试**：

1. 单据编号 + 费用名称
2. 提单号 + 费用名称 + 币种
3. 订舱号 + 费用名称 + 币种
4. 箱号 + 交易双方 + 日期 + 金额

同一规则内命中多行时，用金额距离做二次判别（这正是"同一单据出现同名
费用两次"时的正确解法）。任何字段冲突都会被记录，交由上层转人工。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Settings
from ..io_utils import company_similarity
from ..models import ChargeLine, ExtractedDocument
from .ledger import BillLine, Ledger, norm_key


@dataclass
class MatchResult:
    matched: bool = False
    line: BillLine | None = None
    rule: str | None = None
    rule_label: str = ""
    score: float = 0.0
    notes: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    candidate_count: int = 0

    @property
    def conflict(self) -> bool:
        return bool(self.conflicts)


def _amount_distance(a: float | None, b: float | None) -> float:
    if a is None or b is None:
        return float("inf")
    return abs(a - b)


def _pick_best(candidates: list[BillLine], charge: ChargeLine) -> tuple[BillLine, float, int]:
    """同规则内：优先金额完全一致，否则取金额最接近的。"""
    scored = sorted(
        candidates,
        key=lambda line: (_amount_distance(line.amount, charge.amount), line.bill_id),
    )
    best = scored[0]
    distance = _amount_distance(best.amount, charge.amount)
    return best, distance, len(scored)


def _party_match(doc: ExtractedDocument, line: BillLine) -> float:
    """交易双方相似度：正序或反序匹配都算。"""
    pairs = [
        (company_similarity(doc.issuer, line.issuer), company_similarity(doc.recipient, line.recipient)),
        (company_similarity(doc.issuer, line.recipient), company_similarity(doc.recipient, line.issuer)),
    ]
    best = max(pairs, key=lambda p: (p[0] + p[1]))
    return (best[0] + best[1]) / 2


def match_charge(
    charge: ChargeLine,
    doc: ExtractedDocument,
    ledger: Ledger,
    settings: Settings,
) -> MatchResult:
    result = MatchResult()
    ids = doc.identifiers
    tolerance = settings.amount_tolerance
    scoped = ledger.scope_lines(settings.accounting_entity)

    # ---------- 规则 1：单据编号 + 费用名称 ----------
    reference = doc.effective_reference()
    ref_key = norm_key(reference)
    if ref_key:
        candidates = [
            line
            for line in scoped
            if norm_key(line.reference_no) == ref_key
            and (line.charge_item or "").strip().lower()
            == (charge.charge_item or "").strip().lower()
        ]
        if candidates:
            best, distance, count = _pick_best(candidates, charge)
            result.matched = True
            result.line = best
            result.rule = "rule_1"
            result.rule_label = "单据编号+费用名称"
            result.candidate_count = count
            result.score = 0.98 if distance <= tolerance else max(0.7, 0.95 - distance / 1000)
            result.notes.append(
                f"按单据编号 {reference} + 费用名称 {charge.charge_item} 匹配到台账 {best.bill_id}"
            )
            if best.currency and charge.currency and best.currency != charge.currency:
                result.conflicts.append(
                    f"币种冲突：台账 {best.currency} / 单据 {charge.currency}"
                )
            if count > 1:
                result.notes.append(
                    f"同编号同费用名共有 {count} 条历史明细，按金额最接近原则选中"
                    f"（差额 {distance:.2f}）"
                )
            if distance > tolerance:
                result.notes.append(f"金额存在差异：台账 {best.amount} / 单据 {charge.amount}")
            return result

    # ---------- 规则 2：提单号 + 费用名称 + 币种 ----------
    bl_keys = {
        norm_key(ids.mbl_no),
        norm_key(ids.hbl_no),
        norm_key(ids.bl_no),
    }
    bl_keys.discard("")
    if bl_keys:
        candidates = [
            line
            for line in scoped
            if charge.charge_item
            and (line.charge_item or "").lower() == charge.charge_item.lower()
            and (line.currency or "").upper() == (charge.currency or "").upper()
            and bl_keys
            & {norm_key(line.mbl_no), norm_key(line.hbl_no), norm_key(line.bl_no)} - {""}
        ]
        if candidates:
            best, distance, count = _pick_best(candidates, charge)
            result.matched = True
            result.line = best
            result.rule = "rule_2"
            result.rule_label = "提单号+费用名称+币种"
            result.candidate_count = count
            result.score = 0.9
            result.notes.append(
                f"按提单号 {next(iter(bl_keys))} + 费用名称 + 币种匹配到台账 {best.bill_id}"
            )
            return result

    # ---------- 规则 3：订舱号 + 费用名称 + 币种 ----------
    booking_key = norm_key(ids.booking_no)
    if booking_key:
        candidates = [
            line
            for line in scoped
            if (line.booking_no or "")
            and norm_key(line.booking_no) == booking_key
            and (line.charge_item or "").lower() == charge.charge_item.lower()
            and (line.currency or "").upper() == (charge.currency or "").upper()
        ]
        if candidates:
            best, distance, count = _pick_best(candidates, charge)
            result.matched = True
            result.line = best
            result.rule = "rule_3"
            result.rule_label = "订舱号+费用名称+币种"
            result.candidate_count = count
            result.score = 0.85
            result.notes.append(f"按订舱号 {ids.booking_no} 匹配到台账 {best.bill_id}")
            return result

    # ---------- 规则 4：箱号 + 交易双方 + 日期 + 金额 ----------
    container_keys = {norm_key(c) for c in ids.container_nos if c}
    if container_keys:
        best: tuple[float, BillLine] | None = None
        for line in scoped:
            line_keys = {norm_key(c) for c in line.containers if c}
            if not line_keys or not (container_keys & line_keys):
                continue
            if charge.amount is None or line.amount is None:
                continue
            amount_ok = abs(line.amount - charge.amount) <= max(tolerance, 0.01)
            if not amount_ok:
                continue
            party_score = _party_match(doc, line)
            if party_score < 0.55:
                continue
            score = 0.5 + 0.25 * party_score
            if doc.document_date and line.document_date:
                score += 0.15 if doc.document_date == line.document_date else -0.15
            if best is None or score > best[0]:
                best = (score, line)
        if best is not None and best[0] >= 0.7:
            result.matched = True
            result.line = best[1]
            result.rule = "rule_4"
            result.rule_label = "箱号+交易双方+日期+金额"
            result.score = round(min(0.9, best[0]), 2)
            result.notes.append(
                f"按箱号 {', '.join(sorted(container_keys))[:40]} + 交易双方 + 金额匹配到台账 {best[1].bill_id}"
            )
            if doc.document_date and best[1].document_date and doc.document_date != best[1].document_date:
                result.conflicts.append(
                    f"日期差异：台账 {best[1].document_date} / 单据 {doc.document_date}"
                )
            return result

    result.notes.append("四个匹配规则均未命中，判定为新账单")
    return result


__all__ = ["MatchResult", "match_charge"]
