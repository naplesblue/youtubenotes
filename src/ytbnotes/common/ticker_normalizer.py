"""
Ticker normalization helpers shared by tracker/verifier.

Goals:
1) Keep stored ticker readable/canonical (e.g. CIRCLE -> CRCL, RTN -> RTX).
2) Convert canonical ticker to yfinance market symbol when needed
   (e.g. SPX -> ^GSPC, BRK.B -> BRK-B).
"""

from __future__ import annotations

import re

_CANONICAL_RE = re.compile(r"^[A-Z0-9._-]{1,20}$")
_MARKET_RE = re.compile(r"^[A-Z0-9._^=-]{1,20}$")
_SPLIT_RE = re.compile(r"[\s(（\[]")
_PREFIX_RE = re.compile(r"^[A-Z]+:")
_TRAILING_PUNCT_RE = re.compile(r"[,:;，。；：]+$")

# Raw/legacy aliases -> canonical ticker
_CANONICAL_ALIASES: dict[str, str] = {
    "CIRCLE": "CRCL",
    "RTN": "RTX",
    "BLOCK": "SQ",
    "MARVEL": "MRVL",
    "FNS": "FAST",
    "FASTENAL": "FAST",
    "BRK": "BRK.B",
    "BRK/B": "BRK.B",
    "BRK-B": "BRK.B",
}

# Canonical ticker -> yfinance ticker
_MARKET_ALIASES: dict[str, str] = {
    "SPX": "^GSPC",
    "NDX": "^NDX",
    "DJI": "^DJI",
    "RUT": "^RUT",
    "IXIC": "^IXIC",
    "BRK.B": "BRK-B",
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "WTI": "CL=F",
    "USOIL": "CL=F",
    "BRENT": "BZ=F",
}


def _first_token(raw: str) -> str:
    token = str(raw or "").strip().upper()
    if not token:
        return ""
    token = token.lstrip("$")
    token = _PREFIX_RE.sub("", token, count=1)
    token = _SPLIT_RE.split(token, 1)[0]
    token = _TRAILING_PUNCT_RE.sub("", token)
    return token.strip()


def _fallback_by_company(company_name: str) -> str:
    c = str(company_name or "").strip().upper()
    if not c:
        return ""
    if "CIRCLE" in c:
        return "CRCL"
    if "RAYTHEON" in c:
        return "RTX"
    if "BLOCK" == c or c.startswith("BLOCK "):
        return "SQ"
    if "BERKSHIRE" in c:
        return "BRK.B"
    if "MARVELL" in c or "MARVEL" in c:
        return "MRVL"
    if "FASTENAL" in c:
        return "FAST"
    return ""


def normalize_ticker_symbol(raw_ticker: str, company_name: str = "") -> str:
    """
    Normalize ticker for internal storage/reporting.
    Returns canonical ticker or empty string if unusable.
    """
    base = _first_token(raw_ticker)
    if not base:
        return _fallback_by_company(company_name)

    mapped = _CANONICAL_ALIASES.get(base, base)
    if mapped == "SQL":
        company_mapped = _fallback_by_company(company_name)
        if company_mapped:
            return company_mapped

    if _CANONICAL_RE.match(mapped):
        return mapped

    company_mapped = _fallback_by_company(company_name)
    if company_mapped and _CANONICAL_RE.match(company_mapped):
        return company_mapped
    return ""


def market_ticker_candidates(raw_ticker: str, company_name: str = "") -> list[str]:
    """
    Produce candidate yfinance symbols in priority order.
    """
    canonical = normalize_ticker_symbol(raw_ticker, company_name)
    if not canonical:
        return []

    primary = _MARKET_ALIASES.get(canonical, canonical)
    out: list[str] = []
    seen: set[str] = set()

    def add(sym: str) -> None:
        s = str(sym or "").strip().upper()
        if not s or s in seen:
            return
        if not _MARKET_RE.match(s):
            return
        seen.add(s)
        out.append(s)

    add(primary)

    # BRK commonly appears without share class; canonical alias already maps BRK->BRK.B.
    if canonical == "BRK":
        add("BRK-B")
        add("BRK-A")

    # As a fallback for class-share notation, try dot->dash.
    if "." in primary and primary.startswith("BRK."):
        add(primary.replace(".", "-"))

    return out
