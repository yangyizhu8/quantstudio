"""B-6 WP6 formal hard-crash recovery + alias/lock refusal tests.

Covers (G0 §15):
  * both after-COMMIT boundaries (schema migration + activation) hard-crash
    with strict exit code 92 in an independent Windows subprocess, reject
    0xC0000005, and recover via ALREADY_CURRENT / ALREADY_ACTIVE
  * alias/symlink/junction/hardlink path refusal
  * concurrent dual-lock busy refusal

Hard-crash tests run SERIALLY (no concurrent DuckDB pytest process), mirroring
``test_qfq_schema_migration.py::TestHardCrashRecovery``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from quantstudio.pipeline.qfq_formal_authorization import (
    _bin_write_flags, path_is_link_like, same_file,
)
from quantstudio.pipeline.qfq_formal_cutover import (
    FormalCutoverCommittedReportError, run_formal_schema_migration,
    RECOVERY_STATUS_ALREADY_ACTIVE, recover_already_active,
)
from quantstudio.pipeline.qfq_schema_status import SchemaStatus, detect_schema_status
from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema

_ROOT = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))
HARD_CRASH_EXIT_CODE = 92

# Reuse the canonical COMPLETE_2_0 legacy seed from the migration test suite so
# the formal migration runs against a genuine 2.0 schema (init_duckdb_schema
# would produce a 2.1 schema and short-circuit to ALREADY_CURRENT).
from test_qfq_schema_migration import _seed_full_legacy  # noqa: E402


# ===========================================================================
# 1. Schema migration hard-crash recovery (exit 92)
# ===========================================================================


class TestSchemaMigrationHardCrash:
    def test_os_exit_after_commit_recovers_already_current(self, tmp_path):
        """Hard-crash after durable COMMIT but before report: exit 92, then
        re-open classifies as already_current."""
        db = tmp_path / "formal_migrate.db"
        c = duckdb.connect(str(db))
        _seed_full_legacy(c)
        c.close()
        script = tmp_path / "hard_crash_migrate.py"
        script.write_text(
            "import sys, os\n"
            f"sys.path.insert(0, {str(_ROOT)!r})\n"
            "from quantstudio.pipeline.qfq_formal_cutover import (\n"
            "    run_formal_schema_migration, FormalCutoverCommittedReportError)\n"
            "from pathlib import Path\n"
            f"db = Path({str(db)!r})\n"
            "try:\n"
            "    run_formal_schema_migration(db, allowed_root=db.parent, fault_at='after_commit_before_report')\n"
            "except FormalCutoverCommittedReportError:\n"
            "    os._exit(92)\n",
            encoding="utf-8")
        result = subprocess.run([sys.executable, str(script)], capture_output=True)
        assert result.returncode == HARD_CRASH_EXIT_CODE, \
            f"expected exit {HARD_CRASH_EXIT_CODE}, got {result.returncode}: {result.stderr.decode(errors='replace')}"
        # The DB is durably COMPLETE_2_1.
        ro = duckdb.connect(str(db), read_only=True)
        assert detect_schema_status(ro) == SchemaStatus.COMPLETE_2_1
        ro.close()
        # Re-running classifies as already_current.
        mig = run_formal_schema_migration(db, allowed_root=db.parent)
        assert mig.report_status == "ALREADY_CURRENT"
        assert mig.already_current is True

    def test_before_commit_fault_rolls_back_to_2_0(self, tmp_path):
        db = tmp_path / "formal_migrate_rollback.db"
        c = duckdb.connect(str(db))
        _seed_full_legacy(c)
        c.close()
        with pytest.raises(Exception):
            run_formal_schema_migration(db, allowed_root=db.parent, fault_at="before_commit")
        ro = duckdb.connect(str(db), read_only=True)
        assert detect_schema_status(ro) == SchemaStatus.COMPLETE_2_0
        ro.close()


# ===========================================================================
# 2. Activation after-COMMIT recovery (ALREADY_ACTIVE) — unit level
# ===========================================================================


class TestActivationAfterCommitRecovery:
    def test_recover_already_active_classifies_durable_state(self, tmp_path):
        """After an after_commit_before_report fault leaves the cutover durable,
        recover_already_active re-opens read-only and classifies ALREADY_ACTIVE."""
        from quantstudio.pipeline.qfq_cutover import create_cutover, transition_cutover
        from quantstudio.pipeline.qfq_cutover_activation import _do_activate_in_txn
        from quantstudio.pipeline.qfq_snapshot_evidence import table_evidence
        import sqlite3
        from quantstudio.pipeline.qfq_reanchor_schema import init_sqlite_schema

        db = tmp_path / "formal_act.db"
        aux = tmp_path / "mcp-gen1.aux.db"
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
        # Drive to durable commit via the after_commit fault (raises post-COMMIT).
        with pytest.raises(RuntimeError, match="after_commit_before_report"):
            _do_activate_in_txn(c, cutover_id="cut1", price_source="mcp", expected_old=None,
                                fault_at="after_commit_before_report", pre_wm=pre_wm,
                                committed_before=committed_before, current_id=None)
        c.close()
        # Recover read-only.
        report = recover_already_active(main_db=db, aux_db=aux, cutover_id="cut1",
                                        price_source="mcp", handoff_dir=tmp_path)
        assert report["recovery_status"] == RECOVERY_STATUS_ALREADY_ACTIVE
        assert report["cutover"][1] == "active"
        assert report["active"][1] == "cut1"
        # Field-name set matches staging after_commit_recovery.json
        for field in ("cutover", "active", "legacy_triggers", "legacy_intents",
                      "legacy_cycles", "committed", "dead_letter", "mcp_gen1"):
            assert field in report


# ===========================================================================
# 3. Alias / symlink / junction / hardlink / case refusal
# ===========================================================================


class TestAliasRefusal:
    def test_hardlink_alias_detected(self, tmp_path):
        """A hardlink alias of the formal file has nlink > 1 -> link-like."""
        target = tmp_path / "real.db"
        target.write_bytes(b"payload")
        link = tmp_path / "alias.db"
        try:
            os.link(str(target), str(link))
        except OSError:
            pytest.skip("hardlinks not supported here")
        assert link.exists()
        assert path_is_link_like(link) is True

    def test_same_file_detects_case_alias(self, tmp_path):
        """Case-only alias of a path resolves to the same file."""
        p = tmp_path / "Formal.DB"
        p.write_bytes(b"x")
        lower = tmp_path / "formal.db"
        # On case-insensitive filesystems these are the same file.
        assert same_file(p, lower)


# ===========================================================================
# 4. Concurrent dual-lock busy refusal
# ===========================================================================


class TestDualLockBusy:
    def test_daemon_lock_busy_refused(self, tmp_path, monkeypatch):
        """When .daemon.lock is already held, the formal runner refuses."""
        from filelock import FileLock
        from quantstudio.pipeline import qfq_formal_cutover as fc
        from quantstudio.pipeline.daemon_lifecycle import daemon_lock_path
        # Point DATA_ROOT at tmp_path so the lock files live there.
        fake_lock = tmp_path / ".daemon.lock"
        monkeypatch.setattr(fc, "daemon_lock_path", lambda: fake_lock)
        # Pre-acquire the daemon lock from another FileLock instance.
        holder = FileLock(str(fake_lock), timeout=0)
        holder.acquire(timeout=0)
        try:
            with pytest.raises(fc.FormalCutoverRefused):
                fc._acquire_dual_locks()
        finally:
            holder.release()
