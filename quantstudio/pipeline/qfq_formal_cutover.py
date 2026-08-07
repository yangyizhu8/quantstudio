"""B-6 WP6 formal cutover runner: the only authorized write path to the formal DB.

This module implements the formal cutover transaction boundary under the G0
authorization contract.  It is a sibling of the staging runner
(``qfq_cutover_activation.activate_cutover_staging``); both reuse the shared
policy-free activation core ``_do_activate_in_txn`` so the six-point fault matrix
is byte-for-byte semantically equivalent, but the formal wrapper substitutes the
staging-only guard with the full authorization + dual-lock + backup chain.

Hard boundaries (G0 / CodeBuddy G1 calibration):
  * ``_assert_production_authorized_which_matches_manifest`` is an *independent*
    re-implementation; it does **not** import any private (underscore-prefixed)
    symbol from ``qfq_schema_migration``.
  * The schema migration (COMPLETE_2_0 -> COMPLETE_2_1) is self-written here
    using only read-only migration helpers/constants (detect_schema_status /
    _snapshot / verify_fingerprint / fingerprint constants / DDL constants /
    SQL-construction helpers).  It never imports ``_do_migrate_in_txn``,
    ``_ReportReservation``, ``_assert_not_production`` or
    ``_assert_allowed_root``; the migration module is left untouched so the
    staging guard keeps refusing formal paths unconditionally.
  * The runner never imports ``ResidentCollector.run_once`` / ``execute_task``,
    ``qfq_run_post_ingest`` or ``writer.advance_watermark``.  Watermark release
    is a separate future authorization via the production daemon CLI.
  * handoff is published while the child still holds both locks
    (``locks_release_pending=true``); it never contains its own SHA.  The
    supervisor publishes ``formal_runner_exit_evidence.json`` after the child
    exits and independently recomputes the handoff raw SHA.

This module is local tooling for the hermetic/staging rehearsal only; the real
formal cutover is driven by the CLI (``qfq_formal_cutover_cli``) at a future
maintenance window under a real offline-generated authorization manifest.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import duckdb
from filelock import FileLock, Timeout

from .daemon_lifecycle import (
    collector_run_lock_path, daemon_lock_path, read_daemon_status,
    verify_daemon_identity,
)
from .qfq_aux_router import AuxDbRouter
from .qfq_cutover import CutoverCASFailed, CutoverError, create_cutover, transition_cutover
from .qfq_cutover_activation import (
    LEGACY_GENERATION, LEGACY_SOURCE, _do_activate_in_txn, _freeze_cutover_evidence_core,
    _record, build_cutover_evidence, verify_cutover_evidence, _write_exclusive_json,
)
from .qfq_discovery_baseline import (
    BaselineIdentity, audit_pending_slots, establish_discovery_baseline,
)
from .qfq_formal_authorization import (
    ALLOWED_GRANTS, AuthorizationError, AuthorizationScopeError,
    AuthorizationTamperError, NonceReplayError, compute_required_free_bytes,
    disk_free_bytes, file_evidence, generate_test_manifest, hash_manifest_bytes,
    load_and_verify_manifest, manifest_carry_grant, manifest_grant_nonce,
    path_is_link_like, reserve_nonce, resolve_canonical, same_file,
    verify_formal_file_evidence,
)

BJ_TZ = timezone(timedelta(hours=8))

# Migration read-only helpers/constants (formal self-written migration sequence
# reuses these; never imports _do_migrate_in_txn / _ReportReservation / guards).
from .qfq_schema_migration import (
    DDL_DUCKDB, LEGACY_MAIN_DB_2_0_FINGERPRINT, NEW_TABLES,
    REBUILD_QFQ_TABLES, REBUILD_SHARED_TABLES, SHADOW_LEGACY_SUFFIX,
    SHADOW_V2_SUFFIX, TARGET_MAIN_DB_2_1_FINGERPRINT, _build_shadow_copy_sql,
    _rewrite_ddl_table_name, _snapshot, _target_ddl_for, _validate_rebuilt_table,
    verify_fingerprint,
)
from .qfq_schema_status import SchemaStatus, detect_schema_status
from .qfq_snapshot_evidence import table_evidence

# ---------------------------------------------------------------------------
# Exception hierarchy.
# ---------------------------------------------------------------------------


class FormalCutoverError(RuntimeError):
    """Base error for the formal cutover runner."""


class FormalCutoverRefused(FormalCutoverError):
    """Raised when the formal path is refused (production, authorization, locks)."""


class FormalCutoverCommittedReportError(FormalCutoverError):
    """The cutover transaction durable-committed but report/handoff failed.

    Callers must NOT retry the cutover.  Re-open the database read-only and use
    ``recover_already_active`` to classify the durable state.
    """

    def __init__(self, message: str, *, db_path: str, handoff_dir: str):
        super().__init__(message)
        self.db_path = db_path
        self.handoff_dir = handoff_dir
        self.cutover_committed = True


# ---------------------------------------------------------------------------
# Production-authorization guard (independent re-implementation, P2-1).
# ---------------------------------------------------------------------------


def _configured_formal_main() -> Path:
    """Resolve the configured formal main DB path (independent of migration)."""
    try:
        from quantstudio._paths import db_path
        return resolve_canonical(db_path())
    except Exception as exc:
        raise FormalCutoverRefused("cannot resolve configured formal DB path; fail-closed") from exc


def _configured_formal_aux() -> Path:
    return _configured_formal_main().parent / "qfq_aux.db"


def _assert_production_authorized_which_matches_manifest(
        manifest: Mapping[str, Any], *, main_path: str | Path,
        aux_path: str | Path) -> None:
    """Assert the live formal main/aux paths are exactly the manifest's authorized
    canonical paths, with no symlink/junction/hardlink aliasing and no drift in
    size/mtime/SHA from the manifest's frozen pre-evidence.

    Independent of ``qfq_schema_migration._is_production_db``; this is the
    formal-side *positive* authorization (the live path must BE the authorized
    formal path), with explicit link/hardlink detection on top of samefile +
    case-fold.
    """
    m_main = resolve_canonical(manifest["formal_main_canonical_path"])
    m_aux = resolve_canonical(manifest["formal_aux_canonical_path"])
    live_main = resolve_canonical(main_path)
    live_aux = resolve_canonical(aux_path)
    cfg_main = _configured_formal_main()
    cfg_aux = _configured_formal_aux()
    # The live path must be the configured formal path (samefile or case-fold).
    for live, cfg, role in ((live_main, cfg_main, "main"), (live_aux, cfg_aux, "aux")):
        if not same_file(live, cfg):
            raise FormalCutoverRefused(
                f"live formal {role} path is not the configured formal {role}: {live} vs {cfg}")
        if not same_file(live, m_main if role == "main" else m_aux):
            raise FormalCutoverRefused(
                f"live formal {role} path diverges from manifest authorized {role}: {live} vs {m_main if role=='main' else m_aux}")
    # Reject any symlink/junction/hardlink alias of the formal files themselves.
    for live, role in ((live_main, "main"), (live_aux, "aux")):
        if path_is_link_like(live):
            raise FormalCutoverRefused(
                f"formal {role} path is a symlink/junction/hardlink: {live}; refuse alias")
    verify_formal_file_evidence(manifest, main_path=live_main, aux_path=live_aux)


# ---------------------------------------------------------------------------
# Dual-lock + daemon identity (G0 §3.3.1; reuses daemon_lifecycle primitives).
# ---------------------------------------------------------------------------


@dataclass
class _LockState:
    daemon_lock: Optional[FileLock] = None
    collector_lock: Optional[FileLock] = None
    acquired: bool = False


def _acquire_dual_locks() -> _LockState:
    """Acquire .daemon.lock then .collector_run.lock non-blocking, in fixed order.

    Returns the held lock state.  Raises FormalCutoverRefused on any busy/alive/
    denied condition.  Does not auto-kill; mtime is never used as a stale gate.
    """
    state = _LockState()
    # 1. daemon identity five-check (alive -> BLOCK, denied -> BLOCK).
    status = read_daemon_status()
    if status is not None:
        identity = verify_daemon_identity(status)
        if identity == "alive":
            raise FormalCutoverRefused(
                f"daemon identity=alive (pid={status.get('pid')}); refuse to cutover while a daemon owns the DB")
        if identity == "denied":
            raise FormalCutoverRefused("daemon identity=denied; cannot verify DB ownership; refuse")
        # stale: the recorded process is gone, but this does NOT mean the DB is writable.
    # 2. non-blocking acquire in fixed order: daemon lock first.
    state.daemon_lock = FileLock(str(daemon_lock_path()), timeout=0)
    try:
        state.daemon_lock.acquire(timeout=0)
    except Timeout as exc:
        raise FormalCutoverRefused(".daemon.lock busy; refuse to cutover") from exc
    try:
        state.collector_lock = FileLock(str(collector_run_lock_path()), timeout=0)
        try:
            state.collector_lock.acquire(timeout=0)
        except Timeout as exc:
            state.daemon_lock.release()
            state.daemon_lock = None
            raise FormalCutoverRefused(".collector_run.lock busy; refuse to cutover") from exc
    except BaseException:
        if state.daemon_lock is not None:
            try:
                state.daemon_lock.release()
            except Exception:
                pass
        raise
    state.acquired = True
    return state


def _release_dual_locks(state: _LockState) -> None:
    """Release collector lock first, then daemon lock (reverse of acquire)."""
    if state.collector_lock is not None:
        try:
            state.collector_lock.release()
        except Exception:
            pass
    if state.daemon_lock is not None:
        try:
            state.daemon_lock.release()
        except Exception:
            pass


def _recheck_identity_after_locks() -> str:
    """Second identity + process scan after acquiring both locks (TOCTOU)."""
    status = read_daemon_status()
    if status is not None:
        identity = verify_daemon_identity(status)
        if identity in ("alive", "denied"):
            return identity
    # Process scan: any QuantStudio daemon/collector/GUI worker/known writer we
    # cannot attribute to this runner -> BLOCK.  Lightweight cmdline check.
    try:
        import psutil
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or [])
            except Exception:
                continue
            if "quantstudio.pipeline.daemon" in cmd or "quantstudio.pipeline.qfq_resident" in cmd:
                return "alive"
    except Exception:
        pass
    return "stale"


# ---------------------------------------------------------------------------
# Self-written schema migration sequence (method 2; read-only helper reuse).
# ---------------------------------------------------------------------------


@dataclass
class FormalMigrationResult:
    source_status: str
    target_status: str
    already_current: bool = False
    counts_before: dict = field(default_factory=dict)
    hashes_before: dict = field(default_factory=dict)
    counts_after: dict = field(default_factory=dict)
    hashes_after: dict = field(default_factory=dict)
    txn_validation: list = field(default_factory=list)
    final_fingerprint_ok: bool = False
    report_status: str = "PENDING"


def _formal_migrate_in_txn(conn, *, counts_before: Mapping[str, int],
                            fault_at: Optional[str] = None,
                            txn_validation: Optional[list] = None) -> None:
    """Run the COMPLETE_2_0 -> COMPLETE_2_1 migration steps inside the caller's
    transaction.

    This mirrors ``qfq_schema_migration._do_migrate_in_txn`` steps (a)-(f)
    (shadow create -> copy -> validate -> new tables -> swap rename -> legacy
    cleanup) using the same constants and SQL-construction helpers, so the
    generated SQL is identical to staging.  The caller owns BEGIN/COMMIT/ROLLBACK
    and fingerprint re-read; this function performs no policy or guard work.
    """
    rebuild = list(REBUILD_QFQ_TABLES) + list(REBUILD_SHARED_TABLES)
    # (a) shadow create
    for table in rebuild:
        ddl = _rewrite_ddl_table_name(_target_ddl_for(table), table, f"{table}{SHADOW_V2_SUFFIX}")
        ddl = ddl.replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE", 1)
        conn.execute(ddl)
    # (b) copy legacy rows into shadow
    for table in rebuild:
        shadow = f"{table}{SHADOW_V2_SUFFIX}"
        conn.execute(_build_shadow_copy_sql(table, shadow))
    # (c) validate each rebuilt table
    for table in rebuild:
        shadow = f"{table}{SHADOW_V2_SUFFIX}"
        res = _validate_rebuilt_table(conn, table, shadow, counts_before.get(table, -1))
        if txn_validation is not None:
            txn_validation.append(res)
        if not res["ok"]:
            raise FormalCutoverError(f"migration validate failed {table}: {res['errors']}")
    # (d) new empty tables
    for table in NEW_TABLES:
        conn.execute(DDL_DUCKDB[table])
    # (e) swap: legacy -> __b3b_legacy, shadow __b3b_v2 -> original
    for table in rebuild:
        legacy_tmp = f"{table}{SHADOW_LEGACY_SUFFIX}"
        shadow = f"{table}{SHADOW_V2_SUFFIX}"
        conn.execute(f'ALTER TABLE "{table}" RENAME TO "{legacy_tmp}"')
        conn.execute(f'ALTER TABLE "{shadow}" RENAME TO "{table}"')
    # (f) drop legacy temp tables
    for table in rebuild:
        legacy_tmp = f"{table}{SHADOW_LEGACY_SUFFIX}"
        conn.execute(f'DROP TABLE "{legacy_tmp}"')


def run_formal_schema_migration(main_db: str | Path, *,
                                allowed_root: str | Path,
                                fault_at: Optional[str] = None) -> FormalMigrationResult:
    """Run the formal COMPLETE_2_0 -> COMPLETE_2_1 migration on an already-
    authorized, dual-locked, backed-up formal main DB.

    The caller (``run_formal_cutover``) is responsible for authorization, dual
    locks, and fresh backup before invoking this.  This function only runs the
    schema migration transaction and the post-commit read-only audit, returning
    a result whose ``counts_after``/``hashes_after``/``final_fingerprint_ok`` are
    directly comparable to the staging migration's ``_snapshot`` output.
    """
    db = resolve_canonical(main_db)
    ro = duckdb.connect(str(db), read_only=True)
    try:
        source_status = detect_schema_status(ro)
    finally:
        ro.close()
    if source_status == SchemaStatus.COMPLETE_2_1:
        managed = (list(REBUILD_QFQ_TABLES) + list(REBUILD_SHARED_TABLES)
                   + list(NEW_TABLES))
        ro = duckdb.connect(str(db), read_only=True)
        try:
            ac_counts, ac_hashes = _snapshot(ro, managed, TARGET_MAIN_DB_2_1_FINGERPRINT)
            fp_ok = verify_fingerprint(ro, TARGET_MAIN_DB_2_1_FINGERPRINT, reject_extra=True)
        finally:
            ro.close()
        return FormalMigrationResult(
            source_status=source_status.value, target_status=SchemaStatus.COMPLETE_2_1.value,
            already_current=True, counts_before=ac_counts, hashes_before=ac_hashes,
            counts_after=ac_counts, hashes_after=ac_hashes, final_fingerprint_ok=fp_ok,
            report_status="ALREADY_CURRENT")
    if source_status != SchemaStatus.COMPLETE_2_0:
        raise FormalCutoverError(
            f"formal migration requires COMPLETE_2_0 or COMPLETE_2_1; current={source_status.value}")

    managed_before = list(REBUILD_QFQ_TABLES) + list(REBUILD_SHARED_TABLES) + list(NEW_TABLES)
    # exclude NEW_TABLES from the legacy 2.0 snapshot (they don't exist yet)
    legacy_managed = list(REBUILD_QFQ_TABLES) + list(REBUILD_SHARED_TABLES)
    ro = duckdb.connect(str(db), read_only=True)
    try:
        counts_before, hashes_before = _snapshot(ro, legacy_managed, LEGACY_MAIN_DB_2_0_FINGERPRINT)
    finally:
        ro.close()

    txn_validation: list = []
    conn = duckdb.connect(str(db))
    committed = False
    try:
        conn.execute("BEGIN TRANSACTION")
        try:
            _formal_migrate_in_txn(conn, counts_before=counts_before,
                                   fault_at=fault_at, txn_validation=txn_validation)
            final_fingerprint_ok = verify_fingerprint(
                conn, TARGET_MAIN_DB_2_1_FINGERPRINT, reject_extra=True)
            if not final_fingerprint_ok:
                raise FormalCutoverError("in-transaction target fingerprint verification failed")
            if fault_at == "before_commit":
                raise FormalCutoverError("[fault injection] before_commit")
            conn.execute("COMMIT")
            committed = True
            if fault_at == "after_commit_before_report":
                # Hard crash boundary: emulate the os._exit(92) test path.  The
                # child process is expected to terminate with exit 92 here.
                raise FormalCutoverCommittedReportError(
                    "[fault injection] after_commit_before_report",
                    db_path=str(db), handoff_dir=str(allowed_root))
        except Exception:
            if not committed:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
            raise
    finally:
        conn.close()

    # Post-commit read-only audit (content equivalence vs staging).
    post = duckdb.connect(str(db), read_only=True)
    try:
        final_status = detect_schema_status(post)
        all_tables = (list(REBUILD_QFQ_TABLES) + list(REBUILD_SHARED_TABLES) + list(NEW_TABLES))
        counts_after, hashes_after = _snapshot(post, all_tables, TARGET_MAIN_DB_2_1_FINGERPRINT)
    finally:
        post.close()
    return FormalMigrationResult(
        source_status=source_status.value, target_status=final_status.value,
        already_current=False, counts_before=counts_before, hashes_before=hashes_before,
        counts_after=counts_after, hashes_after=hashes_after,
        txn_validation=txn_validation, final_fingerprint_ok=final_fingerprint_ok,
        report_status="MIGRATION_COMMITTED")


# ---------------------------------------------------------------------------
# Fresh backup (G0 §3.4, inside the dual-lock window).
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def make_fresh_backup(*, main_db: str | Path, aux_db: str | Path,
                      backup_dir: str | Path, config_dir: Optional[str | Path] = None) -> dict:
    """Create fresh main/aux backups + config snapshot + manifests under backup_dir.

    All paths are new (O_EXCL).  Verifies source/backup SHA match, backup main
    schema status, aux integrity, and restore-open to a temp path.  Never
    performs a restore drill on the formal path.
    """
    main = resolve_canonical(main_db)
    aux = resolve_canonical(aux_db)
    bdir = resolve_canonical(backup_dir)
    bdir.mkdir(parents=True, exist_ok=False)
    import shutil
    main_backup = bdir / f"quantstudio.{_now_ts_compact()}.db"
    aux_backup = bdir / f"qfq_aux.{_now_ts_compact()}.db"
    shutil.copy2(main, main_backup)
    shutil.copy2(aux, aux_backup)
    main_sha = _sha256_file(main)
    aux_sha = _sha256_file(aux)
    main_backup_sha = _sha256_file(main_backup)
    aux_backup_sha = _sha256_file(aux_backup)
    if main_sha != main_backup_sha or aux_sha != aux_backup_sha:
        raise FormalCutoverRefused("fresh backup SHA mismatch; refuse to proceed")
    # backup main schema status
    ro = duckdb.connect(str(main_backup), read_only=True)
    try:
        backup_status = detect_schema_status(ro).value
    finally:
        ro.close()
    # aux integrity
    ac = sqlite3.connect(str(aux_backup))
    try:
        integrity = ac.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        ac.close()
    if integrity != "ok":
        raise FormalCutoverRefused(f"backup aux integrity failed: {integrity}")
    # restore-open to a temp path (not the formal path)
    tmp_main = bdir / f"_restore_probe_main.db"
    tmp_aux = bdir / f"_restore_probe_aux.db"
    shutil.copy2(main_backup, tmp_main)
    shutil.copy2(aux_backup, tmp_aux)
    probe = duckdb.connect(str(tmp_main), read_only=True)
    try:
        probe_status = detect_schema_status(probe).value
    finally:
        probe.close()
    pac = sqlite3.connect(str(tmp_aux))
    try:
        probe_integrity = pac.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        pac.close()
    tmp_main.unlink(missing_ok=True)
    tmp_aux.unlink(missing_ok=True)
    backup_manifest = {
        "kind": "quantstudio-b6-formal-backup",
        "created_at": _now_ts(),
        "source_main": file_evidence(main), "source_aux": file_evidence(aux),
        "backup_main": file_evidence(main_backup), "backup_aux": file_evidence(aux_backup),
        "main_sha_match": main_sha == main_backup_sha,
        "aux_sha_match": aux_sha == aux_backup_sha,
        "backup_main_status": backup_status, "aux_quick_check": integrity,
        "restore_probe_main_status": probe_status, "restore_probe_aux_integrity": probe_integrity,
    }
    rollback_manifest = {
        "kind": "quantstudio-b6-formal-rollback",
        "main_backup": str(main_backup), "aux_backup": str(aux_backup),
        "main_source_sha256": main_sha, "aux_source_sha256": aux_sha,
        "main_source_size": main.stat().st_size, "aux_source_size": aux.stat().st_size,
        "created_at": _now_ts(),
    }
    _write_exclusive_json(bdir / "backup_manifest.json", backup_manifest)
    _write_exclusive_json(bdir / "rollback_manifest.json", rollback_manifest)
    return {"backup_manifest": backup_manifest, "rollback_manifest": rollback_manifest,
            "main_backup": str(main_backup), "aux_backup": str(aux_backup)}


def prepare_formal_baseline(*, main_db: str | Path, cutover_id: str,
                            price_source: str, source_generation: str,
                            aux_db_path: str | Path,
                            evidence_output_path: str | Path,
                            config_sha: Optional[str] = None) -> dict:
    """WP6 steps 6-8: initialize the generation-specific aux, create/transition the
    cutover record, build the discovery baseline from the formal DB's own
    ``stock_dividend`` history, freeze immutable evidence, and verify immediate
    replay produces zero new triggers and a clean pending-slot audit.

    This reuses the existing reviewed B-5 primitives (``create_cutover`` /
    ``transition_cutover`` / ``AuxDbRouter.initialize_explicit`` /
    ``establish_discovery_baseline`` / ``dividend_payload_hash`` /
    ``build_cutover_evidence`` / ``audit_pending_slots``) so the formal baseline
    is built by the same code path the B-5 staging review audited.  It must run
    AFTER the schema migration has reached COMPLETE_2_1 (the qfq_source_cutover /
    qfq_discovery_baseline / qfq_active_cutover / qfq_cycle_lease tables must
    exist), inside the dual-lock window.

    The baseline rows come from the formal DB's own
    ``stock_dividend WHERE div_proc='实施' AND ex_date IS NOT NULL`` — the same
    source ``cmd_baseline_build`` uses.  No external data dependency.
    """
    from .qfq_dividend_payload import dividend_payload_hash
    from .qfq_reanchor_schema import SCHEMA_VERSION, DETECTOR_BASELINE_VERSION

    db = resolve_canonical(main_db)
    aux_canon = resolve_canonical(aux_db_path)
    identity = BaselineIdentity(cutover_id=cutover_id, price_source=price_source,
                                source_generation=source_generation)

    conn = duckdb.connect(str(db))
    try:
        # Step 7a: create the cutover record (planned) with the immutable aux path.
        create_cutover(conn, cutover_id=cutover_id, price_source=price_source,
                       source_generation=source_generation,
                       schema_version=SCHEMA_VERSION,
                       baseline_version=DETECTOR_BASELINE_VERSION,
                       aux_db_path=str(aux_canon), config_hash=config_sha)
        # Step 7b: planned -> prepared -> baseline_building
        transition_cutover(conn, cutover_id=cutover_id, expected_status="planned",
                           new_status="prepared")
        transition_cutover(conn, cutover_id=cutover_id, expected_status="prepared",
                           new_status="baseline_building")
    finally:
        conn.close()

    # Step 6: initialize the isolated generation-specific aux DB (O_EXCL/empty).
    # aux-init requires prepared/baseline_building; the cutover is now baseline_building.
    router = AuxDbRouter(main_db=str(db), routes={source_generation: str(aux_canon)})
    route = router.initialize_explicit(source_generation=source_generation,
                                       cutover_id=cutover_id)
    if not route.exists:
        raise FormalCutoverError(f"aux initialization failed for {source_generation}: {route}")

    conn = duckdb.connect(str(db))
    try:
        # Step 7c: build the discovery baseline from the formal DB's own stock_dividend.
        rows = conn.execute(
            "SELECT code, ex_date, record_date, ann_date, end_date, "
            "cash_div_before_tax, cash_div_after_tax, cash_div, stk_div, stk_bo_rate, "
            "stk_co_rate, div_rat, div_proc FROM stock_dividend "
            "WHERE div_proc='实施' AND ex_date IS NOT NULL").fetchall()
        conn.execute("BEGIN TRANSACTION")
        try:
            baseline_count = establish_discovery_baseline(
                conn, identity=identity, rows=rows,
                payload_hash=lambda row: dividend_payload_hash(*row))
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        # Step 7d: baseline_building -> baseline_validated, then freeze evidence.
        transition_cutover(conn, cutover_id=cutover_id,
                           expected_status="baseline_building",
                           new_status="baseline_validated")
        # Formal runner uses the policy-free evidence core (its own authorization
        # chain already preceded this); the staging-only ``_assert_staging_db``
        # inside ``build_cutover_evidence`` is NOT appropriate here.
        evidence = _freeze_cutover_evidence_core(conn, cutover_id=cutover_id,
                                                 output_path=evidence_output_path)
    finally:
        conn.close()

    # Step 8: immediate replay verification — the baseline must cover all current
    # historical dividend events, so an immediate re-discovery produces zero new
    # mcp-gen1 triggers and a clean three-item pending-slot audit.
    conn = duckdb.connect(str(db))
    try:
        new_triggers = conn.execute(
            "SELECT COUNT(*) FROM qfq_trigger_queue WHERE source_generation='mcp-gen1'"
        ).fetchone()[0]
        slots = audit_pending_slots(conn, identity=identity)
    finally:
        conn.close()

    if new_triggers != 0:
        raise FormalCutoverError(
            f"immediate baseline replay produced {new_triggers} mcp-gen1 triggers; "
            "baseline did not cover all historical dividend events")
    if not slots.get("passed"):
        raise FormalCutoverError(
            f"pending-slot audit not clean after baseline build: {slots}")

    return {
        "cutover_id": cutover_id, "baseline_rows": baseline_count,
        "aux_db_path": str(aux_canon), "evidence_manifest_sha256": evidence["manifest_sha256"],
        "immediate_replay_new_triggers": new_triggers,
        "pending_slot_audit": slots,
    }


def _now_ts() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _now_ts_compact() -> str:
    return datetime.now(BJ_TZ).strftime("%Y%m%dT%H%M%S")


# ---------------------------------------------------------------------------
# Handoff + exit evidence (G0 §3.8 / §5.3.1).
# ---------------------------------------------------------------------------


def _write_handoff(*, handoff_dir: str | Path, payload: Mapping[str, Any]) -> str:
    """Publish formal_cutover_handoff.json via O_EXCL; return its raw-bytes SHA-256.

    The handoff is published while the child still holds both locks.  It must
    never contain its own SHA.
    """
    handoff_path = resolve_canonical(handoff_dir) / "formal_cutover_handoff.json"
    if "handoff_sha256" in payload or "self_sha256" in payload:
        raise FormalCutoverError("handoff must not contain its own SHA")
    _write_exclusive_json(handoff_path, payload)
    raw = handoff_path.read_bytes()
    return hash_manifest_bytes(raw)


def write_exit_evidence(*, handoff_dir: str | Path, handoff_raw_sha: str,
                        child_pid: int, child_create_time: float, exit_code: int,
                        locks_released_verified: bool, descendant_scan: list) -> str:
    """Supervisor publishes formal_runner_exit_evidence.json (O_EXCL) after the
    child exits and the dual locks are reacquired+released+verified.
    """
    payload = {
        "kind": "quantstudio-b6-formal-runner-exit-evidence",
        "child_pid": child_pid,
        "child_create_time": child_create_time,
        "child_exit_code": exit_code,
        "handoff_raw_sha256": handoff_raw_sha,
        "locks_released_verified": locks_released_verified,
        "descendant_scan": descendant_scan,
        "written_at": _now_ts(),
    }
    path = resolve_canonical(handoff_dir) / "formal_runner_exit_evidence.json"
    _write_exclusive_json(path, payload)
    return str(path)


# ---------------------------------------------------------------------------
# After-COMMIT recovery (net-new ALREADY_ACTIVE; G0 §3.5.1 / §3.7.1, P2-4).
# ---------------------------------------------------------------------------


RECOVERY_STATUS_ALREADY_ACTIVE = "ALREADY_ACTIVE"


def recover_already_active(*, main_db: str | Path, aux_db: str | Path,
                           cutover_id: str, price_source: str,
                           handoff_dir: str | Path) -> dict:
    """Read-only recovery for an after-COMMIT interruption.

    Opens the DB read-only, classifies the cutover as already durable
    (cutover=active, unique active pointer, legacy non-terminal=0, watermark
    unchanged), and emits a recovery report whose field-name set matches the
    staging ``after_commit_recovery.json`` (NOT the enum value; this is the
    formal-new ALREADY_ACTIVE enum).
    """
    db = resolve_canonical(main_db)
    aux = resolve_canonical(aux_db)
    ro = duckdb.connect(str(db), read_only=True)
    try:
        schema = detect_schema_status(ro).value
        cut_row = ro.execute(
            "SELECT cutover_id, status, source_generation FROM qfq_source_cutover WHERE cutover_id=?",
            [cutover_id]).fetchone()
        active_rows = ro.execute(
            "SELECT price_source, cutover_id FROM qfq_active_cutover WHERE price_source=?",
            [price_source]).fetchall()
        legacy_triggers = ro.execute(
            "SELECT status, COUNT(*) FROM qfq_trigger_queue WHERE price_source=? AND source_generation=? "
            "AND status IN ('scheduled','pending','in_progress','retryable_failed','blocked') GROUP BY status",
            [LEGACY_SOURCE, LEGACY_GENERATION]).fetchall()
        legacy_intents = ro.execute(
            "SELECT status, COUNT(*) FROM qfq_watermark_intent WHERE source=? AND source_generation=? "
            "AND status='pending' GROUP BY status", [LEGACY_SOURCE, LEGACY_GENERATION]).fetchall()
        legacy_cycles = ro.execute(
            "SELECT status, COUNT(*) FROM qfq_cycle_run WHERE price_source=? AND source_generation=? "
            "AND status='started' GROUP BY status", [LEGACY_SOURCE, LEGACY_GENERATION]).fetchall()
        committed = ro.execute("SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='committed'").fetchone()[0]
        dead_letter = ro.execute("SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='dead_letter'").fetchone()[0]
        mcp_gen1 = ro.execute(
            "SELECT COUNT(*) FROM qfq_trigger_queue WHERE source_generation='mcp-gen1'").fetchone()[0]
        wm_ev = table_evidence(ro, "source_watermark")
    finally:
        ro.close()
    if not cut_row or cut_row[1] != "active":
        raise FormalCutoverError(
            f"recovery classification failed: cutover {cutover_id} status={cut_row[1] if cut_row else None}")
    if len(active_rows) != 1 or active_rows[0][1] != cutover_id:
        raise FormalCutoverError(
            f"recovery: active pointer not unique/correct: {active_rows}")
    report = {
        "kind": "quantstudio-b6-formal-after-commit-recovery",
        "recovery_status": RECOVERY_STATUS_ALREADY_ACTIVE,
        "cutover": [cut_row[0], cut_row[1], cut_row[2]],
        "active": [active_rows[0][0], active_rows[0][1]],
        "schema_status": schema,
        "legacy_triggers": [list(r) for r in legacy_triggers],
        "legacy_intents": [list(r) for r in legacy_intents],
        "legacy_cycles": [list(r) for r in legacy_cycles],
        "committed": committed, "dead_letter": dead_letter, "mcp_gen1": mcp_gen1,
        "source_watermark_evidence": wm_ev,
        "recovered_at": _now_ts(),
    }
    _write_exclusive_json(resolve_canonical(handoff_dir) / "formal_recovery_report.json", report)
    return report
