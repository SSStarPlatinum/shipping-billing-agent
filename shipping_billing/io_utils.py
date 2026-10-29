"""通用小工具：哈希、路径、数字与文本处理、结构化日志。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

LOGGER_NAME = "shipping_billing"


def get_logger(name: str | None = None) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME if not name else f"{LOGGER_NAME}.{name}")
    return logger


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def short_id(value: str, length: int = 12) -> str:
    return value[:length]


def ensure_dir(path: Path | str) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_json(path: Path | str, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    with open(p, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def write_json(path: Path | str, data: Any) -> Path:
    p = Path(path)
    ensure_dir(p.parent)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return p


# --------------------------------------------------------------------------- #
# 数字
# --------------------------------------------------------------------------- #
_NUM_CLEAN_RE = re.compile(r"[^0-9.\-]")

#: OCR 常见字符混淆（数字位上的字母 → 数字）
OCR_DIGIT_CONFUSION = {"O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "l": "1",
                       "i": "1", "|": "1", "L": "1", "S": "5", "s": "5", "B": "8",
                       "Z": "2", "z": "2", "G": "6", "T": "7", "g": "9", "q": "9"}


class ParsedNumber:
    """一次数字解析的结果，附带是否发生过 OCR 纠错。"""

    __slots__ = ("value", "raw", "repaired", "ok")

    def __init__(self, value: float | None, raw: str, repaired: bool = False) -> None:
        self.value = value
        self.raw = raw
        self.repaired = repaired
        self.ok = value is not None

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"ParsedNumber(value={self.value}, raw={self.raw!r}, repaired={self.repaired})"


def parse_number(raw: str | None, *, allow_repair: bool = True) -> ParsedNumber:
    """把 "1,410.40" / "USD 5.00" / "(65.00)" / "1O.50" 解析成 float。

    - 支持千分位、括号负数、前后货币符号；
    - allow_repair=True 时，对纯数字位的 OCR 混淆字符做保守替换并标记 repaired。
    """
    if raw is None:
        return ParsedNumber(None, "", False)
    text = str(raw).strip()
    if not text:
        return ParsedNumber(None, "", False)

    repaired = False
    negative = text.startswith("(") and text.endswith(")")
    candidate = text.strip("()[] ")

    # 去掉货币符号与字母前先判断是否存在“数字位上的字母”
    if allow_repair and re.search(r"\d", candidate) and re.search(r"[A-Za-z|]", candidate):
        # 只处理形如 1O.50 / 21.O0 这种“数字串里混入了字母”的情况
        def _fix(match: re.Match[str]) -> str:
            nonlocal repaired
            token = match.group(0)
            if not re.search(r"\d", token):
                return token
            fixed = "".join(OCR_DIGIT_CONFUSION.get(ch, ch) for ch in token)
            if fixed != token and re.fullmatch(r"\d+(?:[.,]\d+)?", fixed):
                repaired = True
                return fixed
            return token

        candidate = re.sub(r"[0-9OoDQIl i|LSsBZzGTgq][0-9OoDQIl i|LSsBZzGTgq.,]*", _fix, candidate)

    cleaned = _NUM_CLEAN_RE.sub("", candidate)
    if cleaned in {"", "-", ".", "-."}:
        return ParsedNumber(None, text, repaired)
    # 多个小数点时保留最后一个作为小数分隔
    if cleaned.count(".") > 1:
        head, _, tail = cleaned.rpartition(".")
        cleaned = head.replace(".", "") + "." + tail
    try:
        value = float(Decimal(cleaned))
    except (InvalidOperation, ValueError):
        return ParsedNumber(None, text, repaired)
    if negative:
        value = -value
    return ParsedNumber(value, text, repaired)


_AMOUNT_TOKEN_RE = re.compile(r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?")
_CURRENCY_TOKEN_RE = re.compile(r"(?<![A-Z])([A-Z]{3})(?![A-Z])")


def extract_numbers(text: str) -> list[str]:
    return _AMOUNT_TOKEN_RE.findall(text)


def last_number(text: str) -> str | None:
    found = _AMOUNT_TOKEN_RE.findall(text)
    return found[-1] if found else None


def round2(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value) + 1e-9, 2)


# --------------------------------------------------------------------------- #
# 文本
# --------------------------------------------------------------------------- #
_WS_RE = re.compile(r"[ \t\u00a0\u3000]+")
_FULLWIDTH_MAP = {
    "（": "(",
    "）": ")",
    "：": ":",
    "，": ",",
    "。": ".",
    "；": ";",
    "－": "-",
    "—": "-",
    "–": "-",
    "￥": "¥",
}


def normalize_text(text: str) -> str:
    """统一全角/半角、空白与破折号，便于后续正则。"""
    if not text:
        return ""
    out = []
    for ch in text:
        out.append(_FULLWIDTH_MAP.get(ch, ch))
    text = "".join(out)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RE.sub(" ", text)
    lines = [line.strip() for line in text.split("\n")]
    # 折叠连续空行为一个
    collapsed: list[str] = []
    blank = 0
    for line in lines:
        if not line:
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        collapsed.append(line)
    return "\n".join(collapsed).strip()


def normalize_company(name: str | None) -> str:
    """公司名归一化，用于实体比对。"""
    if not name:
        return ""
    text = name.upper()
    text = re.sub(r"[\u4e00-\u9fff]", " ", text)  # 中文逐字拆开前先置空，单独处理
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    for token in (
        "CO LTD", "CO., LTD", "COMPANY LIMITED", "LIMITED", "LTD", "LLC", "INC",
        "INCORPORATED", "CORP", "CORPORATION", "GMBH", "SA", "SAS", "BV", "LLP",
        "INTERNATIONAL", "INTL", "LOGISTICS", "LOG", "SHIPPING", "GROUP",
    ):
        text = re.sub(rf"\b{re.escape(token)}\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def company_similarity(a: str | None, b: str | None) -> float:
    """0~1 的公司名相似度（归一化后比较 + 简单包含关系）。"""
    if not a or not b:
        return 0.0
    na, nb = normalize_company(a), normalize_company(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
        return 0.86 if len(shorter) >= 4 else 0.6
    # 词集合 Jaccard + 序列相似度取大者
    sa, sb = set(na.split()), set(nb.split())
    jaccard = len(sa & sb) / max(1, len(sa | sb))
    from difflib import SequenceMatcher

    ratio = SequenceMatcher(None, na, nb).ratio()
    return max(jaccard, ratio * 0.95)


def first_non_empty(values: Iterable[str | None]) -> str | None:
    for v in values:
        if v and str(v).strip():
            return str(v).strip()
    return None


def contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def truncate(text: str, limit: int = 300) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


__all__ = [
    "ParsedNumber",
    "company_similarity",
    "contains_cjk",
    "ensure_dir",
    "extract_numbers",
    "first_non_empty",
    "get_logger",
    "last_number",
    "normalize_company",
    "normalize_text",
    "parse_number",
    "read_json",
    "round2",
    "sha256_bytes",
    "sha256_file",
    "short_id",
    "truncate",
    "write_json",
]
