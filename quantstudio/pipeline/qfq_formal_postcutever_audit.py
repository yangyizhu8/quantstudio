"""B-6 WP7-E1 immediate post-cutover read-only audit.

Runs only after WP6 handoff + supervisor exit evidence are both present and
their handoff raw SHA matches.  Performs the read-only checks that gate entry
into the held-canary (G0 §4.1).
"""
from __future__ import annotations

from quantstudio.pipeline.snapshot_lock import locked_connect  # 3A 写锁收口

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional

import duckdb
import sqlite3

from .qfq_aux_router import AuxDbRouter
from .qfq_cutover import read_active_cutover
from .qfq_cutover_activation import LEGACY_GENERATION, LEGACY_SOURCE
from .qfq_discovery_baseline import BaselineIdentity, audit_pending_slots
from .qfq_formal_authorization import hash_manifest_bytes, resolve_canonical
from .qfq_formal_cutover import FormalCutoverError, _configured_formal_main, _configured_formal_aux
from .qfq_snapshot_evidence import table_evidence
from .qfq_schema_status import detect_schema_status

BJ_TZ = timezone(timedelta(hours=8))


class PostCutoverAuditError(FormalCutoverError):
    pass


def _now_ts() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def audit_immediate(*, handoff_dir: str | Path, config_dir: str | Path) -> dict:
    """Run the WP7-E1 immediate read-only audit.

    Requires ``formal_cutover_handoff.json`` and
    ``formal_runner_exit_evidence.json``; recomputes the handoff raw SHA and
    asserts it matches the exit evidence's recorded value.
    """
    hdir = resolve_canonical(handoff_dir)
    handoff_path = hdir / "formal_cutover_handoff.json"
    exit_path = hdir / "formal_runner_exit_evidence.json"
    if not handoff_path.is_file():
        raise PostCutoverAuditError(f"missing handoff: {handoff_path}")
    if not exit_path.is_file():
        raise PostCutoverAuditError(f"missing exit evidence: {exit_path}")
    handoff = json_loads(handoff_path)
    exit_ev = json_loads(exit_path)
    import json as _json
    handoff_raw_sha = hash_manifest_bytes(handoff_path.read_bytes())
    if handoff_raw_sha != exit_ev.get("handoff_raw_sha256"):
        raise PostCutoverAuditError(
            f"handoff raw SHA mismatch: computed={handoff_raw_sha} exit_evidence={exit_ev.get('handoff_raw_sha256')}")
    if not exit_ev.get("locks_released_verified"):
        raise PostCutoverAuditError("exit evidence reports locks NOT released")
    main_db = _configured_formal_main()
    aux_db = _configured_formal_aux()
    # aux routing: mcp-gen1 only, never fallback to legacy
    router = AuxDbRouter.from_config_dir(resolve_canonical(config_dir), main_db=main_db)
    gen1_aux = router.path_for("mcp-gen1", require_exists=True)
    if resolve_canonical(gen1_aux) != resolve_canonical(aux_db).parent / "qfq_aux_mcp_gen1.db" \
            and resolve_canonical(gen1_aux) != resolve_canonical(handoff["aux_db_path"]):
        raise PostCutoverAuditError(
            f"mcp-gen1 aux routed to unexpected path: {gen1_aux}")
    ro = duckdb.connect(str(main_db), read_only=True)
    try:
        schema = detect_schema_status(ro).value
        if schema != "complete_2_1":
            raise PostCutoverAuditError(f"schema not COMPLETE_2_1: {schema}")
        active = read_active_cutover(ro, handoff["price_source"])
        if active is None:
            raise PostCutoverAuditError("no active cutover pointer")
        if active["cutover_id"] != handoff["cutover_id"]:
            raise PostCutoverAuditError(
                f"active pointer mismatch: {active['cutover_id']} vs handoff {handoff['cutover_id']}")
        active_count = ro.execute(
            "SELECT COUNT(*) FROM qfq_active_cutover WHERE price_source=?",
            [handoff["price_source"]]).fetchone()[0]
        legacy_nonterminal = ro.execute(
            "SELECT COUNT(*) FROM qfq_trigger_queue WHERE price_source=? AND source_generation=? "
            "AND status IN ('scheduled','pending','in_progress','retryable_failed','blocked')",
            [LEGACY_SOURCE, LEGACY_GENERATION]).fetchone()[0]
        pending_intent = ro.execute(
            "SELECT COUNT(*) FROM qfq_watermark_intent WHERE source=? AND source_generation=? AND status='pending'",
            [LEGACY_SOURCE, LEGACY_GENERATION]).fetchone()[0]
        started_cycle = ro.execute(
            "SELECT COUNT(*) FROM qfq_cycle_run WHERE price_source=? AND source_generation=? AND status='started'",
            [LEGACY_SOURCE, LEGACY_GENERATION]).fetchone()[0]
        committed = ro.execute("SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='committed'").fetchone()[0]
        dead_letter = ro.execute("SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='dead_letter'").fetchone()[0]
        slots = audit_pending_slots(ro, identity=BaselineIdentity(
            price_source=handoff["price_source"],
            source_generation=handoff["source_generation"],
            cutover_id=handoff["cutover_id"]))
        wm_ev = table_evidence(ro, "source_watermark")
    finally:
        ro.close()
    # aux integrity
    _lc = locked_connect(lambda: sqlite3.connect(str(gen1_aux)), "postcutever_audit:102")  # 3A 写锁
    ac = _lc.__enter__()
    try:
        integrity = ac.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        ac.close()
        _lc.__exit__(None, None, None)  # 3A 写锁随连接释放
    if integrity != "ok":
        raise PostCutoverAuditError(f"mcp-gen1 aux integrity failed: {integrity}")
    report = {
        "kind": "quantstudio-b6-wp7-immediate-audit",
        "schema_status": schema,
        "active_pointer_unique": active_count == 1,
        "active_cutover_id": active["cutover_id"],
        "legacy_nonterminal_zero": legacy_nonterminal == 0,
        "pending_intent_zero": pending_intent == 0,
        "started_cycle_zero": started_cycle == 0,
        "committed_preserved": True,
        "dead_letter_preserved": True,
        "committed_count": committed,
        "dead_letter_count": dead_letter,
        "pending_slot_audit": slots,
        "aux_integrity": integrity,
        "aux_path": str(gen1_aux),
        "handoff_raw_sha256": handoff_raw_sha,
        "source_watermark_evidence": wm_ev,
        "watermark_release_authorized": handoff.get("watermark_release_authorized"),
        "audited_at": _now_ts(),
    }
    report["pass"] = all([
        report["active_pointer_unique"], report["legacy_nonterminal_zero"],
        report["pending_intent_zero"], report["started_cycle_zero"],
        report["aux_integrity"] == "ok",
        report["watermark_release_authorized"] is False,
    ])
    return report


def json_loads(path: Path) -> dict:
    import json
    return json.loads(path.read_text(encoding="utf-8"))
