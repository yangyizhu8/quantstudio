"""B-6 staging-only cutover evidence, retirement, and activation.

This module deliberately refuses to touch the configured formal database.  It
implements the local/staging transaction boundary used to rehearse B-6:
immutable evidence verification, expected-old active-pointer CAS, legacy
trigger/cycle retirement, pending watermark-intent supersede, and rollback on
any pre-commit failure.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from .qfq_cutover import CutoverCASFailed, CutoverError, _now_ts
from .qfq_snapshot_evidence import _columns, _json_value, canonical_rows, file_evidence, manifest_hash, table_evidence

PRICE_TABLES = ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes")
LEGACY_NONTERMINAL = ("scheduled", "pending", "in_progress", "retryable_failed", "blocked")
LEGACY_GENERATION = "xtquant-legacy"
LEGACY_SOURCE = "xtquant"
ACTIVATION_EVIDENCE_VERSION = "b6-staging-evidence-1"


class CutoverPreconditionFailed(CutoverError):
    pass


def _table_names(conn) -> set[str]:
    try:
        return {str(r[0]) for r in conn.execute("SHOW TABLES").fetchall()}
    except Exception:
        try:
            return {str(r[0]) for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        except Exception:
            return set()


def _hash_payload(value) -> str:
    import hashlib
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _query_evidence(conn, table: str, columns: Sequence[str], *, where: str = "", params: Sequence = ()) -> dict:
    names = set(_columns(conn, table))
    for col in columns:
        if col not in names:
            raise CutoverPreconditionFailed(f"{table} missing evidence column {col}")
    cols = ", ".join(f'"{c}"' for c in columns)
    order = ", ".join(f'"{c}"' for c in columns)
    sql = f'SELECT {cols} FROM "{table}"'
    if where:
        sql += f" WHERE {where}"
    sql += f" ORDER BY {order}"
    rows = conn.execute(sql, list(params)).fetchall()
    blob = canonical_rows(columns, rows)
    return {"table": table, "columns": list(columns), "row_count": len(rows),
            "content_sha256": __import__("hashlib").sha256(blob).hexdigest()}


def _require_tables(conn, tables: Iterable[str]) -> None:
    actual = _table_names(conn)
    missing = [t for t in tables if t not in actual]
    if missing:
        raise CutoverPreconditionFailed(f"missing required staging tables: {missing}")


def _aux_evidence(path: Path) -> dict:
    if not path.is_file():
        raise CutoverPreconditionFailed(f"dynamic aux file missing: {path}")
    conn = sqlite3.connect(str(path))
    try:
        _require_tables(conn, ("adj_factor", "fund_adj"))
        entries = [table_evidence(conn, t) for t in ("adj_factor", "fund_adj")]
        return {"file": file_evidence(path), "tables": entries,
                "manifest_sha256": manifest_hash(entries)}
    finally:
        conn.close()


def _write_exclusive_json(path: Path, payload: Mapping) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            path.chmod(0o444)
        except OSError:
            pass
    except FileExistsError:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != dict(payload):
            raise CutoverPreconditionFailed(f"evidence file already exists with different content: {path}")


def _record(conn, cutover_id: str) -> dict:
    row = conn.execute("SELECT * FROM qfq_source_cutover WHERE cutover_id=?", [cutover_id]).fetchone()
    if row is None:
        raise CutoverPreconditionFailed(f"cutover does not exist: {cutover_id}")
    cols = [r[0] for r in conn.execute("DESCRIBE qfq_source_cutover").fetchall()]
    return dict(zip(cols, row))


def _assert_staging_db(main_db_path: str | Path) -> Path:
    """Use the single configured production path and reject it unconditionally."""
    db = Path(main_db_path).resolve()
    try:
        from quantstudio._paths import db_path as configured_db_path
        prod = Path(configured_db_path()).resolve()
    except Exception as exc:
        raise CutoverPreconditionFailed(
            "cannot resolve configured formal database path; fail-closed") from exc
    if db == prod:
        raise CutoverPreconditionFailed(
            "B-6 is staging-only; configured formal database is rejected")
    return db


def build_cutover_evidence(conn, *, cutover_id: str, main_db_path: str | Path,
                           output_path: str | Path) -> dict:
    """Freeze immutable main/aux snapshots for a baseline_validated cutover."""
    _assert_staging_db(main_db_path)
    rec = _record(conn, cutover_id)
    if rec["status"] != "baseline_validated":
        raise CutoverPreconditionFailed(f"evidence freeze requires baseline_validated, current={rec['status']!r}")
    if not rec.get("aux_db_path"):
        raise CutoverPreconditionFailed("dynamic cutover is missing immutable aux_db_path")
    _require_tables(conn, (*PRICE_TABLES, "source_watermark", "qfq_discovery_baseline"))
    main_entries = [table_evidence(conn, t) for t in PRICE_TABLES]
    wm = table_evidence(conn, "source_watermark")
    baseline = _query_evidence(conn, "qfq_discovery_baseline",
        ("price_source", "source_generation", "cutover_id", "event_logical_key", "applied_payload_hash"),
        where="price_source=? AND source_generation=? AND cutover_id=?",
        params=(rec["price_source"], rec["source_generation"], cutover_id))
    aux = _aux_evidence(Path(rec["aux_db_path"]))
    snapshots = {"price_tables": main_entries, "source_watermark": wm,
                 "discovery_baseline": baseline, "aux": aux}
    payload = {
        "kind": ACTIVATION_EVIDENCE_VERSION, "cutover_id": cutover_id,
        "price_source": rec["price_source"], "source_generation": rec["source_generation"],
        "schema_version": rec["schema_version"], "baseline_version": rec["baseline_version"],
        "aux_db_path": str(Path(rec["aux_db_path"]).resolve()),
        "snapshots": snapshots,
        "manifest_sha256": _hash_payload(snapshots),
    }
    _write_exclusive_json(Path(output_path), payload)
    out = Path(output_path).resolve()
    row = conn.execute(
        "UPDATE qfq_source_cutover SET price_snapshot_version=?, factor_snapshot_version=?, "
        "evidence_path=?, updated_at=? WHERE cutover_id=? AND status='baseline_validated' "
        "RETURNING cutover_id", [manifest_hash(main_entries), aux["manifest_sha256"], str(out), _now_ts(), cutover_id]).fetchone()
    if row is None:
        raise CutoverCASFailed("cutover evidence update CAS failed")
    payload["evidence_path"] = str(out)
    return payload


def verify_cutover_evidence(conn, *, cutover_id: str, main_db_path: str | Path) -> dict:
    """Verify immutable evidence and current staging state before activation."""
    _assert_staging_db(main_db_path)
    rec = _record(conn, cutover_id)
    if rec["status"] not in ("baseline_validated", "active"):
        raise CutoverPreconditionFailed(f"staging snapshot mismatch: {rec['status']!r}")
    path = Path(rec.get("evidence_path") or "").resolve()
    if not path.is_file():
        raise CutoverPreconditionFailed(f"missing immutable evidence: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("kind") != ACTIVATION_EVIDENCE_VERSION or payload.get("cutover_id") != cutover_id:
        raise CutoverPreconditionFailed("evidence version or cutover_id mismatch")
    if payload.get("manifest_sha256") != _hash_payload(payload["snapshots"]):
        raise CutoverPreconditionFailed("evidence manifest hash mismatch")
    aux_path = Path(payload["aux_db_path"]).resolve()
    if str(aux_path) != str(Path(rec["aux_db_path"]).resolve()):
        raise CutoverPreconditionFailed("evidence aux_db_path does not match cutover record")
    current_aux = _aux_evidence(aux_path)
    if current_aux["file"]["sha256"] != payload["snapshots"]["aux"]["file"]["sha256"]:
        raise CutoverPreconditionFailed("aux file changed")
    _require_tables(conn, (*PRICE_TABLES, "source_watermark", "qfq_discovery_baseline"))
    current = {
        "price_tables": [table_evidence(conn, t) for t in PRICE_TABLES],
        "source_watermark": table_evidence(conn, "source_watermark"),
        "discovery_baseline": _query_evidence(conn, "qfq_discovery_baseline",
            ("price_source", "source_generation", "cutover_id", "event_logical_key", "applied_payload_hash"),
            where="price_source=? AND source_generation=? AND cutover_id=?",
            params=(rec["price_source"], rec["source_generation"], cutover_id)),
    }
    for key in current:
        if current[key] != payload["snapshots"][key]:
            raise CutoverPreconditionFailed(f"staging snapshot mismatch: {key}")
    return payload


def activation_dry_run(conn, *, cutover_id: str, price_source: str,
                       expected_old: Optional[str], main_db_path: str | Path) -> dict:
    """Build the exact activation plan using read-only SQL only.

    No evidence is created, no transaction is opened, and no table is mutated.
    """
    _assert_staging_db(main_db_path)
    rec = _record(conn, cutover_id)
    current_row = conn.execute(
        "SELECT cutover_id FROM qfq_active_cutover WHERE price_source=?",
        [price_source]).fetchone()
    current = current_row[0] if current_row else None
    trigger_rows = conn.execute(
        "SELECT status, COUNT(*) FROM qfq_trigger_queue WHERE price_source=? "
        "AND source_generation=? AND status IN "
        "('scheduled','pending','in_progress','retryable_failed','blocked') "
        "GROUP BY status ORDER BY status", [LEGACY_SOURCE, LEGACY_GENERATION]).fetchall()
    trigger_sample = conn.execute(
        "SELECT trigger_id,status FROM qfq_trigger_queue WHERE price_source=? "
        "AND source_generation=? AND status IN "
        "('scheduled','pending','in_progress','retryable_failed','blocked') "
        "ORDER BY status,trigger_id LIMIT 20", [LEGACY_SOURCE, LEGACY_GENERATION]).fetchall()
    intent_rows = conn.execute(
        "SELECT status,COUNT(*) FROM qfq_watermark_intent WHERE source=? "
        "AND source_generation=? AND status='pending' GROUP BY status",
        [LEGACY_SOURCE, LEGACY_GENERATION]).fetchall()
    cycle_rows = conn.execute(
        "SELECT status,COUNT(*) FROM qfq_cycle_run WHERE price_source=? "
        "AND source_generation=? AND status='started' GROUP BY status",
        [LEGACY_SOURCE, LEGACY_GENERATION]).fetchall()
    return {
        "dry_run": True, "read_only": True, "transaction_started": False,
        "evidence_created": False, "cutover_id": cutover_id,
        "new_cutover_status": rec.get("status"), "price_source": price_source,
        "expected_old": expected_old, "current_active": current,
        "expected_old_cas_matches": current == expected_old,
        "legacy_trigger_retirement": {
            "by_status": trigger_rows,
            "total": sum(int(row[1]) for row in trigger_rows),
            "sample": trigger_sample,
        },
        "watermark_intent_supersede": {
            "by_status": intent_rows,
            "total": sum(int(row[1]) for row in intent_rows),
            "advances_source_watermark": False,
        },
        "stale_cycle_interrupt": {
            "by_status": cycle_rows,
            "total": sum(int(row[1]) for row in cycle_rows),
        },
        "sequence": [
            "verify immutable evidence and baseline_validated status",
            "expected-old active-pointer CAS",
            "interrupt stale legacy started cycles",
            "supersede legacy pending watermark intents without advancing source_watermark",
            "retire legacy non-terminal triggers; preserve committed/dead_letter",
            "DELETE expected-old active pointer with RETURNING when present",
            "UPDATE new cutover baseline_validated -> active",
            "INSERT new active pointer",
            "postconditions and COMMIT",
        ],
    }


def _fault(fault_at: Optional[str], point: str) -> None:
    if fault_at == point:
        raise RuntimeError(f"B-6 fault injection: {point}")


def activate_cutover_staging(conn, *, cutover_id: str, price_source: str,
                              expected_old: Optional[str], main_db_path: str | Path,
                              fault_at: Optional[str] = None,
                              verified_evidence: Optional[dict] = None) -> dict:
    """Atomically activate a verified staging cutover and retire legacy work.

    ``verified_evidence`` is an internal rehearsal optimization: callers may
    pass the exact payload returned by one successful verification when running
    multiple rollback fault points against an otherwise unchanged staging copy.
    The normal CLI path leaves it unset and verifies afresh.
    """
    evidence = verified_evidence or verify_cutover_evidence(
        conn, cutover_id=cutover_id, main_db_path=main_db_path)
    rec = _record(conn, cutover_id)
    current = conn.execute("SELECT cutover_id FROM qfq_active_cutover WHERE price_source=?", [price_source]).fetchone()
    current_id = current[0] if current else None
    if current_id != expected_old:
        raise CutoverCASFailed(f"active={current_id!r} != expected_old={expected_old!r}")
    # An existing lease means the owner may still be alive; never infer staleness from timestamps alone.
    lease = conn.execute("SELECT COUNT(*) FROM qfq_cycle_lease WHERE price_source=? AND source_generation=?",
                         [LEGACY_SOURCE, LEGACY_GENERATION]).fetchone()[0]
    if lease:
        raise CutoverPreconditionFailed("legacy cycle lease exists; refuse to kill a possible owner")
    pre_wm = table_evidence(conn, "source_watermark")
    committed_before = conn.execute("SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='committed'").fetchone()[0]
    conn.execute("BEGIN TRANSACTION")
    try:
        row = conn.execute("SELECT status FROM qfq_source_cutover WHERE cutover_id=?", [cutover_id]).fetchone()
        if not row or row[0] != "baseline_validated":
            raise CutoverPreconditionFailed("cutover state changed inside activation transaction")
        # Stale cycles are interrupted first; their pending intents are then superseded.
        interrupted = conn.execute(
            "UPDATE qfq_cycle_run SET status='interrupted', phase='interrupted', "
            "error=?, finished_at=?, updated_at=? WHERE price_source=? AND source_generation=? "
            "AND status='started' RETURNING cycle_id", [
                "legacy xtquant cycle retired during MCP-only cutover", _now_ts(), _now_ts(),
                LEGACY_SOURCE, LEGACY_GENERATION]).fetchall()
        intents = conn.execute(
            "UPDATE qfq_watermark_intent SET status='superseded', hold_reason=?, "
            "committed_at=NULL WHERE source=? AND source_generation=? AND status='pending' "
            "RETURNING cycle_id", ["legacy xtquant watermark intent superseded during MCP-only cutover",
                                    LEGACY_SOURCE, LEGACY_GENERATION]).fetchall()
        retired = conn.execute(
            "UPDATE qfq_trigger_queue SET status='superseded', retired_at=?, retire_reason=?, "
            "claimed_by=NULL, claimed_at=NULL, updated_at=? WHERE price_source=? "
            "AND source_generation=? AND status IN ('scheduled','pending','in_progress','retryable_failed','blocked') "
            "RETURNING trigger_id", [_now_ts(), "legacy xtquant trigger retired during MCP-only cutover", _now_ts(),
                                      LEGACY_SOURCE, LEGACY_GENERATION]).fetchall()
        _fault(fault_at, "after_retirement")
        if current_id is not None:
            deleted = conn.execute(
                "DELETE FROM qfq_active_cutover WHERE price_source=? AND cutover_id=? RETURNING cutover_id",
                [price_source, expected_old]).fetchone()
            if deleted is None:
                raise CutoverCASFailed("expected old active-pointer DELETE CAS failed")
            conn.execute("UPDATE qfq_source_cutover SET status='superseded', updated_at=? WHERE cutover_id=? AND status='active'",
                         [_now_ts(), expected_old])
        _fault(fault_at, "after_pointer_delete")
        # DuckDB enforces the FK while updating a referenced cutover row; mark
        # the new row active before inserting the pointer, still inside the same
        # transaction so no partial state is externally observable.
        updated = conn.execute("UPDATE qfq_source_cutover SET status='active', updated_at=? "
                               "WHERE cutover_id=? AND status='baseline_validated' RETURNING cutover_id",
                               [_now_ts(), cutover_id]).fetchone()
        if updated is None:
            raise CutoverCASFailed("new cutover status CAS failed")
        _fault(fault_at, "after_new_status")
        conn.execute("INSERT INTO qfq_active_cutover VALUES (?,?,?)", [price_source, cutover_id, _now_ts()])
        _fault(fault_at, "after_pointer_insert")
        if conn.execute("SELECT COUNT(*) FROM qfq_active_cutover WHERE price_source=?", [price_source]).fetchone()[0] != 1:
            raise CutoverPreconditionFailed("active pointer uniqueness postcondition failed")
        if conn.execute("SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='committed'").fetchone()[0] != committed_before:
            raise CutoverPreconditionFailed("committed trigger count changed")
        if table_evidence(conn, "source_watermark") != pre_wm:
            raise CutoverPreconditionFailed("source_watermark changed during activation")
        _fault(fault_at, "before_commit")
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    _fault(fault_at, "after_commit_before_report")
    return {"cutover_id": cutover_id, "price_source": price_source,
            "status": "active", "expected_old": expected_old,
            "interrupted_cycles": len(interrupted), "superseded_intents": len(intents),
            "retired_triggers": len(retired), "dead_letter_preserved": True,
            "evidence_manifest_sha256": evidence["manifest_sha256"]}
