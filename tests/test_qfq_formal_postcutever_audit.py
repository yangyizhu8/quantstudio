"""B-6 WP7-E1 immediate post-cutover audit tests."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import duckdb
import pytest

from quantstudio.pipeline.qfq_formal_postcutever_audit import (
    PostCutoverAuditError, audit_immediate,
)
from quantstudio.pipeline.qfq_formal_cutover import _write_handoff, write_exit_evidence
from quantstudio.pipeline.qfq_formal_authorization import hash_manifest_bytes
from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema, init_sqlite_schema
from quantstudio.pipeline.qfq_cutover import create_cutover, transition_cutover
from quantstudio.pipeline.qfq_cutover_activation import _do_activate_in_txn
from quantstudio.pipeline.qfq_snapshot_evidence import table_evidence


def _make_active_formal_db(tmp_path, monkeypatch):
    """Build a small COMPLETE_2_1-like DB with an active mcp-gen1 cutover, and
    point the formal-config helpers at it."""
    db = tmp_path / "formal.duckdb"
    aux = tmp_path / "qfq_aux_mcp_gen1.db"
    c = duckdb.connect(str(db))
    init_duckdb_schema(c)
    for table in ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes"):
        c.execute(f'CREATE TABLE IF NOT EXISTS "{table}" (code VARCHAR, time BIGINT, close DOUBLE)')
        c.execute(f'INSERT INTO "{table}" VALUES (\'000001\', 1, 1.0)')
    c.execute("INSERT INTO qfq_discovery_baseline "
              "(cutover_id,price_source,source_generation,event_logical_key,applied_payload_hash,baselined_at,updated_at) "
              "VALUES ('cut1','mcp','mcp-gen1','k1','h1',NOW(),NOW())")
    create_cutover(c, cutover_id="cut1", price_source="mcp", source_generation="mcp-gen1",
                   schema_version="reanchor-2.1", baseline_version="qfq-detector-2.1", aux_db_path=str(aux))
    for old, new in (("planned", "prepared"), ("prepared", "baseline_building"),
                     ("baseline_building", "baseline_validated")):
        transition_cutover(c, cutover_id="cut1", expected_status=old, new_status=new)
    ac = sqlite3.connect(str(aux)); init_sqlite_schema(ac); ac.commit(); ac.close()
    pre_wm = table_evidence(c, "source_watermark")
    committed_before = c.execute("SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='committed'").fetchone()[0]
    _do_activate_in_txn(c, cutover_id="cut1", price_source="mcp", expected_old=None,
                        fault_at=None, pre_wm=pre_wm, committed_before=committed_before,
                        current_id=None)
    c.close()
    # Point formal-config helpers at the tmp DB.
    from quantstudio import _paths
    monkeypatch.setattr(_paths, "db_path", lambda: str(db))
    # aux router config dir: create a minimal mcp_only dir
    cfg_dir = tmp_path / "mcp_only"
    cfg_dir.mkdir()
    (cfg_dir / "qfq_aux_paths.json").write_text(json.dumps({
        "default": str(aux),
        "generations": {"xtquant-legacy": str(tmp_path / "qfq_aux.db"),
                        "mcp-gen1": str(aux)},
    }))
    return db, aux, cfg_dir


class TestPostCutoverAudit:
    def test_missing_handoff_rejected(self, tmp_path, monkeypatch):
        _make_active_formal_db(tmp_path, monkeypatch)
        hdir = tmp_path / "wp6_out"
        hdir.mkdir()
        with pytest.raises(PostCutoverAuditError, match="missing handoff"):
            audit_immediate(handoff_dir=hdir, config_dir=tmp_path / "mcp_only")

    def test_missing_exit_evidence_rejected(self, tmp_path, monkeypatch):
        db, aux, cfg = _make_active_formal_db(tmp_path, monkeypatch)
        hdir = tmp_path / "wp6_out"
        hdir.mkdir()
        handoff = {"kind": "quantstudio-b6-formal-cutover-handoff", "cutover_id": "cut1",
                   "price_source": "mcp", "source_generation": "mcp-gen1",
                   "aux_db_path": str(aux), "watermark_release_authorized": False}
        _write_handoff(handoff_dir=hdir, payload=handoff)
        with pytest.raises(PostCutoverAuditError, match="missing exit evidence"):
            audit_immediate(handoff_dir=hdir, config_dir=cfg)

    def test_handoff_sha_mismatch_rejected(self, tmp_path, monkeypatch):
        db, aux, cfg = _make_active_formal_db(tmp_path, monkeypatch)
        hdir = tmp_path / "wp6_out"
        hdir.mkdir()
        handoff = {"kind": "quantstudio-b6-formal-cutover-handoff", "cutover_id": "cut1",
                   "price_source": "mcp", "source_generation": "mcp-gen1",
                   "aux_db_path": str(aux), "watermark_release_authorized": False}
        raw_sha = _write_handoff(handoff_dir=hdir, payload=handoff)
        # Publish an exit evidence with a WRONG handoff sha.
        write_exit_evidence(handoff_dir=hdir, handoff_raw_sha="0" * 64,
                            child_pid=0, child_create_time=0.0, exit_code=0,
                            locks_released_verified=True, descendant_scan=[])
        with pytest.raises(PostCutoverAuditError, match="handoff raw SHA mismatch"):
            audit_immediate(handoff_dir=hdir, config_dir=cfg)

    def test_audit_passes_on_clean_state(self, tmp_path, monkeypatch):
        db, aux, cfg = _make_active_formal_db(tmp_path, monkeypatch)
        hdir = tmp_path / "wp6_out"
        hdir.mkdir()
        handoff = {"kind": "quantstudio-b6-formal-cutover-handoff", "cutover_id": "cut1",
                   "price_source": "mcp", "source_generation": "mcp-gen1",
                   "aux_db_path": str(aux), "watermark_release_authorized": False}
        raw_sha = _write_handoff(handoff_dir=hdir, payload=handoff)
        write_exit_evidence(handoff_dir=hdir, handoff_raw_sha=raw_sha,
                            child_pid=0, child_create_time=0.0, exit_code=0,
                            locks_released_verified=True, descendant_scan=[])
        report = audit_immediate(handoff_dir=hdir, config_dir=cfg)
        assert report["active_cutover_id"] == "cut1"
        assert report["legacy_nonterminal_zero"] is True
        assert report["pending_intent_zero"] is True
        assert report["started_cycle_zero"] is True
        assert report["watermark_release_authorized"] is False
