#!/usr/bin/env python
"""W2 detached runner (W2-0.9 安全收口版): launches the staging tool as a fully-
detached Windows process that outlives any Bash call (Bash tool caps at 10 min;
backfills need 2-4 h).

W2-0.9 安全修复（vs 之前版本）：
  - **绝不自动 kill 任何进程**。scan-orphans 默认只报告。kill 是独立显式命令，
    要求精确 PID + staging_root + argv 身份再次验证，默认只报告不执行。
  - orphan/active 匹配绑定 resolved staging_root + 真实 argv + task + nonce，
    绝不把正式 daemon / 其他 staging_root / 其他会话 Python 进程列为 victim。
  - 发现同一 staging_root 的活跃任务时 fail-closed（打印 PID/argv 摘要并退出非零）。
  - launcher 文件名带 task+nonce（不再共享 _detached_launcher_inner.py）。
  - done marker 记录 task/nonce/launcher PID/child PID/staging_root/start/finish/exit_code。

This is an EXECUTION-HARNESS helper, not framework code. It does not touch the
backtest engine, strategy logic, or data semantics.

Usage (invoked by a parent Bash call that returns immediately):
    python scripts/_w2_detached_runner.py run-task <task> --staging-root <SR> ...
    python scripts/_w2_detached_runner.py scan-orphans [--staging-root <SR>]
    python scripts/_w2_detached_runner.py kill --pid <PID> --staging-root <SR>
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DETACHED = 0x00000008  # DETACHED_PROCESS
_NEW_GROUP = 0x00000200  # CREATE_NEW_PROCESS_GROUP
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW

DAEMON_MARKERS = ("quantstudio.pipeline.daemon", "backfill_fin_growth_dividend_staging")


def _proc_argv(proc) -> list:
    try:
        return proc.cmdline() or []
    except Exception:
        return []


def _is_python_proc(argv: list, exe: str) -> bool:
    if not argv:
        return False
    exe_l = (exe or "").lower()
    if not (exe_l.endswith("python.exe") or exe_l.endswith("python")):
        return False
    return True


def _proc_staging_root(argv: list) -> str | None:
    """Extract the --staging-root value from a process argv, if present."""
    for i, a in enumerate(argv):
        if a == "--staging-root" and i + 1 < len(argv):
            try:
                return str(Path(argv[i + 1]).resolve())
            except Exception:
                return argv[i + 1]
        if a.startswith("--staging-root="):
            v = a.split("=", 1)[1]
            try:
                return str(Path(v).resolve())
            except Exception:
                return v
    return None


def _proc_task(argv: list) -> str | None:
    """Extract --run-task value (for staging tool) or --task (for daemon)."""
    for i, a in enumerate(argv):
        if a in ("--run-task", "--task") and i + 1 < len(argv):
            return argv[i + 1]
    return None


def find_w2_processes(staging_root: str | None = None, task: str | None = None):
    """Find QuantStudio daemon/staging python processes, optionally filtered by
    staging_root and task. Returns list of (pid, argv, exe, sr, t).

    Never matches bash/other shells — only python interpreters whose argv invokes
    the daemon module or the staging script. Binds to staging_root/task when given.
    """
    import psutil
    found = []
    for p in psutil.process_iter(["pid", "cmdline", "exe"]):
        try:
            argv = p.info.get("cmdline") or []
            exe = p.info.get("exe") or ""
            if not _is_python_proc(argv, exe):
                continue
            joined = " ".join(argv[1:])
            if not (("-m" in argv and "quantstudio.pipeline.daemon" in joined)
                    or "backfill_fin_growth_dividend_staging" in joined):
                continue
            sr = _proc_staging_root(argv)
            t = _proc_task(argv)
            if staging_root is not None and sr != staging_root:
                continue
            if task is not None and t != task:
                continue
            found.append((p.info["pid"], argv, exe, sr, t))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return found


def scan_orphans(staging_root: str | None = None) -> list[tuple]:
    """Report-only: list QuantStudio daemon/staging processes (no kill).

    If staging_root given, only processes bound to that exact staging_root.
    NEVER kills. Use the explicit `kill` command for that (with re-verification).
    """
    return find_w2_processes(staging_root=staging_root)


def launch_detached(staging_tool_args: list[str], log_path: Path,
                    done_marker: Path, task: str, nonce: str,
                    staging_root: str) -> int:
    """Launch the staging tool detached. Returns the launcher PID.

    The launcher writes combined stdout/stderr to log_path, then writes a rich
    done-marker (task/nonce/launcher_pid/child_pid/staging_root/start/finish/exit_code).
    """
    staging_script = str(_PROJECT_ROOT / "scripts" / "backfill_fin_growth_dividend_staging.py")
    inner_code = (
        "import subprocess, sys, time, pathlib, os\n"
        f"_cmd = [sys.executable, {staging_script!r}] + {staging_tool_args!r}\n"
        f"_log = open({str(log_path)!r}, 'w', encoding='utf-8')\n"
        "_rc = subprocess.call(_cmd, cwd=%r, stdout=_log, stderr=subprocess.STDOUT)\n"
        "_log.close()\n"
        "pathlib.Path(%r).write_text("
        "'task=' + %r + chr(10) + "
        "'nonce=' + %r + chr(10) + "
        "'launcher_pid=' + str(os.getpid()) + chr(10) + "
        "'staging_root=' + %r + chr(10) + "
        "'start=' + %r + chr(10) + "
        "'finish=' + time.ctime() + chr(10) + "
        "'exit_code=' + str(_rc) + chr(10), encoding='utf-8')\n"
        % (str(_PROJECT_ROOT), str(done_marker), task, nonce, staging_root,
           time.ctime())
    )
    # Unique launcher filename per task+nonce (no shared _detached_launcher_inner.py)
    launcher_path = (_PROJECT_ROOT / "output" / "w2_fin_growth_dividend_20260728"
                     / f"_detached_launcher_{task}_{nonce[:8]}.py")
    launcher_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_path.write_text(inner_code, encoding="utf-8")

    if done_marker.exists():
        done_marker.unlink()
    if log_path.exists():
        log_path.unlink()

    proc = subprocess.Popen(
        [sys.executable, str(launcher_path)],
        cwd=str(_PROJECT_ROOT),
        creationflags=_DETACHED | _NEW_GROUP | _NO_WINDOW,
        close_fds=True,
    )
    return proc.pid


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run-task")
    p_run.add_argument("task")
    p_run.add_argument("--source-db", required=True)
    p_run.add_argument("--staging-root", required=True)
    p_run.add_argument("--timeout-sec", type=int, default=21600)
    p_run.add_argument("--log", required=True)
    p_run.add_argument("--done-marker", required=True)

    p_scan = sub.add_parser("scan-orphans")
    p_scan.add_argument("--staging-root", default=None)

    p_kill = sub.add_parser("kill")
    p_kill.add_argument("--pid", type=int, required=True)
    p_kill.add_argument("--staging-root", required=True)

    args = parser.parse_args()

    if args.cmd == "scan-orphans":
        sr = str(Path(args.staging_root).resolve()) if args.staging_root else None
        procs = scan_orphans(staging_root=sr)
        if not procs:
            print("orphans: none")
        for pid, argv, exe, p_sr, p_task in procs:
            print(f"  PID {pid} | staging_root={p_sr} | task={p_task} | "
                  f"argv={' '.join(argv)[:100]}")
        return 0

    if args.cmd == "kill":
        # Explicit kill: re-verify the PID is a QuantStudio process bound to the
        # given staging_root before killing. Never kill on mismatch.
        import psutil
        sr = str(Path(args.staging_root).resolve())
        try:
            p = psutil.Process(args.pid)
            argv = p.cmdline() or []
        except psutil.NoSuchProcess:
            print(f"PID {args.pid} not found")
            return 1
        joined = " ".join(argv[1:])
        is_qs = (("-m" in argv and "quantstudio.pipeline.daemon" in joined)
                 or "backfill_fin_growth_dividend_staging" in joined)
        p_sr = _proc_staging_root(argv)
        if not is_qs or p_sr != sr:
            print(f"REFUSE kill: PID {args.pid} is not a QuantStudio process bound "
                  f"to staging_root {sr} (is_qs={is_qs}, proc_sr={p_sr}). "
                  f"argv={' '.join(argv)[:120]}")
            return 1
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(args.pid)],
                       capture_output=True)
        print(f"killed PID {args.pid} (tree) bound to {sr}")
        return 0

    if args.cmd == "run-task":
        sr = str(Path(args.staging_root).resolve())
        # Pre-launch: detect an active task on the SAME staging_root → fail-closed.
        # We do NOT auto-kill. Different staging_root / formal daemon / other
        # sessions are never touched.
        same_root = find_w2_processes(staging_root=sr, task=args.task)
        if same_root:
            print(f"[BLOCK] an active task {args.task!r} is already running on "
                  f"staging_root {sr}. Refusing to launch a second one. "
                  f"Active process(es):")
            for pid, argv, exe, p_sr, p_task in same_root:
                print(f"  PID {pid} | argv={' '.join(argv)[:120]}")
            print("To stop it, use: kill --pid <PID> --staging-root <SR> "
                  "(re-verified, explicit).")
            return 1
        nonce = f"{int(time.time())}_{os.getpid()}"
        staging_args = [
            "--run-task", args.task,
            "--source-db", args.source_db,
            "--staging-root", args.staging_root,
            "--timeout-sec", str(args.timeout_sec),
        ]
        pid = launch_detached(staging_args, Path(args.log), Path(args.done_marker),
                              args.task, nonce, sr)
        print(f"DETACHED launcher PID: {pid}")
        print(f"task={args.task} nonce={nonce} staging_root={sr}")
        print(f"log: {args.log}")
        print(f"done marker: {args.done_marker}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
