"""LLM 融合策略单测（不调用真实模型）。"""

from __future__ import annotations

from shipping_billing.extract.llm_extractor import LlmCharge, LlmDocument
from shipping_billing.extract.merge import merge_llm_result
from shipping_billing.models import ChargeLine, ExtractedDocument


def _rule_doc() -> ExtractedDocument:
    return ExtractedDocument(
        document_type=None,
        reference_no="DC-108478",
        issuer=None,
        recipient=None,
        currency="USD",
        total=100.0,
        charges=[
            ChargeLine(
                raw_name="HANDLING CHARGE",
                charge_item="Handling Charge",
                amount=100.0,
                currency="USD",
                confidence=0.9,
            )
        ],
    )


def test_llm_fills_missing_fields_only() -> None:
    llm = LlmDocument(
        document_type="Debit Note",
        issuer="NEPTUNE SHIPPING LIMITED",
        recipient="CIMC WETRANS INTL LOG CO LTD",
        total_amount=100.0,
        confidence=0.8,
        charges=[LlmCharge(raw_name="HANDLING CHARGE", amount=100.0, currency="USD")],
    )
    merged = merge_llm_result(_rule_doc(), llm)
    assert merged.document_type == "Debit Note"
    assert merged.issuer == "NEPTUNE SHIPPING LIMITED"
    assert merged.method == "rules+llm"
    # 规则已有的编号/金额不被 LLM 改写
    assert merged.reference_no == "DC-108478"
    assert merged.charges[0].amount == 100.0


def test_llm_conflict_lowers_confidence() -> None:
    llm = LlmDocument(total_amount=999.0, confidence=0.9, charges=[])
    merged = merge_llm_result(_rule_doc(), llm)
    assert merged.total == 100.0  # 规则值优先
    assert any("不一致" in w for w in merged.warnings)


def test_llm_charges_used_when_rules_found_none() -> None:
    doc = ExtractedDocument(document_type="Invoice", currency="USD", total=None)
    llm = LlmDocument(
        charges=[LlmCharge(raw_name="THC", amount=350.0, currency="USD", evidence="THC 350.00")],
        confidence=0.7,
    )
    merged = merge_llm_result(doc, llm)
    assert len(merged.charges) == 1
    assert merged.charges[0].charge_item == "Terminal Handling Charge"
    assert merged.charges[0].amount == 350.0
    assert merged.charges[0].provenance.extractor == "llm"
