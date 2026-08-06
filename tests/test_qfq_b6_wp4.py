from pathlib import Path
import json
import sqlite3

import duckdb
import pytest

from quantstudio.pipeline import qfq_orchestrator_cli as cli
from quantstudio.pipeline.qfq_cutover import create_cutover, transition_cutover
from quantstudio.pipeline.qfq_staging_canary import recover_aborted_canary, run_full_noop_with_timeout, run_scoped_canary
from quantstudio.pipeline.qfq_staging_prep import StagingPrepError, prepare_staging_copy
from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema, init_sqlite_schema


def _db_fixture(tmp_path, *, active=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "staging.duckdb"
    aux = tmp_path / "aux.db"
    c = duckdb.connect(str(db))
    init_duckdb_schema(c)
    for table in ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes"):
        c.execute(f'CREATE TABLE "{table}" (code VARCHAR, time BIGINT)')
        c.execute(f'INSERT INTO "{table}" VALUES (\'510500\', 1)')
    c.execute("CREATE TABLE stock_dividend (code VARCHAR, ex_date BIGINT, div_proc VARCHAR)")
    c.execute("INSERT INTO stock_dividend VALUES ('510500', 1, '??')")
    c.execute(
        "INSERT INTO qfq_discovery_baseline "
        "(cutover_id,price_source,source_generation,event_logical_key,applied_payload_hash,baselined_at,updated_at) "
        "VALUES ('cut1','mcp','mcp-gen1','k1','h1',NOW(),NOW())")
    create_cutover(c, cutover_id="cut1", price_source="mcp", source_generation="mcp-gen1",
                   schema_version="reanchor-2.1", baseline_version="qfq-detector-2.1",
                   aux_db_path=str(aux))
    transition_cutover(c, cutover_id="cut1", expected_status="planned", new_status="prepared")
    transition_cutover(c, cutover_id="cut1", expected_status="prepared", new_status="baseline_building")
    transition_cutover(c, cutover_id="cut1", expected_status="baseline_building", new_status="baseline_validated")
    if active:
        c.execute("UPDATE qfq_source_cutover SET status='active' WHERE cutover_id='cut1'")
        c.execute("INSERT INTO qfq_active_cutover VALUES ('mcp','cut1',NOW())")
    c.close()
    a = sqlite3.connect(str(aux)); init_sqlite_schema(a); a.commit(); a.close()
    return db, aux


def test_cutover_activate_dry_run_is_read_only_and_does_not_create_evidence(tmp_path, capsys):
    db, aux = _db_fixture(tmp_path)
    c = duckdb.connect(str(db), read_only=True)
    before = {
        "cutover": c.execute("SELECT status FROM qfq_source_cutover WHERE cutover_id='cut1'").fetchone(),
        "active": c.execute("SELECT COUNT(*) FROM qfq_active_cutover").fetchone(),
        "baseline": c.execute("SELECT COUNT(*) FROM qfq_discovery_baseline").fetchone(),
    }
    c.close()
    assert cli.main(["--db", str(db), "--json", "cutover-activate",
                     "--cutover-id", "cut1", "--expected-old", "", "--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["transaction_started"] is False
    assert payload["evidence_created"] is False
    assert payload["expected_old_cas_matches"] is True
    assert not list(tmp_path.glob("**/*evidence*.json"))
    c = duckdb.connect(str(db), read_only=True)
    after = {
        "cutover": c.execute("SELECT status FROM qfq_source_cutover WHERE cutover_id='cut1'").fetchone(),
        "active": c.execute("SELECT COUNT(*) FROM qfq_active_cutover").fetchone(),
        "baseline": c.execute("SELECT COUNT(*) FROM qfq_discovery_baseline").fetchone(),
    }
    c.close()
    assert after == before


def test_prepare_staging_copy_holds_locks_and_rejects_nonempty_sidecar(tmp_path, monkeypatch):
    source_db, source_aux = _db_fixture(tmp_path / "source")
    daemon_lock = tmp_path / "daemon.lock"
    collector_lock = tmp_path / "collector.lock"
    monkeypatch.setattr("quantstudio.pipeline.qfq_staging_prep.daemon_lock_path", lambda: daemon_lock)
    monkeypatch.setattr("quantstudio.pipeline.qfq_staging_prep.collector_run_lock_path", lambda: collector_lock)
    result = prepare_staging_copy(source_db=source_db, source_aux=source_aux,
                                  dest=tmp_path / "prepared")
    assert Path(result["main_db"]).is_file()
    assert Path(result["aux_db"]).is_file()
    assert json.loads(Path(result["marker"]).read_text(encoding="utf-8"))["formal_write"] is False
    Path(str(source_db) + ".wal").write_bytes(b"unsafe")
    with pytest.raises(StagingPrepError, match="sidecar"):
        prepare_staging_copy(source_db=source_db, source_aux=source_aux,
                             dest=tmp_path / "rejected")


def test_scoped_canary_preserves_baseline_and_forces_global_hold(tmp_path):
    db, aux = _db_fixture(tmp_path, active=True)
    out = tmp_path / "canary.json"
    payload = run_scoped_canary(main_db=db, aux_db=aux,
                                codes=["510500"], cutover_id="cut1",
                                expected_baseline_rows=1, output_path=out)
    assert payload["runtime_identity"] == {
        "price_source": "mcp", "source_generation": "mcp-gen1", "cutover_id": "cut1"}
    assert payload["assertions"]["baseline_preserved"] is True
    assert payload["assertions"]["global_watermark_forced_hold"] is True
    assert payload["summary"]["status"] == "finalized_held"
    assert out.is_file()



def test_canary_abort_recovery_interrrupts_started_cycle_and_checkpoint(tmp_path):
    db, aux = _db_fixture(tmp_path, active=True)
    c = duckdb.connect(str(db))
    c.execute("INSERT INTO qfq_cycle_run "
              "(cycle_id,phase,status,started_at,price_source,source_generation,cutover_id,updated_at) "
              "VALUES ('cyc_abort','observing','started',NOW(),'mcp','mcp-gen1','cut1',NOW())")
    c.close()
    result = recover_aborted_canary(main_db=db, aux_db=aux)
    assert result["started_before"] == [("cyc_abort", "started", "observing")]
    assert result["sqlite_integrity_before"] == "ok"
    assert result["sqlite_integrity_after"] == "ok"
    assert result["sidecars_after"] == []


def test_canary_full_noop_can_be_disabled_for_hermetic_runs(tmp_path):
    db, aux = _db_fixture(tmp_path, active=True)
    result = run_full_noop_with_timeout(main_db=db, aux_db=aux,
                                        cutover_id="cut1", timeout_sec=0)
    assert result == {"skipped": True, "timeout_sec": 0}


def test_wp4_cli_parser_exposes_prep_and_canary_options():
    args = cli.build_parser().parse_args([
        "--db", "staging.db", "cutover-canary", "--aux-db", "aux.db",
        "--output", "out.json", "--full-noop-timeout-sec", "7"])
    assert args.cmd == "cutover-canary"
    assert args.codes == "510500,159919,000001"
    assert args.full_noop_timeout_sec == 7
