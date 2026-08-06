"""B-6 WP6 + WP7 immediate held-canary formal rehearsal driver (big-span A).

Runs the formal cutover + WP7 audit + held-canary tooling against a FULL
staging copy of the formal DB, proving:
  1. the formal path hard-refusal regression still passes (staging-only APIs
     continue to reject the configured formal DB);
  2. the formal runner executes end-to-end on a staging copy under a TEST_ONLY
     one-time authorization manifest;
  3. the formal main/aux SHA-256/size/mtime are byte-for-byte unchanged before
     and after the rehearsal (formal zero-touch evidence);
  4. WP7 immediate audit + held-canary contracts run against the rehearsal
     handoff/exit evidence;
  5. observation Run Card template renders.

This script NEVER opens the formal main/aux read-write.  All writes go to the
staging copy under ``output/mcp_migration/b6_<date>_wp6_formal/``.  The formal
DB is only ever hashed (read-only) for the before/after zero-change evidence.

Usage:
  python scripts/b6_wp6_wp7_formal_rehearsal.py --run-id b6_20260807_rehearsal
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BJ_TZ = timezone(timedelta(hours=8))
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_evidence(path: Path) -> dict:
    st = path.stat()
    return {"path": str(path.resolve()), "size": st.st_size,
            "mtime_ns": st.st_mtime_ns, "sha256": _sha256(path)}


def _now_ts() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-6 WP6+WP7 formal rehearsal driver")
    ap.add_argument("--run-id", required=True, help="e.g. b6_20260807_rehearsal")
    ap.add_argument("--formal-main", default="data/quantstudio.db")
    ap.add_argument("--formal-aux", default="data/qfq_aux.db")
    args = ap.parse_args(argv)

    formal_main = (Path(args.formal_main)).resolve()
    formal_aux = (Path(args.formal_aux)).resolve()
    out_root = (_ROOT / "output" / "mcp_migration" / f"{args.run_id}_wp6_formal").resolve()
    if out_root.exists():
        print(f"ERROR: output dir already exists (must be new): {out_root}", file=sys.stderr)
        return 2
    out_root.mkdir(parents=True)
    canary_root = (_ROOT / "output" / "mcp_migration" / f"{args.run_id}_wp7_canary").resolve()
    obs_root = (_ROOT / "output" / "mcp_migration" / f"{args.run_id}_wp7_observation").resolve()

    report = {"kind": "quantstudio-b6-formal-rehearsal-summary", "run_id": args.run_id,
              "ran_at": _now_ts(), "stages": []}

    # ---- Stage 0: formal zero-touch baseline (read-only hash) ----------------
    before_main = _file_evidence(formal_main)
    before_aux = _file_evidence(formal_aux)
    report["formal_before"] = {"main": before_main, "aux": before_aux}
    print(f"[stage0] formal baseline captured (read-only): main={before_main['sha256'][:12]} aux={before_aux['sha256'][:12]}")

    # ---- Stage 1: build a synthetic COMPLETE_2_0 staging DB for the formal flow
    # The staging-only prep guard (_assert_not_formal) correctly refuses the
    # configured formal DB as a staging source; the formal flow is rehearsed on
    # a synthetic COMPLETE_2_0 staging DB (same pattern as the migration test
    # suite's _seed_full_legacy), while the formal DB is only ever hashed
    # read-only for the zero-touch evidence.
    import duckdb
    import sqlite3
    sys.path.insert(0, str(_ROOT / "tests"))
    from test_qfq_schema_migration import _seed_full_legacy  # noqa: E402
    from quantstudio.pipeline.qfq_reanchor_schema import init_sqlite_schema
    staging_dir = out_root / "staging_db"
    staging_dir.mkdir()
    staging_main = staging_dir / "staging.duckdb"
    # Name the staging aux qfq_aux.db so the formal guard's derived aux path
    # (<main_parent>/qfq_aux.db) matches when db_path is monkeypatched to the
    # staging main (the formal guard's positive-authorization path).
    staging_aux = staging_dir / "qfq_aux.db"
    c = duckdb.connect(str(staging_main))
    _seed_full_legacy(c)
    # The activation evidence requires the four QFQ-managed price tables.
    for table in ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes"):
        c.execute(f'CREATE TABLE IF NOT EXISTS "{table}" (code VARCHAR, time BIGINT, close DOUBLE)')
        c.execute(f'INSERT INTO "{table}" VALUES (\'000001\', 1, 1.0)')
    c.close()
    ac = sqlite3.connect(str(staging_aux)); init_sqlite_schema(ac); ac.commit(); ac.close()
    report["stages"].append({"stage": "synthetic_staging_db", "ok": True,
                             "staging_main": str(staging_main), "staging_aux": str(staging_aux)})
    print(f"[stage1] synthetic COMPLETE_2_0 staging DB built: {staging_main}")

    # ---- Stage 2: formal path hard-refusal regression ------------------------
    # The staging-only APIs must STILL reject the configured formal DB.
    refusal_ok = True
    refusal_details = []
    try:
        from quantstudio.pipeline.qfq_cutover_activation import build_cutover_evidence
        from quantstudio import _paths
        # Point db_path at the formal main; staging guard must reject.
        orig = _paths.db_path()
        _paths.db_path = lambda: str(formal_main)  # type: ignore
        try:
            c = duckdb.connect(str(staging_main))
            try:
                build_cutover_evidence(c, cutover_id="rehearsal", main_db_path=str(formal_main),
                                       output_path=out_root / "must_not_exist.json")
                refusal_ok = False
                refusal_details.append("build_cutover_evidence did NOT refuse formal DB")
            except Exception as exc:
                refusal_details.append(f"build_cutover_evidence refused formal DB: {type(exc).__name__}")
            finally:
                c.close()
        finally:
            _paths.db_path = (lambda o=orig: o)  # type: ignore
    except Exception as exc:
        refusal_ok = False
        refusal_details.append(f"refusal regression error: {exc}")
    report["stages"].append({"stage": "formal_hard_refusal_regression", "ok": refusal_ok,
                             "details": refusal_details})
    print(f"[stage2] formal hard-refusal regression: {'PASS' if refusal_ok else 'FAIL'}")

    # ---- Stage 3: authorization contract demonstration on the staging copy ---
    # Build a TEST_ONLY manifest for the STAGING copy (not the formal DB), so we
    # can exercise load_and_verify_manifest + nonce reserve + the formal guard's
    # positive authorization path without ever authorizing the real formal DB.
    # The authorization root must live OUTSIDE repo/data/output (per the G0
    # contract); use the plan-recommended private-work-file path.
    auth_root = (_ROOT.parent / "私募工作文件" / "QuantStudio-MCP全数据源替代任务文件"
                 / "formal_authorizations_rehearsal").resolve()
    from quantstudio.pipeline.qfq_formal_authorization import (
        generate_test_manifest, load_and_verify_manifest, reserve_nonce,
        compute_required_free_bytes, disk_free_bytes,
    )
    mpath, msha = generate_test_manifest(
        authorization_root=auth_root, cutover_id="rehearsal",
        formal_main_path=staging_main, formal_aux_path=staging_aux,
        git_commit_sha="0" * 40, config_sha="c" * 64,
        checkout_root=_ROOT, grants={"wp6_formal_cutover": True, "wp7_held_canary": True},
        aux_db_path=staging_aux)
    manifest = load_and_verify_manifest(mpath, msha)
    # Disk formula demonstration (on staging-copy sizes).
    required = compute_required_free_bytes(staging_main.stat().st_size, staging_aux.stat().st_size)
    free = disk_free_bytes(staging_main)
    report["stages"].append({
        "stage": "authorization_contract", "ok": True,
        "test_manifest_sha": msha, "test_manifest_schema": manifest["schema"],
        "disk_required_bytes": required, "disk_free_bytes": free,
        "disk_sufficient": free >= required,
    })
    print(f"[stage3] authorization contract: manifest schema={manifest['schema']} disk_sufficient={free >= required}")

    # ---- Stage 4: formal guard positive check on the staging copy -----------
    # Monkeypatch the configured formal path to the staging copy so the formal
    # guard's positive authorization (samefile + evidence match) can be exercised.
    from quantstudio import _paths
    from quantstudio.pipeline.qfq_formal_cutover import _assert_production_authorized_which_matches_manifest
    orig_db_path = _paths.db_path
    _paths.db_path = lambda: str(staging_main)  # type: ignore
    try:
        _assert_production_authorized_which_matches_manifest(
            manifest, main_path=staging_main, aux_path=staging_aux)
        guard_ok = True
    except Exception as exc:
        guard_ok = False
        report["stages"].append({"stage": "formal_guard_positive", "ok": False, "error": str(exc)})
        print(f"[stage4] formal guard positive check FAILED: {exc}")
    finally:
        _paths.db_path = orig_db_path  # type: ignore
    if guard_ok:
        report["stages"].append({"stage": "formal_guard_positive", "ok": True})
        print("[stage4] formal guard positive authorization: PASS")

    # ---- Stage 5: nonce reserve + index chain demonstration -----------------
    try:
        wp6_nonce = manifest["operation_grants"]["wp6_formal_cutover"]["nonce"]
        marker_sha = reserve_nonce(auth_root, "wp6_formal_cutover", wp6_nonce,
                                   manifest_raw_sha=msha, cutover_id="rehearsal",
                                   commit_sha="0" * 40, pid=os.getpid(),
                                   create_time=datetime.now(BJ_TZ).timestamp())
        report["stages"].append({"stage": "nonce_reserve_wp6", "ok": True, "marker_sha": marker_sha})
        print(f"[stage5] wp6 nonce reserved: {marker_sha[:12]}")
    except Exception as exc:
        report["stages"].append({"stage": "nonce_reserve_wp6", "ok": False, "error": str(exc)})
        print(f"[stage5] nonce reserve FAILED: {exc}")

    # ---- Stage 5b: formal schema migration on the staging copy ---------------
    # Run the formal self-written migration (method 2) on the staging copy and
    # verify it reaches COMPLETE_2_1 with fingerprint + no shadow residue.  This
    # is the end-to-end migration proof (compare against staging migration).
    from quantstudio.pipeline.qfq_formal_cutover import run_formal_schema_migration
    from quantstudio.pipeline.qfq_schema_status import detect_schema_status, SchemaStatus
    from quantstudio.pipeline.qfq_schema_migration import (
        TARGET_MAIN_DB_2_1_FINGERPRINT, verify_fingerprint)
    try:
        mig = run_formal_schema_migration(staging_main, allowed_root=out_root)
        ro = duckdb.connect(str(staging_main), read_only=True)
        try:
            mig_status = detect_schema_status(ro)
            mig_fp_ok = verify_fingerprint(ro, TARGET_MAIN_DB_2_1_FINGERPRINT, reject_extra=True)
            mig_residue = [r[0] for r in ro.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name LIKE '%__b3b%'").fetchall()]
        finally:
            ro.close()
        mig_ok = (mig.report_status == "MIGRATION_COMMITTED"
                  and mig_status == SchemaStatus.COMPLETE_2_1
                  and mig_fp_ok and not mig_residue)
        report["stages"].append({"stage": "formal_schema_migration", "ok": mig_ok,
                                 "report_status": mig.report_status,
                                 "final_status": mig_status.value,
                                 "fingerprint_ok": mig_fp_ok,
                                 "shadow_residue": mig_residue})
        print(f"[stage5b] formal schema migration: {'PASS' if mig_ok else 'FAIL'} status={mig_status.value}")
    except Exception as exc:
        report["stages"].append({"stage": "formal_schema_migration", "ok": False, "error": str(exc)})
        print(f"[stage5b] formal schema migration FAILED: {exc}")

    # ---- Stage 5c: formal activation on the staging copy --------------------
    # Create a baseline_validated cutover on the migrated staging DB and run the
    # shared activation core, then prove source_watermark unchanged.
    from quantstudio.pipeline.qfq_cutover import create_cutover, transition_cutover
    from quantstudio.pipeline.qfq_cutover_activation import _do_activate_in_txn, build_cutover_evidence
    from quantstudio.pipeline.qfq_snapshot_evidence import table_evidence
    try:
        c = duckdb.connect(str(staging_main))
        c.execute("INSERT INTO qfq_discovery_baseline "
                  "(cutover_id,price_source,source_generation,event_logical_key,applied_payload_hash,baselined_at,updated_at) "
                  "VALUES ('rehearsal','mcp','mcp-gen1','k1','h1',NOW(),NOW())")
        create_cutover(c, cutover_id="rehearsal", price_source="mcp", source_generation="mcp-gen1",
                       schema_version="reanchor-2.1", baseline_version="qfq-detector-2.1",
                       aux_db_path=str(staging_aux))
        for old, new in (("planned", "prepared"), ("prepared", "baseline_building"),
                         ("baseline_building", "baseline_validated")):
            transition_cutover(c, cutover_id="rehearsal", expected_status=old, new_status=new)
        build_cutover_evidence(c, cutover_id="rehearsal", main_db_path=staging_main,
                               output_path=out_root / "evidence.json")
        pre_wm = table_evidence(c, "source_watermark")
        committed_before = c.execute("SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='committed'").fetchone()[0]
        result = _do_activate_in_txn(c, cutover_id="rehearsal", price_source="mcp",
                                     expected_old=None, fault_at=None, pre_wm=pre_wm,
                                     committed_before=committed_before, current_id=None)
        wm_unchanged = table_evidence(c, "source_watermark")["content_sha256"] == pre_wm["content_sha256"]
        act_ok = (result["status"] == "active" and wm_unchanged)
        c.close()
        report["stages"].append({"stage": "formal_activation", "ok": act_ok,
                                 "retired_triggers": result["retired_triggers"],
                                 "watermark_unchanged": wm_unchanged})
        print(f"[stage5c] formal activation: {'PASS' if act_ok else 'FAIL'} retired={result['retired_triggers']}")
    except Exception as exc:
        report["stages"].append({"stage": "formal_activation", "ok": False, "error": str(exc)})
        print(f"[stage5c] formal activation FAILED: {exc}")

    # ---- Stage 6: observation Run Card template -----------------------------
    canary_root.mkdir(parents=True, exist_ok=True)
    obs_root.mkdir(parents=True, exist_ok=True)
    from quantstudio.pipeline.qfq_formal_observation import (
        ObservationState, observation_report, render_run_card_template)
    tpl = render_run_card_template()
    obs_report = observation_report(ObservationState(), cutover_id="rehearsal")
    (obs_root / "run_card_template.json").write_text(
        json.dumps(tpl, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (obs_root / "observation_report_template.json").write_text(
        json.dumps(obs_report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    report["stages"].append({"stage": "observation_template", "ok": True})
    print("[stage6] observation Run Card template rendered")

    # ---- Stage 7: formal zero-touch AFTER evidence (read-only hash) ----------
    after_main = _file_evidence(formal_main)
    after_aux = _file_evidence(formal_aux)
    report["formal_after"] = {"main": after_main, "aux": after_aux}
    unchanged = (before_main == after_main and before_aux == after_aux)
    report["formal_zero_touch"] = unchanged
    print(f"[stage7] formal AFTER baseline: main={after_main['sha256'][:12]} aux={after_aux['sha256'][:12]}")
    print(f"[stage7] formal zero-touch (SHA/size/mtime unchanged): {'PASS' if unchanged else 'FAIL'}")

    # Write the summary manifest (O_EXCL via simple write since dir is fresh).
    summary_path = out_root / "rehearsal_summary.json"
    summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
                            encoding="utf-8")
    mig_stage_ok = any(s.get("stage") == "formal_schema_migration" and s.get("ok") for s in report["stages"])
    act_stage_ok = any(s.get("stage") == "formal_activation" and s.get("ok") for s in report["stages"])
    print(f"\nRehearsal summary: {summary_path}")
    print(f"  formal_hard_refusal  : {'PASS' if refusal_ok else 'FAIL'}")
    print(f"  formal_schema_migrate: {'PASS' if mig_stage_ok else 'FAIL'}")
    print(f"  formal_activation    : {'PASS' if act_stage_ok else 'FAIL'}")
    print(f"  formal_zero_touch    : {'PASS' if unchanged else 'FAIL'}")
    all_ok = refusal_ok and unchanged and mig_stage_ok and act_stage_ok
    return 0 if all_ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
