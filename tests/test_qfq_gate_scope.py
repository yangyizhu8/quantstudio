"""firefly 水位线案 FP-5/FP-6/FP-7 防回归测试（2026-10-20 受控窗）。

判据映射：
  A20    窗口内全 committed + 窗口外历史 dead_letter=100 → passed=True，health.dead_letter=100
  A20-N  该负向对照在 FP-6 实施前后**均须 passed=False**（证明判定未放宽）
  A21    claimed 单元内 trigger 非 committed → 仍 fail（单单元判定零放宽）
  A22    FP-7 配额：5 单元 / cap=2 → claim=2、顺延=3、其余保持 pending
  A23    配额开关不改变单元内容（triggers/effective_dates 逐字一致）
  A24/25 source_watermark.last_run_id 写入 + 迁移幂等
  A28    QS_QFQ_GATE_SCOPE=global → 修复前语义（窗口外 dead_letter 亦阻断）
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

import quantstudio.pipeline.qfq_resident_orchestrator as QO
from quantstudio.pipeline.qfq_resident_orchestrator import QFQResidentOrchestrator

IDENT = {"price_source": "mcp", "source_generation": "mcp-gen1",
         "cutover_id": "b6_formal_20260807_v2"}
NOW_MS = int(dt.datetime(2026, 10, 20, 23, 0, 0).timestamp() * 1000)


@pytest.fixture()
def con(tmp_path):
    c = duckdb.connect(str(tmp_path / "t.db"))
    c.execute(
        "CREATE TABLE qfq_trigger_queue (trigger_id VARCHAR, asset_type VARCHAR, code VARCHAR, "
        "trigger_type VARCHAR, source_key VARCHAR, effective_date BIGINT, payload_hash VARCHAR, "
        "factor_old DOUBLE, factor_new DOUBLE, factor_revision VARCHAR, status VARCHAR, "
        "attempt_count INTEGER, claimed_at TIMESTAMP, updated_at TIMESTAMP, claimed_by VARCHAR, "
        "price_source VARCHAR, source_generation VARCHAR, cutover_id VARCHAR)")
    c.execute(
        "CREATE TABLE qfq_watermark_intent (cycle_id VARCHAR, source VARCHAR, table_name VARCHAR, "
        "freq VARCHAR, source_generation VARCHAR, cutover_id VARCHAR, old_watermark BIGINT, "
        "candidate_watermark BIGINT, status VARCHAR, hold_reason VARCHAR, committed_at TIMESTAMP)")
    c.execute(
        "CREATE TABLE source_watermark (source VARCHAR, table_name VARCHAR, freq VARCHAR, "
        "last_date BIGINT, last_batch_id VARCHAR, updated_at TIMESTAMP, "
        "source_generation VARCHAR, cutover_id VARCHAR, "
        "PRIMARY KEY(source, table_name, freq))")
    c.execute("INSERT INTO source_watermark VALUES "
              "('mcp','stock_daily','daily',1788364800000,'batch_a','2026-09-12 21:35:06','mcp-gen1','b6_formal_20260807_v2')")
    yield c
    c.close()


def _orch(cap=0):
    o = QFQResidentOrchestrator.__new__(QFQResidentOrchestrator)
    o._ident = dict(IDENT)
    o.watermark_advancer = None          # 裸实例：dynamic 分支走直接 upsert
    o.cfg = SimpleNamespace(claim_lease_sec=600, quality_thresholds={"dead_letter_max": 0},
                            max_units_per_cycle=cap)
    return o


def _trg(con, tid, code="000001", status="pending", eff=NOW_MS, claimed_at=None):
    con.execute(
        "INSERT INTO qfq_trigger_queue (trigger_id, asset_type, code, trigger_type, "
        "source_key, effective_date, payload_hash, status, attempt_count, claimed_at, "
        "updated_at, price_source, source_generation, cutover_id) "
        "VALUES (?,?,?,?,?,?,?,?,0,?,?,?,?,?)",
        [tid, "STOCK", code, "stock_dividend", "k", eff, "h", status, claimed_at,
         dt.datetime.now(), IDENT["price_source"], IDENT["source_generation"],
         IDENT["cutover_id"]])


def _intent(con, cycle="cyc_t", table="stock_daily", old=1788364800000, cand=1792080000000):
    con.execute(
        "INSERT INTO qfq_watermark_intent VALUES (?,?,?,?,?,?,?,?,'pending',NULL,NULL)",
        [cycle, "mcp", table, "daily", IDENT["source_generation"], IDENT["cutover_id"],
         old, cand])


def _patch_gate_deps(monkeypatch, orch):
    monkeypatch.setattr(QO, "audit_pending_slots", lambda *a, **k: {"passed": True})
    monkeypatch.setattr(orch, "_baseline_identity", lambda: object())


# ---------------- A20 / A20-N / A21：判定域收敛但不放宽 ----------------
def test_a20_out_of_window_dead_letter_no_longer_blocks(con, monkeypatch):
    orch = _orch()
    _patch_gate_deps(monkeypatch, orch)
    _trg(con, "t_claimed", status="committed", eff=NOW_MS)
    # 窗口外历史死信 100 条（effective_date 远早于 old_watermark）
    for i in range(100):
        _trg(con, f"t_hist_{i}", code="999999", status="dead_letter", eff=1700000000000)
    _intent(con)
    passed, report = orch._qfq_gate(con, "cyc_t", [{"code": "000001", "triggers": ["t_claimed"]}],
                                    "run1")
    assert passed is True, report["reasons"]
    assert report["gate_scope"] == "cycle"
    assert report["health"]["dead_letter"] == 100
    assert report["dead_letter_window"] == [1788364800000, 1792080000000]


def test_a20n_non_committed_claimed_trigger_still_fails(con, monkeypatch):
    """负向对照：FP-6 实施前后均须 passed=False（判定未放宽）。"""
    orch = _orch()
    _patch_gate_deps(monkeypatch, orch)
    _trg(con, "t_claimed", status="pending", eff=NOW_MS)
    _intent(con)
    passed, report = orch._qfq_gate(con, "cyc_t",
                                    [{"code": "000001", "triggers": ["t_claimed"]}], "run1")
    assert passed is False
    assert any("未 committed" in r for r in report["reasons"])


def test_a20_in_window_dead_letter_still_blocks(con, monkeypatch):
    orch = _orch()
    _patch_gate_deps(monkeypatch, orch)
    _trg(con, "t_claimed", status="committed", eff=NOW_MS)
    _trg(con, "t_dead_in", code="000002", status="dead_letter",
         eff=1790000000000)          # 落在 [old, candidate] 窗口内
    _intent(con)
    passed, report = orch._qfq_gate(con, "cyc_t",
                                    [{"code": "000001", "triggers": ["t_claimed"]}], "run1")
    assert passed is False
    assert any("dead_letter=1" in r for r in report["reasons"])


def test_a28_global_scope_restores_old_semantics(con, monkeypatch):
    monkeypatch.setenv("QS_QFQ_GATE_SCOPE", "global")
    orch = _orch()
    _patch_gate_deps(monkeypatch, orch)
    _trg(con, "t_claimed", status="committed", eff=NOW_MS)
    for i in range(5):
        _trg(con, f"t_hist_{i}", code="999999", status="dead_letter", eff=1700000000000)
    _intent(con)
    passed, report = orch._qfq_gate(con, "cyc_t",
                                    [{"code": "000001", "triggers": ["t_claimed"]}], "run1")
    assert passed is False, "global 域下窗口外死信必须照旧阻断（A28 回退门）"
    assert report["gate_scope"] == "global"


# ---------------- A22 / A23：配额 ----------------
def test_a22_quota_caps_claimed_units(con):
    orch = _orch(cap=2)
    for i in range(5):
        _trg(con, f"t{i}", code=f"00000{i}", status="pending")
    units = orch._claim_and_merge(con, cycle_id="cyc_t", run_id="run1", as_of_ms=NOW_MS)
    assert len(units) == 2
    assert orch._last_units_deferred == 3
    assert orch._last_units_total == 5
    left = con.execute("select count(*) from qfq_trigger_queue where status='pending'").fetchone()[0]
    assert left == 3, "顺延单元必须保持 pending（不 claim 后丢弃）"


def test_a23_quota_does_not_change_unit_content(con):
    for i in range(3):
        _trg(con, f"t{i}", code=f"00000{i}", status="pending")
    a = _orch(cap=0)._claim_and_merge(con, cycle_id="c1", run_id="r1", as_of_ms=NOW_MS)
    # 复位后测配额
    con.execute("UPDATE qfq_trigger_queue SET status='pending'")
    b = _orch(cap=1)._claim_and_merge(con, cycle_id="c2", run_id="r2", as_of_ms=NOW_MS)
    assert b[0] == a[0], "配额不得改变单元内容（同序取前 N）"


# ---------------- A24 / A25：last_run_id ----------------
def test_a24_advance_watermark_writes_run_id_when_column_exists(con):
    con.execute("ALTER TABLE source_watermark ADD COLUMN last_run_id VARCHAR")
    orch = _orch()
    orch._advance_watermark(con, "mcp", "stock_daily", "daily", 1792080000000, "run_xyz")
    row = con.execute("SELECT last_date,last_batch_id,last_run_id FROM source_watermark "
                      "WHERE table_name='stock_daily'").fetchone()
    assert row == (1792080000000, "run_xyz", "run_xyz")
    assert con.execute("SELECT count(*) FROM source_watermark").fetchone()[0] == 1, "必须 upsert 而非插入新行"


def test_a24_advance_watermark_without_column_still_works(con):
    orch = _orch()
    orch._advance_watermark(con, "mcp", "stock_daily", "daily", 1792080000000, "run_abc")
    row = con.execute("SELECT last_date,last_batch_id FROM source_watermark "
                      "WHERE table_name='stock_daily'").fetchone()
    assert row == (1792080000000, "run_abc"), "旧库（无 last_run_id）路径必须逐位兼容"


def test_a25_migration_script_idempotent(tmp_path):
    """FP-5 迁移 runner：契约闸 + 预检 + 幂等（2026-10-22 升级为契约级迁移）。

    2026-10-22 起本脚本是**契约级迁移**（2.1→2.2），预检要求 COMPLETE_2_1：
    · 未确认契约变更 → exit 5（负向对照，2026-10-20 事故后加固）；
    · legacy 6 列库 → exit 6（预检拒绝：必须先经 2.0→2.1 迁移，不得跨版本直跳）；
    · 真 2.1 库 → 迁移成功 + 二次幂等。
    """
    spec = importlib.util.spec_from_file_location(
        "mig_fp5", Path(__file__).resolve().parents[1] / "scripts" /
        "migrate_source_watermark_last_run_id.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # —— 负向①：legacy 6 列库（非 2.1）不得被本迁移接受 ——
    legacy = tmp_path / "legacy.db"
    c = duckdb.connect(str(legacy))
    c.execute("CREATE TABLE source_watermark (source VARCHAR, table_name VARCHAR, freq VARCHAR, "
              "last_date BIGINT, last_batch_id VARCHAR, updated_at TIMESTAMP)")
    c.execute("INSERT INTO source_watermark VALUES ('mcp','x','daily',1,'b',NULL)")
    c.close()
    assert mod.main(["--db", str(legacy)]) == 5, "无 --allow-schema-contract-change 必须拒执行"
    assert mod.main(["--db", str(legacy), "--allow-schema-contract-change"]) == 6, \
        "非 COMPLETE_2_1 库必须被预检拒绝（exit 6）"

    # —— 正向：真 2.1 库 → 迁移 + 幂等 ——
    from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema
    db = tmp_path / "m.db"
    c = duckdb.connect(str(db))
    init_duckdb_schema(c)
    c.execute("INSERT INTO source_watermark (source, table_name, freq, last_date, "
              "last_batch_id, updated_at, source_generation, cutover_id) VALUES "
              "('mcp','x','daily',1,'b',now(),'mcp-gen1','cut-1')")
    c.close()
    assert mod.main(["--db", str(db), "--dry-run"]) == 0
    assert mod.main(["--db", str(db), "--allow-schema-contract-change"]) == 0
    assert mod.main(["--db", str(db), "--allow-schema-contract-change"]) == 0, "重复执行必须幂等"
    c = duckdb.connect(str(db), read_only=True)
    n = c.execute("SELECT count(*) FROM source_watermark").fetchone()[0]
    cols = [r[0] for r in c.execute(
        "select column_name from duckdb_columns() where table_name='source_watermark'").fetchall()]
    c.close()
    assert n == 1
    assert "last_run_id" in cols
