# -*- coding: utf-8 -*-
"""A1/A2（六步③·2026-10-22）：minute 表写冲突取证——登记表 + 失败槽 + 并发写者指认。

纯单元测试，不触生产库。覆盖：
  A1：主 upsert 路径的失败槽记录（W-4 定谳：该路径原无 record_write_failure）
  A2：同表同时段写者可被指认；无同表同段写者 ⇒ 跨进程候选（R6 口径）
"""
import os
import threading
import time

import pytest

from quantstudio.pipeline import writers as W


@pytest.fixture(autouse=True)
def _clean_registry():
    W._WRITER_REGISTRY.clear()
    W._LAST_WRITE_CTX.clear()
    W._LAST_WRITE_FAILURE.clear()
    yield
    W._WRITER_REGISTRY.clear()


def _conflict_exc(key="159512, 1789090200000, 1min"):
    return RuntimeError(
        'TransactionContext Error: Failed to commit: write-write conflict on key: "%s"' % key)


def test_a1_main_path_failure_ctx_recorded():
    """A1：note_write_context → 失败 → 失败槽为本次写自身（非 last 槽）。"""
    W.note_write_context(table="etf_minutes", batch_id="b1", path="_write_locked",
                         serial=True, pid=123, thread=456)
    W.price_write_exit("etf_minutes")      # 无登记亦须安全
    W.record_write_failure(_conflict_exc())
    ctx = W.last_write_failure()
    assert ctx["table"] == "etf_minutes"
    assert ctx["path"] == "_write_locked"
    assert ctx["serial"] is True
    assert ctx["conflict_key"] == "159512, 1789090200000, 1min"
    assert ctx["conflict_time"] == 1789090200000
    # 真实 pid/thread 覆盖 ctx 中的模拟值（取证须为真实标识）
    assert ctx["pid"] == os.getpid()
    assert ctx["thread"] == threading.get_ident()
    # 无同表同段写者 ⇒ 跨进程候选
    assert ctx.get("cross_process_candidate") is True
    assert ctx.get("peers_n") == 0


def test_a2_peer_identified_same_table_same_range():
    """A2：另一线程同表、时段覆盖 ⇒ 冲突时被指认。

    A1b 修正（2026-10-23）：判活改为「同进程按线程存活」后，**已结束线程**的活跃条目会被
    剪除（正确行为）。故本用例改为「peer 线程**存活期间**做冲突取证」——这才是 A2 的真实
    语义（并发写者被指认），而非「线程已死后仍被指认」。
    """
    _hold = threading.Event()

    def _peer():
        W.price_write_enter("etf_minutes", "qfq_reanchor", "ev1",
                            t_min=1789000000000, t_max=1789100000000)
        _hold.wait(timeout=10)          # 保持线程存活，模拟在程写者

    t = threading.Thread(target=_peer)
    t.start()
    time.sleep(0.1)
    try:
        W.note_write_context(table="etf_minutes", batch_id="b2", path="_write_locked", serial=True)
        W.record_write_failure(_conflict_exc())
        ctx = W.last_write_failure()
        assert ctx["peers_n"] == 1
        peer = ctx["peers"][0]
        assert peer["path"] == "qfq_reanchor"
        assert peer["pid"] == os.getpid()
        assert "cross_process_candidate" not in ctx
    finally:
        _hold.set()
        t.join(timeout=5)


def test_a2_peer_outside_range_not_identified():
    """A2 边界：同表但时段不覆盖 ⇒ 不计为并发写者（跨进程候选成立）。"""
    W.price_write_enter("etf_minutes", "qfq_reanchor", "ev2", t_min=1, t_max=2)
    W.note_write_context(table="etf_minutes", batch_id="b3", path="_write_locked", serial=True)
    W.record_write_failure(_conflict_exc("159512, 1789090200000, 1min"))
    ctx = W.last_write_failure()
    assert ctx["peers_n"] == 0
    assert ctx.get("cross_process_candidate") is True


def test_a1_registry_ttl_cleanup():
    """A1：结束条目过 TTL 后被清理（防登记表泄漏）。"""
    W.price_write_enter("etf_minutes", "p", "b")
    W.price_write_exit("etf_minutes")
    assert len(W._WRITER_REGISTRY) == 1
    for ent in W._WRITER_REGISTRY.values():
        ent["ended"] = 0.0
    W.concurrent_writers("etf_minutes")
    assert len(W._WRITER_REGISTRY) == 0


def test_a1_engine_peer_registration_visible():
    """A1：引擎登记（另一线程、无 df/时段）亦可被指认为同表写者。

    A1b 修正（2026-10-23）：同 a2 用例——peer 线程必须**存活**才在册（判活剪除死线程条目）。
    """
    _hold = threading.Event()

    def _peer():
        W.price_write_enter("etf_minutes", "qfq_reanchor", "ev3")
        _hold.wait(timeout=10)

    t = threading.Thread(target=_peer)
    t.start()
    time.sleep(0.1)
    try:
        W.note_write_context(table="etf_minutes", batch_id="b4", path="_write_passthrough", serial=True)
        W.record_write_failure(_conflict_exc())
        ctx = W.last_write_failure()
        assert ctx["peers_n"] == 1
        assert ctx["peers"][0]["path"] == "qfq_reanchor"
    finally:
        _hold.set()
        t.join(timeout=5)


# ── A3：锁覆盖断言（域限定 _SERIAL_WRITE_TABLES；fail-fast 可关）─────────────

def test_a3_assert_raises_without_lock():
    """A3：分钟表写入口未持锁 ⇒ fail-fast。"""
    with pytest.raises(RuntimeError, match="未持表锁"):
        W._assert_write_locked("etf_minutes", "unit-test")


def test_a3_assert_ok_with_lock():
    """A3：持锁时断言通过（正常路径零触发）。"""
    with W.table_write_lock("etf_minutes"):
        W._assert_write_locked("etf_minutes", "unit-test")


def test_a3_daily_table_not_affected():
    """A3 边界：daily 表不在 _SERIAL_WRITE_TABLES ⇒ 断言不触发（nullcontext 不误伤）。"""
    assert "stock_daily" not in W._SERIAL_WRITE_TABLES
    W._assert_write_locked("stock_daily", "unit-test")


def test_a3_switch_off(monkeypatch):
    """A3 回退面：QS_PRICE_WRITE_ASSERT=0 ⇒ 不触发。"""
    monkeypatch.setenv("QS_PRICE_WRITE_ASSERT", "0")
    W._assert_write_locked("etf_minutes", "unit-test")


# ── A4/A5：真库并发（锁生效 vs 强制重叠负向）─────────────────────────────

import contextlib  # noqa: E402
from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402


def _mk_writer(tmp_path):
    from quantstudio.pipeline.writers import DuckDBWriter
    return DuckDBWriter({"type": "duckdb", "path": str(Path(tmp_path) / "a4a5.db")})


def _mk_df(n_rows=200):
    return pd.DataFrame({
        "code": ["X"] * n_rows,
        "time": list(range(1, n_rows + 1)),
        "freq": ["1min"] * n_rows,
        "open": [1.0] * n_rows, "high": [1.0] * n_rows,
        "low": [1.0] * n_rows, "close": [1.0] * n_rows,
    })


def test_a4_locked_concurrent_writes_no_conflict(tmp_path):
    """A4：持锁并发写同表同 key ⇒ 串行成功，0 次并发异常。"""
    w = _mk_writer(tmp_path)
    df = _mk_df()
    errors = []

    def _w():
        try:
            w.write(df.copy(), "etf_minutes", "a4")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=_w) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == [], f"持锁并发写不应冲突：{errors[:2]}"


def test_a5_unlocked_overlap_reproduces_conflict(tmp_path, monkeypatch):
    """A5 负向对照：**重叠事务**同键并发写 ⇒ 必现冲突（DuckDB 冲突族）。

    机理说明（实证矩阵，2026-10-22）：单语句 upsert 由 DuckDB 内部串行，不会冲突；
    冲突需"两个事务同时改同一行"——正是实弹形态（QFQ 引擎长事务 × 任务写）。
    本测试用两条连接同时 UPDATE 同一行（无表锁串行），断言第二个写者**确定性**失败；
    与 A4（持锁串行 ⇒ 0 冲突）构成对照，证明表锁是有效因子。
    """
    import duckdb
    w = _mk_writer(tmp_path)          # 建库/建表/seed
    db = str(Path(tmp_path) / "a4a5.db")
    w.write(_mk_df(3), "etf_minutes", "seed")

    c1 = duckdb.connect(db)
    c2 = duckdb.connect(db)
    try:
        c1.execute("BEGIN")
        c1.execute("UPDATE etf_minutes SET close=9.0 WHERE code='X' AND time=1")
        c2.execute("BEGIN")
        with pytest.raises(Exception, match="(?i)conflict"):
            c2.execute("UPDATE etf_minutes SET close=8.0 WHERE code='X' AND time=1")
    finally:
        for c in (c1, c2):
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            c.close()


def test_a5_locked_guard_blocks_bypass_writer(tmp_path, monkeypatch):
    """A5 补充：绕过 table_write_lock 的同线程写 ⇒ A3 断言 fail-fast（无锁不静默）。"""
    w = _mk_writer(tmp_path)
    df = _mk_df(3)
    monkeypatch.setattr(W, "table_write_lock", lambda t: contextlib.nullcontext())
    with pytest.raises(RuntimeError, match="未持表锁"):
        w.write(df, "etf_minutes", "a5-bypass")

# ── A6：有界冲突重试（整操作重放；默认 3 次；QS_WRITE_RETRY_MAX=0 关闭）────────

def _mk_a6_writer(tmp_path, monkeypatch):
    w = _mk_writer(tmp_path)
    # 隔离 3A 文件写锁（生产 daemon 在跑，测试不得争锁）
    monkeypatch.setattr(W, "ensure_write_lock", lambda *a, **k: None)
    monkeypatch.setattr(W, "release_write_lock", lambda *a, **k: None)
    monkeypatch.setattr(W, "_retry_backoff", lambda n: None)   # 测试免等待
    W._WRITE_RETRY_STATS.update({"attempts": 0, "exhausted": 0, "success": 0})
    return w


def test_a6_retry_success_once(tmp_path, monkeypatch):
    """A6：首次冲突 → 重试一次成功（重放同一批，幂等）。"""
    w = _mk_a6_writer(tmp_path, monkeypatch)
    calls = {"n": 0}
    _orig = w._write_locked

    def _flaky(df, table, batch_id, passthrough=False):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError('TransactionContext Error: Failed to commit: write-write conflict on key: "X, 1, 1min"')
        return 100

    monkeypatch.setattr(w, "_write_locked", _flaky)
    n = w.write(_mk_df(3), "etf_minutes", "a6-ok")
    assert n == 100 and calls["n"] == 2
    assert W._WRITE_RETRY_STATS["attempts"] >= 1


def test_a6_retry_exhausted(tmp_path, monkeypatch):
    """A6：持续冲突 → 有界耗尽后如实抛出（不无限重试）。"""
    w = _mk_a6_writer(tmp_path, monkeypatch)
    monkeypatch.setenv("QS_WRITE_RETRY_MAX", "2")
    calls = {"n": 0}

    def _always(df, table, batch_id, passthrough=False):
        calls["n"] += 1
        raise RuntimeError("Failed to commit: write-write conflict on key: \"X, 1, 1min\"")

    monkeypatch.setattr(w, "_write_locked", _always)
    with pytest.raises(RuntimeError, match="write-write conflict"):
        w.write(_mk_df(3), "etf_minutes", "a6-fail")
    assert calls["n"] == 3            # 1 次首发 + 2 次重试
    assert W._WRITE_RETRY_STATS["exhausted"] >= 1


def test_a6_non_conflict_no_retry(tmp_path, monkeypatch):
    """A6：非冲突异常不重试（立即抛出）。"""
    w = _mk_a6_writer(tmp_path, monkeypatch)
    calls = {"n": 0}

    def _boom(df, table, batch_id, passthrough=False):
        calls["n"] += 1
        raise ValueError("not a conflict")

    monkeypatch.setattr(w, "_write_locked", _boom)
    with pytest.raises(ValueError):
        w.write(_mk_df(3), "etf_minutes", "a6-boom")
    assert calls["n"] == 1


def test_a6_switch_off(tmp_path, monkeypatch):
    """A6 回退面：QS_WRITE_RETRY_MAX=0 ⇒ 不重试（逐位旧行为）。"""
    w = _mk_a6_writer(tmp_path, monkeypatch)
    monkeypatch.setenv("QS_WRITE_RETRY_MAX", "0")
    calls = {"n": 0}

    def _always(df, table, batch_id, passthrough=False):
        calls["n"] += 1
        raise RuntimeError("write-write conflict on key: \"X, 1, 1min\"")

    monkeypatch.setattr(w, "_write_locked", _always)
    with pytest.raises(RuntimeError):
        w.write(_mk_df(3), "etf_minutes", "a6-off")
    assert calls["n"] == 1


def test_a6_conflict_classifier():
    """A6：冲突族判定（write-write / Conflict on update / 非冲突）。"""
    assert W.is_write_conflict(RuntimeError('write-write conflict on key: "X, 1, 1min"'))
    assert W.is_write_conflict(RuntimeError("TransactionContext Error: Conflict on update!"))
    assert not W.is_write_conflict(ValueError("boom"))

# ── A7：幂等（重试重放不产生重复行）────────────────────────────────────────

def test_a7_retry_after_write_is_idempotent(tmp_path, monkeypatch):
    """A7：首次写已落库后抛冲突（模拟 commit 期冲突）→ 重试重放同一批 ⇒ 行数不重复。"""
    w = _mk_a6_writer(tmp_path, monkeypatch)
    df = _mk_df(50)
    calls = {"n": 0}
    _real = w._write_locked

    def _write_then_conflict(df_, table, batch_id, passthrough=False):
        calls["n"] += 1
        n = _real(df_, table, batch_id, passthrough)
        if calls["n"] == 1:
            raise RuntimeError('TransactionContext Error: Failed to commit: '
                               'write-write conflict on key: "X, 1, 1min"')
        return n

    monkeypatch.setattr(w, "_write_locked", _write_then_conflict)
    assert w.write(df, "etf_minutes", "a7") == 50
    assert calls["n"] == 2
    con = w._duckdb.connect(str(Path(tmp_path) / "a4a5.db"))
    try:
        cnt = con.execute("SELECT count(*) FROM etf_minutes").fetchone()[0]
        dup = con.execute(
            "SELECT count(*) FROM (SELECT code, time, freq, count(*) c FROM etf_minutes "
            "GROUP BY 1,2,3 HAVING c > 1)").fetchone()[0]
    finally:
        con.close()
    assert cnt == 50, f"重试后行数应仍为 50（实际 {cnt}）"
    assert dup == 0, "重试不得产生重复主键行"


def test_a7_repeat_write_idempotent(tmp_path, monkeypatch):
    """A7：同批重复写（两次全量）⇒ 行数不增（upsert 幂等）。"""
    w = _mk_a6_writer(tmp_path, monkeypatch)
    df = _mk_df(30)
    w.write(df, "etf_minutes", "a7-2")
    w.write(df, "etf_minutes", "a7-2")
    con = w._duckdb.connect(str(Path(tmp_path) / "a4a5.db"))
    try:
        cnt = con.execute("SELECT count(*) FROM etf_minutes").fetchone()[0]
    finally:
        con.close()
    assert cnt == 30
