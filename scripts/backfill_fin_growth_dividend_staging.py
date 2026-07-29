#!/usr/bin/env python
"""Controlled staging script for backfilling fin_indicator and stock_dividend data.

This script operates entirely on a STAGING copy of the production database.
It does NOT modify the production database under any circumstances. The
--promote flag is a dry-run only: it prints the commands that WOULD be used
for promotion without actually moving or renaming any files.

Phases:
    prepare   – Create staging environment (copy DB + config)
    run-task  – Execute a single backfill task against the staging DB
    audit     – Run DataQualityAuditor against the staging DB
    promote   – Dry-run promotion (print commands, do NOT execute)

Usage:
    python scripts/backfill_fin_growth_dividend_staging.py --prepare
    python scripts/backfill_fin_growth_dividend_staging.py --run-task fin_indicator
    python scripts/backfill_fin_growth_dividend_staging.py --run-task stock_dividend
    python scripts/backfill_fin_growth_dividend_staging.py --audit
    python scripts/backfill_fin_growth_dividend_staging.py --promote
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import duckdb
from filelock import FileLock

# ---------------------------------------------------------------------------
# Path resolution -- consistent with the rest of the project's scripts
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# Auto-load secrets (provides TUSHARE_TOKEN, QMT_PATH etc.)
try:
    from quantstudio._secrets import load_secrets_env
    load_secrets_env()
except Exception:
    pass  # non-fatal: daemon handles missing secrets gracefully

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [staging] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
STAGING_DB_NAME = "staging.db"
STAGING_CONFIG_DIR_NAME = "config"
REQUIRED_CONFIG_FILES = [
    "data_config.json",
    "sources_config.json",
    "collector_tasks.json",
    "alignment_rules.json",
]

# The two tasks this script is designed to backfill
ALLOWED_TASKS = frozenset({"fin_indicator", "stock_dividend"})

# Runtime artifacts that the daemon creates in the data directory
DAEMON_ARTIFACTS = [
    ".daemon.lock",
    ".collector_run.lock",
    "daemon_status.json",
    "daemon_run_state.json",
    "daemon_stop.request",
]

# P0-3: Evidence is now validated by validate_audit_evidence() — see below


# ===========================================================================
# CLI definition
# ===========================================================================
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Controlled staging backfill for fin_indicator/stock_dividend",
    )
    parser.add_argument(
        "--source-db",
        default=str(_PROJECT_ROOT / "data" / "quantstudio.db"),
        help="Path to production DB (default: data/quantstudio.db)",
    )
    parser.add_argument(
        "--staging-root",
        default=str(_PROJECT_ROOT / "data" / "staging"),
        help="Root directory for staging files (default: data/staging)",
    )
    parser.add_argument(
        "--start-date",
        default="2018-01-01",
        help="Start date for full_range pull (default: 2018-01-01)",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=21600,
        help="Timeout in seconds for subprocess commands (default: 21600 = 6 hours)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without executing",
    )
    parser.add_argument(
        "--reset-staging",
        action="store_true",
        help="Delete and recreate staging root during --prepare (required if staging already exists)",
    )

    # Phase flags (exclusive selection enforced below)
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Create staging environment (copy DB + config)",
    )
    parser.add_argument(
        "--run-task",
        metavar="TASK",
        default=None,
        help="Run one task: fin_indicator or stock_dividend",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Run quality audit on staging DB",
    )
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Dry-run promotion (print commands, do NOT execute)",
    )

    return parser


def validate_args(args: argparse.Namespace) -> None:
    """Validate argument combinations and mutually exclusive phases."""
    phases = [args.prepare, args.run_task is not None, args.audit, args.promote]
    active = sum(1 for p in phases if p)
    if active == 0:
        sys.exit("ERROR: No phase specified. Use --prepare, --run-task, --audit, or --promote.")
    if active > 1:
        sys.exit("ERROR: Only one phase can be specified at a time.")

    if args.run_task is not None and args.run_task not in ALLOWED_TASKS:
        sys.exit(f"ERROR: --run-task must be one of {sorted(ALLOWED_TASKS)}, got {args.run_task!r}")


# ===========================================================================
# Utility helpers
# ===========================================================================
def resolve_absolute(path_str: str) -> Path:
    """Resolve a path string to an absolute Path."""
    p = Path(path_str)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p.resolve()


def staging_db_path(staging_root: Path) -> Path:
    return staging_root / STAGING_DB_NAME


def staging_config_dir(staging_root: Path) -> Path:
    return staging_root / STAGING_CONFIG_DIR_NAME


def run_cmd(
    cmd: List[str],
    cwd: Path = None,
    dry_run: bool = False,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 21600,
    log_file: Optional[Path] = None,
    staging_db: Optional[Path] = None,
    task_name: str = "",
) -> "CommandResult":
    """Run a subprocess command with heartbeat and graceful timeout.

    Returns a CommandResult carrying the exit code, the child PID (when a real
    subprocess was spawned), and the started_at/finished_at wall-clock window
    (ISO-8601, UTC-naive local time) bracketing the child's lifetime.

    On dry_run, just print the command and return a CommandResult with
    exit_code=0, pid=None.

    Heartbeat: prints every 30s with task, pid, elapsed, log path, staging DB size.
    Timeout: after `timeout` seconds, sends SIGTERM, waits 60s, then SIGKILL.
    """
    cmd_str = " ".join(cmd)
    if dry_run:
        logger.info(f"[DRY-RUN] Would execute: {cmd_str}")
        return CommandResult(exit_code=0, pid=None)

    if env is None:
        env = os.environ.copy()

    logger.info(f"Executing: {cmd_str}")

    log_fh = None
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(str(log_file), "w", encoding="utf-8")

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    child_pid = proc.pid
    started_at = datetime.now().isoformat()

    start_time = time.monotonic()
    heartbeat_stop = threading.Event()
    last_heartbeat = [start_time]  # mutable for closure

    def _heartbeat():
        while not heartbeat_stop.is_set():
            heartbeat_stop.wait(30.0)
            if heartbeat_stop.is_set():
                break
            elapsed = time.monotonic() - start_time
            db_size_str = "N/A"
            if staging_db and staging_db.exists():
                try:
                    db_size_str = f"{staging_db.stat().st_size / 1024**2:.1f} MB"
                except Exception:
                    pass
            log_path_str = str(log_file) if log_file else "N/A"
            logger.info(
                f"[HEARTBEAT] task={task_name} pid={proc.pid} "
                f"elapsed={elapsed:.0f}s log={log_path_str} staging_db={db_size_str}"
            )

    heartbeat_thread = threading.Thread(target=_heartbeat, daemon=True)
    heartbeat_thread.start()

    # Read output in a separate thread to avoid blocking
    output_lines: List[str] = []

    def _read_output():
        try:
            for line in iter(proc.stdout.readline, ""):
                sys.stdout.write(line)
                if log_fh:
                    log_fh.write(line)
                output_lines.append(line)
        except Exception:
            pass

    reader_thread = threading.Thread(target=_read_output, daemon=True)
    reader_thread.start()

    exit_code = None
    try:
        if timeout == 0:
            # No timeout limit -- wait indefinitely
            proc.wait()
            exit_code = proc.returncode
        else:
            # Poll with periodic timeout check
            poll_interval = 5.0
            while exit_code is None:
                try:
                    exit_code = proc.wait(timeout=poll_interval)
                except subprocess.TimeoutExpired:
                    elapsed = time.monotonic() - start_time
                    if timeout > 0 and elapsed >= timeout:
                        logger.error(
                            f"TIMEOUT: task {task_name} exceeded {timeout}s limit. "
                            f"Sending SIGTERM to pid={proc.pid}..."
                        )
                        proc.terminate()
                        try:
                            exit_code = proc.wait(timeout=60)
                            logger.info(f"Process terminated gracefully after SIGTERM, exit_code={exit_code}")
                        except subprocess.TimeoutExpired:
                            logger.error(
                                f"Process did not exit after 60s SIGTERM grace period. "
                                f"Sending SIGKILL to pid={proc.pid}..."
                            )
                            proc.kill()
                            exit_code = proc.wait(timeout=10)
                            logger.info(f"Process killed with SIGKILL, exit_code={exit_code}")
                        break
    except Exception as e:
        logger.error(f"Exception during subprocess execution: {e}")
        try:
            proc.terminate()
            proc.wait(timeout=30)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        exit_code = -1
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=5)
        reader_thread.join(timeout=5)
        if log_fh:
            log_fh.close()

    if exit_code is None:
        exit_code = -1

    finished_at = datetime.now().isoformat()
    return CommandResult(
        exit_code=exit_code,
        pid=child_pid,
        started_at=started_at,
        finished_at=finished_at,
    )


# ===========================================================================
# Structured command result + unified runtime-manifest validator (W2-0.7B)
# ===========================================================================
@dataclass
class CommandResult:
    """Outcome of a run_cmd subprocess invocation.

    `pid`/`started_at`/`finished_at` are None for dry-runs (no process spawned).
    For real spawns, `started_at` <= `finished_at` bracket the child's lifetime
    in ISO-8601 (UTC-naive local time). `exit_code` is the child's return code,
    or -1 on internal error / forced kill with no usable code.
    """
    exit_code: int
    pid: Optional[int] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    # Backwards-compatible 3-tuple unpacking (exit_code, stdout, stderr). The
    # last two are always empty (run_cmd streams output live rather than
    # buffering). Kept so any legacy `rc, _, _ = run_cmd(...)` site still works.
    def __iter__(self):
        return iter((self.exit_code, "", ""))


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """Best-effort ISO-8601 parse; returns None on falsy/invalid input."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", ""))
    except Exception:
        return None


def validate_runtime_manifest(
    manifest: Dict,
    *,
    staging_root: Path,
    staging_cfg_dir: Path,
    expected_task: Optional[str] = None,
    expected_nonce: Optional[str] = None,
    expected_pid: Optional[int] = None,
    time_window: Optional[Tuple[str, str]] = None,
    require_pid_match: bool = False,
) -> Tuple[bool, str]:
    """Unified runtime-manifest validator shared by run-task / audit / promote.

    Returns (True, "") on success, (False, "reason") on any failure.

    Checks (all fail-closed):
      - manifest is a non-empty dict
      - format_version == "1.0"
      - task == expected_task (when provided)
      - nonce == expected_nonce (when provided)
      - pid is an int and, when require_pid_match and expected_pid are set,
        pid == expected_pid
      - QUANTSTUDIO_DATA_ROOT and imported_DATA_ROOT both resolve to staging_root
      - config_dir resolves to staging_cfg_dir
      - the seven path fields (writer/batch_audit/quarantine/daemon_log/
        collector_lock/daemon_lock/daemon_status) all resolve to paths whose
        string form starts with staging_root (i.e. inside the staging root)
      - when time_window=(started_at, finished_at) is provided, manifest.created_at
        parses and lies within [started_at, finished_at] inclusive

    `require_pid_match` is opt-in: run-task sets it True (a real child PID is
    known), while audit/promote leave it False (they validate on-disk manifests
    without a parent-child relationship to re-check the PID).
    """
    if not manifest or not isinstance(manifest, dict):
        return False, "manifest is empty or not a dict"
    if manifest.get("format_version") != "1.0":
        return False, f"manifest format_version={manifest.get('format_version')} expected '1.0'"
    if expected_task is not None and manifest.get("task") != expected_task:
        return False, f"manifest task={manifest.get('task')} expected {expected_task}"
    if expected_nonce is not None and manifest.get("nonce") != expected_nonce:
        return False, "manifest nonce mismatch"

    pid_val = manifest.get("pid")
    if not isinstance(pid_val, int) or pid_val <= 0:
        return False, f"manifest pid must be a positive int, got {pid_val!r}"
    if require_pid_match and expected_pid is not None and pid_val != expected_pid:
        return False, f"manifest pid={pid_val} does not match child pid={expected_pid}"

    sr = str(staging_root.resolve())
    for root_field in ("QUANTSTUDIO_DATA_ROOT", "imported_DATA_ROOT"):
        val = manifest.get(root_field, "")
        if not val:
            return False, f"manifest {root_field} is empty"
        if str(Path(val).resolve()) != sr:
            return False, f"manifest {root_field}={val} does not match staging root {sr}"

    cfg_dir_val = manifest.get("config_dir", "")
    if not cfg_dir_val:
        return False, "manifest config_dir is empty"
    if str(Path(cfg_dir_val).resolve()) != str(staging_cfg_dir.resolve()):
        return False, f"manifest config_dir={cfg_dir_val} does not match {staging_cfg_dir}"

    for path_field in (
        "writer_db_path", "batch_audit_db_path", "quarantine_db_path",
        "daemon_log_path", "collector_lock_path", "daemon_lock_path",
        "daemon_status_path",
    ):
        val = manifest.get(path_field, "")
        if not val:
            return False, f"manifest {path_field} is empty"
        # Path must resolve inside the staging root. Compare resolved string
        # prefixes so symlinks/.. are normalized.
        resolved = str(Path(val).resolve())
        if not (resolved == sr or resolved.startswith(sr + os.sep)):
            return False, f"manifest {path_field}={val} not inside staging root"

    if time_window is not None:
        started, finished = time_window
        created = _parse_iso(manifest.get("created_at"))
        start_dt = _parse_iso(started)
        finish_dt = _parse_iso(finished)
        if created is None:
            return False, f"manifest created_at unparseable: {manifest.get('created_at')!r}"
        if start_dt is not None and created < start_dt:
            return False, f"manifest created_at {created} is before child started_at {start_dt}"
        if finish_dt is not None and created > finish_dt:
            return False, (f"manifest created_at {created} is after child "
                           f"finished_at {finish_dt}")

    return True, ""


def check_disk_space(path: Path, required_bytes: int) -> Tuple[bool, int]:
    """Check if the filesystem containing `path` has at least `required_bytes` free.

    Returns (ok, free_bytes).
    """
    try:
        usage = shutil.disk_usage(path)
        return usage.free >= required_bytes, usage.free
    except Exception as e:
        logger.error(f"Cannot check disk space: {e}")
        return False, 0  # fail-closed: unknown disk state BLOCKS prepare


def check_daemon_and_collector_locks(data_root: Path) -> Tuple[bool, Optional[str]]:
    """Check whether daemon or collector is REALLY running (not just stale lock).

    - Daemon: reads daemon_status.json, calls verify_daemon_identity(dict).
      Any exception or unknown state BLOCKS (never "proceeding with caution").
    - Collector: uses filelock.FileLock to attempt non-blocking acquire on
      .collector_run.lock. If acquire fails, the lock is held by another process.

    Returns (should_block, reason_string_or_None).
    """
    # --- Daemon check via daemon_status.json + verify_daemon_identity ---
    status_path = data_root / "daemon_status.json"
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception as e:
            msg = f"Failed to read daemon_status.json: {e}"
            logger.error(msg)
            return True, msg  # BLOCK: unreadable status file = unknown state
        try:
            from quantstudio.pipeline.daemon_lifecycle import verify_daemon_identity
            result = verify_daemon_identity(status)
        except ImportError as e:
            msg = f"Cannot import verify_daemon_identity: {e}"
            logger.error(msg)
            return True, msg  # BLOCK: cannot verify = unknown state
        except Exception as e:
            msg = f"verify_daemon_identity raised exception: {e}"
            logger.error(msg)
            return True, msg  # BLOCK: exception = unknown state

        if result in ("alive", "denied"):
            return True, f"Live daemon detected: status={result}"
        elif result == "stale":
            logger.info("Daemon status is stale, ignoring")
        else:
            return True, f"Daemon status unknown: {result}"  # BLOCK: unknown
    # else: no status file = no daemon

    # --- Collector check via filelock ---
    collector_lock_path = data_root / ".collector_run.lock"
    if collector_lock_path.exists():
        lock = FileLock(str(collector_lock_path), timeout=0)
        try:
            lock.acquire(timeout=0)
            lock.release()
            # Got the lock = nobody holding it
        except Exception:
            return True, "Collector lock is held by another process"

    return False, None


def is_daemon_running(data_root: Path) -> bool:
    """DEPRECATED: Use check_daemon_and_collector_locks instead.

    Kept for backward compatibility. Returns True only if a live PID is found.
    Stale lock files do NOT block.
    """
    should_block, _ = check_daemon_and_collector_locks(data_root)
    return should_block


# ===========================================================================
# Phase: prepare
# ===========================================================================
def phase_prepare(args: argparse.Namespace) -> int:
    """Create the staging environment.

    Steps:
        0. Check --reset-staging if staging root already exists
        1. Check daemon/collector is NOT running
        2. Check disk space (~2x source DB size)
        3. Copy source-db to staging-root/staging.db
        4. Create staging config directory
        5. Copy config files
        6. Modify data_config.json to point db_path to staging.db (absolute)
        7. Set quarantine, batch_audit, log directories to staging-root/
        8. Do NOT modify production watermark
        9. Create empty SQLite ledger files (batch_audit.db, quarantine.db)
    """
    source_db = resolve_absolute(args.source_db)
    staging_root = resolve_absolute(args.staging_root)
    dry_run = args.dry_run
    reset_staging = args.reset_staging

    # W2-0.9 缺陷 C/F：非 dry-run prepare 必须 fail-closed 拒绝 staging_root 位于
    # Git worktree / 项目根 / 项目 data 目录内。根因：长跑 staging DB 在 worktree 内
    # 会被 git.exe（IDE/Git 索引）持有，导致 daemon 子进程 file-in-use IO 失败
    # （W2 第一次真实执行 stock_dividend 2506 次真实 IO 失败）。dry-run 仍允许，只打印
    # 警告以便用户验证外置路径。
    project_root = _PROJECT_ROOT.resolve()
    project_data_dir = (project_root / "data").resolve()
    try:
        # Detect git worktree top-level (treat .git presence as worktree)
        _git_dir = project_root / ".git"
        worktree_root = project_root if _git_dir.exists() else project_root
    except Exception:
        worktree_root = project_root

    def _is_inside(child: Path, parent: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False

    if not dry_run:
        _block_reasons = []
        if _is_inside(staging_root, worktree_root):
            _block_reasons.append(f"Git worktree / project root ({worktree_root})")
        if _is_inside(staging_root, project_data_dir):
            _block_reasons.append(f"project data directory ({project_data_dir})")
        # source_db direct overlap (staging_root IS the source_db, or source_db
        # inside staging_root which would recurse the copy). Note: we intentionally
        # do NOT block staging_root being inside source_db.parent in general — that
        # would reject legitimate sibling layouts (e.g. a test mini-DB + staging dir
        # sharing a temp folder). The worktree/data-dir checks above cover the real
        # production risk (production DB lives in project data/).
        if staging_root == source_db or _is_inside(source_db, staging_root):
            _block_reasons.append("source DB inside staging_root (copy recursion)")
        if _block_reasons:
            logger.error(
                "SAFETY BLOCK: --staging-root for a real (non-dry-run) prepare must "
                "be OUTSIDE the Git worktree / project root / project data dir. "
                "Long-running staging DBs inside the worktree are held by "
                "git.exe/IDE and cause file-in-use IO failures.\n"
                f"  staging_root: {staging_root}\n"
                f"  blocked because inside: {'; '.join(_block_reasons)}\n"
                f"  Use an external path, e.g. D:\\QuantStudio_W2_Staging\\<run_id>")
            return 1
    else:
        if _is_inside(staging_root, worktree_root):
            logger.warning(
                f"[DRY-RUN] WARNING: staging_root {staging_root} is inside the Git "
                f"worktree. A real prepare would BLOCK here. Use an external path.")

    logger.info("=" * 72)
    logger.info("PHASE: prepare -- Create staging environment")
    logger.info(f"  Source DB:    {source_db}")
    logger.info(f"  Staging root: {staging_root}")
    logger.info(f"  Dry run:      {dry_run}")
    logger.info("=" * 72)

    # Step 0: Check if staging root already exists
    staging_marker_path = staging_root / ".quantstudio_staging.json"
    if staging_root.exists() and not dry_run:
        if not reset_staging:
            logger.error(
                f"Staging root already exists: {staging_root}\n"
                f"  Use --reset-staging to delete and recreate, or remove it manually."
            )
            return 1
        # Validate staging marker before deletion
        if not staging_marker_path.exists():
            logger.error(
                f"SAFETY BLOCK: --reset-staging requested but staging marker not found: {staging_marker_path}\n"
                f"  Refusing to delete directory without a valid staging marker."
            )
            return 1
        try:
            marker = json.loads(staging_marker_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"SAFETY BLOCK: Cannot parse staging marker: {e}")
            return 1

        if marker.get("format_version") != "1.0":
            logger.error(f"SAFETY BLOCK: staging marker format_version is not '1.0': {marker.get('format_version')}")
            return 1

        marker_staging = Path(marker.get("staging_root", "")).resolve() if marker.get("staging_root") else None
        marker_source = Path(marker.get("source_db", "")).resolve() if marker.get("source_db") else None

        if marker_staging != staging_root:
            logger.error(
                f"SAFETY BLOCK: staging_root in marker ({marker_staging}) "
                f"does not match resolved target ({staging_root})"
            )
            return 1

        if marker_source != source_db:
            logger.error(
                f"SAFETY BLOCK: source_db in marker ({marker_source}) "
                f"does not match args.source_db ({source_db})"
            )
            return 1

        # Safety checks: target must not be disk root, project root, data/ dir,
        # source_db parent, or contain source_db
        project_root = _PROJECT_ROOT.resolve()
        data_dir = (project_root / "data").resolve()
        source_parent = source_db.parent.resolve()

        forbidden = {
            "disk root": Path(staging_root.anchor).resolve(),
            "project root": project_root,
            "data directory": data_dir,
            "source_db parent": source_parent,
        }
        for label, fpath in forbidden.items():
            if staging_root == fpath:
                logger.error(f"SAFETY BLOCK: staging_root is the {label} ({fpath}) -- refusing to delete")
                return 1

        try:
            staging_root.relative_to(source_db)
            logger.error(
                f"SAFETY BLOCK: staging_root ({staging_root}) contains source_db ({source_db}) -- refusing to delete"
            )
            return 1
        except ValueError:
            pass  # staging_root does NOT contain source_db, good

        try:
            source_db.relative_to(staging_root)
            logger.error(
                f"SAFETY BLOCK: source_db ({source_db}) is inside staging_root ({staging_root}) -- refusing to delete"
            )
            return 1
        except ValueError:
            pass  # source_db is NOT inside staging_root, good

        logger.info(f"[0/9] --reset-staging: marker validated, deleting existing staging root: {staging_root}")
        shutil.rmtree(staging_root)

    # Step 1: Check daemon is NOT running
    data_root = source_db.parent
    if is_daemon_running(data_root):
        logger.error(
            "Daemon is currently running. Please stop the daemon before "
            "preparing the staging environment to ensure data consistency."
        )
        return 1
    logger.info("[1/9] Daemon check: not running ✓")

    # Step 2: Check disk space
    if not source_db.exists():
        logger.error(f"Source DB does not exist: {source_db}")
        return 1

    # Overlap check: staging_root must NOT resolve inside source_db parent, and
    # source_db must NOT resolve inside staging_root. Either would risk the
    # staging copy/cleanup clobbering the production DB.
    source_db_resolved = source_db.resolve()
    source_parent_resolved = source_db_resolved.parent
    staging_root_resolved = staging_root.resolve()
    # Only block if staging_root IS the source_db, or source_db is inside staging_root
    # (which would mean staging copy would recurse). Allowing staging_root inside
    # source_db parent is fine (e.g., data/staging inside data/).
    if staging_root_resolved == source_db_resolved:
        logger.error(f"SAFETY BLOCK: staging_root equals source_db ({staging_root_resolved})")
        return 1
    try:
        source_db_resolved.relative_to(staging_root_resolved)
        logger.error(
            f"SAFETY BLOCK: source_db ({source_db_resolved}) resolves inside "
            f"staging_root ({staging_root_resolved}). Refusing to prepare."
        )
        return 1
    except ValueError:
        pass  # source_db not inside staging_root, OK
    logger.info(f"[2/9] Staging/source overlap check passed ✓")

    source_size = source_db.stat().st_size
    required = source_size * 2  # staging DB + potential growth
    ok, free_bytes = check_disk_space(staging_root.parent, required)
    if not ok:
        logger.error(
            f"Insufficient disk space (or disk check failed): need ~{required / 1024**2:.0f} MB, "
            f"available {free_bytes / 1024**2:.0f} MB"
        )
        return 1
    logger.info(
        f"[2/9] Disk space: source={source_size / 1024**2:.0f} MB, "
        f"free={free_bytes / 1024**2:.0f} MB (>2x required) ✓"
    )

    # Step 3: Copy source DB to staging
    staging_db = staging_db_path(staging_root)
    if dry_run:
        logger.info(f"[3/9] [DRY-RUN] Would copy {source_db} -> {staging_db}")
    else:
        staging_root.mkdir(parents=True, exist_ok=True)
        logger.info(f"[3/9] Copying source DB to staging (this may take a while)...")
        shutil.copy2(source_db, staging_db)
        copied_size = staging_db.stat().st_size
        logger.info(f"[3/9] Copy complete: {staging_db} ({copied_size / 1024**2:.0f} MB) ✓")

        # Verify staging size == source size (fail-closed on truncated copy)
        if copied_size != source_size:
            logger.error(
                f"SAFETY BLOCK: staging DB size mismatch after copy: "
                f"staging={copied_size} source={source_size}. "
                f"Refusing to proceed with a potentially truncated copy."
            )
            return 1
        logger.info(f"[3/9] Size match verified: {copied_size} bytes ✓")

        # Verify SHA-256 of staging == source (fail-closed on silent corruption)
        def _sha256_local(path: Path) -> str:
            h = hashlib.sha256()
            with open(path, "rb") as _f:
                for chunk in iter(lambda: _f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()

        source_hash = _sha256_local(source_db)
        staging_hash = _sha256_local(staging_db)
        if source_hash != staging_hash:
            logger.error(
                f"SAFETY BLOCK: staging DB SHA-256 mismatch after copy: "
                f"source={source_hash} staging={staging_hash}. "
                f"Refusing to proceed with a corrupted copy."
            )
            return 1
        logger.info(f"[3/9] SHA-256 match verified: {staging_hash} ✓")

        # Write staging marker for safe --reset-staging
        marker = {
            "format_version": "1.0",
            "staging_root": str(staging_root.resolve()),
            "source_db": str(source_db.resolve()),
            "created_at": datetime.now().isoformat(),
            "tool": "backfill_fin_growth_dividend_staging",
        }
        staging_marker_path.write_text(
            json.dumps(marker, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        logger.info(f"[3/9] Staging marker written: {staging_marker_path} ✓")

    # Step 4: Create staging config directory
    staging_cfg_dir = staging_config_dir(staging_root)
    if dry_run:
        logger.info(f"[4/9] [DRY-RUN] Would create {staging_cfg_dir}")
    else:
        staging_cfg_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"[4/9] Config directory: {staging_cfg_dir} ✓")

    # Step 5: Copy config files
    source_config_dir = _PROJECT_ROOT / "config"
    for fname in REQUIRED_CONFIG_FILES:
        src = source_config_dir / fname
        dst = staging_cfg_dir / fname
        if not src.exists():
            logger.error(f"  Required config file not found: {src}")
            return 1
        if dry_run:
            logger.info(f"[5/9] [DRY-RUN] Would copy {src} -> {dst}")
        else:
            shutil.copy2(src, dst)
            logger.info(f"[5/9] Copied: {dst.name} ✓")

    # Step 6: Modify data_config.json to point to staging DB (absolute path)
    data_cfg_path = staging_cfg_dir / "data_config.json"
    if dry_run:
        logger.info(f"[6/9] [DRY-RUN] Would update {data_cfg_path} db_path to {staging_db}")
    elif not data_cfg_path.exists():
        logger.error(f"data_config.json not found in staging config: {data_cfg_path}")
        return 1
    else:
        cfg = json.loads(data_cfg_path.read_text(encoding="utf-8"))
        cfg["path"] = str(staging_db)
        data_cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        logger.info(f"[6/9] Updated data_config.json: path={staging_db} ✓")

    # Step 7: Set quarantine, batch_audit, log directories to staging-root/
    if dry_run:
        logger.info(f"[7/9] [DRY-RUN] Would set quarantine/batch_audit/logs paths to staging-root/")
    else:
        # Update quarantine path
        cfg = json.loads(data_cfg_path.read_text(encoding="utf-8"))
        if "quarantine" not in cfg:
            cfg["quarantine"] = {}
        cfg["quarantine"]["path"] = str(staging_root / "quarantine.db")

        data_cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        logger.info(f"[7/9] quarantine.db path set to staging-root/ ✓")

        # Create log directory
        log_dir = staging_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"[7/9] Log directory created: {log_dir} ✓")

    # Step 8: Update start_date in staging collector_tasks for allowed tasks
    tasks_path = staging_cfg_dir / "collector_tasks.json"
    if dry_run:
        logger.info(f"[8/9] [DRY-RUN] Would update {tasks_path} start_date={args.start_date} for fin_indicator/stock_dividend")
    elif tasks_path.exists():
        tasks_cfg = json.loads(tasks_path.read_text(encoding="utf-8"))
        updated = 0
        for t in tasks_cfg.get("tasks", []):
            if isinstance(t, dict) and t.get("table") in ALLOWED_TASKS:
                t["start_date"] = args.start_date
                updated += 1
        if updated > 0:
            tasks_path.write_text(json.dumps(tasks_cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            logger.info(f"[8/9] Updated {updated} task start_date(s) to {args.start_date} ✓")
        else:
            logger.warning(f"[8/9] No allowed tasks found in staging config to update start_date")

    # Step 9: Create empty SQLite ledger files (batch_audit.db, quarantine.db)
    batch_audit_path = staging_root / "batch_audit.db"
    quarantine_path = staging_root / "quarantine.db"
    if dry_run:
        logger.info(f"[9/9] [DRY-RUN] Would create empty SQLite: batch_audit.db, quarantine.db")
    else:
        # Create empty SQLite databases (NOT DuckDB)
        sqlite3.connect(str(batch_audit_path)).close()
        logger.info(f"[9/9] Created empty SQLite batch_audit.db: {batch_audit_path}")
        sqlite3.connect(str(quarantine_path)).close()
        logger.info(f"[9/9] Created empty SQLite quarantine.db: {quarantine_path}")

    logger.info("")
    logger.info("=== prepare phase complete ===")
    logger.info(f"Staging DB:   {staging_db}")
    logger.info(f"Staging cfg:  {staging_cfg_dir}")
    logger.info("")
    logger.info("Next steps:")
    logger.info("  python scripts/backfill_fin_growth_dividend_staging.py --run-task fin_indicator")
    logger.info("  python scripts/backfill_fin_growth_dividend_staging.py --run-task stock_dividend")
    logger.info("  python scripts/backfill_fin_growth_dividend_staging.py --audit")
    logger.info("  python scripts/backfill_fin_growth_dividend_staging.py --promote")
    return 0


# ===========================================================================
# Phase: run-task
# ===========================================================================
def phase_run_task(args: argparse.Namespace) -> int:
    """Run a single backfill task against the staging DB.

    Steps:
        1. Validate staging environment and BLOCK if config doesn't point to staging
        2. Verify ledger files exist (created during --prepare), DO NOT delete/recreate
        3. Set environment variable QUANTSTUDIO_DATA_ROOT to staging_root
        4. Create runtime_paths manifest
        5. Build and execute the daemon command with heartbeat + timeout
        6. Report result
    """
    staging_root = resolve_absolute(args.staging_root)
    task_name = args.run_task
    dry_run = args.dry_run
    timeout_sec = args.timeout_sec

    logger.info("=" * 72)
    logger.info(f"PHASE: run-task -- Backfill {task_name}")
    logger.info(f"  Staging root: {staging_root}")
    logger.info(f"  Timeout:      {timeout_sec}s")
    logger.info(f"  Dry run:      {dry_run}")
    logger.info("=" * 72)

    # Step 1: Validate staging DB path in config
    staging_cfg_dir = staging_config_dir(staging_root)
    data_cfg_path = staging_cfg_dir / "data_config.json"

    if not data_cfg_path.exists():
        logger.error(
            f"Staging config not found: {data_cfg_path}\n"
            f"  Run --prepare first to create the staging environment."
        )
        return 1

    cfg = json.loads(data_cfg_path.read_text(encoding="utf-8"))
    cfg_db_path = resolve_absolute(cfg.get("path", ""))

    staging_db = staging_db_path(staging_root)
    expected_db_path = str(staging_db.resolve())

    if cfg_db_path != staging_db:
        logger.error(
            f"SAFETY BLOCK: Config db_path does NOT point to staging DB.\n"
            f"  config says: {cfg_db_path}\n"
            f"  expected:    {expected_db_path}\n"
            f"  Refusing to run -- this ensures we never touch the production DB."
        )
        return 1

    if not staging_db.exists():
        logger.error(
            f"Staging DB not found: {staging_db}\n"
            f"  Run --prepare first to copy the production DB."
        )
        return 1

    # Step 1b: Block if DATA_ROOT from config doesn't match staging_root.
    config_data_root = cfg.get("data_root", "")
    if config_data_root:
        cfg_data_root_abs = resolve_absolute(config_data_root)
        if cfg_data_root_abs != staging_root:
            logger.error(
                f"SAFETY BLOCK: DATA_ROOT in config ({cfg_data_root_abs}) "
                f"does NOT match staging_root ({staging_root}). "
                f"Refusing to run to protect production data."
            )
            return 1

    logger.info(f"[1/7] Safety check: config db_path -> staging DB verified")

    # Step 2: Validate that daemon is NOT running on the staging DB (P0-2 fix)
    should_block, reason = check_daemon_and_collector_locks(staging_root)
    if should_block:
        logger.error(f"BLOCK: {reason}. Stop it before running a backfill task.")
        return 1
    logger.info(f"[2/7] No live daemon/collector detected")

    # W2-0.9 缺陷 C/F：preflight — 启动 daemon 前独占读写打开 staging.db 并立即关闭，
    # 确认无 git.exe/IDE/GUI/杀毒持有文件锁。任何 file-in-use IO error 立即返回非零，
    # 不得进入任务，不得用失败率容忍。
    try:
        _pf_conn = duckdb.connect(str(staging_db))
        _pf_conn.execute("SELECT 1").fetchall()
        _pf_conn.close()
    except Exception as e:
        logger.error(
            f"BLOCK: staging.db preflight (read-write open) failed: {e}. "
            f"Another process (git.exe/IDE/GUI/AV) likely holds the file. "
            f"Refusing to start the task. Resolve the file lock (move staging "
            f"outside the Git worktree) and retry.")
        return 1
    logger.info(f"[2/7] staging.db preflight (read-write open/close) OK ✓")

    # Step 3: Verify ledger files exist (created during --prepare), DO NOT delete
    batch_audit_path = staging_root / "batch_audit.db"
    quarantine_path = staging_root / "quarantine.db"
    logs_dir = staging_root / "logs"
    collector_lock_path = staging_root / ".collector_run.lock"

    if not dry_run:
        # P0-1: Verify ledger files exist, create if missing (should have been created during prepare)
        if not batch_audit_path.exists():
            logger.warning(f"batch_audit.db not found, creating empty SQLite: {batch_audit_path}")
            sqlite3.connect(str(batch_audit_path)).close()
        else:
            logger.info(f"  Using existing batch_audit.db: {batch_audit_path}")

        if not quarantine_path.exists():
            logger.warning(f"quarantine.db not found, creating empty SQLite: {quarantine_path}")
            sqlite3.connect(str(quarantine_path)).close()
        else:
            logger.info(f"  Using existing quarantine.db: {quarantine_path}")

        logs_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"  Logs directory: {logs_dir}")

        # Update config with quarantine path and collector lock path
        cfg = json.loads(data_cfg_path.read_text(encoding="utf-8"))
        if "quarantine" not in cfg:
            cfg["quarantine"] = {}
        cfg["quarantine"]["path"] = str(quarantine_path)
        cfg[".collector_run.lock"] = str(collector_lock_path)
        data_cfg_path.write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        logger.info(f"  Updated config: quarantine.path, .collector_run.lock")
    else:
        logger.info(
            f"[DRY-RUN] Would verify/create: batch_audit.db, quarantine.db, "
            f"logs/, .collector_run.lock in config"
        )

    # Step 4: Set up environment with QUANTSTUDIO_DATA_ROOT
    env = os.environ.copy()
    env["QUANTSTUDIO_DATA_ROOT"] = str(staging_root.resolve())
    logger.info(f"[3/7] Env QUANTSTUDIO_DATA_ROOT={staging_root.resolve()}")

    # Step 5: Runtime manifest -- the daemon subprocess writes it atomically after
    # real component construction (Change 1 in daemon.py). The parent only stages
    # the target path + a nonce for replay protection, then deletes any stale copy
    # so a missing post-run manifest unambiguously means BLOCK.
    log_file = logs_dir / f"backfill_{task_name}.log"
    manifest_path = staging_root / f"runtime_paths_{task_name}.json"
    import uuid as _uuid
    nonce = str(_uuid.uuid4())
    if not dry_run:
        manifest_path.unlink(missing_ok=True)  # delete any old manifest before launch
        logger.info(f"[4/7] Cleared stale runtime manifest if present: {manifest_path}")
    else:
        logger.info(f"[4/7] [DRY-RUN] Would clear stale runtime manifest: {manifest_path}")

    # Step 6: Build and execute the daemon command
    # --quality-audit none: staging 是分阶段装载（fin_indicator 与 stock_dividend 分两轮），
    # 单任务完成时不跑全库审计——尚未回填的兄弟目标表不应导致当前任务假失败。
    # 最终统一审计由 staging Phase 6（--audit，含 baseline-delta 语义）执行。
    cmd = [
        sys.executable,
        "-m", "quantstudio.pipeline.daemon",
        "--mode", "once",
        "--task", task_name,
        "--pull-mode", "full_range",
        "--quality-audit", "none",
        "--config-dir", str(staging_cfg_dir),
        "--runtime-manifest", str(manifest_path),
        "--runtime-nonce", nonce,
    ]

    logger.info(f"[5/7] Running backfill for {task_name} (full_range)...")
    logger.info(f"  Command: {' '.join(cmd)}")
    logger.info(f"  Log file: {log_file}")
    logger.info("  This may take a long time (tens of minutes to hours).")
    logger.info("  Output will stream to console and log file simultaneously.")

    result = run_cmd(
        cmd,
        cwd=_PROJECT_ROOT,
        dry_run=dry_run,
        env=env,
        timeout=timeout_sec,
        log_file=log_file if not dry_run else None,
        staging_db=staging_db,
        task_name=task_name,
    )

    # Step 7: Report result and verify runtime manifests
    if result.exit_code == 0:
        logger.info(f"[6/7] Task {task_name} completed successfully (exit_code=0, pid={result.pid})")
    else:
        logger.error(f"[6/7] Task {task_name} FAILED with exit_code={result.exit_code}")
        if not dry_run and log_file.exists():
            logger.error(f"  Full log: {log_file}")
        return 1

    # Step 7b: Verify subprocess runtime manifest via the unified validator.
    # run-task is the only call site with a real parent->child relationship, so
    # it pins the manifest PID to the actual Popen PID and brackets created_at
    # inside the child's [started_at, finished_at] lifetime window.
    manifest_path = staging_root / f"runtime_paths_{task_name}.json"
    if not manifest_path.exists():
        logger.error(f"BLOCK: runtime manifest missing: {manifest_path}")
        return 1
    try:
        runtime_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"BLOCK: runtime manifest corrupt: {e}")
        return 1

    ok, reason = validate_runtime_manifest(
        runtime_manifest,
        staging_root=staging_root,
        staging_cfg_dir=staging_cfg_dir,
        expected_task=task_name,
        expected_nonce=nonce,
        expected_pid=result.pid,
        time_window=(result.started_at, result.finished_at),
        require_pid_match=True,
    )
    if not ok:
        logger.error(f"BLOCK: runtime manifest validation FAILED: {reason}")
        return 1
    logger.info(
        f"[7/7] Runtime manifest verified: pid={result.pid} nonce={nonce} "
        f"created_at={runtime_manifest.get('created_at')} within "
        f"[{result.started_at}, {result.finished_at}]"
    )

    # Cross-check the batch ledger final status (W2-0.8 缺陷 D): the daemon CLI
    # now returns non-zero on task failure, but the parent independently verifies
    # the ledger carries a `success` batch for this task. A `failed`/missing batch
    # BLOCKs even if the subprocess somehow exited 0.
    batch_audit_path = staging_root / "batch_audit.db"
    ledger_status_ok = False
    ledger_detail = ""
    if batch_audit_path.exists():
        try:
            import sqlite3 as _sqlite3
            with contextlib.closing(_sqlite3.connect(str(batch_audit_path))) as _conn:
                _row = _conn.execute(
                    "SELECT status, batch_id, rows_written FROM batch_audit "
                    "WHERE task_name = ? OR table_name = ? "
                    "ORDER BY finished_at DESC LIMIT 1",
                    (task_name, task_name),
                ).fetchone()
            if _row is None:
                ledger_detail = f"no batch_audit row for task={task_name}"
            else:
                ledger_status_ok = (_row[0] == "success")
                ledger_detail = f"status={_row[0]} batch_id={_row[1]} rows_written={_row[2]}"
        except Exception as e:
            ledger_detail = f"ledger read error: {e}"
    else:
        ledger_detail = "batch_audit.db not found"
    if not ledger_status_ok:
        logger.error(
            f"BLOCK: batch ledger final status is not 'success' for {task_name} "
            f"({ledger_detail}). Refusing to treat the task as complete."
        )
        return 1
    logger.info(f"[7/7] Batch ledger verified: {ledger_detail}")

    logger.info("")
    logger.info("=== run-task phase complete ===")
    logger.info("Next: python scripts/backfill_fin_growth_dividend_staging.py --audit")
    return 0


# ===========================================================================
# Unified strict evidence validator (P0-4)
# ===========================================================================
def validate_audit_evidence(
    evidence: Dict,
    staging_db: Path,
    source_db: Path,
    staging_cfg_dir: Path,
    expected_profile_version: str = "1.10.0",
    staging_root: Optional[Path] = None,
    marker_created_at: Optional[str] = None,
) -> Tuple[bool, str]:
    """Validate audit evidence with fail-closed semantics.

    Returns (True, "") if all checks pass, or (False, "reason") if any fail.
    Used by both phase_audit (post-audit validation) and phase_promote
    (pre-promotion gate).

    When `staging_root` is provided (audit/promote), the two runtime manifests
    on disk are re-read and hashed; their content must match the manifest
    content captured in the evidence, and each must pass the unified
    `validate_runtime_manifest` path/layout check. Any on-disk drift (deletion,
    edit, replacement) after audit BLOCKS promotion.
    """

    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    # --- 1. staging_db_path/size/sha256 present and match actual ---
    ev_staging_path = evidence.get("staging_db_path")
    if not ev_staging_path:
        return False, "staging_db_path missing in evidence"
    if Path(ev_staging_path).resolve() != staging_db.resolve():
        return False, f"staging_db_path mismatch: evidence={ev_staging_path} actual={staging_db}"

    ev_staging_size = evidence.get("staging_db_size")
    actual_staging_size = staging_db.stat().st_size if staging_db.exists() else 0
    if ev_staging_size != actual_staging_size:
        return False, f"staging_db_size mismatch: evidence={ev_staging_size} actual={actual_staging_size}"

    ev_staging_hash = evidence.get("staging_db_sha256")
    if not ev_staging_hash:
        return False, "staging_db_sha256 missing in evidence"
    actual_staging_hash = _sha256(staging_db) if staging_db.exists() else ""
    if ev_staging_hash != actual_staging_hash:
        return False, f"staging_db_sha256 mismatch: evidence={ev_staging_hash} actual={actual_staging_hash}"

    # --- 2. source_db_path/size present; hash recomputed and matched ---
    ev_source_path = evidence.get("source_db_path")
    if not ev_source_path:
        return False, "source_db_path missing in evidence"
    if Path(ev_source_path).resolve() != source_db.resolve():
        return False, f"source_db_path mismatch: evidence={ev_source_path} actual={source_db}"

    ev_source_size = evidence.get("source_db_size")
    actual_source_size = source_db.stat().st_size if source_db.exists() else 0
    if ev_source_size != actual_source_size:
        return False, f"source_db_size mismatch: evidence={ev_source_size} actual={actual_source_size}"

    ev_source_hash = evidence.get("source_db_sha256")
    if not ev_source_hash:
        return False, "source_db_sha256 missing in evidence"
    actual_source_hash = _sha256(source_db) if source_db.exists() else ""
    if ev_source_hash != actual_source_hash:
        return False, f"source_db_sha256 mismatch: evidence={ev_source_hash} actual={actual_source_hash}"

    # --- 3. 4 config files with non-None sha256 matching actual files ---
    config_hashes = evidence.get("config_hashes", {})
    for fname in REQUIRED_CONFIG_FILES:
        fhash = config_hashes.get(fname)
        if fhash is None:
            return False, f"config file {fname} hash is None/missing in evidence"
        fp = staging_cfg_dir / fname
        if not fp.exists():
            return False, f"config file {fname} not found on disk: {fp}"
        actual_fhash = _sha256(fp)
        if fhash != actual_fhash:
            return False, f"config file {fname} hash mismatch: evidence={fhash} actual={actual_fhash}"

    # --- 4. Version separation: data_schema_version + ptrade_profile_version ---
    # data_schema_version: from alignment_rules.json schema_version (typically "2.0")
    data_schema_version = evidence.get("data_schema_version")
    if not data_schema_version:
        return False, "data_schema_version missing in evidence"
    if data_schema_version != "2.0":
        return False, f"data_schema_version mismatch: evidence={data_schema_version} expected='2.0'"
    # ptrade_profile_version: from ptrade-api-signatures.json profile_version (must be "1.10.0")
    ptrade_profile_version = evidence.get("ptrade_profile_version")
    if ptrade_profile_version != expected_profile_version:
        return False, f"ptrade_profile_version mismatch: evidence={ptrade_profile_version} expected={expected_profile_version}"

    # --- 5. authority_rules has fin_indicator and stock_dividend, both with source=tushare, fallback=false ---
    authority_rules = evidence.get("authority_rules", {})
    for task_name in ("fin_indicator", "stock_dividend"):
        rule = authority_rules.get(task_name)
        if not rule:
            return False, f"authority_rules missing entry for {task_name}"
        if rule.get("authoritative_source") != "tushare":
            return False, f"authority_rules {task_name}.authoritative_source != 'tushare': {rule.get('authoritative_source')}"
        if rule.get("allow_fallback") is not False:
            return False, f"authority_rules {task_name}.allow_fallback is not false: {rule.get('allow_fallback')}"

    # --- 5b. audit checks validation ---
    # W2-0.9 Phase 5 重做：不再要求 audit.errors_count == 0（源库既有 balance_statement
    # 等无关错误会被 staging 继承，强行要求 0 会阻断）。改为：
    #   - checks_run > 0（审计确实运行了）
    #   - baseline_delta_passed == True（数据质量门禁由 baseline-delta 决定：
    #     目标表零 error，非目标表允许继承，new/regressed BLOCK）
    audit = evidence.get("audit", {})
    if audit.get("checks_run", 0) <= 0:
        return False, "audit.checks_run must be > 0"
    if evidence.get("baseline_delta_passed") is not True:
        return False, ("baseline_delta_passed must be true (target_table_errors empty, "
                       "no new/regressed errors). Inherited non-target errors from the "
                       "source baseline are allowed; raw audit.errors_count is NOT required "
                       f"to be 0. Got baseline_delta_passed={evidence.get('baseline_delta_passed')!r}")

    # --- 6. Runtime manifests: full re-validation (content + path layout +
    # on-disk drift). When staging_root is provided we re-read each manifest
    # file from disk and require its SHA-256 + parsed content to exactly match
    # what the evidence captured at audit time. Any post-audit drift BLOCKs. ---
    runtime_manifests = evidence.get("runtime_paths", {})
    manifest_count = len(runtime_manifests)
    if manifest_count < 2:
        return False, f"Need at least 2 runtime manifests, found {manifest_count}"
    evidence_manifest_hashes = evidence.get("runtime_manifest_hashes", {}) or {}

    for fname in ("runtime_paths_fin_indicator.json", "runtime_paths_stock_dividend.json"):
        manifest = runtime_manifests.get(fname)
        if not manifest or not isinstance(manifest, dict) or len(manifest) == 0:
            return False, f"runtime manifest {fname} is empty or invalid in evidence"
        # Full unified layout/task/nonce/path check (no PID window here: audit/
        # promote re-check on-disk artifacts without a parent-child relationship).
        if staging_root is not None:
            ok2, why = validate_runtime_manifest(
                manifest,
                staging_root=staging_root,
                staging_cfg_dir=staging_cfg_dir,
            )
            if not ok2:
                return False, f"runtime manifest {fname} failed unified check: {why}"
        # On-disk drift check: the file must still exist, its hash must match
        # the audit-time hash captured in evidence, and its content must equal
        # the evidence-embedded content byte-for-byte.
        if staging_root is not None:
            disk_path = staging_root / fname
            if not disk_path.exists():
                return False, f"runtime manifest {fname} missing on disk at {disk_path}"
            disk_hash = _sha256(disk_path)
            ev_hash = evidence_manifest_hashes.get(fname)
            if not ev_hash:
                return False, f"runtime manifest {fname} hash missing in evidence"
            if disk_hash != ev_hash:
                return False, (f"runtime manifest {fname} on-disk hash drift: "
                               f"disk={disk_hash} evidence={ev_hash}")
            try:
                disk_content = json.loads(disk_path.read_text(encoding="utf-8"))
            except Exception as e:
                return False, f"runtime manifest {fname} unreadable on disk: {e}"
            if disk_content != manifest:
                return False, f"runtime manifest {fname} on-disk content != evidence content"

    # --- 7. ledger readable ---
    batch_counts = evidence.get("batch_counts", {})
    if not batch_counts:
        return False, "batch_counts missing in evidence (ledger not readable)"

    # --- 7b. Batch conservation: rows_passed + rows_rejected <= rows_raw per task ---
    # 守恒口径：DataValidator 在分流为 passed/rejected 之前，会先对主键做
    # drop_duplicates 去重（validator.py），全量回填时 API 返回的历史重复行被合并，
    # 这部分既不计入 passed 也不计入 rejected，属合法收敛。因此正确的守恒是
    # "passed + rejected <= raw"（去重只会减少行数，不会增加），而不是严格的相等。
    # 同时校验入库一致性：rows_written 必须等于 rows_passed（通过的行应全部入库）。
    for task_name in ("fin_indicator", "stock_dividend"):
        bc = batch_counts.get(task_name, {})
        rows_raw = bc.get("rows_raw")
        rows_passed = bc.get("rows_passed")
        rows_rejected = bc.get("rows_rejected")
        rows_written = bc.get("rows_written")
        if rows_raw is None or rows_passed is None or rows_rejected is None:
            return False, f"{task_name}: batch counts rows_raw/rows_passed/rows_rejected missing (got raw={rows_raw}, passed={rows_passed}, rejected={rows_rejected})"
        try:
            rows_raw = int(rows_raw)
            rows_passed = int(rows_passed)
            rows_rejected = int(rows_rejected)
            rows_written = int(rows_written) if rows_written is not None else None
        except (TypeError, ValueError):
            return False, f"{task_name}: batch counts rows_raw/rows_passed/rows_rejected not integers (got raw={rows_raw!r}, passed={rows_passed!r}, rejected={rows_rejected!r})"
        if rows_raw < 0 or rows_passed < 0 or rows_rejected < 0:
            return False, f"{task_name}: batch counts must be >= 0 (got raw={rows_raw}, passed={rows_passed}, rejected={rows_rejected})"
        if rows_passed + rows_rejected > rows_raw:
            return False, f"{task_name}: rows_passed({rows_passed}) + rows_rejected({rows_rejected}) > rows_raw({rows_raw}) (passed+rejected cannot exceed raw; dedup only reduces)"
        if rows_written is not None and rows_written != rows_passed:
            return False, f"{task_name}: rows_written({rows_written}) != rows_passed({rows_passed}) (written must equal passed)"

    # --- 8. Batch ID uniqueness + EXACT task set + required fields + timing.
    # The two batches must cover exactly {fin_indicator, stock_dividend}, each
    # batch must carry rows_written/rows_new/rows_updated >= 0 and started_at/
    # finished_at, and finished_at must not predate the staging marker. ---
    batch_ids = evidence.get("batch_ids", [])
    if len(batch_ids) < 2:
        return False, f"batch_ids has {len(batch_ids)} entries, need at least 2"
    batch_ids_seen = set()
    batch_tasks_seen = set()
    marker_dt = _parse_iso(marker_created_at) if marker_created_at else None
    for bid in batch_ids:
        bid_id = bid.get("batch_id")
        if not bid_id:
            return False, "batch entry has empty batch_id"
        if bid_id in batch_ids_seen:
            return False, f"duplicate batch_id: {bid_id}"
        batch_ids_seen.add(bid_id)
        task = bid.get("table") or bid.get("task") or bid.get("table_name") or bid.get("task_name")
        if not task:
            return False, f"batch {bid_id} missing task/table"
        if task in batch_tasks_seen:
            return False, f"two batches for same task {task}"
        if task not in ("fin_indicator", "stock_dividend"):
            return False, f"batch {bid_id} has unexpected task {task}"
        batch_tasks_seen.add(task)
        if bid.get("status") != "success":
            return False, f"batch {bid_id} status is {bid.get('status')}, expected success"
        if bid.get("source") != "tushare":
            return False, f"batch {bid_id} source is {bid.get('source')}, expected tushare"
        # Required numeric fields present and non-negative
        for fld in ("rows_written", "rows_new", "rows_updated"):
            v = bid.get(fld)
            if v is None:
                return False, f"batch {bid_id} missing {fld}"
            try:
                iv = int(v)
            except (TypeError, ValueError):
                return False, f"batch {bid_id} {fld}={v!r} not an int"
            if iv < 0:
                return False, f"batch {bid_id} {fld}={iv} must be >= 0"
        # Timing fields present and consistent with staging marker
        started = bid.get("started_at")
        finished = bid.get("finished_at")
        if not started or not finished:
            return False, f"batch {bid_id} missing started_at/finished_at"
        finished_dt = _parse_iso(finished)
        if finished_dt is None:
            return False, f"batch {bid_id} finished_at unparseable: {finished!r}"
        if marker_dt is not None and finished_dt < marker_dt:
            return False, (f"batch {bid_id} finished_at {finished_dt} predates "
                           f"staging marker created_at {marker_dt}")
    # Exact task-set check (not merely "no duplicate task")
    if batch_tasks_seen != {"fin_indicator", "stock_dividend"}:
        return False, f"batch task set {batch_tasks_seen} != {{fin_indicator, stock_dividend}}"

    # --- 9. growth backfill effectiveness: fin_indicator must have > 0 rows and
    # every growth field (np_yoy/or_yoy/tr_yoy) must have > 0 non-null values.
    # An all-zero backfill produced no usable growth data and must BLOCK. ---
    growth_stats = evidence.get("growth_field_stats", {})
    fin_row_count = evidence.get("fin_indicator_row_count", 0)
    try:
        fin_row_count = int(fin_row_count)
    except (TypeError, ValueError):
        fin_row_count = 0
    if fin_row_count <= 0:
        return False, f"fin_indicator_row_count must be > 0, got {fin_row_count}"
    for key in ("np_yoy_non_null", "or_yoy_non_null", "tr_yoy_non_null"):
        val = growth_stats.get(key)
        if val is None:
            return False, f"growth_field_stats missing {key}"
        try:
            ival = int(val)
        except (TypeError, ValueError):
            return False, f"growth_field_stats {key}={val!r} not an int"
        if ival <= 0:
            return False, f"growth_field_stats {key}={ival} must be > 0 (empty backfill)"

    # --- 10. dividend backfill effectiveness: stock_dividend must have > 0 rows
    # and both cash_div_before_tax + cash_div_after_tax must have > 0 non-null.
    # stk_div_non_null (and the stk_bo/stk_co rate counts, if present) only need
    # to exist as non-negative integers (a backfill with no stock dividends is
    # legitimate; a backfill with no cash dividends is not). ---
    dividend_stats = evidence.get("dividend_stats", {})
    div_row_count = evidence.get("stock_dividend_row_count", 0)
    try:
        div_row_count = int(div_row_count)
    except (TypeError, ValueError):
        div_row_count = 0
    if div_row_count <= 0:
        return False, f"stock_dividend_row_count must be > 0, got {div_row_count}"
    for key in ("cash_div_before_tax_non_null", "cash_div_after_tax_non_null"):
        val = dividend_stats.get(key)
        if val is None:
            return False, f"dividend_stats missing {key}"
        try:
            ival = int(val)
        except (TypeError, ValueError):
            return False, f"dividend_stats {key}={val!r} not an int"
        if ival <= 0:
            return False, f"dividend_stats {key}={ival} must be > 0 (empty backfill)"
    for key in ("stk_div_non_null", "stk_bo_rate_non_null", "stk_co_rate_non_null"):
        if key in dividend_stats:
            try:
                ival = int(dividend_stats[key])
            except (TypeError, ValueError):
                return False, f"dividend_stats {key}={dividend_stats[key]!r} not an int"
            if ival < 0:
                return False, f"dividend_stats {key}={ival} must be >= 0"

    # --- 11. watermark has both tasks ---
    watermark_info = evidence.get("watermark_info", {})
    wm_entries = watermark_info.get("entries", [])
    wm_tables = {e.get("table") for e in wm_entries}
    for task_name in ("fin_indicator", "stock_dividend"):
        if task_name not in wm_tables:
            return False, f"watermark_info missing entry for {task_name}"

    # --- 12. audit_time exists ---
    if not evidence.get("audit_time"):
        return False, "audit_time missing in evidence"

    # --- 13. passed is True ---
    # W2-0.9 Phase 5：evidence.passed 是结构收集成功的标记（无 evidence_errors），
    # 不再代表"raw staging audit 零 error"。数据质量 PASS 由 baseline_delta_passed
    # 决定（已在 5b 校验）。这里只确认结构收集本身没有 error。
    if evidence.get("passed") is not True:
        return False, (f"evidence.passed is not True (structural evidence collection "
                       f"had errors): {evidence.get('passed')}")

    return True, ""


# ===========================================================================
# Baseline-delta audit (W2-0.8 Phase 5)
# ===========================================================================
def _issue_key(issue) -> tuple:
    """Stable key for a QualityIssue: (table, check). Used for delta comparison."""
    return (getattr(issue, "table", ""), getattr(issue, "check", ""))


def run_baseline_delta_audit(
    staging_db: Path,
    source_db: Path,
    schemas: Dict,
    authority_rules: Dict,
    target_tables: tuple = ("fin_indicator", "stock_dividend"),
    source_batch_audit: Optional[Path] = None,
    source_quarantine: Optional[Path] = None,
    staging_batch_audit: Optional[Path] = None,
    staging_quarantine: Optional[Path] = None,
) -> Tuple[bool, str, Dict]:
    """W2-0.8 Phase 5 baseline-delta audit.

    Runs the same DataQualityAuditor against the source DB (baseline) and the
    staging DB, then classifies staging errors into:
      - target_table_errors: errors on fin_indicator/stock_dividend (must be [])
      - inherited_unchanged_errors: identical (table,check) errors present in BOTH
        source and staging on non-target tables (allowed)
      - new_errors: staging errors not present in source baseline (BLOCK)
      - regressed_errors: staging errors whose count increased vs source (BLOCK)

    Returns (passed, reason, delta_dict).
    PASS requires: target_table_errors == [], new_errors == [], regressed_errors == [].
    Baseline/source errors are NEVER silently deleted — they are recorded.
    """
    from quantstudio.pipeline.quality_audit import DataQualityAuditor

    def _run(db: Path, bap, qp) -> list:
        aud = DataQualityAuditor(
            db, schemas,
            batch_audit_path=bap if (bap and Path(bap).exists()) else None,
            quarantine_path=qp if (qp and Path(qp).exists()) else None,
            authority_rules=authority_rules,
        )
        rep = aud.run()
        return [i for i in rep.issues if i.severity == "error"]

    # Baseline (source DB, read-only)
    try:
        baseline_errors = _run(source_db, source_batch_audit, source_quarantine)
    except Exception as e:
        return False, f"baseline (source) audit failed: {e}", {}
    baseline_map = {}
    for iss in baseline_errors:
        k = _issue_key(iss)
        baseline_map[k] = baseline_map.get(k, 0) + (getattr(iss, "count", 1) or 1)

    # Staging
    try:
        staging_errors = _run(staging_db, staging_batch_audit, staging_quarantine)
    except Exception as e:
        return False, f"staging audit failed: {e}", {}
    staging_map = {}
    for iss in staging_errors:
        k = _issue_key(iss)
        staging_map[k] = staging_map.get(k, 0) + (getattr(iss, "count", 1) or 1)

    target_table_errors = [
        {"table": k[0], "check": k[1], "count": v}
        for k, v in staging_map.items() if k[0] in target_tables
    ]
    new_errors = [
        {"table": k[0], "check": k[1], "count": v}
        for k, v in staging_map.items() if k not in baseline_map
        and k[0] not in target_tables
    ]
    regressed_errors = [
        {"table": k[0], "check": k[1], "count": v, "baseline_count": baseline_map[k]}
        for k, v in staging_map.items()
        if k in baseline_map and v > baseline_map[k] and k[0] not in target_tables
    ]
    inherited_unchanged = [
        {"table": k[0], "check": k[1], "count": v, "baseline_count": baseline_map[k]}
        for k, v in staging_map.items()
        if k in baseline_map and v <= baseline_map[k] and k[0] not in target_tables
    ]

    delta = {
        "target_table_errors": target_table_errors,
        "new_errors": new_errors,
        "regressed_errors": regressed_errors,
        "inherited_unchanged_errors": inherited_unchanged,
        "source_baseline_error_keys": [
            {"table": k[0], "check": k[1], "count": v} for k, v in baseline_map.items()
        ],
        "staging_error_keys": [
            {"table": k[0], "check": k[1], "count": v} for k, v in staging_map.items()
        ],
    }
    passed = (len(target_table_errors) == 0 and len(new_errors) == 0
              and len(regressed_errors) == 0)
    if not passed:
        reason_parts = []
        if target_table_errors:
            reason_parts.append(f"target_table_errors={len(target_table_errors)}")
        if new_errors:
            reason_parts.append(f"new_errors={len(new_errors)}")
        if regressed_errors:
            reason_parts.append(f"regressed_errors={len(regressed_errors)}")
        reason = "baseline-delta audit FAILED: " + ", ".join(reason_parts)
    else:
        reason = ""
    return passed, reason, delta


def phase_audit(args: argparse.Namespace) -> int:
    """Run DataQualityAuditor against the staging DB.

    Steps:
        1. Connect to staging DB (read-only)
        2. Read alignment_rules for schemas
        3. Build authority rules via daemon.build_authority_rules()
        4. Construct DataQualityAuditor with authority_rules
        5. Run audit
        6. Print results and write audit_evidence.json
    """
    staging_root = resolve_absolute(args.staging_root)
    dry_run = args.dry_run
    source_db = resolve_absolute(args.source_db)

    logger.info("=" * 72)
    logger.info("PHASE: audit -- Quality audit on staging DB")
    logger.info(f"  Staging root: {staging_root}")
    logger.info(f"  Source DB:    {source_db}")
    logger.info(f"  Dry run:      {dry_run}")
    logger.info("=" * 72)

    staging_db = staging_db_path(staging_root)
    staging_cfg_dir = staging_config_dir(staging_root)

    if not staging_db.exists():
        logger.error(f"Staging DB not found: {staging_db}")
        return 1

    alignment_rules_path = staging_cfg_dir / "alignment_rules.json"
    if not alignment_rules_path.exists():
        logger.error(f"alignment_rules.json not found: {alignment_rules_path}")
        return 1

    collector_tasks_path = staging_cfg_dir / "collector_tasks.json"
    if not collector_tasks_path.exists():
        logger.error(f"collector_tasks.json not found: {collector_tasks_path}")
        return 1

    logger.info(f"[1/4] Staging DB:       {staging_db}")
    logger.info(f"[1/4] alignment_rules:  {alignment_rules_path}")
    logger.info(f"[1/4] collector_tasks:  {collector_tasks_path}")
    logger.info("[1/4] Files verified ✓")

    if dry_run:
        logger.info("[2/4] [DRY-RUN] Would read configs and run audit")
        logger.info("=== audit phase complete (dry-run) ===")
        return 0

    # Step 2: Read configs and build authority rules
    logger.info("[2/4] Reading configuration files...")
    alignment_rules = json.loads(alignment_rules_path.read_text(encoding="utf-8"))
    schemas = alignment_rules.get("schemas", {})
    data_schema_version = alignment_rules.get("schema_version", "unknown")
    logger.info(f"  Schemas defined: {sorted(schemas.keys())}")
    logger.info(f"  Data schema version: {data_schema_version}")

    # PTrade profile version: read from ptrade-api-signatures.json (separate from data schema)
    ptrade_profile_version = "unknown"
    ptrade_sig_candidates = [
        _PROJECT_ROOT / "skills" / "quantstudio-strategy-compiler" / "references" / "ptrade-api-signatures.json",
    ]
    for cand in ptrade_sig_candidates:
        try:
            if cand.exists():
                ptrade_cfg = json.loads(cand.read_text(encoding="utf-8"))
                ptrade_profile_version = ptrade_cfg.get("profile_version", "unknown")
                logger.info(f"  PTrade profile version: {ptrade_profile_version} (from {cand})")
                break
        except Exception as e:
            logger.warning(f"  Failed to read {cand}: {e}")
    if ptrade_profile_version == "unknown":
        logger.warning(f"  ptrade-api-signatures.json not found in candidates; ptrade_profile_version=unknown")

    tasks_cfg = json.loads(collector_tasks_path.read_text(encoding="utf-8"))

    # Build authority rules via the canonical daemon function (not reimplemented)
    from quantstudio.pipeline.daemon import build_authority_rules
    authority_rules = build_authority_rules(tasks_cfg)
    logger.info(f"  Authority-locked tables: {sorted(authority_rules.keys())}")

    # Step 3: Construct and run DataQualityAuditor
    logger.info("[3/4] Running DataQualityAuditor...")
    from quantstudio.pipeline.quality_audit import DataQualityAuditor

    quarantine_path = staging_root / "quarantine.db"
    batch_audit_path = staging_root / "batch_audit.db"

    auditor = DataQualityAuditor(
        staging_db,
        schemas,
        batch_audit_path=batch_audit_path if batch_audit_path.exists() else None,
        quarantine_path=quarantine_path if quarantine_path.exists() else None,
        authority_rules=authority_rules,
    )
    report = auditor.run()

    # Step 4: Print results
    logger.info("[4/4] Audit results:")
    logger.info(f"  Checks run:  {report.checks_run}")
    logger.info(f"  Passed:      {report.passed}")
    logger.info(f"  Issues:      {len(report.issues)}")

    errors = [i for i in report.issues if i.severity == "error"]
    warnings = [i for i in report.issues if i.severity == "warning"]

    if errors:
        logger.error(f"  ERRORS ({len(errors)}):")
        for issue in errors[:30]:
            logger.error(f"    [{issue.severity}] {issue.table}/{issue.check}: "
                         f"count={issue.count} {issue.detail}")
        if len(errors) > 30:
            logger.error(f"    ... and {len(errors) - 30} more errors")

    if warnings:
        logger.warning(f"  WARNINGS ({len(warnings)}):")
        for issue in warnings[:20]:
            logger.warning(f"    [{issue.severity}] {issue.table}/{issue.check}: "
                           f"count={issue.count} {issue.detail}")
        if len(warnings) > 20:
            logger.warning(f"    ... and {len(warnings) - 20} more warnings")

    # Build and write audit evidence (always written, even on failure)
    evidence = _build_audit_evidence(
        staging_db=staging_db,
        source_db=source_db,
        staging_root=staging_root,
        staging_cfg_dir=staging_cfg_dir,
        alignment_rules=alignment_rules,
        tasks_cfg=tasks_cfg,
        authority_rules=authority_rules,
        report=report,
        data_schema_version=data_schema_version,
        ptrade_profile_version=ptrade_profile_version,
    )

    evidence_path = staging_root / "audit_evidence.json"
    evidence_path.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    logger.info(f"  audit_evidence.json written (structural): {evidence_path}")

    # Read staging marker created_at (used to gate batch finished_at timing)
    marker_created_at = None
    marker_path = staging_root / ".quantstudio_staging.json"
    if marker_path.exists():
        try:
            marker_created_at = json.loads(
                marker_path.read_text(encoding="utf-8")).get("created_at")
        except Exception:
            marker_created_at = None

    # W2-0.9 Phase 5 重做：baseline-delta audit 必须在 strict validation 之前运行并
    # 写入 evidence（strict validator 现在依赖 baseline_delta_passed）。
    # source 与 staging 使用完全相同的 schemas/authority_rules/check 集合（同一 auditor）。
    # 目标表（fin_indicator/stock_dividend）必须零 error；非目标表允许继承源库既有
    # error（同 key 同量）；new/regressed/severity-upgrade BLOCK。
    quarantine_path = staging_root / "quarantine.db"
    batch_audit_path = staging_root / "batch_audit.db"
    delta_passed, delta_reason, delta = run_baseline_delta_audit(
        staging_db=staging_db,
        source_db=source_db,
        schemas=schemas,
        authority_rules=authority_rules,
        staging_batch_audit=batch_audit_path,
        staging_quarantine=quarantine_path,
    )
    # Re-write evidence with the delta embedded (always recorded, even on fail)
    evidence["baseline_delta_audit"] = delta
    evidence["baseline_delta_passed"] = delta_passed
    evidence_path.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    if not delta_passed:
        logger.error(f"Baseline-delta audit FAILED: {delta_reason}")
        logger.error(f"  target_table_errors: {len(delta.get('target_table_errors', []))}")
        logger.error(f"  new_errors: {len(delta.get('new_errors', []))}")
        logger.error(f"  regressed_errors: {len(delta.get('regressed_errors', []))}")
        logger.error(f"  inherited_unchanged_errors (allowed): "
                     f"{len(delta.get('inherited_unchanged_errors', []))}")
        # delta 失败：evidence 已含 delta 字段，继续走 strict validation（会因
        # baseline_delta_passed=false 而 BLOCK），但不提前 return 以便 strict
        # validator 也输出其结构诊断。
    else:
        logger.info(
            f"Baseline-delta audit PASSED ✓ "
            f"(inherited_unchanged={len(delta.get('inherited_unchanged_errors', []))}, "
            f"target/new/regressed=0)")

    # Validate evidence with unified strict validator (now understands baseline-delta
    # policy: requires baseline_delta_passed=true, NOT raw errors_count==0).
    valid, reason = validate_audit_evidence(
        evidence, staging_db, source_db, staging_cfg_dir,
        staging_root=staging_root, marker_created_at=marker_created_at,
    )
    if not valid:
        logger.error(f"Audit evidence validation FAILED: {reason}")
        return 1
    logger.info("Audit evidence validation PASSED ✓ (structure + baseline-delta)")

    # 最终通过：结构门禁 + baseline-delta 门禁均 PASS
    logger.info("")
    logger.info("=== AUDIT PASSED ===")
    logger.info("Staging DB meets W2 quality contract (target tables clean, "
                "non-target inherited/baseline-delta PASS).")
    logger.info("Next: python scripts/backfill_fin_growth_dividend_staging.py --promote")
    return 0


# ===========================================================================
# Audit evidence builder
# ===========================================================================
def _build_audit_evidence(
    staging_db: Path,
    source_db: Path,
    staging_root: Path,
    staging_cfg_dir: Path,
    alignment_rules: Dict,
    tasks_cfg: Dict,
    authority_rules: Dict,
    report,
    data_schema_version: str,
    ptrade_profile_version: str,
) -> Dict:
    """Collect comprehensive audit evidence for the promotion gate.

    P1: Includes ALL required fields. If any data read fails, sets passed=false
    and returns evidence anyway with error details -- never silently skips.
    """

    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def _file_info(path: Path) -> Dict:
        try:
            return {
                "path": str(path.resolve()),
                "size": path.stat().st_size if path.exists() else 0,
                "sha256": _sha256(path) if path.exists() else None,
            }
        except Exception as e:
            return {"path": str(path.resolve()), "size": 0, "sha256": None, "error": str(e)}

    evidence_errors: List[str] = []

    # --- Core DB file info (flat keys for P0-3 + nested for compatibility) ---
    staging_info = _file_info(staging_db)
    source_info = _file_info(source_db)

    evidence: Dict = {
        "audit_time": datetime.now().isoformat(),
        "staging_db_path": str(staging_db.resolve()),
        "staging_db_size": staging_info.get("size", 0),
        "staging_db_sha256": staging_info.get("sha256"),
        "source_db_path": str(source_db.resolve()),
        "source_db_size": source_info.get("size", 0),
        "source_db_sha256": source_info.get("sha256"),
        "staging_db": staging_info,
        "source_db": source_info,
        "data_schema_version": data_schema_version,
        "ptrade_profile_version": ptrade_profile_version,
        "authority_rules": authority_rules,
    }

    # --- Config file hashes (P0-3: ALL 4 required) ---
    config_hashes: Dict = {}
    config_files: Dict = {}
    for fname in REQUIRED_CONFIG_FILES:
        fp = staging_cfg_dir / fname
        if fp.exists():
            info = _file_info(fp)
            config_hashes[fname] = info.get("sha256")
            config_files[fname] = info
        else:
            config_hashes[fname] = None
            config_files[fname] = {"path": str(fp.resolve()), "size": 0, "sha256": None, "error": "file not found"}
    evidence["config_hashes"] = config_hashes
    evidence["config_files"] = config_files

    # --- Runtime paths manifests (+ on-disk SHA-256 for promotion drift check) ---
    runtime_manifests: Dict = {}
    runtime_manifest_hashes: Dict = {}
    try:
        for manifest_file in sorted(staging_root.glob("runtime_paths_*.json")):
            try:
                content = json.loads(manifest_file.read_text(encoding="utf-8"))
                runtime_manifests[manifest_file.name] = content
                runtime_manifest_hashes[manifest_file.name] = _sha256(manifest_file)
            except Exception as e:
                runtime_manifests[manifest_file.name] = {"error": str(e)}
    except Exception as e:
        evidence_errors.append(f"runtime_manifests: {e}")
    evidence["runtime_paths"] = runtime_manifests
    evidence["runtime_manifest_hashes"] = runtime_manifest_hashes

    # --- Task batch IDs from batch_audit.db (SQLite, P0-1) ---
    batch_ids: List[Dict] = []
    batch_counts: Dict = {}
    try:
        batch_audit_path = staging_root / "batch_audit.db"
        if batch_audit_path.exists():
            # Read with the FULL canonical schema (rows_new/rows_updated are
            # ALTER-added by BatchAudit.init; started_at/finished_at are base
            # columns). Use closing() so the SQLite connection is always closed
            # (Windows releases the file lock on close, not on context-exit).
            with contextlib.closing(sqlite3.connect(str(batch_audit_path))) as conn:
                try:
                    cursor = conn.execute(
                        "SELECT batch_id, task_name, table_name, source, freq, status, "
                        "rows_raw, rows_passed, rows_rejected, rows_written, "
                        "rows_new, rows_updated, started_at, finished_at "
                        "FROM batch_audit ORDER BY finished_at DESC LIMIT 500"
                    )
                    cols = [d[0] for d in cursor.description]
                    rows = cursor.fetchall()
                    for r in rows:
                        record = dict(zip(cols, r))
                        batch_ids.append(record)
                except Exception:
                    # Fallback: minimal schema (older ledgers without rows_new/
                    # rows_updated/started_at). Backfill the missing keys so the
                    # validator's field-presence gate fails explicitly rather
                    # than with a KeyError.
                    cursor = conn.execute(
                        "SELECT batch_id, rows_raw, rows_passed, rows_rejected, rows_written, "
                        "status, finished_at FROM batch_audit ORDER BY finished_at DESC LIMIT 500"
                    )
                    cols = [d[0] for d in cursor.description]
                    rows = cursor.fetchall()
                    for r in rows:
                        record = dict(zip(cols, r))
                        record.setdefault("rows_new", None)
                        record.setdefault("rows_updated", None)
                        record.setdefault("started_at", None)
                        batch_ids.append(record)

            total_attempted = sum((r.get("rows_raw") or 0) for r in batch_ids)
            total_passed = sum((r.get("rows_passed") or 0) for r in batch_ids)
            total_rejected = sum((r.get("rows_rejected") or 0) for r in batch_ids)
            total_written = sum((r.get("rows_written") or 0) for r in batch_ids)
            total_failed = sum(1 for r in batch_ids if r.get("status") == "failed")
            batch_counts = {
                "attempted": total_attempted,
                "passed": total_passed,
                "rejected": total_rejected,
                "written": total_written,
                "failed": total_failed,
                "total_batches": len(batch_ids),
            }
            # Per-task breakdown keyed by task/table name, for batch conservation check
            # (rows_passed + rows_rejected == rows_raw). Aggregate all batch rows for a
            # given task so multi-batch tasks still validate.
            for task_name in ("fin_indicator", "stock_dividend"):
                task_rows = [r for r in batch_ids
                             if (r.get("table_name") or r.get("task_name") or r.get("task")) == task_name]
                if task_rows:
                    batch_counts[task_name] = {
                        "rows_raw": sum((r.get("rows_raw") or 0) for r in task_rows),
                        "rows_passed": sum((r.get("rows_passed") or 0) for r in task_rows),
                        "rows_rejected": sum((r.get("rows_rejected") or 0) for r in task_rows),
                        "rows_written": sum((r.get("rows_written") or 0) for r in task_rows),
                        "batch_count": len(task_rows),
                    }
    except Exception as e:
        evidence_errors.append(f"batch_audit: {e}")
    evidence["batch_ids"] = batch_ids
    evidence["batch_counts"] = batch_counts

    # --- Staging DB statistics (using DuckDB for read-only queries) ---
    fin_indicator_row_count = 0
    growth_field_stats: Dict = {}
    stock_dividend_row_count = 0
    dividend_stats: Dict = {}
    watermark_info: Dict = {}
    try:
        conn = duckdb.connect(str(staging_db), read_only=True)
        tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}

        if "fin_indicator" in tables:
            fin_indicator_row_count = conn.execute(
                "SELECT COUNT(*) FROM fin_indicator"
            ).fetchone()[0]
            np_total = fin_indicator_row_count
            np_non_null = conn.execute(
                "SELECT COUNT(*) FROM fin_indicator WHERE np_yoy IS NOT NULL"
            ).fetchone()[0]
            or_non_null = conn.execute(
                "SELECT COUNT(*) FROM fin_indicator WHERE or_yoy IS NOT NULL"
            ).fetchone()[0]
            tr_non_null = conn.execute(
                "SELECT COUNT(*) FROM fin_indicator WHERE tr_yoy IS NOT NULL"
            ).fetchone()[0]
            growth_field_stats = {
                "np_yoy_non_null": np_non_null,
                "np_yoy_total": np_total,
                "np_yoy_null_rate": round((np_total - np_non_null) / max(np_total, 1), 4),
                "or_yoy_non_null": or_non_null,
                "or_yoy_total": np_total,
                "or_yoy_null_rate": round((np_total - or_non_null) / max(np_total, 1), 4),
                "tr_yoy_non_null": tr_non_null,
                "tr_yoy_total": np_total,
                "tr_yoy_null_rate": round((np_total - tr_non_null) / max(np_total, 1), 4),
            }

        if "stock_dividend" in tables:
            stock_dividend_row_count = conn.execute(
                "SELECT COUNT(*) FROM stock_dividend"
            ).fetchone()[0]
            pre_tax_non_null = conn.execute(
                "SELECT COUNT(*) FROM stock_dividend WHERE cash_div_before_tax IS NOT NULL"
            ).fetchone()[0]
            post_tax_non_null = conn.execute(
                "SELECT COUNT(*) FROM stock_dividend WHERE cash_div_after_tax IS NOT NULL"
            ).fetchone()[0]
            stk_non_null = conn.execute(
                "SELECT COUNT(*) FROM stock_dividend WHERE stk_div IS NOT NULL"
            ).fetchone()[0]
            dividend_stats = {
                "row_count": stock_dividend_row_count,
                "cash_div_before_tax_non_null": pre_tax_non_null,
                "cash_div_after_tax_non_null": post_tax_non_null,
                "stk_div_non_null": stk_non_null,
            }
            # stk_bo_rate / stk_co_rate are new columns; count them only if the
            # columns exist in the staging schema (old schemas lack them).
            div_cols = {r[0] for r in conn.execute("DESCRIBE stock_dividend").fetchall()}
            for col, stat_key in (("stk_bo_rate", "stk_bo_rate_non_null"),
                                  ("stk_co_rate", "stk_co_rate_non_null")):
                if col in div_cols:
                    try:
                        dividend_stats[stat_key] = conn.execute(
                            f"SELECT COUNT(*) FROM stock_dividend WHERE {col} IS NOT NULL"
                        ).fetchone()[0]
                    except Exception:
                        pass

        # Watermark info
        if "source_watermark" in tables:
            wm_rows = conn.execute(
                "SELECT table_name, source, last_date, updated_at "
                "FROM source_watermark ORDER BY table_name, source"
            ).fetchall()
            watermark_info = {
                "count": len(wm_rows),
                "entries": [
                    {"table": r[0], "source": r[1],
                     "watermark": str(r[2]), "updated_at": str(r[3])}
                    for r in wm_rows[:200]
                ],
            }

        conn.close()
    except Exception as e:
        evidence_errors.append(f"staging_db_stats: {e}")

    evidence["fin_indicator_row_count"] = fin_indicator_row_count
    evidence["growth_field_stats"] = growth_field_stats
    evidence["stock_dividend_row_count"] = stock_dividend_row_count
    evidence["dividend_stats"] = dividend_stats
    evidence["watermark_info"] = watermark_info

    # --- Audit report summary ---
    errors_list = [
        {"check": i.check, "table": i.table, "count": i.count,
         "severity": i.severity, "detail": i.detail}
        for i in report.issues if i.severity == "error"
    ]
    warnings_list = [
        {"check": i.check, "table": i.table, "count": i.count,
         "severity": i.severity, "detail": i.detail}
        for i in report.issues if i.severity == "warning"
    ]
    evidence["audit"] = {
        "checks_run": report.checks_run,
        "errors_count": len(errors_list),
        "warnings_count": len(warnings_list),
        "errors": errors_list,
        "warnings": warnings_list,
    }

    # --- Passed: 结构收集成功的标记（无 evidence_errors）。---
    # 注意：evidence.passed 不代表"raw staging audit 零 error"。raw audit 的
    # inherited/baseline error（如 balance_statement/WatermarkConsistency 这类
    # source baseline 本就存在的非目标表问题）由 baseline_delta_passed 门禁负责
    # （inherited_unchanged 允许）。validate_audit_evidence 第 13 项据此只检查
    # 结构收集本身无 error，不在此处因 inherited error 让整个 evidence 失败。
    evidence["passed"] = len(evidence_errors) == 0
    if evidence_errors:
        evidence["evidence_errors"] = evidence_errors
        logger.warning(f"Evidence collection errors ({len(evidence_errors)}):")
        for err in evidence_errors:
            logger.warning(f"  - {err}")

    return evidence


# ===========================================================================
# Phase: promote (DRY-RUN ONLY)
# ===========================================================================
def phase_promote(args: argparse.Namespace) -> int:
    """Dry-run promotion. Print what WOULD be done without doing it.

    Steps:
        1. Read audit_evidence.json and validate all gate checks
        2. Print the exact commands that WOULD be executed for promotion
        3. Print backup path with timestamp
        4. Do NOT actually move/rename any files
    """
    staging_root = resolve_absolute(args.staging_root)
    source_db = resolve_absolute(args.source_db)

    logger.info("=" * 72)
    logger.info("PHASE: promote -- DRY-RUN ONLY (no files will be moved)")
    logger.info(f"  Source DB:    {source_db}")
    logger.info(f"  Staging root: {staging_root}")
    logger.info("=" * 72)

    staging_db = staging_db_path(staging_root)
    if not staging_db.exists():
        logger.error(f"Staging DB not found: {staging_db}")
        return 1

    staging_cfg_dir = staging_config_dir(staging_root)

    # ------------------------------------------------------------------
    # Step 1: Read audit_evidence.json and perform gate checks via unified validator
    # ------------------------------------------------------------------
    evidence_path = staging_root / "audit_evidence.json"
    if not evidence_path.exists():
        logger.error(
            f"BLOCK: audit_evidence.json not found at {evidence_path}\n"
            f"  Run --audit first to produce the evidence file."
        )
        return 1

    logger.info("[1/4] Reading audit_evidence.json...")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    # Re-read staging marker created_at (batch timing gate)
    marker_created_at = None
    marker_path = staging_root / ".quantstudio_staging.json"
    if marker_path.exists():
        try:
            marker_created_at = json.loads(
                marker_path.read_text(encoding="utf-8")).get("created_at")
        except Exception:
            marker_created_at = None

    # Promotion re-runs the full gate including on-disk manifest drift check
    # (staging_root is passed so the two manifests are re-hashed from disk).
    valid, reason = validate_audit_evidence(
        evidence, staging_db, source_db, staging_cfg_dir,
        staging_root=staging_root, marker_created_at=marker_created_at,
    )
    if not valid:
        logger.error(f"BLOCK: audit evidence validation FAILED: {reason}")
        return 1
    logger.info("[1/4] All promotion gates passed ✓")

    # ------------------------------------------------------------------
    # Step 2 & 3: Print the promotion plan
    # ------------------------------------------------------------------
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = source_db.parent / f"quantstudio_backup_{timestamp}.db"

    logger.info("")
    logger.info("=" * 72)
    logger.info("PROMOTION PLAN (DRY-RUN -- NOTHING HAS BEEN EXECUTED)")
    logger.info("=" * 72)
    logger.info("")
    logger.info("The following commands WOULD be executed to promote the staging DB:")
    logger.info("")
    logger.info("Step A: Backup current production DB")
    logger.info(f"  copy {source_db}")
    logger.info(f"    -> {backup_path}")
    logger.info("")
    logger.info("Step B: Replace production DB with staging DB")
    logger.info(f"  move {staging_db}")
    logger.info(f"    -> {source_db}")
    logger.info("")
    logger.info("Step C: Clean up staging directory (optional)")
    logger.info(f"  rmdir /s {staging_root}   (or keep for audit trail)")
    logger.info("")
    logger.info("-" * 72)
    logger.info("WARNING: This is a DRY-RUN. No files have been moved or modified.")
    logger.info("")
    logger.info("To execute the actual promotion, you must:")
    logger.info("  1. Stop the daemon")
    logger.info("  2. Close any GUI instances")
    logger.info("  3. Run the commands above manually")
    logger.info("")
    logger.info(f"Backup path with timestamp: {backup_path}")
    logger.info(f"Staging DB location:        {staging_db}")
    logger.info(f"Production DB location:     {source_db}")

    # Also check WAL/SHM files the staging DB might have
    wal_path = Path(str(staging_db) + ".wal")
    shm_path = Path(str(staging_db) + "-wal")  # duckdb sometimes uses this
    if wal_path.exists():
        logger.info(f"  Note: staging WAL file exists: {wal_path}")
        logger.info(f"        Ensure DuckDB connections are closed before promotion.")

    return 0


# ===========================================================================
# Main entry point
# ===========================================================================
def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    validate_args(args)

    # Dispatch to the appropriate phase
    if args.prepare:
        return phase_prepare(args)
    elif args.run_task is not None:
        return phase_run_task(args)
    elif args.audit:
        return phase_audit(args)
    elif args.promote:
        return phase_promote(args)
    else:
        sys.exit("No phase selected.")


if __name__ == "__main__":
    sys.exit(main())
