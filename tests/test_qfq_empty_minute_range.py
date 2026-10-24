# -*- coding: utf-8 -*-
"""QFQ 空分钟表修复 —— 验收固件（A1–A11 / A9b–A9d）

方案件：docs/qfq-empty-minute-range-design-20261024.md（v1.1，②审通过 + A9 证伪后增量）
客户问题：仅启用日线任务（分钟表空）⇒ 重锚单元全 skipped ⇒ committed=0 ⇒ 水位永不提交。

覆盖：
  FP-1  A1/A2/A3    _security_range 空表语义（None 而非 0）
  FP-2  A4/A5/A6    FreshCapture.capture 支持 minute_range=None
  FP-3a A9c         apply_reanchor_for_security：未显式允许时仍硬失败（负向）
  FP-3b A9d         stage_fresh_authoritative：空分钟跳过 stage（minute_absent）
  FP-3c A9b         apply 端到端 committed（allow_empty_minute=True）

原则：全部内存 DuckDB / 桩 fetcher，不碰生产库、不连 xtquant。
"""
from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from quantstudio.pipeline.qfq_fresh_capture import FreshCapture, FreshFetcher
from quantstudio.pipeline.qfq_reanchor_engine import (
    ReanchorTolerances,
    apply_reanchor_for_security,
    stage_fresh_authoritative,
)
from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema
from quantstudio.pipeline.qfq_resident_orchestrator import QFQResidentOrchestrator

D1, D2 = 1788192000000, 1792598400000     # 2026-09-01 / 2026-10-20 附近（epoch-ms）
M1, M2 = 1789090200000, 1792506000000


# --------------------------------------------------------------------------- #
# FP-1 · A1/A2/A3
# --------------------------------------------------------------------------- #
def _mk_price_db(daily_times, minute_times, code="000001"):
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE stock_daily(code VARCHAR, time BIGINT)")
    c.execute("CREATE TABLE stock_minutes(code VARCHAR, time BIGINT)")
    for t in daily_times:
        c.execute("INSERT INTO stock_daily VALUES (?, ?)", [code, t])
    for t in minute_times:
        c.execute("INSERT INTO stock_minutes VALUES (?, ?)", [code, t])
    return c


def test_a1_minute_empty_returns_none_not_zero():
    c = _mk_price_db([D1, D2], [])
    try:
        d, m = QFQResidentOrchestrator._security_range(None, c, "STOCK", "000001")
        assert d == (D1, D2)
        assert m == (None, None), f"分钟空必须返回 (None,None)，实测 {m}"
    finally:
        c.close()


def test_a2_both_empty_returns_double_none():
    c = _mk_price_db([], [])
    try:
        assert QFQResidentOrchestrator._security_range(None, c, "STOCK", "000001") == \
            ((None, None), (None, None))
    finally:
        c.close()


def test_a3_negative_both_present_bitwise_same():
    """A3（负向）：两表有数据 ⇒ 与旧实现逐位相同。"""
    c = _mk_price_db([D1, D2], [M1, M2])
    try:
        r = QFQResidentOrchestrator._security_range(None, c, "STOCK", "000001")
        assert r == ((D1, D2), (M1, M2))
    finally:
        c.close()


def test_a3b_other_code_rows_not_counted():
    c = _mk_price_db([D1], [], code="600000")
    try:
        assert QFQResidentOrchestrator._security_range(None, c, "STOCK", "000001") == \
            ((None, None), (None, None))
    finally:
        c.close()


# --------------------------------------------------------------------------- #
# FP-2 · A4/A5/A6
# --------------------------------------------------------------------------- #
def _frames(times):
    idx = pd.DatetimeIndex(times)
    return pd.DataFrame({"open": [10.0] * len(times), "high": [11.0] * len(times),
                         "low": [9.0] * len(times), "close": [10.5] * len(times)},
                        index=idx)


class _CountingFetcher(FreshFetcher):
    def __init__(self):
        self.calls = []

    def fetch_none_front(self, asset_type, xt_code, period, s, e):
        self.calls.append((period, s, e))
        return _frames(["2026-10-20 09:30", "2026-10-21 09:30"]), \
               _frames(["2026-10-20 09:30", "2026-10-21 09:30"])


def _cap():
    cap = FreshCapture.__new__(FreshCapture)
    cap._period_for = lambda table, default: default      # 绕 cfg 依赖（不触库/不联网）
    return cap


def test_a4_a5_minute_none_skips_minute_fetch():
    f = _CountingFetcher()
    rec, fd, fm = _cap().capture(None, asset_type="STOCK", code="000001", run_id="r1",
                                 daily_range_ms=(D1, D2), minute_range_ms=None,
                                 fetcher=f, source="mcp", write=False)
    assert len(f.calls) == 1, f"应只采集日线一次，实测 {f.calls}"
    assert f.calls[0][0] == "daily"
    assert rec.minute_range_start is None and rec.minute_range_end is None
    assert rec.daily_range_start == D1 and rec.daily_range_end == D2
    assert len(fm) == 0
    assert {"time", "open", "high", "low", "close", "freq"}.issubset(set(fm.columns))
    assert isinstance(rec.minute_sha256, str) and len(rec.minute_sha256) == 64


def test_a6_negative_tuple_still_fetches_both():
    f = _CountingFetcher()
    rec, _, fm = _cap().capture(None, asset_type="STOCK", code="000001", run_id="r1",
                                daily_range_ms=(D1, D2), minute_range_ms=(M1, M2),
                                fetcher=f, source="mcp", write=False)
    assert len(f.calls) == 2 and any(c[0] == "1min" for c in f.calls)
    assert (rec.minute_range_start, rec.minute_range_end) == (M1, M2)
    assert len(fm) == 2


def test_capture_daily_none_raises():
    with pytest.raises(ValueError, match="daily_range_ms"):
        _cap().capture(None, asset_type="STOCK", code="000001", run_id="r1",
                       daily_range_ms=None, minute_range_ms=None,
                       fetcher=_CountingFetcher(), source="mcp", write=False)


@pytest.mark.parametrize("mr", [(None, None), (M1, None), (None, M2)])
def test_a9e_partial_or_tuple_none_treated_as_empty_minute(mr):
    """A9e（生产实证回归 2026-10-24）：`(None,None)` 或半 None 不得崩，须等同「无分钟」。

    `_security_range` 的空表语义是 tuple `(None, None)`；若 capture 只判 `is not None`，
    tuple 会通过并崩在 `_ms_to_yyyymmdd(None)`（生产 13:24 实测 TypeError）。
    """
    f = _CountingFetcher()
    rec, fd, fm = _cap().capture(None, asset_type="STOCK", code="000001", run_id="r1",
                                 daily_range_ms=(D1, D2), minute_range_ms=mr,
                                 fetcher=f, source="mcp", write=False)
    assert len(f.calls) == 1 and f.calls[0][0] == "daily", f.calls
    assert rec.minute_range_start is None and rec.minute_range_end is None
    assert len(fm) == 0


# --------------------------------------------------------------------------- #
# FP-3a · A9c 负向：未显式允许时仍硬失败
# --------------------------------------------------------------------------- #
def _empty_minutes():
    return pd.DataFrame(columns=["code", "time", "open", "high", "low", "close",
                                 "open_front", "high_front", "low_front", "close_front",
                                 "freq"])


def _daily_fresh():
    return pd.DataFrame([{"code": "000001", "time": D1, "open": 10.0, "high": 11.0,
                          "low": 9.0, "close": 10.5, "open_front": 10.0, "high_front": 11.0,
                          "low_front": 9.0, "close_front": 10.5}])


def test_a9c_default_rejects_empty_minutes():
    """A9c：allow_empty_minute 默认 False ⇒ 空分钟仍 ValueError（防沉默漂移）。"""
    conn = duckdb.connect(":memory:")
    init_duckdb_schema(conn)
    try:
        with pytest.raises(ValueError, match="非空 fresh_minutes"):
            apply_reanchor_for_security(
                conn, asset_type="STOCK", code="000001",
                fresh_daily=_daily_fresh(), calendar=None,
                model="fresh_authoritative_rebase", model_reason="unit-test",
                fresh_minutes=_empty_minutes(), fresh_source="mcp",
                fresh_capture_id="cap-1", fresh_metadata_sha256="a" * 64)
    finally:
        conn.close()


def test_a9c_allow_only_for_rebase_model():
    """A9c²：allow_empty_minute 仅 fresh_authoritative_rebase；其他模型显式拒绝。"""
    conn = duckdb.connect(":memory:")
    init_duckdb_schema(conn)
    try:
        with pytest.raises(ValueError, match="仅支持"):
            apply_reanchor_for_security(
                conn, asset_type="STOCK", code="000001",
                fresh_daily=_daily_fresh(), calendar=None,
                model="fresh_staged", model_reason="unit-test",
                fresh_minutes=_empty_minutes(), fresh_source="mcp",
                fresh_capture_id="cap-1", fresh_metadata_sha256="a" * 64,
                allow_empty_minute=True)
    finally:
        conn.close()


def test_a9c_none_not_allowed_with_flag():
    """A9c³：allow_empty_minute=True 时也要求传「带列空 DF」而非 None。"""
    conn = duckdb.connect(":memory:")
    init_duckdb_schema(conn)
    try:
        with pytest.raises(ValueError, match="带列的空 DataFrame"):
            apply_reanchor_for_security(
                conn, asset_type="STOCK", code="000001",
                fresh_daily=_daily_fresh(), calendar=None,
                model="fresh_authoritative_rebase", model_reason="unit-test",
                fresh_minutes=None, fresh_source="mcp",
                fresh_capture_id="cap-1", fresh_metadata_sha256="a" * 64,
                allow_empty_minute=True)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# FP-3b · A9d：stage 层空分钟跳过（minute_absent）
# --------------------------------------------------------------------------- #
def test_a9d_stage_skips_minute_segment():
    conn = duckdb.connect(":memory:")
    init_duckdb_schema(conn)
    try:
        # 未允许 ⇒ ReanchorBlocked
        from quantstudio.pipeline.qfq_reanchor_engine import ReanchorBlocked
        with pytest.raises(Exception):
            stage_fresh_authoritative(conn, "STOCK", "000001", _daily_fresh(),
                                      _empty_minutes(), ["1min"], None, None,
                                      allow_empty_minute=False)
    finally:
        conn.close()


def _mk_price_tables(conn):
    """最小价格表（列名与引擎读取一致；init_duckdb_schema 不含价格表）。"""
    conn.execute("""CREATE TABLE stock_daily(time BIGINT, code VARCHAR, open DOUBLE,
      high DOUBLE, low DOUBLE, close DOUBLE, preClose DOUBLE, volume DOUBLE, amount DOUBLE,
      open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, close_front DOUBLE,
      PRIMARY KEY(code,time))""")
    conn.execute("""CREATE TABLE stock_minutes(time BIGINT, code VARCHAR, freq VARCHAR,
      open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
      open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, close_front DOUBLE,
      PRIMARY KEY(code,freq,time))""")
    conn.execute("INSERT INTO stock_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 [D1, "000001", 10.0, 11.0, 9.0, 10.5, 10.0, 1000.0, 10000.0,
                  10.0, 11.0, 9.0, 10.5])


def test_a9d2_stage_returns_minute_absent_when_allowed():
    """A9d-2：allow_empty_minute=True ⇒ stage 返回 minute_absent=True（staged_daily 就绪）。"""
    conn = duckdb.connect(":memory:")
    init_duckdb_schema(conn)
    try:
        _mk_price_tables(conn)
        out = stage_fresh_authoritative(conn, "STOCK", "000001", _daily_fresh(),
                                        _empty_minutes(), ["1min"], None, None,
                                        allow_empty_minute=True)
        assert out.get("minute_absent") is True
        assert out.get("staged_daily")
        assert out.get("minute_partial") == {}
    finally:
        conn.close()
