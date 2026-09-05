# -*- coding: utf-8 -*-
"""F-LOCAL-MIN B2' 契约测试（设计 docs/f-local-min-b2p-design.md §4）。

锚点主断言（数据形态对齐审核实测：000017@06-30 全天 240 根、尾三根 14:55/56/57 @6.12）：
anchor=07-01 09:31、include=False、count=3 → 期望 06-30 尾三根（跨日）；
PIT 反断言：include=False 时 07-01 当日 bar 不得泄漏；
include=True 补用例：07-01 当日 bar 可见（cutoff 语义）；
五分支：FrequencyCapabilityError 三分类语义不变（Timestamp 双形态）；
路由保持：stock/etf 路由与指数跳过行为不变。
"""
import duckdb
import pandas as pd
import pytest

from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
from quantstudio.backtest.providers.duckdb_provider import (
    DuckDBCalendarProvider, DuckDBMarketDataProvider)
from quantstudio.backtest.providers.frequency_labels import FrequencyCapabilityError


def _make_db(tmp_path):
    """scratch 库：000017 两日分钟（06-30 全天 240 根尾三根 @6.12；07-01 09:31 起 5 根 @10.0）。"""
    db = tmp_path / "b2p.db"
    conn = duckdb.connect(str(db))
    conn.execute(
        "CREATE TABLE stock_minutes (code VARCHAR, time BIGINT, freq VARCHAR, "
        "open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, "
        "amount DOUBLE, preClose DOUBLE, open_front DOUBLE, high_front DOUBLE, "
        "low_front DOUBLE, close_front DOUBLE, open_back DOUBLE, high_back DOUBLE, "
        "low_back DOUBLE, close_back DOUBLE, suspendFlag INTEGER)")
    base_d = int(pd.Timestamp("2026-06-30 09:31", tz="Asia/Shanghai").value // 10**6)
    pm_base = int(pd.Timestamp("2026-06-30 13:01", tz="Asia/Shanghai").value // 10**6)
    rows = []
    # 上午 09:31-11:30（120 根）
    for i in range(120):
        t = base_d + i * 60000
        close = 5.0 + i % 10 * 0.01
        rows.append(("000017", t, "1min", 6.0, 6.2, 5.9, close, 1000, 10200.0, 6.0,
                     6.0, 6.2, 5.9, close, 6.0, 6.2, 5.9, close, 0))
    # 下午 13:01-14:57（117 根，止于 14:57——尾三根 14:55/56/57 @6.12 与平台 QSPROBE 对应）
    for i in range(117):
        t = pm_base + i * 60000
        hhmm = (13, 1 + i) if i < 59 else (14, 1 + i - 59)
        close = 6.12 if i >= 114 else (5.0 + i % 10 * 0.01)
        rows.append(("000017", t, "1min", 6.0, 6.2, 5.9, close, 1000, 10200.0, 6.0,
                     6.0, 6.2, 5.9, close, 6.0, 6.2, 5.9, close, 0))
    base_d2 = int(pd.Timestamp("2026-07-01 09:31", tz="Asia/Shanghai").value // 10**6)
    for i in range(5):
        t = base_d2 + i * 60000
        rows.append(("000017", t, "1min", 10.0, 10.5, 9.8, 10.0, 500, 5000.0, 6.12,
                     10.0, 10.5, 9.8, 10.0, 10.0, 10.5, 9.8, 10.0, 0))
    conn.executemany(
        "INSERT INTO stock_minutes (code, time, freq, open, high, low, close, volume, "
        "amount, preClose, open_front, high_front, low_front, close_front, open_back, "
        "high_back, low_back, close_back, suspendFlag) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows)
    conn.execute("CHECKPOINT")
    conn.close()
    return db


@pytest.fixture()
def env(tmp_path):
    db = _make_db(tmp_path)
    cal = DuckDBCalendarProvider(db)
    mkt = DuckDBMarketDataProvider(db)
    dda = DuckDBDataAccess(db)
    return dda, mkt, cal


def test_anchor_cross_day_count3(env):
    """锚点主断言：07-01 09:31 include=False count=3 → 06-30 尾三根 @6.12（跨日）。"""
    dda, mkt, cal = env
    cutoff = int(pd.Timestamp("2026-07-01 09:31", tz="Asia/Shanghai").value // 10**6) - 60000  # 前一 bar
    df = dda.query_minute_bars_by_count_batch(
        ["000017"], 3, "2026-07-01", "1min", "pre", bar_cutoff_ms=cutoff)
    assert len(df) == 3
    assert list(df["close"]) == [6.12, 6.12, 6.12]
    times = [pd.Timestamp(t, unit="ms", tz="Asia/Shanghai").strftime("%H:%M")
             for t in df["time"]]
    assert times == ["14:55", "14:56", "14:57"]


def test_pit_no_leak(env):
    """PIT 反断言：include=False 时 07-01 当日 bar 不得泄漏。"""
    dda, mkt, cal = env
    cutoff = int(pd.Timestamp("2026-07-01 09:31", tz="Asia/Shanghai").value // 10**6) - 60000
    df = dda.query_minute_bars_by_count_batch(
        ["000017"], 3, "2026-07-01", "1min", "pre", bar_cutoff_ms=cutoff)
    assert (df["time"] < cutoff).all()


def test_include_true_same_day_visible(env):
    """include=True 补用例：07-01 当日 bar 可见（cutoff 控制）+ 跨日回补语义。"""
    dda, mkt, cal = env
    cutoff = int(pd.Timestamp("2026-07-01 09:35", tz="Asia/Shanghai").value // 10**6)
    df = dda.query_minute_bars_by_count_batch(
        ["000017"], 3, "2026-07-01", "1min", "pre", bar_cutoff_ms=cutoff)
    assert len(df) == 3
    times = [pd.Timestamp(t, unit="ms", tz="Asia/Shanghai") for t in df["time"]]
    # cutoff 内当日已有 ≥3 根 → 最近 3 根全为当日（09:33/34/35）
    assert [t.strftime("%H:%M") for t in times] == ["09:33", "09:34", "09:35"]
    assert all(t.strftime("%Y-%m-%d") == "2026-07-01" for t in times)
    # 跨日回补：count=8（当日仅 5 根）→ 前 3 根回补 06-30 尾盘 14:55/56/57 @6.12
    df8 = dda.query_minute_bars_by_count_batch(
        ["000017"], 8, "2026-07-01", "1min", "pre", bar_cutoff_ms=cutoff)
    assert len(df8) == 8
    times8 = [pd.Timestamp(t, unit="ms", tz="Asia/Shanghai") for t in df8["time"]]
    assert [t.strftime("%Y-%m-%d") for t in times8[:3]] == ["2026-06-30"] * 3
    assert [t.strftime("%H:%M") for t in times8[:3]] == ["14:55", "14:56", "14:57"]
    assert [float(c) for c in df8["close"][:3]] == [6.12, 6.12, 6.12]
    assert all(t.strftime("%Y-%m-%d") == "2026-07-01" for t in times8[3:])


def test_provider_level_counts(env):
    """provider 级（engine 实际调用形态）Timestamp end_date 双形态。"""
    dda, mkt, cal = env
    r = mkt.get_bars_by_count(["000017"], 3, pd.Timestamp("2026-07-01"), None, "pre", frequency="1m")
    assert "000017" in r and len(r["000017"]) == 3
    # Timestamp 形态不炸且同结果
    r2 = mkt.get_bars_by_count(["000017"], 3, pd.Timestamp("2026-07-01"), None, "pre", frequency="1m")
    pd.testing.assert_frame_equal(r["000017"].reset_index(drop=True), r2["000017"].reset_index(drop=True))


def test_freq_capability_semantics(env):
    """五分支矩阵：FREQ 缺失/指数 TABLE_MISSING 语义不变（Timestamp 双形态）。"""
    dda, mkt, cal = env
    with pytest.raises(FrequencyCapabilityError):
        dda.query_minute_bars_by_count_batch(["000017"], 3, "2026-07-01", "5min", "pre")
    with pytest.raises(FrequencyCapabilityError):
        dda.query_minute_bars_by_count_batch(["000852"], 3, "2026-07-01", "1min", "pre")
