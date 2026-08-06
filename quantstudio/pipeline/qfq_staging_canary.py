"""B-6 post-activation staging canary and abort recovery."""
from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional, Sequence

import duckdb

from .qfq_calendar import CalendarService
from .qfq_cutover_activation import _assert_staging_db
from .qfq_orchestrator_types import QFQOrchestratorConfig
from .qfq_resident_orchestrator import QFQResidentOrchestrator

PRICE_TABLES = ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes")


class CanaryError(RuntimeError):
    pass


def _assert_staging_aux(aux_db: Path) -> None:
    try:
        from quantstudio._paths import db_path
        formal_aux = Path(db_path()).resolve().parent / "qfq_aux.db"
    except Exception as exc:
        raise CanaryError("cannot resolve formal aux path; fail-closed") from exc
    if aux_db.resolve() == formal_aux:
        raise CanaryError("cutover-canary refuses configured formal aux")


def _write_exclusive(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except FileExistsError as exc:
        raise CanaryError(f"refuse overwrite canary evidence: {path}") from exc


def recover_aborted_canary(*, main_db: str | Path, aux_db: str | Path,
                            source_generation: str = "mcp-gen1") -> dict:
    """Recover a staging-only canary killed after cycle creation.

    Any started dynamic cycle is explicitly marked interrupted. DuckDB and
    SQLite are checkpointed, SQLite integrity is checked before/after, and
    non-empty transaction sidecars must be gone when recovery returns.
    """
    main_db = _assert_staging_db(main_db)
    aux_db = Path(aux_db).resolve()
    _assert_staging_aux(aux_db)
    if not main_db.is_file() or not aux_db.is_file():
        raise CanaryError("staging main/aux missing")
    conn = duckdb.connect(str(main_db), read_only=False)
    try:
        before = conn.execute(
            "SELECT cycle_id,status,phase FROM qfq_cycle_run "
            "WHERE source_generation=? AND status='started'", [source_generation]).fetchall()
        if before:
            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute(
                    "UPDATE qfq_cycle_run SET status='interrupted',phase='interrupted',"
                    "error=?,finished_at=NOW(),updated_at=NOW() "
                    "WHERE source_generation=? AND status='started'",
                    ["post-activation canary terminated before finalize", source_generation])
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        conn.execute("CHECKPOINT")
        after = conn.execute(
            "SELECT cycle_id,status,phase,error FROM qfq_cycle_run "
            "WHERE source_generation=? ORDER BY updated_at", [source_generation]).fetchall()
    finally:
        conn.close()
    sqlite_conn = sqlite3.connect(str(aux_db), timeout=60)
    try:
        integrity_before = sqlite_conn.execute("PRAGMA integrity_check").fetchone()[0]
        checkpoint = sqlite_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        integrity_after = sqlite_conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        sqlite_conn.close()
    sidecars = [p for p in (Path(str(main_db) + ".wal"), Path(str(aux_db) + "-wal"),
                            Path(str(aux_db) + "-journal"), Path(str(aux_db) + "-shm"))
                if p.exists() and p.stat().st_size > 0]
    if integrity_before != "ok" or integrity_after != "ok" or sidecars:
        raise CanaryError(
            f"canary recovery failed: integrity={integrity_before}/{integrity_after}, sidecars={sidecars}")
    return {"started_before": before, "cycles_after": after,
            "sqlite_integrity_before": integrity_before,
            "wal_checkpoint": checkpoint, "sqlite_integrity_after": integrity_after,
            "sidecars_after": []}


def _full_noop_worker(main_db: str, aux_db: str, cutover_id: str) -> None:
    """Child-process full no-op cycle used only for timeout/recovery rehearsal."""
    raw = {
        "enabled": True, "require_bootstrap": False,
        "factor_refresh_enabled": False, "price_source": "mcp",
        "generation_mode": "dynamic", "source_generation": "mcp-gen1",
        "cutover_id": cutover_id, "stock_factor_detector": "mcp_factor_detector",
        "etf_factor_detector": "mcp_factor_detector", "freqs": ["1min"],
        "watermark_policy": "hold_until_consistent",
    }
    cfg = QFQOrchestratorConfig.from_dict(raw)
    conn = duckdb.connect(str(main_db), read_only=False)
    try:
        orch = QFQResidentOrchestrator(
            cfg, main_db=str(main_db), aux_db=str(aux_db), fetcher=object(),
            calendar=CalendarService(main_db=main_db))
        orch.prepare_runtime(conn, require_aux=True)
        cycle_id = orch.begin_cycle(conn)
        orch.run_post_ingest(
            conn, cycle_id=cycle_id, run_id=f"b6_full_noop_{int(time.time())}",
            as_of_ms=int(time.time() * 1000))
    finally:
        conn.close()


def run_full_noop_with_timeout(*, main_db: str | Path, aux_db: str | Path,
                               cutover_id: str, timeout_sec: float) -> dict:
    """Run an unscoped no-op in a child and recover staging on timeout."""
    main_db = _assert_staging_db(main_db)
    aux_db = Path(aux_db).resolve()
    _assert_staging_aux(aux_db)
    if timeout_sec <= 0:
        return {"skipped": True, "timeout_sec": timeout_sec}
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(
        target=_full_noop_worker,
        args=(str(main_db), str(aux_db), cutover_id),
        name="qfq-b6-full-noop-canary")
    proc.start()
    proc.join(timeout_sec)
    if proc.is_alive():
        proc.terminate()
        proc.join(30)
        if proc.is_alive():
            proc.kill()
            proc.join(30)
        recovery = recover_aborted_canary(main_db=main_db, aux_db=aux_db)
        return {"skipped": False, "timed_out": True, "timeout_sec": timeout_sec,
                "exit_code": proc.exitcode, "recovery": recovery}
    if proc.exitcode != 0:
        recovery = recover_aborted_canary(main_db=main_db, aux_db=aux_db)
        raise CanaryError(
            f"full no-op child failed exit={proc.exitcode}; recovery={recovery}")
    return {"skipped": False, "timed_out": False, "timeout_sec": timeout_sec,
            "exit_code": proc.exitcode}


def _price_summaries(conn) -> dict:
    return {table: conn.execute(
        f'SELECT COUNT(*),MIN(time),MAX(time) FROM "{table}"').fetchone()
        for table in PRICE_TABLES}


def run_scoped_canary(*, main_db: str | Path, aux_db: str | Path,
                       codes: Sequence[str], cutover_id: str,
                       expected_baseline_rows: int = 2181,
                       output_path: Optional[str | Path] = None,
                       recover_aborted: bool = True) -> dict:
    """Run a bounded dynamic-generation canary with global watermark hold."""
    main_db = _assert_staging_db(main_db)
    aux_db = Path(aux_db).resolve()
    _assert_staging_aux(aux_db)
    codes = tuple(dict.fromkeys(str(code).strip() for code in codes if str(code).strip()))
    if not codes:
        raise CanaryError("canary codes cannot be empty")
    recovery = recover_aborted_canary(main_db=main_db, aux_db=aux_db) if recover_aborted else None
    raw = {
        "enabled": True, "require_bootstrap": False,
        "factor_refresh_enabled": False, "price_source": "mcp",
        "generation_mode": "dynamic", "source_generation": "mcp-gen1",
        "cutover_id": cutover_id, "stock_factor_detector": "mcp_factor_detector",
        "etf_factor_detector": "mcp_factor_detector", "freqs": ["1min"],
        "watermark_policy": "hold_until_consistent",
    }
    cfg = QFQOrchestratorConfig.from_dict(raw)
    conn = duckdb.connect(str(main_db), read_only=False)
    try:
        before = {
            "baseline": conn.execute(
                "SELECT COUNT(*) FROM qfq_discovery_baseline WHERE cutover_id=?",
                [cutover_id]).fetchone()[0],
            "mcp_triggers": conn.execute(
                "SELECT COUNT(*) FROM qfq_trigger_queue WHERE source_generation='mcp-gen1'").fetchone()[0],
            "mcp_intents": conn.execute(
                "SELECT COUNT(*) FROM qfq_watermark_intent WHERE source_generation='mcp-gen1'").fetchone()[0],
            "watermark": conn.execute(
                "SELECT COUNT(*),COALESCE(SUM(CASE WHEN last_date IS NULL THEN 0 ELSE last_date END),0) "
                "FROM source_watermark").fetchone(),
            "prices": _price_summaries(conn),
        }
        orch = QFQResidentOrchestrator(
            cfg, main_db=str(main_db), aux_db=str(aux_db), fetcher=object(),
            calendar=CalendarService(main_db=main_db))
        identity = orch.prepare_runtime(conn, require_aux=True)
        cycle_id = orch.begin_cycle(conn)
        started = time.monotonic()
        summary = orch.run_post_ingest(
            conn, cycle_id=cycle_id,
            run_id=f"b6_canary_{int(time.time())}", as_of_ms=int(time.time() * 1000),
            codes_filter=codes)
        after = {
            "baseline": conn.execute(
                "SELECT COUNT(*) FROM qfq_discovery_baseline WHERE cutover_id=?",
                [cutover_id]).fetchone()[0],
            "mcp_triggers": conn.execute(
                "SELECT COUNT(*) FROM qfq_trigger_queue WHERE source_generation='mcp-gen1'").fetchone()[0],
            "mcp_intents": conn.execute(
                "SELECT COUNT(*) FROM qfq_watermark_intent WHERE source_generation='mcp-gen1'").fetchone()[0],
            "watermark": conn.execute(
                "SELECT COUNT(*),COALESCE(SUM(CASE WHEN last_date IS NULL THEN 0 ELSE last_date END),0) "
                "FROM source_watermark").fetchone(),
            "prices": _price_summaries(conn),
        }
    finally:
        conn.close()
    gate = summary.gate_report or {}
    assertions = {
        "dynamic_identity_active": identity == {
            "price_source": "mcp", "source_generation": "mcp-gen1", "cutover_id": cutover_id},
        "baseline_preserved": before["baseline"] == after["baseline"] == expected_baseline_rows,
        "no_new_mcp_trigger": before["mcp_triggers"] == after["mcp_triggers"] == 0,
        "no_mcp_intent": before["mcp_intents"] == after["mcp_intents"] == 0,
        "source_watermark_unchanged": before["watermark"] == after["watermark"],
        "price_summaries_unchanged": before["prices"] == after["prices"],
        "scoped_gate_passed": gate.get("scoped_gate_passed") is True,
        "global_watermark_forced_hold": summary.status == "finalized_held" and gate.get("passed") is False,
    }
    if not all(assertions.values()):
        raise CanaryError(f"canary assertion failed: {assertions}")
    payload = {
        "kind": "b6-post-activation-staging-canary", "codes": list(codes),
        "cutover_id": cutover_id, "runtime_identity": identity,
        "elapsed_sec": round(time.monotonic() - started, 3), "recovery": recovery,
        "before": before, "after": after,
        "summary": {"cycle_id": cycle_id, "status": summary.status,
                    "triggers_found": summary.triggers_found, "claimed": summary.claimed,
                    "committed": summary.committed, "pending_due": summary.pending_due,
                    "error": summary.error, "gate_report": gate},
        "assertions": assertions,
    }
    if output_path is not None:
        _write_exclusive(Path(output_path).resolve(), payload)
        payload["output_path"] = str(Path(output_path).resolve())
    return payload
