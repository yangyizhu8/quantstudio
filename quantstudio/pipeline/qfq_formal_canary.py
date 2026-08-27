"""B-6 WP7-E2 formal held-canary runner.

Runs strictly before watermark release, only after WP6 handoff + exit evidence
are both present and verified.  Consumes the ``wp7_held_canary`` grant with an
independent nonce (never the WP6 nonce).  Enforces global watermark hold and
rejects any unexpected trigger/intent or source-watermark change as a P0.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import duckdb

from .qfq_aux_router import AuxDbRouter
from .qfq_cutover_activation import LEGACY_GENERATION, LEGACY_SOURCE
from .qfq_formal_authorization import (
    AuthorizationError, assert_no_watermark_release_grant, hash_manifest_bytes,
    load_and_verify_manifest, manifest_carry_grant, manifest_grant_nonce,
    reserve_nonce, resolve_canonical,
)
from .qfq_formal_cutover import (
    FormalCutoverError, _configured_formal_aux, _configured_formal_main,
    _write_handoff,
)
from .qfq_snapshot_evidence import table_evidence

BJ_TZ = timezone(timedelta(hours=8))
PRICE_TABLES = ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes")
DEFAULT_CANARY_CODES = ("510500", "159919", "000001")


class FormalCanaryError(FormalCutoverError):
    pass


class FormalCanaryP0(FormalCutoverError):
    """Raised when an unexpected trigger/intent or watermark change is observed."""


def _now_ts() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _price_summaries(conn) -> dict:
    return {table: conn.execute(
        f'SELECT COUNT(*),MIN(time),MAX(time) FROM "{table}"').fetchone()
        for table in PRICE_TABLES}


def run_held_canary(*, authorization_path: str, authorization_sha256: str,
                    handoff_dir: str | Path, config_dir: str | Path,
                    codes: Optional[Sequence[str]] = None,
                    output_path: Optional[str | Path] = None,
                    dry_run: bool = False,
                    main_db_override: Optional[str | Path] = None,
                    staging_db_override: Optional[str | Path] = None) -> dict:
    """Run the formal held-canary with global watermark hold.

    The canary verifies the WP6 handoff + exit evidence, consumes the
    ``wp7_held_canary`` grant with an independent nonce, runs a bounded
    dynamic-generation cycle with ``watermark_policy=hold_until_consistent``,
    and asserts baseline/no-new-trigger/no-intent/watermark-unchanged.

    Unexpected triggers/intents or a source-watermark change raise
    ``FormalCanaryP0``.

    Three DB-resolution modes (mutually exclusive):
      # 3A 写锁（qfq_formal_canary:209）：全程持锁（引用计数，嵌套安全）
      from quantstudio.pipeline.snapshot_lock import (ensure_write_lock,
                                                      release_write_lock)
      ensure_write_lock('formal_canary')
      * default (both None) → ``_configured_formal_main()`` (formal production DB);
      * ``main_db_override`` → dry-run ONLY (read-only snapshot); real execution
        with this param raises;
      * ``staging_db_override`` → staging real-orchestrator validation ONLY
        (``dry_run=False``); the path must contain ``staging`` or ``output``
        markers and must NOT equal the formal DB path.  All writes land on the
        staging copy (independent file, never flows back to production).
    """
    manifest = load_and_verify_manifest(authorization_path, authorization_sha256)
    # Defense-in-depth: a WP7-E2 manifest must NEVER carry a watermark-release
    # grant (G0 §3.1 item 20).
    assert_no_watermark_release_grant(manifest)
    if not manifest_carry_grant(manifest, "wp7_held_canary"):
        raise FormalCanaryError("manifest does not grant wp7_held_canary")
    hdir = resolve_canonical(handoff_dir)
    handoff_path = hdir / "formal_cutover_handoff.json"
    exit_path = hdir / "formal_runner_exit_evidence.json"
    if not handoff_path.is_file() or not exit_path.is_file():
        raise FormalCanaryError("WP6 handoff or exit evidence missing")
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    exit_ev = json.loads(exit_path.read_text(encoding="utf-8"))
    handoff_raw_sha = hash_manifest_bytes(handoff_path.read_bytes())
    if handoff_raw_sha != exit_ev.get("handoff_raw_sha256"):
        raise FormalCanaryError("handoff raw SHA mismatch with exit evidence")
    if handoff.get("watermark_release_authorized") is not False:
        raise FormalCanaryError("handoff must pin watermark_release_authorized=false")
    if not exit_ev.get("locks_released_verified"):
        raise FormalCanaryError("exit evidence reports locks NOT released")
    canary_codes = tuple(codes) if codes else DEFAULT_CANARY_CODES
    if not canary_codes:
        raise FormalCanaryError("canary codes cannot be empty")
    # main_db_override is ONLY for dry_run (staging copy).  Real execution must
    # never redirect via this param — it always runs against the configured
    # formal path under real authorization.
    if main_db_override is not None and not dry_run:
        raise FormalCanaryError(
            "main_db_override is only allowed with dry_run=True; real held-canary "
            "execution must run against the configured formal DB path")
    # staging_db_override is ONLY for staging real-orchestrator validation
    # (dry_run=False).  It must NOT be used with dry_run=True (use
    # main_db_override for dry-runs).  The path must contain staging/output
    # markers and must NOT equal the formal DB path.
    if staging_db_override is not None and dry_run:
        raise FormalCanaryError(
            "staging_db_override is only for real execution (dry_run=False); "
            "use main_db_override for dry-runs")
    if main_db_override is not None and staging_db_override is not None:
        raise FormalCanaryError(
            "main_db_override and staging_db_override are mutually exclusive")
    if staging_db_override is not None:
        main_db = resolve_canonical(staging_db_override)
        # Hard path-safety: the staging DB must NOT be the formal production DB.
        formal_main = _configured_formal_main()
        if str(main_db).lower() == str(formal_main).lower():
            raise FormalCanaryError(
                f"staging_db_override resolves to the formal production DB path: {main_db}; "
                "staging validation must point at a copy, never at production")
        try:
            if main_db.exists() and formal_main.exists() and os.path.samefile(str(main_db), str(formal_main)):
                raise FormalCanaryError(
                    f"staging_db_override is the same file as the formal production DB: {main_db}")
        except OSError:
            pass
        path_str = str(main_db).lower()
        if "staging" not in path_str and "output" not in path_str:
            raise FormalCanaryError(
                f"staging_db_override path must contain 'staging' or 'output' marker: {main_db}")
        print(f"*** STAGING VALIDATION: real orchestrator against override DB, NOT formal production ***",
              file=__import__("sys").stderr)
        print(f"*** staging_db_resolved: {main_db} ***", file=__import__("sys").stderr)
    elif main_db_override is not None:
        main_db = resolve_canonical(main_db_override)
    else:
        main_db = _configured_formal_main()
    aux_db = _configured_formal_aux()
    router = AuxDbRouter.from_config_dir(resolve_canonical(config_dir), main_db=main_db)
    gen1_aux = router.path_for("mcp-gen1", require_exists=True)

    # ---- dry_run: gates-only, read-only snapshot, no orchestrator, NO nonce burn ---
    # dry_run must have ZERO irreversible side effects — including nonce burn.
    # The nonce is consumed ONLY in the real execution path below, so a dry-run
    # can be repeated without exhausting the manifest's single-use nonce.
    if dry_run:
        ro = duckdb.connect(str(main_db), read_only=True)
        try:
            before = {
                "baseline": ro.execute(
                    "SELECT COUNT(*) FROM qfq_discovery_baseline WHERE cutover_id=?",
                    [manifest["cutover_id"]]).fetchone()[0],
                "mcp_triggers": ro.execute(
                    "SELECT COUNT(*) FROM qfq_trigger_queue WHERE source_generation='mcp-gen1'").fetchone()[0],
                "mcp_intents": ro.execute(
                    "SELECT COUNT(*) FROM qfq_watermark_intent WHERE source_generation='mcp-gen1'").fetchone()[0],
                "watermark": ro.execute(
                    "SELECT COUNT(*),COALESCE(SUM(CASE WHEN last_date IS NULL THEN 0 ELSE last_date END),0) "
                    "FROM source_watermark").fetchone(),
                "prices": _price_summaries(ro),
            }
        finally:
            ro.close()
        return {
            "kind": "quantstudio-b6-wp7-held-canary-dry-run",
            "dry_run": True,
            "cutover_id": manifest["cutover_id"],
            "main_db_resolved": str(main_db),
            "canary_codes": list(canary_codes),
            "gates_passed": {
                "manifest_loaded": True,
                "wp7_held_canary_grant": True,
                "no_watermark_release_grant": True,
                "handoff_exists": True,
                "exit_evidence_exists": True,
                "handoff_raw_sha_match": handoff_raw_sha == exit_ev.get("handoff_raw_sha256"),
                "watermark_release_authorized_false": handoff.get("watermark_release_authorized") is False,
                "locks_released_verified": exit_ev.get("locks_released_verified") is True,
                "nonce_not_burned": True,
            },
            "before_snapshot": before,
            "note": "dry_run: gates + read-only snapshot only; no orchestrator constructed, "
                    "no run_post_ingest called, no nonce burned",
            "ran_at": _now_ts(),
        }

    # ---- real path: nonce burn + orchestrator cycle --------------------------
    # The nonce is consumed HERE (real execution only), not in dry_run, so a
    # dry-run can be repeated without exhausting the manifest's single-use nonce.
    import os as _os
    from pathlib import Path as _Path
    nonce = manifest_grant_nonce(manifest, "wp7_held_canary")
    auth_root_for_ledger = _Path(authorization_path).resolve().parent
    marker_sha = reserve_nonce(
        str(auth_root_for_ledger), "wp7_held_canary", nonce,
        manifest_raw_sha=authorization_sha256, cutover_id=manifest["cutover_id"],
        commit_sha=manifest["git_commit_sha"], pid=_os.getpid(),
        create_time=datetime.now(BJ_TZ).timestamp())
    from quantstudio.pipeline.snapshot_lock import locked_connect
    _lctx = locked_connect(lambda: duckdb.connect(str(main_db), read_only=False),
                           "formal_canary")  # 3A 写锁（qfq_formal_canary:209）
    conn = _lctx.__enter__()
    try:
        before = {
            "baseline": conn.execute(
                "SELECT COUNT(*) FROM qfq_discovery_baseline WHERE cutover_id=?",
                [manifest["cutover_id"]]).fetchone()[0],
            "mcp_triggers": conn.execute(
                "SELECT COUNT(*) FROM qfq_trigger_queue WHERE source_generation='mcp-gen1'").fetchone()[0],
            "mcp_intents": conn.execute(
                "SELECT COUNT(*) FROM qfq_watermark_intent WHERE source_generation='mcp-gen1'").fetchone()[0],
            "watermark": conn.execute(
                "SELECT COUNT(*),COALESCE(SUM(CASE WHEN last_date IS NULL THEN 0 ELSE last_date END),0) "
                "FROM source_watermark").fetchone(),
            "prices": _price_summaries(conn),
        }
        # Build a held-watermark config and run one bounded cycle via the
        # resident orchestrator (same primitive the staging scoped canary uses).
        from .qfq_orchestrator_types import QFQOrchestratorConfig
        from .qfq_resident_orchestrator import QFQResidentOrchestrator
        from .qfq_calendar import CalendarService
        raw = {
            "enabled": True, "require_bootstrap": False,
            "factor_refresh_enabled": False, "price_source": "mcp",
            "generation_mode": "dynamic", "source_generation": "mcp-gen1",
            "cutover_id": manifest["cutover_id"],
            "stock_factor_detector": "mcp_factor_detector",
            "etf_factor_detector": "mcp_factor_detector", "freqs": ["1min"],
            "watermark_policy": "hold_until_consistent",
        }
        cfg = QFQOrchestratorConfig.from_dict(raw)
        orch = QFQResidentOrchestrator(
            cfg, main_db=str(main_db), aux_db=str(gen1_aux), fetcher=object(),
            calendar=CalendarService(main_db=main_db))
        identity = orch.prepare_runtime(conn, require_aux=True)
        cycle_id = orch.begin_cycle(conn)
        summary = orch.run_post_ingest(
            conn, cycle_id=cycle_id,
            run_id=f"b6_formal_canary_{int(time.time())}",
            as_of_ms=int(time.time() * 1000), codes_filter=canary_codes)
        after = {
            "baseline": conn.execute(
                "SELECT COUNT(*) FROM qfq_discovery_baseline WHERE cutover_id=?",
                [manifest["cutover_id"]]).fetchone()[0],
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
        _lctx.__exit__(None, None, None)  # 3A 写锁随连接释放
    assertions = {
        "dynamic_identity_active": identity.get("price_source") == "mcp"
            and identity.get("source_generation") == "mcp-gen1"
            and identity.get("cutover_id") == manifest["cutover_id"],
        "baseline_preserved": before["baseline"] == after["baseline"],
        "no_new_mcp_trigger": before["mcp_triggers"] == after["mcp_triggers"] == 0,
        "no_mcp_intent": before["mcp_intents"] == after["mcp_intents"] == 0,
        "source_watermark_unchanged": before["watermark"] == after["watermark"],
        "prices_unchanged": before["prices"] == after["prices"],
        "global_watermark_forced_hold": getattr(summary, "status", None) == "finalized_held",
    }
    # P0 conditions
    if after["mcp_triggers"] != before["mcp_triggers"] or after["mcp_intents"] != before["mcp_intents"]:
        raise FormalCanaryP0(
            f"unexpected mcp triggers/intents during held-canary: {assertions}")
    if before["watermark"] != after["watermark"]:
        raise FormalCanaryP0(f"source watermark changed during held-canary: {assertions}")
    report = {
        "kind": "quantstudio-b6-wp7-held-canary",
        "cutover_id": manifest["cutover_id"],
        "main_db_resolved": str(main_db),
        "runtime_identity": identity,
        "canary_codes": list(canary_codes),
        "before": before, "after": after,
        "assertions": assertions,
        "nonce_ledger_marker_sha256": marker_sha,
        "summary_status": getattr(summary, "status", None),
        "watermark_release_authorized": False,
        "ran_at": _now_ts(),
    }
    report["pass"] = all(assertions.values())
    if output_path is not None:
        from .qfq_formal_cutover import _write_handoff as _wh
        # O_EXCL evidence write (binary-safe on Windows)
        from .qfq_formal_authorization import _bin_write_flags
        op = resolve_canonical(output_path)
        op.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        flags = _bin_write_flags()
        fd = os.open(str(op), flags, 0o644)
        try:
            os.write(fd, encoded.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    return report
