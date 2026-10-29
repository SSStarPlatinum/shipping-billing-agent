"""pytest 公共夹具。"""

from __future__ import annotations

from pathlib import Path

import pytest

from shipping_billing.config import PROJECT_ROOT, Settings

SAMPLES = PROJECT_ROOT / "data" / "samples"
LEDGER = PROJECT_ROOT / "data" / "ledger" / "existing_bills.json"
OCR_CACHE = PROJECT_ROOT / "data" / "ocr_cache"

PDF1 = SAMPLES / "PDF示例账单1.pdf"
PDF2 = SAMPLES / "PDF 示例账单2.pdf"
IMAGE = SAMPLES / "图片示例.png"
EML = next(SAMPLES.glob("*.eml"))


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        ocr_mode="cache",
        ledger_path=LEDGER,
        ocr_cache_dir=OCR_CACHE,
        accounting_entity="NEPTUNE SHIPPING LIMITED",
        work_dir=tmp_path / "work",
        output_dir=tmp_path / "out",
        llm_cache_dir=tmp_path / "llm",
        entity_aliases=("NEPTUNE SHIPPING LIMITED (LAX)",),
    )


@pytest.fixture()
def samples_dir() -> Path:
    return SAMPLES
