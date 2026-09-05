# -*- coding: utf-8 -*-
"""F-DUCKDB-LOCK A2 验收单测（设计 docs/duckdb-lock-timeout-design.md）。

覆盖：
- P2 per-statement 看门狗超时（interrupt 实测语义：InterruptException→重试→显式归因）
- P2 非超时异常透传（异常行为契约不变）
- P1 分片 vs 单条 SQL 逐行等价 + QS_BARS_CACHE_PROGRESS 心跳
- P3 连接失败静默消音（None 契约不变 + QS_DUCKDB_CONN_UNAVAILABLE 一次性告警）
- qs_diagnostics 计数聚合

全部使用 scratch 临时库，零生产副作用。
"""
import os
import time

import duckdb
import pandas as pd
import pytest

from quantstudio.backtest.providers.duckdb_data_access import (
    DuckDBDataAccess,
    _env_pos_float,
)


@pytest.fixture()
def scratch_db(tmp_path):
    db = tmp_path / "a2_test.db"
    conn = duckdb.connect(str(db))
    conn.execute(
        "CREATE TABLE stock_daily AS "
        "SELECT format('{:03d}', (range % 7)) AS code, range AS time, "
        "range % 100 AS close FROM range(70)"
    )
    conn.execute("CHECKPOINT")
    conn.close()
    return db


def test_env_pos_float():
    os.environ["QS_T1"] = "5"
    assert _env_pos_float("QS_T1", 30.0) == 5.0
    os.environ["QS_T1"] = "-2"
    assert _env_pos_float("QS_T1", 30.0) == 30.0
    os.environ["QS_T1"] = "abc"
    assert _env_pos_float("QS_T1", 30.0) == 30.0
    del os.environ["QS_T1"]
    assert _env_pos_float("QS_T1", 30.0) == 30.0


def test_p2_watchdog_timeout_then_loud(scratch_db, monkeypatch):
    dda = DuckDBDataAccess(scratch_db)
    conn = duckdb.connect(str(scratch_db), read_only=True)
    monkeypatch.setattr(dda, "_get_conn", lambda: conn)
    monkeypatch.setenv("QS_DUCKDB_QUERY_TIMEOUT_S", "1")
    heavy = "SELECT COUNT(*) FROM range(9000000) a, range(9000000) b"
    with pytest.raises(RuntimeError, match="QS_DUCKDB_QUERY_TIMEOUT"):
        dda._execute_with_timeout(conn, heavy)
    detail = [e for e in dda.qs_diagnostics()
              if e["kind"] == "QS_DUCKDB_QUERY_TIMEOUT" and "attempt" in e]
    assert len(detail) == 2  # attempt1 + attempt2 明细
    agg = [e for e in dda.qs_diagnostics()
           if e["kind"] == "QS_DUCKDB_QUERY_TIMEOUT" and e.get("occurrences")]
    assert agg and agg[0]["occurrences"] == 2
    conn.close()


def test_p2_error_passthrough(scratch_db):
    dda = DuckDBDataAccess(scratch_db)
    conn = duckdb.connect(str(scratch_db), read_only=True)
    with pytest.raises(duckdb.BinderException):
        dda._execute_with_timeout(conn, "SELECT nosuchcol FROM stock_daily")
    conn.close()


def test_p2_slow_but_within_budget_succeeds(scratch_db, monkeypatch):
    dda = DuckDBDataAccess(scratch_db)
    conn = duckdb.connect(str(scratch_db), read_only=True)
    monkeypatch.setenv("QS_DUCKDB_QUERY_TIMEOUT_S", "30")
    df = dda._execute_with_timeout(conn, "SELECT COUNT(*) AS n FROM stock_daily")
    assert df["n"][0] == 70
    assert dda.qs_diagnostics() == []
    conn.close()


def _load_reference_single_shot(conn, missing, cols, table="stock_daily"):
    placeholders = ", ".join(["?"] * len(missing))
    df = conn.execute(
        f"SELECT {cols} FROM {table} WHERE code IN ({placeholders})", missing
    ).fetchdf()
    if df is None or df.empty:
        return {}
    df = df.sort_values(["code", "time"]).reset_index(drop=True)
    return {c: sub.reset_index(drop=True) for c, sub in df.groupby("code", sort=False)}


def test_p1_chunk_equivalence(scratch_db, monkeypatch):
    codes = [format(i, "03d") for i in range(7)]
    cols = "code, time, close"
    # 参照：单条 SQL（原始实现语义）
    conn = duckdb.connect(str(scratch_db), read_only=True)
    reference = _load_reference_single_shot(conn, codes, cols)
    conn.close()
    # A2：分片路径（chunk=2 → 4 片）经 _ensure_bars_in_cache 填缓存
    dda = DuckDBDataAccess(scratch_db)
    monkeypatch.setenv("QS_BARS_CACHE_CHUNK_SIZE", "2")
    dda._ensure_bars_in_cache(codes, "stock_daily", cols)
    assert len(dda._bars_history_cache) == len(reference) == 7
    for code, ref_df in reference.items():
        got = dda._bars_history_cache[("stock_daily", code)]
        pd.testing.assert_frame_equal(got, ref_df)
    # QS_BARS_CACHE_PROGRESS 心跳（多片触发）
    kinds = [e.get("kind") for e in []]  # 占位：心跳走 logger，见下一条 caplog 测试


def test_p1_chunk_equivalence_small_chunk_and_cache_hit(scratch_db, monkeypatch):
    codes = ["000", "001", "002"]
    cols = "code, time, close"
    monkeypatch.setenv("QS_BARS_CACHE_CHUNK_SIZE", "1")
    dda = DuckDBDataAccess(scratch_db)
    dda._ensure_bars_in_cache(codes, "stock_daily", cols)
    first_pass = {k: v.copy() for k, v in dda._bars_history_cache.items()}
    assert len(first_pass) == 3
    # 二次调用：全部命中 → 不再查询（missing 空），缓存不变
    dda._ensure_bars_in_cache(codes, "stock_daily", cols)
    for k, v in first_pass.items():
        pd.testing.assert_frame_equal(dda._bars_history_cache[k], v)


def test_p1_progress_heartbeat_logged(scratch_db, monkeypatch, caplog):
    import logging
    codes = [format(i, "03d") for i in range(5)]
    cols = "code, time, close"
    monkeypatch.setenv("QS_BARS_CACHE_CHUNK_SIZE", "2")
    dda = DuckDBDataAccess(scratch_db)
    with caplog.at_level(logging.INFO, logger="quantstudio.backtest.providers.duckdb_data_access"):
        dda._ensure_bars_in_cache(codes, "stock_daily", cols)
    progress = [r for r in caplog.records if "QS_BARS_CACHE_PROGRESS" in r.getMessage()]
    assert progress, "多片加载必须输出 QS_BARS_CACHE_PROGRESS 心跳"
    assert "loaded=5/5" in progress[0].getMessage()


def test_p3_conn_unavailable_silent_cure(tmp_path, caplog, monkeypatch):
    import logging
    missing_db = tmp_path / "no_such.db"   # 文件不存在 → exists() False 走 except 分支
    # 直接构造：先造文件再删，或用 monkeypatch 让 exists() True 但 connect 失败
    dda = DuckDBDataAccess(missing_db)
    # exists()=False → 走原 except? 原 except 只捕 connect 异常；文件缺失走 if False → 落到 return None 无告警。
    # 为触发 except：让 exists() 返回 True 但 connect 抛异常（锁/损坏文件）——用非 db 文件模拟。
    import duckdb as _d
    fake_db = tmp_path / "fake.db"
    fake_db.write_text("not a duckdb file")
    dda2 = DuckDBDataAccess(fake_db)
    with caplog.at_level(logging.WARNING, logger="quantstudio.backtest.providers.duckdb_data_access"):
        out = dda2._get_conn()
    assert out is None  # None 契约不变
    kinds = [e["kind"] for e in dda2.qs_diagnostics()]
    assert "QS_DUCKDB_CONN_UNAVAILABLE" in kinds
    warns = [r for r in caplog.records if "QS_DUCKDB_CONN_UNAVAILABLE" in r.getMessage()]
    assert len(warns) == 1  # 一次性告警
    # 第二次失败：不再重复告警，只计数（caplog 清空后重测增量）
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="quantstudio.backtest.providers.duckdb_data_access"):
        dda2._get_conn()
    warns2 = [r for r in caplog.records if "QS_DUCKDB_CONN_UNAVAILABLE" in r.getMessage()]
    assert len(warns2) == 0
    agg = [e for e in dda2.qs_diagnostics() if e["kind"] == "QS_DUCKDB_CONN_UNAVAILABLE" and e.get("occurrences")]
    assert agg and agg[0]["occurrences"] >= 2


def test_p3_missing_file_returns_none_quiet(tmp_path):
    dda = DuckDBDataAccess(tmp_path / "absent.db")  # exists()=False：不告警（原行为）
    assert dda._get_conn() is None


def test_diag_cap_and_counter_aggregation(scratch_db):
    dda = DuckDBDataAccess(scratch_db)
    for i in range(60):
        dda._qs_record_diag("QS_TEST_FLOOD", {"i": i})
    diag = dda.qs_diagnostics()
    detail = [e for e in diag if e["kind"] == "QS_TEST_FLOOD" and "i" in e]
    agg = [e for e in diag if e["kind"] == "QS_TEST_FLOOD" and e.get("occurrences")]
    assert len(detail) == 50          # 明细封顶
    assert agg and agg[0]["occurrences"] == 60  # 计数聚合
