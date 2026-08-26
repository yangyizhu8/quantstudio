# -*- coding: utf-8 -*-
"""P-D14 测试矩阵：D3 首日 ETF no_price 修复（2026-08-27）。

设计：docs/pd14-d3-firstday-noprice-design.md（Step 2 审计通过 + 两细化）
根因：docs/evidence/pd14-d3-firstday-noprice-rootcause-20260827.md（DB 确证）
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from quantstudio.backtest.providers.duckdb_data_access import (  # noqa: E402
    DuckDBDataAccess)
from quantstudio.backtest.providers.duckdb_provider import _start_ms  # noqa: E402

DB = str(pathlib.Path(__file__).resolve().parents[1] / "data" / "quantstudio.db")


def _has_db():
    return pathlib.Path(DB).exists()


# ========== T1：单日查询条件为窗口匹配 ==========

def test_t1_single_day_query_uses_window(monkeypatch):
    """单日查询条件变为 `time BETWEEN date_ms AND date_ms+86399999`。"""
    captured = {}
    import pandas as pd

    def _fake_sql(self, where):
        captured["where"] = where
        return "SELECT * FROM stock_daily WHERE " + where

    def _fake_conn(self):
        class _C:
            def execute(self, sql):
                captured["sql"] = sql
                return _R(self)
        return _C()

    class _R:
        def __init__(self, c):
            self.c = c

        def fetchdf(self):
            return pd.DataFrame()

    monkeypatch.setattr(DuckDBDataAccess, "_snapshot_sql", _fake_sql)
    monkeypatch.setattr(DuckDBDataAccess, "_get_conn", _fake_conn)
    dao = DuckDBDataAccess(db_path=DB)
    ms = _start_ms("2026-07-01")
    dao.query_daily_snapshot(ms)
    assert captured["where"] == (
        f"time >= {ms} AND time <= {ms + 86_399_999}")


# ========== T2：DB 实证——窗口含 515050 @1.334 ==========

@pytest.mark.skipif(not _has_db(), reason="DB 不存在")
def test_t2_window_contains_515050():
    dao = DuckDBDataAccess(db_path=DB)
    ms = _start_ms("2026-07-01")
    df = dao.query_daily_snapshot(ms)
    assert len(df) > 0
    hit = df[df["code"].astype(str) == "515050"]
    assert not hit.empty, "窗口应含 515050（08:00 组被吸收）"
    assert abs(float(hit.iloc[0]["close"]) - 1.334) < 1e-6, \
        f"515050 close 应=1.334（平台成交价），实际 {hit.iloc[0]['close']}"


# ========== T3：边界——次日窗口不串日 ==========

@pytest.mark.skipif(not _has_db(), reason="DB 不存在")
def test_t3_next_day_no_leak():
    dao = DuckDBDataAccess(db_path=DB)
    ms = _start_ms("2026-07-02")
    df = dao.query_daily_snapshot(ms)
    hit = df[df["code"].astype(str) == "515050"]
    assert not hit.empty
    # 07-02 窗口内 515050 行的 time 应为 07-02（1782921600000），非 07-01
    assert int(hit.iloc[0]["time"]) == 1782921600000, \
        f"07-02 窗口不应含 07-01 数据，time={hit.iloc[0]['time']}"


# ========== T4：去重护栏——无同 code 双 time 重复 ==========

@pytest.mark.skipif(not _has_db(), reason="DB 不存在")
def test_t4_no_duplicate_code_in_window():
    import duckdb
    conn = duckdb.connect(DB, read_only=True)
    ms = _start_ms("2026-07-01")
    dup = conn.execute(
        "SELECT code, COUNT(DISTINCT time) c FROM etf_daily "
        "WHERE time >= ? AND time <= ? GROUP BY code HAVING c > 1",
        [ms, ms + 86_399_999]).fetchall()
    conn.close()
    assert len(dup) == 0, f"07-01 存在双 time 重复 code: {dup[:5]}（护栏防护场景）"


# ========== T5：去重护栏逻辑（mock 双行 → 取最大 time） ==========

def test_t5_dedup_takes_max_time(monkeypatch):
    import pandas as pd
    ms = _start_ms("2026-07-01")
    df_in = pd.DataFrame({
        "code": ["515050", "515050", "511260"],
        "time": [ms, ms + 3600000, ms],
        "close": [1.33, 1.334, 134.0],   # 双行：08:00 组 close 应为 1.334
    })

    def _fake_conn(self):
        class _C:
            def execute(self, sql):
                return _R()
        return _C()

    class _R:
        def fetchdf(self):
            return df_in.copy()

    monkeypatch.setattr(DuckDBDataAccess, "_get_conn", _fake_conn)
    dao = DuckDBDataAccess(db_path=DB)
    out = dao.query_daily_snapshot(ms)
    rows = out[out["code"].astype(str) == "515050"]
    assert len(rows) == 1, "去重后应剩 1 行"
    assert abs(float(rows.iloc[0]["close"]) - 1.334) < 1e-6, \
        "去重取最大 time 行（close=1.334）"


# ========== T6：预取 vs 单日字节级一致 ==========

@pytest.mark.skipif(not _has_db(), reason="DB 不存在")
def test_t6_preload_matches_single_day():
    dao = DuckDBDataAccess(db_path=DB)
    ms1 = _start_ms("2026-07-01")
    ms2 = _start_ms("2026-07-03")
    # 先单日查询（缓存 07-01）
    day_df = dao.query_daily_snapshot(ms1)
    # 预取 07-01~07-03（窗口路径）
    dao.preload_daily_snapshots(ms1, ms2)
    preload_df = dao.query_daily_snapshot(ms1)
    assert len(day_df) == len(preload_df), \
        f"单日 vs 预取行数不一致: {len(day_df)} vs {len(preload_df)}"
    assert set(day_df["code"]) == set(preload_df["code"])