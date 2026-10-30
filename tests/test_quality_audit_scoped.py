"""Phase 1（2026-10-31）质量审计范围治理与保护 —— 机检用例（A0/A1/A2/A3/A4/A6）。

方案：docs/quality-audit-oom-and-runall-design.md（v3 终审通过）
客户实证：60GB 库上 ``_audit_prices`` 在 12.4 GiB 处 OOM；73 个串行任务 = 73 次全库审计。

本文件只做 hermetic 机检（临时库 / monkeypatch），绝不触生产库。
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import duckdb
import pytest

from quantstudio.pipeline.quality_audit import DataQualityAuditor, QualityReport


def _schemas():
    return {"a": {"columns": {}}, "b": {"columns": {}}, "c": {"columns": {}}}


# --------------------------------------------------------------------------
# A4：tables 白名单
# --------------------------------------------------------------------------
def test_a4_whitelist_only_selected():
    aud = DataQualityAuditor("x.db", _schemas(), tables=["b"])
    assert [t for t, _ in aud._audit_table_items()] == ["b"]


def test_a4_none_means_all_tables():
    aud = DataQualityAuditor("x.db", _schemas())
    assert [t for t, _ in aud._audit_table_items()] == ["a", "b", "c"]


def test_a4_empty_whitelist_means_zero_tables():
    aud = DataQualityAuditor("x.db", _schemas(), tables=[])
    assert aud._audit_table_items() == []


def test_a4_whitelist_preserves_schema_order():
    aud = DataQualityAuditor("x.db", _schemas(), tables=["c", "a"])
    assert [t for t, _ in aud._audit_table_items()] == ["a", "c"]


def test_a4_unknown_table_name_is_ignored():
    aud = DataQualityAuditor("x.db", _schemas(), tables=["zzz"])
    assert aud._audit_table_items() == []


# --------------------------------------------------------------------------
# A3：重型检查异常隔离与超时留痕
# --------------------------------------------------------------------------
def test_a3_oom_is_downgraded_not_raised():
    aud = DataQualityAuditor("x.db", {})
    rep = QualityReport()

    def boom(*_a, **_k):
        raise duckdb.OutOfMemoryException("Out of Memory Error: 12.4 GiB/12.5 GiB used")

    aud._guarded_call(rep, "t", boom)
    checks = [i.check for i in rep.issues]
    assert "audit_skipped_oom" in checks


def test_a3_programming_error_still_propagates():
    """负向：非内存/查询类异常必须抛穿，不得被降级掩盖。"""
    aud = DataQualityAuditor("x.db", {})
    rep = QualityReport()

    def boom(*_a, **_k):
        raise ValueError("programming error must not be swallowed")

    with pytest.raises(ValueError):
        aud._guarded_call(rep, "t", boom)


def test_a3_normal_call_produces_no_noise():
    aud = DataQualityAuditor("x.db", {})
    rep = QualityReport()
    assert aud._guarded_call(rep, "t", lambda: 42) == 42
    assert not [i for i in rep.issues
                if i.check in ("audit_skipped_oom", "audit_query_error", "audit_timeout")]


def test_a3_timeout_is_recorded(monkeypatch):
    """全库域（tables=None）读 QS_AUDIT_FULL_TABLE_TIMEOUT_S（C2 分档）。"""
    monkeypatch.setenv("QS_AUDIT_FULL_TABLE_TIMEOUT_S", "0.01")
    aud = DataQualityAuditor("x.db", {})
    rep = QualityReport()
    aud._guarded_call(rep, "t", lambda: time.sleep(0.05))
    assert [i.check for i in rep.issues].count("audit_timeout") == 1


def test_a3_timeout_task_scope_uses_task_env(monkeypatch):
    """task 域（给定白名单）读 QS_AUDIT_TABLE_TIMEOUT_S（C2 分档）。"""
    monkeypatch.setenv("QS_AUDIT_TABLE_TIMEOUT_S", "0.01")
    monkeypatch.setenv("QS_AUDIT_FULL_TABLE_TIMEOUT_S", "999")
    aud = DataQualityAuditor("x.db", {}, tables=["t"])
    rep = QualityReport()
    aud._guarded_call(rep, "t", lambda: time.sleep(0.05))
    assert [i.check for i in rep.issues].count("audit_timeout") == 1


def test_a3_timeout_zero_disables_recording(monkeypatch):
    """负向：两域 TIMEOUT=0 时不得产生 timeout 条目（旧行为）。"""
    monkeypatch.setenv("QS_AUDIT_TABLE_TIMEOUT_S", "0")
    monkeypatch.setenv("QS_AUDIT_FULL_TABLE_TIMEOUT_S", "0")
    aud = DataQualityAuditor("x.db", {})
    rep = QualityReport()
    aud._guarded_call(rep, "t", lambda: time.sleep(0.02))
    aud_task = DataQualityAuditor("x.db", {}, tables=["t"])
    aud_task._guarded_call(rep, "t", lambda: time.sleep(0.02))
    assert not [i for i in rep.issues if i.check == "audit_timeout"]


# --------------------------------------------------------------------------
# A1：AdjustmentAnchor 新旧口径逐位对拍
# --------------------------------------------------------------------------
_ANCHOR_COLS = {"code", "time", "close", "close_front", "close_back"}


def _anchor_db(rows):
    con = duckdb.connect()
    con.execute("CREATE TABLE t (code VARCHAR, time BIGINT, close DOUBLE, "
                "close_front DOUBLE, close_back DOUBLE)")
    if rows:
        con.executemany("INSERT INTO t VALUES (?,?,?,?,?)", rows)
    return con


def _anchor_count(con, mode):
    os.environ["QS_AUDIT_ANCHOR_MODE"] = mode
    aud = DataQualityAuditor("x.db", {})
    rep = QualityReport()
    aud._audit_prices(con, rep, "t", _ANCHOR_COLS)
    return sum(i.count for i in rep.issues if i.check == "AdjustmentAnchor")


def _base(codes=2, n=5):
    out = []
    for c in [f"C{i}" for i in range(codes)]:
        for i in range(n):
            out.append((c, 1000 + i, 10.0, 10.0, 10.0))
    return out


@pytest.mark.parametrize("label,rows,expect", [
    ("基线无偏差", _base(), 0),
    ("首条 close_back 偏 3.1%", [("C0", 1000, 10.0, 10.0, 10.31)] + _base()[1:], 1),
    ("中间行偏差不计", _base()[:2] + [("C0", 1002, 10.0, 10.5, 10.5)] + _base()[3:], 0),
    ("末行 close_front 为 NULL", _base()[:4] + [("C0", 1004, 10.0, None, 10.0)], 0),
    ("首行 close_back 为 NULL", [("C0", 1000, 10.0, 10.0, None)] + _base()[1:], 0),
])
def test_a1_new_matches_legacy(label, rows, expect):
    """new（去 SELECT * + 单次排序）与 legacy 计数逐位相同，且期望值独立可证。"""
    con = _anchor_db(rows)
    try:
        legacy = _anchor_count(con, "legacy")
        new = _anchor_count(con, "new")
    finally:
        con.close()
    assert legacy == new == expect, f"{label}: legacy={legacy} new={new} expect={expect}"


def test_a1_new_detects_last_row_front_deviation():
    """正向：末条 close_front 偏 3.1% 必须被检出（证明 new 路径不是恒 0）。"""
    rows = [("C0", 1000 + i, 10.0, 10.0, 10.0) for i in range(4)]
    rows.append(("C0", 1004, 10.0, 10.31, 10.0))
    con = _anchor_db(rows)
    try:
        assert _anchor_count(con, "new") == 1
    finally:
        con.close()


# --------------------------------------------------------------------------
# A2：分钟锚点漂移 —— 候选上限截断可见 + legacy 无截断（负向）
# --------------------------------------------------------------------------
def _drift_env(tmp: Path, n_codes: int):
    """合成 stock_minutes + stock_dividend + aux(adj_factor) 因子库。"""
    aux = tmp / "qfq_aux.db"
    now = int(time.time() * 1000)
    con = sqlite3.connect(str(aux))
    con.execute("CREATE TABLE adj_factor (code TEXT, time INTEGER, adj_factor REAL)")
    con.executemany("INSERT INTO adj_factor VALUES (?,?,?)",
                    [(f"S{i:04d}", now - 60 * 86400000, 1.0) for i in range(n_codes)]
                    + [(f"S{i:04d}", now - 20 * 86400000, 1.1) for i in range(n_codes)])
    con.commit()
    con.close()

    db = duckdb.connect()
    db.execute("CREATE TABLE stock_minutes (code VARCHAR, time BIGINT, "
               "close DOUBLE, close_front DOUBLE)")
    db.execute("CREATE TABLE stock_dividend (code VARCHAR, ex_date BIGINT)")
    bars = [(f"S{i:04d}", now - d * 86400000, 10.0, 10.0)
            for i in range(n_codes) for d in range(25)]
    db.executemany("INSERT INTO stock_minutes VALUES (?,?,?,?)", bars)
    db.executemany("INSERT INTO stock_dividend VALUES (?,?)",
                   [(f"S{i:04d}", now - 10 * 86400000) for i in range(n_codes)])
    return db, aux


def _drift_issues(db, aux, mode, max_codes, monkeypatch):
    monkeypatch.setenv("QS_AUDIT_DRIFT_MODE", mode)
    if max_codes is None:
        monkeypatch.delenv("QS_AUDIT_DRIFT_MAX_CODES", raising=False)
    else:
        monkeypatch.setenv("QS_AUDIT_DRIFT_MAX_CODES", str(max_codes))
    aud = DataQualityAuditor("x.db", {}, qfq_aux_override=str(aux))
    rep = QualityReport()
    aud._audit_minute_anchor_drift(db, rep, {"stock_minutes", "stock_dividend"})
    return {i.check: i.count for i in rep.issues}


def test_a2_truncation_is_visible(tmp_path, monkeypatch):
    """候选 30 > 上限 20 ⇒ 必须留 AnchorDriftTruncated（截断不得静默）。"""
    db, aux = _drift_env(tmp_path, 30)
    try:
        issues = _drift_issues(db, aux, "bounded", 20, monkeypatch)
    finally:
        db.close()
    assert issues.get("AnchorDriftTruncated") == 30


def test_a2_legacy_mode_has_no_truncation(tmp_path, monkeypatch):
    """负向：legacy 模式忽略上限，不得产生截断条目。"""
    db, aux = _drift_env(tmp_path, 30)
    try:
        issues = _drift_issues(db, aux, "legacy", 20, monkeypatch)
    finally:
        db.close()
    assert "AnchorDriftTruncated" not in issues


# --------------------------------------------------------------------------
# A6：数据成败与审计完整性分离 —— 见 tests/test_pipeline_execution_policy.py
#     ::test_execute_task_with_quality_separates_data_from_audit（同一判据，
#     用该文件既有的 collector 工厂构造，避免在此重复脆弱的对象手工装配）。
#     本文件不重复覆盖，避免两处断言漂移。
# --------------------------------------------------------------------------

