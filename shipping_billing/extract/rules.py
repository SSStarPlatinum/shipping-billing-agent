"""规则抽取：单据类型、编号、日期、交易双方、币种、合计、业务标识、费用明细。

为什么"规则优先"？
* 金额、编号这类字段必须**可验证、可复现**，不能交给概率模型拍脑袋；
* 规则跑不出结果时才会让 LLM 补充（见 ``llm_extractor.py``），并且两者
  交叉校验，冲突就降置信度 / 转人工。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..io_utils import (
    company_similarity,
    contains_cjk,
    get_logger,
    last_number,
    parse_number,
    truncate,
)
from ..models import (
    DocumentIdentifiers,
    ExtractedDocument,
    Provenance,
    RawDocument,
)
from ..normalize.charge_names import find_alias_in_line, standardize
from ..normalize.currency import ISO4217_CODES, normalize_currency
from ..normalize.payment_terms import detect_payment_term_in_text, normalize_payment_term
from .rows import (
    iter_charge_sections,
    parse_charge_row,
)
from .tables import table_to_charges

logger = get_logger("rules")


# --------------------------------------------------------------------------- #
# 单据类型
# --------------------------------------------------------------------------- #
_TITLE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Debit Note", re.compile(r"^\s*(?:DEBIT\s+NOTE|DEBIT\s+MEMO)\b", re.I)),
    ("Credit Note", re.compile(r"^\s*(?:CREDIT\s+NOTE|CREDIT\s+MEMO)\b", re.I)),
    ("Invoice", re.compile(r"^\s*(?:COMMERCIAL\s+|FREIGHT\s+)?INVOICE\b", re.I)),
    ("Arrival Notice", re.compile(r"^\s*ARRIVAL\s+NOTICE\b", re.I)),
    ("Bill of Lading", re.compile(r"^\s*(?:BILL\s+OF\s+LADING|B/L)\b", re.I)),
    ("Statement", re.compile(r"^\s*(?:STATEMENT|ACCOUNT\s+STATEMENT)\b", re.I)),
    ("Debit Note", re.compile(r"^\s*DEBIT\s+INVOICE\b", re.I)),
)


def detect_document_type(text: str, *, subject: str | None = None, filename: str = "") -> tuple[str | None, float, str]:
    """返回 (类型, 置信度, 依据)。"""
    if subject and re.search(r"PRE[\s\-]*ALERT", subject, re.I):
        return "Pre-Alert", 0.9, "邮件主题包含 PRE-ALERT"

    head_lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()][:10]
    for line in head_lines:
        for doc_type, pattern in _TITLE_RULES:
            if pattern.search(line):
                return doc_type, 0.95, f"标题行 '{truncate(line, 40)}'"

    upper = (text or "").upper()
    if "TOTAL CHARGES TO BE PAID" in upper or (
        upper.count("ARRIVAL NOTICE") >= 1 and "FREE TIME" in upper
    ):
        return "Arrival Notice", 0.72, "正文包含 ARRIVAL NOTICE / TOTAL CHARGES TO BE PAID"
    if "BILL OF LADING" in upper and "SHIPPER" in upper and "CONSIGNEE" in upper:
        return "Bill of Lading", 0.7, "正文包含 BILL OF LADING / SHIPPER / CONSIGNEE"
    if re.search(r"\bCREDIT\s+NOTE\b", upper):
        return "Credit Note", 0.65, "正文包含 CREDIT NOTE"
    if re.search(r"\bDEBIT\s+NOTE\b", upper):
        return "Debit Note", 0.65, "正文包含 DEBIT NOTE"
    if re.search(r"\bINVOICE\b", upper):
        return "Invoice", 0.6, "正文包含 INVOICE"
    if re.search(r"\bPRE[\s\-]*ALERT\b", upper):
        return "Pre-Alert", 0.6, "正文包含 PRE-ALERT"
    if "EML" == (filename.rsplit(".", 1)[-1].lower() if "." in filename else ""):
        return "Email", 0.4, "邮件未说明单据类型"
    return None, 0.2, "未能识别单据类型"


# --------------------------------------------------------------------------- #
# 公司名
# --------------------------------------------------------------------------- #
_COMPANY_HINT = re.compile(
    r"\b(?:CO\.?,?\s*LTD|COMPANY\s+LIMITED|LIMITED|LTD\.?|LLC|L\.L\.C\.?|INC\.?|"
    r"INCORPORATED|CORP\.?|CORPORATION|GMBH|B\.?V\.?|S\.?A\.?|PTE\.?\s+LTD|"
    r"FORWARDING|LOGISTICS|SHIPPING|LINES|TRADING|AGENCIES|AGENT|GROUP|"
    r"TRANSPORT|EXPRESS|MARITIME|INTERNATIONAL)\b",
    re.IGNORECASE,
)

#: 单据水印/生成平台标识，不属于交易双方
_WATERMARK_RE = re.compile(r"PILOT\s+AISHIP|PLTPLT|湃乐多|POWERED\s+BY", re.IGNORECASE)

_PARTY_BLOCK_PREFIXES = (
    "TEL",
    "FAX",
    "EMAIL",
    "E-MAIL",
    "WEB",
    "HTTP",
    "BENEFICIARY",
    "BANK",
    "ROUTING",
    "ACCOUNT",
    "ABA",
    "SWIFT",
    "TOTAL",
    "SUB",
    "GRAND",
    "PAGE",
    "DEBIT",
    "CREDIT",
    "INVOICE",
    "DATE",
    "NO.",
    "OUR FILE",
    "AGENT FILE",
    "SHIPPER",
    "CONSIGNEE",
    "NOTIFY",
    "ALSO NOTIFY",
    "SECOND NOTIFY",
    "AS AGENTS",
    "AS AGENT",
    "AGENT FOR",
    "ON BEHALF",
    "C/O",
    "ATTN",
    "PLEASE",
    "REF",
    "FILE",
    "VESSEL",
    "VOYAGE",
    "CARRIER",
    "MASTER",
    "BOOKING",
    "B/L",
    "MBL",
    "HBL",
    "AMS",
    "TERMS",
    "SAY",
    "ORIGINAL",
    "COPY",
    "GROSS",
    "MEASURE",
    "PKGS",
    "CBM",
    "KGS",
    "LBS",
    "ITEM",
)


def _trim_company(value: str) -> str:
    text = (value or "").strip(" :：,;.")
    text = re.split(r"\s{2,}", text)[0]
    text = re.split(r"\s+(?:AS\s+AGENTS?|ON\s+BEHALF)\b", text, flags=re.I)[0]
    text = re.split(r"\b(?:TEL|FAX|EMAIL|E-MAIL|ATTN|WEBSITE)\b", text, flags=re.I)[0]
    # 只去掉"确实是币种"的尾部三位代码，避免把 LTD/LLC/INC 当成币种切掉
    tail = re.search(r"\s+([A-Z]{3})$", text)
    if tail and tail.group(1) in ISO4217_CODES:
        text = text[: tail.start()]
    text = re.sub(r"\s*\([^)]{0,12}\)\s*$", "", text)  # 末尾 (LAX)
    text = re.sub(r"\s+", " ", text).strip(" ,;-")
    return truncate(text, 90)


def _looks_like_company(value: str) -> bool:
    text = (value or "").strip()
    if len(text) < 4 or len(text) > 95:
        return False
    if _WATERMARK_RE.search(text):
        return False
    if len(text.split()) < 2 and not _COMPANY_HINT.search(text):
        return False
    if re.search(r"@|https?://|www\.", text, re.I):
        return False
    letters = sum(ch.isalpha() for ch in text)
    if letters < 4:
        return False
    # 公司名一般是全大写或词首大写；小写词过多说明是句子片段（条款正文/免责声明）
    words = text.split()
    if len(words) >= 3:
        lowercase = sum(1 for word in words if word[:1].islower())
        if lowercase / len(words) > 0.25:
            return False
        if sum(1 for word in words if word.endswith(".")) >= 2:
            return False
    return True


def _company_candidates(lines: list[str], limit: int = 60) -> list[str]:
    from ..io_utils import normalize_company

    out: list[str] = []
    seen: set[str] = set()
    for line in lines[:limit]:
        text = line.strip(" |:;,.")
        if not text or len(text) > 95:
            continue
        if text.upper().startswith(_PARTY_BLOCK_PREFIXES):
            continue
        if contains_cjk(text) or _WATERMARK_RE.search(text):
            continue
        if not _COMPANY_HINT.search(text):
            continue
        if len(text.split()) > 9:
            continue
        letters = sum(ch.isalpha() for ch in text)
        if letters / max(1, len(text)) < 0.55:
            continue
        if not _looks_like_company(text):
            continue
        key = normalize_company(text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


_ISSUER_PATTERNS = (
    re.compile(r"GRAND\s+TOTAL\s+BALANCE\s+DUE\s+TO\s+(?P<v>[^\n]{3,90})", re.I),
    re.compile(r"BALANCE\s+DUE\s+TO\s+(?P<v>[^\n]{3,90})", re.I),
    re.compile(r"(?:ISSUED\s+BY|ISSUER|DOCUMENT\s+ISSUER)[ \t]*:?[ \t]*(?P<v>[^\n]{3,90})", re.I),
    re.compile(
        r"(?:ALL\s+PAYMENTS?\s+MUST\s+BE\s+ISSUED\s+TO|PAYMENTS?\s+TO|REMIT\s+TO|PAYABLE\s+TO)"
        r"[ \t]*:?[ \t]*(?P<v>[^\n]{3,90})",
        re.I,
    ),
    # 收款人常是银行或第三方账户名，不一定是单据签发方，故不作为签发方来源
)

_RECIPIENT_PATTERNS = (
    re.compile(r"(?:BILL\s*TO|BILLED\s*TO|INVOICE\s+TO|SOLD\s+TO)[ \t]*:?[ \t]*(?P<v>[^\n]{3,90})", re.I),
    re.compile(r"^(?:TO|MESSRS)[ \t]*:?[ \t]*(?P<v>[^\n]{3,90})", re.I),
    re.compile(r"(?:PAYER|DEBTOR|CUSTOMER)[ \t]*:?[ \t]*(?P<v>[^\n]{3,90})", re.I),
)

_CONSIGNEE_PATTERN = re.compile(r"CONSIGNEE[ \t]*:?[ \t]*(?P<v>[^\n]{3,90})", re.I)


def _count_party(candidates: list[str], text: str) -> dict[str, int]:
    upper = text.upper()
    return {c: upper.count(c.split("\n")[0].upper()) for c in candidates}


def extract_parties(
    text: str,
    *,
    doc: RawDocument,
    doc_type: str | None,
) -> tuple[str | None, str | None, float, list[str]]:
    notes: list[str] = []
    issuer: str | None = None
    recipient: str | None = None
    confidence = 0.5

    if doc.kind == "eml":
        meta = doc.metadata or {}
        issuer = _mailbox(meta.get("from"))
        recipient = _mailbox(meta.get("to"))
        if issuer:
            notes.append(f"签发方取自邮件头 From: {issuer}")
        if recipient:
            notes.append(f"接收方取自邮件头 To: {recipient}")
        return issuer, recipient, 0.85, notes

    for pattern in _ISSUER_PATTERNS:
        match = pattern.search(text)
        if match:
            candidate = _trim_company(match.group("v"))
            if _looks_like_company(candidate):
                issuer = candidate
                confidence = max(confidence, 0.88)
                notes.append(f"签发方由关键字规则识别: {issuer}")
                break

    if issuer is None:
        match = _CN_ISSUER.search(text)
        if match:
            candidate = _trim_company(match.group(1))
            if _looks_like_company(candidate):
                issuer = candidate
                confidence = max(confidence, 0.86)
                notes.append(f"签发方取自中文标签: {issuer}")

    for pattern in _RECIPIENT_PATTERNS:
        match = pattern.search(text)
        if match:
            candidate = _trim_company(match.group("v"))
            if _looks_like_company(candidate) and not _same_company(candidate, issuer):
                recipient = candidate
                confidence = max(confidence, 0.82)
                notes.append(f"接收方由关键字规则识别: {recipient}")
                break

    if recipient is None:
        match = _CN_RECIPIENT.search(text)
        if match:
            candidate = _trim_company(match.group(1))
            if _looks_like_company(candidate) and not _same_company(candidate, issuer):
                recipient = candidate
                confidence = max(confidence, 0.84)
                notes.append(f"接收方取自中文标签: {recipient}")

    if recipient is None and doc_type in {"Arrival Notice", "Bill of Lading", "Statement"}:
        match = _CONSIGNEE_PATTERN.search(text)
        if match:
            candidate = _trim_company(match.group("v"))
            if _looks_like_company(candidate) and not _same_company(candidate, issuer):
                recipient = candidate
                notes.append(f"接收方取自 CONSIGNEE 字段: {recipient}")

    lines = [ln for ln in text.split("\n") if ln.strip()]
    candidates = _company_candidates(lines)
    if candidates:
        counts = _count_party(candidates, text)
        ordered = sorted(
            candidates,
            key=lambda c: (-counts.get(c, 0), text.upper().find(c.upper())),
        )
        if issuer is None and ordered:
            issuer = _trim_company(ordered[0])
            confidence = min(confidence, 0.62)
            notes.append(f"签发方按抬头出现频次推断: {issuer}")
        if recipient is None:
            for candidate in ordered:
                trimmed = _trim_company(candidate)
                if not _same_company(trimmed, issuer) and _looks_like_company(trimmed):
                    recipient = trimmed
                    confidence = min(confidence, 0.6)
                    notes.append(f"接收方按抬头顺序推断: {recipient}")
                    break
    return issuer, recipient, confidence, notes


def _same_company(a: str | None, b: str | None) -> bool:
    return bool(a and b and company_similarity(a, b) >= 0.8)


def _mailbox(value: str | None) -> str | None:
    """从 'Name <addr>' 里取可读标识：优先显示名，否则取地址。"""
    if not value:
        return None
    from email.utils import parseaddr

    display, address = parseaddr(str(value))
    display = (display or "").strip().strip('"')
    if display and "@" not in display:
        return truncate(display, 60)
    return address or display or None


# --------------------------------------------------------------------------- #
# 编号 / 日期 / 币种 / 合计
# --------------------------------------------------------------------------- #
_REFERENCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Debit/Credit Note", re.compile(r"D/C\s*NO\.?\s*:?\s*([A-Z0-9][A-Z0-9\-/]{2,})", re.I)),
    ("Debit/Credit Note", re.compile(r"(?:DEBIT|CREDIT)\s+NOTE\s*(?:NO\.?|NUMBER|#)\s*:?\s*([A-Z0-9][A-Z0-9\-/]{2,})", re.I)),
    ("Invoice", re.compile(r"DEBIT\s+INVOICE\s*:?\s*([A-Z0-9][A-Z0-9\-/]{3,})", re.I)),
    ("Invoice", re.compile(r"\bINVOICE\s*(?:NO\.?|NUMBER|#)\s*:?\s*([A-Z0-9][A-Z0-9\-/]{3,})", re.I)),
    ("Arrival Notice", re.compile(r"\bB/L\s*(?:NO\.?|NUMBER|#)\s*:?\s*([A-Z0-9]{6,})", re.I)),
    ("Bill of Lading", re.compile(r"\bB/L\s*(?:NO\.?|NUMBER|#)\s*:?\s*([A-Z0-9]{6,})", re.I)),
)

_DATE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("D/C DATE", re.compile(r"D/C\s*DATE\s*:?\s*(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4})", re.I)),
    ("INVOICE DATE", re.compile(r"INVOICE\s*DATE\s*:?\s*(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4})", re.I)),
    ("ISSUE DATE", re.compile(r"(?:DATE\s+OF\s+ISSUE|ISSUED\s+DATE|ISSUE\s+DATE)\s*:?\s*(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4})", re.I)),
    ("DOCUMENT DATE", re.compile(r"(?:DOCUMENT\s+DATE|DATE)\s*:?\s*(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4})", re.I)),
)

_CURRENCY_PATTERNS = (
    re.compile(r"(?:CURRENCY|CCY|CURR\.?)\s*:?\s*([A-Z]{3})\b"),
    re.compile(r"(?:CUR)\s*:?\s*([A-Z]{3})\b"),
)

_TOTAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"TOTAL\s+AMOUNT\s*:?\s*[A-Z]{0,3}\s*([\d,]+\.?\d*)", re.I),
    re.compile(r"TOTAL\s+CHARGES?\s+TO\s+BE\s+PAID\s*:?\s*([\d,]+\.?\d*)", re.I),
    re.compile(r"GRAND\s+TOTAL[^0-9\n]{0,40}([\d,]+\.\d{2})", re.I),
    re.compile(r"^\s*TOTAL\s*:?\s*([\d,]+\.\d{2})", re.I | re.M),
    re.compile(r"BALANCE\s+DUE[^0-9\n]{0,40}([\d,]+\.\d{2})", re.I),
    re.compile(r"^\s*([A-Z]{3})\s+([\d,]+\.\d{2})\s*$", re.M),
)

_SUBTOTAL_RE = re.compile(r"(?:SUB\s*TOTAL|小计)\s*:?\s*([\d,]+\.?\d*)", re.I)
_TAX_RE = re.compile(r"(?:TAX|VAT|GST|税额)\s*:?\s*([\d,]+\.?\d*)", re.I)

# ---------------------------------------------------------------------------
# 中文 / 表格型单据的字段标签（Excel、Word 常见）
# ---------------------------------------------------------------------------
_CN_REFERENCE = re.compile(
    r"(?:单据编号|账单编号|发票号码|发票号|费用单号|单号)[\s|:]*([A-Z0-9][A-Z0-9\-/]{2,})", re.I
)
_CN_DATE = re.compile(
    r"(?:单据日期|账单日期|开票日期|费用日期)[\s|:]*(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4})"
)
_CN_ISSUER = re.compile(r"(?:签发方|开票方|出票方)[\s|:]*([^|\n]{3,90})")
_CN_RECIPIENT = re.compile(r"(?:接收方|收票方|付款方|客户名称)[\s|:]*([^|\n]{3,90})")
_CN_CURRENCY = re.compile(r"(?:币种|货币|结算币种)[\s|:]*([A-Z]{3})")


def normalize_date_token(token: str) -> str | None:
    """把 10-24-2026 / 10/03/2025 / 2026-10-15 统一成 ISO 日期。"""
    if not token:
        return None
    parts = re.split(r"[-/.]", token.strip())
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    a, b, c = (int(p) for p in parts)
    if len(parts[0]) == 4:
        year, month, day = a, b, c
    elif len(parts[2]) == 4:
        year = c
        if a > 12 and b <= 12:
            day, month = a, b
        else:
            month, day = a, b
    else:
        return None
    if not (1900 < year < 2200 and 1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def extract_reference(text: str, doc_type: str | None) -> tuple[str | None, str | None]:
    match = _CN_REFERENCE.search(text)
    if match:
        return match.group(1).strip(" .,:;"), "中文标签(单据编号)"
    ordered = list(_REFERENCE_PATTERNS)
    ordered.sort(key=lambda item: 0 if item[0] == doc_type else 1)
    for _, pattern in ordered:
        match = pattern.search(text)
        if match:
            value = match.group(1).strip(" .,:;")
            if value.upper() in {"DATE", "NO", "NUMBER", "USD", "CNY"}:
                continue
            return value, pattern.pattern[:32]
    return None, None


def extract_document_date(text: str, doc_type: str | None, metadata: dict | None = None) -> str | None:
    match = _CN_DATE.search(text)
    if match:
        iso = normalize_date_token(match.group(1))
        if iso:
            return iso
    for _, pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            iso = normalize_date_token(match.group(1))
            if iso:
                return iso
    # 邮件用 Date 头
    if metadata:
        raw = metadata.get("date")
        if raw:
            from email.utils import parsedate_to_datetime

            try:
                return parsedate_to_datetime(raw).date().isoformat()
            except (TypeError, ValueError):
                pass
    return None


def extract_currency(text: str) -> tuple[str | None, str]:
    match = _CN_CURRENCY.search(text)
    if match:
        result = normalize_currency(match.group(1))
        if result.valid:
            return result.code, "币种标签"
    for pattern in _CURRENCY_PATTERNS:
        match = pattern.search(text)
        if match:
            result = normalize_currency(match.group(1))
            if result.valid:
                return result.code, "CURRENCY 字段"
    upper = text.upper()
    counts = {code: len(re.findall(rf"(?<![A-Z]){code}(?![A-Z])", upper)) for code in ("USD", "CNY", "EUR", "HKD", "SGD", "GBP", "JPY")}
    best = max(counts, key=lambda c: counts[c]) if counts else None
    if best and counts[best] > 0:
        return best, "文本中高频币种"
    if "$" in text:
        return "USD", "货币符号 $"
    if "¥" in text or "￥" in text:
        return "CNY", "货币符号 ¥"
    return None, "未识别"


def extract_totals(text: str) -> tuple[float | None, float | None, float | None, str | None]:
    total = None
    total_currency = None
    for index, pattern in enumerate(_TOTAL_PATTERNS):
        match = pattern.search(text)
        if not match:
            continue
        if pattern.groups == 2:  # `USD 26.90` 形式
            total_currency = match.group(1)
            value = parse_number(match.group(2)).value
        else:
            value = parse_number(match.group(1)).value
        if value is not None:
            total = abs(value)
            return None, None, total, total_currency
    subtotal_match = _SUBTOTAL_RE.search(text)
    subtotal = parse_number(subtotal_match.group(1)).value if subtotal_match else None
    tax_match = _TAX_RE.search(text)
    tax = parse_number(tax_match.group(1)).value if tax_match else None
    return subtotal, tax, total, total_currency


# --------------------------------------------------------------------------- #
# 业务标识
# --------------------------------------------------------------------------- #
_ID_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "mbl_no": (
        re.compile(r"(?:MASTER\s+B/?L|M\.?B\.?L\.?|MASTER\s+BILL\s+OF\s+LADING)\s*(?:NO\.?|NUMBER|#)?\s*:?\s*([A-Z]{2,6}[0-9][A-Z0-9]{4,})", re.I),
    ),
    "hbl_no": (
        re.compile(r"(?:HOUSE\s+B/?L|H\.?B\.?L\.?)\s*(?:NO\.?|NUMBER|#)?\s*:?\s*([A-Z]{2,6}[0-9][A-Z0-9]{4,})", re.I),
    ),
    "booking_no": (
        re.compile(r"(?:BOOKING\s*(?:NO\.?|NUMBER|#)|订舱号)\s*:?\s*([A-Z0-9][A-Z0-9\-]{4,})", re.I),
    ),
    "bl_no": (
        re.compile(r"(?:B/?L\s*(?:NO\.?|NUMBER|#)|BILL\s+OF\s+LADING\s*(?:NO\.?|NUMBER|#))\s*:?\s*([A-Z0-9]{6,})", re.I),
        re.compile(r"\b((?:CMDU|MAEU|OOLU|MSCU|COSU|EGLV|HLCU|ONEY|YMLU|ZIMU|SUDU|TGHU)[A-Z0-9]{6,})\b"),
    ),
    "invoice_no": (
        re.compile(r"(?:INVOICE|INV)\s*(?:NO\.?|NUMBER|#)\s*:?\s*([A-Z0-9][A-Z0-9\-/]{2,})", re.I),
    ),
    "ams_no": (
        re.compile(r"AMS\s*(?:NO\.?|NUMBER|#)?\s*:?\s*([A-Z0-9]{8,})", re.I),
    ),
    "agent_file_no": (
        re.compile(r"AGENT\s+FILE\s*NO\.?\s*:?\s*([A-Z0-9][A-Z0-9\-/]{2,})", re.I),
    ),
}

_CONTAINER_RE = re.compile(r"\b([A-Z]{3,4}\d{7})\b")
_VESSEL_RE = re.compile(r"VESSEL\s*(?:NAME)?\s*:?\s*([A-Z0-9][A-Z0-9 .'\-/]{2,40})", re.I)


def extract_identifiers(text: str, *, exclude: set[str] | None = None) -> DocumentIdentifiers:
    ids = DocumentIdentifiers()
    for name, patterns in _ID_PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                value = match.group(1).strip(" .,:;")
                if value.upper() in {"DATE", "NUMBER", "NO"}:
                    continue
                setattr(ids, name, value)
                break
    containers: list[str] = []
    bl_tokens = {
        ids.mbl_no,
        ids.hbl_no,
        ids.bl_no,
        ids.booking_no,
        ids.invoice_no,
        ids.reference_no,
        *(exclude or set()),
    }
    for match in _CONTAINER_RE.finditer(text):
        code = match.group(1)
        # 形如 CHN4053601 / ORFR1618731 的提单号、发票号同样满足"3~4 字母+7 数字"，需排除
        if code in bl_tokens:
            continue
        if len(code) == 10:  # 标准箱号是 4 字母+7 数字；10 位说明可能缺字母，需看上下文
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            line_end = len(text) if line_end == -1 else line_end
            context = text[line_start:line_end].upper()
            if re.search(r"B/?L\b|BILL|WAYBILL|INVOICE|BOOKING|REF\b|MASTER|HOUSE", context):
                continue
        if code not in containers:
            containers.append(code)
    ids.container_nos = containers[:10]
    vessel_match = _VESSEL_RE.search(text)
    if vessel_match:
        vessel = vessel_match.group(1).strip()
        ids.vessel = truncate(vessel, 40) or None
    if ids.mbl_no is None and ids.bl_no and ids.bl_no.upper().startswith(("CMDU", "MAEU", "OOLU", "MSCU", "COSU", "HLCU")):
        ids.mbl_no = ids.bl_no
    return ids


# --------------------------------------------------------------------------- #
# 费用明细
# --------------------------------------------------------------------------- #
@dataclass
class ChargeExtraction:
    charges: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    method: str = "rules"


def _make_charge(
    *,
    raw_name: str,
    amount: float,
    currency: str | None,
    default_currency: str | None,
    default_payment: str | None,
    confidence: float,
    source_line: str | None,
    provenance: Provenance | None,
    quantity: float | None = None,
    unit_rate: float | None = None,
    payment: str | None = None,
    sign: int = 1,
    repairs: list[str] | None = None,
):
    from ..models import ChargeLine

    alias_hit = find_alias_in_line(raw_name)
    if alias_hit is not None:
        standard = standardize(alias_hit[1])
        raw_display = raw_name[alias_hit[2]: alias_hit[3]].strip() or raw_name
        confidence = min(0.99, confidence + 0.06)
    else:
        standard = standardize(raw_name)
        raw_display = raw_name

    resolved_currency = currency or default_currency
    resolved_payment = normalize_payment_term(payment) or default_payment

    warnings: list[str] = [*repairs] if repairs else []
    if not standard.known:
        warnings.append(f"费用名称 '{raw_name}' 未在标准词表中，已保留原文")
    if resolved_currency is None:
        warnings.append("币种缺失")

    return ChargeLine(
        raw_name=raw_display,
        charge_item=standard.standard,
        standardized=standard.known,
        amount=round(amount, 2),
        currency=resolved_currency,
        quantity=quantity,
        unit_rate=unit_rate,
        payment_term=resolved_payment,
        amount_sign=sign,
        confidence=round(confidence, 2),
        source_line=source_line,
        provenance=provenance,
        warnings=warnings,
    )


def extract_charges(
    doc: RawDocument,
    *,
    default_currency: str | None,
    default_payment: str | None,
) -> ChargeExtraction:
    extraction = ChargeExtraction()
    seen_lines: set[tuple[str, str]] = set()
    table_keys: set[tuple[str, float]] = set()

    # 1) 表格
    for table in doc.tables:
        if table.is_empty():
            continue
        for item in table_to_charges(table.rows, locator=table.name or ""):
            provenance = Provenance(
                source_file=doc.filename,
                source_role="primary",
                kind=doc.kind,
                locator=item.locator or table.locator,
                extractor="table",
                ocr_source=doc.ocr_source,
                snippet=truncate(item.line, 160),
            )
            charge = _make_charge(
                raw_name=item.description,
                amount=item.amount,
                currency=item.currency,
                default_currency=default_currency,
                default_payment=default_payment,
                confidence=0.9,
                source_line=item.line,
                provenance=provenance,
                quantity=item.quantity,
                unit_rate=item.unit_rate,
                payment=item.payment,
            )
            key = (charge.charge_item, charge.source_line or "")
            if key in seen_lines:
                continue
            seen_lines.add(key)
            table_keys.add(((charge.charge_item or "").lower(), round(charge.amount or 0, 2)))
            extraction.charges.append(charge)

    # 2) 文本区块
    lines = doc.text.split("\n")
    for header_index, body in iter_charge_sections(lines):
        has_header = header_index >= 0
        for line_index, line in body:
            row = parse_charge_row(line)
            if row is None:
                continue
            alias = find_alias_in_line(row.description)
            if not has_header:
                # 没有表头时只接受"高置信布局"或"命中费用词表"的行
                strong = row.layout in {"multi_currency", "rate_qty_pc_amount"} and row.confidence >= 0.85
                if alias is None and not strong:
                    continue
                if alias is None and row.layout == "amount_only":
                    continue
            if row.amount <= 0:
                continue
            provenance = Provenance(
                source_file=doc.filename,
                source_role="primary",
                kind=doc.kind,
                locator=f"line {line_index + 1}" + (f" (section header line {header_index + 1})" if has_header else ""),
                extractor="rules",
                ocr_source=doc.ocr_source,
                snippet=truncate(line, 160),
            )
            charge = _make_charge(
                raw_name=row.description,
                amount=row.amount,
                currency=row.currency,
                default_currency=default_currency,
                default_payment=default_payment,
                confidence=row.confidence + (0.04 if has_header else 0.0),
                source_line=line,
                provenance=provenance,
                quantity=row.quantity,
                unit_rate=row.unit_rate,
                sign=row.sign,
                repairs=row.repairs,
            )
            key = (charge.charge_item, line.strip().lower())
            if key in seen_lines:
                extraction.warnings.append(f"检测到重复费用行，已去重: {truncate(line, 60)}")
                continue
            # 表格已经解析过的同一笔费用，不再从表格的文本投影里重复抽取
            if (charge.charge_item or "").lower() in {
                name for name, _ in table_keys
            } and ((charge.charge_item or "").lower(), round(charge.amount or 0, 2)) in table_keys:
                extraction.warnings.append(f"表格与文本重复，已去重: {truncate(line, 60)}")
                continue
            seen_lines.add(key)
            extraction.charges.append(charge)

    # 3) 完全没抓到费用时，尝试"别名 + 同行金额"的兜底扫描
    if not extraction.charges:
        fallback = _fallback_alias_scan(doc, default_currency=default_currency, default_payment=default_payment)
        extraction.charges.extend(fallback)
        if fallback:
            extraction.warnings.append("费用明细由别名兜底扫描得到，置信度较低")

    if doc.tables and not extraction.charges:
        extraction.warnings.append("存在表格但未识别出费用列")
    return extraction


def _fallback_alias_scan(
    doc: RawDocument, *, default_currency: str | None, default_payment: str | None
):
    out = []
    for index, line in enumerate(doc.text.split("\n")):
        alias = find_alias_in_line(line)
        if alias is None:
            continue
        number = last_number(line)
        if number is None:
            continue
        parsed = parse_number(number)
        if parsed.value is None or parsed.value <= 0:
            continue
        provenance = Provenance(
            source_file=doc.filename,
            kind=doc.kind,
            locator=f"line {index + 1}",
            extractor="rules-alias",
            ocr_source=doc.ocr_source,
            snippet=truncate(line, 160),
        )
        out.append(
            _make_charge(
                raw_name=line[: alias[3]].strip() or alias[1],
                amount=parsed.value,
                currency=None,
                default_currency=default_currency,
                default_payment=default_payment,
                confidence=0.5,
                source_line=line,
                provenance=provenance,
                repairs=[f"金额 {parsed.raw} 已做 OCR 纠错"] if parsed.repaired else None,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def extract_document_rules(doc: RawDocument) -> ExtractedDocument:
    text = doc.text if doc.text else ""
    meta = doc.metadata or {}
    result = ExtractedDocument(source=doc, raw_text=text)

    doc_type, type_conf, type_note = detect_document_type(
        text, subject=meta.get("subject"), filename=doc.filename
    )
    result.document_type = doc_type
    result.confidence["document_type"] = type_conf
    if type_note:
        result.warnings.append(f"单据类型判定: {type_note}")

    reference, ref_rule = extract_reference(text, doc_type)
    result.reference_no = reference

    result.document_date = extract_document_date(text, doc_type, meta)
    result.identifiers = extract_identifiers(
        text, exclude={reference} if reference else None
    )

    issuer, recipient, party_conf, party_notes = extract_parties(text, doc=doc, doc_type=doc_type)
    result.issuer, result.recipient = issuer, recipient
    result.confidence["parties"] = party_conf
    result.warnings.extend(party_notes)

    currency, _ = extract_currency(text)
    result.currency = currency
    subtotal, tax, total, total_currency = extract_totals(text)
    if total_currency:
        currency = normalize_currency(total_currency).code
        result.currency = currency
    result.subtotal = subtotal
    result.tax = tax
    result.total = total

    payment, payment_note = _extract_payment_term(text)
    result.payment_term = payment
    if payment_note:
        result.warnings.append(payment_note)

    charges = extract_charges(doc, default_currency=currency, default_payment=payment)
    result.charges = charges.charges
    result.warnings.extend(charges.warnings)

    if result.reference_no is None:
        fallback_ref = (
            result.identifiers.reference_no
            or result.identifiers.mbl_no
            or result.identifiers.hbl_no
            or result.identifiers.invoice_no
            or result.identifiers.bl_no
        )
        if fallback_ref:
            result.reference_no = fallback_ref
            result.warnings.append(f"未找到单据号，使用业务标识作为编号: {fallback_ref}")

    # 置信度
    charge_conf = (
        sum(c.confidence for c in result.charges) / len(result.charges) if result.charges else 0.0
    )
    result.confidence["charges"] = round(charge_conf, 2)
    result.confidence["reference_no"] = 0.9 if reference else (0.55 if result.reference_no else 0.1)
    result.confidence["document_date"] = 0.9 if result.document_date else 0.2
    result.confidence["currency"] = 0.9 if currency else 0.2
    result.method = "rules"
    logger.debug(
        "规则抽取完成 %s: type=%s ref=%s charges=%d",
        doc.filename,
        doc_type,
        result.reference_no,
        len(result.charges),
    )
    return result


def _extract_payment_term(text: str) -> tuple[str | None, str | None]:
    match = re.search(r"(?:TERMS?|PAY(?:MENT)?\s+TERM)\s*:?\s*([A-Z]{2,4})", text, re.I)
    if match:
        term = normalize_payment_term(match.group(1))
        if term:
            return term, f"付款方式取自 TERMS 字段: {match.group(1)}"
    term = detect_payment_term_in_text(text)
    if term:
        return term, f"付款方式由正文关键词推断: {term}"
    return None, None


__all__ = [
    "ChargeExtraction",
    "detect_document_type",
    "extract_charges",
    "extract_currency",
    "extract_document_date",
    "extract_document_rules",
    "extract_identifiers",
    "extract_parties",
    "extract_reference",
    "extract_totals",
    "normalize_date_token",
]
