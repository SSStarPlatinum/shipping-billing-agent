"""校验与置信度单测。"""

from __future__ import annotations

from shipping_billing.models import ChargeLine, ExtractedDocument, Provenance
from shipping_billing.validation.validators import (
    WARNING,
    has_blocking,
    is_container_valid,
    validate_charge,
    validate_document,
)


def _charge(**kwargs) -> ChargeLine:
    base = dict(raw_name="Handling Charge", charge_item="Handling Charge", amount=65.0,
                currency="USD", confidence=0.9)
    base.update(kwargs)
    return ChargeLine(**base)


def test_valid_charge_has_no_errors() -> None:
    issues = validate_charge(_charge())
    assert not has_blocking(issues)


def test_missing_amount_is_blocking() -> None:
    issues = validate_charge(_charge(amount=None))
    assert has_blocking(issues)
    assert any(i.code == "AMOUNT_MISSING" for i in issues)


def test_invalid_currency_is_blocking() -> None:
    issues = validate_charge(_charge(currency="XYZ"))
    assert has_blocking(issues)
    assert any(i.code == "CURRENCY_INVALID" for i in issues)


def test_unit_rate_times_qty_mismatch() -> None:
    issues = validate_charge(_charge(unit_rate=100.0, quantity=2, amount=150.0))
    assert any(i.code == "AMOUNT_CALC_MISMATCH" and i.level == WARNING for i in issues)


def test_ocr_confusion_detected() -> None:
    issues = validate_charge(_charge(source_line="HANDLING CHARGE 1O.50"))
    assert any(i.code == "OCR_CONFUSION" for i in issues)


def test_total_mismatch_and_duplicates() -> None:
    doc = ExtractedDocument(
        document_type="Debit Note",
        reference_no="DC-1",
        total=100.0,
        charges=[_charge(amount=60.0), _charge(amount=60.0)],
    )
    issues = validate_document(doc)
    codes = {i.code for i in issues}
    assert "TOTAL_MISMATCH" in codes
    assert "DUPLICATE_CHARGE_IN_DOC" in codes


def test_container_check_digit() -> None:
    # ISO 6346 官方示例
    assert is_container_valid("CSQU3054383") is True
    assert is_container_valid("CSQU3054384") is False
    assert is_container_valid("TIIU8975975") in (True, False)  # 只要求不抛异常


def test_container_length_warning() -> None:
    doc = ExtractedDocument(
        identifiers={"container_nos": ["JKU0450463"]},  # 10 位，少一个字母
        charges=[_charge()],
    )
    issues = validate_document(doc)
    assert any(i.code == "CONTAINER_LENGTH" for i in issues)


def test_source_evidence_kept() -> None:
    provenance = Provenance(source_file="a.pdf", kind="pdf", locator="line 3")
    charge = _charge(provenance=provenance)
    assert charge.provenance is not None
    assert charge.provenance.locator == "line 3"
