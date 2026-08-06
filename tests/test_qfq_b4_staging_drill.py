"""B-4 staging rehearsal tests (hermetic; no production DB access)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import duckdb
import pytest

from quantstudio.pipeline.qfq_reanchor_schema import init_sqlite_schema
from quantstudio.pipeline.qfq_schema_status import SchemaStatus, detect_schema_status
from scripts import qfq_b4_staging_drill as B4
from tests.test_qfq_schema_migration import _seed_full_legacy
from tests.test_qfq_event_discovery import STOCK_DIVIDEND_DDL, FT


def _legacy_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))
    try:
        _seed_full_legacy(conn)
        conn.execute(STOCK_DIVIDEND_DDL)
        conn.execute(
            "INSERT INTO stock_dividend "
            "(code, ex_date, record_date, cash_div, stk_div, div_rat, div_proc) "
            "VALUES ('600000', 1705276800000, 1704844800000, 0.5, 0.0, 0.5, '实施')"
        )
    finally:
        conn.close()
    return path


def _controlled_aux(path: Path) -> Path:
    with sqlite3.connect(str(path)) as conn:
        init_sqlite_schema(conn)
        conn.execute("INSERT INTO adj_factor(code,time,adj_factor) VALUES ('600000',?,1.0)", [FT])
        conn.execute("INSERT INTO fund_adj(code,time,adj_factor) VALUES ('510300',?,1.0)", [FT])
        conn.commit()
    return path


def test_exclusive_json_never_overwrites(tmp_path):
    target = tmp_path / "evidence.json"
    B4._write_json_exclusive(target, {"status": "first"})
    with pytest.raises(FileExistsError):
        B4._write_json_exclusive(target, {"status": "second"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "first"}


def test_normal_migration_drill_rolls_back_then_commits(tmp_path):
    db = _legacy_db(tmp_path / "normal" / "quantstudio.db")
    result = B4.run_normal_migration_drill(db, db.parent)
    assert result["dry_run"]["report_status"] == B4.REPORT_STATUS_DRY_RUN_COMPLETE
    assert result["rollback"]["report_status"] == B4.REPORT_STATUS_ROLLED_BACK
    assert result["apply"]["report_status"] == B4.REPORT_STATUS_MIGRATION_COMMITTED
    assert result["already_current"]["report_status"] == B4.REPORT_STATUS_ALREADY_CURRENT
    conn = duckdb.connect(str(db), read_only=True)
    try:
        assert detect_schema_status(conn) is SchemaStatus.COMPLETE_2_1
    finally:
        conn.close()


def test_committed_interruption_recovers_with_fresh_report(tmp_path):
    db = _legacy_db(tmp_path / "recovery" / "quantstudio.db")
    result = B4.run_recovery_migration_drill(db, db.parent)
    assert "COMMITTED" in result["committed_error"]
    assert result["recovery"]["report_status"] == B4.REPORT_STATUS_ALREADY_CURRENT
    assert Path(result["recovery"]["report_path"]).name == "report_02_fresh_already_current.json"


def test_offline_mcp_bootstrap_first_discover_stays_pre_cutover(tmp_path):
    db = _legacy_db(tmp_path / "normal" / "quantstudio.db")
    B4.run_normal_migration_drill(db, db.parent)
    aux = _controlled_aux(tmp_path / "normal" / "qfq_aux_b4.db")
    result = B4.run_offline_mcp_bootstrap_discover(db, aux)
    assert result["trigger_count_after_baseline"] == result["trigger_count_before"]
    assert result["trigger_count_after_replay"] == result["trigger_count_after_first_discover"]
    assert result["immediate_replay"] == {
        "dividend_triggers": 0,
        "stock_observation_new": 0,
        "etf_observation_new": 0,
        "revision_triggers": 0,
    }
    assert result["active_cutover_count"] == 0
    assert not any(result["mcp_gen1_counts"].values())
    assert result["mcp_cursor_rows"]
    assert all(row[3] == "xtquant-legacy" for row in result["mcp_cursor_rows"])


def test_preflight_is_zero_write_and_requires_exact_formal_paths(tmp_path, monkeypatch):
    formal_dir = tmp_path / "formal"
    formal_dir.mkdir()
    main = _legacy_db(formal_dir / "quantstudio.db")
    aux = _controlled_aux(formal_dir / "qfq_aux.db")
    main_before = B4.file_evidence(main)
    aux_before = B4.file_evidence(aux)

    monkeypatch.setattr(B4, "production_db_path", lambda: main)
    monkeypatch.setattr(
        B4,
        "lock_evidence",
        lambda: B4.LockEvidence(None, "no-status", True, True),
    )
    monkeypatch.setattr(B4, "_git_gate", lambda: {"head": "test", "staged_paths": []})
    monkeypatch.setattr(B4.shutil, "disk_usage", lambda _p: type("U", (), {"free": 1 << 50})())

    result = B4.preflight(main, aux, tmp_path / "out", "run1")
    assert result["formal_schema_status"] == "complete_2_0"
    assert result["production_ready"] is False
    assert B4.file_evidence(main) == main_before
    assert B4.file_evidence(aux) == aux_before

    wal = Path(str(aux) + "-wal")
    wal.write_bytes(b"uncheckpointed")
    with pytest.raises(B4.B4DrillError, match="transaction sidecar"):
        B4.preflight(main, aux, tmp_path / "out", "run_wal")
    wal.unlink()

    other = tmp_path / "other.db"
    other.write_bytes(main.read_bytes())
    with pytest.raises(B4.B4DrillError, match="configured production"):
        B4.preflight(other, aux, tmp_path / "out", "run2")
