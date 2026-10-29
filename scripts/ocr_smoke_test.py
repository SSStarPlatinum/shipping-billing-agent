#!/usr/bin/env python
"""OCR 网关连通性冒烟测试。

OCR 服务需要能自己下载文件，所以必须给一个**公网可访问**的 PDF/图片地址：

    python scripts/ocr_smoke_test.py https://example.com/bill.pdf

会用题目给出的协议真实调用一次网关，并打印解析结果（文字前 800 字 / 表格数 / 置信度）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shipping_billing.config import Settings
from shipping_billing.ingest.ocr_client import OcrClient, OcrError


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    url = argv[1]
    settings = Settings.from_env(ocr_mode="api")
    client = OcrClient(settings)

    print(f"endpoint : {settings.ocr_url}")
    print(f"task     : {settings.ocr_task}")
    print(f"file url : {url}")
    print("-" * 60)
    try:
        outcome = client.process_url(url)
    except OcrError as exc:
        print(f"[FAIL] OCR 调用失败: {exc}")
        return 1

    print(f"[OK] source={outcome.source} confidence={outcome.confidence}")
    print(f"tables   : {len(outcome.tables)}")
    for index, table in enumerate(outcome.tables[:3], start=1):
        print(f"  table {index}: {len(table.rows)} rows x {len(table.rows[0]) if table.rows else 0} cols")
    print("-" * 60)
    text = outcome.text or ""
    print(text[:800] + ("..." if len(text) > 800 else ""))
    print("-" * 60)
    print(f"text chars: {len(text)}")
    if outcome.warnings:
        print("warnings  :")
        for warning in outcome.warnings:
            print(f"  - {warning}")
    return 0 if text else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
