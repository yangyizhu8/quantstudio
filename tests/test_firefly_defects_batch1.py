# -*- coding: utf-8 -*-
"""firefly 缺陷清单第一批（#1/#2/#3/#4/#6）修复回归测试（2026-10-25）。

对应 .specify/bugs/firefly-engine-defects-20261025/assessment.md 与 fix.md：
- #2 bootstrap 播种后同轮 fail-closed（控制流缺陷）：
    正向：空库首跑播种成功 → 本轮通过门槛（bootstrap_required=False）；
    负向对照硬门：真失败路径（非全新源/播种不生效）仍 fail-closed held。
- #3 supersede_stale_intents 作废可恢复 intent：
    正向：finalized_held 周期的 pending intent 过继给新周期（candidate 保留）；
    负向：failed 周期 / 无周期行 intent 照旧 superseded；同键多候选只过继最大一条。
- #4 _skip_security 留 in_progress 毒化 gate：
    正向：skip 显式回退 pending、清 claimed_by/at；
    负向：非 in_progress 态不覆盖（CAS 守卫）；跨世代行不动。
- #6 qfq 周期跨资产污染：
    正向：stock_daily 结构性空 → STOCK 单元不 claim（保持 pending）；
    负向：stock_daily 有数据（哪怕 1 行）→ STOCK 单元正常 claim。
- #1 writer UPSERT 去 RETURNING + spill 兜底：
    SQL 形态（T2 开启无 RETURNING）、temp_directory/QS_DUCKDB_MEMORY_LIMIT、
    有界合成冒烟（重叠批写入有界时间内完成——非 447 万行挂起复刻，只作回归护栏）。

审计口径变化联动：tests/test_writer_upsert_skip_identical.py 的 changed 断言
已随 #1 修复同步更新（changed 恒 0，无下游消费）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import duckdb
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline.qfq_reanchor_schema import (  # noqa: E402
    init_duckdb_schema, init_sqlite_schema)
from quantstudio.pipeline.qfq_orchestrator_types import QFQOrchestratorConfig  # noqa: E402
from quantstudio.pipeline.qfq_resident_orchestrator import (  # noqa: E402
    QFQResidentOrchestrator)
from quantstudio.pipeline.writers import DuckDBWriter  # noqa: E402

NOW_MS = 1700000000000
GEN = ("xtquant", "xtquant-legacy", "legacy-xtquant-pre-cutover")


@pytest.fixture
def orch_env(tmp_path):
    duck = tmp_path / "qfq_main.db"
    aux = tmp_path / "qfq_aux.db"
    dconn = duckdb.connect(str(duck))
    init_duckdb_schema(dconn)
    dconn.execute(
        "CREATE TABLE stock_daily (code VARCHAR, time BIGINT, open DOUBLE, "
        "high DOUBLE, low DOUBLE, close DOUBLE)")
    dconn.execute(
        "CREATE TABLE etf_daily (code VARCHAR, time BIGINT, open DOUBLE, "
        "high DOUBLE, low DOUBLE, close DOUBLE)")
    dconn.execute(
        "CREATE TABLE stock_dividend (code VARCHAR, ex_date VARCHAR, "
        "record_date VARCHAR, div_proc VARCHAR)")
    dconn.execute(
        "CREATE TABLE stock_minutes (code VARCHAR, time BIGINT, freq VARCHAR, "
        "close DOUBLE)")
    dconn.execute(
        "CREATE TABLE etf_minutes (code VARCHAR, time BIGINT, freq VARCHAR, "
        "close DOUBLE)")
    auxc = __import__("sqlite3").connect(str(aux), timeout=30)
    init_sqlite_schema(auxc)
    auxc.close()
    cfg = QFQOrchestratorConfig(enabled=True)
    orch = QFQResidentOrchestrator(cfg, main_db=str(duck), aux_db=str(aux))
    yield dconn, orch
    dconn.close()


def _ins_trigger(conn, tid, asset_type, code, eff=NOW_MS, status="pending",
                 gen=GEN, extra_sql=""):
    conn.execute(
        "INSERT INTO qfq_trigger_queue (trigger_id, asset_type, code, trigger_type, "
        "detection_source, status, effective_date, attempt_count, trigger_id_version, "
        "price_source, source_generation, cutover_id, created_at, updated_at"
        + (", claimed_by, claimed_at" if extra_sql else "") + ") VALUES "
        f"('{tid}','{asset_type}','{code}','stock_dividend','stock_dividend',"
        f"'{status}',{eff},0,1,'{gen[0]}','{gen[1]}','{gen[2]}',"
        "'2026-01-08 00:00:00','2026-01-08 00:00:00'" + extra_sql + ")")


# ══════════════════════════════════════════════════════════════════════
# #2 bootstrap 播种后同轮 fail-closed（控制流）
# ══════════════════════════════════════════════════════════════════════
def _run_post_ingest(orch, conn):
    cid = orch.begin_cycle(conn)
    orch.calendar = True          # 非 None：通过 calendar 前置检查
    summary = orch.run_post_ingest(
        conn, cycle_id=cid, run_id="run_test", as_of_ms=NOW_MS,
        fetcher=object())
    return cid, summary


def test_bug2_seed_success_continues_cycle(orch_env):
    """#2 正向：空库首跑播种成功 → 本轮通过门槛（不 fail-closed、不留 bootstrap 债）。"""
    conn, orch = orch_env
    assert orch.bootstrap_completed(conn) is False      # 前置：确无 bootstrap
    _cid, summary = _run_post_ingest(orch, conn)
    # 修复前：播种成功也 finalized_held + bootstrap_required（控制流缺陷）
    assert summary.bootstrap_required is False, "播种成功后不得再标记 bootstrap_required"
    assert "require_bootstrap" not in (summary.error or "")
    assert orch.bootstrap_completed(conn) is True       # 播种落库且判定完成
    row = conn.execute(
        "SELECT params FROM qfq_bootstrap_run WHERE status='completed'").fetchone()
    assert row is not None and "fresh-deploy-auto" in (row[0] or "")


def test_bug2_negative_true_failure_still_fail_closed(orch_env, monkeypatch):
    """#2 负向对照硬门：非全新源（is_fresh_source=False）→ 播种不触发、仍 fail-closed held。"""
    import quantstudio.pipeline.qfq_cutover as qfq_cutover
    conn, orch = orch_env
    monkeypatch.setattr(qfq_cutover, "is_fresh_source_for_qfq",
                        lambda *a, **k: False, raising=True)
    _cid, summary = _run_post_ingest(orch, conn)
    assert summary.status == "finalized_held", "真失败路径必须仍 fail-closed held"
    assert summary.bootstrap_required is True
    assert "require_bootstrap" in (summary.error or "")
    n = conn.execute("SELECT COUNT(*) FROM qfq_bootstrap_run").fetchone()[0]
    assert n == 0, "非全新源不得播种"


def test_bug2_negative_seed_not_effective_still_fail_closed(orch_env, monkeypatch):
    """#2 负向对照（变体）：播种被调用但复检仍不通过（如版本门拒绝）→ 仍 fail-closed。"""
    conn, orch = orch_env
    monkeypatch.setattr(
        orch, "_seed_fresh_deploy_bootstrap", lambda conn: False)
    _cid, summary = _run_post_ingest(orch, conn)
    assert summary.status == "finalized_held"
    assert summary.bootstrap_required is True


# ══════════════════════════════════════════════════════════════════════
# #4 _skip_security 回退 trigger → pending
# ══════════════════════════════════════════════════════════════════════
def test_bug4_skip_resets_in_progress_to_pending(orch_env):
    """#4 正向：skip 把本单元 in_progress trigger 回退 pending、清 claimed_by/at。"""
    conn, orch = orch_env
    _ins_trigger(conn, "t1", "STOCK", "600000", status="in_progress",
                 extra_sql=",'run_x','2026-01-08 00:00:01'")
    _ins_trigger(conn, "t2", "STOCK", "600000", status="in_progress",
                 extra_sql=",'run_x','2026-01-08 00:00:01'")
    outcome = orch._skip_security(
        conn, ["t1", "t2"], "STOCK", "600000",
        "empty_daily_range", "日线结构性空")
    assert outcome.status == "skipped"
    rows = conn.execute(
        "SELECT status, claimed_by, claimed_at FROM qfq_trigger_queue "
        "WHERE trigger_id IN ('t1','t2') ORDER BY trigger_id").fetchall()
    assert rows == [("pending", None, None), ("pending", None, None)], (
        "skip 后 trigger 必须回到 pending（兑现 docstring 契约，否则毒化 gate orphan）")


def test_bug4_negative_cas_guard_non_in_progress(orch_env):
    """#4 负向：非 in_progress 态（retryable_failed）不被覆盖；跨世代行不动。"""
    conn, orch = orch_env
    _ins_trigger(conn, "t3", "STOCK", "600001", status="retryable_failed",
                 extra_sql=",'run_x','2026-01-08 00:00:01'")
    other_gen = ("xtquant", "some-generation", "some-cutover")
    _ins_trigger(conn, "t4", "STOCK", "600002", status="in_progress",
                 gen=other_gen, extra_sql=",'run_x','2026-01-08 00:00:01'")
    outcome = orch._skip_security(
        conn, ["t3", "t4"], "STOCK", "600001", "empty_fresh_capture", "x")
    assert outcome.status == "skipped"                     # skip 语义不被回退失败阻断
    s3 = conn.execute("SELECT status FROM qfq_trigger_queue WHERE trigger_id='t3'").fetchone()
    s4 = conn.execute(
        "SELECT status, claimed_by FROM qfq_trigger_queue WHERE trigger_id='t4'").fetchone()
    assert s3[0] == "retryable_failed", "CAS 守卫：非 in_progress 不得覆盖"
    assert s4 == ("in_progress", "run_x"), "跨世代行不动"


# ══════════════════════════════════════════════════════════════════════
# #6 资产域守卫：结构性空域不 claim
# ══════════════════════════════════════════════════════════════════════
def test_bug6_empty_stock_domain_not_claimed(orch_env):
    """#6 正向：stock_daily 结构性空 → STOCK 单元不 claim（保持 pending），ETF 正常。"""
    conn, orch = orch_env
    conn.execute("INSERT INTO etf_daily (code, time) VALUES ('159215', ?)", [NOW_MS])
    _ins_trigger(conn, "ts1", "STOCK", "600000")
    _ins_trigger(conn, "ts2", "STOCK", "600001")
    _ins_trigger(conn, "te1", "ETF", "159215")
    units = orch._claim_and_merge(
        conn, cycle_id="c_test", run_id="run_x", as_of_ms=NOW_MS + 1)
    assert all(u["asset_type"] == "ETF" for u in units), (
        "stock_daily 结构性空时不得 claim STOCK 单元（跨资产污染，毒化 gate）")
    statuses = dict(conn.execute(
        "SELECT trigger_id, status FROM qfq_trigger_queue").fetchall())
    assert statuses["ts1"] == "pending" and statuses["ts2"] == "pending"
    assert statuses["te1"] == "in_progress"


def test_bug6_negative_stock_with_data_claimed(orch_env):
    """#6 负向：stock_daily 有数据（哪怕 1 行）→ STOCK 单元正常 claim（防过度限定）。"""
    conn, orch = orch_env
    conn.execute("INSERT INTO stock_daily (code, time) VALUES ('600000', ?)", [NOW_MS])
    _ins_trigger(conn, "ts1", "STOCK", "600000")
    units = orch._claim_and_merge(
        conn, cycle_id="c_test", run_id="run_x", as_of_ms=NOW_MS + 1)
    assert any(u["asset_type"] == "STOCK" for u in units), (
        "域非空时 STOCK 单元必须正常 claim（保守口径：只滤结构性空）")


# ══════════════════════════════════════════════════════════════════════
# #3 可恢复 held intent 过继；死周期照旧清障
# ══════════════════════════════════════════════════════════════════════
def _mk_held_intent(orch, conn, table, candidate, cycle_status):
    cid = orch.begin_cycle(conn)
    orch.defer_watermark(
        conn, cycle_id=cid, source="xtquant", table=table, freq="daily",
        candidate_watermark=candidate)
    conn.execute(
        "UPDATE qfq_cycle_run SET status=?, phase='finalized' WHERE cycle_id=?",
        [cycle_status, cid])
    return cid


def test_bug3_held_intent_adopted_by_new_cycle(orch_env):
    """#3 正向：finalized_held 周期的 pending intent 过继给新周期，candidate 保留。"""
    conn, orch = orch_env
    _mk_held_intent(orch, conn, "etf_daily", "2000000000000", "finalized_held")
    new_cid = orch.begin_cycle(conn)
    row = conn.execute(
        "SELECT cycle_id, status, candidate_watermark, hold_reason "
        "FROM qfq_watermark_intent WHERE table_name='etf_daily'").fetchone()
    assert row[0] == new_cid, "可恢复 held intent 应过继给新周期"
    assert row[1] == "pending"
    assert row[2] == "2000000000000", "candidate_watermark 必须保留（不得作废）"
    assert row[3] is None


def test_bug3_negative_dead_cycle_still_superseded(orch_env):
    """#3 负向：failed 周期与无周期行的 intent 照旧 superseded（清障语义不放宽）。"""
    conn, orch = orch_env
    _mk_held_intent(orch, conn, "stock_daily", "1500000000000", "failed")
    orphan_cid = orch.begin_cycle(conn)
    orch.defer_watermark(
        conn, cycle_id=orphan_cid, source="xtquant", table="etf_minutes",
        freq="1min", candidate_watermark="1600000000000")
    conn.execute("DELETE FROM qfq_cycle_run WHERE cycle_id=?", [orphan_cid])
    orch.begin_cycle(conn)
    rows = dict((t, s) for t, s in conn.execute(
        "SELECT table_name, status FROM qfq_watermark_intent").fetchall())
    assert rows["stock_daily"] == "superseded", "failed 周期 intent 必须照旧清障"
    assert rows["etf_minutes"] == "superseded", "无周期行 intent 必须照旧清障"


def test_bug3_multi_candidate_only_max_adopted(orch_env):
    """#3 负向（防放大）：同键多条 pending 只过继最大 candidate 一条，其余 superseded。

    注：经 defer_watermark 正常流无法造出同键两行（begin_cycle 过继 + defer 的
    ON CONFLICT 同键 upsert 会合并），故此处直插两行验证 supersede 层去重。
    """
    conn, orch = orch_env
    c1 = orch.begin_cycle(conn)
    c2 = orch.begin_cycle(conn)

    def _ins_intent(cyc, cand):
        conn.execute(
            "INSERT INTO qfq_watermark_intent (cycle_id, source, table_name, freq, "
            "source_generation, cutover_id, old_watermark, candidate_watermark, status) "
            "VALUES (?,?,?,?,?,?,NULL,?,'pending')",
            [cyc, "xtquant", "etf_daily", "daily", GEN[1], GEN[2], cand])
    _ins_intent(c1, "1500000000000")
    _ins_intent(c2, "1800000000000")
    conn.execute(
        "UPDATE qfq_cycle_run SET status='finalized_held' WHERE cycle_id IN (?,?)",
        [c1, c2])
    orch.begin_cycle(conn)
    rows = conn.execute(
        "SELECT candidate_watermark, status FROM qfq_watermark_intent "
        "WHERE table_name='etf_daily' ORDER BY candidate_watermark").fetchall()
    assert ("1500000000000", "superseded") in rows
    assert ("1800000000000", "pending") in rows, "只过继最大候选一条"


# ══════════════════════════════════════════════════════════════════════
# #1 writer：去 RETURNING + spill 兜底
# ══════════════════════════════════════════════════════════════════════
TABLE = "stock_daily"
COLS = ["code", "time", "close", "volume", "amount", "preClose", "pctChg"]


def _mk_writer(tmp_path, name="w"):
    db = tmp_path / f"{name}.duckdb"
    return db, DuckDBWriter({"type": "duckdb", "path": str(db)})


class _SpyConn:
    def __init__(self, real):
        self._real = real
        self.sql = []

    def execute(self, query, *a, **k):
        self.sql.append(query)
        return self._real.execute(query, *a, **k)

    def register(self, *a, **k):
        return self._real.register(*a, **k)

    def unregister(self, *a, **k):
        return self._real.unregister(*a, **k)

    def close(self):
        return self._real.close()


def test_bug1_no_returning_in_upsert_sql(tmp_path, monkeypatch):
    """#1 SQL 形态：T2 开启/关闭两态 upsert 均不含 RETURNING（物化开销移除）。"""
    monkeypatch.setenv("QS_UPSERT_SKIP_IDENTICAL", "1")
    db, w = _mk_writer(tmp_path)
    seed = pd.DataFrame(
        [["600000", 1000, 10.0, 1.0, 1.0, 9.5, 0.5]], columns=COLS)
    w.write(seed, TABLE, "seed")
    real = w._conn
    holder = {}

    def factory():
        s = _SpyConn(real())
        holder["spy"] = s
        return s
    w._conn = factory
    try:
        w.write(seed, TABLE, "b")
    finally:
        try:
            del w._conn
        except AttributeError:
            pass
    upserts = [q for q in holder["spy"].sql
               if isinstance(q, str) and q.startswith("INSERT INTO " + TABLE)]
    assert upserts, "未捕获 upsert SQL"
    for q in upserts:
        assert "RETURNING" not in q.upper(), "upsert 不得携带 RETURNING（缺陷#1）"
    assert any("IS DISTINCT FROM" in q for q in upserts), "T2 WHERE 语义保持"


def test_bug1_spill_defaults(tmp_path, monkeypatch):
    """#1 spill 兜底：temp_directory 指到库旁 _duckdb_tmp；memory_limit 仅显式 env 生效。"""
    monkeypatch.delenv("QS_DUCKDB_MEMORY_LIMIT", raising=False)
    db, w = _mk_writer(tmp_path, "spill")
    conn = w._conn()
    try:
        td = conn.execute("SELECT current_setting('temp_directory')").fetchone()[0]
        assert td.replace("\\", "/").endswith("_duckdb_tmp"), td
        assert Path(td).exists()
    finally:
        conn.close()
    monkeypatch.setenv("QS_DUCKDB_MEMORY_LIMIT", "2GB")
    conn2 = w._conn()
    try:
        ml = conn2.execute("SELECT current_setting('memory_limit')").fetchone()[0]
        # DuckDB 1.4.5 把十进制 '2GB'（2e9 B）规范化显示为 GiB（≈1.86）——
        # 断言换算值而非字符串前缀（机器默认 = 80% RAM，不会落在 1.7~2.0 区间）。
        _gi = float(ml.split()[0])
        assert abs(_gi - 2e9 / 2**30) < 0.2, ml
    finally:
        conn2.close()


def test_bug1_bounded_synthetic_overlap_smoke(tmp_path, monkeypatch):
    """#1 有界合成冒烟（非 447 万行挂起复刻）：20 万行基线 + 4×5 万重叠批，
    T2 开启，全链在护栏时间内完成且数据正确——回归护栏防执行计划退化。"""
    monkeypatch.setenv("QS_UPSERT_SKIP_IDENTICAL", "1")
    _db, w = _mk_writer(tmp_path, "syn")
    base = pd.DataFrame(
        [[f"{600000 + i:06d}", 1000, 10.0, 1.0, 1.0, 9.5, 0.5]
         for i in range(200_000)], columns=COLS)
    t0 = time.perf_counter()
    w.write(base, TABLE, "seed")
    for k in range(4):
        batch = base.iloc[k * 50_000:(k + 1) * 50_000].copy()
        batch["close"] = 11.0 + k          # 制造真实变化（进 delete+insert 相位）
        w.write(batch, TABLE, f"ov{k}")
    elapsed = time.perf_counter() - t0
    n = w.execute_read(f"SELECT COUNT(*) FROM {TABLE}")[0][0]
    assert n == 200_000
    changed = w.execute_read(
        f"SELECT COUNT(*) FROM {TABLE} WHERE close >= 11.0")[0][0]
    assert changed == 200_000
    assert elapsed < 120.0, f"合成护栏超时（{elapsed:.1f}s）——执行计划可能退化"
