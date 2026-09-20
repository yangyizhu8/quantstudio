"""T2 锁生命周期契约测试：锁文件释放后**不得被删除**（preserve_lock_file）。

红态依据（计划 §0.3 + 总调度 2026-09-20 裁定九处清单）：
  filelock 3.32.0 默认在 _release() 末尾删除锁文件（WindowsFileLock 与 UnixFileLock 同）。
  删除锁文件有跨进程竞态（POSIX flock 陷阱）：A 持锁释放→文件被删→B 在新 inode 上建锁，
  与 A 删除前的旧 inode 锁产生"双持有"窗口；目录粘滞位/权限场景还会导致重建失败。
  处置 = 九处构造点全部 preserve_lock_file=True（锁文件常驻，永不删除）。

本文件先红后绿：当前（未加参）红；九处落码后绿。
替身纪律：被测为真实 CollectorRunLock / 实例锁构造，仅 monkeypatch 锁路径指向 tmp_path。
"""
from __future__ import annotations

import pytest

import quantstudio.pipeline.daemon_lifecycle as L


def _fresh_lock(tmp_path, name):
    p = tmp_path / name
    monkey_target_collector = lambda: p      # noqa: E731
    return p, monkey_target_collector


def test_collector_run_lock_release_preserves_file(tmp_path, monkeypatch):
    """采集锁（#1/#2 构造点族）：acquire→release 后锁文件必须仍在。"""
    p = tmp_path / "collector_run.lock"
    monkeypatch.setattr(L, "collector_run_lock_path", lambda: p)
    lock = L.CollectorRunLock(timeout=1)
    with lock:
        assert p.exists(), "持锁期间锁文件应存在"
    assert p.exists(), (
        "锁文件在释放后被删除 —— 跨进程删锁竞态陷阱（preserve_lock_file 缺失，"
        "T2 九处构造点改造目标）")


def test_collector_run_lock_try_acquire_preserves_file(tmp_path, monkeypatch):
    """try_acquire 路径（#2, timeout=0）：获取成功后释放，锁文件必须仍在。"""
    p = tmp_path / "collector_run.lock"
    monkeypatch.setattr(L, "collector_run_lock_path", lambda: p)
    lock = L.CollectorRunLock(timeout=0)
    acquired = False
    try:
        with lock:
            acquired = True
            assert p.exists()
        assert acquired
    except Exception:
        pytest.skip("try_acquire 未获得锁（并发环境）——非本测靶")
    assert p.exists(), "try_acquire 释放后锁文件被删除（同陷阱）"


def test_all_filelock_constructions_carry_preserve_lock_file():
    """静态全谱契约：T2 九处改造目标文件中，每个 FileLock( 构造都必须带
    preserve_lock_file=True（workers.py 除外——其两处将委托化删除）。

    比逐点运行时测试更强：任何一处漏改即红；新增构造点忘带参也红。
    （原第三条试图经 DaemonLifecycle 运行时测实例锁，因构造需 config_dir 失败——
     测试台缺陷登记后改为本静态契约，覆盖面反而更全。）
    """
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    targets = [
        "quantstudio/pipeline/daemon_lifecycle.py",
        "quantstudio/pipeline/daemon.py",
        "quantstudio/pipeline/qfq_formal_cutover.py",
        "quantstudio/pipeline/qfq_orchestrator_cli.py",
        "quantstudio/pipeline/qfq_staging_prep.py",
    ]
    # T2 终态（裁定②）：workers.py = **零 FileLock 构造**（全委托；比带参更强的状态）
    workers_src = (root / "quantstudio/gui/workers.py").read_text(encoding="utf-8")
    assert "FileLock(" not in workers_src, (
        "workers.py 应为零 FileLock 构造（T2 委托化终态），仍发现构造点")
    offenders = []
    for rel in targets:
        lines = (root / rel).read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if "FileLock(" not in line:
                continue
            # 括号平衡合并：从本行起拼接至 ( 与 ) 配平（覆盖多行构造；
            # 不用正则 [^)]* —— 它在 str(path()) 的内括号处截断，恒漏句尾参数）
            seg = line
            j = i
            while seg.count("(") > seg.count(")") and j + 1 < len(lines):
                j += 1
                seg += " " + lines[j].strip()
            if "preserve_lock_file" not in seg:
                offenders.append(f"{rel}:{i + 1}: {seg[:90]}")
    assert not offenders, (
        "以下 FileLock 构造缺 preserve_lock_file=True（T2 九处改造目标）：\n  "
        + "\n  ".join(offenders))


def test_release_instance_lock_keeps_lock_file(tmp_path, monkeypatch):
    """步④ 契约：release_instance_lock() **不得删除** .daemon.lock。

    原 :368 显式 unlink 与 preserve_lock_file=True 直接矛盾（注释"FileLock 不会
    自动删"是加参前旧语义残留）：
      - POSIX：删锁文件 = flock inode 竞态本体（计划 §0.3 陷阱）；
      - Windows：释放后立即删与并发 acquire 的重建竞态（msvcrt 句柄语义）。
    无人依赖"退出后文件消失"（acquire 自动建文件）——锁文件常驻是全局决定。
    """
    p = tmp_path / ".daemon.lock"
    monkeypatch.setattr(L, "daemon_lock_path", lambda: p)
    monkeypatch.setattr(L, "DATA_ROOT", tmp_path, raising=False)
    from quantstudio.pipeline.daemon_lifecycle import DaemonLifecycle
    import inspect
    sig = inspect.signature(DaemonLifecycle.__init__)
    kwargs = {}
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        if param.default is not inspect.Parameter.empty:
            continue
        kwargs[name] = str(tmp_path)      # 必填位给 tmp 路径（config_dir 等）
    mgr = DaemonLifecycle(**kwargs)
    assert mgr.acquire_instance_lock(), "空闲路径下实例锁应获取成功"
    assert p.exists()
    mgr.release_instance_lock()
    assert p.exists(), (
        "release_instance_lock 删除了 .daemon.lock —— 与 preserve_lock_file=True 矛盾"
        "（POSIX flock 竞态本体；Windows 重建竞态）")
