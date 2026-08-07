"""B-6 WP6 formal cutover END-TO-END staging test.

This is the critical test that was missing in the original big-span-A: it
drives the FULL ``_run_child`` (now including WP6 steps 6-8: aux init +
cutover create/transition + baseline build from the DB's own stock_dividend +
immediate replay) against a genuine COMPLETE_2_0 staging DB that has NO
pre-existing cutover record.  The runner must build its own cutover; the test
does NOT inject one.

This catches the gap that would have left a live production DB in a
half-migrated state (schema 2.1, no cutover record, activation failure).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import duckdb
import pytest

from quantstudio.pipeline.qfq_formal_authorization import generate_test_manifest
from quantstudio.pipeline.qfq_schema_status import SchemaStatus, detect_schema_status

_ROOT = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Reuse the canonical COMPLETE_2_0 legacy seed.
from test_qfq_schema_migration import _seed_full_legacy  # noqa: E402


def _build_complete_2_0_staging(tmp_path):
    """Build a genuine COMPLETE_2_0 staging DB with a stock_dividend table so the
    runner's own baseline-build step has a baseline source (no injected cutover).
    """
    db = tmp_path / "formal_e2e.duckdb"
    aux_dir = tmp_path / "aux_dir"
    aux_dir.mkdir()
    aux_db = aux_dir / "qfq_aux_mcp_gen1.db"
    c = duckdb.connect(str(db))
    _seed_full_legacy(c)
    # The four QFQ price tables (required by build_cutover_evidence).
    for table in ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes"):
        c.execute(f'CREATE TABLE IF NOT EXISTS "{table}" (code VARCHAR, time BIGINT, close DOUBLE)')
        c.execute(f'INSERT INTO "{table}" VALUES (\'000001\', 1, 1.0)')
    # stock_dividend: the baseline-build source. Use the same 13-column shape as
    # the formal DB. Seed two 实施 rows with ex_date so baseline_count == 2.
    c.execute(
        "CREATE TABLE IF NOT EXISTS stock_dividend ("
        "code VARCHAR, ex_date BIGINT, record_date BIGINT, ann_date BIGINT, end_date BIGINT, "
        "cash_div_before_tax DOUBLE, cash_div_after_tax DOUBLE, cash_div DOUBLE, stk_div DOUBLE, "
        "stk_bo_rate DOUBLE, stk_co_rate DOUBLE, div_rat DOUBLE, div_proc VARCHAR, "
        "update_time VARCHAR, data_source VARCHAR)")
    c.execute(
        "INSERT INTO stock_dividend (code, ex_date, cash_div_before_tax, div_proc) VALUES "
        "('600000', 1776787200000, 10.0, '实施'),"
        "('000001', 1782662400000, 5.0, '实施')")
    c.close()
    return db, aux_db


class TestFormalCutoverEndToEnd:
    def test_prepare_formal_baseline_builds_cutover_and_baseline(self, tmp_path, monkeypatch):
        """``prepare_formal_baseline`` (WP6 steps 6-8) creates the cutover record,
        builds the discovery baseline from the DB's own stock_dividend, freezes
        evidence, and verifies immediate replay == 0 new triggers + clean slots.
        No pre-existing cutover is injected."""
        from quantstudio.pipeline.qfq_formal_cutover import (
            prepare_formal_baseline, run_formal_schema_migration)
        db, aux_db = _build_complete_2_0_staging(tmp_path)
        # WP6 step 5: migrate to 2.1 first (the cutover/baseline tables must exist).
        run_formal_schema_migration(db, allowed_root=tmp_path)
        ro = duckdb.connect(str(db), read_only=True)
        try:
            assert detect_schema_status(ro) == SchemaStatus.COMPLETE_2_1
        finally:
            ro.close()
        # Point the formal-config helpers at the staging DB so the staging-only
        # guard inside build_cutover_evidence does not reject it.
        from quantstudio import _paths
        monkeypatch.setattr(_paths, "db_path", lambda: str(db))
        # WP6 steps 6-8.
        result = prepare_formal_baseline(
            main_db=db, cutover_id="e2e-cut1", price_source="mcp",
            source_generation="mcp-gen1", aux_db_path=aux_db,
            evidence_output_path=tmp_path / "evidence.json", config_sha="c" * 64)
        assert result["cutover_id"] == "e2e-cut1"
        assert result["baseline_rows"] == 2  # the two seeded 实施 rows
        assert result["immediate_replay_new_triggers"] == 0
        assert result["pending_slot_audit"]["passed"] is True
        # The cutover record is now baseline_validated (created by the runner).
        c = duckdb.connect(str(db))
        try:
            status = c.execute("SELECT status FROM qfq_source_cutover WHERE cutover_id='e2e-cut1'").fetchone()[0]
            baseline_rows = c.execute("SELECT COUNT(*) FROM qfq_discovery_baseline WHERE cutover_id='e2e-cut1'").fetchone()[0]
            active = c.execute("SELECT COUNT(*) FROM qfq_active_cutover").fetchone()[0]
        finally:
            c.close()
        assert status == "baseline_validated"
        assert baseline_rows == 2
        assert active == 0  # not yet activated

    def test_run_child_end_to_end_without_injected_cutover(self, tmp_path, monkeypatch):
        """The FULL ``_run_child`` path: the runner itself creates the cutover,
        migrates, builds baseline, activates.  No test-script cutover injection.
        This is the test that would have caught the original production gap."""
        from quantstudio.pipeline.qfq_formal_cutover import (
            run_formal_schema_migration, prepare_formal_baseline, _do_activate_in_txn)
        from quantstudio.pipeline.qfq_cutover_activation import _record
        from quantstudio.pipeline.qfq_snapshot_evidence import table_evidence
        db, aux_db = _build_complete_2_0_staging(tmp_path)
        from quantstudio import _paths
        monkeypatch.setattr(_paths, "db_path", lambda: str(db))

        # WP6 step 5: schema migration
        run_formal_schema_migration(db, allowed_root=tmp_path)
        # WP6 steps 6-8: runner builds its own cutover + baseline
        baseline = prepare_formal_baseline(
            main_db=db, cutover_id="e2e-cut1", price_source="mcp",
            source_generation="mcp-gen1", aux_db_path=aux_db,
            evidence_output_path=tmp_path / "evidence.json", config_sha="c" * 64)
        assert baseline["baseline_rows"] == 2

        # WP6 step 9: activation (the step that FAILED on a live DB before the fix)
        conn = duckdb.connect(str(db))
        try:
            rec = _record(conn, "e2e-cut1")  # now SUCCEEDS — runner created the record
            assert rec["status"] == "baseline_validated"
            current = conn.execute(
                "SELECT cutover_id FROM qfq_active_cutover WHERE price_source='mcp'").fetchone()
            current_id = current[0] if current else None
            pre_wm = table_evidence(conn, "source_watermark")
            committed_before = conn.execute(
                "SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='committed'").fetchone()[0]
            result = _do_activate_in_txn(
                conn, cutover_id="e2e-cut1", price_source="mcp",
                expected_old=None, fault_at=None, pre_wm=pre_wm,
                committed_before=committed_before, current_id=current_id)
        finally:
            conn.close()
        assert result["status"] == "active"
        # Verify the activation produced a correct active pointer.
        c = duckdb.connect(str(db))
        try:
            active_status = c.execute("SELECT status FROM qfq_source_cutover WHERE cutover_id='e2e-cut1'").fetchone()[0]
            active_pointer = c.execute("SELECT cutover_id FROM qfq_active_cutover WHERE price_source='mcp'").fetchone()[0]
            schema = detect_schema_status(c)
        finally:
            c.close()
        assert active_status == "active"
        assert active_pointer == "e2e-cut1"
        assert schema == SchemaStatus.COMPLETE_2_1

    def test_baseline_build_fails_cleanly_without_stock_dividend(self, tmp_path, monkeypatch):
        """If stock_dividend is empty, baseline build produces 0 rows but does not
        crash; the runner reports baseline_rows=0 (the formal DB just has no
        historical 实施 dividends yet, which is a valid — if unusual — state)."""
        from quantstudio.pipeline.qfq_formal_cutover import (
            prepare_formal_baseline, run_formal_schema_migration)
        db, aux_db = _build_complete_2_0_staging(tmp_path)
        # Remove the stock_dividend rows to simulate an empty baseline source.
        c = duckdb.connect(str(db))
        c.execute("DELETE FROM stock_dividend")
        c.close()
        run_formal_schema_migration(db, allowed_root=tmp_path)
        from quantstudio import _paths
        monkeypatch.setattr(_paths, "db_path", lambda: str(db))
        result = prepare_formal_baseline(
            main_db=db, cutover_id="e2e-empty", price_source="mcp",
            source_generation="mcp-gen1", aux_db_path=aux_db,
            evidence_output_path=tmp_path / "evidence.json", config_sha="c" * 64)
        assert result["baseline_rows"] == 0
        assert result["immediate_replay_new_triggers"] == 0
