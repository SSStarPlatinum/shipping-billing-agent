"""记录级置信度打分。

一个可解释的加权模型（而不是拍脑袋给个数字）：

    score = 0.35 * 明细置信度
          + 0.15 * 单据类型置信度
          + 0.15 * 记账方向置信度
          + 0.10 * 编号存在
          + 0.10 * 交易双方存在
          + 0.15 * 匹配/新增确定性
          - 校验问题惩罚

低于阈值 → PENDING_REVIEW，交人工处理。
"""

from __future__ import annotations

from ..models import ChargeLine, ExtractedDocument
from ..finance.posting import PostingDecision
from .validators import ERROR, Issue, WARNING


def score_record(
    *,
    doc: ExtractedDocument,
    charge: ChargeLine,
    posting: PostingDecision,
    matched: bool,
    issues: list[Issue],
) -> tuple[float, list[str]]:
    factors: list[str] = []

    charge_conf = charge.confidence
    type_conf = doc.confidence.get("document_type", 0.5)
    posting_conf = posting.confidence
    has_ref = 1.0 if doc.effective_reference() else 0.0
    has_parties = 1.0 if (doc.issuer and doc.recipient) else 0.0
    match_conf = 0.9 if matched else 0.75

    score = (
        0.35 * charge_conf
        + 0.15 * type_conf
        + 0.15 * posting_conf
        + 0.10 * has_ref
        + 0.10 * has_parties
        + 0.15 * match_conf
    )
    factors.append(f"明细置信度 {charge_conf:.2f}")
    factors.append(f"单据类型置信度 {type_conf:.2f}")
    factors.append(f"记账方向置信度 {posting_conf:.2f}")
    factors.append(f"编号{'齐全' if has_ref else '缺失'}，双方信息{'齐全' if has_parties else '不完整'}")

    for issue in issues:
        if issue.level == ERROR:
            score -= 0.30
            factors.append(f"扣分：{issue.code}")
        elif issue.level == WARNING:
            score -= 0.04
    score = max(0.05, min(0.99, score))
    return round(score, 2), factors


__all__ = ["score_record"]
