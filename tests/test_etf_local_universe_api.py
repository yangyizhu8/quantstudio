from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from quantstudio.backtest.providers.base import ReferenceDataCapabilityError
from quantstudio.backtest.providers.duckdb_provider import DuckDBReferenceDataProvider
from quantstudio.backtest.ptrade_api import PtradeAPI
from scripts.sync_etf_basic import build_payload, classify_etf


def _ms(date: str) -> int:
    return int(pd.Timestamp(date, tz="Asia/Shanghai").timestamp() * 1000)


def _make_db(path: Path, *, include_metadata: bool = True) -> Path:
    conn = duckdb.connect(str(path))
    try:
        conn.execute("CREATE TABLE etf_daily (code VARCHAR, time BIGINT, volume DOUBLE)")
        bars = [
            ("510050", _ms("2018-01-02"), 1.0),
            ("159915", _ms("2018-01-02"), 1.0),
            ("588000", _ms("2020-07-22"), 1.0),
            ("510900", _ms("2018-01-02"), 1.0),
            ("511010", _ms("2018-01-02"), 1.0),
            ("511990", _ms("2018-01-02"), 1.0),
            ("518880", _ms("2018-01-02"), 1.0),
            ("159980", _ms("2018-01-02"), 1.0),
            ("510999", _ms("2020-01-02"), 1.0),
        ]
        conn.executemany("INSERT INTO etf_daily VALUES (?, ?, ?)", bars)
        if include_metadata:
            conn.execute("""
                CREATE TABLE etf_basic (
                    code VARCHAR, list_date BIGINT, delist_date BIGINT,
                    etf_type VARCHAR, is_cross_border BOOLEAN
                )
            """)
            rows = [
                ("510050", _ms("2005-02-23"), None, "equity", False),
                ("159915", _ms("2011-12-09"), None, "equity", False),
                ("588000", _ms("2020-07-22"), None, "equity", False),
                ("510900", _ms("2012-10-22"), None, "qdii", True),
                ("511010", _ms("2013-03-25"), None, "bond", False),
                ("511990", _ms("2013-01-28"), None, "money", False),
                ("518880", _ms("2013-07-29"), None, "gold", False),
                ("159980", _ms("2019-12-05"), None, "commodity", False),
                ("510999", _ms("2020-01-02"), _ms("2021-01-05"), "equity", False),
                ("510777", _ms("2018-01-02"), None, "equity", False),
            ]
            conn.executemany("INSERT INTO etf_basic VALUES (?, ?, ?, ?, ?)", rows)
    finally:
        conn.close()
    return path


def test_provider_returns_pit_domestic_equity_universe(tmp_path):
    provider = DuckDBReferenceDataProvider(_make_db(tmp_path / "etf.db"))

    assert provider.get_etf_list_local("2020-07-21") == ["159915", "510050", "510999"]
    assert provider.get_etf_list_local("2020-07-22") == ["159915", "510050", "510999", "588000"]
    assert provider.get_etf_list_local("2021-01-06") == ["159915", "510050", "588000"]


def test_provider_filters_types_and_respects_active_only(tmp_path):
    provider = DuckDBReferenceDataProvider(_make_db(tmp_path / "etf.db"))

    assert provider.get_etf_list_local("2021-01-06", etf_type="bond") == ["511010"]
    assert provider.get_etf_list_local("2021-01-06", etf_type="money") == ["511990"]
    assert provider.get_etf_list_local("2021-01-06", etf_type="gold") == ["518880"]
    assert provider.get_etf_list_local("2021-01-06", etf_type="qdii") == ["510900"]
    assert "510999" in provider.get_etf_list_local(
        "2021-01-06", etf_type="all", active_only=False
    )


def test_api_uses_current_backtest_date_and_returns_ptrade_suffixes(tmp_path):
    provider = DuckDBReferenceDataProvider(_make_db(tmp_path / "etf.db"))
    api = PtradeAPI(reference=provider)
    api._current_date = "2020-07-22"

    assert api.get_etf_list_local() == [
        "159915.SZ", "510050.SS", "510999.SS", "588000.SS"
    ]


def test_api_requires_runtime_date_when_query_date_omitted(tmp_path):
    api = PtradeAPI(reference=DuckDBReferenceDataProvider(_make_db(tmp_path / "etf.db")))
    with pytest.raises(ReferenceDataCapabilityError, match="active backtest date"):
        api.get_etf_list_local()


def test_missing_metadata_is_an_explicit_capability_error(tmp_path):
    provider = DuckDBReferenceDataProvider(
        _make_db(tmp_path / "etf_without_metadata.db", include_metadata=False)
    )
    with pytest.raises(ReferenceDataCapabilityError, match="sync_etf_basic.py"):
        provider.get_etf_list_local("2020-01-02")


def test_invalid_etf_type_is_rejected(tmp_path):
    provider = DuckDBReferenceDataProvider(_make_db(tmp_path / "etf.db"))
    with pytest.raises(ValueError, match="unsupported etf_type"):
        provider.get_etf_list_local("2020-01-02", etf_type="crypto")


def test_metadata_classifier_separates_local_equity_and_non_equity():
    cases = [
        ({"ts_code": "510300.SH", "name": "\u6caa\u6df1300ETF", "fund_type": "\u80a1\u7968\u578b"}, ("equity", False)),
        ({"ts_code": "513100.SH", "name": "\u7eb3\u6307ETF", "fund_type": "\u80a1\u7968\u578b"}, ("qdii", True)),
        ({"ts_code": "511010.SH", "name": "\u56fd\u503aETF", "fund_type": "\u503a\u5238\u578b"}, ("bond", False)),
        ({"ts_code": "511990.SH", "name": "\u8d27\u5e01ETF", "fund_type": "\u8d27\u5e01\u578b"}, ("money", False)),
        ({"ts_code": "518880.SH", "name": "\u9ec4\u91d1ETF", "fund_type": "\u5176\u4ed6"}, ("gold", False)),
        ({"ts_code": "159980.SZ", "name": "\u6709\u8272\u91d1\u5c5e\u671f\u8d27ETF", "fund_type": "\u5176\u4ed6"}, ("commodity", False)),
    ]
    for raw, expected in cases:
        etf_type, cross_border, _ = classify_etf(pd.Series(raw))
        assert (etf_type, cross_border) == expected


def test_build_payload_derives_missing_dates_from_daily_history():
    raw = pd.DataFrame([{
        "ts_code": "510999.SH", "name": "\u6d4b\u8bd5ETF", "benchmark": "\u6d4b\u8bd5\u6307\u6570",
        "fund_type": "\u80a1\u7968\u578b", "invest_type": None, "type": "\u80a1\u7968\u578b",
        "status": "D", "list_date": None, "delist_date": None,
    }])
    bounds = pd.DataFrame([{
        "code": "510999", "first_bar_ms": _ms("2020-01-02"),
        "last_bar_ms": _ms("2021-01-04"),
    }])
    payload = build_payload(raw, bounds)
    assert payload.loc[0, "list_date"] == _ms("2020-01-02")
    assert payload.loc[0, "delist_date"] == _ms("2021-01-05")

def test_ptrade_import_exports_local_etf_api():
    from quantstudio.backtest import ptrade_import
    assert callable(ptrade_import.get_etf_list_local)


def test_api_delegates_to_reference_provider_without_storage_access():
    class ReferenceStub:
        def __init__(self):
            self.calls = []

        def get_etf_list_local(self, **kwargs):
            self.calls.append(kwargs)
            return ["510050", "159915"]

    reference = ReferenceStub()
    api = PtradeAPI(reference=reference)
    result = api.get_etf_list_local("2020-01-02", etf_type="equity", active_only=True)
    assert result == ["510050.SS", "159915.SZ"]
    assert reference.calls == [{
        "query_date": "2020-01-02", "etf_type": "equity", "active_only": True,
    }]
