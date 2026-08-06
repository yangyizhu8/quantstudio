"""B-4 MCP cutover staging drill and pre-production checkpoint.

This tool is intentionally outside the production daemon path.  It copies the
formal databases while holding QuantStudio's daemon/collector locks, then
performs the B-4 rehearsal only on the copies:

* legacy 2.0 dry-run and pre-COMMIT rollback;
* normal 2.0 -> 2.1 migration and already-current audit;
* post-COMMIT interruption and fresh-report recovery;
* offline MCP bootstrap / first-discover characterization and replay check using a controlled
  auxiliary database;
* frozen pre-cutover sentinel and no-active-cutover assertions.

It does not implement B-5 generation-aware SQL, does not activate mcp-gen1,
and never writes the formal DuckDB/SQLite files.  Without ``--execute`` the
command performs preflight only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import duckdb
from filelock import FileLock, Timeout

from quantstudio._paths import db_path as production_db_path
from quantstudio.pipeline.daemon_lifecycle import (
    collector_run_lock_path,
    daemon_lock_path,
    read_daemon_status,
    verify_daemon_identity,
)
from quantstudio.pipeline.qfq_event_discovery import EventDiscovery
from quantstudio.pipeline.qfq_orchestrator_types import QFQOrchestratorConfig
from quantstudio.pipeline.qfq_reanchor_schema import init_sqlite_schema
from quantstudio.pipeline.qfq_schema_contracts import (
    CUTOVER_LEGACY_XTQUANT_PRE_CUTOVER,
    GENERATION_LEGACY_XTQUANT,
)
from quantstudio.pipeline.qfq_schema_migration import (
    MigrationCommittedReportError,
    QfqMigrationError,
    REPORT_STATUS_ALREADY_CURRENT,
    REPORT_STATUS_DRY_RUN_COMPLETE,
    REPORT_STATUS_MIGRATION_COMMITTED,
    REPORT_STATUS_ROLLED_BACK,
    migrate_reanchor_2_0_to_2_1,
)
from quantstudio.pipeline.qfq_schema_status import SchemaStatus, detect_schema_status

BJ_TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = ROOT / "output" / "mcp_migration"
QFQ_GENERATION_TABLES: Tuple[str, ...] = (
    "qfq_anchor_state",
    "qfq_reanchor_event",
    "qfq_pending_backfill",
    "qfq_bootstrap_run",
    "qfq_cycle_run",
    "qfq_trigger_queue",
    "qfq_watermark_intent",
    "qfq_fresh_capture",
    "qfq_observation_cursor",
    "source_watermark",
)


class B4DrillError(RuntimeError):
    """B-4 preflight or rehearsal failed closed."""


@dataclass(frozen=True)
class FileEvidence:
    path: str
    canonical_path: str
    size: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class LockEvidence:
    daemon_status: Optional[dict]
    daemon_identity: str
    daemon_lock_acquirable: bool
    collector_lock_acquirable: bool


def _now_iso() -> str:
    return datetime.now(BJ_TZ).isoformat(timespec="seconds")


def _run_id() -> str:
    return datetime.now(BJ_TZ).strftime("b4_%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]


def _sha256_file(path: Path, chunk_size: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def file_evidence(path: Path) -> FileEvidence:
    path = Path(path)
    if not path.is_file():
        raise B4DrillError(f"required file is missing: {path}")
    st = path.stat()
    return FileEvidence(
        path=str(path),
        canonical_path=str(path.resolve()),
        size=int(st.st_size),
        mtime_ns=int(st.st_mtime_ns),
        sha256=_sha256_file(path),
    )


def _write_json_exclusive(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(str(path), flags, 0o600)
    try:
        data = (json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf-8")
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)


def _schema_status(path: Path) -> str:
    conn = duckdb.connect(str(path), read_only=True)
    try:
        return detect_schema_status(conn).value
    finally:
        conn.close()


def _git_gate() -> dict:
    def run(*args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=str(ROOT), text=True, encoding="utf-8", errors="replace"
        ).strip()

    cached = run("diff", "--cached", "--name-only")
    return {
        "head": run("rev-parse", "HEAD"),
        "staged_paths": [line for line in cached.splitlines() if line.strip()],
    }


def _try_lock(path: Path) -> bool:
    lock = FileLock(str(path), timeout=0)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return False
    else:
        lock.release()
        return True


def lock_evidence() -> LockEvidence:
    status = read_daemon_status()
    identity = verify_daemon_identity(status) if status else "no-status"
    return LockEvidence(
        daemon_status=status,
        daemon_identity=identity,
        daemon_lock_acquirable=_try_lock(daemon_lock_path()),
        collector_lock_acquirable=_try_lock(collector_run_lock_path()),
    )


@contextmanager
def _hold_copy_locks() -> Iterator[None]:
    """Hold both framework locks for the complete formal-file snapshot copy."""
    daemon = FileLock(str(daemon_lock_path()), timeout=0)
    collector = FileLock(str(collector_run_lock_path()), timeout=0)
    with ExitStack() as stack:
        try:
            stack.enter_context(daemon.acquire(timeout=0))
        except Timeout as exc:
            raise B4DrillError("QuantStudio daemon lock is busy; refuse inconsistent staging copy") from exc
        try:
            stack.enter_context(collector.acquire(timeout=0))
        except Timeout as exc:
            raise B4DrillError("collector_run lock is busy; refuse inconsistent staging copy") from exc
        yield


def _required_space(main_size: int, aux_size: int) -> int:
    # Baseline plus two migration branches. Reserve conservatively for copy
    # growth/checkpoints: 5x main + one aux + 10 GiB headroom.
    return main_size * 5 + aux_size + (10 << 30)

def _nonempty_transaction_sidecars(main_db: Path, aux_db: Path) -> List[dict]:
    """Return non-empty WAL/journal files that make a raw file copy unsafe."""
    candidates = (
        Path(str(main_db) + ".wal"),
        Path(str(aux_db) + "-wal"),
        Path(str(aux_db) + "-journal"),
    )
    return [
        {"path": str(path), "size": int(path.stat().st_size)}
        for path in candidates
        if path.exists() and path.stat().st_size > 0
    ]


def _assert_no_nonempty_transaction_sidecars(main_db: Path, aux_db: Path) -> None:
    sidecars = _nonempty_transaction_sidecars(main_db, aux_db)
    if sidecars:
        raise B4DrillError(
            "non-empty transaction sidecar exists; refuse raw staging copy: "
            f"{sidecars}"
        )


def preflight(formal_db: Path, formal_aux: Path, output_root: Path, run_id: str) -> dict:
    formal_db = formal_db.resolve()
    formal_aux = formal_aux.resolve()
    expected_prod = Path(production_db_path()).resolve()
    expected_aux = expected_prod.parent / "qfq_aux.db"
    if formal_db != expected_prod or formal_aux != expected_aux:
        raise B4DrillError(
            "B-4 formal paths must be the configured production main/aux paths; "
            f"got main={formal_db}, aux={formal_aux}"
        )
    if not formal_db.is_file() or not formal_aux.is_file():
        raise B4DrillError("formal main/aux database is missing")
    _assert_no_nonempty_transaction_sidecars(formal_db, formal_aux)
    target = (output_root / run_id).resolve()
    if target.exists():
        raise B4DrillError(f"B-4 run directory already exists (no overwrite): {target}")

    locks = lock_evidence()
    if not locks.daemon_lock_acquirable or not locks.collector_lock_acquirable:
        raise B4DrillError(f"QuantStudio writer lock is busy: {asdict(locks)}")

    main_status = _schema_status(formal_db)
    if main_status != SchemaStatus.COMPLETE_2_0.value:
        raise B4DrillError(
            f"formal main DB must remain COMPLETE_2_0 before production cutover; got {main_status}"
        )

    usage = shutil.disk_usage(output_root.resolve().anchor or str(output_root.resolve()))
    required = _required_space(formal_db.stat().st_size, formal_aux.stat().st_size)
    if usage.free < required:
        raise B4DrillError(
            f"insufficient free space for B-4 full-copy branches: free={usage.free}, required={required}"
        )

    git = _git_gate()
    if git["staged_paths"]:
        raise B4DrillError(f"staging index must remain empty before B-4: {git['staged_paths']}")

    return {
        "checked_at": _now_iso(),
        "formal_db": str(formal_db),
        "formal_aux": str(formal_aux),
        "formal_schema_status": main_status,
        "run_dir": str(target),
        "free_bytes": usage.free,
        "required_bytes": required,
        "locks": asdict(locks),
        "git": git,
        "production_ready": False,
        "reason": "B-4 rehearsal only; B-5 through B-8 and a separate production confirmation remain required",
    }


def _copy_verified(source: Path, target: Path, source_ev: FileEvidence) -> FileEvidence:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise B4DrillError(f"copy target already exists: {target}")
    shutil.copy2(source, target)
    target_ev = file_evidence(target)
    if target_ev.size != source_ev.size or target_ev.sha256 != source_ev.sha256:
        raise B4DrillError(f"copy verification failed: {source} -> {target}")
    return target_ev


def prepare_copies(
    formal_db: Path, formal_aux: Path, run_dir: Path
) -> Tuple[dict, Path, Path, Path, Path]:
    """Create immutable baseline plus normal/recovery branches under held locks."""
    baseline_dir = run_dir / "baseline"
    normal_dir = run_dir / "normal"
    recovery_dir = run_dir / "recovery"
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=False)

    with _hold_copy_locks():
        _assert_no_nonempty_transaction_sidecars(formal_db, formal_aux)
        main_before = file_evidence(formal_db)
        aux_before = file_evidence(formal_aux)
        baseline_db = baseline_dir / "quantstudio.db"
        baseline_aux = baseline_dir / "qfq_aux.db"
        baseline_main_ev = _copy_verified(formal_db, baseline_db, main_before)
        baseline_aux_ev = _copy_verified(formal_aux, baseline_aux, aux_before)
        main_after = file_evidence(formal_db)
        aux_after = file_evidence(formal_aux)

    if main_before != main_after or aux_before != aux_after:
        raise B4DrillError("formal database evidence changed during staging copy")

    normal_db = normal_dir / "quantstudio.db"
    recovery_db = recovery_dir / "quantstudio.db"
    _copy_verified(baseline_db, normal_db, baseline_main_ev)
    _copy_verified(baseline_db, recovery_db, baseline_main_ev)

    # B-4 uses a controlled new-generation-style aux database for offline
    # bootstrap/discover.  The full copied aux remains immutable evidence.
    drill_aux = normal_dir / "qfq_aux_b4.db"
    _build_controlled_aux(baseline_aux, drill_aux)

    manifest = {
        "created_at": _now_iso(),
        "formal_before": {"main": asdict(main_before), "aux": asdict(aux_before)},
        "formal_after_copy": {"main": asdict(main_after), "aux": asdict(aux_after)},
        "baseline": {"main": asdict(baseline_main_ev), "aux": asdict(baseline_aux_ev)},
        "branches": {
            "normal_db": str(normal_db),
            "recovery_db": str(recovery_db),
            "controlled_aux": str(drill_aux),
        },
    }
    _write_json_exclusive(evidence_dir / "copy_manifest.json", manifest)
    _write_json_exclusive(
        run_dir / ".quantstudio_b4_staging.json",
        {
            "kind": "quantstudio-b4-staging",
            "created_at": _now_iso(),
            "formal_main": str(formal_db.resolve()),
            "formal_aux": str(formal_aux.resolve()),
            "copy_manifest": str((evidence_dir / "copy_manifest.json").resolve()),
        },
    )
    return manifest, normal_db, recovery_db, drill_aux, baseline_db


def _build_controlled_aux(source_aux: Path, target_aux: Path) -> None:
    """Build a small offline aux DB from real factor rows, excluding legacy alerts."""
    if target_aux.exists():
        raise B4DrillError(f"controlled aux already exists: {target_aux}")
    target_aux.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(target_aux)) as out:
        init_sqlite_schema(out)
        with sqlite3.connect(f"file:{source_aux.as_posix()}?mode=ro", uri=True) as src:
            for table in ("adj_factor", "fund_adj"):
                exists = src.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", [table]
                ).fetchone()[0]
                if not exists:
                    continue
                rows = src.execute(
                    f"SELECT code, time, adj_factor FROM {table} "
                    "WHERE adj_factor IS NOT NULL ORDER BY time DESC, code LIMIT 8"
                ).fetchall()
                out.executemany(
                    f"INSERT OR REPLACE INTO {table}(code,time,adj_factor) VALUES (?,?,?)", rows
                )
        out.commit()


def _migration_report_summary(report) -> dict:
    return {
        "source_status": report.source_status,
        "target_status": report.target_status,
        "report_status": report.report_status,
        "report_path": report.report_path,
        "applied": report.applied,
        "already_current": report.already_current,
        "row_counts_before": report.row_counts_before,
        "row_counts_after": report.row_counts_after,
        "hashes_before": report.hashes_before,
        "hashes_after": report.hashes_after,
        "validation_results": report.validation_results,
    }


def run_normal_migration_drill(db: Path, branch_dir: Path) -> dict:
    """Validate rollback, normal apply and idempotent already-current audit."""
    branch_dir = branch_dir.resolve()
    dry_path = branch_dir / "report_01_dry_run.json"
    dry = migrate_reanchor_2_0_to_2_1(
        db, allowed_root=branch_dir, apply=False, report_path=dry_path
    )
    if dry.report_status != REPORT_STATUS_DRY_RUN_COMPLETE:
        raise B4DrillError(f"unexpected dry-run status: {dry.report_status}")

    rollback_path = branch_dir / "report_02_before_commit_rollback.json"
    try:
        migrate_reanchor_2_0_to_2_1(
            db,
            allowed_root=branch_dir,
            apply=True,
            failure_injection="before_commit",
            report_path=rollback_path,
        )
    except QfqMigrationError:
        pass
    else:
        raise B4DrillError("before_commit failure injection unexpectedly succeeded")
    rollback_payload = json.loads(rollback_path.read_text(encoding="utf-8"))
    if rollback_payload.get("report_status") != REPORT_STATUS_ROLLED_BACK:
        raise B4DrillError(f"rollback report is not ROLLED_BACK: {rollback_payload}")
    if _schema_status(db) != SchemaStatus.COMPLETE_2_0.value:
        raise B4DrillError("pre-COMMIT injected failure did not restore COMPLETE_2_0")

    post_rollback_path = branch_dir / "report_03_post_rollback_audit.json"
    post_rollback = migrate_reanchor_2_0_to_2_1(
        db, allowed_root=branch_dir, apply=False, report_path=post_rollback_path
    )
    if post_rollback.hashes_before != dry.hashes_before:
        raise B4DrillError("logical content hashes changed after pre-COMMIT rollback")

    apply_path = branch_dir / "report_04_apply.json"
    applied = migrate_reanchor_2_0_to_2_1(
        db, allowed_root=branch_dir, apply=True, report_path=apply_path
    )
    if applied.report_status != REPORT_STATUS_MIGRATION_COMMITTED:
        raise B4DrillError(f"normal migration not committed: {applied.report_status}")
    if _schema_status(db) != SchemaStatus.COMPLETE_2_1.value:
        raise B4DrillError("normal branch is not COMPLETE_2_1 after apply")

    current_path = branch_dir / "report_05_already_current.json"
    current = migrate_reanchor_2_0_to_2_1(
        db, allowed_root=branch_dir, apply=True, report_path=current_path
    )
    if current.report_status != REPORT_STATUS_ALREADY_CURRENT:
        raise B4DrillError(f"idempotent rerun not ALREADY_CURRENT: {current.report_status}")

    return {
        "dry_run": _migration_report_summary(dry),
        "rollback": rollback_payload,
        "post_rollback": _migration_report_summary(post_rollback),
        "apply": _migration_report_summary(applied),
        "already_current": _migration_report_summary(current),
    }


def run_recovery_migration_drill(db: Path, branch_dir: Path) -> dict:
    """Validate accepted TD-42 recovery contract on a separate full branch."""
    branch_dir = branch_dir.resolve()
    interrupted_path = branch_dir / "report_01_after_commit_interrupted.json"
    try:
        migrate_reanchor_2_0_to_2_1(
            db,
            allowed_root=branch_dir,
            apply=True,
            failure_injection="after_commit_before_report",
            report_path=interrupted_path,
        )
    except MigrationCommittedReportError as exc:
        committed_error = str(exc)
    else:
        raise B4DrillError("after_commit_before_report did not raise committed error")
    if _schema_status(db) != SchemaStatus.COMPLETE_2_1.value:
        raise B4DrillError("post-COMMIT interrupted branch is not COMPLETE_2_1")

    recovery_path = branch_dir / "report_02_fresh_already_current.json"
    recovered = migrate_reanchor_2_0_to_2_1(
        db, allowed_root=branch_dir, apply=True, report_path=recovery_path
    )
    if recovered.report_status != REPORT_STATUS_ALREADY_CURRENT:
        raise B4DrillError(f"fresh report recovery failed: {recovered.report_status}")
    return {
        "committed_error": committed_error,
        "interrupted_report": json.loads(interrupted_path.read_text(encoding="utf-8")),
        "recovery": _migration_report_summary(recovered),
    }


def _count_generation_value(conn, value: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
    }
    for table in QFQ_GENERATION_TABLES:
        if table not in existing:
            continue
        cols = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='main' AND table_name=?",
                [table],
            ).fetchall()
        }
        if "source_generation" in cols:
            counts[table] = int(
                conn.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE source_generation=?', [value]
                ).fetchone()[0]
            )
    return counts


def run_offline_mcp_bootstrap_discover(db: Path, aux_db: Path) -> dict:
    """Exercise MCP-configured baseline/discover without network or active cutover."""
    cfg = QFQOrchestratorConfig.from_dict(
        {
            "enabled": True,
            "factor_refresh_enabled": False,
            "require_bootstrap": False,
            "price_source": "mcp",
            # Attack values: B-4/B-3a must persist only frozen legacy sentinels.
            "source_generation": "mcp-gen1",
            "cutover_id": "cut_b4_must_not_activate",
        }
    )
    as_of_ms = int(time.time() * 1000)
    conn = duckdb.connect(str(db), read_only=False)
    try:
        if detect_schema_status(conn) is not SchemaStatus.COMPLETE_2_1:
            raise B4DrillError("bootstrap/discover branch must be COMPLETE_2_1")
        trigger_before = int(
            conn.execute("SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0]
        )
        disc = EventDiscovery(cfg, aux_db=str(aux_db))
        baseline = disc.establish_baseline(
            conn, as_of_ms=as_of_ms, run_id="b4_mcp_baseline"
        )
        trigger_after_baseline = int(
            conn.execute("SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0]
        )
        if trigger_after_baseline != trigger_before:
            raise B4DrillError("MCP bootstrap flooded historical triggers")

        new_dividends = disc.scan_stock_dividend(
            conn, as_of_ms=as_of_ms, run_id="b4_first_discover", bootstrap=False
        )
        stock_obs = disc.observe_stock_adj_factor(
            conn, as_of_ms=as_of_ms, run_id="b4_first_discover", bootstrap=False
        )
        etf_obs = disc.observe_etf_fund_adj(
            conn, as_of_ms=as_of_ms, run_id="b4_first_discover", bootstrap=False
        )
        revision_triggers = disc.consume_revision_alerts(
            conn, run_id="b4_first_discover", as_of_ms=as_of_ms
        )
        trigger_after_discover = int(
            conn.execute("SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0]
        )
        # B-4 characterizes first-discover output.  Dividend discovery is intentionally
        # a full-table hash scan in the current pre-B-5 implementation; bootstrap only
        # establishes its cursor and does not yet install the B-5 discovery-baseline CAS.
        # Therefore first-pass dividend additions are evidence, not a B-4 failure.  The
        # required current-contract property is deterministic replay: the same scan and
        # factor observations must add no further trigger on an immediate second pass.
        replay_dividends = disc.scan_stock_dividend(
            conn, as_of_ms=as_of_ms, run_id="b4_first_discover_replay", bootstrap=False
        )
        replay_stock = disc.observe_stock_adj_factor(
            conn, as_of_ms=as_of_ms, run_id="b4_first_discover_replay", bootstrap=False
        )
        replay_etf = disc.observe_etf_fund_adj(
            conn, as_of_ms=as_of_ms, run_id="b4_first_discover_replay", bootstrap=False
        )
        replay_revisions = disc.consume_revision_alerts(
            conn, run_id="b4_first_discover_replay", as_of_ms=as_of_ms
        )
        trigger_after_replay = int(
            conn.execute("SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0]
        )
        if replay_dividends or replay_stock.new_count or replay_etf.new_count or replay_revisions:
            raise B4DrillError(
                "immediate first-discover replay is not idempotent: "
                f"dividends={len(replay_dividends)}, stock={replay_stock.new_count}, "
                f"etf={replay_etf.new_count}, revisions={len(replay_revisions)}"
            )
        if trigger_after_replay != trigger_after_discover:
            raise B4DrillError(
                "trigger count changed on immediate first-discover replay: "
                f"first={trigger_after_discover}, replay={trigger_after_replay}"
            )

        cursor_rows = conn.execute(
            "SELECT detector_name, asset_type, price_source, source_generation "
            "FROM qfq_observation_cursor WHERE price_source='mcp' ORDER BY 1,2"
        ).fetchall()
        if not cursor_rows:
            raise B4DrillError("MCP-configured baseline did not persist observation cursors")
        for row in cursor_rows:
            # The frozen qfq_observation_cursor 2.1 fingerprint has no cutover_id
            # column.  Its generation must still remain the B-3a legacy sentinel.
            if row[3] != GENERATION_LEGACY_XTQUANT:
                raise B4DrillError(f"B-4 persisted active-looking generation identity: {row}")

        active_count = int(conn.execute("SELECT COUNT(*) FROM qfq_active_cutover").fetchone()[0])
        mcp_gen1_counts = _count_generation_value(conn, "mcp-gen1")
        if active_count != 0 or any(mcp_gen1_counts.values()):
            raise B4DrillError(
                f"B-4 crossed B-6 boundary: active={active_count}, mcp_gen1={mcp_gen1_counts}"
            )
        conn.commit()
        return {
            "as_of_ms": as_of_ms,
            "baseline": baseline,
            "trigger_count_before": trigger_before,
            "trigger_count_after_baseline": trigger_after_baseline,
            "trigger_count_after_first_discover": trigger_after_discover,
            "trigger_count_after_replay": trigger_after_replay,
            "first_discover": {
                "dividend_triggers": len(new_dividends),
                "stock_observation_new": stock_obs.new_count,
                "etf_observation_new": etf_obs.new_count,
                "revision_triggers": len(revision_triggers),
                "contract_note": (
                    "pre-B-5 stock_dividend discovery is a full-table hash scan; "
                    "first-pass additions are recorded, immediate replay must be zero"
                ),
            },
            "immediate_replay": {
                "dividend_triggers": len(replay_dividends),
                "stock_observation_new": replay_stock.new_count,
                "etf_observation_new": replay_etf.new_count,
                "revision_triggers": len(replay_revisions),
            },
            "mcp_cursor_rows": [list(row) for row in cursor_rows],
            "active_cutover_count": active_count,
            "mcp_gen1_counts": mcp_gen1_counts,
            "boundary": "static pre-cutover only; B-5/B-6 not implemented or activated",
        }
    except Exception:
        # EventDiscovery uses DuckDB autocommit in the current pre-B-5 path.
        # Do not mask the original diagnostic with "no transaction is active".
        raise
    finally:
        conn.close()


def run_full_drill(formal_db: Path, formal_aux: Path, output_root: Path, run_id: str) -> dict:
    run_dir = (output_root / run_id).resolve()
    checkpoint = preflight(formal_db, formal_aux, output_root, run_id)
    run_dir.mkdir(parents=True, exist_ok=False)
    _write_json_exclusive(run_dir / "preflight.json", checkpoint)

    manifest, normal_db, recovery_db, drill_aux, baseline_db = prepare_copies(
        formal_db.resolve(), formal_aux.resolve(), run_dir
    )
    normal = run_normal_migration_drill(normal_db, normal_db.parent)
    recovery = run_recovery_migration_drill(recovery_db, recovery_db.parent)
    bootstrap_discover = run_offline_mcp_bootstrap_discover(normal_db, drill_aux)

    formal_end = {
        "main": asdict(file_evidence(formal_db.resolve())),
        "aux": asdict(file_evidence(formal_aux.resolve())),
    }
    if formal_end != manifest["formal_before"]:
        raise B4DrillError("formal database evidence changed during B-4 rehearsal")

    report = {
        "run_id": run_id,
        "started_at": checkpoint["checked_at"],
        "finished_at": _now_iso(),
        "status": "PASS",
        "scope": "B-4 only",
        "preflight": checkpoint,
        "copy_manifest": manifest,
        "normal_migration": normal,
        "recovery_migration": recovery,
        "mcp_bootstrap_first_discover": bootstrap_discover,
        "formal_final": formal_end,
        "branch_status": {
            "baseline": _schema_status(baseline_db),
            "normal": _schema_status(normal_db),
            "recovery": _schema_status(recovery_db),
        },
        "b5_b6_excluded": [
            "dynamic generation/cutover SQL filtering",
            "global RETURNING conversion",
            "legacy trigger retirement",
            "mcp-gen1 activation",
            "active cutover pointer transition",
        ],
        "production_ready": False,
        "next_gate": "independent B-4 review, then explicit authorization before B-5",
        "git_sync_authorized": False,
    }
    _write_json_exclusive(run_dir / "b4_drill_report.json", report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="B-4 MCP cutover staging rehearsal")
    parser.add_argument("--formal-db", default=str(Path(production_db_path()).resolve()))
    parser.add_argument(
        "--formal-aux",
        default=str((Path(production_db_path()).resolve().parent / "qfq_aux.db")),
    )
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="create verified full staging copies and execute the drill; default is preflight only",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    formal_db = Path(args.formal_db)
    formal_aux = Path(args.formal_aux)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or _run_id()
    try:
        if args.execute:
            payload = run_full_drill(formal_db, formal_aux, output_root, run_id)
        else:
            payload = preflight(formal_db, formal_aux, output_root, run_id)
            payload["status"] = "PREFLIGHT_PASS"
            payload["execute_required"] = True
    except B4DrillError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {"status": "ERROR", "error_type": type(exc).__name__, "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
