from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

from quantstudio.backtest.events import import_strategy_event_csv
from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
from quantstudio.backtest.providers.duckdb_provider import DuckDBReferenceDataProvider
from quantstudio.backtest.ptrade_api import PtradeAPI


def make_db(path: Path):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE stock_daily(code VARCHAR, time BIGINT)")
    days = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"])
    rows = [("600000", int(day.tz_localize("Asia/Shanghai").timestamp() * 1000)) for day in days]
    con.executemany("INSERT INTO stock_daily VALUES (?, ?)", rows)
    con.close()


def test_import_and_query_two_unrelated_event_types(tmp_path):
    db = tmp_path / "events.duckdb"
    make_db(db)
    first_cover = tmp_path / "first_cover.csv"
    pd.DataFrame([{
        "\u53d1\u5e03\u65e5\u671f": "2026-01-02",
        "\u80a1\u7968\u4ee3\u7801": 600000,
        "\u8bc4\u7ea7": "\u4e70\u5165",
        "\u80a1\u7968\u540d\u79f0": "\u6d66\u53d1\u94f6\u884c",
        "\u884c\u4e1a": "\u94f6\u884c",
        "\u5238\u5546": "\u793a\u4f8b\u8bc1\u5238",
    }]).to_csv(first_cover, index=False, encoding="utf-8-sig")
    earnings = tmp_path / "earnings.csv"
    pd.DataFrame([{
        "announce_date": "2026-01-05", "ticker": "000001",
        "surprise": "positive", "company": "PingAn Bank",
    }]).to_csv(earnings, index=False, encoding="utf-8-sig")

    result1 = import_strategy_event_csv(db, first_cover, "first_cover", {
        "event_date": "\u53d1\u5e03\u65e5\u671f",
        "code": "\u80a1\u7968\u4ee3\u7801",
        "signal": "\u8bc4\u7ea7",
        "name": "\u80a1\u7968\u540d\u79f0",
        "category": "\u884c\u4e1a",
        "source": "\u5238\u5546",
    })
    result2 = import_strategy_event_csv(db, earnings, "earnings_surprise", {
        "event_date": "announce_date", "code": "ticker",
        "signal": "surprise", "name": "company",
    })
    assert result1["imported_rows"] == 1
    assert result2["imported_rows"] == 1

    access = DuckDBDataAccess(db)
    cover = access.query_strategy_events("first_cover", effective_date="2026-01-05")
    earn = access.query_strategy_events("earnings_surprise", effective_date="2026-01-06")
    assert cover.iloc[0]["code"] == "600000.SS"
    assert cover.iloc[0]["signal"] == "\u4e70\u5165"
    assert earn.iloc[0]["code"] == "000001.SZ"
    assert json.loads(earn.iloc[0]["payload"])["surprise"] == "positive"


def test_reference_and_public_api_route_event_queries(tmp_path):
    db = tmp_path / "events.duckdb"
    make_db(db)
    csv = tmp_path / "event.csv"
    pd.DataFrame([{
        "date": "2026-01-02", "code": "600000", "signal": "buy",
    }]).to_csv(csv, index=False)
    import_strategy_event_csv(db, csv, "generic", {
        "event_date": "date", "code": "code", "signal": "signal",
    }, encoding="utf-8")
    reference = DuckDBReferenceDataProvider(db)
    api = PtradeAPI(reference=reference)
    result = api.get_strategy_events("generic", effective_date="2026-01-05")
    assert result[["code", "signal"]].to_dict("records") == [
        {"code": "600000.SS", "signal": "buy"}
    ]


def test_order_at_price_routes_explicit_visible_price():
    calls = {}

    class Engine:
        def _immediate_execute(self, security, shares=None, prices=None, date=None, curr_data=None):
            calls.update({"security": security, "shares": shares, "prices": prices, "date": date})
            return "order-id"

        def refresh_portfolio(self, prices):
            calls["refresh"] = prices

    api = PtradeAPI()
    api._engine = Engine()
    api._current_date = "2026-01-05"
    api._current_day_data = pd.DataFrame({"code": ["600000"]})
    result = api.order_at_price("600000.SS", -100, 9.5)
    assert result == "order-id"
    assert calls["shares"] == -100
    assert calls["prices"] == {"600000.SH": 9.5}
    assert calls["refresh"] == {"600000.SH": 9.5}


def test_event_import_preserves_source_row_order(tmp_path):
    db = tmp_path / "ordered-events.duckdb"
    make_db(db)
    csv = tmp_path / "ordered.csv"
    pd.DataFrame([
        {"date": "2026-01-02", "code": "600001", "signal": "buy", "name": "first"},
        {"date": "2026-01-02", "code": "600002", "signal": "buy", "name": "second"},
    ]).to_csv(csv, index=False)
    import_strategy_event_csv(db, csv, "ordered", {
        "event_date": "date", "code": "code", "signal": "signal", "name": "name",
    }, encoding="utf-8")
    access = DuckDBDataAccess(db)
    result = access.query_strategy_events("ordered", effective_date="2026-01-05")
    assert result["source_row_id"].tolist() == [0, 1]
    assert result["name"].tolist() == ["first", "second"]
