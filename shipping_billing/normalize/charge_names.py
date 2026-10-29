"""费用名称标准化。

题目要求：
* 将不同写法映射为统一的航运费用名称；
* **同时保存原始名称和标准化后的名称**；
* 表里给出的 6 组映射是必须覆盖的最低要求，这里扩充为常用海运费用词表。

设计要点：
1. 别名表用"最长优先 + 词边界"匹配，避免 ``DOC`` 命中 ``DOCK``、``HC`` 命中 ``CHC``；
2. 未命中词表时**不臆造**，保留原文并标记 ``standardized=False``；
3. 返回匹配到的具体别名，便于审计链说明"为什么归到这一类"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 标准名 -> 别名（第一项作为规范显示名）
STANDARD_ALIASES: dict[str, tuple[str, ...]] = {
    # —— 题目明确要求的 6 组 ——
    "Handling Charge": (
        "Handling Fee",
        "H/D Fee",
        "Handling Charge",
        "H/C",
        "H.C.",
        "HC",
        "Handing Charge",
        "Handling Charges",
        "Handling",
    ),
    "Documentation Fee": (
        "Documentation Fee",
        "Doc Fee",
        "Doc. Fee",
        "DOC",
        "DOC FEE",
        "Documents Fee",
        "Document Fee",
        "Documentation Charge",
    ),
    "Terminal Handling Charge": (
        "Terminal Handling Charge",
        "THC",
        "Terminal Handling Fee",
        "Terminal Handling",
    ),
    "Trucking Charge": (
        "Trucking Charge",
        "Trucking Fee",
        "Trucking",
        "Inland Trucking",
        "Truck Fee",
    ),
    "Customs Clearance Fee": (
        "Customs Clearance Fee",
        "Customs Fee",
        "Customs Clearance",
        "Custom Clearance Fee",
        "Customs Declaration Fee",
        "Customs Charge",
    ),
    "Storage Charge": (
        "Storage Charge",
        "Storage Fee",
        "Storage",
        "Storage Charges",
    ),
    # —— 扩充：海运常见费用 ——
    "Wharfage Administration Fee": ("Wharfage Administration Fee",),
    "Wharfage": ("Wharfage", "Warfage"),
    "Detention Charge": (
        "Detention Charge",
        "Detention Fee",
        "Detention",
        "Add'l Detention Free Time Charge",
        "Additional Detention Free Time Charge",
        "ADD'L DETENTION FREE TIME CHARGE",
        "Detention Free Time Charge",
    ),
    "Demurrage Charge": ("Demurrage", "Demurrage Charge"),
    "Ocean Freight": (
        "Ocean Freight",
        "Ocean Frt",
        "O/F",
        "Freight",
        "Basic Ocean Freight",
        "Sea Freight",
    ),
    "On Carriage Charge": (
        "On Carriage Additional",
        "On Carriage",
        "Oncarriage",
        "On-Carriage",
        "Rail Charge",
        "Rail Freight",
    ),
    "Bunker Adjustment Factor": ("Bunker Adjustment Factor", "BAF", "Bunker Surcharge"),
    "Currency Adjustment Factor": ("Currency Adjustment Factor", "CAF"),
    "Port Congestion Surcharge": ("Port Congestion Surcharge", "PCS"),
    "ISPS Charge": ("ISPS", "ISPS Charge", "ISPS Fee"),
    "AMS Fee": ("AMS", "AMS Fee", "AMS Charge", "Automated Manifest System"),
    "ENS Fee": ("ENS", "ENS Fee", "Entry Summary Declaration"),
    "Delivery Order Fee": ("Delivery Order Fee", "D/O Fee", "DO Fee", "Delivery Order"),
    "Container Cleaning Fee": ("Container Cleaning Fee", "Cleaning Fee", "Washing Fee"),
    "Repositioning Charge": ("Repositioning Charge", "Reposition Fee"),
    "Equipment Imbalance Surcharge": ("Equipment Imbalance Surcharge", "EIS"),
    "Peak Season Surcharge": ("Peak Season Surcharge", "PSS"),
    "Documentation Amendment Fee": ("Amendment Fee", "Documentation Amendment Fee"),
    "Telex Release Fee": ("Telex Release Fee", "Telex Fee"),
    "Customs Inspection Fee": ("Customs Inspection Fee", "Inspection Fee"),
    "Fumigation Fee": ("Fumigation Fee", "Fumigation"),
    "Insurance Fee": ("Insurance Fee", "Insurance Premium"),
    "Agency Fee": ("Agency Fee", "Agent Fee"),
    "Handling - Import": ("Import Handling",),
}

#: 逆序索引：归一化别名 -> 标准名
_ALIAS_TO_STANDARD: dict[str, str] = {}
for _standard, _aliases in STANDARD_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_STANDARD[_alias.upper()] = _standard


def normalize_key(name: str) -> str:
    text = (name or "").upper()
    text = text.replace("’", "'").replace("`", "'")
    text = re.sub(r"[^A-Z0-9/'.\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _build_pattern(alias: str) -> re.Pattern[str] | None:
    """别名的正则：空白弹性、词边界保护。"""
    key = normalize_key(alias)
    if not key:
        return None
    escaped = re.escape(key).replace(r"\ ", r"\s*")
    # 允许 H/C 与 H / C 两种写法
    escaped = escaped.replace(r"/", r"\s*/\s*")
    return re.compile(rf"(?<![A-Z0-9]){escaped}(?![A-Z0-9])")


#: 按别名长度降序，保证 "ADD'L DETENTION FREE TIME CHARGE" 先于 "Detention"
_ALIAS_PATTERNS: list[tuple[re.Pattern[str], str, str]] = []
for _alias, _standard in sorted(
    _ALIAS_TO_STANDARD.items(), key=lambda kv: len(kv[0]), reverse=True
):
    _pattern = _build_pattern(_alias)
    if _pattern is not None:
        _ALIAS_PATTERNS.append((_pattern, _standard, _alias))

_TRAILING_CODE_RE = re.compile(
    r"\s*[-–—]\s*(?:US|CN|HK|SG|EU|UK)\s*[A-Z0-9\-]{2,}$", re.IGNORECASE
)
_LEADING_INDEX_RE = re.compile(r"^\s*\d{1,2}[.)]?\s+(?=[A-Za-z])")
_MULTISPACE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class StandardizedName:
    original: str
    standard: str
    matched_alias: str | None
    known: bool
    confidence: float

    def __str__(self) -> str:  # pragma: no cover
        return self.standard


def clean_raw_name(raw: str) -> str:
    """去掉行号、列分隔符、末尾区域代码等噪声，用于显示与兜底标准化。"""
    name = (raw or "").strip()
    name = _LEADING_INDEX_RE.sub("", name)
    name = _TRAILING_CODE_RE.sub("", name)
    name = re.sub(r"\s*[-–—]\s*$", "", name)
    name = _MULTISPACE_RE.sub(" ", name).strip(" -–—:.,")
    return name


def _titlecase(name: str) -> str:
    if name.isupper():
        small = {"OF", "FOR", "AND", "THE", "TO", "BY", "AT", "IN", "ON"}
        parts = []
        for word in name.split():
            parts.append(word.capitalize() if word.upper() not in small else word.lower())
        return " ".join(parts)
    return name


def standardize(raw_name: str, *, use_fuzzy: bool = True) -> StandardizedName:
    """把原始费用名映射为标准名。"""
    original = (raw_name or "").strip()
    cleaned = clean_raw_name(original)
    key = normalize_key(cleaned)

    if key in _ALIAS_TO_STANDARD:
        standard = _ALIAS_TO_STANDARD[key]
        return StandardizedName(original, standard, cleaned, True, 0.98)

    for pattern, standard, alias in _ALIAS_PATTERNS:
        if pattern.search(cleaned):
            # 别名只占原文一部分时置信度略降（可能是复合费用）
            coverage = len(alias) / max(1, len(key))
            confidence = 0.92 if coverage > 0.6 else 0.8
            return StandardizedName(original, standard, alias, True, confidence)

    if use_fuzzy:
        from difflib import SequenceMatcher

        best: tuple[float, str, str] | None = None
        for alias_key, standard in _ALIAS_TO_STANDARD.items():
            if abs(len(alias_key) - len(key)) > 8:
                continue
            ratio = SequenceMatcher(None, key, alias_key).ratio()
            if ratio >= 0.9 and (best is None or ratio > best[0]):
                best = (ratio, standard, alias_key)
        if best is not None:
            return StandardizedName(original, best[1], best[2], True, round(0.62 + best[0] * 0.2, 2))

    # 未收录：保留原文（去噪后），标记未标准化
    fallback = _titlecase(cleaned) if cleaned else original
    return StandardizedName(original, fallback, None, False, 0.5)


def find_alias_in_line(line: str) -> tuple[str, str, int, int] | None:
    """在一行文本中定位费用别名，返回 (标准名, 命中的别名, start, end)。"""
    best: tuple[str, str, int, int] | None = None
    upper = (line or "").upper()
    for pattern, standard, alias in _ALIAS_PATTERNS:
        match = pattern.search(upper)
        if not match:
            continue
        if best is None or (match.end() - match.start()) > (best[3] - best[2]):
            best = (standard, alias, match.start(), match.end())
    return best


__all__ = [
    "STANDARD_ALIASES",
    "StandardizedName",
    "clean_raw_name",
    "find_alias_in_line",
    "normalize_key",
    "standardize",
]
