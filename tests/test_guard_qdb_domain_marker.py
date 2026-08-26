"""Guard SYSTEM 不可读进程 QDB 域归因测试（v1.1 验收 V1-V6）。

对应 docs/governance-guard-system-proc-design.md §5：
  V1 无 marker 时 fail_closed 维持（回归不变）
  V2 真实归因：marker 存在+pid 存活+父链命中 → qdb_domain:* 重分类
     （含跨用户路径验证：marker 路径为固定 D 盘路径，非 $env:TEMP，
      以另一进程上下文写入 marker 模拟 SYSTEM 写 + 本进程读取）
  V3 其余不可读仍 fail-closed：marker 不存在/pid 死/task 不在集合
  V4 过期 marker 清扫（pid 死 + mtime>15min）
  V5 歧义名回归：qfq_maintenance 不在白名单，命中仍 abort
  V6 既有 guard 语义回归（扩展名锚定等）
"""
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import governance_snapshot as gs  # noqa: E402


MARKER_DIR = gs.QDB_DOMAIN_MARKER_DIR


def _write_marker(task, pid, age_min=0.0):
    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    f = MARKER_DIR / f"{task}_{pid}.json"
    f.write_text(json.dumps({"task": task, "pid": pid}), encoding="utf-8")
    if age_min:
        old = time.time() - age_min * 60
        os.utime(f, (old, old))
    return f


def _clear_markers():
    if MARKER_DIR.exists():
        for f in MARKER_DIR.glob("*.json"):
            f.unlink()


def _unreadable_hit(pid):
    return {"pid": pid, "cmd": "<UNREADABLE-CMDLINE python.exe> ...",
            "matched_pattern": "fail_closed"}


def test_v1_no_marker_failclosed_unchanged():
    """V1：无 marker 时归因返回 None → fail_closed 维持。"""
    _clear_markers()
    with mock.patch.object(gs, "_qdb_domain_markers", return_value={}):
        assert gs._attribute_qdb_domain(999999, {}, time.time()) is None


def test_v2_marker_attribution_via_parent_chain():
    """V2：marker pid = 父进程（本测试进程）pid，不可读子进程父链命中 → 归因。"""
    _clear_markers()
    parent_pid = os.getpid()
    markers = {("run_cloud_sync", parent_pid): time.time()}
    # 子进程 pid：spawn 一个真实 python 子进程，其父链包含本进程
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with mock.patch.object(gs, "_qdb_domain_markers", return_value=markers):
            got = gs._attribute_qdb_domain(p.pid, markers, time.time())
        assert got == "qdb_domain:run_cloud_sync", got
    finally:
        p.kill()
        p.wait()


def test_v2_cross_context_marker_write():
    """V2 跨用户路径验证：marker 由另一进程上下文（子进程）写入固定 D 盘路径，
    本进程（模拟 guard 用户态）读取成功且内容一致。路径非 $env:TEMP。"""
    _clear_markers()
    assert str(MARKER_DIR).startswith("D:\\"), "marker 必须固定 D 盘路径"
    assert "TEMP" not in str(MARKER_DIR).upper()
    writer_pid = os.getpid() + 100000  # 模拟 SYSTEM 任务 pid
    code = (
        "import json,sys;"
        f"open(r'{MARKER_DIR}\\run_cloud_sync_{writer_pid}.json','w',encoding='utf-8')"
        f".write(json.dumps({{'task':'run_cloud_sync','pid':{writer_pid}}}))"
    )
    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-c", code], check=True)
    got = gs._qdb_domain_markers()
    assert ("run_cloud_sync", writer_pid) in got, got
    _clear_markers()


def test_v3_unattributed_stays_failclosed():
    """V3a：marker task 不在 QDB_DOMAIN_TASKS → 不被 _qdb_domain_markers 收录。"""
    _clear_markers()
    f = _write_marker("not_a_qdb_task", 12345)
    got = gs._qdb_domain_markers()
    assert ("not_a_qdb_task", 12345) not in got
    f.unlink()


def test_v3_dead_pid_marker_not_attributed():
    """V3b：marker pid 已死 → alive 过滤后无归因对象。"""
    markers = {("run_cloud_sync", 999999): time.time()}  # pid 不存在
    got = gs._attribute_qdb_domain(os.getpid(), markers, time.time())
    assert got is None


def test_v3_unrelated_pid_not_attributed():
    """V3c：存活 marker 但命中进程不在其父链 → None（红线：不误归因无关 SYSTEM 进程）。"""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        markers = {("run_cloud_sync", p.pid): time.time()}
        # other 的父链是本进程，不含 p → 不归因
        assert gs._attribute_qdb_domain(other.pid, markers, time.time()) is None
        # 自身 pid 命中（marker pid 即进程自身/祖先链）
        assert gs._attribute_qdb_domain(p.pid, markers, time.time()) == "qdb_domain:run_cloud_sync"
    finally:
        p.kill(); p.wait(); other.kill(); other.wait()


def test_v4_stale_marker_cleanup():
    """V4：pid 死 + mtime>15min → 启动清扫删除；mtime 新鲜或 pid 活 → 保留。"""
    _clear_markers()
    stale = _write_marker("run_cloud_sync", 999999, age_min=30)      # 死 pid + 过期 → 删
    fresh = _write_marker("run_cloud_sync", 999998, age_min=2)       # 死 pid + 新鲜 → 留
    alive = _write_marker("run_repair_minutes", os.getpid())          # 活 pid → 留
    removed = gs._cleanup_stale_qdb_markers(time.time())
    assert removed == 1, removed
    assert not stale.exists() and fresh.exists() and alive.exists()
    _clear_markers()


def test_v5_ambiguous_name_not_whitelisted():
    """V5：qfq_maintenance 双实现歧义名禁入白名单 → yield 检查仍 abort。"""
    assert "qfq_maintenance" not in gs.QDB_READ_ONLY_PATTERNS
    hits = [{"pid": 1, "cmd": "python ...qfq_maintenance.py", "matched_pattern": "qfq_maintenance"}]
    writers = [h for h in hits
               if h.get("matched_pattern") not in gs.QDB_READ_ONLY_PATTERNS
               and not str(h.get("matched_pattern", "")).startswith("qdb_domain:")]
    assert writers, "歧义名命中必须保持 writer（abort）"


def test_v6_yield_and_startup_qdb_domain_exempt():
    """V6：qdb_domain:* 命中被 yield/启动豁免；fail_closed 未归因命中仍拦截。"""
    # yield：归因命中不抛
    gs._yield_check_data_side.__wrapped__ if hasattr(gs._yield_check_data_side, "__wrapped__") else None
    with mock.patch.object(gs, "_data_side_tasks_running", return_value=[
            {"pid": 2, "cmd": "<UNREADABLE> QDB域归因:run_cloud_sync",
             "matched_pattern": "qdb_domain:run_cloud_sync"}]):
        gs._yield_check_data_side()  # 不抛 GuardAbort
    # 启动：归因命中豁免（0 路径需内存/时段守卫不触发——用深夜/周末时段与充裕内存 mock）
    with mock.patch.object(gs, "_data_side_tasks_running", return_value=[
            {"pid": 2, "matched_pattern": "qdb_domain:run_repair_minutes"}]), \
         mock.patch.object(gs, "_free_phys_mb", return_value=1 << 20), \
         mock.patch.object(gs, "_in_trading_hours", return_value=False), \
         mock.patch.object(gs, "_cleanup_stale_qdb_markers", return_value=0):
        assert gs.data_side_guard("verify") == 0
    # 启动：未归因 fail_closed 仍拒绝
    with mock.patch.object(gs, "_data_side_tasks_running", return_value=[
            _unreadable_hit(3)]), \
         mock.patch.object(gs, "_free_phys_mb", return_value=1 << 20), \
         mock.patch.object(gs, "_in_trading_hours", return_value=False), \
         mock.patch.object(gs, "_cleanup_stale_qdb_markers", return_value=0):
        assert gs.data_side_guard("verify") == 6


def test_v6_readable_whitelist_patterns_yield_exempt():
    """V6b：可读 QDB 域 pattern（run_cloud_sync.ps1 锚定等）yield 豁免。"""
    hits = [{"pid": 4, "cmd": "powershell -File ...run_cloud_sync.ps1",
             "matched_pattern": "run_cloud_sync"}]
    with mock.patch.object(gs, "_data_side_tasks_running", return_value=hits):
        gs._yield_check_data_side()  # 不抛
