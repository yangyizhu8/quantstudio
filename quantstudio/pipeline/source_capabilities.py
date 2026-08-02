"""Canonical data-source capability discovery for pipeline and GUI."""
from __future__ import annotations

from functools import lru_cache
from typing import Dict, List, Tuple

from .sources.akshare_adapter import AkshareAdapter
from .sources.astockdata_adapter import AStockDataAdapter
from .sources.baostock_adapter import BaostockAdapter
from .sources.tushare_adapter import TushareAdapter
from .sources.xtquant_adapter import XtquantAdapter
from .sources.mcp_adapter import MCPAdapter

ADAPTER_CLASSES = {
    "tushare": TushareAdapter,
    "baostock": BaostockAdapter,
    "akshare": AkshareAdapter,
    "xtquant": XtquantAdapter,
    "a_stock_data": AStockDataAdapter,
    "mcp": MCPAdapter,
}
KNOWN_SOURCES: Tuple[str, ...] = tuple(ADAPTER_CLASSES)
KNOWN_TABLE_FREQS: Tuple[Tuple[str, str], ...] = (
    ("stock_daily", "daily"), ("stock_minutes", "1min"),
    ("stock_minutes", "5min"), ("stock_minutes", "15min"),
    ("stock_minutes", "30min"), ("stock_minutes", "60min"),
    ("tick", "tick"),
    ("stock_float_share", "daily"), ("stock_daily_valuation", "daily"),
    ("index_constituents", "daily"), ("fin_indicator", "daily"),
    ("index_daily", "daily"), ("etf_daily", "daily"),
    ("etf_basic", "daily"),
    ("balance_statement", "daily"), ("income_statement", "daily"),
    ("cashflow_statement", "daily"), ("stock_dividend", "daily"),
    ("sw_industry", "daily"), ("etf_minutes", "1min"),
    ("etf_minutes", "5min"), ("etf_minutes", "15min"),
    ("etf_minutes", "30min"), ("etf_minutes", "60min"),
    ("industry_classification", "daily"), ("industry_membership", "daily"),
    ("stock_namechange", "daily"), ("stock_delist", "daily"),
)


@lru_cache(maxsize=1)
def capability_matrix() -> Dict[str, List[str]]:
    """Build the GUI matrix from adapter.supports_task(), the runtime truth."""
    matrix: Dict[str, List[str]] = {}
    for table, freq in KNOWN_TABLE_FREQS:
        current = matrix.setdefault(table, [])
        for source in KNOWN_SOURCES:
            try:
                adapter = object.__new__(ADAPTER_CLASSES[source])
                ok, _ = adapter.supports_task(table, freq)
            except Exception:
                ok = False
            if ok and source not in current:
                current.append(source)
    return matrix


def supported_sources(table: str) -> List[str]:
    return list(capability_matrix().get(table, ()))


def supports_task(source: str, table: str, freq: str) -> Tuple[bool, str]:
    """Query an adapter's declared capability without constructing clients."""
    cls = ADAPTER_CLASSES.get(source)
    if cls is None:
        return False, f"unregistered source: {source}"
    try:
        return cls.supports_task(object.__new__(cls), table, freq)
    except Exception as exc:
        return False, f"capability check failed: {exc}"
