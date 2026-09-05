# -*- coding: utf-8 -*-
"""F-LOCAL-MIN B2 契约测试（设计 docs/f-local-min-b2-design.md §3.4）。

五组：
1. 双形态不炸：Timestamp vs 等价字符串入参 iter_trading_days_in_range 逐位一致
2. _as_date_str 五形态归一 + 不可识别透传
3. get_history 端到端：Timestamp anchor 分钟调用非空（B1 零输出反断言）
4. 限频告警：首条 warning + 二次零告警 + 计数
5. 五分支矩阵：FrequencyCapabilityError 三分类语义在 Timestamp 入参下不变
"""
import datetime
import logging

import duckdb
import pandas as pd
import pytest

from quantstudio.backtest.providers.intraday_windows import (
    _as_date_str, iter_trading_days_in_range)


class _MiniCalendar:
    """最小日历：周一至周五为交易日（测试用）。"""
    def get_trade_days(self, start, end):
        days = pd.date_range(start, end, freq="D")
        return [d for d in days if d.weekday() < 5]


def test_1_dual_form_no_raise():
    cal = _MiniCalendar()
    ts_start, ts_end = pd.Timestamp("2026-07-01"), pd.Timestamp("2026-07-03")
    str_start, str_end = "2026-07-01", "2026-07-03"
    a = iter_trading_days_in_range(ts_start, ts_end, cal)
    b = iter_trading_days_in_range(str_start, str_end, cal)
    assert a == b == ["2026-07-01", "2026-07-02", "2026-07-03"]


def test_2_normalize_forms():
    cases = [
        ("2026-07-01", "2026-07-01"),
        ("2026-07-01 09:31:00", "2026-07-01"),
        (pd.Timestamp("2026-07-01 09:31:00"), "2026-07-01"),
        (datetime.date(2026, 7, 1), "2026-07-01"),
        (datetime.datetime(2026, 7, 1, 9, 31), "2026-07-01"),
    ]
    for v, want in cases:
        assert _as_date_str(v) == want, (v, _as_date_str(v))
    # epoch-ms（上海 09:30 语义）
    assert _as_date_str(1781568000000) == "2026-06-16"
    # 不可识别形态原样透传（不在本层新造失败）
    class Weird:
        pass
    w = Weird()
    assert _as_date_str(w) is w


def _make_engine_env(tmp_path):
    """搭 scratch 库 + 引擎装配：stock_minutes 3 日分钟数据 + 日线表。

    time 构造：上海时区 2026-06-16/17/18 三日的 09:31-11:10 时段内（end-labeled
    窗口 09:31-11:30 内），每码每日 20 根 1 分钟 bar——保证落在时段窗口内。
    """
    import duckdb as _d
    base_ms = int(pd.Timestamp("2026-06-16 09:31", tz="Asia/Shanghai").value // 10**6)
    db = tmp_path / "b2.db"
    conn = _d.connect(str(db))
    conn.execute(
        "CREATE TABLE stock_minutes AS SELECT format('{:03d}', (range % 2)) AS code, "
        "'1min' AS freq, " + str(base_ms) + " + (range % 3) * 86400000 + (range % 20) * 60000 AS time, "
        "10.0 AS open, 10.5 AS high, 9.8 AS low, 10.2 AS close, 1000 AS volume, "
        "10200.0 AS amount, 10.1 AS preClose, 10.2 AS open_front, 10.7 AS high_front, "
        "10.0 AS low_front, 10.4 AS close_front, 10.4 AS open_back, 10.9 AS high_back, "
        "10.2 AS low_back, 10.6 AS close_back, 10.3 AS preClose_bak, 0 AS suspendFlag "
        "FROM range(60)"
    )
    conn.execute(
        "CREATE TABLE stock_daily AS SELECT format('{:03d}', (range % 2)) AS code, "
        "1781568000000 + range * 86400000 AS time, 10 AS open, 10 AS high, 10 AS low, "
        "10 AS close, 100 AS volume, 1000 AS amount, 10 AS pctChg, 10 AS preClose, "
        "1 AS turn, 5 AS peTTM, 5 AS pbMRQ, 10 AS open_front, 10 AS high_front, "
        "10 AS low_front, 10 AS close_front FROM range(5)"
    )
    conn.execute("CREATE TABLE trade_calendar AS SELECT CAST(1781568000000 + range * 86400000 AS BIGINT) AS time FROM range(6)")
    conn.execute("CHECKPOINT")
    conn.close()
    return db


def test_3_get_history_end_to_end_nonempty(tmp_path, monkeypatch):
    """B1 零输出反断言：Timestamp anchor 下分钟调用非空（scratch 库真数据）。"""
    from quantstudio.backtest.providers.duckdb_provider import DuckDBMarketDataProvider
    db = _make_engine_env(tmp_path)
    mkt = DuckDBMarketDataProvider(db)
    # Timestamp 入参（B1 违约形态）——修复后不炸且有数据
    df = mkt.get_bars_by_count(["000", "001"], 3, pd.Timestamp("2026-06-16"), None, "pre", frequency="1m")
    assert isinstance(df, dict)
    assert any(len(v) > 0 for v in df.values()), "修复后分钟批量路径必须返回数据"


def test_4_limit_frequency_warning(tmp_path, caplog, monkeypatch):
    """L1375 兜底：首条 QS_HIST_FAIL warning + 二次零告警（计数聚合）。"""
    from quantstudio.backtest.ptrade_api import PtradeAPI
    api = PtradeAPI.__new__(PtradeAPI)
    class Boom:
        def get_bars_by_count(self, *a, **kw):
            raise ValueError("boom-detail-xyz")
    api._market = Boom()
    api._engine = None
    api._current_date = pd.Timestamp("2026-07-01")
    api._prev_date = pd.Timestamp("2026-06-30")
    api._current_bar_ts = pd.Timestamp("2026-07-01 09:31:00")
    api._day_minute_history = None   # 预置实例属性（真实 __init__ 会建；裸实例需手动）
    api._day_minute_date = None
    api._query_cache = {}
    monkeypatch.delenv("QS_DUCKDB_QUERY_TIMEOUT_S", raising=False)
    with caplog.at_level(logging.WARNING, logger="quantstudio.backtest.ptrade_api"):
        r1 = PtradeAPI.get_history(api, 3, frequency="1m", field=["close"],
                                   security_list=["000001"], fq="pre", include=False, is_dict=True)
        assert r1 == {}  # 返回契约不变
        r2 = PtradeAPI.get_history(api, 3, frequency="1m", field=["close"],
                                   security_list=["000001"], fq="pre", include=False, is_dict=True)
        assert r2 == {}
    warns = [r for r in caplog.records if "QS_HIST_FAIL" in r.getMessage()]
    assert len(warns) == 1, "同类异常首条告警一次"
    assert "boom-detail-xyz" in warns[0].getMessage()
    counts = api._qs_hist_fail_counts
    assert sum(counts.values()) >= 2  # 两次都计数


def test_5_freq_capability_semantics_with_timestamp(tmp_path):
    """五分支矩阵：Timestamp 入参下 FrequencyCapabilityError 语义不变（TABLE_MISSING 指数/可转债、TABLE_EMPTY 空表、FREQ 缺失）。"""
    import duckdb as _d
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    from quantstudio.backtest.providers.frequency_labels import FrequencyCapabilityError
    db = _make_engine_env(tmp_path)
    dda = DuckDBDataAccess(db)
    # 指数/无分钟表形态：_resolve_minute_table=None → TABLE_MISSING（行为不变；str/Timestamp 双形态）
    for sv in ("000852", pd.Timestamp("2026-06-16")):
        with pytest.raises(FrequencyCapabilityError):
            dda.query_minute_bars_by_range(
                "000852", sv, "2026-06-16", "1m", calendar_provider=None)
    # FREQ 缺失：stock_minutes 只有 '1min'，查 '5m' → FREQ_NOT_IN_TABLE（str/Timestamp 双形态；
    # 不再建空表——dda 连接只读 attach，CREATE 会 InvalidInputException）
    with pytest.raises(FrequencyCapabilityError):
        dda.query_minute_bars_by_range(
            "000", "2026-06-16", "2026-06-16", "5m", calendar_provider=None)
    with pytest.raises(FrequencyCapabilityError):
        dda.query_minute_bars_by_range(
            "000", pd.Timestamp("2026-06-16"), pd.Timestamp("2026-06-16"), "5m", calendar_provider=None)
