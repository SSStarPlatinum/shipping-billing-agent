"""费用行解析单测。"""

from __future__ import annotations

import pytest

from shipping_billing.extract.rows import (
    is_charge_header,
    looks_like_charge_description,
    parse_charge_row,
)


def test_debit_note_row() -> None:
    row = parse_charge_row("HANDLING CHARGE - 65.00 1.00 C 65.00")
    assert row is not None
    assert row.description == "HANDLING CHARGE"
    assert row.amount == 65.0
    assert row.unit_rate == 65.0
    assert row.quantity == 1.0
    assert row.pc_flag == "C"


def test_debit_note_row_with_thousands() -> None:
    row = parse_charge_row("4 ADD'L DETENTION FREE TIME CHARGE - 1,310.40 1.00 C 1,310.40")
    assert row is not None
    assert row.amount == 1310.40


def test_currency_amount_row() -> None:
    row = parse_charge_row("On Carriage Additional - US EmOCA17 USD  245.0000")
    assert row is not None
    assert row.currency == "USD"
    assert row.amount == 245.0
    assert row.description.startswith("On Carriage Additional")


def test_multi_currency_invoice_row() -> None:
    row = parse_charge_row("WHARFAGE ADMINISTRATION FEE C 5.00 USD 5.00 1.0000 USD 5.00")
    assert row is not None
    assert row.description == "WHARFAGE ADMINISTRATION FEE"
    assert row.amount == 5.0
    assert row.currency == "USD"

    row2 = parse_charge_row("WHARFAGE *TT 2.85 USD 21.90 1.0000 USD 21.90")
    assert row2 is not None
    assert row2.amount == 21.90


def test_symbol_amount_row() -> None:
    row = parse_charge_row("SHA pay TAR : H/C $50")
    assert row is not None
    assert row.amount == 50.0
    assert row.currency == "USD"


@pytest.mark.parametrize(
    "line",
    [
        "SAPU2040689 C9950614 20ST 233PIECE(S) 43762 706.00",
        "TOTAL CHARGES TO BE PAID:  245.0000",
        "GRAND TOTAL BALANCE DUE TO NEPTUNE SHIPPING LIMITED (LAX) USD",
        "USD 26.90",
        "CHN4053601 CWaybill",
        "TEL:626-442-8049 FAX:626-442-8062",
        "10/19/2026 05:58 PMGENERAL NOTICE",
    ],
)
def test_non_charge_lines_are_rejected(line: str) -> None:
    assert parse_charge_row(line) is None


def test_currency_only_rejected() -> None:
    assert looks_like_charge_description("USD") is False
    assert looks_like_charge_description("HANDLING CHARGE") is True


def test_header_detection() -> None:
    assert is_charge_header("DESCRIPTION UNIT RATE QTY P/C DEBIT(+) CREDIT(-)")
    assert is_charge_header("CHARGES CUR AMOUNT")
    assert is_charge_header("Base Rate Original Cur Amount R.O.E. Curr Amount")
    assert not is_charge_header("VESSEL:")


def test_ocr_digit_repair() -> None:
    row = parse_charge_row("HANDLING CHARGE 1O.50")
    assert row is not None
    assert row.amount == 10.50
    assert any("OCR" in r for r in row.repairs)
