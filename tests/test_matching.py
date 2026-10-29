"""AR/AP 判定与匹配规则单测。"""

from __future__ import annotations

from shipping_billing.finance.posting import decide_posting
from shipping_billing.matching.ledger import BillLine, Ledger
from shipping_billing.matching.matcher import match_charge
from shipping_billing.models import ChargeLine, DocumentIdentifiers, ExtractedDocument

ENTITY = "NEPTUNE SHIPPING LIMITED"


def _charge(name="Handling Charge", amount=65.0, currency="USD") -> ChargeLine:
    return ChargeLine(raw_name=name, charge_item=name, amount=amount, currency=currency)


def _doc(reference="DC-108478", issuer=ENTITY, recipient="CIMC WETRANS INTL LOG CO LTD", **ids):
    return ExtractedDocument(
        document_type="Debit Note",
        reference_no=reference,
        issuer=issuer,
        recipient=recipient,
        identifiers=DocumentIdentifiers(**ids),
    )


# --------------------------------------------------------------------------- #
def test_ap_when_entity_is_recipient() -> None:
    decision = decide_posting(
        entity="CIMC WETRANS INTL LOG CO LTD",
        issuer="NEPTUNE SHIPPING LIMITED",
        recipient="CIMC WETRANS INTL LOG CO LTD",
        document_type="Debit Note",
    )
    assert decision.posting_type == "AP"
    assert decision.role == "recipient"


def test_ar_when_entity_is_issuer() -> None:
    decision = decide_posting(
        entity=ENTITY,
        issuer=ENTITY,
        recipient="CIMC WETRANS INTL LOG CO LTD",
        document_type="Debit Note",
    )
    assert decision.posting_type == "AR"


def test_credit_note_flips_direction() -> None:
    debit = decide_posting(
        entity=ENTITY, issuer=ENTITY, recipient="AGENT", document_type="Debit Note"
    )
    credit = decide_posting(
        entity=ENTITY, issuer=ENTITY, recipient="AGENT", document_type="Credit Note"
    )
    assert debit.posting_type == "AR"
    assert credit.posting_type == "AP"  # 我方开贷记单 → 欠对方


def test_unknown_entity_requires_review() -> None:
    decision = decide_posting(
        entity="SOME OTHER COMPANY LTD",
        issuer="NEPTUNE SHIPPING LIMITED",
        recipient="CIMC WETRANS INTL LOG CO LTD",
        document_type="Debit Note",
    )
    assert decision.posting_type is None
    assert decision.requires_review is True


def test_negative_line_flips() -> None:
    decision = decide_posting(
        entity=ENTITY,
        issuer=ENTITY,
        recipient="AGENT",
        document_type="Debit Note",
        amount_sign=-1,
    )
    assert decision.posting_type == "AP"


# --------------------------------------------------------------------------- #
def _ledger() -> Ledger:
    return Ledger(
        entity=ENTITY,
        lines=[
            BillLine(
                bill_id="B1", charge_item="Handling Charge", amount=40.0, currency="USD",
                reference_no="DC-108478", accounting_entity=ENTITY,
            ),
            BillLine(
                bill_id="B2", charge_item="Handling Charge", amount=35.0, currency="USD",
                reference_no="DC-108478", accounting_entity=ENTITY,
            ),
            BillLine(
                bill_id="B3", charge_item="Trucking Charge", amount=100.0, currency="USD",
                mbl_no="OOLU4055331281", accounting_entity=ENTITY,
            ),
            BillLine(
                bill_id="B4", charge_item="Wharfage", amount=21.9, currency="USD",
                booking_no="ZIMUNGB1312144", accounting_entity=ENTITY,
            ),
            BillLine(
                bill_id="B5", charge_item="Storage Charge", amount=80.0, currency="USD",
                containers=["SAPU2040689"], issuer=ENTITY,
                recipient="CIMC WETRANS INTL LOG CO LTD", document_date="2026-10-26",
                accounting_entity=ENTITY,
            ),
        ],
    )


class _S:
    amount_tolerance = 0.02
    accounting_entity = ENTITY


def test_rule1_and_amount_disambiguation() -> None:
    ledger = _ledger()
    result = match_charge(_charge(amount=65.0), _doc(), ledger, _S())
    assert result.rule == "rule_1"
    assert result.line.bill_id == "B1"
    assert result.candidate_count == 2

    result2 = match_charge(_charge(amount=35.0), _doc(), ledger, _S())
    assert result2.line.bill_id == "B2"


def test_rule2_bl_number() -> None:
    ledger = _ledger()
    doc = _doc(reference=None, mbl_no="OOLU4055331281")
    result = match_charge(_charge(name="Trucking Charge", amount=100.0), doc, ledger, _S())
    assert result.rule == "rule_2"
    assert result.line.bill_id == "B3"


def test_rule3_booking_number() -> None:
    ledger = _ledger()
    doc = _doc(reference=None, booking_no="ZIMUNGB1312144")
    result = match_charge(_charge(name="Wharfage", amount=21.9), doc, ledger, _S())
    assert result.rule == "rule_3"
    assert result.line.bill_id == "B4"


def test_rule4_container_parties_amount() -> None:
    ledger = _ledger()
    doc = _doc(reference=None, container_nos=["SAPU2040689"])
    doc.document_date = "2026-10-26"
    result = match_charge(_charge(name="Storage Charge", amount=80.0), doc, ledger, _S())
    assert result.rule == "rule_4"
    assert result.line.bill_id == "B5"


def test_no_match_returns_create_signal() -> None:
    ledger = _ledger()
    result = match_charge(_charge(name="Detention Charge", amount=1310.4), _doc(), ledger, _S())
    assert result.matched is False


def test_other_entity_ledger_is_out_of_scope() -> None:
    ledger = _ledger()
    ledger.lines[0].accounting_entity = "ALL AMERICA FORWARDING, INC."
    result = match_charge(_charge(amount=40.0), _doc(), ledger, _S())
    assert all(line.bill_id != "B1" for line in ledger.scope_lines(ENTITY))
    assert result.line is None or result.line.bill_id != "B1"
