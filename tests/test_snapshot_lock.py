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


# --------------------------------------------------------------------------
# D. Part A：回收互斥陈旧自清（AC-m 组 / 2026-09-18）
#    被测行为：「回收者在创建与释放之间被杀 → 互斥永久残留 → 自愈永久失效」被修复。
# --------------------------------------------------------------------------
def _mutex_path():
    return sl._reclaim_path()


def _write_mutex(payload):
    p = _mutex_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")


def _stale_mutex(age_s=None, pid="dead"):
    """构造陈旧回收互斥：心跳超阈值 + 持有者进程不存在（默认）。"""
    if age_s is None:
        age_s = sl.RECLAIM_STALE_SECONDS + 60
    return {"pid": _free_pid() if pid == "dead" else pid,
            "task_id": "writers:reclaim:stale-holder",
            "heartbeat": time.time() - age_s}


def _dead_lock():
    return {"pid": _free_pid(), "task_id": "writers:write:stock_minutes:dead",
            "heartbeat": time.time() - 3600}


def test_mutex_stale_threshold_boundary():
    """单元：阈值判定 —— 未超阈值一律不算陈旧；超阈值且死证成立才算。"""
    now = time.time()
    dead = _free_pid()
    fresh = sl.reclaim_mutex_stale({"pid": dead, "heartbeat": now - (sl.RECLAIM_STALE_SECONDS - 1)}, now=now)
    assert fresh["stale"] is False and fresh["reason"] == "fresh", fresh
    boundary = sl.reclaim_mutex_stale({"pid": dead, "heartbeat": now - sl.RECLAIM_STALE_SECONDS}, now=now)
    assert boundary["stale"] is False, boundary  # 恰好等于阈值 = 未超（> 才清）
    stale = sl.reclaim_mutex_stale({"pid": dead, "heartbeat": now - (sl.RECLAIM_STALE_SECONDS + 1)}, now=now)
    assert stale["stale"] is True and stale["reason"] == "stale_dead", stale


def test_mutex_live_holder_not_cleared_even_when_heartbeat_stale():
    """红线：持有者进程存活（哪怕心跳停更）→ 绝不清 —— 否则其 finally 会误删他人互斥。"""
    now = time.time()
    st = sl.reclaim_mutex_stale({"pid": os.getpid(), "heartbeat": now - 9999}, now=now)
    assert st["stale"] is False and st["reason"] == "holder_alive", st
    # 端到端：活人互斥在场时，陈旧主锁不得被回收
    _write_mutex({"pid": os.getpid(), "task_id": "alive-reclaimer",
                  "heartbeat": time.time() - 9999})
    _write_payload(_dead_lock())
    with pytest.raises(WriteLockHeld):
        acquire_write_lock("m-live-mutex", timeout_s=1.0)
    assert lock_path().exists(), "活人互斥在场时主锁不得被回收"


def test_mutex_unreadable_or_pidless_fails_closed():
    """fail-closed：互斥 payload 不可解析 / pid 缺失 → 一律不清（与主锁判据 5 同款）。"""
    now = time.time()
    assert sl.reclaim_mutex_stale(None, now=now)["stale"] is False
    assert sl.reclaim_mutex_stale({"heartbeat": now - 9999}, now=now)["reason"] == "undecidable_pid"
    assert sl.reclaim_mutex_stale({"pid": 1}, now=now)["reason"] == "unparsable_heartbeat"
    # 端到端：不可解析的互斥在场 → 不清、不回收
    _mutex_path().parent.mkdir(parents=True, exist_ok=True)
    _mutex_path().write_text("{not-json", encoding="utf-8")
    _write_payload(_dead_lock())
    with pytest.raises(WriteLockHeld):
        acquire_write_lock("m-unreadable", timeout_s=1.0)
    assert _mutex_path().exists(), "不可解析互斥必须保留（fail-closed）"
    assert lock_path().exists(), "主锁不得被回收"


def test_fresh_mutex_behaves_exactly_as_before():
    """非抢占路径新旧等价（零行为变化）：互斥新鲜 = 正常竞争 → 直接放弃且零审计。"""
    _write_mutex({"pid": os.getpid(), "task_id": "busy-reclaimer", "heartbeat": time.time()})
    holder, diag = _dead_lock(), None
    _write_payload(holder)
    diag = sl.diagnose_holder(holder)
    assert diag["reclaimable"] is True
    assert sl.try_reclaim_stale(holder, diag, reclaimer_task="t-fresh") is False
    assert lock_path().exists(), "新鲜互斥在场时不得回收"
    assert _mutex_path().exists(), "新鲜互斥不得被清"
    assert _audit_lines() == [], f"非抢占路径必须零审计（旧行为逐位一致）: {_audit_lines()}"


def test_selfheal_disabled_does_not_clear_stale_mutex(monkeypatch):
    """回退开关：QS_WRITE_LOCK_SELFHEAL=0 → 陈旧互斥同样不清（自清继承同一闸门）。"""
    monkeypatch.setenv(sl.SELFHEAL_ENV, "0")
    _write_mutex(_stale_mutex())
    _write_payload(_dead_lock())
    with pytest.raises(WriteLockHeld):
        acquire_write_lock("m-rollback", timeout_s=1.0)
    assert _mutex_path().exists(), "回退态不得清陈旧互斥"
    assert lock_path().exists(), "回退态不得回收"


def test_stale_mutex_cleared_reclaim_proceeds_and_is_audited():
    """AC-m4（硬门）：陈旧互斥自清生效 → 回收得以继续 + 审计事件 reclaim_mutex_cleared 恰一条。

    修复前此场景永久卡死（FileExistsError 直接 return False，自愈永久失效）。
    """
    _write_mutex(_stale_mutex())
    _write_payload(_dead_lock())
    lk = acquire_write_lock("m-stale-clear", timeout_s=5.0)
    try:
        assert read_holder() is not None and read_holder()["task_id"] == "m-stale-clear"
        audits = _audit_lines()
        cleared = [ln for ln in audits if "reclaim_mutex_cleared" in ln]
        reclaimed = [ln for ln in audits if "reclaim STALE+DEAD" in ln]
        assert len(cleared) == 1, f"reclaim_mutex_cleared 应恰一条: {audits}"
        assert len(reclaimed) == 1, f"主锁回收应恰一条: {audits}"
        assert str(sl.RECLAIM_STALE_SECONDS) in cleared[0], cleared[0]
        assert not _mutex_path().exists(), "自清后互斥应由新的回收者持有并在结束时释放"
    finally:
        lk.release()


_MUTEX_RACE_CHILD = """
import os, sys, time
from quantstudio.pipeline.snapshot_lock import acquire_write_lock, WriteLockHeld
out = sys.argv[1]
try:
    lk = acquire_write_lock('race-child', timeout_s=5.0)
except WriteLockHeld:
    sys.exit(0)
with open(out, 'a', encoding='utf-8') as f:
    f.write('ACQUIRED %d' % os.getpid() + chr(10))
time.sleep(6.0)
lk.release()
"""


def test_stale_mutex_race_eight_processes_still_exactly_one(tmp_path):
    """AC-m1（硬门）：陈旧互斥在场时八进程并发回收 → 仍**恰一获锁**（互斥性不受自清影响）。

    自清的并发安全依据：unlink 幂等 + 真实锁互斥由 O_EXCL 线性化；最坏后果仅是多一条审计行。
    """
    _write_mutex(_stale_mutex())
    _write_payload(_dead_lock())
    out = tmp_path / "acquired_mutex.txt"
    procs = [subprocess.Popen([sys.executable, "-c", _MUTEX_RACE_CHILD, str(out)],
                              cwd=str(ROOT), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace",
                              env=_child_env())
             for _ in range(8)]
    for p in procs:
        p.wait(timeout=120)
    lines = ([ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
             if out.exists() else [])
    assert len(lines) == 1, f"恰一获锁断言失败（自清不得破坏互斥）: {lines}"
    audits = _audit_lines()
    cleared = [ln for ln in audits if "reclaim_mutex_cleared" in ln]
    reclaimed = [ln for ln in audits if "reclaim STALE+DEAD" in ln]
    assert len(reclaimed) == 1, f"主锁回收应恰一条: {audits}"
    # 自清审计条数**不是安全不变量**：并发下落败者若在他人已清后又删到新互斥，会产生额外审计行
    # —— 该情形批件已接受（"最坏=多一个回收者并发进入+多一条审计行"）。故此处只断言「至少一条、
    # 且不超过并发数」，安全不变量由上面两条（恰一获锁 / 主锁回收恰一条）承载。
    assert 1 <= len(cleared) <= 8, f"自清审计条数越界: {audits}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


# ── Part A-2（2026-09-22 批准）：空/不可解析互斥的 mtime 佐证（AC-m2 组）──────
def _age_mutex(seconds: float):
    """把回收互斥的 mtime 回拨 seconds 秒（构造「不可解析且 mtime 陈旧」场景）。"""
    p = _mutex_path()
    t = time.time() - seconds
    os.utime(p, (t, t))


def _write_empty_mutex():
    _mutex_path().parent.mkdir(parents=True, exist_ok=True)
    _mutex_path().write_text("", encoding="utf-8")


def test_ac_m5_stale_empty_mutex_cleared_by_mtime_and_audited():
    """AC-m5（硬门）：空互斥 + mtime 陈旧 ⇒ 清除 + 审计 reclaim_mutex_cleared_empty_mtime。

    Part A 在此 fail-closed ⇒ 空互斥永久楔住后续回收；本件以 mtime 作陈旧佐证。
    """
    _write_empty_mutex()
    _age_mutex(sl.RECLAIM_STALE_SECONDS + 5)
    _write_payload(_dead_lock())
    lk = acquire_write_lock("m-a2-empty-stale", timeout_s=5.0)
    try:
        audits = _audit_lines()
        hits = [ln for ln in audits if "reclaim_mutex_cleared_empty_mtime" in ln]
        assert len(hits) == 1, f"应恰一条空互斥审计: {audits}"
        assert "reason=unparseable_json" in hits[0], hits[0]
        assert str(sl.RECLAIM_STALE_SECONDS) in hits[0], hits[0]
        assert any("reclaim STALE+DEAD" in ln for ln in audits), "主锁回收应继续"
    finally:
        lk.release()


def test_ac_m6_fresh_empty_mutex_not_cleared():
    """AC-m6（硬门）：空互斥但 mtime 新鲜（<阈值）⇒ 不清（防误删「正在创建」的互斥）。"""
    _write_empty_mutex()
    _write_payload(_dead_lock())
    with pytest.raises(WriteLockHeld):
        acquire_write_lock("m-a2-empty-fresh", timeout_s=1.0)
    assert _mutex_path().exists(), "新鲜空互斥不得被清"


def test_ac_m7_second_read_identity_mismatch_aborts(monkeypatch):
    """AC-m7：二次 stat 身份不符（期间被重建）⇒ 放弃清除（TOCTOU 收窄）。"""
    class _St:
        def __init__(self, ns, size):
            self.st_mtime_ns = ns
            self.st_size = size
            self.st_mtime = ns / 1e9

    class _P:
        def __init__(self):
            self.i = 0
            self._seq = [_St(1_000_000_000_000, 0), _St(2_000_000_000_000, 12)]

        def stat(self):
            v = self._seq[min(self.i, len(self._seq) - 1)]
            self.i += 1
            return v

        def unlink(self, *a, **kw):
            # 记录式桩（不抛异常：fixture 清理亦会调用 unlink，抛异常会污染其它用例）
            self.unlinked += 1

    p = _P()
    p.unlinked = 0
    monkeypatch.setattr(sl, "_reclaim_path", lambda: p)
    assert sl._clear_stale_reclaim_mutex_by_mtime("t-a2", "unparseable_json", 0) is False
    assert p.unlinked == 0, "身份不符时不得 unlink"


def test_ac_m8_live_holder_stale_mtime_not_cleared():
    """AC-m8：payload **有效**（可解析且持有者活着）⇒ mtime 再old 也不得走 mtime 清除。"""
    _write_mutex({"pid": os.getpid(), "task_id": "a2-live", "heartbeat": time.time()})
    _age_mutex(sl.RECLAIM_STALE_SECONDS + 999)
    _write_payload(_dead_lock())
    with pytest.raises(WriteLockHeld):
        acquire_write_lock("m-a2-live", timeout_s=1.0)
    assert _mutex_path().exists(), "有效互斥（活人）不得被 mtime 路径清除"


def test_ac_m9_empty_mtime_kill_switch(monkeypatch):
    """AC-m9：QS_WRITE_LOCK_EMPTY_MTIME=0 ⇒ 空互斥（陈旧 mtime）仍 fail-closed（等效 Part A）。"""
    monkeypatch.setenv(sl.EMPTY_MTIME_ENV, "0")
    _write_empty_mutex()
    _age_mutex(sl.RECLAIM_STALE_SECONDS + 5)
    _write_payload(_dead_lock())
    with pytest.raises(WriteLockHeld):
        acquire_write_lock("m-a2-off", timeout_s=1.0)
    assert _mutex_path().exists(), "开关关闭时空互斥不得被清（逐位等效 Part A）"
    assert not any("reclaim_mutex_cleared_empty_mtime" in ln for ln in _audit_lines())
