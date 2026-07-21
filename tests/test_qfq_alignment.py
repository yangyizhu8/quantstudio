import pandas as pd

from quantstudio.pipeline.aligner import FieldAligner, to_ms_timestamp


def _aligner(table="stock_daily"):
    columns = {name: {"type": "float", "required": False} for name in (
        "open", "high", "low", "close", "open_front", "high_front",
        "low_front", "close_front", "open_back", "high_back", "low_back", "close_back")}
    columns.update({"code": {"type": "str"}, "time": {"type": "int"}})
    return FieldAligner({"schemas": {table: {"columns": columns, "time_key": "time"}},
                         "source_mappings": {"tushare": {table: {"identity": True}}}})


def test_minute_bars_join_daily_adjustment_factor_by_trading_day():
    a = _aligner("stock_minutes")
    bars = pd.DataFrame({"code": ["600000", "600000"],
                         "time": [to_ms_timestamp("2026-01-02 09:31:00"),
                                  to_ms_timestamp("2026-01-05 09:31:00")],
                         "open": [10.0, 12.0], "high": [10.0, 12.0],
                         "low": [10.0, 12.0], "close": [10.0, 12.0]})
    adj = pd.DataFrame({"code": ["600000", "600000"],
                        "time": [to_ms_timestamp("2026-01-02"), to_ms_timestamp("2026-01-05")],
                        "adj_factor": [1.0, 2.0]})
    out = a._apply_qfq(bars, adj, "stock_minutes")
    assert out["close_front"].tolist() == [5.0, 12.0]
    assert out["close_back"].tolist() == [10.0, 24.0]


def test_adjustment_anchors_follow_time_not_numeric_min_max():
    a = _aligner()
    bars = pd.DataFrame({"code": ["600000", "600000", "600000"],
                         "time": [to_ms_timestamp(x) for x in ("2026-01-01", "2026-01-02", "2026-01-03")],
                         "close": [10.0, 10.0, 10.0]})
    adj = pd.DataFrame({"code": ["600000"] * 3, "time": bars["time"],
                        "adj_factor": [2.0, 1.0, 1.5]})
    out = a._apply_qfq(bars, adj, "stock_daily")
    assert out["close_front"].tolist() == [10 * 2 / 1.5, 10 / 1.5, 10.0]
    assert out["close_back"].tolist() == [10.0, 5.0, 7.5]


def test_native_adjustment_source_does_not_apply_factor_twice():
    rules = {"schemas": {"stock_daily": {"columns": {
        "code": {"type": "str"}, "time": {"type": "int"}, "close": {"type": "float"},
        "close_front": {"type": "float"}, "close_back": {"type": "float"}}}},
        "source_mappings": {"akshare": {"stock_daily": {"identity": True}}}}
    raw = pd.DataFrame({"code": ["600000"], "time": [to_ms_timestamp("2026-01-01")],
                        "close": [10.0], "close_front": [8.0], "close_back": [12.0]})
    adj = pd.DataFrame({"code": ["600000"], "time": raw["time"], "adj_factor": [3.0]})
    out, meta = FieldAligner(rules).align(raw, "stock_daily", "akshare", adj_factor_df=adj)
    assert out.loc[0, "close_front"] == 8.0
    assert "qfq_native_passthrough:akshare" in meta["applied_steps"]


def test_requested_minute_frequency_is_preserved():
    rules = {"schemas": {"stock_minutes": {"columns": {
        "code": {"type": "str"}, "time": {"type": "int"}, "close": {"type": "float"},
        "freq": {"type": "str"}}}},
        "source_mappings": {"tushare": {"stock_minutes": {"identity": True}}}}
    raw = pd.DataFrame({"code": ["600000"], "time": [to_ms_timestamp("2026-01-01 09:35")],
                        "close": [10.0]})
    out, _ = FieldAligner(rules).align(raw, "stock_minutes", "tushare", freq="5min")
    assert out.loc[0, "freq"] == "5min"
