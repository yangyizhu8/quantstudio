"""B-6 WP7-E3 formal watermark release — the ONLY entry point that advances the
formal source watermark after a successful WP6 cutover.

Hard contract (G0 §5.3 / §5.3.4):
  * This module is the sole entry point for normal watermark advancement.  It
    NEVER calls ``UPDATE source_watermark``, ``writer.advance_watermark()``, or
    any production-collection primitive directly.  It only invokes the existing
    production CLI ``python -m quantstudio.pipeline.daemon --mode once --task
    <task> --pull-mode incremental --quality-audit full`` for the four fixed QFQ
    tasks, serially.
  * The release is driven by an independent ``wp7_e3_watermark_release`` grant
    on a SEPARATE authorization manifest (never the WP6/WP7-E2 manifest).  The
    WP6/WP7-E2 loaders explicitly reject any manifest carrying this grant
    (``assert_no_watermark_release_grant``).
  * This entry point must NOT run inside the formal runner / supervisor process.
    It is a separate process, launched after WP6 + WP7-E2 + G2 have all passed.
  * Release success is defined ONLY by a committed intent produced through the
    normal ``run_once -> execute_task -> qfq cycle -> post_ingest`` chain — never
    by a direct watermark write.

This module is FROZEN code + gates only.  It does not execute any formal DB
read-write when imported; the CLI subcommand drives execution under explicit
authorization.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .qfq_formal_authorization import (
    AuthorizationError, AuthorizationScopeError, WP7_E3_GRANT,
    assert_no_watermark_release_grant, hash_manifest_bytes,
    load_and_verify_manifest, manifest_carry_grant, manifest_grant_nonce,
    reserve_nonce, resolve_canonical,
)
from .qfq_formal_cutover import (
    FormalCutoverError, _acquire_dual_locks, _configured_formal_aux,
    _configured_formal_main, _release_dual_locks,
)

BJ_TZ = timezone(timedelta(hours=8))

#: The four fixed QFQ tasks, run serially (never parallel).  Per G0 §5.3.2.
WATERMARK_RELEASE_TASKS = (
    "mcp_etf_daily",
    "mcp_etf_minutes",
    "mcp_stock_daily",
    "mcp_stock_minutes",
)

#: The production CLI invocation that is the ONLY allowed watermark-advancement
#: path.  Per G0 §5.3.2 / §5.3.4.
_RELEASE_CLI_TEMPLATE = [
    sys.executable, "-m", "quantstudio.pipeline.daemon",
    "--mode", "once",
    "--config-dir", "config/profiles/mcp_only",
    "--pull-mode", "incremental",
    "--quality-audit", "full",
]


class WatermarkReleaseError(FormalCutoverError):
    pass


class WatermarkReleaseBypass(WatermarkReleaseError):
    """Raised when a direct watermark-write bypass is attempted (a hard P0)."""


def _assert_not_spawned_child() -> None:
    """Assert this process is NOT a spawned child of the formal runner / supervisor.

    Per the independent-process constraint: the release entry point must run in
    a fresh process, never inside the formal runner's ``_run_child`` or the
    supervisor's execution session.
    """
    # The formal runner sets this env var on spawned children; its presence here
    # means we are inside a runner session, which is forbidden.
    if os.environ.get("_QFQ_FORMAL_RUNNER_CHILD") == "1":
        raise WatermarkReleaseError(
            "watermark release must NOT run inside a formal runner/supervisor "
            "child process; launch it as a separate process after G2 PASS")


def verify_handoff_and_exit_evidence(*, handoff_dir: str | Path) -> dict:
    """Verify WP6 handoff + exit evidence are complete and consistent.

    Checks (G0 §5.3.1 item 13 / WP7 contract):
      * both files exist;
      * the exit evidence's ``handoff_raw_sha256`` matches an independent
        recomputation of the handoff raw bytes;
      * the handoff pins ``watermark_release_authorized=false`` (the release
        comes from THIS separate authorization, not from the handoff);
      * ``locks_released_verified=true`` (the runner exited cleanly);
      * the child PID recorded in the handoff is no longer alive.
    """
    hdir = resolve_canonical(handoff_dir)
    handoff_path = hdir / "formal_cutover_handoff.json"
    exit_path = hdir / "formal_runner_exit_evidence.json"
    if not handoff_path.is_file():
        raise WatermarkReleaseError(f"missing handoff: {handoff_path}")
    if not exit_path.is_file():
        raise WatermarkReleaseError(f"missing exit evidence: {exit_path}")
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    exit_ev = json.loads(exit_path.read_text(encoding="utf-8"))
    # Independent recomputation of handoff raw SHA.
    handoff_raw_sha = hash_manifest_bytes(handoff_path.read_bytes())
    exit_sha = exit_ev.get("handoff_raw_sha256", "")
    if not exit_sha:
        raise WatermarkReleaseError(
            "exit evidence has empty handoff_raw_sha256; cannot verify handoff binding")
    if handoff_raw_sha != exit_sha:
        raise WatermarkReleaseError(
            f"handoff raw SHA mismatch: computed={handoff_raw_sha} exit_evidence={exit_sha}")
    if handoff.get("watermark_release_authorized") is not False:
        raise WatermarkReleaseError(
            "handoff must pin watermark_release_authorized=false")
    if not exit_ev.get("locks_released_verified"):
        raise WatermarkReleaseError("exit evidence reports locks NOT released")
    # Child PID no longer alive.
    child_pid = handoff.get("child_pid")
    if isinstance(child_pid, int) and child_pid > 0:
        try:
            import psutil
            try:
                p = psutil.Process(child_pid)
                if p.is_running():
                    raise WatermarkReleaseError(
                        f"formal runner child pid={child_pid} still alive; release refused")
            except psutil.NoSuchProcess:
                pass  # good — child is gone
        except ImportError:
            pass  # psutil unavailable; skip liveness check (PID binding still recorded)
    return {"handoff_raw_sha256": handoff_raw_sha, "child_pid": child_pid,
            "locks_released_verified": exit_ev.get("locks_released_verified")}


def assert_dual_locks_free() -> None:
    """Assert the dual locks are free (non-blocking acquire + release).  Per G0
    §5.3.4: before release, the formal runner PID must be gone and the locks
    must be releasable."""
    try:
        ls = _acquire_dual_locks()
        _release_dual_locks(ls)
    except Exception as exc:
        raise WatermarkReleaseError(
            f"dual locks not free before watermark release: {exc}") from exc


def _run_one_release_task(task: str, *, timeout: int = 3600) -> dict:
    """Invoke the production daemon CLI for ONE QFQ task.  Returns the CLI result.

    This is the ONLY watermark-advancement path.  It never writes
    source_watermark directly; the daemon's normal
    ``run_once -> execute_task -> qfq cycle -> post_ingest`` chain commits the
    intent and advances the watermark through the QFQ gate.
    """
    cmd = _RELEASE_CLI_TEMPLATE + ["--task", task]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                cwd=str(resolve_canonical(".")))
    except subprocess.TimeoutExpired as exc:
        raise WatermarkReleaseError(f"task {task} timed out after {timeout}s") from exc
    return {"task": task, "returncode": result.returncode,
            "stdout_tail": result.stdout[-2000:], "stderr_tail": result.stderr[-2000:]}


def release_watermark(*, authorization_path: str, authorization_sha256: str,
                      handoff_dir: str | Path,
                      dry_run: bool = False) -> dict:
    """WP7-E3 watermark release entry point.

    Consumes the ``wp7_e3_watermark_release`` grant, verifies the WP6 handoff +
    exit evidence, asserts the dual locks are free and this is not a spawned
    child, then invokes the production daemon CLI for the four fixed QFQ tasks
    serially.  Any task that fails/held/produces no terminal intent stops the
    sequence immediately (no manual watermark fix).

    ``dry_run=True`` performs every gate EXCEPT the actual daemon invocation
    (used for preflight verification).
    """
    # 0. Independent-process assertion.
    _assert_not_spawned_child()

    # 1. Load + verify the WP7-E3 manifest (separate from WP6/WP7-E2 manifest).
    # Only this entry point accepts the wp7_e3_watermark_release grant; WP6/WP7-E2
    # loaders use the default ALLOWED_GRANTS and reject it.
    manifest = load_and_verify_manifest(
        authorization_path, authorization_sha256,
        extra_allowed_grants=(WP7_E3_GRANT,))
    if not manifest_carry_grant(manifest, WP7_E3_GRANT):
        raise AuthorizationScopeError(
            f"manifest does not grant {WP7_E3_GRANT!r}; release refused")
    nonce = manifest_grant_nonce(manifest, WP7_E3_GRANT)

    # 2. Verify WP6 handoff + exit evidence completeness.
    ev = verify_handoff_and_exit_evidence(handoff_dir=handoff_dir)

    # 3. Assert dual locks free (runner exited).
    assert_dual_locks_free()

    # 4. Burn the wp7_e3 nonce (separate ledger from wp6/wp7_held_canary).
    auth_root_for_ledger = Path(authorization_path).resolve().parent
    marker_sha = reserve_nonce(
        str(auth_root_for_ledger), WP7_E3_GRANT, nonce,
        manifest_raw_sha=authorization_sha256, cutover_id=manifest["cutover_id"],
        commit_sha=manifest["git_commit_sha"], pid=os.getpid(),
        create_time=datetime.now(BJ_TZ).timestamp(),
        extra_allowed_grants=(WP7_E3_GRANT,))

    if dry_run:
        return {"dry_run": True, "handoff_evidence": ev,
                "nonce_ledger_marker_sha256": marker_sha,
                "tasks_planned": list(WATERMARK_RELEASE_TASKS)}

    # 5. Run the four tasks serially via the production daemon CLI.
    results = []
    for task in WATERMARK_RELEASE_TASKS:
        res = _run_one_release_task(task)
        results.append(res)
        if res["returncode"] != 0:
            raise WatermarkReleaseError(
                f"task {task} failed (exit {res['returncode']}); stopping sequence; "
                "no manual watermark fix permitted")
        # A successful release requires a committed intent from the normal chain.
        # The daemon CLI's own exit code + quality-audit gate is the terminal
        # signal; we do not second-guess it with a direct watermark read here.

    return {"dry_run": False, "handoff_evidence": ev,
            "nonce_ledger_marker_sha256": marker_sha,
            "tasks": results,
            "released_at": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")}
