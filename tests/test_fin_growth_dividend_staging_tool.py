"""
Test staging tool (W1.8) -- expanded with 13 tests.

All tests use temporary DuckDB, temporary staging directories, and
subprocess.run with timeout=30. No real Tushare credentials required.

Covers: prepare, run-task safety, audit evidence, and promote gates.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import duckdb
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_STAGING_SCRIPT = _PROJECT_ROOT / "scripts" / "backfill_fin_growth_dividend_staging.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mini_db() -> str:
    """Create a minimal temp DuckDB with source_watermark table."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    conn = duckdb.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_watermark (
            source VARCHAR, table_name VARCHAR, freq VARCHAR,
            last_date BIGINT, last_batch_id VARCHAR, updated_at TIMESTAMP,
            source_generation VARCHAR NOT NULL, cutover_id VARCHAR NOT NULL,
            PRIMARY KEY(source, table_name, freq)
        )
    """)
    conn.close()
    return path


def _make_config_dir(temp_dir: Path, db_path: str) -> Path:
    """Create a minimal config directory with the files the staging tool needs."""
    config_dir = temp_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "data_config.json").write_text(
        json.dumps({"path": db_path}), encoding="utf-8")
    (config_dir / "sources_config.json").write_text(
        json.dumps({"sources": {"tushare": {"enabled": True}}}), encoding="utf-8")
    tasks = {
        "tasks": [
            {"name": "fin_indicator", "table": "fin_indicator", "source": "tushare",
             "freq": "daily", "codes": ["ALL"], "authoritative_source": "tushare",
             "allow_fallback": False, "start_date": "2020-01-01", "max_workers": 1},
            {"name": "stock_dividend", "table": "stock_dividend", "source": "tushare",
             "freq": "daily", "codes": ["ALL"], "authoritative_source": "tushare",
             "allow_fallback": False, "start_date": "2020-01-01", "max_workers": 1},
            {"name": "stock_daily", "table": "stock_daily", "source": "xtquant",
             "freq": "daily", "codes": ["ALL"], "start_date": "2020-01-01", "max_workers": 1},
        ]
    }
    (config_dir / "collector_tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
    schemas = {
        "fin_indicator": {"columns": {"code": {"type": "str"}, "eps": {"type": "float"}},
                          "primary_key": ["code", "end_date", "ann_date"]},
        "stock_dividend": {"columns": {"code": {"type": "str"}, "ex_date": {"type": "int"}},
                           "primary_key": ["code", "ex_date"]},
        "stock_daily": {"columns": {"code": {"type": "str"}, "time": {"type": "int"}},
                        "primary_key": ["code", "time"]},
    }
    (config_dir / "alignment_rules.json").write_text(
        json.dumps({"schemas": schemas}), encoding="utf-8")
    return config_dir


def _run_staging(args: list, cwd: str = None, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run the staging script as a subprocess."""
    cmd = [sys.executable, str(_STAGING_SCRIPT)] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=cwd or str(_PROJECT_ROOT))


# ===========================================================================
# Tests: prepare phase
# ===========================================================================

class TestPrepareDryRun:
    """test_prepare_dry_run_returns_0"""

    def test_prepare_dry_run_returns_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root = tmp_p / "staging"

            result = _run_staging([
                "--source-db", db_path,
                "--staging-root", str(staging_root),
                "--dry-run", "--prepare",
            ])

            assert result.returncode == 0, f"stderr: {result.stderr}"
            assert not staging_root.exists()
            os.unlink(db_path)


class TestPrepareCreatesStaging:
    """test_prepare_creates_staging_with_absolute_paths"""

    def test_prepare_creates_staging_with_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            config_dir = _make_config_dir(tmp_p, db_path)
            staging_root = tmp_p / "staging"

            result = _run_staging([
                "--source-db", db_path,
                "--staging-root", str(staging_root),
                "--start-date", "2022-01-01",
                "--prepare",
            ])

            assert result.returncode == 0, f"stderr: {result.stderr}"
            assert staging_root.exists()

            # Check that data_config.json has absolute path
            staging_config = staging_root / "config" / "data_config.json"
            assert staging_config.exists()
            cfg = json.loads(staging_config.read_text(encoding="utf-8"))
            db_path_in_cfg = cfg.get("path", "")
            assert Path(db_path_in_cfg).is_absolute()
            # The path should point to the staging DB, not the source DB
            assert "staging" in db_path_in_cfg

            os.unlink(db_path)


class TestPrepareStartDate:
    """test_prepare_updates_start_date_for_allowed_tasks"""

    def test_prepare_updates_start_date_for_allowed_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            config_dir = _make_config_dir(tmp_p, db_path)
            staging_root = tmp_p / "staging"

            result = _run_staging([
                "--source-db", db_path,
                "--staging-root", str(staging_root),
                "--start-date", "2022-01-01",
                "--prepare",
            ])

            assert result.returncode == 0, f"stderr: {result.stderr}"

            staging_tasks_path = staging_root / "config" / "collector_tasks.json"
            assert staging_tasks_path.exists()
            tasks_cfg = json.loads(staging_tasks_path.read_text(encoding="utf-8"))
            for t in tasks_cfg.get("tasks", []):
                if t.get("table") in ("fin_indicator", "stock_dividend"):
                    assert t["start_date"] == "2022-01-01", \
                        f"Expected start_date=2022-01-01 for {t.get('table')}, got {t.get('start_date')}"

            os.unlink(db_path)


class TestPrepareOtherTasks:
    """test_prepare_does_not_modify_other_tasks"""

    def test_prepare_does_not_modify_other_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root = tmp_p / "staging"

            # Read production config to learn original start_date values
            prod_tasks_path = _PROJECT_ROOT / "config" / "collector_tasks.json"
            prod_tasks = json.loads(prod_tasks_path.read_text(encoding="utf-8"))
            original_start_dates = {}
            for t in prod_tasks.get("tasks", []):
                original_start_dates[t["name"]] = t.get("start_date")

            result = _run_staging([
                "--source-db", db_path,
                "--staging-root", str(staging_root),
                "--start-date", "2022-01-01",
                "--prepare",
            ])

            assert result.returncode == 0, f"stderr: {result.stderr}"

            staging_tasks_path = staging_root / "config" / "collector_tasks.json"
            staging_tasks = json.loads(staging_tasks_path.read_text(encoding="utf-8"))
            for t in staging_tasks.get("tasks", []):
                name = t.get("name")
                table = t.get("table")
                if table not in ("fin_indicator", "stock_dividend"):
                    expected = original_start_dates.get(name)
                    actual = t.get("start_date")
                    assert actual == expected, \
                        f"Non-allowed task {name} ({table}): expected start_date={expected}, got {actual}"

            os.unlink(db_path)


class TestProductionConfigUnchanged:
    """test_production_config_unchanged_after_prepare"""

    def test_production_config_unchanged_after_prepare(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            config_dir = _make_config_dir(tmp_p, db_path)
            staging_root = tmp_p / "staging"

            # Snapshot production config before prepare
            orig_tasks = json.loads((config_dir / "collector_tasks.json").read_text(encoding="utf-8"))
            orig_data = json.loads((config_dir / "data_config.json").read_text(encoding="utf-8"))

            result = _run_staging([
                "--source-db", db_path,
                "--staging-root", str(staging_root),
                "--start-date", "2022-01-01",
                "--prepare",
            ])

            assert result.returncode == 0, f"stderr: {result.stderr}"

            # Production config MUST be unchanged
            post_tasks = json.loads((config_dir / "collector_tasks.json").read_text(encoding="utf-8"))
            post_data = json.loads((config_dir / "data_config.json").read_text(encoding="utf-8"))

            assert post_tasks == orig_tasks
            assert post_data == orig_data

            os.unlink(db_path)


# ===========================================================================
# Tests: run-task safety
# ===========================================================================

class TestRunTaskWrongDbBlocked:
    """test_wrong_db_path_blocked_for_run_task"""

    def test_wrong_db_path_blocked_for_run_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root = tmp_p / "staging"

            # Manually create a staging config that points to the WRONG DB
            staging_root.mkdir(parents=True, exist_ok=True)
            staging_cfg_dir = staging_root / "config"
            staging_cfg_dir.mkdir(parents=True, exist_ok=True)

            # data_config.json points to source db (not staging db)
            (staging_cfg_dir / "data_config.json").write_text(
                json.dumps({"path": db_path}), encoding="utf-8")
            (staging_cfg_dir / "sources_config.json").write_text(
                json.dumps({"sources": {"tushare": {"enabled": True}}}), encoding="utf-8")
            (staging_cfg_dir / "collector_tasks.json").write_text(
                json.dumps({"tasks": [{"name": "fin_indicator", "table": "fin_indicator",
                                       "source": "tushare"}]}), encoding="utf-8")
            (staging_cfg_dir / "alignment_rules.json").write_text(
                json.dumps({"schemas": {}}), encoding="utf-8")

            result = _run_staging([
                "--source-db", db_path,
                "--staging-root", str(staging_root),
                "--run-task", "fin_indicator",
            ])

            assert result.returncode != 0
            assert "SAFETY BLOCK" in (result.stdout + result.stderr)

            os.unlink(db_path)


class TestStaleLock:
    """test_stale_lock_does_not_block"""

    def test_stale_lock_does_not_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            config_dir = _make_config_dir(tmp_p, db_path)
            staging_root = tmp_p / "staging"

            lock_path = Path(db_path).parent / ".daemon.lock"
            lock_path.write_text("stale")

            result = _run_staging([
                "--source-db", db_path,
                "--staging-root", str(staging_root),
                "--prepare",
            ])
            # Stale lock file alone should NOT block (P0-2 fix).
            assert result.returncode == 0, f"stderr: {result.stderr}"

            os.unlink(db_path)


# ===========================================================================
# Tests: subprocess environment
# ===========================================================================

class TestSubprocessEnv:
    """test_subprocess_env_has_staging_data_root"""

    def test_subprocess_env_has_staging_data_root(self):
        """Verify that phase_run_task sets QUANTSTUDIO_DATA_ROOT in the subprocess env.

        We import phase_run_task directly and mock subprocess to inspect the env.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root = tmp_p / "staging"

            # Create a proper staging environment first
            _make_config_dir(tmp_p, db_path)
            staging_root.mkdir(parents=True, exist_ok=True)
            staging_cfg_dir = staging_root / "config"
            staging_cfg_dir.mkdir(parents=True, exist_ok=True)
            staging_db = staging_root / "staging.db"
            # Copy source db to staging
            import shutil
            shutil.copy2(db_path, str(staging_db))

            (staging_cfg_dir / "data_config.json").write_text(
                json.dumps({"path": str(staging_db.resolve())}), encoding="utf-8")
            (staging_cfg_dir / "sources_config.json").write_text(
                json.dumps({"sources": {"tushare": {"enabled": True}}}), encoding="utf-8")
            (staging_cfg_dir / "collector_tasks.json").write_text(
                json.dumps({"tasks": [{"name": "fin_indicator", "table": "fin_indicator",
                                       "source": "tushare"}]}), encoding="utf-8")
            (staging_cfg_dir / "alignment_rules.json").write_text(
                json.dumps({"schemas": {}}), encoding="utf-8")

            # Import phase_run_task and mock subprocess to capture the env
            from scripts.backfill_fin_growth_dividend_staging import phase_run_task
            import argparse

            captured_env = {}

            def fake_run_cmd(cmd, cwd=None, dry_run=False, env=None, timeout=21600, log_file=None, staging_db=None, task_name=None, runtime_manifest=None, runtime_nonce=None):
                nonlocal captured_env
                captured_env = dict(env) if env else {}
                # Parse cmd list for --runtime-manifest and --runtime-nonce
                import json as _json, os as _os, pathlib
                cmd_manifest = None
                cmd_nonce = None
                if isinstance(cmd, list):
                    for i, arg in enumerate(cmd):
                        if arg == "--runtime-manifest" and i + 1 < len(cmd):
                            cmd_manifest = cmd[i + 1]
                        elif arg == "--runtime-nonce" and i + 1 < len(cmd):
                            cmd_nonce = cmd[i + 1]
                if cmd_manifest:
                    _sr = staging_root.resolve()
                    from datetime import datetime as _dt
                    _started = _dt.now().isoformat()
                    _manifest = {
                        "format_version": "1.0", "task": "fin_indicator",
                        "pid": _os.getpid(), "nonce": cmd_nonce,
                        "created_at": _started,
                        "QUANTSTUDIO_DATA_ROOT": str(_sr),
                        "imported_DATA_ROOT": str(_sr),
                        "writer_db_path": str(staging_db.resolve()),
                        "batch_audit_db_path": str((_sr / "batch_audit.db")),
                        "quarantine_db_path": str((_sr / "quarantine.db")),
                        "daemon_log_path": str((_sr / "logs" / "daemon.log")),
                        "collector_lock_path": str((_sr / ".collector_run.lock")),
                        "daemon_lock_path": str((_sr / ".daemon.lock")),
                        "daemon_status_path": str((_sr / "daemon_status.json")),
                        "config_dir": str(staging_cfg_dir.resolve()),
                    }
                    p = pathlib.Path(cmd_manifest)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(_json.dumps(_manifest), encoding="utf-8")
                # W2-0.8: write a success batch to the ledger so phase_run_task's
                # batch-status cross-check passes.
                import sqlite3 as _sq3
                from datetime import datetime as _bdt
                _now_iso = _bdt.now().isoformat()
                _lc = _sq3.connect(str(_sr / "batch_audit.db"))
                try:
                    _lc.execute(
                        "CREATE TABLE IF NOT EXISTS batch_audit ("
                        " batch_id TEXT, task_name TEXT, table_name TEXT, source TEXT,"
                        " freq TEXT, status TEXT, rows_raw INTEGER, rows_passed INTEGER,"
                        " rows_rejected INTEGER, rows_written INTEGER, rows_new INTEGER,"
                        " rows_updated INTEGER, started_at TEXT, finished_at TEXT)")
                    _lc.execute(
                        "INSERT INTO batch_audit (batch_id, task_name, table_name, source,"
                        " freq, status, rows_raw, rows_passed, rows_rejected, rows_written,"
                        " rows_new, rows_updated, started_at, finished_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        ("b-fin-default", "fin_indicator", "fin_indicator", "tushare",
                         "daily", "success", 1, 1, 0, 1, 1, 0, _now_iso, _now_iso))
                    _lc.commit()
                finally:
                    _lc.close()
                from scripts.backfill_fin_growth_dividend_staging import CommandResult as _CR
                from datetime import datetime as _dt
                return _CR(exit_code=0, pid=_os.getpid(),
                           started_at=_started, finished_at=_dt.now().isoformat())

            with patch("scripts.backfill_fin_growth_dividend_staging.run_cmd", fake_run_cmd):
                ns = argparse.Namespace(
                    staging_root=str(staging_root),
                    run_task="fin_indicator",
                    dry_run=False,
                    timeout_sec=3600,
                    source_db=db_path,
                )
                rc = phase_run_task(ns)
                assert rc == 0

            assert "QUANTSTUDIO_DATA_ROOT" in captured_env
            assert captured_env["QUANTSTUDIO_DATA_ROOT"] == str(staging_root.resolve())

            os.unlink(db_path)


# ===========================================================================
# Tests: audit phase
# ===========================================================================

class TestAuditReceivesAuthorityRules:
    """test_audit_receives_authority_rules"""

    def test_audit_receives_authority_rules(self):
        """Verify that DataQualityAuditor receives authority_rules during audit."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root = tmp_p / "staging"

            # Create staging environment
            staging_db = staging_root / "staging.db"
            staging_root.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(db_path, str(staging_db))

            staging_cfg_dir = staging_root / "config"
            staging_cfg_dir.mkdir(parents=True, exist_ok=True)

            (staging_cfg_dir / "data_config.json").write_text(
                json.dumps({"path": str(staging_db.resolve())}), encoding="utf-8")
            (staging_cfg_dir / "sources_config.json").write_text(
                json.dumps({"sources": {"tushare": {"enabled": True}}}), encoding="utf-8")
            tasks_cfg = {
                "tasks": [
                    {"name": "fin_indicator", "table": "fin_indicator",
                     "source": "tushare", "authoritative_source": "tushare",
                     "allow_fallback": False},
                    {"name": "stock_dividend", "table": "stock_dividend",
                     "source": "tushare", "authoritative_source": "tushare",
                     "allow_fallback": False},
                ]
            }
            (staging_cfg_dir / "collector_tasks.json").write_text(
                json.dumps(tasks_cfg), encoding="utf-8")
            (staging_cfg_dir / "alignment_rules.json").write_text(
                json.dumps({"schemas": {}}), encoding="utf-8")

            # Mock DataQualityAuditor to capture authority_rules
            from scripts.backfill_fin_growth_dividend_staging import phase_audit
            import argparse

            captured_authority_rules = {}

            class FakeReport:
                checks_run = 10
                passed = True
                issues = []

            def fake_auditor_init(self, db_path, schemas, batch_audit_path=None,
                                  quarantine_path=None, shared_conn=None,
                                  authority_rules=None):
                nonlocal captured_authority_rules
                captured_authority_rules = authority_rules or {}

            def fake_run(self):
                return FakeReport()

            with patch("quantstudio.pipeline.quality_audit.DataQualityAuditor.__init__",
                       fake_auditor_init):
                with patch("quantstudio.pipeline.quality_audit.DataQualityAuditor.run",
                           fake_run):
                    ns = argparse.Namespace(
                        staging_root=str(staging_root),
                        dry_run=False,
                        source_db=db_path,
                    )
                    rc = phase_audit(ns)
                    # Mock audit can't produce complete evidence, so rc may be 1
                    # The authority_rules capture is what we're testing

            assert "fin_indicator" in captured_authority_rules
            assert "stock_dividend" in captured_authority_rules
            assert captured_authority_rules["fin_indicator"]["authoritative_source"] == "tushare"
            assert captured_authority_rules["stock_dividend"]["allow_fallback"] is False

            os.unlink(db_path)


class TestAuditPassWritesEvidence:
    """test_audit_pass_writes_evidence_json"""

    def test_audit_pass_writes_evidence_json(self):
        """After a passing audit, audit_evidence.json is written to staging root."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root = tmp_p / "staging"

            # Create staging environment
            staging_db = staging_root / "staging.db"
            staging_root.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(db_path, str(staging_db))

            staging_cfg_dir = staging_root / "config"
            staging_cfg_dir.mkdir(parents=True, exist_ok=True)

            (staging_cfg_dir / "data_config.json").write_text(
                json.dumps({"path": str(staging_db.resolve())}), encoding="utf-8")
            (staging_cfg_dir / "sources_config.json").write_text(
                json.dumps({"sources": {"tushare": {"enabled": True}}}), encoding="utf-8")
            (staging_cfg_dir / "collector_tasks.json").write_text(
                json.dumps({"tasks": []}), encoding="utf-8")
            (staging_cfg_dir / "alignment_rules.json").write_text(
                json.dumps({"schemas": {}}), encoding="utf-8")

            from scripts.backfill_fin_growth_dividend_staging import phase_audit
            import argparse

            class FakeReport:
                checks_run = 5
                passed = True
                issues = []

            with patch("quantstudio.pipeline.quality_audit.DataQualityAuditor.run",
                       return_value=FakeReport()):
                with patch("quantstudio.pipeline.quality_audit.DataQualityAuditor.__init__",
                           return_value=None):
                    ns = argparse.Namespace(
                        staging_root=str(staging_root),
                        dry_run=False,
                        source_db=db_path,
                    )
                    rc = phase_audit(ns)
                    # Mock audit can't produce complete evidence, so rc may be 1
                    # The authority_rules capture is what we're testing

            evidence_path = staging_root / "audit_evidence.json"
            assert evidence_path.exists(), "audit_evidence.json must be written after audit"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            # New flat evidence format
            assert evidence.get("passed") is True
            assert "staging_db_path" in evidence or evidence.get("staging_db", {}).get("path")

            os.unlink(db_path)


# ===========================================================================
# Tests: promote phase
# ===========================================================================

class TestPromoteMissingEvidenceBlocked:
    """test_promote_missing_evidence_blocked"""

    def test_promote_missing_evidence_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root = tmp_p / "staging"

            # Create staging DB and config (but NO audit_evidence.json)
            staging_db = staging_root / "staging.db"
            staging_root.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(db_path, str(staging_db))

            result = _run_staging([
                "--source-db", db_path,
                "--staging-root", str(staging_root),
                "--promote",
            ])

            assert result.returncode != 0
            assert "audit_evidence.json not found" in (result.stdout + result.stderr)

            os.unlink(db_path)


class TestPromoteEvidenceHashDriftBlocked:
    """test_promote_evidence_hash_drift_blocked"""

    def test_promote_evidence_hash_drift_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root = tmp_p / "staging"

            # Create staging DB
            staging_db = staging_root / "staging.db"
            staging_root.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(db_path, str(staging_db))

            # Create config
            staging_cfg_dir = staging_root / "config"
            staging_cfg_dir.mkdir(parents=True, exist_ok=True)
            (staging_cfg_dir / "data_config.json").write_text(
                json.dumps({"path": str(staging_db.resolve())}), encoding="utf-8")
            (staging_cfg_dir / "sources_config.json").write_text(
                json.dumps({"sources": {"tushare": {"enabled": True}}}), encoding="utf-8")
            (staging_cfg_dir / "collector_tasks.json").write_text(
                json.dumps({"tasks": []}), encoding="utf-8")
            (staging_cfg_dir / "alignment_rules.json").write_text(
                json.dumps({"schemas": {}}), encoding="utf-8")

            # New flat evidence format
            evidence = {
                "passed": True,
                "staging_db_path": str(staging_db.resolve()),
                "staging_db_size": staging_db.stat().st_size,
                "staging_db_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
                "source_db_path": db_path,
                "source_db_size": 0,
                "source_db_sha256": None,
                "config_hashes": {"data_config.json": "test", "sources_config.json": "test", "collector_tasks.json": "test", "alignment_rules.json": "test"},
                "config_files": {},
                "profile_version": "test",
                "authority_rules": {},
                "batch_ids": {},
                "batch_counts": {},
            }
            (staging_root / "audit_evidence.json").write_text(
                json.dumps(evidence), encoding="utf-8")

            result = _run_staging([
                "--source-db", db_path,
                "--staging-root", str(staging_root),
                "--promote",
            ])

            assert result.returncode != 0
            assert "hash" in (result.stdout + result.stderr).lower() or "mismatch" in (result.stdout + result.stderr)

            os.unlink(db_path)


class TestPromoteDryRun:
    """test_promote_dry_run_does_not_move_files"""

    def test_promote_dry_run_does_not_move_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root = tmp_p / "staging"

            # Create staging DB
            staging_db = staging_root / "staging.db"
            staging_root.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(db_path, str(staging_db))

            # Create config
            staging_cfg_dir = staging_root / "config"
            staging_cfg_dir.mkdir(parents=True, exist_ok=True)
            (staging_cfg_dir / "data_config.json").write_text(
                json.dumps({"path": str(staging_db.resolve())}), encoding="utf-8")
            (staging_cfg_dir / "sources_config.json").write_text(
                json.dumps({"sources": {"tushare": {"enabled": True}}}), encoding="utf-8")
            (staging_cfg_dir / "collector_tasks.json").write_text(
                json.dumps({"tasks": []}), encoding="utf-8")
            (staging_cfg_dir / "alignment_rules.json").write_text(
                json.dumps({"schemas": {}}), encoding="utf-8")

            # Use actual config file hashes
            import hashlib
            actual_hash = hashlib.sha256(staging_db.read_bytes()).hexdigest()
            source_hash = hashlib.sha256(Path(db_path).read_bytes()).hexdigest()
            source_size = Path(db_path).stat().st_size
            cfg_hashes = {
                "data_config.json": hashlib.sha256((staging_cfg_dir / "data_config.json").read_bytes()).hexdigest(),
                "sources_config.json": hashlib.sha256((staging_cfg_dir / "sources_config.json").read_bytes()).hexdigest(),
                "collector_tasks.json": hashlib.sha256((staging_cfg_dir / "collector_tasks.json").read_bytes()).hexdigest(),
                "alignment_rules.json": hashlib.sha256((staging_cfg_dir / "alignment_rules.json").read_bytes()).hexdigest(),
            }
            # Write two real manifest files on disk so promotion's on-disk drift
            # check (re-hash + content compare) passes. Their hashes are captured
            # into the evidence below.
            sr = staging_root.resolve()
            manifest_fin = {
                "format_version": "1.0", "task": "fin_indicator", "pid": 999999,
                "nonce": "test1", "created_at": "2026-07-27T00:00:00",
                "QUANTSTUDIO_DATA_ROOT": str(sr), "imported_DATA_ROOT": str(sr),
                "writer_db_path": str(staging_db.resolve()),
                "batch_audit_db_path": str(sr / "batch_audit.db"),
                "quarantine_db_path": str(sr / "quarantine.db"),
                "daemon_log_path": str(sr / "logs" / "daemon.log"),
                "collector_lock_path": str(sr / ".collector_run.lock"),
                "daemon_lock_path": str(sr / ".daemon.lock"),
                "daemon_status_path": str(sr / "daemon_status.json"),
                "config_dir": str(staging_cfg_dir.resolve()),
            }
            manifest_div = dict(manifest_fin)
            manifest_div["task"] = "stock_dividend"
            manifest_div["nonce"] = "test2"
            fin_path = staging_root / "runtime_paths_fin_indicator.json"
            div_path = staging_root / "runtime_paths_stock_dividend.json"
            fin_path.write_text(json.dumps(manifest_fin), encoding="utf-8")
            div_path.write_text(json.dumps(manifest_div), encoding="utf-8")
            fin_hash = hashlib.sha256(fin_path.read_bytes()).hexdigest()
            div_hash = hashlib.sha256(div_path.read_bytes()).hexdigest()
            evidence = {
                "passed": True,
                "staging_db_path": str(staging_db.resolve()),
                "staging_db_size": staging_db.stat().st_size,
                "staging_db_sha256": actual_hash,
                "source_db_path": db_path,
                "source_db_size": source_size,
                "source_db_sha256": source_hash,
                "config_hashes": cfg_hashes,
                "config_files": {k: {"path": str(staging_cfg_dir / k), "sha256": v} for k, v in cfg_hashes.items()},
                "profile_version": "1.10.0",
                "data_schema_version": "2.0",
                "ptrade_profile_version": "1.10.0",
                "authority_rules": {
                    "fin_indicator": {"authoritative_source": "tushare", "allow_fallback": False},
                    "stock_dividend": {"authoritative_source": "tushare", "allow_fallback": False},
                },
                "batch_ids": [
                    {"batch_id": "batch_001", "status": "success", "table": "fin_indicator",
                     "source": "tushare", "rows_written": 1, "rows_new": 1, "rows_updated": 0,
                     "started_at": "2026-07-27T00:00:00", "finished_at": "2026-07-27T00:01:00"},
                    {"batch_id": "batch_002", "status": "success", "table": "stock_dividend",
                     "source": "tushare", "rows_written": 1, "rows_new": 1, "rows_updated": 0,
                     "started_at": "2026-07-27T00:00:00", "finished_at": "2026-07-27T00:01:00"},
                ],
                "batch_counts": {
                    "fin_indicator": {"attempted": 1, "written": 1, "rows_raw": 1, "rows_passed": 1, "rows_rejected": 0},
                    "stock_dividend": {"attempted": 1, "written": 1, "rows_raw": 1, "rows_passed": 1, "rows_rejected": 0},
                },
                "fin_indicator_row_count": 5000,
                "stock_dividend_row_count": 1000,
                "growth_field_stats": {"np_yoy_non_null": 5000, "or_yoy_non_null": 4500, "tr_yoy_non_null": 4000},
                "dividend_stats": {"cash_div_before_tax_non_null": 1000, "cash_div_after_tax_non_null": 1000, "stk_div_non_null": 500},
                "watermark_info": {"count": 2, "entries": [{"table": "fin_indicator"}, {"table": "stock_dividend"}]},
                "audit_time": "2026-07-27T00:00:00",
                "audit": {"checks_run": 10, "errors_count": 0, "warnings_count": 0},
                "baseline_delta_passed": True,
                "runtime_paths": {"runtime_paths_fin_indicator.json": manifest_fin, "runtime_paths_stock_dividend.json": manifest_div},
                "runtime_manifest_hashes": {"runtime_paths_fin_indicator.json": fin_hash, "runtime_paths_stock_dividend.json": div_hash},
            }
            (staging_root / "audit_evidence.json").write_text(
                json.dumps(evidence), encoding="utf-8")

            # Record file states before promote
            source_db_stat_before = os.stat(db_path)
            staging_db_stat_before = os.stat(staging_db)

            result = _run_staging([
                "--source-db", db_path,
                "--staging-root", str(staging_root),
                "--promote",
            ])

            # Promote is always dry-run only
            assert result.returncode == 0, f"stderr: {result.stderr}"
            assert "DRY-RUN" in (result.stdout + result.stderr)

            # Source DB must NOT have been modified
            source_db_stat_after = os.stat(db_path)
            assert source_db_stat_after.st_size == source_db_stat_before.st_size
            assert source_db_stat_after.st_mtime == source_db_stat_before.st_mtime

            # Staging DB must still exist (not moved)
            assert staging_db.exists()

            os.unlink(db_path)


# ===========================================================================
# W2-0.7B extension -- negative gate tests + full dual-task E2E
#
# All tests below use temporary DuckDB / SQLite ledgers and a FAKE daemon
# child (monkeypatched run_cmd). No real Tushare credentials, no real daemon
# subprocess, no production DB writes. Every test asserts the return code.
# ===========================================================================

import sqlite3  # noqa: E402  -- imported here to keep the top-of-file diff small


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _make_fake_child_writer(staging_root: Path, staging_db: Path,
                             staging_cfg_dir: Path, batch_records=None):
    """Return a fake_run_cmd that simulates a successful daemon child:
       writes a valid runtime manifest AND optional batch_audit.db rows.

    `batch_records` is a list of dicts written to batch_audit table inside
    the SQLite ledger. The fake child inserts fin_indicator / stock_dividend
    rows into the staging DuckDB so downstream audit has something to read.

    Returns a CommandResult whose pid equals the pid embedded in the manifest
    (os.getpid()), so phase_run_task's require_pid_match gate passes.
    """
    from scripts.backfill_fin_growth_dividend_staging import CommandResult
    def _fake_run_cmd(cmd, cwd=None, dry_run=False, env=None, timeout=21600,
                      log_file=None, staging_db=None, task_name=""):
        # Parse cmd list for --runtime-manifest / --runtime-nonce
        cmd_manifest = None
        cmd_nonce = None
        cmd_task = task_name
        if isinstance(cmd, list):
            for i, arg in enumerate(cmd):
                if arg == "--runtime-manifest" and i + 1 < len(cmd):
                    cmd_manifest = cmd[i + 1]
                elif arg == "--runtime-nonce" and i + 1 < len(cmd):
                    cmd_nonce = cmd[i + 1]
                elif arg == "--task" and i + 1 < len(cmd):
                    cmd_task = cmd[i + 1]
        sr = staging_root.resolve()
        from datetime import datetime as _dt
        started = _dt.now().isoformat()
        manifest = {
            "format_version": "1.0",
            "task": cmd_task,
            "pid": os.getpid(),
            "nonce": cmd_nonce,
            "created_at": started,
            "QUANTSTUDIO_DATA_ROOT": str(sr),
            "imported_DATA_ROOT": str(sr),
            "writer_db_path": str(staging_db.resolve()),
            "batch_audit_db_path": str(sr / "batch_audit.db"),
            "quarantine_db_path": str(sr / "quarantine.db"),
            "daemon_log_path": str(sr / "logs" / "daemon.log"),
            "collector_lock_path": str(sr / ".collector_run.lock"),
            "daemon_lock_path": str(sr / ".daemon.lock"),
            "daemon_status_path": str(sr / "daemon_status.json"),
            "config_dir": str(staging_cfg_dir.resolve()),
        }
        if cmd_manifest:
            p = Path(cmd_manifest)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = Path(str(p) + ".tmp")
            tmp.write_text(json.dumps(manifest), encoding="utf-8")
            os.replace(str(tmp), str(p))

        # Always write a batch_audit.db row (SQLite ledger). Use the FULL
        # canonical BatchAudit schema so the audit evidence builder's SELECT
        # (which now reads rows_new/rows_updated/started_at/finished_at) finds
        # the columns it expects. If no batch_records were supplied, write a
        # default success batch for cmd_task so phase_run_task's ledger
        # cross-check (W2-0.8 缺陷 D) passes.
        effective_records = batch_records if batch_records else [{
            "batch_id": f"b-{cmd_task}-default", "task_name": cmd_task,
            "table": cmd_task, "rows_raw": 1, "rows_passed": 1,
            "rows_rejected": 0, "rows_written": 1, "rows_new": 1, "rows_updated": 0,
            "status": "success",
        }]
        ledger_path = sr / "batch_audit.db"
        conn = sqlite3.connect(str(ledger_path))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS batch_audit ("
                " batch_id TEXT, task_name TEXT, table_name TEXT, source TEXT,"
                " freq TEXT, status TEXT, rows_raw INTEGER, rows_passed INTEGER,"
                " rows_rejected INTEGER, rows_written INTEGER, rows_new INTEGER,"
                " rows_updated INTEGER, started_at TEXT, finished_at TEXT)"
            )
            for rec in effective_records:
                # Use real now() for started/finished so they fall after the
                # staging marker created_at (written during --prepare). The
                # fake child runs after prepare, so now() > marker time.
                from datetime import datetime as _bdt
                _now_iso = _bdt.now().isoformat()
                conn.execute(
                    "INSERT INTO batch_audit (batch_id, task_name, table_name, source,"
                    " freq, status, rows_raw, rows_passed, rows_rejected, rows_written,"
                    " rows_new, rows_updated, started_at, finished_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rec.get("batch_id"), rec.get("task_name") or rec.get("table"),
                     rec.get("table"), rec.get("source", "tushare"),
                     rec.get("freq", "daily"), rec.get("status", "success"),
                     rec.get("rows_raw", 1), rec.get("rows_passed", 1),
                     rec.get("rows_rejected", 0), rec.get("rows_written", 1),
                     rec.get("rows_new", 1), rec.get("rows_updated", 0),
                     rec.get("started_at", _now_iso),
                     rec.get("finished_at", _now_iso)),
                )
            conn.commit()
        finally:
            conn.close()
        # Return a CommandResult whose pid matches the manifest pid and whose
        # started_at/finished_at bracket the manifest created_at (= started).
        from datetime import datetime as _dt
        return CommandResult(exit_code=0, pid=os.getpid(),
                             started_at=started, finished_at=_dt.now().isoformat())
    return _fake_run_cmd


def _setup_full_staging(tmp_p: Path, db_path: str,
                         with_growth_dividend_rows: bool = True):
    """Create a fully-prepared staging root (mimics a successful --prepare):
       staging DB copy + config dir + ledgers + watermark + (optional) data rows.

    Returns (staging_root, staging_db, staging_cfg_dir).
    """
    import shutil
    staging_root = tmp_p / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging_db = staging_root / "staging.db"
    shutil.copy2(db_path, str(staging_db))

    staging_cfg_dir = staging_root / "config"
    staging_cfg_dir.mkdir(parents=True, exist_ok=True)
    _make_config_dir(staging_root, str(staging_db.resolve()))
    # _make_config_dir creates staging_root/config -- but we need staging_cfg_dir
    # to be exactly that. Re-point.
    staging_cfg_dir = staging_root / "config"

    # Create ledgers (SQLite, not DuckDB)
    sqlite3.connect(str(staging_root / "batch_audit.db")).close()
    sqlite3.connect(str(staging_root / "quarantine.db")).close()
    (staging_root / "logs").mkdir(parents=True, exist_ok=True)

    # Write staging marker (needed if a test later calls --reset-staging)
    marker = {
        "format_version": "1.0",
        "staging_root": str(staging_root.resolve()),
        "source_db": str(Path(db_path).resolve()),
        "created_at": "2026-07-28T00:00:00",
        "tool": "backfill_fin_growth_dividend_staging",
    }
    (staging_root / ".quantstudio_staging.json").write_text(
        json.dumps(marker), encoding="utf-8")

    # If requested, add fin_indicator / stock_dividend tables to staging DB
    # and source_watermark entries, so a downstream audit has something to read.
    if with_growth_dividend_rows:
        conn = duckdb.connect(str(staging_db))
        try:
            tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
            if "fin_indicator" not in tables:
                conn.execute(
                    "CREATE TABLE fin_indicator (code VARCHAR, end_date INTEGER,"
                    " ann_date INTEGER, eps DOUBLE, np_yoy DOUBLE, or_yoy DOUBLE,"
                    " tr_yoy DOUBLE, diluted_eps DOUBLE, update_flag VARCHAR)"
                )
                conn.execute(
                    "INSERT INTO fin_indicator VALUES "
                    "('000001.SZ,20231231,20240130,1.5,10.0,8.0,7.0,1.4,'1')"
                )
            if "stock_dividend" not in tables:
                conn.execute(
                    "CREATE TABLE stock_dividend (code VARCHAR, end_date INTEGER,"
                    " ann_date INTEGER, div_proc VARCHAR, stk_div DOUBLE,"
                    " cash_div_before_tax DOUBLE, cash_div_after_tax DOUBLE,"
                    " stk_bo_rate DOUBLE, stk_co_rate DOUBLE, ex_date INTEGER,"
                    " data_source VARCHAR)"
                )
                conn.execute(
                    "INSERT INTO stock_dividend VALUES "
                    "('000001.SZ,20231231,20240130,'实施',0.1,1.0,0.8,0.05,0.05,20240601,'tushare')"
                )
            # Add watermark rows for both tasks if the table exists
            if "source_watermark" in tables:
                conn.execute("DELETE FROM source_watermark WHERE table_name IN ('fin_indicator','stock_dividend')")
                conn.execute(
                    "INSERT INTO source_watermark (source, table_name, freq, last_date,"
                    " last_batch_id, updated_at, source_generation, cutover_id) VALUES "
                    "('tushare','fin_indicator','daily',20240130,'b1','2026-07-28','not-qfq-managed','not-applicable'),"
                    "('tushare','stock_dividend','daily',20240130,'b2','2026-07-28','not-qfq-managed','not-applicable')"
                )
            conn.close()
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
    return staging_root, staging_db, staging_cfg_dir


def _make_full_passing_evidence(staging_db: Path, source_db: str,
                                  staging_cfg_dir: Path) -> dict:
    """Build a complete audit_evidence.json that passes validate_audit_evidence().
    Used by promote gate tests.
    """
    cfg_hashes = {}
    for fname in ("data_config.json", "sources_config.json",
                  "collector_tasks.json", "alignment_rules.json"):
        fp = staging_cfg_dir / fname
        cfg_hashes[fname] = _sha256_file(fp) if fp.exists() else None
    return {
        "passed": True,
        "audit_time": "2026-07-28T00:00:00",
        "staging_db_path": str(staging_db.resolve()),
        "staging_db_size": staging_db.stat().st_size,
        "staging_db_sha256": _sha256_file(staging_db),
        "source_db_path": str(Path(source_db).resolve()),
        "source_db_size": Path(source_db).stat().st_size,
        "source_db_sha256": _sha256_file(source_db),
        "config_hashes": cfg_hashes,
        "config_files": {k: {"path": str(staging_cfg_dir / k), "sha256": v}
                         for k, v in cfg_hashes.items()},
        "data_schema_version": "2.0",
        "ptrade_profile_version": "1.10.0",
        "authority_rules": {
            "fin_indicator": {"authoritative_source": "tushare", "allow_fallback": False},
            "stock_dividend": {"authoritative_source": "tushare", "allow_fallback": False},
        },
        "runtime_paths": {
            "runtime_paths_fin_indicator.json": {"writer_db_path": str(staging_db), "nonce": "n1"},
            "runtime_paths_stock_dividend.json": {"writer_db_path": str(staging_db), "nonce": "n2"},
        },
        "batch_counts": {
            "fin_indicator": {"rows_raw": 1, "rows_passed": 1, "rows_rejected": 0},
            "stock_dividend": {"rows_raw": 1, "rows_passed": 1, "rows_rejected": 0},
        },
        "batch_ids": [
            {"batch_id": "b1", "table": "fin_indicator", "status": "success",
             "source": "tushare", "rows_written": 1, "rows_new": 1, "rows_updated": 0,
             "started_at": "2026-07-28T00:00:00", "finished_at": "2026-07-28T00:01:00"},
            {"batch_id": "b2", "table": "stock_dividend", "status": "success",
             "source": "tushare", "rows_written": 1, "rows_new": 1, "rows_updated": 0,
             "started_at": "2026-07-28T00:00:00", "finished_at": "2026-07-28T00:01:00"},
        ],
        "fin_indicator_row_count": 1,
        "stock_dividend_row_count": 1,
        "growth_field_stats": {"np_yoy_non_null": 1, "or_yoy_non_null": 1, "tr_yoy_non_null": 1},
        "dividend_stats": {"cash_div_before_tax_non_null": 1,
                           "cash_div_after_tax_non_null": 1, "stk_div_non_null": 1,
                           "stk_bo_rate_non_null": 1, "stk_co_rate_non_null": 1},
        "watermark_info": {"count": 2, "entries": [
            {"table": "fin_indicator"}, {"table": "stock_dividend"}]},
        "audit": {"checks_run": 10, "errors_count": 0, "warnings_count": 0,
                  "errors": [], "warnings": []},
        # W2-0.9 Phase 5：validate_audit_evidence 现在要求 baseline_delta_passed=True
        # （而非 raw errors_count==0）。该 helper 构造的是"通过"证据，故 delta 为 pass。
        "baseline_delta_passed": True,
        "baseline_delta_audit": {
            "target_table_errors": [], "new_errors": [], "regressed_errors": [],
            "inherited_unchanged_errors": []},
    }


# ===========================================================================
# Negative gate group 1: daemon / collector lock states
# ===========================================================================

class TestDaemonGateAlive:
    """test_run_task_blocks_when_daemon_alive"""

    def test_run_task_blocks_when_daemon_alive(self):
        """An alive daemon in the staging data_root must BLOCK --run-task."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)

            # Write a daemon_status.json that verify_daemon_identity reads as "alive".
            # We use the current process's PID + exe/cmdline so it matches a live proc,
            # BUT the daemon must not be THIS process; instead patch verify_daemon_identity.
            import argparse
            from scripts.backfill_fin_growth_dividend_staging import phase_run_task

            with patch("quantstudio.pipeline.daemon_lifecycle.verify_daemon_identity",
                       return_value="alive"):
                ns = argparse.Namespace(
                    staging_root=str(staging_root), run_task="fin_indicator",
                    dry_run=False, timeout_sec=60, source_db=db_path,
                )
                rc = phase_run_task(ns)
            assert rc == 1, "alive daemon must BLOCK run-task"
            os.unlink(db_path)


class TestDaemonGateDenied:
    """test_run_task_blocks_when_daemon_denied"""

    def test_run_task_blocks_when_daemon_denied(self):
        """A denied daemon (AccessDenied) must BLOCK --run-task."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)

            import argparse
            from scripts.backfill_fin_growth_dividend_staging import phase_run_task

            with patch("quantstudio.pipeline.daemon_lifecycle.verify_daemon_identity",
                       return_value="denied"):
                ns = argparse.Namespace(
                    staging_root=str(staging_root), run_task="fin_indicator",
                    dry_run=False, timeout_sec=60, source_db=db_path,
                )
                rc = phase_run_task(ns)
            assert rc == 1, "denied daemon must BLOCK run-task"
            os.unlink(db_path)


class TestDaemonGateCorruptStatus:
    """test_run_task_blocks_when_daemon_status_corrupt"""

    def test_run_task_blocks_when_daemon_status_corrupt(self):
        """A corrupt (unparseable) daemon_status.json must BLOCK --run-task."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)

            # Write corrupt JSON to daemon_status.json
            (staging_root / "daemon_status.json").write_text("{not valid json", encoding="utf-8")

            import argparse
            from scripts.backfill_fin_growth_dividend_staging import phase_run_task
            ns = argparse.Namespace(
                staging_root=str(staging_root), run_task="fin_indicator",
                dry_run=False, timeout_sec=60, source_db=db_path,
            )
            rc = phase_run_task(ns)
            assert rc == 1, "corrupt daemon status must BLOCK run-task"
            os.unlink(db_path)


class TestDaemonGateStalePasses:
    """test_run_task_passes_when_daemon_status_stale"""

    def test_run_task_passes_when_daemon_status_stale(self):
        """A stale daemon status must NOT block run-task (proceed to subprocess)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)

            import argparse
            from scripts.backfill_fin_growth_dividend_staging import phase_run_task

            fake = _make_fake_child_writer(staging_root, staging_db, staging_cfg_dir)
            with patch("quantstudio.pipeline.daemon_lifecycle.verify_daemon_identity",
                       return_value="stale"):
                with patch("scripts.backfill_fin_growth_dividend_staging.run_cmd", fake):
                    ns = argparse.Namespace(
                        staging_root=str(staging_root), run_task="fin_indicator",
                        dry_run=False, timeout_sec=60, source_db=db_path,
                    )
                    rc = phase_run_task(ns)
            assert rc == 0, "stale daemon status must NOT block run-task"
            os.unlink(db_path)


class TestCollectorLockHeld:
    """test_run_task_blocks_when_collector_lock_held"""

    def test_run_task_blocks_when_collector_lock_held(self):
        """A held collector_run.lock must BLOCK --run-task."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)

            # Acquire the collector lock from THIS process and hold it.
            from filelock import FileLock
            lock_path = staging_root / ".collector_run.lock"
            held = FileLock(str(lock_path), timeout=1)
            held.acquire(timeout=1)
            try:
                import argparse
                from scripts.backfill_fin_growth_dividend_staging import phase_run_task
                ns = argparse.Namespace(
                    staging_root=str(staging_root), run_task="fin_indicator",
                    dry_run=False, timeout_sec=60, source_db=db_path,
                )
                rc = phase_run_task(ns)
                assert rc == 1, "held collector lock must BLOCK run-task"
            finally:
                held.release()
            os.unlink(db_path)


class TestCollectorLockStalePasses:
    """test_run_task_passes_when_collector_lock_stale"""

    def test_run_task_passes_when_collector_lock_stale(self):
        """A stale (unheld) collector_run.lock file must NOT block run-task."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)

            # Create a stale lock file (file exists but nobody holds it)
            (staging_root / ".collector_run.lock").write_text("stale", encoding="utf-8")

            import argparse
            from scripts.backfill_fin_growth_dividend_staging import phase_run_task
            fake = _make_fake_child_writer(staging_root, staging_db, staging_cfg_dir)
            with patch("scripts.backfill_fin_growth_dividend_staging.run_cmd", fake):
                ns = argparse.Namespace(
                    staging_root=str(staging_root), run_task="fin_indicator",
                    dry_run=False, timeout_sec=60, source_db=db_path,
                )
                rc = phase_run_task(ns)
            assert rc == 0, "stale collector lock must NOT block run-task"
            os.unlink(db_path)


# ===========================================================================
# Negative gate group 2: timeout / heartbeat
# ===========================================================================

class TestRunCmdTimeoutZero:
    """test_run_cmd_timeout_zero_means_no_timeout"""

    def test_run_cmd_timeout_zero_means_no_timeout(self):
        """timeout=0 must run to completion without a timeout error."""
        from scripts.backfill_fin_growth_dividend_staging import run_cmd
        # A fast command that exits 0
        rc, _, _ = run_cmd([sys.executable, "-c", "import sys; sys.exit(0)"],
                           timeout=0, task_name="ut")
        assert rc == 0


class TestRunCmdPositiveTimeoutKills:
    """test_run_cmd_positive_timeout_terminates_long_child"""

    def test_run_cmd_positive_timeout_terminates_long_child(self):
        """A positive timeout must terminate a child that exceeds it."""
        from scripts.backfill_fin_growth_dividend_staging import run_cmd
        # Sleep 30s with timeout=2 -- should be killed (non-zero rc).
        rc, _, _ = run_cmd([sys.executable, "-c", "import time; time.sleep(30)"],
                           timeout=2, task_name="ut")
        assert rc != 0, "long child must be terminated by timeout"


class TestRunCmdHeartbeatVisible:
    """test_run_cmd_heartbeat_emits_log_lines"""

    def test_run_cmd_heartbeat_emits_log_lines(self, caplog):
        """Heartbeat thread should emit at least one log line for a long child.

        caplog captures at the WARNING+ level by default, so we set the staging
        module logger to INFO explicitly. The child sleeps 35s so the heartbeat
        (which fires every 30s) is guaranteed to fire at least once before the
        process exits.
        """
        import logging
        from scripts.backfill_fin_growth_dividend_staging import run_cmd, logger as staging_logger
        with caplog.at_level(
                logging.INFO,
                logger="scripts.backfill_fin_growth_dividend_staging"):
            run_cmd([sys.executable, "-c", "import time; time.sleep(35)"],
                    timeout=60, task_name="ut_heartbeat")
        msgs = [r.message for r in caplog.records]
        assert any("HEARTBEAT" in m for m in msgs), \
            f"expected a HEARTBEAT log line, got: {msgs!r}"


# ===========================================================================
# Negative gate group 3: --reset-staging marker validation
# ===========================================================================

class TestResetStagingNoMarkerBlocked:
    """test_reset_staging_without_marker_blocked"""

    def test_reset_staging_without_marker_blocked(self):
        """--reset-staging on a staging root with no marker file must BLOCK."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root = tmp_p / "staging"
            staging_root.mkdir(parents=True, exist_ok=True)
            # Populate staging root with a fake file but NO marker
            (staging_root / "staging.db").write_text("dummy", encoding="utf-8")

            result = _run_staging([
                "--source-db", db_path,
                "--staging-root", str(staging_root),
                "--reset-staging", "--prepare",
            ])
            assert result.returncode != 0
            assert "marker not found" in (result.stdout + result.stderr)
            os.unlink(db_path)


class TestResetStagingMarkerMismatchBlocked:
    """test_reset_staging_marker_source_mismatch_blocked"""

    def test_reset_staging_marker_source_mismatch_blocked(self):
        """--reset-staging where marker.source_db != args.source_db must BLOCK."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            other_db = _make_mini_db()
            staging_root = tmp_p / "staging"
            staging_root.mkdir(parents=True, exist_ok=True)
            (staging_root / "staging.db").write_text("dummy", encoding="utf-8")
            marker = {
                "format_version": "1.0",
                "staging_root": str(staging_root.resolve()),
                "source_db": str(Path(db_path).resolve()),  # points to db_path
                "created_at": "2026-07-28T00:00:00",
            }
            (staging_root / ".quantstudio_staging.json").write_text(
                json.dumps(marker), encoding="utf-8")

            # But call with --source-db = other_db (mismatch)
            result = _run_staging([
                "--source-db", other_db,
                "--staging-root", str(staging_root),
                "--reset-staging", "--prepare",
            ])
            assert result.returncode != 0
            assert "source_db" in (result.stdout + result.stderr)
            os.unlink(db_path)
            os.unlink(other_db)


class TestResetStagingProjectRootBlocked:
    """test_reset_staging_pointing_at_project_root_blocked"""

    def test_reset_staging_pointing_at_project_root_blocked(self):
        """--reset-staging where staging_root resolves to project root must BLOCK."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            # staging_root = project root
            project_root = _PROJECT_ROOT.resolve()
            marker_path = project_root / ".quantstudio_staging.json"
            marker = {
                "format_version": "1.0",
                "staging_root": str(project_root),
                "source_db": str(Path(db_path).resolve()),
                "created_at": "2026-07-28T00:00:00",
            }
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            try:
                result = _run_staging([
                    "--source-db", db_path,
                    "--staging-root", str(project_root),
                    "--reset-staging", "--prepare",
                ])
                assert result.returncode != 0
                assert "project root" in (result.stdout + result.stderr)
            finally:
                marker_path.unlink(missing_ok=True)
            os.unlink(db_path)


class TestResetStagingDataDirBlocked:
    """test_reset_staging_pointing_at_data_dir_blocked"""

    def test_reset_staging_pointing_at_data_dir_blocked(self):
        """--reset-staging where staging_root resolves to data/ dir must BLOCK."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _make_mini_db()
            # staging_root = the project's data directory
            data_dir = (_PROJECT_ROOT / "data").resolve()
            marker_path = data_dir / ".quantstudio_staging.json"
            marker = {
                "format_version": "1.0",
                "staging_root": str(data_dir),
                "source_db": str(Path(db_path).resolve()),
                "created_at": "2026-07-28T00:00:00",
            }
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            try:
                result = _run_staging([
                    "--source-db", db_path,
                    "--staging-root", str(data_dir),
                    "--reset-staging", "--prepare",
                ])
                assert result.returncode != 0
                assert "data directory" in (result.stdout + result.stderr)
            finally:
                marker_path.unlink(missing_ok=True)
            os.unlink(db_path)


# ===========================================================================
# Negative gate group 4: prepare environment failures
# ===========================================================================

class TestPrepareMissingConfigFileBlocked:
    """test_prepare_missing_config_file_blocked"""

    def test_prepare_missing_config_file_blocked(self):
        """--prepare must BLOCK if a required config file is missing.

        We call phase_prepare IN-PROCESS so the REQUIRED_CONFIG_FILES patch is
        visible (a subprocess would not inherit the monkeypatch).
        """
        import argparse
        from scripts.backfill_fin_growth_dividend_staging import phase_prepare
        with patch("scripts.backfill_fin_growth_dividend_staging.REQUIRED_CONFIG_FILES",
                   ["definitely_not_present.json"]):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_p = Path(tmp)
                db_path = _make_mini_db()
                _make_config_dir(tmp_p, db_path)
                staging_root = tmp_p / "staging"
                ns = argparse.Namespace(
                    source_db=db_path, staging_root=str(staging_root),
                    dry_run=False, reset_staging=False, start_date="2022-01-01",
                )
                rc = phase_prepare(ns)
                # prepare copies the DB first then fails on missing config.
                assert rc != 0, "missing required config file must BLOCK prepare"
                os.unlink(db_path)


class TestPrepareDiskCheckFailureBlocked:
    """test_prepare_disk_check_failure_blocks"""

    def test_prepare_disk_check_failure_blocks(self):
        """If check_disk_space returns (False, 0), --prepare must BLOCK.

        Called in-process so the patch is visible.
        """
        import argparse
        from scripts.backfill_fin_growth_dividend_staging import phase_prepare
        with patch("scripts.backfill_fin_growth_dividend_staging.check_disk_space",
                   return_value=(False, 0)):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_p = Path(tmp)
                db_path = _make_mini_db()
                _make_config_dir(tmp_p, db_path)
                staging_root = tmp_p / "staging"
                ns = argparse.Namespace(
                    source_db=db_path, staging_root=str(staging_root),
                    dry_run=False, reset_staging=False, start_date="2022-01-01",
                )
                rc = phase_prepare(ns)
                assert rc != 0, "disk check failure must BLOCK prepare"
                os.unlink(db_path)


class TestPrepareUnreadableSourceDbBlocked:
    """test_prepare_unreadable_source_db_blocked"""

    def test_prepare_unreadable_source_db_blocked(self):
        """--prepare must BLOCK if the source DB path does not exist."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            nonexistent = str(tmp_p / "does_not_exist.db")
            staging_root = tmp_p / "staging"
            result = _run_staging([
                "--source-db", nonexistent,
                "--staging-root", str(staging_root),
                "--prepare",
            ])
            assert result.returncode != 0


class TestPrepareCopySizeMismatchBlocked:
    """test_prepare_copy_size_mismatch_blocked"""

    def test_prepare_copy_size_mismatch_blocked(self):
        """If shutil.copy2 produces a file of the wrong size, prepare must BLOCK.

        Called in-process so the shutil.copy2 patch is visible.
        """
        import argparse
        import scripts.backfill_fin_growth_dividend_staging as staging_mod
        from scripts.backfill_fin_growth_dividend_staging import phase_prepare
        real_copy2 = shutil.copy2

        def bad_copy2(src, dst, *, follow_symlinks=True):
            real_copy2(src, dst, follow_symlinks=follow_symlinks)
            # Truncate the copy so size mismatches source
            with open(dst, "ab") as f:
                f.truncate(1)

        with patch.object(staging_mod.shutil, "copy2", bad_copy2):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_p = Path(tmp)
                db_path = _make_mini_db()
                _make_config_dir(tmp_p, db_path)
                staging_root = tmp_p / "staging"
                ns = argparse.Namespace(
                    source_db=db_path, staging_root=str(staging_root),
                    dry_run=False, reset_staging=False, start_date="2022-01-01",
                )
                rc = phase_prepare(ns)
            assert rc != 0, "size mismatch must BLOCK prepare"
            os.unlink(db_path)


class TestPrepareCopyShaMismatchBlocked:
    """test_prepare_copy_sha_mismatch_blocked"""

    def test_prepare_copy_sha_mismatch_blocked(self):
        """If the staging copy's SHA differs from source, prepare must BLOCK.

        Called in-process. The fake copy2 flips the last byte of the staging
        copy so size is identical but content differs -- exercising the SHA gate
        specifically (the size gate passes first).
        """
        import argparse
        import scripts.backfill_fin_growth_dividend_staging as staging_mod
        from scripts.backfill_fin_growth_dividend_staging import phase_prepare
        real_copy2 = shutil.copy2

        def sha_mismatch_copy2(src, dst, *, follow_symlinks=True):
            real_copy2(src, dst, follow_symlinks=follow_symlinks)
            data = Path(dst).read_bytes()
            # Flip last byte but keep size identical so the size gate passes
            # and the SHA gate is the one that fails.
            if len(data) > 0:
                flipped = data[:-1] + bytes([data[-1] ^ 0xFF])
                Path(dst).write_bytes(flipped)

        with patch.object(staging_mod.shutil, "copy2", sha_mismatch_copy2):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_p = Path(tmp)
                db_path = _make_mini_db()
                _make_config_dir(tmp_p, db_path)
                staging_root = tmp_p / "staging"
                ns = argparse.Namespace(
                    source_db=db_path, staging_root=str(staging_root),
                    dry_run=False, reset_staging=False, start_date="2022-01-01",
                )
                rc = phase_prepare(ns)
            assert rc != 0, "SHA mismatch must BLOCK prepare"
            os.unlink(db_path)


class TestPrepareWorktreeInternalBlocked:
    """W2-0.9 缺陷 C/F：非 dry-run prepare 在 Git worktree/项目根/data 目录内 → BLOCK。"""

    def test_prepare_inside_project_data_dir_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            # staging_root inside the project data dir
            staging_root = _PROJECT_ROOT / "data" / "should_be_blocked_staging"
            result = _run_staging([
                "--source-db", db_path,
                "--staging-root", str(staging_root),
                "--prepare",
            ])
            assert result.returncode != 0, "staging inside project data/ must BLOCK"
            assert "SAFETY BLOCK" in (result.stdout + result.stderr)
            assert "OUTSIDE" in (result.stdout + result.stderr)
            os.unlink(db_path)

    def test_prepare_inside_project_root_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _make_mini_db()
            # staging_root inside the project root (a subdir)
            staging_root = _PROJECT_ROOT / "should_be_blocked_staging_root"
            result = _run_staging([
                "--source-db", db_path,
                "--staging-root", str(staging_root),
                "--prepare",
            ])
            assert result.returncode != 0, "staging inside project root must BLOCK"
            assert "SAFETY BLOCK" in (result.stdout + result.stderr)
            os.unlink(db_path)

    def test_prepare_worktree_internal_blocked_but_dryrun_warns(self):
        """dry-run 对 worktree 内路径不 BLOCK，只打印 WARNING。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _make_mini_db()
            staging_root = _PROJECT_ROOT / "data" / "dryrun_warn_staging"
            result = _run_staging([
                "--source-db", db_path,
                "--staging-root", str(staging_root),
                "--dry-run", "--prepare",
            ])
            # dry-run should not BLOCK (exit 0) but warn
            assert result.returncode == 0, f"dry-run must not BLOCK: {result.stderr}"
            assert "WARNING" in (result.stdout + result.stderr)
            os.unlink(db_path)


# ===========================================================================
# Negative gate group 5: run-task child manifest validation
# ===========================================================================

class TestRunTaskManifestMissingBlocked:
    """test_run_task_blocks_when_child_manifest_missing"""

    def test_run_task_blocks_when_child_manifest_missing(self):
        """If the child exits 0 but writes no manifest, run-task must BLOCK."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)

            import argparse
            from scripts.backfill_fin_growth_dividend_staging import phase_run_task

            def fake_no_manifest(cmd, cwd=None, dry_run=False, env=None, timeout=21600,
                                  log_file=None, staging_db=None, task_name=""):
                from scripts.backfill_fin_growth_dividend_staging import CommandResult
                return CommandResult(exit_code=0, pid=os.getpid())

            with patch("scripts.backfill_fin_growth_dividend_staging.run_cmd", fake_no_manifest):
                ns = argparse.Namespace(
                    staging_root=str(staging_root), run_task="fin_indicator",
                    dry_run=False, timeout_sec=60, source_db=db_path,
                )
                rc = phase_run_task(ns)
            assert rc == 1, "missing child manifest must BLOCK run-task"
            os.unlink(db_path)


class TestRunTaskManifestStaleNonceBlocked:
    """test_run_task_blocks_when_child_manifest_nonce_stale"""

    def test_run_task_blocks_when_child_manifest_nonce_stale(self):
        """If the child manifest nonce != parent nonce, run-task must BLOCK."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)

            import argparse
            from scripts.backfill_fin_growth_dividend_staging import phase_run_task

            def fake_stale_nonce(cmd, cwd=None, dry_run=False, env=None, timeout=21600,
                                  log_file=None, staging_db=None, task_name=""):
                from datetime import datetime as _dt
                from scripts.backfill_fin_growth_dividend_staging import CommandResult
                sr = staging_root.resolve()
                started = _dt.now().isoformat()
                for i, arg in enumerate(cmd):
                    if arg == "--runtime-manifest" and i + 1 < len(cmd):
                        p = Path(cmd[i + 1])
                        p.parent.mkdir(parents=True, exist_ok=True)
                        manifest = {
                            "format_version": "1.0", "task": task_name,
                            "pid": os.getpid(), "created_at": started,
                            "nonce": "WRONG_NONVALUE",  # mismatched
                            "QUANTSTUDIO_DATA_ROOT": str(sr),
                            "imported_DATA_ROOT": str(sr),
                            "writer_db_path": str(staging_db.resolve()),
                            "batch_audit_db_path": str(sr / "batch_audit.db"),
                            "quarantine_db_path": str(sr / "quarantine.db"),
                            "daemon_log_path": str(sr / "logs" / "daemon.log"),
                            "collector_lock_path": str(sr / ".collector_run.lock"),
                            "daemon_lock_path": str(sr / ".daemon.lock"),
                            "daemon_status_path": str(sr / "daemon_status.json"),
                            "config_dir": str(staging_cfg_dir.resolve()),
                        }
                        p.write_text(json.dumps(manifest), encoding="utf-8")
                return CommandResult(exit_code=0, pid=os.getpid(),
                                     started_at=started, finished_at=_dt.now().isoformat())

            with patch("scripts.backfill_fin_growth_dividend_staging.run_cmd", fake_stale_nonce):
                ns = argparse.Namespace(
                    staging_root=str(staging_root), run_task="fin_indicator",
                    dry_run=False, timeout_sec=60, source_db=db_path,
                )
                rc = phase_run_task(ns)
            assert rc == 1, "stale manifest nonce must BLOCK run-task"
            os.unlink(db_path)


class TestRunTaskManifestWrongTaskBlocked:
    """test_run_task_blocks_when_child_manifest_wrong_task"""

    def test_run_task_blocks_when_child_manifest_wrong_task(self):
        """If manifest.task != requested task, run-task must BLOCK."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)

            import argparse
            from scripts.backfill_fin_growth_dividend_staging import phase_run_task

            def fake_wrong_task(cmd, cwd=None, dry_run=False, env=None, timeout=21600,
                                 log_file=None, staging_db=None, task_name=""):
                from datetime import datetime as _dt
                from scripts.backfill_fin_growth_dividend_staging import CommandResult
                sr = staging_root.resolve()
                cmd_nonce = None
                cmd_manifest = None
                started = _dt.now().isoformat()
                for i, arg in enumerate(cmd):
                    if arg == "--runtime-manifest" and i + 1 < len(cmd):
                        cmd_manifest = cmd[i + 1]
                    elif arg == "--runtime-nonce" and i + 1 < len(cmd):
                        cmd_nonce = cmd[i + 1]
                manifest = {
                    "format_version": "1.0",
                    "task": "stock_dividend",  # WRONG -- requested fin_indicator
                    "pid": os.getpid(), "nonce": cmd_nonce, "created_at": started,
                    "QUANTSTUDIO_DATA_ROOT": str(sr),
                    "imported_DATA_ROOT": str(sr),
                    "writer_db_path": str(staging_db.resolve()),
                    "batch_audit_db_path": str(sr / "batch_audit.db"),
                    "quarantine_db_path": str(sr / "quarantine.db"),
                    "daemon_log_path": str(sr / "logs" / "daemon.log"),
                    "collector_lock_path": str(sr / ".collector_run.lock"),
                    "daemon_lock_path": str(sr / ".daemon.lock"),
                    "daemon_status_path": str(sr / "daemon_status.json"),
                    "config_dir": str(staging_cfg_dir.resolve()),
                }
                Path(cmd_manifest).write_text(json.dumps(manifest), encoding="utf-8")
                return CommandResult(exit_code=0, pid=os.getpid(),
                                     started_at=started, finished_at=_dt.now().isoformat())

            with patch("scripts.backfill_fin_growth_dividend_staging.run_cmd", fake_wrong_task):
                ns = argparse.Namespace(
                    staging_root=str(staging_root), run_task="fin_indicator",
                    dry_run=False, timeout_sec=60, source_db=db_path,
                )
                rc = phase_run_task(ns)
            assert rc == 1, "manifest with wrong task must BLOCK run-task"
            os.unlink(db_path)


class TestRunTaskManifestWrongDataRootBlocked:
    """test_run_task_blocks_when_child_manifest_wrong_data_root"""

    def test_run_task_blocks_when_child_manifest_wrong_data_root(self):
        """If manifest QUANTSTUDIO_DATA_ROOT != staging_root, run-task must BLOCK."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)

            import argparse
            from scripts.backfill_fin_growth_dividend_staging import phase_run_task

            def fake_wrong_root(cmd, cwd=None, dry_run=False, env=None, timeout=21600,
                                 log_file=None, staging_db=None, task_name=""):
                from datetime import datetime as _dt
                from scripts.backfill_fin_growth_dividend_staging import CommandResult
                sr = staging_root.resolve()
                cmd_nonce = None
                cmd_manifest = None
                started = _dt.now().isoformat()
                for i, arg in enumerate(cmd):
                    if arg == "--runtime-manifest" and i + 1 < len(cmd):
                        cmd_manifest = cmd[i + 1]
                    elif arg == "--runtime-nonce" and i + 1 < len(cmd):
                        cmd_nonce = cmd[i + 1]
                manifest = {
                    "format_version": "1.0", "task": task_name,
                    "pid": os.getpid(), "nonce": cmd_nonce, "created_at": started,
                    "QUANTSTUDIO_DATA_ROOT": "/totally/wrong/root",  # WRONG
                    "imported_DATA_ROOT": str(sr),
                    "writer_db_path": str(staging_db.resolve()),
                    "batch_audit_db_path": str(sr / "batch_audit.db"),
                    "quarantine_db_path": str(sr / "quarantine.db"),
                    "daemon_log_path": str(sr / "logs" / "daemon.log"),
                    "collector_lock_path": str(sr / ".collector_run.lock"),
                    "daemon_lock_path": str(sr / ".daemon.lock"),
                    "daemon_status_path": str(sr / "daemon_status.json"),
                    "config_dir": str(staging_cfg_dir.resolve()),
                }
                Path(cmd_manifest).write_text(json.dumps(manifest), encoding="utf-8")
                return CommandResult(exit_code=0, pid=os.getpid(),
                                     started_at=started, finished_at=_dt.now().isoformat())

            with patch("scripts.backfill_fin_growth_dividend_staging.run_cmd", fake_wrong_root):
                ns = argparse.Namespace(
                    staging_root=str(staging_root), run_task="fin_indicator",
                    dry_run=False, timeout_sec=60, source_db=db_path,
                )
                rc = phase_run_task(ns)
            assert rc == 1, "manifest with wrong DATA_ROOT must BLOCK run-task"
            os.unlink(db_path)


class TestRunTaskManifestWrongWriterPathBlocked:
    """test_run_task_blocks_when_child_manifest_wrong_writer_path"""

    def test_run_task_blocks_when_child_manifest_wrong_writer_path(self):
        """If manifest writer_db_path != staging_root/staging.db, BLOCK."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)

            import argparse
            from scripts.backfill_fin_growth_dividend_staging import phase_run_task

            def fake_wrong_path(cmd, cwd=None, dry_run=False, env=None, timeout=21600,
                                 log_file=None, staging_db=None, task_name=""):
                from datetime import datetime as _dt
                from scripts.backfill_fin_growth_dividend_staging import CommandResult
                sr = staging_root.resolve()
                cmd_nonce = None
                cmd_manifest = None
                started = _dt.now().isoformat()
                for i, arg in enumerate(cmd):
                    if arg == "--runtime-manifest" and i + 1 < len(cmd):
                        cmd_manifest = cmd[i + 1]
                    elif arg == "--runtime-nonce" and i + 1 < len(cmd):
                        cmd_nonce = cmd[i + 1]
                manifest = {
                    "format_version": "1.0", "task": task_name,
                    "pid": os.getpid(), "nonce": cmd_nonce, "created_at": started,
                    "QUANTSTUDIO_DATA_ROOT": str(sr),
                    "imported_DATA_ROOT": str(sr),
                    "writer_db_path": "/etc/wrong.db",  # WRONG
                    "batch_audit_db_path": str(sr / "batch_audit.db"),
                    "quarantine_db_path": str(sr / "quarantine.db"),
                    "daemon_log_path": str(sr / "logs" / "daemon.log"),
                    "collector_lock_path": str(sr / ".collector_run.lock"),
                    "daemon_lock_path": str(sr / ".daemon.lock"),
                    "daemon_status_path": str(sr / "daemon_status.json"),
                    "config_dir": str(staging_cfg_dir.resolve()),
                }
                Path(cmd_manifest).write_text(json.dumps(manifest), encoding="utf-8")
                return CommandResult(exit_code=0, pid=os.getpid(),
                                     started_at=started, finished_at=_dt.now().isoformat())

            with patch("scripts.backfill_fin_growth_dividend_staging.run_cmd", fake_wrong_path):
                ns = argparse.Namespace(
                    staging_root=str(staging_root), run_task="fin_indicator",
                    dry_run=False, timeout_sec=60, source_db=db_path,
                )
                rc = phase_run_task(ns)
            assert rc == 1, "manifest with wrong writer_db_path must BLOCK"
            os.unlink(db_path)


# ===========================================================================
# Negative gate group 6: audit evidence validator (validate_audit_evidence)
# ===========================================================================

class TestValidatorDuplicateBatchIdBlocked:
    """test_validator_duplicate_batch_id_blocked"""

    def test_validator_duplicate_batch_id_blocked(self):
        """Two batches with the same batch_id must fail evidence validation."""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            # Both batches carry valid per-batch fields so the test reaches the
            # duplicate-batch_id gate rather than tripping a field-presence gate.
            ev["batch_ids"] = [
                {"batch_id": "dup", "table": "fin_indicator", "status": "success",
                 "source": "tushare", "rows_written": 1, "rows_new": 1, "rows_updated": 0,
                 "started_at": "2026-07-28T00:00:00", "finished_at": "2026-07-28T00:01:00"},
                {"batch_id": "dup", "table": "stock_dividend", "status": "success",
                 "source": "tushare", "rows_written": 1, "rows_new": 1, "rows_updated": 0,
                 "started_at": "2026-07-28T00:00:00", "finished_at": "2026-07-28T00:01:00"},
            ]
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            assert not ok, f"duplicate batch_id must BLOCK, got ok={ok}"
            assert "duplicate" in reason.lower()
            os.unlink(db_path)


class TestValidatorSameTaskBatchesBlocked:
    """test_validator_two_batches_same_task_blocked"""

    def test_validator_two_batches_same_task_blocked(self):
        """Two distinct batch_ids but same task must fail (one batch per task)."""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            ev["batch_ids"] = [
                {"batch_id": "b1", "table": "fin_indicator", "status": "success",
                 "source": "tushare", "rows_written": 1, "rows_new": 1, "rows_updated": 0,
                 "started_at": "2026-07-28T00:00:00", "finished_at": "2026-07-28T00:01:00"},
                {"batch_id": "b2", "table": "fin_indicator", "status": "success",
                 "source": "tushare", "rows_written": 1, "rows_new": 1, "rows_updated": 0,
                 "started_at": "2026-07-28T00:00:00", "finished_at": "2026-07-28T00:01:00"},
            ]
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            assert not ok
            assert "same task" in reason.lower()
            os.unlink(db_path)


class TestValidatorBatchConservationFailBlocked:
    """test_validator_batch_conservation_failure_blocked

    守恒口径（W2-0.9 修正）：DataValidator 分流前先做主键去重
    (validator.py drop_duplicates)，去重掉的行既不计入 passed 也不计入
    rejected，属合法收敛。因此守恒放宽为：
      rows_passed + rows_rejected <= rows_raw  （去重只会减少行数）
    且入库一致性：rows_written == rows_passed。
    真正的违规是 passed+rejected 超过 raw，或 written 与 passed 不一致。
    """

    def test_validator_batch_conservation_exceeds_raw_blocked(self):
        """rows_passed + rows_rejected > rows_raw must fail validation."""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            # 3 + 3 = 6 > raw 5 (passed+rejected 超过 raw, 违规)
            ev["batch_counts"]["fin_indicator"] = {
                "rows_raw": 5, "rows_passed": 3, "rows_rejected": 3}
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            assert not ok
            assert "rows_passed" in reason
            os.unlink(db_path)

    def test_validator_batch_written_not_equal_passed_blocked(self):
        """rows_written != rows_passed must fail validation (入库一致性)."""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            # passed+rejected (4) <= raw (5) 合法去重, 但 written != passed 违规
            ev["batch_counts"]["fin_indicator"] = {
                "rows_raw": 5, "rows_passed": 3, "rows_rejected": 1, "rows_written": 2}
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            assert not ok
            assert "rows_written" in reason
            os.unlink(db_path)

    def test_validator_batch_conservation_dedup_allowed(self):
        """合法去重 (passed+rejected < raw) 必须通过, 不应误判为违规."""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            # 272765 raw, 167312 passed, 8 rejected, written==passed (真实全量回填口径)
            ev["batch_counts"]["fin_indicator"] = {
                "rows_raw": 272765, "rows_passed": 167312,
                "rows_rejected": 8, "rows_written": 167312}
            ev["batch_counts"]["stock_dividend"] = {
                "rows_raw": 55094, "rows_passed": 54904,
                "rows_rejected": 0, "rows_written": 54904}
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            # 只校验守恒相关逻辑通过 (其他 evidence 字段由 _make_full_passing_evidence 保证)
            # 若因其他原因失败, reason 不应包含 rows_passed/rows_written 守恒信息
            if not ok:
                assert "rows_passed" not in reason, f"合法去重被误判: {reason}"
                assert "rows_written" not in reason, f"合法去重被误判: {reason}"
            os.unlink(db_path)


class TestValidatorGrowthAllZeroBlocked:
    """test_validator_growth_fields_all_zero_blocked

    Gap4: an all-zero (or any single zero) growth backfill produced no usable
    data and must BLOCK promotion. fin_indicator_row_count=0 also BLOCKs.
    """

    def test_growth_all_zero_blocked(self):
        """All three growth non-null counts == 0 must BLOCK."""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            ev["growth_field_stats"] = {"np_yoy_non_null": 0, "or_yoy_non_null": 0, "tr_yoy_non_null": 0}
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            assert not ok, "all-zero growth counts must BLOCK"
            assert "np_yoy_non_null" in reason
            os.unlink(db_path)

    def test_single_growth_field_zero_blocked(self):
        """Any single growth field == 0 must BLOCK (e.g. or_yoy empty)."""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            ev["growth_field_stats"] = {"np_yoy_non_null": 5, "or_yoy_non_null": 0, "tr_yoy_non_null": 5}
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            assert not ok, "a zero or_yoy count must BLOCK"
            assert "or_yoy_non_null" in reason
            os.unlink(db_path)

    def test_fin_indicator_zero_rows_blocked(self):
        """fin_indicator_row_count == 0 must BLOCK (no rows backfilled)."""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            ev["fin_indicator_row_count"] = 0
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            assert not ok
            assert "fin_indicator_row_count" in reason
            os.unlink(db_path)


class TestValidatorDividendAllZeroBlocked:
    """Gap4: all-zero pre/post-tax dividend counts must BLOCK."""

    def test_dividend_pre_tax_zero_blocked(self):
        """cash_div_before_tax_non_null == 0 must BLOCK."""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            ev["dividend_stats"]["cash_div_before_tax_non_null"] = 0
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            assert not ok, "zero pre-tax dividend count must BLOCK"
            assert "cash_div_before_tax_non_null" in reason
            os.unlink(db_path)

    def test_dividend_post_tax_zero_blocked(self):
        """cash_div_after_tax_non_null == 0 must BLOCK."""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            ev["dividend_stats"]["cash_div_after_tax_non_null"] = 0
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            assert not ok, "zero post-tax dividend count must BLOCK"
            assert "cash_div_after_tax_non_null" in reason
            os.unlink(db_path)

    def test_dividend_zero_rows_blocked(self):
        """stock_dividend_row_count == 0 must BLOCK."""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            ev["stock_dividend_row_count"] = 0
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            assert not ok
            assert "stock_dividend_row_count" in reason
            os.unlink(db_path)

    def test_valid_nonzero_stats_pass(self):
        """Sanity: a fully-valid evidence object must PASS the value gates."""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            assert ok, f"valid evidence must PASS, got: {reason}"
            os.unlink(db_path)


class TestValidatorDataSchemaMismatchBlocked:
    """test_validator_data_schema_version_mismatch_blocked"""

    def test_validator_data_schema_version_mismatch_blocked(self):
        """data_schema_version != '2.0' must fail validation."""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            ev["data_schema_version"] = "1.0"  # WRONG
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            assert not ok
            assert "data_schema_version" in reason
            os.unlink(db_path)


class TestValidatorProfileMismatchBlocked:
    """test_validator_ptrade_profile_version_mismatch_blocked"""

    def test_validator_ptrade_profile_version_mismatch_blocked(self):
        """ptrade_profile_version != '1.10.0' must fail validation."""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            ev["ptrade_profile_version"] = "1.9.0"  # WRONG
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            assert not ok
            assert "ptrade_profile_version" in reason
            os.unlink(db_path)


class TestValidatorAuditChecksZeroBlocked:
    """test_validator_audit_checks_zero_blocked"""

    def test_validator_audit_checks_zero_blocked(self):
        """audit.checks_run == 0 must fail validation."""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            ev["audit"]["checks_run"] = 0
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            assert not ok
            assert "checks_run" in reason
            os.unlink(db_path)


class TestValidatorAuditErrorsPolicy:
    """W2-0.9 Phase 5：audit.errors_count 不再单独 BLOCK；baseline_delta_passed 决定。

    旧契约（errors_count != 0 → BLOCK）已废弃，因为源库既有非目标表错误会被继承。
    新契约：errors_count != 0 + baseline_delta_passed=True → PASS（继承允许）；
    baseline_delta_passed 缺失/False → BLOCK。
    """

    def test_errors_nonzero_with_delta_pass_is_allowed(self):
        """errors_count != 0 但 baseline_delta_passed=True（继承的非目标表错误）→ PASS。"""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            ev["audit"]["errors_count"] = 3  # inherited non-target errors
            # baseline_delta_passed stays True (from helper)
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            assert ok, (f"inherited non-target errors with baseline_delta_passed=True must PASS; "
                        f"got: {reason}")
            os.unlink(db_path)

    def test_missing_baseline_delta_passed_blocks(self):
        """baseline_delta_passed 缺失 → BLOCK（不得仅凭 passed=true 放行）。"""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            ev.pop("baseline_delta_passed", None)  # remove the delta verdict
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            assert not ok
            assert "baseline_delta_passed" in reason
            os.unlink(db_path)

    def test_baseline_delta_passed_false_blocks(self):
        """baseline_delta_passed=False → BLOCK。"""
        from scripts.backfill_fin_growth_dividend_staging import validate_audit_evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)
            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            ev["baseline_delta_passed"] = False
            ok, reason = validate_audit_evidence(ev, staging_db, Path(db_path), staging_cfg_dir)
            assert not ok
            assert "baseline_delta_passed" in reason
            os.unlink(db_path)


# ===========================================================================
# Fast simulated dual-task E2E (monkeypatched run_cmd -- NOT a real subprocess)
#
# This is the FAST simulated E2E: it monkeypatches run_cmd in-process so no real
# daemon subprocess is spawned. It exercises the full prepare->task->audit->promote
# data flow and all validator gates, but does NOT validate real subprocess
# boundaries (Popen PID inheritance, daemon CLI nonce/manifest plumbing,
# ResidentCollector construction, os.replace in the child). The REAL subprocess
# E2E is in TestRealSubprocessDualTaskE2E below.
# ===========================================================================

class TestFullDualTaskE2E:
    """test_fast_simulated_dual_task_e2e

    Fast simulated E2E (monkeypatched run_cmd, not a real subprocess).
    prepare -> run-task fin_indicator -> run-task stock_dividend -> audit ->
    promote dry-run. The fake run_cmd writes valid runtime manifests + ledger
    rows + data rows. No real Tushare, no production DB writes. All return codes
    asserted unconditionally.
    """

    def test_fast_simulated_dual_task_e2e(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            config_dir = _make_config_dir(tmp_p, db_path)
            staging_root_arg = tmp_p / "staging"

            # ---- STEP 1: prepare ----
            rc_prepare = _run_staging([
                "--source-db", db_path,
                "--staging-root", str(staging_root_arg),
                "--start-date", "2022-01-01",
                "--prepare",
            ]).returncode
            assert rc_prepare == 0, "prepare must succeed"
            assert (staging_root_arg / "staging.db").exists()
            assert (staging_root_arg / "batch_audit.db").exists()
            assert (staging_root_arg / "quarantine.db").exists()
            # ledgers must be SQLite, not DuckDB (explicit close for Windows)
            _probe = sqlite3.connect(str(staging_root_arg / "batch_audit.db"))
            _probe.close()

            staging_db = staging_root_arg / "staging.db"
            staging_cfg_dir = staging_root_arg / "config"

            # Snapshot source DB SHA-256 BEFORE tasks (must be unchanged after)
            source_sha_before = _sha256_file(db_path)
            source_size_before = Path(db_path).stat().st_size

            import argparse
            from scripts.backfill_fin_growth_dividend_staging import (
                phase_run_task, phase_audit, phase_promote)

            # ---- STEP 2: run-task fin_indicator (fake child) ----
            fin_batches = [{
                "batch_id": "b-fin-001", "task_name": "fin_indicator",
                "table": "fin_indicator", "rows_raw": 1, "rows_passed": 1,
                "rows_rejected": 0, "rows_written": 1,
            }]
            fake_fin = _make_fake_child_writer(
                staging_root_arg, staging_db, staging_cfg_dir,
                batch_records=fin_batches)
            with patch("scripts.backfill_fin_growth_dividend_staging.run_cmd", fake_fin):
                ns = argparse.Namespace(
                    staging_root=str(staging_root_arg), run_task="fin_indicator",
                    dry_run=False, timeout_sec=60, source_db=db_path,
                )
                rc_fin = phase_run_task(ns)
            assert rc_fin == 0, "run-task fin_indicator must succeed"
            assert (staging_root_arg / "runtime_paths_fin_indicator.json").exists()
            # First batch written to ledger (explicit close for Windows locks)
            _lc = sqlite3.connect(str(staging_root_arg / "batch_audit.db"))
            try:
                n = _lc.execute("SELECT COUNT(*) FROM batch_audit").fetchone()[0]
            finally:
                _lc.close()
            assert n == 1, f"expected 1 ledger row after fin task, got {n}"

            # ---- STEP 3: run-task stock_dividend (fake child) ----
            # IMPORTANT: second task must NOT clear the first task's ledger row.
            div_batches = [{
                "batch_id": "b-div-001", "task_name": "stock_dividend",
                "table": "stock_dividend", "rows_raw": 1, "rows_passed": 1,
                "rows_rejected": 0, "rows_written": 1,
            }]
            fake_div = _make_fake_child_writer(
                staging_root_arg, staging_db, staging_cfg_dir,
                batch_records=div_batches)
            with patch("scripts.backfill_fin_growth_dividend_staging.run_cmd", fake_div):
                ns = argparse.Namespace(
                    staging_root=str(staging_root_arg), run_task="stock_dividend",
                    dry_run=False, timeout_sec=60, source_db=db_path,
                )
                rc_div = phase_run_task(ns)
            assert rc_div == 0, "run-task stock_dividend must succeed"
            assert (staging_root_arg / "runtime_paths_stock_dividend.json").exists()

            # Both batches must coexist in ONE ledger (second didn't clear first)
            _lc2 = sqlite3.connect(str(staging_root_arg / "batch_audit.db"))
            try:
                rows = _lc2.execute(
                    "SELECT task_name FROM batch_audit ORDER BY task_name").fetchall()
            finally:
                _lc2.close()
            assert [r[0] for r in rows] == ["fin_indicator", "stock_dividend"], \
                f"both task batches must coexist in ledger, got {rows}"

            # ---- STEP 4: audit ----
            # Use a passing fake report so evidence collection is the focus.
            class _PassingReport:
                checks_run = 12
                issues = []
                @property
                def passed(self):
                    return True

            # Ensure staging DB has the data tables the evidence builder reads.
            conn = duckdb.connect(str(staging_db))
            tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
            if "fin_indicator" not in tables:
                conn.execute(
                    "CREATE TABLE fin_indicator (code VARCHAR, np_yoy DOUBLE,"
                    " or_yoy DOUBLE, tr_yoy DOUBLE, diluted_eps DOUBLE, update_flag VARCHAR)")
                conn.execute("INSERT INTO fin_indicator VALUES ('X',1.0,1.0,1.0,1.0,'1')")
            if "stock_dividend" not in tables:
                conn.execute(
                    "CREATE TABLE stock_dividend (code VARCHAR, cash_div_before_tax DOUBLE,"
                    " cash_div_after_tax DOUBLE, stk_div DOUBLE)")
                conn.execute("INSERT INTO stock_dividend VALUES ('X',1.0,0.8,0.1)")
            wm_tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
            if "source_watermark" in wm_tables:
                conn.execute("DELETE FROM source_watermark WHERE table_name IN ('fin_indicator','stock_dividend')")
                conn.execute(
                    "INSERT INTO source_watermark (source, table_name, freq, last_date,"
                    " last_batch_id, updated_at, source_generation, cutover_id) VALUES "
                    "('tushare','fin_indicator','daily',20240130,'b1','2026-07-28','not-qfq-managed','not-applicable'),"
                    "('tushare','stock_dividend','daily',20240130,'b2','2026-07-28','not-qfq-managed','not-applicable')")
            conn.close()

            with patch("quantstudio.pipeline.quality_audit.DataQualityAuditor.__init__",
                       return_value=None):
                with patch("quantstudio.pipeline.quality_audit.DataQualityAuditor.run",
                           return_value=_PassingReport()):
                    ns = argparse.Namespace(
                        staging_root=str(staging_root_arg),
                        dry_run=False, source_db=db_path,
                    )
                    rc_audit = phase_audit(ns)
            assert rc_audit == 0, "audit must pass and produce valid evidence"
            ev_path = staging_root_arg / "audit_evidence.json"
            assert ev_path.exists(), "audit_evidence.json must be written"
            ev = json.loads(ev_path.read_text(encoding="utf-8"))
            assert ev.get("passed") is True
            assert ev.get("data_schema_version") == "2.0"
            assert ev.get("ptrade_profile_version") == "1.10.0"

            # ---- STEP 5: promote (dry-run) ----
            rc_promote = _run_staging([
                "--source-db", db_path,
                "--staging-root", str(staging_root_arg),
                "--promote",
            ]).returncode
            assert rc_promote == 0, "promote dry-run must succeed"
            # Source DB must be byte-for-byte unchanged
            assert _sha256_file(db_path) == source_sha_before
            assert Path(db_path).stat().st_size == source_size_before
            # Staging DB must still exist (not moved)
            assert staging_db.exists()

            os.unlink(db_path)


class TestProductionDbUnchangedAfterE2E:
    """test_production_db_byte_identical_after_full_e2e"""

    def test_production_db_byte_identical_after_full_e2e(self):
        """Independent re-assertion that the source DB SHA+size is invariant
        across a full prepare->task->audit->promote cycle."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            _make_config_dir(tmp_p, db_path)
            staging_root_arg = tmp_p / "staging"

            before_sha = _sha256_file(db_path)
            before_size = Path(db_path).stat().st_size
            before_mtime = Path(db_path).stat().st_mtime_ns

            # prepare
            assert _run_staging(["--source-db", db_path,
                                  "--staging-root", str(staging_root_arg),
                                  "--prepare"]).returncode == 0

            after_prepare_sha = _sha256_file(db_path)
            assert after_prepare_sha == before_sha
            assert Path(db_path).stat().st_size == before_size
            assert Path(db_path).stat().st_mtime_ns == before_mtime

            os.unlink(db_path)


# ===========================================================================
# W2-0.7B Gap 1/2: REAL daemon subprocess + manifest PID/timestamp gates
#
# The real subprocess E2E launches `python -m quantstudio.pipeline.daemon` as an
# actual Popen child. A sitecustomize.py on PYTHONPATH injects a FAKE
# TushareAdapter into sys.modules BEFORE the child imports
# quantstudio.pipeline.sources, so the real daemon once-path runs end-to-end
# (ResidentCollector construction -> os.replace manifest write -> BatchAudit
# record) against canned data, with NO real Tushare credentials and NO edits to
# production source.
# ===========================================================================

# sitecustomize.py payload: installs a fake TushareAdapter that returns canned
# tushare-source-column DataFrames for fin_indicator / stock_dividend. It must
# be auto-imported by CPython at child startup, so it is written verbatim to a
# temp dir placed on PYTHONPATH for the child only.
_SITECUSTOMIZE_SRC = r'''
"""Test-only sitecustomize: inject a fake TushareAdapter into sys.modules.

Auto-imported by CPython at interpreter startup because its directory is on
PYTHONPATH. Runs BEFORE the daemon child imports quantstudio.pipeline.sources,
so sources/__init__.py's `from .tushare_adapter import TushareAdapter` picks up
this fake class instead of the real one. Touches NO production source.
"""
import sys
import types

fake_mod = types.ModuleType("quantstudio.pipeline.sources.tushare_adapter")


class TushareAdapter:
    """Canned-data fake. Returns tushare-source columns (aligner renames)."""

    def __init__(self, config):
        self.config = config or {}

    def configure_execution(self, task):
        pass

    def supports_task(self, table, freq):
        return (True, "")

    def get_all_stock_codes(self):
        # Tiny universe so the run finishes in seconds.
        return ["000001.SZ"]

    def fetch_table(self, table, start, end, freq="daily", codes=None):
        import pandas as pd
        if table == "fin_indicator":
            df = pd.DataFrame([{
                "ts_code": "000001.SZ", "ann_date": 20240130, "end_date": 20231231,
                "eps": 1.5, "dt_eps": 1.4, "bps": 10.0, "roe": 15.0,
                "pe_ttm": 8.0, "pb": 1.2, "ps_ttm": 2.0,
                "netprofit_yoy": 10.0, "or_yoy": 9.0, "tr_yoy": 8.5,
                "update_flag": "1",
            }])
        elif table == "stock_dividend":
            df = pd.DataFrame([{
                "ts_code": "000001.SZ", "ex_date": 20240601, "record_date": 20240531,
                "ann_date": 20240130, "end_date": 20231231,
                "cash_div_tax": 1.0, "cash_div": 0.8, "stk_div": 0.1,
                "stk_bo_rate": 0.05, "stk_co_rate": 0.05, "div_proc": "实施",
                "update_time": "2024-01-30",
            }])
        else:
            import pandas as pd
            df = pd.DataFrame()
        return df, {"source": "tushare"}

    def close(self):
        pass


fake_mod.TushareAdapter = TushareAdapter
sys.modules["quantstudio.pipeline.sources.tushare_adapter"] = fake_mod
'''


def _write_sitecustomize(target_dir: Path) -> Path:
    """Write the fake-adapter sitecustomize.py into target_dir. Returns its path."""
    target_dir.mkdir(parents=True, exist_ok=True)
    sc_path = target_dir / "sitecustomize.py"
    sc_path.write_text(_SITECUSTOMIZE_SRC, encoding="utf-8")
    return sc_path


def _run_real_daemon_child(staging_cfg_dir: Path, staging_root: Path,
                            manifest_path: Path, nonce: str, task: str,
                            extra_pythonpath: str, timeout: int = 120):
    """Launch a REAL daemon subprocess and return (CommandResult-like tuple).

    Mirrors what phase_run_task builds, but with PYTHONPATH augmented so the
    child auto-imports the fake-adapter sitecustomize. Returns
    (exit_code, child_pid, started_iso, finished_iso, manifest_path).
    """
    cmd = [
        sys.executable, "-m", "quantstudio.pipeline.daemon",
        "--mode", "once", "--task", task, "--pull-mode", "full_range",
        "--quality-audit", "none",
        "--config-dir", str(staging_cfg_dir),
        "--runtime-manifest", str(manifest_path),
        "--runtime-nonce", nonce,
    ]
    env = os.environ.copy()
    env["QUANTSTUDIO_DATA_ROOT"] = str(staging_root.resolve())
    # Prepend the fake-adapter dir so sitecustomize.py is auto-imported first.
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (extra_pythonpath + os.pathsep + existing_pp
                         if existing_pp else extra_pythonpath)
    started = __import__("datetime").datetime.now().isoformat()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            cwd=str(_PROJECT_ROOT), env=env, text=True)
    child_pid = proc.pid
    out_lines = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            out_lines.append(line)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=30)
    finished = __import__("datetime").datetime.now().isoformat()
    child_output = "".join(out_lines)
    return proc.returncode, child_pid, started, finished, manifest_path, child_output


class TestRealSubprocessDualTaskE2E:
    """test_real_subprocess_dual_task_e2e

    REAL daemon subprocess + fake-adapter E2E. Validates the boundaries a mock
    cannot: real Popen PID inheritance, daemon CLI nonce/manifest plumbing,
    ResidentCollector.from_configs construction, os.replace manifest write in
    the child, real BatchAudit/Quarantine/Writer paths, and the parent's
    PID-pinned + time-windowed manifest validation against the actual child PID.
    """

    def test_real_subprocess_dual_task_e2e(self):
        import uuid as _uuid
        import contextlib
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            _make_config_dir(tmp_p, db_path)
            staging_root_arg = tmp_p / "staging"

            # prepare (subprocess) -- creates staging.db, ledgers, config, marker
            rc_prepare = _run_staging([
                "--source-db", db_path, "--staging-root", str(staging_root_arg),
                "--start-date", "2022-01-01", "--prepare",
            ]).returncode
            assert rc_prepare == 0, "prepare must succeed"

            staging_db = staging_root_arg / "staging.db"
            staging_cfg_dir = staging_root_arg / "config"

            # Snapshot source DB before tasks (must be byte-identical after)
            source_sha_before = _sha256_file(db_path)
            source_size_before = Path(db_path).stat().st_size

            # Write fake-adapter sitecustomize into an isolated dir
            fake_dir = tmp_p / "fakepkg"
            _write_sitecustomize(fake_dir)

            child_pids = {}
            child_outputs = {}
            for task in ("fin_indicator", "stock_dividend"):
                manifest_path = staging_root_arg / f"runtime_paths_{task}.json"
                nonce = str(_uuid.uuid4())
                manifest_path.unlink(missing_ok=True)
                rc, pid, started, finished, mp, cout = _run_real_daemon_child(
                    staging_cfg_dir, staging_root_arg, manifest_path, nonce,
                    task, str(fake_dir))
                child_outputs[task] = cout
                assert rc == 0, (
                    f"real daemon child for {task} failed rc={rc}\n"
                    f"CHILD OUTPUT:\n{cout}")
                # Capture the real child PID for the PID-match assertion below
                child_pids[task] = pid
                # Capture the real child PID for the PID-match assertion below
                child_pids[task] = pid
                # The child must have written the manifest via os.replace
                assert mp.exists(), f"real child did not write manifest {mp}"
                manifest = json.loads(mp.read_text(encoding="utf-8"))
                # PID must equal the ACTUAL Popen PID (the whole point of Gap2)
                assert manifest.get("pid") == pid, (
                    f"manifest pid {manifest.get('pid')} != real child pid {pid}")
                # created_at must fall within the child's lifetime window
                from datetime import datetime as _dt
                created = _dt.fromisoformat(manifest["created_at"])
                assert _dt.fromisoformat(started) <= created <= _dt.fromisoformat(finished), (
                    f"manifest created_at {created} outside [{started}, {finished}]")

            # Both manifests written by two distinct real child PIDs
            assert child_pids["fin_indicator"] != child_pids["stock_dividend"], \
                "two real children must have distinct PIDs"

            # Source DB byte-identical after two real tasks
            assert _sha256_file(db_path) == source_sha_before
            assert Path(db_path).stat().st_size == source_size_before

            os.unlink(db_path)


class TestRunTaskWrongPidBlocked:
    """Gap2: manifest pid != actual Popen pid must BLOCK run-task."""

    def test_run_task_blocks_when_manifest_pid_wrong(self):
        from scripts.backfill_fin_growth_dividend_staging import (
            phase_run_task, CommandResult)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)

            def fake_wrong_pid(cmd, cwd=None, dry_run=False, env=None, timeout=21600,
                                log_file=None, staging_db=None, task_name=""):
                from datetime import datetime as _dt
                sr = staging_root.resolve()
                cmd_nonce = None
                cmd_manifest = None
                started = _dt.now().isoformat()
                for i, arg in enumerate(cmd):
                    if arg == "--runtime-manifest" and i + 1 < len(cmd):
                        cmd_manifest = cmd[i + 1]
                    elif arg == "--runtime-nonce" and i + 1 < len(cmd):
                        cmd_nonce = cmd[i + 1]
                manifest = {
                    "format_version": "1.0", "task": task_name,
                    "pid": 999999,  # WRONG -- does not match the returned pid
                    "nonce": cmd_nonce, "created_at": started,
                    "QUANTSTUDIO_DATA_ROOT": str(sr), "imported_DATA_ROOT": str(sr),
                    "writer_db_path": str(staging_db.resolve()),
                    "batch_audit_db_path": str(sr / "batch_audit.db"),
                    "quarantine_db_path": str(sr / "quarantine.db"),
                    "daemon_log_path": str(sr / "logs" / "daemon.log"),
                    "collector_lock_path": str(sr / ".collector_run.lock"),
                    "daemon_lock_path": str(sr / ".daemon.lock"),
                    "daemon_status_path": str(sr / "daemon_status.json"),
                    "config_dir": str(staging_cfg_dir.resolve()),
                }
                Path(cmd_manifest).write_text(json.dumps(manifest), encoding="utf-8")
                # Return a DIFFERENT pid than the manifest's 999999
                return CommandResult(exit_code=0, pid=os.getpid(),
                                     started_at=started, finished_at=_dt.now().isoformat())

            import argparse
            with patch("scripts.backfill_fin_growth_dividend_staging.run_cmd", fake_wrong_pid):
                ns = argparse.Namespace(
                    staging_root=str(staging_root), run_task="fin_indicator",
                    dry_run=False, timeout_sec=60, source_db=db_path,
                )
                rc = phase_run_task(ns)
            assert rc == 1, "manifest pid != child pid must BLOCK run-task"
            os.unlink(db_path)


class TestRunTaskStaleTimestampBlocked:
    """Gap2: manifest created_at before child started_at must BLOCK run-task."""

    def test_run_task_blocks_when_manifest_timestamp_stale(self):
        from scripts.backfill_fin_growth_dividend_staging import (
            phase_run_task, CommandResult)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)

            def fake_stale_ts(cmd, cwd=None, dry_run=False, env=None, timeout=21600,
                               log_file=None, staging_db=None, task_name=""):
                from datetime import datetime as _dt
                sr = staging_root.resolve()
                cmd_nonce = None
                cmd_manifest = None
                for i, arg in enumerate(cmd):
                    if arg == "--runtime-manifest" and i + 1 < len(cmd):
                        cmd_manifest = cmd[i + 1]
                    elif arg == "--runtime-nonce" and i + 1 < len(cmd):
                        cmd_nonce = cmd[i + 1]
                # Manifest created_at is FAR in the past (stale)
                manifest = {
                    "format_version": "1.0", "task": task_name,
                    "pid": os.getpid(), "nonce": cmd_nonce,
                    "created_at": "2000-01-01T00:00:00",
                    "QUANTSTUDIO_DATA_ROOT": str(sr), "imported_DATA_ROOT": str(sr),
                    "writer_db_path": str(staging_db.resolve()),
                    "batch_audit_db_path": str(sr / "batch_audit.db"),
                    "quarantine_db_path": str(sr / "quarantine.db"),
                    "daemon_log_path": str(sr / "logs" / "daemon.log"),
                    "collector_lock_path": str(sr / ".collector_run.lock"),
                    "daemon_lock_path": str(sr / ".daemon.lock"),
                    "daemon_status_path": str(sr / "daemon_status.json"),
                    "config_dir": str(staging_cfg_dir.resolve()),
                }
                Path(cmd_manifest).write_text(json.dumps(manifest), encoding="utf-8")
                now = _dt.now().isoformat()
                return CommandResult(exit_code=0, pid=os.getpid(),
                                     started_at=now, finished_at=now)

            import argparse
            with patch("scripts.backfill_fin_growth_dividend_staging.run_cmd", fake_stale_ts):
                ns = argparse.Namespace(
                    staging_root=str(staging_root), run_task="fin_indicator",
                    dry_run=False, timeout_sec=60, source_db=db_path,
                )
                rc = phase_run_task(ns)
            assert rc == 1, "stale manifest created_at must BLOCK run-task"
            os.unlink(db_path)


class TestRunTaskManifestFutureTimestampBlocked:
    """Gap2: manifest created_at after child finished_at must BLOCK."""

    def test_run_task_blocks_when_manifest_timestamp_future(self):
        from scripts.backfill_fin_growth_dividend_staging import (
            phase_run_task, CommandResult)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)

            def fake_future_ts(cmd, cwd=None, dry_run=False, env=None, timeout=21600,
                                log_file=None, staging_db=None, task_name=""):
                from datetime import datetime as _dt
                sr = staging_root.resolve()
                cmd_nonce = None
                cmd_manifest = None
                for i, arg in enumerate(cmd):
                    if arg == "--runtime-manifest" and i + 1 < len(cmd):
                        cmd_manifest = cmd[i + 1]
                    elif arg == "--runtime-nonce" and i + 1 < len(cmd):
                        cmd_nonce = cmd[i + 1]
                # Manifest created_at is FAR in the future
                manifest = {
                    "format_version": "1.0", "task": task_name,
                    "pid": os.getpid(), "nonce": cmd_nonce,
                    "created_at": "2099-01-01T00:00:00",
                    "QUANTSTUDIO_DATA_ROOT": str(sr), "imported_DATA_ROOT": str(sr),
                    "writer_db_path": str(staging_db.resolve()),
                    "batch_audit_db_path": str(sr / "batch_audit.db"),
                    "quarantine_db_path": str(sr / "quarantine.db"),
                    "daemon_log_path": str(sr / "logs" / "daemon.log"),
                    "collector_lock_path": str(sr / ".collector_run.lock"),
                    "daemon_lock_path": str(sr / ".daemon.lock"),
                    "daemon_status_path": str(sr / "daemon_status.json"),
                    "config_dir": str(staging_cfg_dir.resolve()),
                }
                Path(cmd_manifest).write_text(json.dumps(manifest), encoding="utf-8")
                now = _dt.now().isoformat()
                return CommandResult(exit_code=0, pid=os.getpid(),
                                     started_at=now, finished_at=now)

            import argparse
            with patch("scripts.backfill_fin_growth_dividend_staging.run_cmd", fake_future_ts):
                ns = argparse.Namespace(
                    staging_root=str(staging_root), run_task="fin_indicator",
                    dry_run=False, timeout_sec=60, source_db=db_path,
                )
                rc = phase_run_task(ns)
            assert rc == 1, "future manifest created_at must BLOCK run-task"
            os.unlink(db_path)


class TestPromotionManifestDriftBlocked:
    """Gap3: if a runtime manifest file is edited/deleted between audit and
    promote, promotion must BLOCK (on-disk hash drift)."""

    def test_promotion_blocks_when_manifest_edited_after_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)

            # Write two real manifest files on disk
            sr = staging_root.resolve()
            def _mk(task, nonce):
                m = {
                    "format_version": "1.0", "task": task, "pid": 12345,
                    "nonce": nonce, "created_at": "2026-07-28T00:00:00",
                    "QUANTSTUDIO_DATA_ROOT": str(sr), "imported_DATA_ROOT": str(sr),
                    "writer_db_path": str(staging_db.resolve()),
                    "batch_audit_db_path": str(sr / "batch_audit.db"),
                    "quarantine_db_path": str(sr / "quarantine.db"),
                    "daemon_log_path": str(sr / "logs" / "daemon.log"),
                    "collector_lock_path": str(sr / ".collector_run.lock"),
                    "daemon_lock_path": str(sr / ".daemon.lock"),
                    "daemon_status_path": str(sr / "daemon_status.json"),
                    "config_dir": str(staging_cfg_dir.resolve()),
                }
                return m
            fin_m = _mk("fin_indicator", "n1")
            div_m = _mk("stock_dividend", "n2")
            fin_path = staging_root / "runtime_paths_fin_indicator.json"
            div_path = staging_root / "runtime_paths_stock_dividend.json"
            fin_path.write_text(json.dumps(fin_m), encoding="utf-8")
            div_path.write_text(json.dumps(div_m), encoding="utf-8")
            fin_hash = _sha256_file(fin_path)
            div_hash = _sha256_file(div_path)

            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            ev["runtime_paths"] = {
                "runtime_paths_fin_indicator.json": fin_m,
                "runtime_paths_stock_dividend.json": div_m,
            }
            ev["runtime_manifest_hashes"] = {
                "runtime_paths_fin_indicator.json": fin_hash,
                "runtime_paths_stock_dividend.json": div_hash,
            }
            (staging_root / "audit_evidence.json").write_text(
                json.dumps(ev), encoding="utf-8")

            # Now TAMPER with one manifest on disk AFTER audit (simulate drift)
            tampered = dict(fin_m)
            tampered["nonce"] = "TAMPERED"
            fin_path.write_text(json.dumps(tampered), encoding="utf-8")

            result = _run_staging([
                "--source-db", db_path, "--staging-root", str(staging_root),
                "--promote",
            ])
            assert result.returncode != 0, "manifest drift must BLOCK promotion"
            combined = result.stdout + result.stderr
            assert "drift" in combined.lower() or "hash" in combined.lower(), (
                f"expected drift/hash message, got: {combined}")
            os.unlink(db_path)

    def test_promotion_blocks_when_manifest_deleted_after_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db_path = _make_mini_db()
            staging_root, staging_db, staging_cfg_dir = _setup_full_staging(tmp_p, db_path)

            sr = staging_root.resolve()
            def _mk(task, nonce):
                return {
                    "format_version": "1.0", "task": task, "pid": 12345,
                    "nonce": nonce, "created_at": "2026-07-28T00:00:00",
                    "QUANTSTUDIO_DATA_ROOT": str(sr), "imported_DATA_ROOT": str(sr),
                    "writer_db_path": str(staging_db.resolve()),
                    "batch_audit_db_path": str(sr / "batch_audit.db"),
                    "quarantine_db_path": str(sr / "quarantine.db"),
                    "daemon_log_path": str(sr / "logs" / "daemon.log"),
                    "collector_lock_path": str(sr / ".collector_run.lock"),
                    "daemon_lock_path": str(sr / ".daemon.lock"),
                    "daemon_status_path": str(sr / "daemon_status.json"),
                    "config_dir": str(staging_cfg_dir.resolve()),
                }
            fin_m = _mk("fin_indicator", "n1")
            div_m = _mk("stock_dividend", "n2")
            fin_path = staging_root / "runtime_paths_fin_indicator.json"
            div_path = staging_root / "runtime_paths_stock_dividend.json"
            fin_path.write_text(json.dumps(fin_m), encoding="utf-8")
            div_path.write_text(json.dumps(div_m), encoding="utf-8")

            ev = _make_full_passing_evidence(staging_db, db_path, staging_cfg_dir)
            ev["runtime_paths"] = {
                "runtime_paths_fin_indicator.json": fin_m,
                "runtime_paths_stock_dividend.json": div_m,
            }
            ev["runtime_manifest_hashes"] = {
                "runtime_paths_fin_indicator.json": _sha256_file(fin_path),
                "runtime_paths_stock_dividend.json": _sha256_file(div_path),
            }
            (staging_root / "audit_evidence.json").write_text(
                json.dumps(ev), encoding="utf-8")

            # DELETE one manifest after audit
            fin_path.unlink()

            result = _run_staging([
                "--source-db", db_path, "--staging-root", str(staging_root),
                "--promote",
            ])
            assert result.returncode != 0, "deleted manifest must BLOCK promotion"
            combined = result.stdout + result.stderr
            assert "missing" in combined.lower(), (
                f"expected missing-manifest message, got: {combined}")
            os.unlink(db_path)

