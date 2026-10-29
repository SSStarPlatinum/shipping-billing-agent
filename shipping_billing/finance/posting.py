"""应收 / 应付（AR / AP）判定。

题目强调：**不能仅根据 Debit Note / Credit Note 直接判断 AR/AP**，
必须结合签发方、接收方、当前记账主体和收费方向。

判定模型：
    1. 角色识别：记账主体是"签发方"还是"接收方"（公司名模糊匹配）；
    2. 基础方向：我方签发 → 我方收钱 → AR；我方接收 → 我方付钱 → AP；
    3. 方向修正：Credit Note / 红字（负数）行会反转方向；
    4. 一切不明确的情况 → posting_type = None + 转人工，绝不猜。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..io_utils import company_similarity

#: 视为"我方开票"的单据类型
DEBIT_DOCUMENT_TYPES = {"Debit Note", "Invoice", "Arrival Notice", "Statement", "Debit Memo"}
#: 视为"冲销/红字"的单据类型
CREDIT_DOCUMENT_TYPES = {"Credit Note", "Credit Memo"}

#: 公司名相似度阈值
MATCH_THRESHOLD = 0.7


@dataclass
class PostingDecision:
    posting_type: str | None
    role: str | None  # issuer / recipient / None
    confidence: float
    reasoning: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    requires_review: bool = False


def _role_scores(entity: str, issuer: str | None, recipient: str | None) -> tuple[float, float]:
    issuer_score = company_similarity(entity, issuer) if issuer else 0.0
    recipient_score = company_similarity(entity, recipient) if recipient else 0.0
    return issuer_score, recipient_score


def decide_posting(
    *,
    entity: str,
    issuer: str | None,
    recipient: str | None,
    document_type: str | None,
    amount_sign: int = 1,
) -> PostingDecision:
    reasoning: list[str] = []
    warnings: list[str] = []

    if not entity:
        return PostingDecision(
            posting_type=None,
            role=None,
            confidence=0.0,
            reasoning=["未配置记账主体，无法判断 AR/AP"],
            warnings=["accounting_entity 为空"],
            requires_review=True,
        )

    issuer_score, recipient_score = _role_scores(entity, issuer, recipient)
    reasoning.append(
        f"记账主体『{entity}』与签发方『{issuer or '未知'}』相似度 {issuer_score:.2f}，"
        f"与接收方『{recipient or '未知'}』相似度 {recipient_score:.2f}"
    )

    role: str | None = None
    if issuer_score >= MATCH_THRESHOLD and issuer_score >= recipient_score:
        role = "issuer"
    elif recipient_score >= MATCH_THRESHOLD:
        role = "recipient"

    if role is None:
        warnings.append("单据签发方/接收方均未匹配到记账主体，无法确定应收应付")
        reasoning.append("无法从单据确定我方角色，按规范不允许仅凭单据类型猜测，转人工复核")
        return PostingDecision(
            posting_type=None,
            role=None,
            confidence=0.1,
            reasoning=reasoning,
            warnings=warnings,
            requires_review=True,
        )

    base = "AR" if role == "issuer" else "AP"
    reasoning.append(
        f"我方是单据的{'签发方（对外开票/收费）' if role == 'issuer' else '接收方（被开票/被收费）'}，"
        f"基础方向判定为 {base}"
    )

    doc_type = (document_type or "").strip()
    direction: str | None = None
    if doc_type in CREDIT_DOCUMENT_TYPES:
        direction = "credit"
    elif doc_type in DEBIT_DOCUMENT_TYPES:
        direction = "debit"
    elif doc_type == "Pre-Alert":
        direction = "debit"
        reasoning.append("Pre-Alert 属于费用预告，默认按正向费用处理")
    else:
        direction = "debit"
        warnings.append(f"单据类型『{document_type or '未知'}』未纳入方向规则，按正向费用处理")

    posting = base
    if direction == "credit":
        posting = "AP" if base == "AR" else "AR"
        reasoning.append(f"单据类型为 {doc_type}（贷记/冲销），方向由 {base} 反转为 {posting}")

    if amount_sign < 0:
        posting = "AP" if posting == "AR" else "AR"
        reasoning.append("该明细为红字/贷记行，方向再次反转")

    confidence = 0.9 if role == "issuer" else 0.88
    if doc_type not in DEBIT_DOCUMENT_TYPES and doc_type not in CREDIT_DOCUMENT_TYPES:
        confidence -= 0.2
    if abs(issuer_score - recipient_score) < 0.1:
        confidence -= 0.15
        warnings.append("签发方与接收方都与记账主体相似，存在歧义")

    reasoning.append(f"最终过账方向：{posting}")
    return PostingDecision(
        posting_type=posting,
        role=role,
        confidence=round(max(0.05, min(0.98, confidence)), 2),
        reasoning=reasoning,
        warnings=warnings,
    )


__all__ = ["PostingDecision", "decide_posting"]
