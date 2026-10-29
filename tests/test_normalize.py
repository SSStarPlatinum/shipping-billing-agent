"""标准化层单测：费用名、币种、付款方式。"""

from __future__ import annotations

import pytest

from shipping_billing.normalize.charge_names import find_alias_in_line, standardize
from shipping_billing.normalize.currency import is_valid_currency, normalize_currency
from shipping_billing.normalize.payment_terms import (
    detect_payment_term_in_text,
    normalize_payment_term,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 题目明确要求的 6 组映射
        ("Handling Fee", "Handling Charge"),
        ("H/D Fee", "Handling Charge"),
        ("Handling Charge", "Handling Charge"),
        ("H/C", "Handling Charge"),
        ("Doc Fee", "Documentation Fee"),
        ("Documentation Fee", "Documentation Fee"),
        ("THC", "Terminal Handling Charge"),
        ("Terminal Handling Charge", "Terminal Handling Charge"),
        ("Trucking Fee", "Trucking Charge"),
        ("Customs Fee", "Customs Clearance Fee"),
        ("Storage Fee", "Storage Charge"),
        # 大小写/标点差异
        ("handling fee", "Handling Charge"),
        ("H  /  C", "Handling Charge"),
        ("DOC FEE", "Documentation Fee"),
        ("HANDLING CHARGE", "Handling Charge"),
        # 带行号前缀与区域后缀
        ("4 ADD'L DETENTION FREE TIME CHARGE", "Detention Charge"),
        ("On Carriage Additional - US EmOCA17", "On Carriage Charge"),
    ],
)
def test_required_mappings(raw: str, expected: str) -> None:
    result = standardize(raw)
    assert result.standard == expected
    assert result.known is True


def test_unknown_name_keeps_original() -> None:
    result = standardize("SPECIAL SURVEY OUT OF SCOPE FEE")
    assert result.known is False
    assert "SPECIAL SURVEY" in result.standard.upper()


def test_doc_alias_does_not_match_dock() -> None:
    """DOC 不能误命中 DOCK / DOCKET。"""
    result = standardize("DOCK LEASE FEE")
    assert result.standard != "Documentation Fee"


def test_find_alias_in_line() -> None:
    hit = find_alias_in_line("SHA pay TAR : H/C $50")
    assert hit is not None
    assert hit[0] == "Handling Charge"
    assert "H/C" in hit[1]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("USD", "USD"), ("usd", "USD"), ("$", "USD"), ("US$", "USD"), ("￥", "CNY"), ("RMB", "CNY")],
)
def test_currency_normalization(raw: str, expected: str) -> None:
    assert normalize_currency(raw).code == expected


def test_currency_invalid() -> None:
    result = normalize_currency("XYZ")
    assert result.valid is False
    assert is_valid_currency("XYZ") is False
    assert is_valid_currency("USD") is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PP", "PP"),
        ("PREPAID", "PP"),
        ("FREIGHT PREPAID", "PP"),
        ("CC", "CC"),
        ("COLLECT", "CC"),
        ("到付", "CC"),
        ("COD", "COD"),
        ("UNKNOWN-TERM", None),
        (None, None),
    ],
)
def test_payment_terms(raw, expected) -> None:
    assert normalize_payment_term(raw) == expected


def test_detect_payment_term_prefers_prepaid() -> None:
    text = "HBL Telex release HOLD FROM YANTIAN TO LONG BEACH,CA CY/CY FREIGHT PREPAID"
    assert detect_payment_term_in_text(text) == "PP"
