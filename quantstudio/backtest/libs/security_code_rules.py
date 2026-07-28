"""Authoritative security-code classification and normalization rules.

This module is the only runtime authority for exchange suffixes and A-share board
classification.  It intentionally keeps historical BSE codes unchanged while
using the official BSE old-to-920 mapping to distinguish listed BSE securities
from unrelated 4xx/8xx NEEQ or delisted-board codes.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional


SECURITY_CODE_RULES_VERSION = "1.0.0"

_MAPPING_FILE = Path(__file__).with_name("bse_legacy_code_mapping.json")
try:
    _MAPPING_PAYLOAD = json.loads(_MAPPING_FILE.read_text(encoding="utf-8"))
    BSE_LEGACY_TO_920 = dict(_MAPPING_PAYLOAD.get("mapping", {}))
except (OSError, ValueError, TypeError):  # fail closed: never classify all 4xx/8xx as BSE
    BSE_LEGACY_TO_920 = {}


@lru_cache(maxsize=65536)
def bare_code(code) -> str:
    return str(code).strip().upper().split(".")[0]


@lru_cache(maxsize=65536)
def _suffix(code) -> str:
    text = str(code).strip().upper()
    return text.split(".", 1)[1] if "." in text else ""


_ST_PREFIXES = ("ST", "*ST", "S*ST", "SST")


def is_st_stock(code: str, name: Optional[str] = None) -> bool:
    """Return whether the supplied point-in-time security name is ST/*ST."""
    if not name:
        return False
    upper = str(name).strip().upper()
    return any(upper.startswith(prefix) for prefix in _ST_PREFIXES)


@lru_cache(maxsize=65536)
def is_bse_market(code: str) -> bool:
    """Return BSE equity membership without blanket 4xx/8xx inference."""
    bare = bare_code(code)
    return bare.startswith("920") or bare in BSE_LEGACY_TO_920


def is_star_market(code: str) -> bool:
    return bare_code(code).startswith("688")


def is_chinext(code: str) -> bool:
    return bare_code(code).startswith(("300", "301"))


def is_chinext_market(code: str) -> bool:
    return is_chinext(code)


def is_convertible_bond(code: str) -> bool:
    return bare_code(code).startswith(("110", "111", "113", "118", "123", "127", "128"))


def is_etf(code: str) -> bool:
    bare = bare_code(code)
    return bare.startswith((
        "159", "160", "161", "162", "163", "164", "165", "166", "167", "168",
        "169", "180", "501", "502", "505", "506", "508", "510", "511", "512",
        "513", "515", "516", "517", "518", "519", "520", "521", "522", "526",
        "530", "551", "560", "561", "562", "563", "588", "589",
    ))


def is_index(code: str) -> bool:
    bare = bare_code(code)
    suffix = _suffix(code)
    if bare.startswith(("399", "899")):
        return True
    # Shanghai index codes overlap Shenzhen equity bare codes; require explicit SH suffix.
    return bare.startswith("000") and suffix in {"SH", "SS", "XSHG"}


def is_main_board(code: str) -> bool:
    bare = bare_code(code)
    if is_etf(code) or is_convertible_bond(code) or is_index(code):
        return False
    return bare.startswith(("600", "601", "603", "605", "000", "001", "002", "003"))


def classify_security(code: str) -> str:
    # Index first: the BSE 50 index uses an explicit .BJ suffix but is not equity.
    if is_index(code):
        return "index"
    if is_bse_market(code):
        return "bse"
    if is_star_market(code):
        return "star_market"
    if is_chinext(code):
        return "chinext"
    if is_convertible_bond(code):
        return "convertible_bond"
    if is_etf(code):
        return "etf"
    if is_main_board(code):
        return "main_board"
    return "unknown"


@lru_cache(maxsize=65536)
def exchange(code: str) -> str:
    """Return SH/SZ/BJ, with exact BSE membership overriding a wrong alias."""
    bare = bare_code(code)
    if is_bse_market(bare):
        return "BJ"
    suffix = _suffix(code)
    if suffix in {"SH", "SS", "XSHG"}:
        return "SH"
    if suffix in {"SZ", "XSHE"}:
        return "SZ"
    if suffix in {"BJ", "XBJ", "XBSE"}:
        return "BJ"
    if bare.startswith(("110", "111", "113", "118")):
        return "SH"
    if bare.startswith(("123", "127", "128")):
        return "SZ"
    if bare.startswith(("5", "6", "9")):
        return "SH"
    if bare.startswith(("0", "1", "2", "3")):
        return "SZ"
    return "SH"  # preserve the framework's historical unknown-code fallback



def exchange_of(code: str) -> str:
    """Readable alias for callers that need the canonical exchange."""
    return exchange(code)


@lru_cache(maxsize=65536)
def normalize_security_code(code: str, target: Literal["qmt", "ptrade", "bare"] = "qmt") -> str:
    bare = bare_code(code)
    if target not in {"qmt", "ptrade", "bare"}:
        raise ValueError(f"unsupported normalization target: {target}")
    if target == "bare":
        return bare
    market = exchange(code)
    if target == "qmt":
        return f"{bare}.{'SH' if market == 'SH' else 'SZ' if market == 'SZ' else 'BJ'}"
    return f"{bare}.{'SS' if market == 'SH' else 'SZ' if market == 'SZ' else 'BJ'}"


def normalize_to_qmt(code: str) -> str:
    return normalize_security_code(code, "qmt")


def normalize_to_ptrade(code: str) -> str:
    return normalize_security_code(code, "ptrade")


def describe_security_code_rules() -> dict:
    """Return structured documentation derived from the runtime authority."""
    prefixes = sorted({code[:3] for code in BSE_LEGACY_TO_920})
    return {
        "version": SECURITY_CODE_RULES_VERSION,
        "supported_suffixes": {
            "sh": ["SH", "SS", "XSHG"],
            "sz": ["SZ", "XSHE"],
            "bj": ["BJ", "XBJ", "XBSE"],
        },
        "current_bse_prefixes": ["920"],
        "official_bse_legacy_prefixes": prefixes,
        "official_bse_legacy_mapping_count": len(BSE_LEGACY_TO_920),
        "bse_legacy_mapping_source": _MAPPING_PAYLOAD.get("source_url", ""),
        "unknown_code_fallback": "SH",
    }


def render_security_code_rules_markdown() -> str:
    """Render the human contract directly from runtime rule metadata."""
    info = describe_security_code_rules()
    prefix_text = ", ".join(
        f"`{prefix}`" for prefix in info["official_bse_legacy_prefixes"]
    )
    return f"""# Security Code and Market Classification Rules

> Rules version: `{info['version']}`  
> Runtime authority: `quantstudio/backtest/libs/security_code_rules.py`  
> Generated from module metadata; this document is never a runtime dependency.

## Supported suffix aliases

| Market | Accepted input | QMT output | PTrade output |
|---|---|---|---|
| Shanghai | `.SH` / `.SS` / `.XSHG` / bare | `.SH` | `.SS` |
| Shenzhen | `.SZ` / `.XSHE` / bare | `.SZ` | `.SZ` |
| Beijing | `.BJ` / `.XBJ` / `.XBSE` / bare | `.BJ` | `.BJ` |

## Beijing Stock Exchange boundary

- Current BSE equity range: `920`.
- Legacy compatibility uses the exact official old/new mapping, never a blanket `4`/`8` prefix rule.
- Official legacy mapping count: **{info['official_bse_legacy_mapping_count']}**.
- Legacy prefixes present in the official mapping: {prefix_text}.
- `400xxx`, `420xxx`, and arbitrary unmapped `8xxxxx` codes are not BSE equities.
- Mapping source: `{info['bse_legacy_mapping_source']}`.

## Classification precedence

`index -> bse -> star_market -> chinext -> convertible_bond -> etf -> main_board -> unknown`

## Compatibility policy

- Suffix aliases are normalized, but historical security numbers are not rewritten to `920`; this preserves historical-data lookup semantics.
- Unknown bare codes retain the pre-PR1 Shanghai fallback.
- ETF, index, and convertible-bond checks precede main-board checks to prevent overlapping-range misclassification.
"""
