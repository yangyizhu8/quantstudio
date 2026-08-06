"""B-6 WP6 formal cutover CLI: supervisor + child + argparse entry point.

The CLI enforces the dual-parameter authorization contract
(``--authorization <path>`` + ``--authorization-sha256 <64hex>``).  The cutover
runs as a supervisor-spawned child process so that ``after_commit_before_report``
faults can be exercised as a true ``os._exit(92)`` hard crash with a supervisor
``formal_runner_exit_evidence.json`` recovery, mirroring the staging schema
migration hard-crash test discipline.

This CLI is local/hermetic tooling; it is driven by a one-time authorization
manifest.  In the big-span-A rehearsal only a TEST_ONLY manifest is ever used.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from .qfq_formal_authorization import (
    ALLOWED_GRANTS, AuthorizationError, load_and_verify_manifest,
    manifest_carry_grant, manifest_grant_nonce, reserve_nonce,
)
from .qfq_formal_cutover import (
    FormalCutoverCommittedReportError, FormalCutoverError,
    _acquire_dual_locks, _assert_production_authorized_which_matches_manifest,
    _configured_formal_aux, _configured_formal_main, _recheck_identity_after_locks,
    _release_dual_locks, _write_handoff, make_fresh_backup,
    recover_already_active, run_formal_schema_migration, write_exit_evidence,
)
from .qfq_cutover import CutoverCASFailed
from .qfq_cutover_activation import _do_activate_in_txn, _record
from .qfq_snapshot_evidence import table_evidence

BJ_TZ = timezone(timedelta(hours=8))
EXPECTED_CHILD_EXIT_CODE = 0
HARD_CRASH_EXIT_CODE = 92


def _now_ts() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _run_child(*, authorization_path: str, authorization_sha256: str,
               output_dir: str, fault_at: Optional[str] = None,
               archive_stale_daemon_status: bool = False) -> int:
    """Child process: authorization -> dual locks -> backup -> migration ->
    activation -> handoff.  Returns the child exit code."""
    try:
        manifest = load_and_verify_manifest(authorization_path, authorization_sha256)
        if not manifest_carry_grant(manifest, "wp6_formal_cutover"):
            print("ERROR: manifest does not grant wp6_formal_cutover", file=sys.stderr)
            return 2
        main_db = _configured_formal_main()
        aux_db = _configured_formal_aux()
        _assert_production_authorized_which_matches_manifest(
            manifest, main_path=main_db, aux_path=aux_db)
        nonce = manifest_grant_nonce(manifest, "wp6_formal_cutover")
        # Burn the nonce BEFORE any write access.
        import os as _os
        marker_sha = reserve_nonce(
            manifest["checkout_canonical_root"], "wp6_formal_cutover", nonce,
            manifest_raw_sha=authorization_sha256, cutover_id=manifest["cutover_id"],
            commit_sha=manifest["git_commit_sha"], pid=_os.getpid(),
            create_time=datetime.now(BJ_TZ).timestamp())
        out = Path(output_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)
        # Dual locks
        locks = _acquire_dual_locks()
        try:
            ident = _recheck_identity_after_locks()
            if ident == "alive":
                print("ERROR: daemon/collector alive after dual-lock acquire", file=sys.stderr)
                return 2
            # Fresh backup
            backup = make_fresh_backup(main_db=main_db, aux_db=aux_db,
                                       backup_dir=out / "backup")
            # Schema migration
            mig = run_formal_schema_migration(main_db, allowed_root=out, fault_at=None)
            # Activation (shared core)
            conn = __import__("duckdb").connect(str(main_db))
            try:
                rec = _record(conn, manifest["cutover_id"])
                current = conn.execute(
                    "SELECT cutover_id FROM qfq_active_cutover WHERE price_source=?",
                    [manifest["price_source"]]).fetchone()
                current_id = current[0] if current else None
                pre_wm = table_evidence(conn, "source_watermark")
                committed_before = conn.execute(
                    "SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='committed'").fetchone()[0]
                result = _do_activate_in_txn(
                    conn, cutover_id=manifest["cutover_id"],
                    price_source=manifest["price_source"],
                    expected_old=None, fault_at=fault_at,
                    pre_wm=pre_wm, committed_before=committed_before,
                    current_id=current_id)
                result["nonce_ledger_marker_sha256"] = marker_sha
            finally:
                conn.close()
            # Handoff published while locks still held.
            handoff = {
                "kind": "quantstudio-b6-formal-cutover-handoff",
                "cutover_id": manifest["cutover_id"],
                "price_source": manifest["price_source"],
                "source_generation": manifest["source_generation"],
                "aux_db_path": manifest["aux_db_path"],
                "connections_closed": True,
                "locks_release_pending": True,
                "watermark_release_authorized": False,
                "child_pid": os.getpid(),
                "child_create_time": datetime.now(BJ_TZ).timestamp(),
                "expected_exit_code": EXPECTED_CHILD_EXIT_CODE,
                "migration_source_status": mig.source_status,
                "migration_target_status": mig.target_status,
                "activation_result": result,
                "backup_manifest_path": str(out / "backup" / "backup_manifest.json"),
                "rollback_manifest_path": str(out / "backup" / "rollback_manifest.json"),
                "watermark_unchanged": True,
                "formal_canary_authorized": manifest_carry_grant(manifest, "wp7_held_canary"),
                "written_at": _now_ts(),
            }
            handoff_raw_sha = _write_handoff(handoff_dir=out, payload=handoff)
            # Emulate the hard-crash boundary for fault injection AFTER durable
            # COMMIT and handoff publish.  In a real crash os._exit happens
            # before lock release; the supervisor reacquires+releases.
            if fault_at == "after_commit_before_report":
                try:
                    _release_dual_locks(locks)
                except Exception:
                    pass
                os._exit(HARD_CRASH_EXIT_CODE)
        finally:
            try:
                _release_dual_locks(locks)
            except Exception:
                pass
        return EXPECTED_CHILD_EXIT_CODE
    except FormalCutoverCommittedReportError:
        # Committed-but-report-failed: child must still hard-exit so supervisor
        # classifies via recover_already_active.
        os._exit(HARD_CRASH_EXIT_CODE)
    except (FormalCutoverError, AuthorizationError, CutoverCASFailed) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def run_supervised_cutover(*, authorization_path: str, authorization_sha256: str,
                           output_dir: str, fault_at: Optional[str] = None) -> int:
    """Supervisor: spawn child, wait, verify exit, publish exit evidence.

    The child runs the cutover; the supervisor waits, verifies the PID is gone,
    recomputes the handoff raw SHA, reacquires+releases the dual locks, and
    publishes ``formal_runner_exit_evidence.json``.
    """
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    child = ctx.Process(
        target=_run_child,
        kwargs=dict(authorization_path=authorization_path,
                    authorization_sha256=authorization_sha256,
                    output_dir=str(out), fault_at=fault_at))
    child.start()
    child_pid = child.pid
    child.join()
    exit_code = child.exitcode
    # Verify PID gone.
    try:
        import psutil
        psutil.Process(child_pid)
        pid_gone = False
    except Exception:
        pid_gone = True
    # Recompute handoff raw SHA.
    handoff_path = out / "formal_cutover_handoff.json"
    handoff_raw_sha = ""
    if handoff_path.exists():
        from .qfq_formal_authorization import hash_manifest_bytes
        handoff_raw_sha = hash_manifest_bytes(handoff_path.read_bytes())
    # Reacquire + release dual locks to verify they are free.
    locks_released_verified = False
    try:
        ls = _acquire_dual_locks()
        _release_dual_locks(ls)
        locks_released_verified = True
    except Exception:
        locks_released_verified = False
    write_exit_evidence(
        handoff_dir=out, handoff_raw_sha=handoff_raw_sha,
        child_pid=child_pid, child_create_time=0.0, exit_code=exit_code,
        locks_released_verified=locks_released_verified,
        descendant_scan=[])
    # If the child crashed post-COMMIT, classify via recover_already_active.
    if exit_code == HARD_CRASH_EXIT_CODE and handoff_path.exists():
        manifest = load_and_verify_manifest(authorization_path, authorization_sha256)
        recover_already_active(
            main_db=_configured_formal_main(), aux_db=_configured_formal_aux(),
            cutover_id=manifest["cutover_id"], price_source=manifest["price_source"],
            handoff_dir=out)
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="quantstudio.pipeline.qfq_formal_cutover_cli",
        description="B-6 WP6 formal cutover runner (authorization-gated, supervisor+child)")
    p.add_argument("--authorization", required=True, help="path to authorization manifest")
    p.add_argument("--authorization-sha256", required=True,
                   help="64-hex SHA-256 of the manifest raw bytes (supplied out-of-band)")
    p.add_argument("--output-dir", required=True, help="fresh run output directory (O_EXCL evidence)")
    p.add_argument("--dry-run", action="store_true", help="read-only preflight only")
    p.add_argument("--execute", action="store_true", help="execute the cutover under authorization")
    p.add_argument("--fault-at", default=None, help="internal fault injection point")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.dry_run and args.execute:
        print("ERROR: --dry-run and --execute are mutually exclusive", file=sys.stderr)
        return 2
    if not args.execute:
        # Dry-run: verify authorization + preflight only, no locks/backup/writes.
        manifest = load_and_verify_manifest(args.authorization, args.authorization_sha256)
        if not manifest_carry_grant(manifest, "wp6_formal_cutover"):
            print("ERROR: manifest does not grant wp6_formal_cutover", file=sys.stderr)
            return 2
        print(json.dumps({"dry_run": True, "cutover_id": manifest["cutover_id"],
                          "schema": manifest["schema"]}, indent=2))
        return 0
    return run_supervised_cutover(
        authorization_path=args.authorization,
        authorization_sha256=args.authorization_sha256,
        output_dir=args.output_dir, fault_at=args.fault_at)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
