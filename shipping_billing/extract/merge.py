"""规则结果 与 LLM 结果 的融合策略。

原则：**规则为准，LLM 补位，冲突降级**。
* 数值类（金额、币种、编号）以规则/原文字面值为准，LLM 只做交叉校验；
* 语义类（单据类型、交易双方）规则缺失时才采用 LLM 结论；
* 两者冲突 → 保留规则值 + 写入 warning + 置信度打折（必要时转人工）。
"""

from __future__ import annotations

from ..io_utils import company_similarity, truncate
from ..models import ChargeLine, ExtractedDocument, Provenance
from ..normalize.charge_names import standardize
from ..normalize.currency import normalize_currency
from ..normalize.payment_terms import normalize_payment_term
from .llm_extractor import LlmDocument


def _same_text(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    if company_similarity(a, b) >= 0.86:
        return True
    return a.strip().lower() == b.strip().lower()


def merge_llm_result(rule: ExtractedDocument, llm: LlmDocument) -> ExtractedDocument:
    doc = rule.model_copy(deep=True)
    doc.method = "rules+llm"
    notes: list[str] = ["LLM 参与抽取并做交叉校验"]
    conflicts: list[str] = []

    # ---- 单据类型 ----
    if not doc.document_type and llm.document_type:
        doc.document_type = llm.document_type
        doc.confidence["document_type"] = min(0.8, llm.confidence)
        notes.append(f"LLM 补充单据类型: {llm.document_type}")
    elif doc.document_type and llm.document_type and not _same_text(doc.document_type, llm.document_type):
        conflicts.append(f"单据类型不一致（规则 {doc.document_type} / LLM {llm.document_type}）")

    # ---- 编号 / 日期 ----
    if not doc.reference_no and llm.reference_no:
        doc.reference_no = llm.reference_no
        notes.append(f"LLM 补充单据编号: {llm.reference_no}")
    if not doc.document_date and llm.document_date:
        doc.document_date = llm.document_date
        notes.append(f"LLM 补充单据日期: {llm.document_date}")

    # ---- 交易双方 ----
    if not doc.issuer and llm.issuer:
        doc.issuer = llm.issuer
        notes.append(f"LLM 补充签发方: {llm.issuer}")
    if not doc.recipient and llm.recipient:
        doc.recipient = llm.recipient
        notes.append(f"LLM 补充接收方: {llm.recipient}")
    if doc.issuer and llm.issuer and company_similarity(doc.issuer, llm.issuer) < 0.6:
        conflicts.append(f"签发方不一致（规则 {doc.issuer} / LLM {llm.issuer}）")

    # ---- 币种 / 付款方式 ----
    if not doc.currency and llm.currency:
        result = normalize_currency(llm.currency)
        if result.valid:
            doc.currency = result.code
            notes.append(f"LLM 补充币种: {result.code}")
    if not doc.payment_term and llm.payment_term:
        term = normalize_payment_term(llm.payment_term)
        if term:
            doc.payment_term = term
            notes.append(f"LLM 补充付款方式: {term}")

    # ---- 费用明细 ----
    if not doc.charges and llm.charges:
        for item in llm.charges:
            if item.amount is None:
                continue
            standard = standardize(item.raw_name)
            doc.charges.append(
                ChargeLine(
                    raw_name=item.raw_name,
                    charge_item=standard.standard,
                    standardized=standard.known,
                    amount=round(float(item.amount), 2),
                    currency=(normalize_currency(item.currency).code or doc.currency),
                    quantity=item.quantity,
                    unit_rate=item.unit_rate,
                    payment_term=normalize_payment_term(item.payment_term) or doc.payment_term,
                    confidence=min(0.7, llm.confidence),
                    source_line=item.evidence,
                    provenance=Provenance(
                        source_file=(doc.source.filename if doc.source else "unknown"),
                        kind=(doc.source.kind if doc.source else "unknown"),
                        extractor="llm",
                        snippet=truncate(item.evidence or "", 160),
                    ),
                    warnings=["费用明细由 LLM 补充，置信度较低"],
                )
            )
        notes.append(f"LLM 补充 {len(llm.charges)} 条费用明细")
    elif doc.charges and llm.charges:
        rule_total = round(sum(c.amount or 0 for c in doc.charges), 2)
        llm_total = round(sum(c.amount or 0 for c in llm.charges if c.amount), 2)
        if abs(rule_total - llm_total) > max(0.02, rule_total * 0.01):
            conflicts.append(f"费用合计不一致（规则 {rule_total:.2f} / LLM {llm_total:.2f}）")
        else:
            notes.append(f"LLM 复核费用合计一致（{rule_total:.2f}）")

    # ---- 总额 ----
    if doc.total is None and llm.total_amount is not None:
        doc.total = round(float(llm.total_amount), 2)
        notes.append(f"LLM 补充单据总额: {doc.total}")
    elif doc.total is not None and llm.total_amount is not None:
        if abs(doc.total - float(llm.total_amount)) > max(0.02, doc.total * 0.005):
            conflicts.append(f"单据总额不一致（规则 {doc.total:.2f} / LLM {llm.total_amount:.2f}）")

    if conflicts:
        doc.warnings.extend(conflicts)
        for key in list(doc.confidence):
            doc.confidence[key] = round(max(0.1, doc.confidence[key] * 0.8), 2)
    if notes:
        doc.warnings.extend(notes)
    return doc


__all__ = ["merge_llm_result"]
