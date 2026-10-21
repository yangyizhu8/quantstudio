"""firefly「水位线」案（2026-10-18）N4 / FP-4 防回归测试。

缺陷：停止路径只写 sidecar（run_state / qfq_cycle_terminal），不写 DB 终态
      ⇒ ``qfq_cycle_run`` 永久停在 ``started``（生产实测 10 条）。

本文件锁定契约（A13–A15）：
  A13 活跃周期 → 置 ``interrupted`` 且 ``finished_at`` 非空、``error`` 带原因；
  A14 水位零改动（只补终态字段）；
  A15 幂等/隔离：二次调用 False；非本 identity / 已终态 / 行不存在 → False 且不动。

运行：.venv/Scripts/python -m pytest tests/test_qfq_cycle_abort.py -q
"""

from __future__ import annotations

import datetime as dt
import inspect

import duckdb
import pytest

from quantstudio.pipeline.qfq_resident_orchestrator import QFQResidentOrchestrator

IDENT = {"price_source": "mcp", "source_generation": "mcp-gen1",
         "cutover_id": "b6_formal_20260807_v2"}


@pytest.fixture()
def con(tmp_path):
    c = duckdb.connect(str(tmp_path / "t.db"))
    c.execute(
        "CREATE TABLE qfq_cycle_run (cycle_id VARCHAR, status VARCHAR, phase VARCHAR, "
        "started_at TIMESTAMP, updated_at TIMESTAMP, finished_at TIMESTAMP, error VARCHAR, "
        "price_source VARCHAR, source_generation VARCHAR, cutover_id VARCHAR)")
    c.execute(
        "CREATE TABLE source_watermark (source VARCHAR, table_name VARCHAR, freq VARCHAR, "
        "last_date BIGINT, last_batch_id VARCHAR, updated_at TIMESTAMP)")
    c.execute("INSERT INTO source_watermark VALUES "
              "('mcp','stock_daily','daily', 1788364800000,'manual_x','2026-09-12 21:35:06')")
    yield c
    c.close()


def _orch(ident=None):
    """裸实例（跳过 __init__）：本方法只依赖 _ident，避免拉起编排器全链路。"""
    o = QFQResidentOrchestrator.__new__(QFQResidentOrchestrator)
    o._ident = dict(ident or IDENT)
    return o


def _add(con, cycle_id="cyc_a", status="started", phase="applying", ident=None):
    i = ident or IDENT
    con.execute(
        "INSERT INTO qfq_cycle_run VALUES (?,?,?,?,?,NULL,NULL,?,?,?)",
        [cycle_id, status, phase, dt.datetime(2026, 10, 17, 15, 27, 2),
         dt.datetime(2026, 10, 17, 19, 34, 44),
         i["price_source"], i["source_generation"], i["cutover_id"]])


def _row(con, cycle_id="cyc_a"):
    return con.execute(
        "SELECT status, phase, finished_at, error FROM qfq_cycle_run WHERE cycle_id=?",
        [cycle_id]).fetchone()


# ---------------- A13 / A14 ----------------
def test_a13_stop_marks_cycle_interrupted_and_keeps_watermark(con):
    _add(con)
    wm_before = con.execute("SELECT * FROM source_watermark").fetchall()
    assert _orch().close_cycle_interrupted(con, cycle_id="cyc_a",
                                           reason="stop_requested") is True
    status, phase, finished_at, error = _row(con)
    assert status == "interrupted" and phase == "interrupted"
    assert finished_at is not None
    assert error.startswith("interrupted:")
    # A14：水位逐字不动
    assert con.execute("SELECT * FROM source_watermark").fetchall() == wm_before


# ---------------- A15：幂等 / 隔离 ----------------
def test_a15_second_call_returns_false(con):
    _add(con)
    o = _orch()
    assert o.close_cycle_interrupted(con, cycle_id="cyc_a", reason="r1") is True
    first = _row(con)
    assert o.close_cycle_interrupted(con, cycle_id="cyc_a", reason="r2") is False
    assert _row(con) == first, "已终态不得被二次改写（error 应保留首次原因）"


@pytest.mark.parametrize("status", ["finalized", "finalized_held", "failed", "interrupted"])
def test_a15_terminal_states_untouched(con, status):
    _add(con, status=status, phase="finalized")
    assert _orch().close_cycle_interrupted(con, cycle_id="cyc_a", reason="r") is False
    assert _row(con)[0] == status


def test_a15_other_identity_not_touched(con):
    _add(con, ident={"price_source": "mcp", "source_generation": "other-gen",
                     "cutover_id": "b6_formal_20260807_v2"})
    assert _orch().close_cycle_interrupted(con, cycle_id="cyc_a", reason="r") is False
    assert _row(con)[0] == "started"


def test_a15_missing_cycle_returns_false(con):
    assert _orch().close_cycle_interrupted(con, cycle_id="cyc_nope", reason="r") is False


# ---------------- 接线守卫：停止分支持有终态补齐调用 ----------------
def test_wiring_lifecycle_calls_abort_on_stop():
    from quantstudio.pipeline import daemon_lifecycle as dl
    src = inspect.getsource(dl)
    assert "qfq_abort_cycle" in src, "停止分支必须调用 FP-4 终态补齐"
    assert "getattr(collector, \"qfq_abort_cycle\", None)" in src, \
        "必须以 getattr 守卫调用（兼容无该方法的 mock collector）"
