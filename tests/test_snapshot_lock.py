# -*- coding: utf-8 -*-
"""3A 写锁模块单测（DSH 终审验收要求：互斥/心跳/陈锁/释放后再获取/CLI 包裹透传）"""
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quantstudio.pipeline.snapshot_lock import (
    WriteLockHeld, acquire_write_lock, lock_path, read_holder)


@pytest.fixture(autouse=True)
def _clean_lock():
    # 隔离：清文件锁 + 归零同进程重入深度（防跨用例级联）
    import quantstudio.pipeline.snapshot_lock as sl
    sl._depth = 0
    sl._ensure_handles.clear()
    lock_path().unlink(missing_ok=True)
    yield
    sl._depth = 0
    sl._ensure_handles.clear()
    lock_path().unlink(missing_ok=True)


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
    """跨进程互斥：本进程持锁时，子进程 CLI 获取失败（exit 2 + 持有者信息）"""
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
        r = subprocess.run([sys.executable, "-c", child],
                           capture_output=True, text=True,
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
    """心跳更新持有者时间戳"""
    lk = acquire_write_lock("hb", timeout_s=1.0)
    before = read_holder()["heartbeat"]
    time.sleep(0.02)
    lk.heartbeat()
    assert read_holder()["heartbeat"] > before
    lk.release()


def test_stale_lock_flagged_not_cleared():
    """陈锁：心跳超时 → WriteLockHeld.stale=True，锁文件不被自动清除"""
    p = lock_path()
    p.write_text('{"pid": 999999, "task_id": "ghost", "heartbeat": 0}',
                 encoding="utf-8")
    with pytest.raises(WriteLockHeld) as ei:
        acquire_write_lock("t", timeout_s=0.3)
    assert ei.value.stale is True
    assert p.exists(), "陈锁须人工确认，不得自动清除"


def test_cli_wrapper_passthrough():
    """CLI 包裹器：透传退出码/stdout/stderr；持锁期间子进程运行"""
    code = ("import sys; sys.stdout.write('OUT99'); sys.stderr.write('ERR99'); "
            "sys.exit(7)")
    r = subprocess.run(
        [sys.executable, "-m", "quantstudio.pipeline.snapshot_lock", "run",
         sys.executable, "-c", code],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert r.returncode == 7
    assert "OUT99" in r.stdout and "ERR99" in r.stderr


def test_cli_wrapper_lock_conflict_exit2():
    """CLI 包裹器：锁冲突时退出码 2 且输出持有者信息"""
    lk = acquire_write_lock("holder-x", timeout_s=1.0)
    try:
        r = subprocess.run(
            [sys.executable, "-m", "quantstudio.pipeline.snapshot_lock", "run",
             sys.executable, "-c", "print('should-not-run')"],
            capture_output=True, text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        assert r.returncode == 2
        assert "holder-x" in r.stderr
    finally:
        lk.release()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
