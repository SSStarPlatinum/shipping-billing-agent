"""单据抽取编排：规则 → LLM（可选）→ 融合 → 二次标准化。

这里的函数都是"纯节点"，便于被 LangChain Runnable 串成链，也便于单测。
"""

from __future__ import annotations

from ..config import Settings
from ..io_utils import get_logger
from ..models import ExtractedDocument, RawDocument
from ..normalize.charge_names import standardize
from ..normalize.currency import normalize_currency
from ..normalize.payment_terms import normalize_payment_term
from .llm_extractor import LlmDocument, LlmExtractor
from .merge import merge_llm_result
from .rules import extract_document_rules

logger = get_logger("extract")


# --------------------------------------------------------------------------- #
# 节点
# --------------------------------------------------------------------------- #
def rule_node(payload: dict) -> dict:
    raw: RawDocument = payload["raw"]
    result = extract_document_rules(raw)
    return {**payload, "rules": result}


def llm_node_factory(llm: LlmExtractor | None):
    def _node(payload: dict) -> dict:
        raw: RawDocument = payload["raw"]
        if llm is None or not llm.enabled:
            return {**payload, "llm": None}
        result = llm.extract(raw.text, filename=raw.filename, kind=raw.kind)
        return {**payload, "llm": result}

    return _node


def merge_node(payload: dict) -> ExtractedDocument:
    rules: ExtractedDocument = payload["rules"]
    llm_result: LlmDocument | None = payload.get("llm")
    if llm_result is None:
        return rules
    return merge_llm_result(rules, llm_result)


def normalize_node(doc: ExtractedDocument) -> ExtractedDocument:
    """补齐币种/付款方式，统一费用名，规整金额与置信度。"""
    doc = doc.model_copy(deep=True)
    for charge in doc.charges:
        if not charge.currency:
            charge.currency = doc.currency
        else:
            result = normalize_currency(charge.currency)
            charge.currency = result.code if result.valid else doc.currency
        if not charge.payment_term:
            charge.payment_term = doc.payment_term
        else:
            charge.payment_term = normalize_payment_term(charge.payment_term) or doc.payment_term
        if not charge.charge_item:
            standard = standardize(charge.raw_name)
            charge.charge_item = standard.standard
            charge.standardized = standard.known
        if charge.amount is not None:
            charge.amount = round(abs(charge.amount), 2)
        charge.confidence = round(min(0.99, max(0.05, charge.confidence)), 2)
    return doc


# --------------------------------------------------------------------------- #
# 附件上下文
# --------------------------------------------------------------------------- #
def merge_attachment_context(
    primary: ExtractedDocument, attachments: list[ExtractedDocument]
) -> ExtractedDocument:
    """把附件（如 B/L）里的业务标识补进主单据，用于后续匹配。"""
    doc = primary.model_copy(deep=True)
    for att in attachments:
        if att is doc:
            continue
        changed: list[str] = []
        for field_name in (
            "mbl_no",
            "hbl_no",
            "booking_no",
            "bl_no",
            "invoice_no",
            "ams_no",
            "agent_file_no",
            "vessel",
            "voyage",
        ):
            current = getattr(doc.identifiers, field_name)
            new = getattr(att.identifiers, field_name)
            if not current and new:
                setattr(doc.identifiers, field_name, new)
                changed.append(f"{field_name}={new}")
        for container in att.identifiers.container_nos:
            if container not in doc.identifiers.container_nos:
                doc.identifiers.container_nos.append(container)
                changed.append(f"container={container}")
        if not doc.issuer and att.issuer:
            doc.issuer = att.issuer
            changed.append(f"issuer={att.issuer}")
        if not doc.recipient and att.recipient:
            doc.recipient = att.recipient
            changed.append(f"recipient={att.recipient}")
        if changed:
            source = att.source.filename if att.source else "attachment"
            doc.attachment_context[source] = changed
            doc.warnings.append(
                f"从附件 {source} 补充业务上下文: {', '.join(changed[:6])}"
                + (" ..." if len(changed) > 6 else "")
            )
    return doc


# --------------------------------------------------------------------------- #
# 对外
# --------------------------------------------------------------------------- #
class DocumentExtractor:
    def __init__(self, settings: Settings, llm: LlmExtractor | None = None) -> None:
        self.settings = settings
        self.llm = llm or LlmExtractor(settings)
        self._chain = None

    def chain(self):
        """LangChain 链：规则 ∥ LLM → 融合 → 标准化。"""
        if self._chain is None:
            from ..agent import build_document_chain

            self._chain = build_document_chain(self.llm)
        return self._chain

    def extract_one(self, raw: RawDocument) -> ExtractedDocument:
        payload = self.chain().invoke({"raw": raw})
        return payload if isinstance(payload, ExtractedDocument) else normalize_node(payload)

    def extract(self, raw: RawDocument) -> list[ExtractedDocument]:
        """返回 [主单据, 附件中可独立成账的单据...]。"""
        primary = self.extract_one(raw)
        attachment_docs: list[ExtractedDocument] = []
        for child in raw.attachments:
            child_doc = self.extract_one(child)
            # 递归：孙附件也作为上下文
            nested = [self.extract_one(grand) for grand in child.attachments]
            attachment_docs.extend([d for d in nested if d.charges])
            if child_doc.charges:
                attachment_docs.append(child_doc)
            else:
                primary = merge_attachment_context(primary, [child_doc, *nested])

        if raw.attachments:
            primary.warnings.append(
                f"邮件包含 {len(raw.attachments)} 个附件: "
                + ", ".join(a.filename for a in raw.attachments)
            )
        primary = merge_attachment_context(
            primary, attachment_docs
        ) if attachment_docs else primary
        return [primary, *attachment_docs]

    def describe(self) -> dict:
        return {
            "llm_enabled": self.llm.enabled,
            "llm_model": self.settings.llm_model if self.llm.enabled else None,
            "llm_stats": dict(self.llm.stats),
        }


__all__ = [
    "DocumentExtractor",
    "llm_node_factory",
    "merge_attachment_context",
    "merge_node",
    "normalize_node",
    "rule_node",
]
