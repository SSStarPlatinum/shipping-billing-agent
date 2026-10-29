"""付款方式标准化：PP（预付）/ CC（到付）/ COD 等。"""

from __future__ import annotations

import re

#: 规范值
PREPAID = "PP"
COLLECT = "CC"
CASH_ON_DELIVERY = "COD"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bFREIGHT\s+PREPAID\b|\bPREPAID\b|\bPRE\s*-?\s*PAID\b|\bPP\b"), PREPAID),
    (re.compile(r"\bFREIGHT\s+COLLECT\b|\bCOLLECT\b|\bCC\b|\bPAY\s+AT\s+DEST(?:INATION)?\b"), COLLECT),
    (re.compile(r"\bCASH\s+ON\s+DELIVERY\b|\bCOD\b"), CASH_ON_DELIVERY),
]

#: 中文/业务口语
_CN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"预付"), PREPAID),
    (re.compile(r"到付|目的港付"), COLLECT),
]

VALID_TERMS = {PREPAID, COLLECT, CASH_ON_DELIVERY, "PREPAID", "COLLECT"}


def normalize_payment_term(value: str | None) -> str | None:
    """把各种写法归一到 PP / CC / COD；识别不了返回 None（不臆造）。"""
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    if text in {"PP", "PREPAID", "PRE-PAID", "PRE PAID"}:
        return PREPAID
    if text in {"CC", "COLLECT"}:
        return COLLECT
    if text in {"COD", "CASH ON DELIVERY"}:
        return CASH_ON_DELIVERY
    for pattern, term in _PATTERNS:
        if pattern.search(text):
            return term
    for pattern, term in _CN_PATTERNS:
        if pattern.search(str(value)):
            return term
    return None


def detect_payment_term_in_text(text: str) -> str | None:
    """从整段文本（如邮件正文）里推断付款方式，命中多个时优先 PREPAID。"""
    if not text:
        return None
    upper = text.upper()
    found: list[str] = []
    for pattern, term in _PATTERNS:
        if pattern.search(upper):
            found.append(term)
    if not found:
        for pattern, term in _CN_PATTERNS:
            if pattern.search(text):
                found.append(term)
    if not found:
        return None
    if PREPAID in found:
        return PREPAID
    return found[0]


__all__ = [
    "CASH_ON_DELIVERY",
    "COLLECT",
    "PREPAID",
    "VALID_TERMS",
    "detect_payment_term_in_text",
    "normalize_payment_term",
]
