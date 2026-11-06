# -*- coding: utf-8 -*-
"""软锁自锁死修复（A+C+D+E+F）生命周期验收用例（2026-11-04）。

规格：docs/handoff/softlock-ef-repair-spec-20261104.md（T2/T3/T4 门控 + V6/V7 追加判据）。
覆盖：
  - S1 基线：owner + ensure×N + release×N + owner.release() ⇒ 文件消失；_depth 恒 0/1；
  - S2 错序释放两形态（owner 先走 / ensure 栈序破坏）⇒ 无残留、无计数漂移；
  - S3 ensure/release 正序配对嵌套回归（正常路径不退化）；
  - 交错 100 轮混合直接句柄/ensure/release_write_lock（种子固定，可复现）；
  - C 同进程自愈（S4 泄漏态 ⇒ 自愈 + 审计 self_pid_lost_handle）
    + 负向（pid≠self / create_time 不匹配 ⇒ 不得回收）；
  - D 并发 4 线程×200 混用直接句柄与 ensure（60s 看门狗；零 WriteLockHeld；
    终态无锁文件、_depth==0；健康并发不得触发同进程自愈）；
  - V6 非 OSError 注入（MemoryError/KeyboardInterrupt）⇒ 锁文件不残留（E 生效）；
  - V7 手工 0 字节锁：mtime 超龄 ⇒ 可回收 + 审计 predicate=empty_mtime_stale；
    未超龄 ⇒ 不回收（防误删初始化中锁）。

G1-G4 E 项盲区补强（2026-11-05，docs/design-softlock-g1g4-e-blindspot-20261105.md）：
  - G1 payload 写入 OSError ⇒ WARNING（errno/类型/路径）+ 原样上抛 + 无 0 字节残留；
  - G2 _recreate 失败 ⇒ 清除本次创建文件（ino 双重限定）+ 负向：他人锁不误删；
  - G3 release unlink PermissionError ⇒ WARNING + 有界重试（成功/耗尽两态）；
  - G4 同进程并发 acquire（入口检查后、循环时已持锁的确定性时序）⇒ 浅句柄放行
    不抛 WriteLockHeld；跨进程（subprocess 双进程）互斥仍生效（守护负向）。

负向对照（A·规格 T2 门）＝临时回退 A（恢复 `_depth += 1`/`-= 1` 计数语义）后重跑
本文件：S1/S2 组必红（_depth 断言与「owner 释放即文件消失」断言双判据）——证明修在 A。
该回退动作属验收期人工步骤，不在本文件内自动化。

隔离三件与 tests/test_snapshot_lock.py 同款（QS_WRITE_LOCK_DIR / QS_WRITE_LOCK_AUDIT_LOG /
用例前后状态复位）——零碰生产 data/snapshots/。
"""
import errno
import json
import logging
import os
import random
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import quantstudio.pipeline.snapshot_lock as sl
from quantstudio.pipeline.snapshot_lock import (
    WriteLockHeld, acquire_write_lock, lock_path, read_holder)

psutil = pytest.importorskip("psutil")


@pytest.fixture(autouse=True)
def _clean_lock(tmp_path, monkeypatch):
    # 隔离三件：① 锁目录重定向 tmp（零碰生产 data/snapshots）；
    #          ② 回收审计重定向 tmp；③ 清同进程私有状态（防跨用例级联）。
    monkeypatch.setenv("QS_WRITE_LOCK_DIR", str(tmp_path / "lockdir"))

    def _reset():
        sl._depth = 0
        sl._current_lock = None
        sl._ensure_handles.clear()
        sl._live_handles.clear()
        lock_path().unlink(missing_ok=True)
        sl._reclaim_path().unlink(missing_ok=True)

    _reset()
    monkeypatch.setenv(sl.AUDIT_LOG_ENV, str(tmp_path / "write_lock_reclaim.log"))
    monkeypatch.delenv(sl.SELFHEAL_ENV, raising=False)
    yield
    _reset()


def _audit_lines():
    p = sl._audit_path()
    if not p.exists():
        return []
    return [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _write_payload(payload):
    lock_path().write_text(json.dumps(payload), encoding="utf-8")


def _assert_fully_released():
    """终态判据（A）：无锁文件、_depth==0、无 current、两表全空。"""
    assert not lock_path().exists(), "终态不得残留锁文件"
    assert sl._depth == 0, f"终态 _depth 必须 0（A：0/1 语义），实测 {sl._depth}"
    assert sl._current_lock is None
    assert not sl._live_handles and not sl._ensure_handles


# --------------------------------------------------------------------------
# T2 / S1-S3 + 交错 100：A 浅句柄不计数（0/1 语义）生命周期
# --------------------------------------------------------------------------
def test_S1_owner_ensure_mix_releases_clean():
    """S1 基线（T2 判据原文）：owner+ensure×N+release×N+owner.release() ⇒ 文件消失；
    全程 _depth 恒 0/1。"""
    lk = acquire_write_lock("S1-owner", timeout_s=2.0)
    assert sl._depth == 1 and lock_path().exists()
    for i in range(5):
        sl.ensure_write_lock(f"S1-ensure-{i}")
        assert sl._depth == 1, f"第 {i} 层 ensure 后 _depth 必须 1（浅句柄不计数）"
    for i in range(5):
        sl.release_write_lock()
        assert sl._depth == 1 and lock_path().exists(), "浅层释放不得动文件锁"
    lk.release()
    _assert_fully_released()


def test_S2_out_of_order_owner_first_no_residue():
    """S2 形态①（错序释放）：owner 先 release、浅句柄后 release——旧计数语义下
    _current_lock 残留指向已释放句柄（本用例对其断言即负向判别点）。"""
    outer = acquire_write_lock("S2-outer", timeout_s=2.0)
    inner = acquire_write_lock("S2-inner", timeout_s=0.5)
    assert inner._shallow is True
    outer.release()                       # 错序：owner 先走
    assert not lock_path().exists() and sl._depth == 0
    inner.release()                       # 浅句柄后释放：空操作，不得复活任何状态
    _assert_fully_released()


def test_S2_stack_order_violation_no_residue():
    """S2 形态②（栈序破坏）：ensure(owner 入栈) 后先经 release_write_lock 弹出 owner
    ——A 后「owner 释放即文件消失」立即成立；旧计数语义下该时点文件仍残留且
    _depth=1（若用户侧浅句柄因异常未释放即成永久泄漏）。"""
    sl.ensure_write_lock("S2b-e1")        # owner，入栈
    h = acquire_write_lock("S2b-direct", timeout_s=0.5)
    assert h._shallow is True
    sl.release_write_lock()               # 弹出的是 owner（栈内仅一条）
    assert not lock_path().exists(), "owner 出栈释放后文件必须立即消失（A）"
    assert sl._depth == 0
    h.release()                           # 浅句柄兜底释放：空操作
    _assert_fully_released()


def test_S3_nested_ensure_pairs_clean():
    """S3 正序配对回归：1/2/5 层嵌套 ensure/release 全配对 ⇒ 每层文件状态正确。"""
    for n in (1, 2, 5):
        for i in range(n):
            sl.ensure_write_lock(f"S3-{n}-{i}")
            assert sl._depth == 1 and lock_path().exists()
        for _ in range(n):
            sl.release_write_lock()
        assert sl._depth == 0 and not lock_path().exists(), f"{n} 层嵌套后必须干净"
    _assert_fully_released()


def test_interleaved_100_mixed_ops_depth_0_1():
    """交错 100 轮（种子固定可复现）：直接句柄/ensure/release_write_lock/浅句柄
    混合乱序——每步 _depth ∈ {0,1} 且文件存在性 == 持有态；终态完全干净。"""
    rng = random.Random(20261104)
    held = []
    for i in range(100):
        op = rng.randrange(4)
        if op == 0:
            sl.ensure_write_lock(f"ix-ensure-{i}")
        elif op == 1:
            sl.release_write_lock()
        elif op == 2:
            h = acquire_write_lock(f"ix-direct-{i}", timeout_s=2.0, poll_s=0.02)
            held.append(h)
            if rng.random() < 0.5:
                h.release()
        else:
            if held and rng.random() < 0.7:
                held.pop(rng.randrange(len(held))).release()
        assert sl._depth in (0, 1), f"步 {i}（op={op}）：_depth 必须 0/1，实测 {sl._depth}"
        assert lock_path().exists() == (sl._depth > 0), f"步 {i}（op={op}）：文件/持有态脱钩"
    while sl._ensure_handles:
        sl.release_write_lock()
    for h in held:
        h.release()
    _assert_fully_released()


# --------------------------------------------------------------------------
# T3 / C：同进程自愈（三重条件齐备才回收，防误删）+ 负向对照 ×2
# --------------------------------------------------------------------------
def _self_payload(pid=None, pid_create_time=None, heartbeat=None, task="C-holder"):
    """构造 v2 自证 payload（默认归本进程、心跳新鲜——C 判据不依赖心跳年龄）。"""
    return {"v": 2,
            "pid": os.getpid() if pid is None else pid,
            "task_id": task,
            "heartbeat": time.time() if heartbeat is None else heartbeat,
            "host": socket.gethostname(),
            "pid_create_time": (sl._self_create_time() if pid_create_time is None
                                else pid_create_time),
            "token": "tok-self"}


def test_S4_self_heal_recovers_and_audits():
    """T3 正向：S4 泄漏态（payload 归本进程但状态已丢）⇒ acquire 自愈成功 +
    write_lock_reclaim.log 有 self_pid_lost_handle；心跳新鲜亦自愈（C 不看心跳）。"""
    assert sl._self_create_time() is not None, "psutil 不可用：无法构造双证 payload"
    _write_payload(_self_payload(task="S4-lost"))   # fixture 已复位状态 ⇒ 丢句柄态
    t0 = time.time()
    lk = acquire_write_lock("S4-heal", timeout_s=5.0)
    try:
        assert time.time() - t0 < 2.0, "自愈应在首个轮询内完成（无等待）"
        assert read_holder()["task_id"] == "S4-heal"
        assert any("self_pid_lost_handle" in ln for ln in _audit_lines()), _audit_lines()
    finally:
        lk.release()
    _assert_fully_released()


def test_C_negative_wrong_pid_not_reclaimed():
    """T3 负向①：pid≠self（哪怕 create_time/心跳均「正常」）⇒ 不得走同进程自愈。"""
    _write_payload(_self_payload(pid=os.getpid() + 1, task="C-other"))
    with pytest.raises(WriteLockHeld):
        acquire_write_lock("C-neg-1", timeout_s=0.6, poll_s=0.1)
    assert lock_path().exists(), "pid≠self 的锁不得被同进程自愈回收"
    assert not any("self_pid_lost_handle" in ln for ln in _audit_lines())


def test_C_negative_create_time_mismatch_not_reclaimed():
    """T3 负向②：pid==self 但 create_time 不匹配（疑 PID 复用）⇒ 不得自愈回收。"""
    ct = sl._self_create_time()
    if ct is None:
        pytest.skip("psutil 不可用：create_time 恒 None==None，无法构造不匹配")
    _write_payload(_self_payload(pid_create_time=ct - 12345.0, task="C-reused"))
    with pytest.raises(WriteLockHeld):
        acquire_write_lock("C-neg-2", timeout_s=0.6, poll_s=0.1)
    assert lock_path().exists(), "create_time 不匹配的锁不得被同进程自愈回收"
    assert not any("self_pid_lost_handle" in ln for ln in _audit_lines())


# --------------------------------------------------------------------------
# T4 / D：并发 4 线程×200 混用直接句柄与 ensure（60s 看门狗）
# --------------------------------------------------------------------------
def test_D_concurrent_4x200_mixed_no_deadlock_clean():
    """T4：4 线程×200 acquire/release + 混用 ensure/release_write_lock ⇒
    终态无锁文件、零 WriteLockHeld、_depth==0；60s 看门狗内完成（无死锁）；
    健康并发不得触发同进程自愈（触发即 D 状态竞争缺陷）。"""
    errors = []
    errors_lock = threading.Lock()

    def worker(tid):
        try:
            for i in range(200):
                lk = acquire_write_lock(f"D{tid}-{i}", timeout_s=10.0, poll_s=0.02)
                try:
                    assert sl._depth in (0, 1), f"_depth 越界: {sl._depth}"
                finally:
                    lk.release()
                if i % 3 == 0:
                    sl.ensure_write_lock(f"E{tid}-{i}")
                    sl.release_write_lock()
        except BaseException as e:        # 记录一切：WriteLockHeld/断言/意外异常
            with errors_lock:
                errors.append((tid, repr(e)))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)                # 60s 看门狗
    elapsed = time.time() - t0
    assert not any(t.is_alive() for t in threads), f"60s 看门狗触发：疑似死锁（{elapsed:.1f}s）"
    assert errors == [], f"并发期出现异常: {errors[:3]}"
    _assert_fully_released()
    assert not any("self_pid_lost_handle" in ln for ln in _audit_lines()), \
        f"健康并发不得触发同进程自愈: {_audit_lines()}"


# --------------------------------------------------------------------------
# V6 / E：非 OSError 注入 ⇒ 锁文件不残留
# --------------------------------------------------------------------------
@pytest.mark.parametrize("exc", [MemoryError, KeyboardInterrupt],
                         ids=["memory-error", "keyboard-interrupt"])
def test_V6_non_oserror_payload_failure_no_residue(monkeypatch, exc):
    """V6（T2 场景）：os.open 成功后 payload 写入段注入非 OSError 异常 ⇒
    异常原样上抛 + 0 字节锁不残留 + 状态干净；异常后无需自愈即可再次获取。"""
    def boom(self):
        raise exc("注入：初始化段非 OSError 异常（客户形态3 模拟）")

    monkeypatch.setattr(sl.WriteLock, "_write_payload", boom)
    with pytest.raises(exc):
        acquire_write_lock("V6", timeout_s=2.0)
    assert not lock_path().exists(), "E：非 OSError 初始化失败不得残留 0 字节锁"
    assert sl._depth == 0 and sl._current_lock is None and not sl._live_handles
    monkeypatch.undo()                    # 撤销注入后再验证可正常获取
    lk = acquire_write_lock("V6-retry", timeout_s=2.0)
    lk.release()
    _assert_fully_released()


# --------------------------------------------------------------------------
# V7 / F：手工 0 字节锁——mtime 超龄回收 / 未超龄不回收
# --------------------------------------------------------------------------
def test_V7_empty_lock_aged_mtime_reclaimed_and_audited():
    """V7 正向：手工 0 字节锁 + mtime 超龄 ⇒ acquire 经既有 CAS+审计通道回收 +
    审计行 predicate=empty_mtime_stale。"""
    lock_path().write_text("", encoding="utf-8")     # 0 字节（形态3 现场）
    t = time.time() - (sl.EMPTY_LOCK_MTIME_SECONDS + 5)
    os.utime(lock_path(), (t, t))                    # mtime 回拨超龄
    lk = acquire_write_lock("V7-aged", timeout_s=5.0, poll_s=0.1)
    try:
        assert read_holder()["task_id"] == "V7-aged"
        assert any("predicate=empty_mtime_stale" in ln for ln in _audit_lines()), \
            _audit_lines()
    finally:
        lk.release()
    _assert_fully_released()


def test_V7_empty_lock_fresh_mtime_not_reclaimed():
    """V7 负向：0 字节锁未超龄（疑「正在初始化」）⇒ 不回收（fail-closed），
    判定语含「空锁×年龄」明确结论。"""
    lock_path().write_text("", encoding="utf-8")
    with pytest.raises(WriteLockHeld) as ei:
        acquire_write_lock("V7-fresh", timeout_s=0.6, poll_s=0.1)
    assert ei.value.diagnosis.get("verdict") == "undecidable"
    assert "空锁" in (ei.value.diagnosis.get("detail") or "")
    assert lock_path().exists(), "未超龄空锁不得回收（防误删初始化中锁）"
    assert _audit_lines() == []


# --------------------------------------------------------------------------
# G1/G2/G3（2026-11-05 E 项盲区①②③）：OSError 注入——V6 补强三例 + 防误删/有界
# --------------------------------------------------------------------------
_LOCK_LOGGER = "quantstudio.pipeline.snapshot_lock"


def test_G1_payload_oserror_warns_no_residue_then_reacquire(monkeypatch, caplog):
    """V6 补①（G1）：payload 写入段注入 OSError ⇒ WARNING（errno/类型/路径）+
    原样上抛 + 空锁文件不残留（不再「静默吞掉继续持锁」）；撤销注入后可正常获取。"""
    def boom(self):
        raise OSError(errno.ENOSPC, "注入：payload 写盘失败（模拟磁盘满）")

    with caplog.at_level(logging.WARNING, logger=_LOCK_LOGGER):
        monkeypatch.setattr(sl.WriteLock, "_write_payload", boom)
        with pytest.raises(OSError):
            acquire_write_lock("G1", timeout_s=2.0)
    assert not lock_path().exists(), "G1：payload 写失败不得残留 0 字节锁"
    assert sl._depth == 0 and sl._current_lock is None and not sl._live_handles
    warns = [r.getMessage() for r in caplog.records
             if r.levelno >= logging.WARNING and "payload 写入失败" in r.getMessage()]
    assert warns, f"G1：必须留 WARNING 留痕: {[r.getMessage() for r in caplog.records]}"
    msg = warns[0]
    assert str(errno.ENOSPC) in msg and "OSError" in msg and str(lock_path()) in msg, msg
    monkeypatch.undo()                    # 撤销注入后再验证可正常获取
    lk = acquire_write_lock("G1-retry", timeout_s=2.0)
    lk.release()
    _assert_fully_released()


def test_G2_recreate_oserror_no_residue(monkeypatch, caplog):
    """V6 补②（G2）：_recreate 建出后写 payload 注入 OSError ⇒ 返回 False +
    WARNING 留痕 + 本次创建的空锁文件被清除（无 0 字节残留）。"""
    lk = sl.WriteLock(task_id="G2", _path=lock_path(), token="tok-g2")

    def boom(self):
        raise OSError(errno.ENOSPC, "注入：recreate payload 写盘失败")

    with caplog.at_level(logging.WARNING, logger=_LOCK_LOGGER):
        monkeypatch.setattr(sl.WriteLock, "_write_payload", boom)
        assert lk._recreate() is False
    assert not lock_path().exists(), "G2：_recreate 失败不得残留 0 字节文件"
    assert any(r.levelno >= logging.WARNING and "_recreate 写 payload 失败" in r.getMessage()
               for r in caplog.records), [r.getMessage() for r in caplog.records]


def test_G2_recreate_failure_never_deletes_foreign_lock(monkeypatch):
    """G2 负向（禁误删他人锁）：_recreate 建出后、payload 写入前，锁文件被他人
    删除重建（外部替换）⇒ ino 双重限定生效——失败清理放弃，他人锁原样保留。"""
    lk = sl.WriteLock(task_id="G2n", _path=lock_path(), token="tok-g2n")

    def replace_then_boom(self):
        lock_path().unlink(missing_ok=True)      # 模拟外部删除本次建出的空文件
        _write_payload({"v": 2, "pid": os.getpid() + 1, "task_id": "G2-foreign",
                        "heartbeat": time.time(), "host": socket.gethostname(),
                        "pid_create_time": None, "token": "tok-foreign"})
        raise OSError(errno.EIO, "注入：写入失败（文件已被外部替换）")

    monkeypatch.setattr(sl.WriteLock, "_write_payload", replace_then_boom)
    assert lk._recreate() is False
    cur = read_holder()
    assert cur and cur.get("task_id") == "G2-foreign", "G2 负向：他人锁不得被误删"


def test_G3_release_unlink_failure_warns_and_retries(monkeypatch, caplog):
    """V6 补③（G3）：release unlink 注入 PermissionError（前 2 次）⇒ 不抛 +
    WARNING 留痕（errno/路径/持有者）+ 有界重试生效（第 3 次成功删锁）。"""
    lk = acquire_write_lock("G3", timeout_s=2.0)
    assert lock_path().exists()
    real_unlink = Path.unlink
    calls = []

    def flaky_unlink(self, *a, **kw):
        calls.append(str(self))
        if len(calls) <= 2:
            raise PermissionError(errno.EACCES, "注入：拒绝访问（模拟被占用）")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    with caplog.at_level(logging.WARNING, logger=_LOCK_LOGGER):
        lk.release()                        # 不得抛（release 契约不变）
    monkeypatch.undo()                      # 先撤销注入，保障 fixture teardown 清理可用
    assert sl._depth == 0 and sl._current_lock is None
    assert len(calls) == 3, f"应有界重试至成功（实测 {len(calls)} 次）: {calls}"
    assert not lock_path().exists(), "重试成功后锁文件必须删除"
    warns = [r.getMessage() for r in caplog.records
             if r.levelno >= logging.WARNING and "release 删除锁文件失败" in r.getMessage()]
    assert warns and ("errno=13" in warns[0] or str(errno.EACCES) in warns[0]) \
               and ".write_lock" in warns[0], \
        [r.getMessage() for r in caplog.records]
    _assert_fully_released()


def test_G3_release_unlink_retry_bounded_when_exhausted(monkeypatch, caplog):
    """G3 有界性：unlink 恒失败 ⇒ 总尝试恰 1+RELEASE_UNLINK_RETRIES 次；重试耗尽
    只留 WARNING 不抛；残留文件可见（僵尸锁可观察，交由 F 超龄回收/人工处置）。"""
    lk = acquire_write_lock("G3x", timeout_s=2.0)
    calls = []

    def always_fail(self, *a, **kw):
        calls.append(str(self))
        raise PermissionError(errno.EACCES, "注入：拒绝访问（恒失败）")

    monkeypatch.setattr(Path, "unlink", always_fail)
    with caplog.at_level(logging.WARNING, logger=_LOCK_LOGGER):
        lk.release()                        # 耗尽仍不得抛
    monkeypatch.undo()
    assert sl.RELEASE_UNLINK_RETRIES == 3 and sl.RELEASE_UNLINK_RETRY_INTERVAL_S == 0.05
    assert len(calls) == 1 + sl.RELEASE_UNLINK_RETRIES, \
        f"重试必须有界（总尝试应恰 {1 + sl.RELEASE_UNLINK_RETRIES} 次，实测 {len(calls)}）"
    lock_path().unlink(missing_ok=True)     # 手工清注入期遗留的僵尸文件
    warns = [r.getMessage() for r in caplog.records
             if r.levelno >= logging.WARNING and "重试耗尽" in r.getMessage()]
    assert warns, f"G3：耗尽必须留 WARNING: {[r.getMessage() for r in caplog.records]}"
    _assert_fully_released()


# --------------------------------------------------------------------------
# G4（2026-11-05 E 项盲区④）：同进程并发 acquire 重入放行 + 跨进程互斥守护
# --------------------------------------------------------------------------
def test_G4_same_process_concurrent_acquire_no_write_lock_held(monkeypatch):
    """G4 正向：构造「线程 B 入口检查时未持锁（_depth==0）、进入等待循环时本进程
    已持锁」的确定性并发窗口（lock_path 门控强制时序）⇒ B 按浅句柄语义放行，
    不等待不抛 WriteLockHeld，且不得走 C 自愈（本进程持锁非丢句柄）。"""
    entered = threading.Event()
    holder_ready = threading.Event()
    real_lock_path = sl.lock_path

    def gated_lock_path():
        # 仅拦第二线程：首次到达等待循环即报 entered（此刻必已通过入口检查且
        # _depth==0），并阻塞至主线程持锁——确定性构造 G4 目标时序。
        if threading.current_thread().name == "G4-second":
            entered.set()
            holder_ready.wait(timeout=10)
        return real_lock_path()

    monkeypatch.setattr(sl, "lock_path", gated_lock_path)

    results = {}

    def second_thread():
        try:
            lk2 = acquire_write_lock("G4-second", timeout_s=8.0, poll_s=0.05)
            results["shallow"] = bool(lk2._shallow)
            lk2.release()
        except BaseException as e:
            results["exc"] = repr(e)

    t = threading.Thread(target=second_thread, name="G4-second")
    t.start()
    assert entered.wait(timeout=10), "第二线程未到达等待循环"
    owner = acquire_write_lock("G4-owner", timeout_s=2.0)   # 主线程此刻才持锁
    holder_ready.set()                     # 放行 B：O_EXCL 必 FileExistsError → G4
    t.join(timeout=15)
    monkeypatch.undo()
    assert not t.is_alive(), "第二线程卡死：G4 放行未生效"
    assert "exc" not in results, f"同进程并发不得抛 WriteLockHeld/其它异常: {results}"
    assert results.get("shallow") is True, results
    owner.release()
    _assert_fully_released()
    assert not any("self_pid_lost_handle" in ln for ln in _audit_lines()), \
        f"G4 放行不得走 C 自愈（本进程持锁非丢句柄）: {_audit_lines()}"


_G4_NEG_CHILD = """
from quantstudio.pipeline.snapshot_lock import acquire_write_lock, WriteLockHeld
try:
    lk = acquire_write_lock('G4-neg-child', timeout_s=1.5, poll_s=0.1)
except WriteLockHeld as e:
    assert e.holder.get('task_id') == 'G4-neg-owner', e.holder
    print('G4NEG-HELD-OK')
else:
    lk.release()
    print('G4NEG-BUG-ACQUIRED')
"""


def test_G4_cross_process_mutex_still_enforced():
    """G4 守护负向（跨进程互斥绝不放宽）：本进程持锁时，另一**进程**（subprocess）
    并发 acquire 仍须在超时后抛 WriteLockHeld——G4 重入放行仅限同进程。"""
    owner = acquire_write_lock("G4-neg-owner", timeout_s=2.0)
    try:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")   # 调用时构造（继承重定向）
        r = subprocess.run([sys.executable, "-c", _G4_NEG_CHILD],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=str(ROOT), env=env, timeout=60)
        assert "G4NEG-HELD-OK" in r.stdout, \
            f"跨进程互斥被放宽（G4 误放行）: rc={r.returncode} out={r.stdout} err={r.stderr}"
        assert "G4NEG-BUG-ACQUIRED" not in r.stdout
        assert r.returncode == 0, r.stderr
        assert lock_path().exists(), "子进程不得回收本进程（存活）持有的锁"
    finally:
        owner.release()
    _assert_fully_released()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
