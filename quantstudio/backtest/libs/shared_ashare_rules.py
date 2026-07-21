"""A-share trading rules built on the authoritative security-code module."""
from __future__ import annotations

from typing import Optional

from .security_code_rules import (
    is_bse_market,
    is_chinext_market,
    is_star_market,
    is_st_stock,
)


def get_price_limit_pct(code: str, name: Optional[str] = None) -> float:
    """Return the daily price-limit percentage for an A-share security."""
    if is_st_stock(code, name):
        return 0.05
    if is_star_market(code) or is_chinext_market(code):
        return 0.20
    if is_bse_market(code):
        return 0.30
    return 0.10


def is_price_limit_blocked(
    code: str,
    direction: int,
    pct_chg: Optional[float],
    name: Optional[str] = None,
) -> bool:
    """Whether a price limit blocks the requested direction."""
    if pct_chg is None:
        return False
    limit = get_price_limit_pct(code, name)
    tolerance = 0.001
    if direction == 1:
        return pct_chg >= limit - tolerance
    return pct_chg <= -limit + tolerance


def is_t1_blocked(entry_date, bar_date, t0_mode: bool = False) -> bool:
    """Return whether the A-share T+1 rule blocks a same-day sale."""
    if t0_mode:
        return False
    entry_str = str(entry_date)[:10] if entry_date else None
    bar_str = str(bar_date)[:10] if bar_date else None
    return bool(entry_str and bar_str and entry_str == bar_str)


def round_to_lot(raw_size: float, min_lot: int = 100) -> int:
    """Round a non-negative order size down to the configured lot."""
    return max(int(raw_size / min_lot) * min_lot, 0)


_TRADING_PERIODS = [
    ("093000", "113000"),
    ("130000", "150000"),
]


def is_trading_time(hhmmss: str) -> bool:
    """Return whether HHMMSS is inside the regular A-share sessions."""
    return any(start <= hhmmss <= end for start, end in _TRADING_PERIODS)
