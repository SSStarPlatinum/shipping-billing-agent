"""LangChain 编排层。

虽然是"确定性优先"的系统，但整条处理链仍然用 LangChain 的 Runnable 组合：

    RunnableParallel(规则抽取 ∥ LLM 抽取)  →  融合  →  标准化  →  校验

好处：
* 两个抽取通道共享同一份输入快照，互不阻塞；
* 每个节点都是纯函数，可单测、可回放、可在中间插入缓存/重试;
* 需要换模型、加检索、加工具时，只改图不改业务代码。
"""

from __future__ import annotations

from typing import Any

from .extract.document_extractor import (
    llm_node_factory,
    merge_node,
    normalize_node,
    rule_node,
)
from .extract.llm_extractor import LlmExtractor


def build_document_chain(llm: LlmExtractor | None = None):
    """单据抽取链（输入 {"raw": RawDocument}）。"""
    from langchain_core.runnables import RunnableLambda, RunnableParallel

    rules_branch = RunnableLambda(rule_node)
    llm_branch = RunnableLambda(llm_node_factory(llm))

    def _collect(results: dict[str, Any]) -> dict[str, Any]:
        """两条分支各自返回 {**payload, ...}，这里收敛成 merge_node 需要的形状。"""
        merged = dict(results.get("rules") or {})
        merged["llm"] = (results.get("llm") or {}).get("llm")
        return merged

    return (
        RunnableParallel(rules=rules_branch, llm=llm_branch)
        | RunnableLambda(_collect)
        | RunnableLambda(merge_node)
        | RunnableLambda(normalize_node)
    )


def build_record_chain(context: dict[str, Any]):
    """明细入账链：匹配 → 记账方向 → 校验 → 输出记录。

    context 里放台账、配置等"只读依赖"，避免节点内部再取全局状态。
    """
    from langchain_core.runnables import RunnableLambda

    from .finance.posting import decide_posting
    from .matching.matcher import match_charge
    from .pipeline_utils import assemble_record
    from .validation.validators import validate_charge

    ledger = context["ledger"]
    settings = context["settings"]

    def _match(payload: dict) -> dict:
        charge = payload["charge"]
        doc = payload["document"]
        result = match_charge(charge, doc, ledger, settings)
        return {**payload, "match": result}

    def _posting(payload: dict) -> dict:
        doc = payload["document"]
        decision = decide_posting(
            entity=settings.accounting_entity,
            issuer=doc.issuer,
            recipient=doc.recipient,
            document_type=doc.document_type,
            amount_sign=payload["charge"].amount_sign,
        )
        return {**payload, "posting": decision}

    def _validate(payload: dict) -> dict:
        issues = validate_charge(payload["charge"], payload["document"])
        issues.extend(payload.get("doc_issues") or [])
        return {**payload, "issues": issues}

    return (
        RunnableLambda(_match)
        | RunnableLambda(_posting)
        | RunnableLambda(_validate)
        | RunnableLambda(assemble_record)
    )


__all__ = ["build_document_chain", "build_record_chain"]
