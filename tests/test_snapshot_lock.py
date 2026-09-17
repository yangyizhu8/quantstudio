# -*- coding: utf-8 -*-
"""3A 写锁模块单测（原契约）+ 批一「死亡自愈」验收契约（2026-09-17）

契约分两层：
  A. 原有契约（互斥 / 重入 / 心跳 / 释放后再获取 / CLI 包裹透传）——不得退化；
  B. 批一新增契约（陈锁回收判据 / CAS 竞态 / 所有权校验 / legacy 回放）。

硬门用例（与 docs/write-lock-selfheal-design.md §八 对齐）：
  - AC5  test_reclaim_race_eight_processes          （八进程并发回收，恰一获锁）
  - AC-replay test_replay_customer_payloads[客户A/客户B]（两客户真实 payload 回放）

隔离（三件，缺一即碰生产）：
  ① 锁目录经 `QS_WRITE_LOCK_DIR` 重定向到本用例 tmp（会话级兜底见 tests/conftest.py
     `_isolate_write_lock_dir`）——**零碰生产 `data/snapshots/`**；
  ② 回收审计经 `QS_WRITE_LOCK_AUDIT_LOG` 重定向到 tmp_path（子进程经 `_child_env()` 继承）；
  ③ 用例前后清同进程重入深度/句柄表，防跨用例级联。
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import quantstudio.pipeline.snapshot_lock as sl
from quantstudio.pipeline.snapshot_lock import (
    WriteLockHeld, WriteLockLost, acquire_write_lock, assert_lock_owner,
    lock_path, read_holder)

psutil = pytest.importorskip("psutil")

# 两客户现场真实残留 payload（legacy 格式：仅 pid/task_id/heartbeat）
CUST_A = {"pid": 26168,
          "task_id": "writers:write:stock_minutes:mcp_stock_minutes_mcp_20260912_134759_f193d5",
          "heartbeat": 1789226968.5837445}
CUST_B = {"pid": 19968,
          "task_id": "writers:write:etf_minutes:mcp_etf_minutes_mcp_20260916_210701_98a344",
          "heartbeat": 1789586002.7676578}
CUST_LOG_EPOCH = {"A": 1789576004.0, "B": 1789626498.0}  # 观测时刻（UTC 秒），仅用于年龄断言


@pytest.fixture(autouse=True)
def _clean_lock(tmp_path, monkeypatch):
    # 隔离（三件）：① 锁目录重定向到 tmp（**零碰生产 data/snapshots**）；
    #             ② 回收审计重定向到 tmp；③ 清同进程重入深度/句柄表（防跨用例级联）。
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


def _aged(payload, age_s):
    d = dict(payload)
    d["heartbeat"] = time.time() - age_s
    return d


def _free_pid(hint=None):
    """返回一个当前不存在的 pid（优先使用 hint，用于真实 payload 回放）。"""
    if hint is not None and not psutil.pid_exists(int(hint)):
        return int(hint)
    for pid in range(4_000_000, 3_999_000, -1):
        if not psutil.pid_exists(pid):
            return pid
    pytest.skip("找不到可用的不存在 pid")


# 子进程统一 UTF-8 IO + 宽容解码：控制台/管道 GBK 与 UTF-8 混用时不再让 reader 线程炸掉
# （中文报错文本经管道传输时的编码判定依环境变量而变，属测试脚手架问题，非生产语义）
def _child_env():
    """子进程环境：UTF-8 IO + 继承当前（含 monkeypatch 注入的）环境变量。

    注意：必须在调用时构造——模块级常量会冻结 import 时的 os.environ，
    导致 monkeypatch.setenv（如 QS_WRITE_LOCK_AUDIT_LOG 重定向）传不到子进程，
    子进程便会把回收审计写进真实 data/snapshots/（本用例曾以此污染一次现场）。
    """
    return dict(os.environ, PYTHONIOENCODING="utf-8")


def _run_child(args):
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          cwd=str(ROOT), env=_child_env())


# --------------------------------------------------------------------------
# A. 原有契约（不得退化）
# --------------------------------------------------------------------------
def test_reentrancy_same_process():
    """同进程重入（v2 语义）：嵌套 acquire 返回浅句柄，全部释放后文件锁才消失"""
    lk = acquire_write_lock("task-A", timeout_s=1.0)
    inner = acquire_write_lock("task-B", timeout_s=0.1)  # 同进程 → 浅句柄，不抛
    assert inner._shallow is True
    inner.release()
    assert lock_path().exists(), "内层释放后外层仍持锁"
    lk.release()
    assert not lock_path().exists(), "外层释放后锁文件消失"


def test_mutual_exclusion_cross_process():
    """跨进程互斥：本进程持锁时，子进程获取失败（持有者信息可读）"""
    lk = acquire_write_lock("task-A", timeout_s=1.0)
    try:
        child = (
            "from quantstudio.pipeline.snapshot_lock import acquire_write_lock, WriteLockHeld" + chr(10) +
            "try:" + chr(10) +
            "    acquire_write_lock('task-B', timeout_s=0.5)" + chr(10) +
            "except WriteLockHeld as e:" + chr(10) +
            "    assert e.holder.get('task_id') == 'task-A'" + chr(10) +
            "    print('HELD_OK')" + chr(10)
        )
        r = _run_child([sys.executable, "-c", child])
        assert "HELD_OK" in r.stdout, r.stderr
        assert r.returncode == 0
    finally:
        lk.release()


def test_release_then_reacquire():
    """释放后可再获取（幂等 release）"""
    lk = acquire_write_lock("t1", timeout_s=1.0)
    lk.release()
    lk.release()  # 幂等
    lk2 = acquire_write_lock("t2", timeout_s=1.0)
    lk2.release()


def test_heartbeat_updates():
    """心跳更新持有者时间戳（API 不变：批一不引入后台线程）"""
    lk = acquire_write_lock("hb", timeout_s=1.0)
    before = read_holder()["heartbeat"]
    time.sleep(0.02)
    lk.heartbeat()
    assert read_holder()["heartbeat"] > before
    lk.release()


def test_cli_wrapper_passthrough():
    """CLI 包裹器：透传退出码/stdout/stderr；持锁期间子进程运行"""
    code = ("import sys; sys.stdout.write('OUT99'); sys.stderr.write('ERR99'); "
            "sys.exit(7)")
    r = _run_child(
        [sys.executable, "-m", "quantstudio.pipeline.snapshot_lock", "run",
         sys.executable, "-c", code])
    assert r.returncode == 7
    assert "OUT99" in r.stdout and "ERR99" in r.stderr


def test_cli_wrapper_lock_conflict_exit2():
    """CLI 包裹器：锁冲突时退出码 2 且输出持有者信息（心跳新鲜 → 不回收）"""
    lk = acquire_write_lock("holder-x", timeout_s=1.0)
    try:
        r = _run_child(
            [sys.executable, "-m", "quantstudio.pipeline.snapshot_lock", "run",
             sys.executable, "-c", "print('should-not-run')"])
        assert r.returncode == 2
        assert "holder-x" in (r.stderr or "")
        assert lock_path().exists(), "新鲜心跳的锁不得被回收"
    finally:
        lk.release()


# --------------------------------------------------------------------------
# B. 批一新增契约：死亡自愈
# --------------------------------------------------------------------------
def test_new_payload_carries_v2_fields():
    """payload v2：含 host / pid_create_time / token（供跨主机判据与所有权校验）"""
    lk = acquire_write_lock("v2", timeout_s=1.0)
    try:
        h = read_holder()
        assert h["v"] == 2
        assert h["pid"] == os.getpid()
        assert h["host"] == socket.gethostname()
        assert h["token"] == lk.token and h["token"]
        assert isinstance(h["pid_create_time"], float)
    finally:
        lk.release()


def test_lock_dir_override_isolates_from_production(monkeypatch, tmp_path):
    """T7-1 前提：QS_WRITE_LOCK_DIR 生效 → 验收用例与生产 data/snapshots 物理隔离"""
    assert str(lock_path()).startswith(str(tmp_path))
    repo_dir = ROOT / "data" / "snapshots"
    assert repo_dir not in lock_path().parents, "锁不得落在生产快照目录"


def test_default_lock_dir_matches_repo_convention(monkeypatch):
    """默认（未设重定向）解析路径与接入前逐位一致。

    仅断言路径解析，**不断言生产锁文件不存在**：生产锁是活资源——本机 daemon 正常写入期间
    会瞬时创建 `data/snapshots/.write_lock`，对该文件做「不存在」断言会假红（本用例首轮即以
    此暴露）。「验收前后无残留」由 T7-2 前/后基线 diff 判定
    （`agent_workspace/snapshot_dir_baseline.py`），而非单测断言。
    """
    monkeypatch.delenv("QS_WRITE_LOCK_DIR", raising=False)
    assert lock_path() == ROOT / "data" / "snapshots" / ".write_lock"


def test_stale_lock_dead_holder_reclaimed():
    """AC1：陈锁 + 持有者进程不存在 → 自动回收 + 审计行 + 本进程取得锁"""
    dead = _free_pid()
    _write_payload(_aged({"pid": dead, "task_id": "ghost", "heartbeat": 0}, 3600))
    # 构造 10 分钟以上的陈锁年龄
    lock_path().write_text(json.dumps({"pid": dead, "task_id": "ghost",
                                       "heartbeat": time.time() - 1200}),
                           encoding="utf-8")
    t0 = time.time()
    lk = acquire_write_lock("selfheal", timeout_s=3.0)
    try:
        assert time.time() - t0 <= 2.0, "回收应在 1 个轮询周期量级内完成"
        assert read_holder()["pid"] == os.getpid()
        audits = _audit_lines()
        assert len(audits) == 1
        assert "predicate=legacy_weak" in audits[0]
        assert f"pid={dead}" in audits[0]
        assert "heartbeat_age=" in audits[0]
    finally:
        lk.release()


@pytest.mark.parametrize("name,payload,age_floor_s", [
    ("客户A", CUST_A, 390_000),   # 2026-09-12 23:29:28 -> 09-17 12:04:44 ≈ 390,915 s
    ("客户B", CUST_B, 21_000),    # 2026-09-17 03:13:22 -> 09-17 09:08:18 ≈ 21,295 s
])
def test_replay_customer_payloads(name, payload, age_floor_s):
    """AC-replay（硬门）：用两客户真实残留 payload 回放，断言 legacy 弱判据回收成功。

    前置：该 pid 在本机必须不存在（否则用等价死 pid 替代，并在断言信息中说明）。
    """
    real_pid = int(payload["pid"])
    pid = real_pid if not psutil.pid_exists(real_pid) else _free_pid()
    replay = dict(payload, pid=pid)
    _write_payload(replay)

    diag = sl.diagnose_holder(replay)
    assert diag["verdict"] == "stale_dead", f"{name} 判定错误: {diag}"
    assert diag["predicate"] == "legacy_weak"
    assert diag["heartbeat_age"] >= age_floor_s, (
        f"{name} 回放年龄不符: {diag['heartbeat_age']}")

    lk = acquire_write_lock(f"replay-{name}", timeout_s=3.0)
    try:
        assert read_holder()["pid"] == os.getpid()
        audits = _audit_lines()
        assert len(audits) == 1
        assert "predicate=legacy_weak" in audits[0] and f"pid={pid}" in audits[0]
        if pid != real_pid:
            print(f"[replay] {name}: 本机 pid {real_pid} 存活，改用等价死 pid {pid}")
    finally:
        lk.release()


_HOLD_CHILD = """
import sys, time
from quantstudio.pipeline.snapshot_lock import acquire_write_lock
lk = acquire_write_lock('live-holder', timeout_s=3.0)
open(sys.argv[1], 'w', encoding='utf-8').write('ready')
time.sleep(12.0)
lk.release()
"""


def test_stale_heartbeat_live_holder_not_reclaimed(tmp_path):
    """AC2（红线）：持有者进程存活 + 心跳停更 → 不回收，fail-closed"""
    ready = tmp_path / "ready.txt"
    proc = subprocess.Popen([sys.executable, "-c", _HOLD_CHILD, str(ready)],
                            cwd=str(ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", env=_child_env())
    try:
        for _ in range(200):
            if ready.exists():
                break
            time.sleep(0.05)
        assert ready.exists(), "子进程未取得锁"
        h = read_holder()
        h["heartbeat"] = time.time() - 3600      # 模拟「存活但心跳停更」
        _write_payload(h)

        with pytest.raises(WriteLockHeld) as ei:
            acquire_write_lock("parent", timeout_s=1.5)
        assert ei.value.stale is True
        assert ei.value.diagnosis.get("verdict") == "stale_alive"
        assert "存活" in str(ei.value)
        assert lock_path().exists(), "存活持有者的锁不得被回收"
        assert _audit_lines() == [], "未回收不得写审计行"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_cross_host_lock_not_reclaimed():
    """AC3：跨主机 payload → 不回收"""
    dead = _free_pid()
    _write_payload(_aged({"v": 2, "pid": dead, "task_id": "remote",
                          "host": "another-host", "token": "x"}, 3600))
    with pytest.raises(WriteLockHeld) as ei:
        acquire_write_lock("t", timeout_s=1.0)
    assert ei.value.diagnosis.get("verdict") == "cross_host"
    assert lock_path().exists()
    assert _audit_lines() == []


def test_pid_reuse_treated_as_dead_and_reclaimed():
    """AC4：PID 复用（create_time 不符）→ 判原持有者已终止 → 回收"""
    me = os.getpid()
    real_ct = float(psutil.Process(me).create_time())
    _write_payload(_aged({"v": 2, "pid": me, "task_id": "old-holder",
                          "host": socket.gethostname(),
                          "pid_create_time": real_ct - 100000.0,
                          "token": "oldtoken"}, 3600))
    lk = acquire_write_lock("t", timeout_s=2.0)
    try:
        assert read_holder()["pid"] == me
        audits = _audit_lines()
        assert len(audits) == 1 and "predicate=v2_pid_reused" in audits[0]
    finally:
        lk.release()


def test_unreadable_lock_not_reclaimed():
    """不可解析 payload → 不回收（fail-closed）"""
    lock_path().write_text("not-json", encoding="utf-8")
    with pytest.raises(WriteLockHeld) as ei:
        acquire_write_lock("t", timeout_s=0.5)
    assert ei.value.diagnosis.get("verdict") == "undecidable"
    assert lock_path().exists()


def test_selfheal_disabled_fails_closed(monkeypatch):
    """回退开关：QS_WRITE_LOCK_SELFHEAL=0 → 退回旧「仅告警」行为"""
    monkeypatch.setenv(sl.SELFHEAL_ENV, "0")
    dead = _free_pid()
    _write_payload({"pid": dead, "task_id": "ghost", "heartbeat": time.time() - 3600})
    with pytest.raises(WriteLockHeld) as ei:
        acquire_write_lock("t", timeout_s=0.6)
    assert ei.value.stale is True
    assert lock_path().exists()
    assert _audit_lines() == []


def test_lock_taken_by_another_owner_raises():
    """AC7（硬判据）：锁被**他人取得**（token 不同）→ WriteLockLost（防静默双写）"""
    lk = acquire_write_lock("owner", timeout_s=1.0)
    try:
        other = dict(read_holder(), token="other-process-token", pid=os.getpid() + 1)
        _write_payload(other)
        with pytest.raises(WriteLockLost):
            lk.assert_still_owner()
        with pytest.raises(WriteLockLost):
            assert_lock_owner()
        assert read_holder()["token"] == "other-process-token", "不得覆盖他人持锁内容"
    finally:
        lk.release()


def test_missing_lock_file_atomically_restored():
    """锁文件被外部删除（非他人取得）→ 原子重建恢复持有，不误判为所有权丢失。

    场景来源：既有回归（tests/test_qfq_reanchor_batch1.py 的锁卫生 fixture 直接
    unlink 锁文件）暴露——「文件缺失」本身不构成双写事实，**他人持锁**才构成。
    """
    lk = acquire_write_lock("owner", timeout_s=1.0)
    try:
        lock_path().unlink()
        lk.assert_still_owner()      # 不抛：就地恢复
        assert lock_path().exists(), "应原子重建锁文件"
        assert read_holder()["token"] == lk.token
        assert_lock_owner()          # 模块级校验同样通过
    finally:
        lk.release()


def test_owner_check_passes_while_holding():
    """未篡改时所有权校验为空操作（不改变正常写路径行为）"""
    lk = acquire_write_lock("owner", timeout_s=1.0)
    try:
        lk.assert_still_owner()
        assert_lock_owner()  # 不抛
    finally:
        lk.release()


def test_release_all_write_locks_idempotent():
    """atexit/信号路径的尽力释放：释放后可再次获取"""
    lk = acquire_write_lock("s1", timeout_s=1.0)
    sl.release_all_write_locks()
    assert not lock_path().exists()
    lk2 = acquire_write_lock("s2", timeout_s=1.0)
    lk2.release()


_RACE_CHILD = """
import os, sys, time
from quantstudio.pipeline.snapshot_lock import acquire_write_lock, WriteLockHeld
out = sys.argv[1]
try:
    lk = acquire_write_lock('race-child', timeout_s=3.0)
except WriteLockHeld:
    sys.exit(0)
with open(out, 'a', encoding='utf-8') as f:
    f.write('ACQUIRED %d' % os.getpid() + chr(10))
time.sleep(6.0)
lk.release()
"""


def test_reclaim_race_eight_processes(tmp_path):
    """AC5（硬门）：八进程并发回收同一陈锁 → 恰一获得锁，审计恰一条 reclaim"""
    dead = _free_pid()
    _write_payload({"pid": dead, "task_id": "writers:write:stock_minutes:race",
                    "heartbeat": time.time() - 3600})
    out = tmp_path / "acquired.txt"
    procs = [subprocess.Popen([sys.executable, "-c", _RACE_CHILD, str(out)],
                              cwd=str(ROOT), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace",
                              env=_child_env())
             for _ in range(8)]
    for p in procs:
        p.wait(timeout=90)
    lines = ([ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
             if out.exists() else [])
    assert len(lines) == 1, f"恰一获锁断言失败: {lines}"
    audits = _audit_lines()
    assert len(audits) == 1, f"审计应恰一条 reclaim: {audits}"
    assert "predicate=legacy_weak" in audits[0]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
