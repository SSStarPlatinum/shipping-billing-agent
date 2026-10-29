"""表格型费用明细解析（Excel / Word 表格 / OCR 返回的 tables）。

与文本行解析的区别：这里有真实的列，因此用**表头同义词映射**定位列，
比正则猜列更稳，也更容易解释（"第 3 列是金额"）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..io_utils import parse_number

#: 列语义 -> 表头候选词（统一大写比较）
HEADER_SYNONYMS: dict[str, tuple[str, ...]] = {
    "charge": (
        "CHARGE",
        "CHARGE ITEM",
        "CHARGE NAME",
        "DESCRIPTION",
        "DESC",
        "ITEM",
        "PARTICULARS",
        "PARTICULAR",
        "FEE",
        "费用",
        "费用名称",
        "费用项目",
        "收费项目",
        "项目",
        "名目",
    ),
    "amount": (
        "AMOUNT",
        "AMT",
        "TOTAL",
        "金额",
        "小计",
        "合计",
        "应付金额",
        "应收金额",
    ),
    "currency": ("CUR", "CCY", "CURRENCY", "币种", "货币", "币制"),
    "quantity": ("QTY", "QUANTITY", "数量", "件数", "箱量"),
    "unit_rate": ("RATE", "UNIT RATE", "UNIT PRICE", "PRICE", "单价"),
    "payment": ("P/C", "P C", "PAYMENT", "PAY TERM", "PAYMENT TERM", "付款方式", "预付/到付"),
    "remark": ("REMARK", "NOTE", "备注", "说明"),
}

_STOP_ROWS = (
    "TOTAL",
    "SUB TOTAL",
    "SUBTOTAL",
    "GRAND",
    "合计",
    "小计",
    "总计",
    "总额",
    "SAY",
    "REMARK",
    "备注",
)


@dataclass
class TableCharge:
    description: str
    amount: float
    amount_raw: str
    currency: str | None = None
    quantity: float | None = None
    unit_rate: float | None = None
    payment: str | None = None
    locator: str = ""
    line: str = ""


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().upper())


def _match_column(header: str) -> list[str]:
    """一个表头单元格可能同时命中多个语义（如 "AMOUNT USD"）。"""
    text = _norm(header)
    if not text:
        return []
    hits: list[str] = []
    for field, synonyms in HEADER_SYNONYMS.items():
        for syn in synonyms:
            syn_norm = _norm(syn)
            if text == syn_norm or re.search(rf"(?<![A-Z]){re.escape(syn_norm)}(?![A-Z])", text):
                hits.append(field)
                break
    return hits


def find_header_row(rows: list[list[str]]) -> tuple[int, dict[str, int]] | None:
    """在前 15 行里找最像表头的一行。"""
    best: tuple[int, dict[str, int]] | None = None
    best_score = 0
    for idx, row in enumerate(rows[:15]):
        mapping: dict[str, int] = {}
        for col, cell in enumerate(row):
            for field in _match_column(cell):
                mapping.setdefault(field, col)
        score = 0
        if "charge" in mapping:
            score += 2
        if "amount" in mapping:
            score += 3
        score += len(mapping)
        if "charge" in mapping and "amount" in mapping and score > best_score:
            best, best_score = (idx, mapping), score
    return best


def table_to_charges(
    rows: list[list[str]], *, locator: str = "", max_rows: int = 500
) -> list[TableCharge]:
    if not rows:
        return []
    header = find_header_row(rows)
    if header is None:
        return []
    header_index, mapping = header
    charge_col = mapping.get("charge", 0)
    amount_col = mapping.get("amount")

    charges: list[TableCharge] = []
    for row_index in range(header_index + 1, min(len(rows), header_index + 1 + max_rows)):
        row = rows[row_index]
        if not row or not any(cell.strip() for cell in row):
            continue
        first = _norm(row[0]) if row else ""
        joined = " ".join(_norm(c) for c in row)
        if any(first.startswith(prefix) or joined.startswith(prefix) for prefix in _STOP_ROWS):
            break

        name = row[charge_col].strip() if charge_col < len(row) else ""
        if not name:
            # 有些表把序号放在第 0 列
            name = next((c.strip() for c in row if c.strip() and not re.fullmatch(r"[\d.,%-]+", c.strip())), "")
        if not name:
            continue

        amount_text = ""
        if amount_col is not None and amount_col < len(row):
            amount_text = row[amount_col]
        if not amount_text.strip():
            numbers = [c for c in reversed(row) if re.fullmatch(r"[-(]?[\d,]+(?:\.\d+)?\)?", c.strip())]
            if not numbers:
                continue
            amount_text = numbers[0]
        parsed = parse_number(amount_text)
        if not parsed.ok or parsed.value is None:
            continue

        currency = None
        if "currency" in mapping and mapping["currency"] < len(row):
            currency = row[mapping["currency"]].strip().upper() or None
        quantity = None
        if "quantity" in mapping and mapping["quantity"] < len(row):
            quantity = parse_number(row[mapping["quantity"]]).value
        unit_rate = None
        if "unit_rate" in mapping and mapping["unit_rate"] < len(row):
            unit_rate = parse_number(row[mapping["unit_rate"]]).value
        payment = None
        if "payment" in mapping and mapping["payment"] < len(row):
            payment = row[mapping["payment"]].strip() or None

        charges.append(
            TableCharge(
                description=name,
                amount=abs(parsed.value),
                amount_raw=parsed.raw,
                currency=currency,
                quantity=quantity,
                unit_rate=unit_rate,
                payment=payment,
                locator=f"{locator} row {row_index + 1}" if locator else f"row {row_index + 1}",
                line=" | ".join(c for c in row if c.strip()),
            )
        )
    return charges


__all__ = ["HEADER_SYNONYMS", "TableCharge", "find_header_row", "table_to_charges"]
