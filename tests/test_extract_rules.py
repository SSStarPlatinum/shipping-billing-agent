"""规则抽取单测：单据头字段 + 费用明细。"""

from __future__ import annotations

from shipping_billing.extract import rules
from shipping_billing.ingest import OcrClient, load_document


def _extract(settings, path):
    doc = load_document(path, ocr_client=OcrClient(settings))
    return rules.extract_document_rules(doc)


def test_detect_document_type_titles() -> None:
    assert rules.detect_document_type("DEBIT NOTE\nD/C No.: X")[0] == "Debit Note"
    assert rules.detect_document_type("INVOICE\n...")[0] == "Invoice"
    assert rules.detect_document_type("CREDIT NOTE\n...")[0] == "Credit Note"
    assert rules.detect_document_type("", subject="PRE-ALERT SSZSE")[0] == "Pre-Alert"


def test_normalize_date_token() -> None:
    assert rules.normalize_date_token("10-24-2026") == "2026-10-24"
    assert rules.normalize_date_token("10/03/2025") == "2025-10-03"
    assert rules.normalize_date_token("2026-10-15") == "2026-10-15"
    assert rules.normalize_date_token("24-10-2026") == "2026-10-24"
    assert rules.normalize_date_token("not-a-date") is None


def test_debit_note_pdf(settings) -> None:
    doc = _extract(settings, settings.data_dir / "samples" / "PDF 示例账单2.pdf")
    assert doc.document_type == "Debit Note"
    assert doc.reference_no == "DC-108478"
    assert doc.document_date == "2026-10-24"
    assert doc.currency == "USD"
    assert doc.payment_term == "COD"
    assert doc.total == 1410.40
    assert [c.charge_item for c in doc.charges] == [
        "Handling Charge",
        "Detention Charge",
        "Handling Charge",
    ]
    assert [c.amount for c in doc.charges] == [65.0, 1310.4, 35.0]
    assert doc.charge_total() == 1410.40


def test_arrival_notice_pdf(settings) -> None:
    doc = _extract(settings, settings.data_dir / "samples" / "PDF示例账单1.pdf")
    assert doc.document_type == "Arrival Notice"
    assert len(doc.charges) == 1
    charge = doc.charges[0]
    assert charge.charge_item == "On Carriage Charge"
    assert charge.amount == 245.0
    assert charge.currency == "USD"
    assert doc.identifiers.container_nos == ["SAPU2040689"]
    assert "CMDUCHN4053601" in (doc.identifiers.bl_no or "")


def test_image_invoice(settings) -> None:
    doc = _extract(settings, settings.data_dir / "samples" / "图片示例.png")
    assert doc.document_type == "Invoice"
    assert doc.reference_no == "ORFR1618731"
    assert doc.document_date == "2025-10-03"
    assert [c.amount for c in doc.charges] == [5.0, 21.9]
    assert [c.charge_item for c in doc.charges] == [
        "Wharfage Administration Fee",
        "Wharfage",
    ]
    assert "JKU0450463" in doc.identifiers.container_nos


def test_eml_body_and_attachments(settings) -> None:
    path = next((settings.data_dir / "samples").glob("*.eml"))
    raw = load_document(path, ocr_client=OcrClient(settings))
    assert raw.kind == "eml"
    assert len(raw.attachments) == 2, [a.filename for a in raw.attachments]
    assert all(a.kind == "pdf" for a in raw.attachments)
    assert "PRE-ALERT" in (raw.metadata.get("subject") or "")

    doc = rules.extract_document_rules(raw)
    assert doc.document_type == "Pre-Alert"
    assert doc.reference_no == "OOLU4055331281"
    assert doc.identifiers.mbl_no == "OOLU4055331281"
    assert doc.identifiers.container_nos == ["TIIU8975975"]
    assert len(doc.charges) == 1
    charge = doc.charges[0]
    assert charge.raw_name == "H/C"
    assert charge.charge_item == "Handling Charge"
    assert charge.amount == 50.0
    assert charge.currency == "USD"
    assert charge.payment_term == "PP"
