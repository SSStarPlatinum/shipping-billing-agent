"""币种标准化与 ISO 4217 校验。

题目要求：币种统一使用三位代码；校验是否符合 ISO 4217。
"""

from __future__ import annotations

import re

#: ISO 4217 现行货币代码（含常用资金代码）
ISO4217_CODES: frozenset[str] = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BOV
    BRL BSD BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUC CUP CVE
    CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD
    HNL HRK HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD
    KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN
    MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD
    RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP STN SVC SYP SZL THB TJS
    TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST
    XAF XAG XAU XBA XBB XBC XBD XCD XDR XOF XPD XPF XPT XSU XTS XUA XXX YER ZAR ZMW
    ZWL
    """.split()
)

#: 常见符号/写法 -> ISO 代码
SYMBOL_TO_CODE: dict[str, str] = {
    "$": "USD",
    "US$": "USD",
    "USD$": "USD",
    "U.S.$": "USD",
    "US DOLLAR": "USD",
    "USD": "USD",
    "DOLLAR": "USD",
    "€": "EUR",
    "EUR": "EUR",
    "£": "GBP",
    "GBP": "GBP",
    "¥": "CNY",
    "￥": "CNY",
    "RMB": "CNY",
    "CNY": "CNY",
    "人民币": "CNY",
    "元": "CNY",
    "HK$": "HKD",
    "HKD": "HKD",
    "NT$": "TWD",
    "TWD": "TWD",
    "S$": "SGD",
    "SGD": "SGD",
    "JPY": "JPY",
    "円": "JPY",
    "₩": "KRW",
    "KRW": "KRW",
    "AUD": "AUD",
    "CAD": "CAD",
    "CHF": "CHF",
}

_CODE_RE = re.compile(r"(?<![A-Z])([A-Z]{3})(?![A-Z])")


class CurrencyResult:
    __slots__ = ("code", "original", "valid", "note")

    def __init__(self, code: str | None, original: str | None, valid: bool, note: str = "") -> None:
        self.code = code
        self.original = original
        self.valid = valid
        self.note = note

    def __repr__(self) -> str:  # pragma: no cover
        return f"CurrencyResult(code={self.code!r}, valid={self.valid}, note={self.note!r})"


def normalize_currency(token: str | None, *, default: str | None = None) -> CurrencyResult:
    """把 "usd" / "US$" / "美元" / "¥" 等统一成 ISO 代码。"""
    if token is None or not str(token).strip():
        if default:
            return CurrencyResult(default, None, default in ISO4217_CODES, "使用单据默认币种")
        return CurrencyResult(None, None, False, "币种缺失")

    raw = str(token).strip()
    upper = raw.upper()

    if upper in SYMBOL_TO_CODE:
        return CurrencyResult(SYMBOL_TO_CODE[upper], raw, True)
    if raw in SYMBOL_TO_CODE:
        return CurrencyResult(SYMBOL_TO_CODE[raw], raw, True)
    if upper in ISO4217_CODES:
        return CurrencyResult(upper, raw, True)

    # "USD 245.00" 这类混合串里提取代码
    match = _CODE_RE.search(upper)
    if match and match.group(1) in ISO4217_CODES:
        return CurrencyResult(match.group(1), raw, True, "从混合文本中提取币种")

    if default:
        return CurrencyResult(default, raw, default in ISO4217_CODES, "无法识别，使用单据默认币种")
    return CurrencyResult(None, raw, False, f"非法币种: {raw}")


def is_valid_currency(code: str | None) -> bool:
    return bool(code) and code.upper() in ISO4217_CODES


__all__ = ["ISO4217_CODES", "SYMBOL_TO_CODE", "CurrencyResult", "is_valid_currency", "normalize_currency"]
