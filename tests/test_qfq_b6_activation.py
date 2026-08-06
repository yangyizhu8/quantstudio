"""B-6 staging-only activation/retirement contract tests."""
from __future__ import annotations

import json
import sqlite3

import duckdb
import pytest

from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema, init_sqlite_schema
from quantstudio.pipeline.qfq_cutover import create_cutover, transition_cutover
from quantstudio.pipeline.qfq_cutover_activation import (
    CutoverPreconditionFailed, activate_cutover_staging, build_cutover_evidence,
)


def _staging(tmp_path):
    db = tmp_path / "staging.duckdb"
    aux = tmp_path / "mcp-gen1.aux.db"
    c = duckdb.connect(str(db))
    init_duckdb_schema(c)
    for table in ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes"):
        c.execute(f'CREATE TABLE "{table}" (code VARCHAR, time BIGINT, close DOUBLE)')
        c.execute(f'INSERT INTO "{table}" VALUES (\'000001\', 1, 1.0)')
    c.execute("INSERT INTO qfq_discovery_baseline "
              "(cutover_id,price_source,source_generation,event_logical_key,applied_payload_hash,baselined_at,updated_at) "
              "VALUES ('cut1','mcp','mcp-gen1','k1','h1',NOW(),NOW())")
    create_cutover(c, cutover_id="cut1", price_source="mcp", source_generation="mcp-gen1",
                   schema_version="reanchor-2.1", baseline_version="qfq-detector-2.1",
                   aux_db_path=str(aux))
    transition_cutover(c, cutover_id="cut1", expected_status="planned", new_status="prepared")
    transition_cutover(c, cutover_id="cut1", expected_status="prepared", new_status="baseline_building")
    transition_cutover(c, cutover_id="cut1", expected_status="baseline_building", new_status="baseline_validated")
    ac = sqlite3.connect(str(aux)); init_sqlite_schema(ac); ac.commit(); ac.close()
    return c, db, aux


def _insert_legacy_rows(c):
    now = "2026-08-06 12:00:00"
    c.execute(
        "INSERT INTO qfq_cycle_run (cycle_id,phase,status,started_at,price_source,source_generation,cutover_id,updated_at) "
        "VALUES ('cyc-old','started','started',?,?,?, ?,?)",
        [now, "xtquant", "xtquant-legacy", "legacy-xtquant-pre-cutover", now])
    c.execute(
        "INSERT INTO qfq_watermark_intent (cycle_id,source,table_name,freq,source_generation,cutover_id,status) "
        "VALUES ('cyc-old','xtquant','stock_daily','daily','xtquant-legacy','legacy-xtquant-pre-cutover','pending')")
    for trigger_id, status in (("t-p", "pending"), ("t-s", "scheduled"),
                               ("t-i", "in_progress"), ("t-d", "dead_letter"), ("t-c", "committed")):
        c.execute(
            "INSERT INTO qfq_trigger_queue "
            "(trigger_id,asset_type,code,trigger_type,detection_source,status,trigger_id_version,"
            "price_source,source_generation,cutover_id,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,1,'xtquant','xtquant-legacy','legacy-xtquant-pre-cutover',?,?)",
            [trigger_id, "STOCK", "000001", "stock_dividend", "stock_dividend", status, "2026-08-06 12:00:00", "2026-08-06 12:00:00"])


def test_evidence_is_immutable_and_activation_retires_only_legacy_nonterminal(tmp_path):
    c, db, aux = _staging(tmp_path)
    _insert_legacy_rows(c)
    evidence = build_cutover_evidence(c, cutover_id="cut1", main_db_path=db,
                                      output_path=tmp_path / "evidence.json")
    assert evidence["kind"] == "b6-staging-evidence-1"
    # Repeating the exact freeze is idempotent; a different payload would be rejected.
    build_cutover_evidence(c, cutover_id="cut1", main_db_path=db,
                           output_path=tmp_path / "evidence.json")
    result = activate_cutover_staging(c, cutover_id="cut1", price_source="mcp",
                                      expected_old=None, main_db_path=db)
    assert result["status"] == "active"
    assert result["retired_triggers"] == 3
    assert result["interrupted_cycles"] == 1
    assert result["superseded_intents"] == 1
    assert c.execute("SELECT status FROM qfq_trigger_queue WHERE trigger_id='t-d'").fetchone()[0] == "dead_letter"
    assert c.execute("SELECT status FROM qfq_trigger_queue WHERE trigger_id='t-c'").fetchone()[0] == "committed"
    assert c.execute("SELECT status FROM qfq_trigger_queue WHERE trigger_id='t-p'").fetchone()[0] == "superseded"
    assert c.execute("SELECT status FROM qfq_watermark_intent WHERE cycle_id='cyc-old'").fetchone()[0] == "superseded"
    c.close()


def test_activation_fault_before_commit_rolls_back_all_retirement_and_pointer_changes(tmp_path):
    c, db, aux = _staging(tmp_path)
    _insert_legacy_rows(c)
    build_cutover_evidence(c, cutover_id="cut1", main_db_path=db,
                           output_path=tmp_path / "evidence.json")
    with pytest.raises(RuntimeError, match="after_retirement"):
        activate_cutover_staging(c, cutover_id="cut1", price_source="mcp",
                                 expected_old=None, main_db_path=db,
                                 fault_at="after_retirement")
    assert c.execute("SELECT status FROM qfq_source_cutover WHERE cutover_id='cut1'").fetchone()[0] == "baseline_validated"
    assert c.execute("SELECT COUNT(*) FROM qfq_active_cutover").fetchone()[0] == 0
    assert c.execute("SELECT status FROM qfq_trigger_queue WHERE trigger_id='t-p'").fetchone()[0] == "pending"
    assert c.execute("SELECT status FROM qfq_watermark_intent WHERE cycle_id='cyc-old'").fetchone()[0] == "pending"
    c.close()


def test_activation_expected_old_pointer_is_compare_and_swap(tmp_path):
    c, db, aux = _staging(tmp_path)
    build_cutover_evidence(c, cutover_id="cut1", main_db_path=db,
                           output_path=tmp_path / "evidence.json")
    with pytest.raises(Exception, match="expected_old"):
        activate_cutover_staging(c, cutover_id="cut1", price_source="mcp",
                                 expected_old="other-cutover", main_db_path=db)
    assert c.execute("SELECT status FROM qfq_source_cutover WHERE cutover_id='cut1'").fetchone()[0] == "baseline_validated"
    c.close()



def test_b6_evidence_and_activation_primitives_reject_configured_formal_db(tmp_path, monkeypatch):
    c, db, aux = _staging(tmp_path)
    from quantstudio import _paths
    monkeypatch.setattr(_paths, "db_path", lambda: db)
    with pytest.raises(CutoverPreconditionFailed, match="formal database"):
        build_cutover_evidence(c, cutover_id="cut1", main_db_path=db,
                               output_path=tmp_path / "evidence.json")
    c.close()
