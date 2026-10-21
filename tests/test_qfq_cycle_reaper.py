"""firefly「水位线」案（2026-10-18）N4 / FP-3 防回归测试。

缺陷：``begin_cycle`` 只清 intent 不关闭周期行；停止路径只写 sidecar 不写 DB 终态
      ⇒ 进程重启后 ``qfq_cycle_run`` 留下永久 ``started``（生产实测 10 条）。

本文件锁定契约（A9–A12）：
  A9  干跑清单 == 符合双条件门的行；
  A10 **活进程拒收**：started_at 晚于本进程启动 → 一律不回收（防误杀长 applying）；
  A11 幂等：二次调用零变更；
  A12 水位零改动：``source_watermark`` 前后逐字相同（只修终态记录）。
  另含：终态不动 / identity 隔离 / dry_run 不写库 / 阈值可配。

说明：本文件全程复用**同一条连接**（pytest 下多连接快照语义会干扰断言，
      与被测代码无关），断言对象仍是 DB 真实行。

运行：.venv/Scripts/python -m pytest tests/test_qfq_cycle_reaper.py -q
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pytest

from quantstudio.pipeline.qfq_resident_orchestrator import reap_stale_cycles

IDENT = {"price_source": "mcp", "source_generation": "mcp-gen1",
         "cutover_id": "b6_formal_20260807_v2"}
OTHER = {"price_source": "mcp", "source_generation": "other-gen",
         "cutover_id": "b6_formal_20260807_v2"}
NOW = dt.datetime.now()                          # 真实墙钟（与 _stale_active_cutoff 同基）
# 本进程启动 = 10 分钟前：早于此刻开始的周期才可能是"上一进程遗留"
PROC_START_MS = int((NOW - dt.timedelta(minutes=10)).timestamp() * 1000)


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


def _add_cycle(con, cycle_id="cyc_stale", status="started", phase="applying",
               started=None, updated=None,
               ident=None):
    """默认=「上一进程遗留」：2 天前开始、5 小时前更新（越 4h 阈值）。"""
    started = started if started is not None else (NOW - dt.timedelta(days=2))
    updated = updated if updated is not None else (NOW - dt.timedelta(hours=5))
    ident = ident or IDENT
    con.execute(
        "INSERT INTO qfq_cycle_run VALUES (?,?,?,?,?,NULL,NULL,?,?,?)",
        [cycle_id, status, phase, started, updated,
         ident["price_source"], ident["source_generation"], ident["cutover_id"]])


def _reap(con, ident=None, **kw):
    return reap_stale_cycles(con, ident=ident or IDENT,
                             process_start_ms=PROC_START_MS, **kw)


def _cycle(con, cycle_id):
    return con.execute(
        "SELECT status, phase, finished_at, error FROM qfq_cycle_run WHERE cycle_id=?",
        [cycle_id]).fetchone()


def _wm(con):
    return con.execute("SELECT last_date, last_batch_id FROM source_watermark").fetchall()


# ---------------- A9：干跑清单 + 正式回收 ----------------
def test_a9_reap_marks_previous_process_cycle_interrupted(con):
    _add_cycle(con)
    dry = _reap(con, dry_run=True)
    assert [x["cycle_id"] for x in dry] == ["cyc_stale"], "干跑清单必须命中"
    assert _cycle(con, "cyc_stale")[0] == "started", "dry_run 不得写库"

    got = _reap(con)
    assert [x["cycle_id"] for x in got] == ["cyc_stale"]
    status, phase, finished_at, error = _cycle(con, "cyc_stale")
    assert status == "interrupted" and phase == "interrupted"
    assert finished_at is not None
    assert error.startswith("reaped:")
    # A12：水位逐字不动
    assert _wm(con) == [(1788364800000, "manual_x")]


# ---------------- A10：活进程拒收（负向对照）----------------
def test_a10_cycle_started_after_process_start_is_not_reaped(con):
    # started_at 晚于本进程启动 = 属当前活进程（applying 实测可长跑 6.5h+，
    # updated_at 也可能陈旧）——必须拒收
    _add_cycle(con, cycle_id="cyc_live", started=NOW - dt.timedelta(minutes=5),
               updated=NOW - dt.timedelta(minutes=4))
    assert _reap(con) == [], "活进程的周期绝不能被回收"
    assert _cycle(con, "cyc_live")[0] == "started"


def test_a10_fresh_cycle_is_not_reaped(con):
    _add_cycle(con, cycle_id="cyc_fresh", started=NOW - dt.timedelta(minutes=30),
               updated=NOW - dt.timedelta(minutes=1))
    assert _reap(con) == []


# ---------------- A11：幂等 ----------------
def test_a11_second_run_is_noop(con):
    _add_cycle(con)
    assert len(_reap(con)) == 1
    first = _cycle(con, "cyc_stale")
    assert _reap(con) == [], "已是终态 → 二次调用必须零命中"
    assert _cycle(con, "cyc_stale") == first


# ---------------- 终态不动 / identity 隔离 / 阈值 ----------------
@pytest.mark.parametrize("status,phase", [
    ("finalized", "finalized"), ("finalized_held", "finalized"),
    ("failed", "failed"), ("interrupted", "interrupted")])
def test_terminal_states_untouched(con, status, phase):
    _add_cycle(con, cycle_id="cyc_t", status=status, phase=phase)
    assert _reap(con) == []
    assert _cycle(con, "cyc_t")[0] == status


def test_identity_isolation(con):
    _add_cycle(con, cycle_id="cyc_other", ident=OTHER)
    assert _reap(con) == [], "异世代周期不得被本 identity 回收"
    assert _cycle(con, "cyc_other")[0] == "started"


def test_custom_max_age_threshold(con):
    _add_cycle(con, cycle_id="cyc_1h", started=NOW - dt.timedelta(days=2),
               updated=NOW - dt.timedelta(hours=1))
    assert _reap(con, max_age_s=4 * 3600) == [], "1h 前更新 → 4h 阈值下不回收"
    assert [x["cycle_id"] for x in _reap(con, max_age_s=1800)] == ["cyc_1h"], \
        "阈值收到 30min 后应命中（阈值可配，语义不变）"
