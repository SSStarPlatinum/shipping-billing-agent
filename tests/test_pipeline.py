"""端到端测试：样例目录 → 标准 JSON。

这是整个项目的"回归闸门"：任何改动都必须让这 7 条记录保持一致。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shipping_billing.config import Settings
from shipping_billing.pipeline import BillingAgent


def _summary(result):
    return [
        (
            record.document_info.reference_no,
            record.charge_details.charge_item,
            record.charge_details.amount,
            record.operation,
            record.financial_attributes.posting_type,
        )
        for record in result.records
    ]


def test_end_to_end_samples(settings: Settings, samples_dir: Path) -> None:
    agent = BillingAgent(settings)
    result = agent.run([samples_dir])

    assert result.summary["files"] == 4
    assert result.summary["documents"] == 4
    assert len(result.records) == 7

    got = _summary(result)
    assert (
        "DC-108478",
        "Handling Charge",
        65.0,
        "UPDATE",
        "AR",
    ) in got
    assert (
        "DC-108478",
        "Handling Charge",
        35.0,
        "NO_CHANGE",
        "AR",
    ) in got
    assert (
        "DC-108478",
        "Detention Charge",
        1310.4,
        "CREATE",
        "AR",
    ) in got
    assert (
        "CMDUCHN4053601",
        "On Carriage Charge",
        245.0,
        "NO_CHANGE",
        "AR",
    ) in got
    assert (
        "OOLU4055331281",
        "Handling Charge",
        50.0,
        "PENDING_REVIEW",
        None,
    ) in got
    assert (
        "ORFR1618731",
        "Wharfage",
        21.9,
        "PENDING_REVIEW",
        None,
    ) in got

    # UPDATE 只改了金额，其它字段继承台账
    updated = next(r for r in result.records if r.operation == "UPDATE")
    assert updated.audit_trail.matched_bill_id == "BILL-1001"
    assert [c["field"] for c in updated.audit_trail.field_changes] == ["amount"]
    assert updated.audit_trail.source["file"] == "PDF 示例账单2.pdf"

    # 台账已被更新/新增
    ledger_after = {line.bill_id: line for line in agent.ledger.lines}
    assert ledger_after["BILL-1001"].amount == 65.0
    assert any(line.charge_item == "Detention Charge" for line in agent.ledger.lines)


def test_ap_perspective(settings: Settings, samples_dir: Path) -> None:
    ap_settings = settings.with_overrides(
        accounting_entity="ALL AMERICA FORWARDING, INC."
    )
    agent = BillingAgent(ap_settings)
    result = agent.run([samples_dir / "图片示例.png"])

    assert len(result.records) == 2
    assert {r.financial_attributes.posting_type for r in result.records} == {"AP"}
    assert {r.operation for r in result.records} == {"NO_CHANGE"}

    matched = {r.charge_details.charge_item: r.audit_trail.match_rule for r in result.records}
    assert matched["Wharfage Administration Fee"] == "订舱号+费用名称+币种"
    assert matched["Wharfage"] == "箱号+交易双方+日期+金额"


def test_output_json_is_valid_and_complete(settings: Settings, samples_dir: Path, tmp_path: Path) -> None:
    agent = BillingAgent(settings)
    result = agent.run([samples_dir])
    paths = agent.write_outputs(result, tmp_path)

    payload = json.loads(Path(paths["results"]).read_text(encoding="utf-8"))
    assert isinstance(payload, list) and len(payload) == 7
    required = {
        "operation",
        "document_info",
        "transaction_party",
        "charge_details",
        "financial_attributes",
        "audit_trail",
    }
    for record in payload:
        assert required <= set(record)
        assert record["operation"] in {"CREATE", "UPDATE", "NO_CHANGE", "PENDING_REVIEW"}
        amount = record["charge_details"]["amount"]
        assert amount is None or isinstance(amount, (int, float))
        currency = record["charge_details"]["currency"]
        assert currency is None or (len(currency) == 3 and currency.isupper())
        assert isinstance(record["audit_trail"]["ai_reasoning"], list)
        assert record["audit_trail"]["ai_reasoning"]

    assert Path(paths["report"]).read_text(encoding="utf-8").startswith("# 航运智能账单助手")
    assert json.loads(Path(paths["run_log"]).read_text(encoding="utf-8"))["files"]


def test_duplicate_document_detection(settings: Settings, samples_dir: Path, tmp_path: Path) -> None:
    """同一份单据喂两遍：第二份必须被识别为重复，不能重复入账。"""
    import shutil

    copy_path = tmp_path / "PDF 示例账单2.pdf"
    shutil.copyfile(samples_dir / "PDF 示例账单2.pdf", copy_path)
    agent = BillingAgent(settings)
    result = agent.run([samples_dir / "PDF 示例账单2.pdf", copy_path])

    reviewed = [r for r in result.records if r.operation == "PENDING_REVIEW"]
    assert reviewed, "重复单据应转人工复核"
    assert any("重复" in reason for r in reviewed for reason in r.audit_trail.ai_reasoning)


@pytest.mark.parametrize("ocr_mode", ["cache", "text"])
def test_ocr_modes_offline(settings: Settings, samples_dir: Path, ocr_mode: str) -> None:
    agent = BillingAgent(settings.with_overrides(ocr_mode=ocr_mode))
    result = agent.run([samples_dir])
    assert result.records
    assert not result.failures
