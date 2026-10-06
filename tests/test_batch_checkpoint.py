# -*- coding: utf-8 -*-
"""T1 批级断点（batch_checkpoint）验收测试（规格件 §三 T1 / §六 验收 / §七 质量判据）。

被测对象（writers.py 新增，开关 QS_BATCH_CHECKPOINT 默认关）：
    _is_batch_checkpoint_enabled()                 fail-closed 开关解析
    DuckDBWriter.batch_checkpoint_commit(...)      提交「已完成批」断点（fail-closed）
    DuckDBWriter.batch_checkpoint_load(...)        读断点 -> {window_key: status}
    DuckDBWriter.batch_checkpoint_clear(...)       任务自然成功后清理
daemon 接入面（daemon.py 新增）：
    ResidentCollector._bc_filter_completed(...)    续跑：剔除已完成批（其余一律保留）
    ResidentCollector._bc_commit(...)              写入成功后提交（gate 关/异常 no-op）

覆盖矩阵：
    S  开关 fail-closed 解析（默认关）。
    E  等价性：gate 关闭 -> 不建表 / load={} / commit=False / clear no-op；写入零变化。
    R  commit -> load 往返（status=completed、rows_written 保真）+ 键隔离。
    I  幂等：同键重复 commit 仅一行且字段更新。
    F  fail-closed 铁律（"断点可红"，P-10 同款：越断点场景必须阻断跳过）：
       F1 写断点失败（INSERT 抛错）-> 无断点 -> 该窗口必须重跑；
       F2 回读校验失败 -> commit=False 且撤销 completed 标记 -> 该窗口必须重跑；
       F3 状态非 completed / 键缺失 / 读异常 -> 一律不跳过（必须阻断）。
    C  clear 只清指定 (task, table, freq)。
    D  daemon 接入面：gate 关原样返回（同一对象）；_bc_commit gate 关零副作用。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import duckdb
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline.writers import (  # noqa: E402
    DuckDBWriter, _is_batch_checkpoint_enabled)

ENV = "QS_BATCH_CHECKPOINT"
BC_TABLE = "batch_checkpoint"
TABLE = "stock_daily"
COLS = ["code", "time", "close", "volume", "amount", "preClose", "pctChg"]


# ── helpers ─────────────────────────────────────────────────────────────
def _mk(tmp_path, name):
    db = tmp_path / f"{name}.duckdb"
    return db, DuckDBWriter({"type": "duckdb", "path": str(db)})


def _df(rows):
    return pd.DataFrame(rows, columns=COLS)


def _db_connect(db, read_only=True):
    return duckdb.connect(str(db), read_only=read_only)


def _table_exists(db, table):
    con = _db_connect(db)
    try:
        n = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [table]).fetchone()[0]
        return n > 0
    finally:
        con.close()


def _bc_rows(db, task, table, freq):
    """读断点表原始行 [(window_key, status, rows_written, batch_id)]。"""
    if not _table_exists(db, BC_TABLE):
        return []
    con = _db_connect(db)
    try:
        return con.execute(
            "SELECT window_key, status, rows_written, batch_id FROM batch_checkpoint "
            "WHERE task_name=? AND table_name=? AND freq=? ORDER BY window_key",
            [task, table, freq]).fetchall()
    finally:
        con.close()


def _bc_raw_insert(db, task, table, freq, window_key, status, rows=0, batch="raw"):
    con = _db_connect(db, read_only=False)
    try:
        con.execute(
            "INSERT INTO batch_checkpoint "
            "(task_name, table_name, freq, window_key, batch_id, status, rows_written, updated_at) "
            "VALUES (?,?,?,?,?,?,?, now())",
            [task, table, freq, window_key, batch, status, rows])
    finally:
        con.close()


def _snapshot(db):
    con = _db_connect(db)
    try:
        rows = con.execute(
            "SELECT " + ", ".join(COLS) + f" FROM {TABLE} ORDER BY code, time").fetchall()
    finally:
        con.close()

    def cell(v):
        return ("NULL",) if v is None else (type(v).__name__, repr(v))
    return tuple(cell(c) for r in rows for c in r)


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [] if self._row is None else [self._row]


class _Spy:
    """拦截 DuckDBWriter._conn() 返回的连接：可注入 INSERT 失败 / 伪造回读。"""

    def __init__(self, real):
        self._real = real
        self.sql = []
        self.raise_on = None    # 命中前缀则抛错（模拟写断点失败）
        self.fake_back = None   # 伪造回读行（模拟回读校验失败）

    def execute(self, q, *a, **k):
        self.sql.append(q)
        if isinstance(q, str):
            if self.raise_on and q.startswith(self.raise_on):
                raise RuntimeError("inject:" + self.raise_on)
            if (self.fake_back is not None
                    and q.startswith("SELECT status, rows_written FROM batch_checkpoint")):
                return _FakeResult(self.fake_back)
        return self._real.execute(q, *a, **k)

    def register(self, *a, **k):
        return self._real.register(*a, **k)

    def unregister(self, *a, **k):
        return self._real.unregister(*a, **k)

    def close(self):
        return self._real.close()


def _install_spy(writer, raise_on=None, fake_back=None):
    real = writer._conn
    holder = {}

    def factory():
        s = _Spy(real())
        s.raise_on = raise_on
        s.fake_back = fake_back
        holder["spy"] = s
        return s

    writer._conn = factory
    return holder


def _uninstall_spy(writer):
    try:
        del writer._conn
    except AttributeError:
        pass


def _daemon():
    return pytest.importorskip("quantstudio.pipeline.daemon")


def _stub(writer):
    """最小 ResidentCollector 替身：仅需 .writer 即可绑定 _bc_* 方法。"""
    return types.SimpleNamespace(writer=writer)


SEED = _df([
    ["600000", 1000, 10.0, 100.0, 1000.0, 9.5, 0.5],
    ["600001", 1000, 20.0, 200.0, 2000.0, 19.5, 0.5],
    ["600002", 1000, 30.0, 300.0, 3000.0, 29.5, 0.5],
])
BATCH = _df([
    ["600000", 1000, 10.0, 100.0, 1000.0, 9.5, 0.5],
    ["600001", 1000, 21.5, 200.0, 2000.0, 19.5, 0.5],
    ["600003", 1000, 40.0, 400.0, 4000.0, 39.5, 0.5],
])


# ── S 开关 fail-closed 解析 ─────────────────────────────────────────────
@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("on", True), ("TRUE", True), ("On", True),
    ("0", False), ("false", False), ("off", False), ("", False),
    ("yes", False), ("2", False), ("  ", False),
])
def test_switch_fail_closed(monkeypatch, val, expected):
    monkeypatch.setenv(ENV, val)
    assert _is_batch_checkpoint_enabled() is expected


def test_switch_default_off(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert _is_batch_checkpoint_enabled() is False


# ── E 等价性：gate 关闭零回归 ───────────────────────────────────────────
def test_off_gate_creates_no_table_and_zero_effect(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    db, w = _mk(tmp_path, "off")
    r = w.write(SEED, TABLE, "seed")
    assert int(r) == 3
    assert not _table_exists(db, BC_TABLE), "关闭态不得创建 batch_checkpoint 表"
    assert w.batch_checkpoint_load("t", TABLE, "daily") == {}
    assert w.batch_checkpoint_commit("t", TABLE, "daily", "w1", "b1", 5) is False
    w.batch_checkpoint_clear("t", TABLE, "daily")     # no-op
    assert not _table_exists(db, BC_TABLE), "关闭态 clear 不得建表"


def test_off_gate_write_equivalence(tmp_path, monkeypatch):
    """等价性口径：既有批写入逐位不变（gate 关 vs 开，同输入终态逐列逐位一致）。"""
    monkeypatch.delenv(ENV, raising=False)
    db_off, w_off = _mk(tmp_path, "eq_off")
    db_on, w_on = _mk(tmp_path, "eq_on")
    w_off.write(SEED, TABLE, "seed")
    monkeypatch.setenv(ENV, "1")
    w_on.write(SEED, TABLE, "seed")
    w_off.write(BATCH, TABLE, "b2")
    w_on.write(BATCH, TABLE, "b2")
    assert _snapshot(db_off) == _snapshot(db_on), "T1 开关改变了既有批写入终态"


# ── R 往返 + 键隔离 ─────────────────────────────────────────────────────
def test_commit_load_roundtrip_and_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV, "1")
    db, w = _mk(tmp_path, "rt")
    assert w.batch_checkpoint_commit("t", TABLE, "daily", "20260901", "b1", 7) is True
    assert w.batch_checkpoint_load("t", TABLE, "daily") == {"20260901": "completed"}
    assert _bc_rows(db, "t", TABLE, "daily") == [("20260901", "completed", 7, "b1")]
    # 键隔离：table / freq / task / window 互不影响
    assert w.batch_checkpoint_load("t", TABLE, "weekly") == {}
    assert w.batch_checkpoint_load("t2", TABLE, "daily") == {}
    w.batch_checkpoint_commit("t", TABLE, "daily", "20260902", "b2", 4)
    assert w.batch_checkpoint_load("t", TABLE, "daily") == {
        "20260901": "completed", "20260902": "completed"}


# ── I 幂等 ──────────────────────────────────────────────────────────────
def test_commit_idempotent_single_row(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV, "1")
    db, w = _mk(tmp_path, "idem")
    w.batch_checkpoint_commit("t", TABLE, "daily", "w1", "b1", 3)
    w.batch_checkpoint_commit("t", TABLE, "daily", "w1", "b2", 9)   # 同键重放
    rows = _bc_rows(db, "t", TABLE, "daily")
    assert len(rows) == 1, "同键重复 commit 必须只有一行"
    assert rows == [("w1", "completed", 9, "b2")], "重放应更新 rows_written/batch_id"


# ── F fail-closed 铁律（"断点可红"，越断点必须阻断跳过）──────────────────
def test_p10_f1_commit_write_failure_leaves_no_checkpoint(tmp_path, monkeypatch):
    """F1：写断点失败（INSERT 抛错）-> 无断点 -> 该窗口续跑必须重跑（不得跳过）。"""
    monkeypatch.setenv(ENV, "1")
    db, w = _mk(tmp_path, "f1")
    _install_spy(w, raise_on="INSERT INTO batch_checkpoint")
    try:
        ok = w.batch_checkpoint_commit("t", TABLE, "daily", "20260901", "b1", 5)
    finally:
        _uninstall_spy(w)
    assert ok is False, "写失败必须返回 False（不推进）"
    assert w.batch_checkpoint_load("t", TABLE, "daily") == {}
    assert _bc_rows(db, "t", TABLE, "daily") == []
    dr = _daemon()
    kept = dr.ResidentCollector._bc_filter_completed(
        _stub(w), "t", TABLE, "daily", ["20260901", "20260902"])
    assert kept == ["20260901", "20260902"], "越断点场景必须阻断跳过"


def test_p10_f2_readback_mismatch_is_red(tmp_path, monkeypatch):
    """F2：回读校验失败 -> commit=False 且撤销 completed 标记 -> 该窗口必须重跑。"""
    monkeypatch.setenv(ENV, "1")
    db, w = _mk(tmp_path, "f2")
    _install_spy(w, fake_back=("completed", 999))   # 伪造 rows_written 不一致
    try:
        ok = w.batch_checkpoint_commit("t", TABLE, "daily", "20260901", "b1", 5)
    finally:
        _uninstall_spy(w)
    assert ok is False, "回读校验失败必须返回 False"
    assert _bc_rows(db, "t", TABLE, "daily") == [], "补偿删除未清掉 completed 标记（fail-open）"
    assert w.batch_checkpoint_load("t", TABLE, "daily") == {}
    dr = _daemon()
    kept = dr.ResidentCollector._bc_filter_completed(
        _stub(w), "t", TABLE, "daily", ["20260901"])
    assert kept == ["20260901"], "越断点场景必须阻断跳过"


def test_p10_f3_only_completed_status_is_skipped(tmp_path, monkeypatch):
    """F3：仅 status=='completed' 可跳过；pending/缺失一律保留（越断点阻断）。"""
    monkeypatch.setenv(ENV, "1")
    db, w = _mk(tmp_path, "f3")
    w.batch_checkpoint_commit("t", TABLE, "daily", "ok_day", "b1", 3)
    _bc_raw_insert(db, "t", TABLE, "daily", "pending_day", "pending")
    _bc_raw_insert(db, "t", TABLE, "daily", "failed_day", "failed")
    dr = _daemon()
    kept = dr.ResidentCollector._bc_filter_completed(
        _stub(w), "t", TABLE, "daily",
        ["ok_day", "pending_day", "failed_day", "absent_day"])
    assert kept == ["pending_day", "failed_day", "absent_day"]


def test_p10_f3_load_exception_returns_all(tmp_path, monkeypatch):
    """F3：断点读取异常 -> 降级为无断点（全窗重跑），绝不误跳过。"""
    monkeypatch.setenv(ENV, "1")
    _, w = _mk(tmp_path, "f3b")

    def boom(*a, **k):
        raise RuntimeError("inject: load fail")

    w.batch_checkpoint_load = boom       # 注入读失败
    dr = _daemon()
    windows = ["a", "b"]
    kept = dr.ResidentCollector._bc_filter_completed(_stub(w), "t", TABLE, "daily", windows)
    assert kept == windows


# ── C 清理 ──────────────────────────────────────────────────────────────
def test_clear_only_target_key(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV, "1")
    db, w = _mk(tmp_path, "clr")
    w.batch_checkpoint_commit("t", TABLE, "daily", "d1", "b1", 1)
    w.batch_checkpoint_commit("t", TABLE, "daily", "d2", "b2", 2)
    w.batch_checkpoint_commit("t", TABLE, "weekly", "w1", "b3", 3)
    w.batch_checkpoint_clear("t", TABLE, "daily")
    assert w.batch_checkpoint_load("t", TABLE, "daily") == {}
    assert w.batch_checkpoint_load("t", TABLE, "weekly") == {"w1": "completed"}


# ── D daemon 接入面 ─────────────────────────────────────────────────────
def test_daemon_gate_off_passthrough_same_object(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    db, w = _mk(tmp_path, "d_off")
    dr = _daemon()
    windows = ["a", "b"]
    kept = dr.ResidentCollector._bc_filter_completed(_stub(w), "t", TABLE, "daily", windows)
    assert kept is windows, "gate 关闭必须原样返回同一对象（零回归）"
    dr.ResidentCollector._bc_commit(_stub(w), "t", TABLE, "daily", "b1", "a", 5)
    assert not _table_exists(db, BC_TABLE), "gate 关闭 _bc_commit 必须零副作用"


def test_daemon_commit_then_filter_skips_completed(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV, "1")
    _, w = _mk(tmp_path, "d_on")
    dr = _daemon()
    dr.ResidentCollector._bc_commit(_stub(w), "t", TABLE, "daily", "b1", "d1", 5)
    kept = dr.ResidentCollector._bc_filter_completed(
        _stub(w), "t", TABLE, "daily", ["d1", "d2"])
    assert kept == ["d2"], "已完成批应被跳过、未完成批保留"
