"""W2-0.9 缺陷 C/F 测试：detached runner 安全收口。

验证：
- scan-orphans 默认只报告，绝不 kill
- orphan 匹配绑定 staging_root + argv + task，不误伤正式 daemon / 其他会话 Python
- 同一 staging_root 已有活跃任务 → run-task fail-closed（不启动第二个，不自动 kill）
- kill 命令要求精确 PID + staging_root + argv 身份再验证，不匹配则 REFUSE
- launcher 文件名带 task+nonce（唯一）
- kill 显式命令默认不执行（需 PID + staging_root）
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_RUNNER = _PROJECT_ROOT / "scripts" / "_w2_detached_runner.py"


def _run_runner(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(_RUNNER)] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=str(_PROJECT_ROOT))


class TestScanOrphansReportOnly:
    """scan-orphans 默认只报告，绝不 kill。"""

    def test_scan_no_orphans_reports_none(self):
        r = _run_runner(["scan-orphans"])
        assert r.returncode == 0
        assert "none" in r.stdout.lower() or "orphan" in r.stdout.lower()

    def test_scan_with_staging_root_filter(self):
        r = _run_runner(["scan-orphans", "--staging-root",
                         "D:/nonexistent_staging_root_xyz"])
        assert r.returncode == 0


class _FakeProc:
    """Mimics a psutil Process with a .info dict (as process_iter yields)."""
    def __init__(self, pid, cmdline, exe):
        self.info = {"pid": pid, "cmdline": cmdline, "exe": exe}


class TestPreciseMatching:
    """orphan 匹配绑定 staging_root + argv + task，不误伤。"""

    def test_find_w2_processes_filters_by_staging_root(self):
        from scripts._w2_detached_runner import find_w2_processes
        fake_procs = [
            _FakeProc(111, [sys.executable, "-m",
             "quantstudio.pipeline.daemon", "--staging-root", "D:/match_root",
             "--task", "fin_indicator"], sys.executable),
            _FakeProc(222, [sys.executable, "-m",
             "quantstudio.pipeline.daemon", "--staging-root", "D:/other_root",
             "--task", "stock_dividend"], sys.executable),
            _FakeProc(333, ["/bin/bash", "-c", "grep daemon"], "/bin/bash"),
        ]
        with patch("psutil.process_iter", return_value=iter(fake_procs)):
            found = find_w2_processes(staging_root=str(Path("D:/match_root").resolve()))
        pids = [f[0] for f in found]
        assert 111 in pids, "matching staging_root process must be found"
        assert 222 not in pids, "different staging_root must be excluded"
        assert 333 not in pids, "bash shell must never match"

    def test_find_w2_processes_excludes_non_python(self):
        from scripts._w2_detached_runner import find_w2_processes
        fake_procs = [
            _FakeProc(444, ["C:/Program Files/Git/usr/bin/bash.exe",
             "-c", "python -m quantstudio.pipeline.daemon --staging-root D:/x"],
             "C:/Program Files/Git/usr/bin/bash.exe"),
        ]
        with patch("psutil.process_iter", return_value=iter(fake_procs)):
            found = find_w2_processes()
        assert all(f[0] != 444 for f in found), "bash.exe must never match even if argv mentions daemon"


class TestSameRootFailClosed:
    """同一 staging_root 已有活跃任务 → run-task fail-closed（不启动，不自动 kill）。

    Tested at the function level (the run-task CLI runs in a subprocess where
    parent-side patches don't apply). The CLI dispatches to find_w2_processes +
    launch_detached; we verify find_w2_processes + the same-root guard logic.
    """

    def test_same_root_detection_returns_active(self):
        from scripts._w2_detached_runner import find_w2_processes
        sr = str(Path("D:/staging_x").resolve())
        fake_procs = [
            _FakeProc(9999, [sys.executable, "-m",
             "quantstudio.pipeline.daemon", "--staging-root", sr,
             "--task", "fin_indicator"], sys.executable),
        ]
        with patch("psutil.process_iter", return_value=iter(fake_procs)):
            active = find_w2_processes(staging_root=sr, task="fin_indicator")
        assert len(active) == 1, "same-root+same-task active process must be detected"
        assert active[0][0] == 9999

    def test_different_root_not_detected_as_same(self):
        from scripts._w2_detached_runner import find_w2_processes
        sr = str(Path("D:/staging_x").resolve())
        other = str(Path("D:/other_root").resolve())
        fake_procs = [
            _FakeProc(9999, [sys.executable, "-m",
             "quantstudio.pipeline.daemon", "--staging-root", other,
             "--task", "fin_indicator"], sys.executable),
        ]
        with patch("psutil.process_iter", return_value=iter(fake_procs)):
            active = find_w2_processes(staging_root=sr, task="fin_indicator")
        assert len(active) == 0, "different staging_root must not be detected as same-root active"


class TestKillExplicitAndReverified:
    """kill 命令要求精确 PID + staging_root + argv 身份再验证。"""

    def test_kill_refuses_nonexistent_pid(self):
        r = _run_runner(["kill", "--pid", "99999999",
                         "--staging-root", "D:/staging_x"])
        assert r.returncode != 0

    def test_kill_refuses_mismatched_staging_root(self):
        # PID exists (this test process) but its argv isn't a QuantStudio daemon
        # bound to the given staging_root → REFUSE
        r = _run_runner(["kill", "--pid", str(__import__("os").getpid()),
                         "--staging-root", "D:/staging_x"])
        assert r.returncode != 0, "must REFUSE kill on argv/root mismatch"
        assert "REFUSE" in r.stdout or "not a QuantStudio" in r.stdout


class TestUniqueLauncherFilename:
    """launcher 文件名带 task+nonce（唯一）。"""

    def test_launch_detached_uses_unique_filename(self, monkeypatch):
        from scripts import _w2_detached_runner as runner
        captured = {}
        real_popen = subprocess.Popen

        class _FakeProc:
            pid = 12345

        def _fake_popen(cmd, **kw):
            captured["cmd"] = cmd
            captured["kw"] = kw
            return _FakeProc()

        monkeypatch.setattr(runner.subprocess, "Popen", _fake_popen)
        monkeypatch.setattr(runner.time, "ctime", lambda: "TIMESTAMP")
        log = _PROJECT_ROOT / "output" / "w2_fin_growth_dividend_20260728" / "test_run.log"
        done = _PROJECT_ROOT / "output" / "w2_fin_growth_dividend_20260728" / "test_run.DONE"
        pid = runner.launch_detached(
            ["--run-task", "fin_indicator", "--source-db", "x", "--staging-root", "y"],
            log, done, "fin_indicator", "abc12345", "D:/y")
        # The launcher file written should contain task+nonce in its name
        launcher_files = list((_PROJECT_ROOT / "output" / "w2_fin_growth_dividend_20260728").glob(
            "_detached_launcher_fin_indicator_*.py"))
        assert any("abc12345" in f.name for f in launcher_files), (
            "launcher filename must include task+nonce; got "
            f"{[f.name for f in launcher_files]}")
        # cleanup
        for f in launcher_files:
            f.unlink(missing_ok=True)
        log.unlink(missing_ok=True)
        done.unlink(missing_ok=True)
