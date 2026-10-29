"""费用行解析。

PDF/OCR 出来的都是"扁平文本"，没有真正的单元格。这里用**列布局模板**把
一行文本还原成一条费用记录：先尝试信息量最大的布局（含单价/数量/币种），
再逐级降级到只有金额的布局。

配合**区块定位**（先找到表头行，再往下读到 TOTAL/空行为止）大幅降低误报：
比如 B/L 里的箱号行 ``SAPU2040689 C9950614 20ST 233PIECE(S) 43762 706.00``
绝不能被当成一条 706 美元的费用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NUM = r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?"
_CUR = r"[A-Z]{3}"
_SYM = r"US\$|USD\$|HK\$|S\$|NT\$|[$¥€£]"

#: 明确不是费用行的起始关键字（表头、汇总、银行信息、运输条款等）
NON_CHARGE_PREFIXES: tuple[str, ...] = (
    "TOTAL",
    "SUB TOTAL",
    "SUBTOTAL",
    "SUB-TOTAL",
    "GRAND",
    "SAY",
    "BALANCE",
    "PAID",
    "AMOUNT DUE",
    "REMARK",
    "MEMO",
    "BENEFICIARY",
    "BANK",
    "ROUTING",
    "ACCOUNT",
    "ABA",
    "SWIFT",
    "TEL",
    "FAX",
    "EMAIL",
    "E-MAIL",
    "WEB",
    "HTTP",
    "DATE",
    "DUE DATE",
    "PAGE",
    "VESSEL",
    "VOYAGE",
    "SHIPPER",
    "CONSIGNEE",
    "NOTIFY",
    "PORT",
    "PLACE",
    "ETA",
    "ETD",
    "ATD",
    "ATA",
    "CURRENCY",
    "CNTR",
    "SEAL",
    "WEIGHT",
    "MEASURE",
    "PKG",
    "CBM",
    "KGS",
    "LBS",
    "SHIPPED",
    "RECEIVED",
    "ORIGINAL",
    "COPY",
    "BOOKING",
    "MASTER B/L",
    "HOUSE B/L",
    "BILL NO",
    "D/C NO",
    "OUR FILE",
    "AGENT FILE",
    "TERMS",
    "DEL ",
    "POL ",
    "POD ",
    "POR ",
    "F.DEST",
    "NO. OF",
    "DESCRIPTION",
    "UNIT",
    "QTY",
    "RATE",
    "ARRIVED",
    "CLEARANCE",
    "FIRMS CODE",
    "PAYMENT RECEIVED",
    "OBL RECEIVED",
    "PLEASE",
    "NOTE",
    "IF YOU",
    "THE ",
    "THIS ",
    "ALL ",
)


@dataclass
class RowParse:
    layout: str
    description: str
    amount: float
    amount_raw: str
    currency: str | None = None
    quantity: float | None = None
    unit_rate: float | None = None
    pc_flag: str | None = None
    line: str = ""
    confidence: float = 0.8
    repairs: list[str] = field(default_factory=list)
    sign: int = 1


_LAYOUTS: list[tuple[str, re.Pattern[str], dict[str, str], float]] = [
    # 1) 多币种发票：WHARFAGE ADMINISTRATION FEE C 5.00 USD 5.00 1.0000 USD 5.00
    (
        "multi_currency",
        re.compile(
            rf"^(?P<desc>.+?)\s+(?P<base>[A-Z*]{{1,3}})\s+(?P<rate>{NUM})\s+"
            rf"(?P<cur1>{_CUR})\s+(?P<amt1>{NUM})\s+(?P<roe>{NUM})\s+"
            rf"(?P<cur2>{_CUR})\s+(?P<amt2>{NUM})$"
        ),
        {"amount": "amt2", "currency": "cur2"},
        0.9,
    ),
    # 2) DEBIT NOTE：HANDLING CHARGE - 65.00 1.00 C 65.00
    (
        "rate_qty_pc_amount",
        re.compile(
            rf"^(?P<desc>.+?)\s+(?:(?P<rate>{NUM})\s+)?(?P<qty>{NUM})\s+"
            rf"(?P<pc>[PCD])\s+(?P<amount>{NUM})$"
        ),
        {"amount": "amount", "quantity": "qty", "unit_rate": "rate", "pc_flag": "pc"},
        0.9,
    ),
    # 3) 带符号：SHA pay TAR: H/C $50
    (
        "symbol_amount",
        re.compile(rf"^(?P<desc>.+?)\s+(?P<sym>{_SYM})\s*(?P<amount>{NUM})$"),
        {"amount": "amount", "currency": "sym"},
        0.86,
    ),
    # 4) 带三字母币种：On Carriage Additional - US EmOCA17 USD 245.0000
    (
        "currency_amount",
        re.compile(rf"^(?P<desc>.+?)\s+(?P<cur>{_CUR})\s+(?P<amount>{NUM})$"),
        {"amount": "amount", "currency": "cur"},
        0.86,
    ),
    # 5) 单价 * 数量 * 金额：Trucking 100.00 2 200.00
    (
        "rate_qty_amount",
        re.compile(
            rf"^(?P<desc>.+?)\s+(?P<rate>{NUM})\s+(?P<qty>{NUM})\s+(?P<amount>{NUM})$"
        ),
        {"amount": "amount", "quantity": "qty", "unit_rate": "rate"},
        0.78,
    ),
    # 6) 金额在前：HANDLING 65.00 USD
    (
        "amount_currency",
        re.compile(rf"^(?P<desc>.+?)\s+(?P<amount>{NUM})\s+(?P<cur>{_CUR})$"),
        {"amount": "amount", "currency": "cur"},
        0.8,
    ),
    # 7) 数量 + P/C + 金额（没有单价列）
    (
        "qty_pc_amount",
        re.compile(
            rf"^(?P<desc>.+?)\s+(?P<qty>{NUM})\s+(?P<pc>[PCD])\s+(?P<amount>{NUM})$"
        ),
        {"amount": "amount", "quantity": "qty", "pc_flag": "pc"},
        0.72,
    ),
    # 8) 仅金额：THC 350.00
    (
        "amount_only",
        re.compile(rf"^(?P<desc>.+?)\s+(?P<amount>{NUM})$"),
        {"amount": "amount"},
        0.58,
    ),
]


def _clean_desc(desc: str) -> str:
    text = desc.strip()
    text = re.sub(r"^[\s\-–—:|*]+", "", text)
    text = re.sub(r"[\s\-–—:|]+$", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def looks_like_charge_description(desc: str) -> bool:
    text = _clean_desc(desc)
    if len(text) < 3:
        return False
    upper = text.upper()
    # 纯币种（如合计行 "USD 26.90"）不是费用名称
    from ..normalize.currency import ISO4217_CODES

    if re.fullmatch(r"[A-Z]{3}", upper) and upper in ISO4217_CODES:
        return False
    for prefix in NON_CHARGE_PREFIXES:
        if upper.startswith(prefix):
            return False
    if re.search(r"@|https?://|www\.", text, re.IGNORECASE):
        return False
    # 箱号 (4 字母 + 7 数字)
    if re.search(r"\b[A-Z]{4}\d{7}\b", upper):
        return False
    # 提单号样式 HALF 数字，如 CHN4053601
    if re.fullmatch(r"[A-Z]{2,5}\d{6,}", upper.replace(" ", "")):
        return False
    # 日期开头
    if re.match(r"^\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}", text):
        return False
    letters = sum(ch.isalpha() for ch in text)
    if letters < 2:
        return False
    if letters / max(1, len(text)) < 0.25:
        return False
    # 纯大写单号（含 4 位以上数字连写）
    if re.fullmatch(r"[A-Z0-9\-/ ]{3,}", upper) and re.search(r"\d{5,}", upper):
        return False
    return True


def parse_charge_row(line: str) -> RowParse | None:
    """尝试把一行文本解析成一条费用。"""
    if not line or len(line) > 220:
        return None
    original = line.strip()
    if not original:
        return None
    text, repaired_line = repair_numeric_tokens(original)

    from ..io_utils import parse_number

    for name, pattern, mapping, confidence in _LAYOUTS:
        match = pattern.match(text)
        if not match:
            continue
        desc = _clean_desc(match.group("desc"))
        if not looks_like_charge_description(desc):
            continue
        amount_parsed = parse_number(match.group(mapping["amount"]))
        if not amount_parsed.ok or amount_parsed.value is None:
            continue
        amount = amount_parsed.value
        if abs(amount) >= 1_000_000_000:
            continue
        row = RowParse(
            layout=name,
            description=desc,
            amount=abs(amount),
            amount_raw=amount_parsed.raw,
            line=original,
            confidence=confidence,
            sign=-1 if amount < 0 else 1,
        )
        if amount_parsed.repaired:
            row.repairs.append(f"金额 {amount_parsed.raw} 存在 OCR 混淆字符，已修正为 {amount}")
        if repaired_line:
            row.repairs.append(f"识别到 OCR 易混字符，已按数字位还原: {original} → {text}")

        if "currency" in mapping:
            raw_cur = match.group(mapping["currency"])
            row.currency = _normalize_symbol_currency(raw_cur)
        if "quantity" in mapping:
            qty = parse_number(match.group(mapping["quantity"]))
            row.quantity = qty.value
        if "unit_rate" in mapping:
            rate = parse_number(match.group(mapping["unit_rate"]))
            row.unit_rate = rate.value
        if "pc_flag" in mapping:
            row.pc_flag = match.group(mapping["pc_flag"])

        # 括号负数（红字/冲销）
        if match.group(mapping["amount"]).strip().startswith("("):
            row.sign = -1
        return row
    return None


def _normalize_symbol_currency(symbol: str | None) -> str | None:
    if not symbol:
        return None
    table = {
        "$": "USD",
        "US$": "USD",
        "USD$": "USD",
        "HK$": "HKD",
        "S$": "SGD",
        "NT$": "TWD",
        "¥": "CNY",
        "￥": "CNY",
        "€": "EUR",
        "£": "GBP",
    }
    if symbol in table:
        return table[symbol]
    if re.fullmatch(r"[A-Z]{3}", symbol):
        return symbol
    return None


# --------------------------------------------------------------------------- #
# 区块定位
# --------------------------------------------------------------------------- #
_HEADER_PATTERNS = (
    re.compile(r"DESCRIPTION.{0,40}(AMOUNT|RATE|DEBIT)", re.IGNORECASE),
    re.compile(r"^CHARGES?\s+(CUR|CCY|CURRENCY)\b", re.IGNORECASE),
    re.compile(r"^CHARGES?\s+AMOUNT", re.IGNORECASE),
    re.compile(r"BASE.{0,15}RATE.{0,30}(AMOUNT|CURR)", re.IGNORECASE),
    re.compile(r"(CHARGE|ITEM|FEE).{0,20}(NAME|DESCRIPTION).{0,40}(AMOUNT|金额)", re.IGNORECASE),
    re.compile(r"费用(名称|项目)?.{0,20}(金额|单价)"),
    re.compile(r"^(序号\s*)?(费用|收费)(项目|名称|明细)"),
    re.compile(r"^PARTICULARS.{0,40}AMOUNT", re.IGNORECASE),
)

_STOP_PREFIXES = (
    "TOTAL",
    "SUB TOTAL",
    "SUBTOTAL",
    "SUB-TOTAL",
    "GRAND",
    "SAY",
    "BALANCE",
    "PAID AMOUNT",
    "REMARK",
    "MEMO",
    "BENEFICIARY",
    "BANK",
    "PLEASE NOTE",
    "NOTE:",
    "TERMS AND CONDITIONS",
    "THIS IS",
)

#: 形如 "1O.50" / "S0.00" 的数字位混淆 token（必须同时含数字与混淆字母）
_NUMISH_RE = re.compile(
    r"(?<![A-Za-z0-9])(?=[0-9OoIlSsBZzGQ]*\d)[0-9OoIlSsDq]{1,3}(?:[.,][0-9OoIlSsBZzGQ]{1,2})?(?![A-Za-z0-9])"
)


def repair_numeric_tokens(line: str) -> tuple[str, bool]:
    """把 OCR 混淆的数字 token（0/O、1/I、5/S）还原，返回 (新行, 是否修复过)。"""
    from ..io_utils import OCR_DIGIT_CONFUSION

    repaired = False

    def _fix(match: re.Match[str]) -> str:
        nonlocal repaired
        token = match.group(0)
        fixed = "".join(OCR_DIGIT_CONFUSION.get(ch, ch) for ch in token)
        if fixed != token and re.fullmatch(r"\d{1,3}(?:,\d{3})+\d*(?:\.\d+)?|\d+(?:\.\d+)?", fixed):
            repaired = True
            return fixed
        return token

    return _NUMISH_RE.sub(_fix, line), repaired


def is_charge_header(line: str) -> bool:
    text = (line or "").strip()
    if len(text) < 4:
        return False
    return any(p.search(text) for p in _HEADER_PATTERNS)


def is_stop_line(line: str) -> bool:
    text = (line or "").strip().upper()
    if not text:
        return False  # 空行单独处理
    return any(text.startswith(prefix) for prefix in _STOP_PREFIXES)


def iter_charge_sections(
    lines: list[str], max_lines_after_header: int = 40
) -> list[tuple[int, list[tuple[int, str]]]]:
    """返回 [(表头行号, [(行号, 文本)])]。

    没有表头时返回整篇文本作为"宽松区块"，由别名锚定再做精度过滤。
    """
    sections: list[tuple[int, list[tuple[int, str]]]] = []
    index = 0
    total = len(lines)
    while index < total:
        if is_charge_header(lines[index]):
            header_index = index
            body: list[tuple[int, str]] = []
            cursor = index + 1
            parsed_rows = 0
            while cursor < total and cursor - index <= max_lines_after_header:
                line = lines[cursor]
                if is_stop_line(line):
                    break
                if not line.strip():
                    if parsed_rows > 0:
                        break
                    cursor += 1
                    continue
                body.append((cursor, line))
                if parse_charge_row(line) is not None:
                    parsed_rows += 1
                cursor += 1
            if body:
                sections.append((header_index, body))
            index = cursor + 1
        else:
            index += 1
    if not sections:
        sections.append((-1, [(i, line) for i, line in enumerate(lines) if line.strip()]))
    return sections


__all__ = [
    "NON_CHARGE_PREFIXES",
    "RowParse",
    "is_charge_header",
    "is_stop_line",
    "iter_charge_sections",
    "looks_like_charge_description",
    "parse_charge_row",
]
