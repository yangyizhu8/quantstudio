from __future__ import annotations

import pandas as pd

from tests.conftest import daily_row, make_providers


def test_proxy_profile_exposes_only_open_then_completed_daily_bar(build_db):
    from quantstudio.backtest.backtest_engine import BacktestEngine
    from quantstudio.backtest.ptrade_api import _api

    day = "2026-01-05"
    db = build_db(stock_daily=[
        daily_row("600000", day, close=12.0, open_p=10.0, high=13.0,
                  low=9.0, preclose=9.5, pctchg=(12.0 / 9.5 - 1) * 100),
    ])

    class Calendar:
        def get_trade_days(self, start, end):
            return [pd.Timestamp(day, tz="Asia/Shanghai").to_pydatetime()]

        def get_trading_day(self, date, offset=0):
            return pd.Timestamp(day).date()

    observed = {}

    def at_open(context):
        hist = _api.get_history(
            1, frequency="1m", field=["open", "high", "low", "close"],
            security_list="600000.SS", include=True, is_dict=True)
        frame = hist["600000.SS"]
        observed["open"] = frame.iloc[-1].to_dict()
        observed["open_dt"] = context.current_dt.strftime("%H:%M")

    def at_close(context):
        hist = _api.get_history(
            240, frequency="1m", field=["open", "high", "low", "close"],
            security_list="600000.SS", include=True, is_dict=True)
        frame = hist["600000.SS"]
        observed["close"] = {
            "rows": len(frame),
            "open": float(frame.iloc[0]["open"]),
            "high": float(frame["high"].max()),
            "low": float(frame["low"].min()),
            "close": float(frame.iloc[-1]["close"]),
        }
        observed["close_dt"] = context.current_dt.strftime("%H:%M")

    def initialize(context):
        _api.run_daily(context, at_open, time="09:31")
        _api.run_daily(context, at_close, time="15:00")

    engine = BacktestEngine(
        db_path=str(db), strategy={"initialize": initialize}, start=day, end=day,
        capital=100000, engine_profile="daily-open-close-proxy-v1",
        match_price_mode="close", providers=make_providers(db, Calendar()),
    )
    result, _output_dir = engine.run()

    assert observed["open_dt"] == "09:31"
    assert observed["open"] == {"open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0}
    assert observed["close_dt"] == "15:00"
    assert observed["close"] == {
        "rows": 2, "open": 10.0, "high": 13.0, "low": 9.0, "close": 12.0,
    }
    assert len(result.nav_history) == 1
    assert result.nav_history[0]["date"] == day


def test_proxy_profile_rejects_next_open():
    import pytest
    from quantstudio.backtest.backtest_engine import BacktestEngine

    with pytest.raises(ValueError, match="next_open"):
        BacktestEngine(
            db_path="unused.db", strategy={}, start="2026-01-05", end="2026-01-05",
            engine_profile="daily-open-close-proxy-v1", match_price_mode="next_open",
        )

