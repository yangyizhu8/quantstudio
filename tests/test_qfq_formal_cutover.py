"""B-6 WP6 formal cutover authorization, nonce, prod-guard, and 6-fault-point
semantic-equivalence tests.

Covers the G1 acceptance criteria for the formal runner:
  * authorization manifest tamper detection (single byte / pre-SHA / self-hash)
  * nonce replay / marker deletion / index tamper detection
  * the formal prod-guard rejects symlink/junction/hardlink/case aliases and
    never imports qfq_schema_migration private symbols
  * the six activation fault points are semantically equivalent to staging
  * committed/dead-letter conservation; watermark unchanged
  * the formal runner has no production-collection import/call path
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import duckdb
import pytest

from quantstudio.pipeline.qfq_formal_authorization import (
    ALLOWED_GRANTS, AuthorizationError, AuthorizationScopeError,
    AuthorizationTamperError, NonceReplayError, compute_required_free_bytes,
    generate_test_manifest, hash_manifest_bytes, load_and_verify_manifest,
    manifest_carry_grant, manifest_grant_nonce, path_is_link_like, reserve_nonce,
    same_file, verify_formal_file_evidence,
)
from quantstudio.pipeline.qfq_formal_cutover import (
    FormalCutoverCommittedReportError, FormalCutoverError, FormalCutoverRefused,
    RECOVERY_STATUS_ALREADY_ACTIVE, _assert_production_authorized_which_matches_manifest,
    recover_already_active, run_formal_schema_migration,
)
from quantstudio.pipeline.qfq_cutover_activation import (
    _do_activate_in_txn, build_cutover_evidence,
)
from quantstudio.pipeline.qfq_cutover import create_cutover, transition_cutover
from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema, init_sqlite_schema
from quantstudio.pipeline.qfq_schema_migration import (
    LEGACY_MAIN_DB_2_0_FINGERPRINT, TARGET_MAIN_DB_2_1_FINGERPRINT,
    migrate_reanchor_2_0_to_2_1,
)
from quantstudio.pipeline.qfq_schema_status import SchemaStatus, detect_schema_status


# ---------------------------------------------------------------------------
# Shared staging DB fixture (mirrors tests/test_qfq_b6_activation.py:_staging).
# ---------------------------------------------------------------------------


def _seed_legacy_2_0_schema(conn):
    """Seed a DuckDB with the COMPLETE_2_0 legacy tables + a few price rows."""
    init_duckdb_schema(conn)
    for table in ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes"):
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" (code VARCHAR, time BIGINT, close DOUBLE)')
        conn.execute(f'INSERT INTO "{table}" VALUES (\'000001\', 1, 1.0)')


def _staging_db(tmp_path):
    db = tmp_path / "staging.duckdb"
    aux = tmp_path / "mcp-gen1.aux.db"
    c = duckdb.connect(str(db))
    _seed_legacy_2_0_schema(c)
    c.close()
    return db, aux


def _prepare_baseline_validated(db, aux, cutover_id="cut1"):
    c = duckdb.connect(str(db))
    c.execute("INSERT INTO qfq_discovery_baseline "
              "(cutover_id,price_source,source_generation,event_logical_key,applied_payload_hash,baselined_at,updated_at) "
              "VALUES (?, 'mcp','mcp-gen1','k1','h1',NOW(),NOW())", [cutover_id])
    create_cutover(c, cutover_id=cutover_id, price_source="mcp", source_generation="mcp-gen1",
                   schema_version="reanchor-2.1", baseline_version="qfq-detector-2.1",
                   aux_db_path=str(aux))
    transition_cutover(c, cutover_id=cutover_id, expected_status="planned", new_status="prepared")
    transition_cutover(c, cutover_id=cutover_id, expected_status="prepared", new_status="baseline_building")
    transition_cutover(c, cutover_id=cutover_id, expected_status="baseline_building", new_status="baseline_validated")
    c.close()
    ac = sqlite3.connect(str(aux)); init_sqlite_schema(ac); ac.commit(); ac.close()


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


# ===========================================================================
# 1. Disk formula (P2-2)
# ===========================================================================


class TestDiskFormula:
    def test_frozen_baseline_matches_plan(self):
        # Frozen baseline: main=14996746240 aux=2641793024 -> 91004735488
        assert compute_required_free_bytes(14996746240, 2641793024) == 91004735488

    def test_runtime_size_recomputed_not_hardcoded(self):
        # Different sizes must yield a different (recomputed) value.
        assert compute_required_free_bytes(100, 50) != 91004735488

    def test_reserve_floor_is_10gib(self):
        assert compute_required_free_bytes(1, 1) == 5 + 2 + 10 * (1024 ** 3)

    def test_reserve_20pct_when_above_floor(self):
        # main+aux = 100 GiB -> 20% = 20 GiB > 10 GiB floor
        big = 60 * (1024 ** 3)
        assert compute_required_free_bytes(big, 40 * (1024 ** 3)) == 5 * big + 2 * 40 * (1024 ** 3) + 20 * (1024 ** 3)


# ===========================================================================
# 2. Authorization manifest tamper detection
# ===========================================================================


class TestAuthorizationTamper:
    def test_load_valid_manifest(self, tmp_path):
        auth_root = tmp_path / "auth_root"
        main = tmp_path / "main.db"
        main.write_bytes(b"main")
        aux = tmp_path / "qfq_aux.db"
        aux.write_bytes(b"aux")
        path, sha = generate_test_manifest(
            authorization_root=auth_root, cutover_id="c1",
            formal_main_path=main, formal_aux_path=aux,
            git_commit_sha="0" * 40, config_sha="c" * 64,
            checkout_root=tmp_path, grants={"wp6_formal_cutover": True},
            aux_db_path=aux)
        m = load_and_verify_manifest(path, sha)
        assert m["cutover_id"] == "c1"
        assert m["watermark_release_authorized"] is False
        assert manifest_carry_grant(m, "wp6_formal_cutover")

    def test_missing_manifest_rejected(self, tmp_path):
        with pytest.raises((FileNotFoundError, AuthorizationError)):
            load_and_verify_manifest(tmp_path / "nope.json", "0" * 64)

    def test_raw_sha_mismatch_rejected(self, tmp_path):
        auth_root = tmp_path / "auth_root"
        main = tmp_path / "main.db"; main.write_bytes(b"main")
        aux = tmp_path / "qfq_aux.db"; aux.write_bytes(b"aux")
        path, sha = generate_test_manifest(
            authorization_root=auth_root, cutover_id="c1",
            formal_main_path=main, formal_aux_path=aux,
            git_commit_sha="0" * 40, config_sha="c" * 64,
            checkout_root=tmp_path, grants={"wp6_formal_cutover": True},
            aux_db_path=aux)
        with pytest.raises(AuthorizationTamperError):
            load_and_verify_manifest(path, "a" * 64)

    def test_single_byte_tamper_rejected(self, tmp_path):
        auth_root = tmp_path / "auth_root"
        main = tmp_path / "main.db"; main.write_bytes(b"main")
        aux = tmp_path / "qfq_aux.db"; aux.write_bytes(b"aux")
        path, sha = generate_test_manifest(
            authorization_root=auth_root, cutover_id="c1",
            formal_main_path=main, formal_aux_path=aux,
            git_commit_sha="0" * 40, config_sha="c" * 64,
            checkout_root=tmp_path, grants={"wp6_formal_cutover": True},
            aux_db_path=aux)
        raw = bytearray(path.read_bytes())
        raw[100] ^= 0xFF  # flip a byte deep in the JSON
        path.write_bytes(bytes(raw))
        with pytest.raises(AuthorizationTamperError):
            load_and_verify_manifest(path, sha)

    def test_watermark_release_true_rejected(self, tmp_path):
        auth_root = tmp_path / "auth_root"
        main = tmp_path / "main.db"; main.write_bytes(b"main")
        aux = tmp_path / "qfq_aux.db"; aux.write_bytes(b"aux")
        path, sha = generate_test_manifest(
            authorization_root=auth_root, cutover_id="c1",
            formal_main_path=main, formal_aux_path=aux,
            git_commit_sha="0" * 40, config_sha="c" * 64,
            checkout_root=tmp_path, grants={"wp6_formal_cutover": True},
            aux_db_path=aux)
        m = json.loads(path.read_text())
        m["watermark_release_authorized"] = True
        raw = json.dumps(m, ensure_ascii=False, sort_keys=True).encode("utf-8")
        path.write_bytes(raw)
        new_sha = hash_manifest_bytes(raw)
        with pytest.raises(AuthorizationTamperError):
            load_and_verify_manifest(path, new_sha)

    def test_self_declared_hash_rejected(self, tmp_path):
        auth_root = tmp_path / "auth_root"
        main = tmp_path / "main.db"; main.write_bytes(b"main")
        aux = tmp_path / "qfq_aux.db"; aux.write_bytes(b"aux")
        path, sha = generate_test_manifest(
            authorization_root=auth_root, cutover_id="c1",
            formal_main_path=main, formal_aux_path=aux,
            git_commit_sha="0" * 40, config_sha="c" * 64,
            checkout_root=tmp_path, grants={"wp6_formal_cutover": True},
            aux_db_path=aux)
        m = json.loads(path.read_text())
        m["self_declared_sha256"] = sha  # a self-declared hash must never satisfy
        raw = json.dumps(m, ensure_ascii=False, sort_keys=True).encode("utf-8")
        path.write_bytes(raw)
        new_sha = hash_manifest_bytes(raw)
        with pytest.raises(AuthorizationTamperError):
            load_and_verify_manifest(path, new_sha)

    def test_expected_sha_not_hex_rejected(self, tmp_path):
        auth_root = tmp_path / "auth_root"
        main = tmp_path / "main.db"; main.write_bytes(b"main")
        aux = tmp_path / "qfq_aux.db"; aux.write_bytes(b"aux")
        path, sha = generate_test_manifest(
            authorization_root=auth_root, cutover_id="c1",
            formal_main_path=main, formal_aux_path=aux,
            git_commit_sha="0" * 40, config_sha="c" * 64,
            checkout_root=tmp_path, grants={"wp6_formal_cutover": True},
            aux_db_path=aux)
        with pytest.raises(AuthorizationError):
            load_and_verify_manifest(path, "XYZ" * 21)


# ===========================================================================
# 3. Nonce replay / marker deletion / index tamper (plan §3.1.1)
# ===========================================================================


class TestNonceLedger:
    def _make_manifest(self, tmp_path, grant="wp6_formal_cutover"):
        auth_root = tmp_path / "auth_root"
        main = tmp_path / "main.db"; main.write_bytes(b"main")
        aux = tmp_path / "qfq_aux.db"; aux.write_bytes(b"aux")
        path, sha = generate_test_manifest(
            authorization_root=auth_root, cutover_id="c1",
            formal_main_path=main, formal_aux_path=aux,
            git_commit_sha="0" * 40, config_sha="c" * 64,
            checkout_root=tmp_path, grants={grant: True}, aux_db_path=aux)
        return auth_root, path, sha

    def test_first_reserve_succeeds(self, tmp_path):
        auth_root, path, sha = self._make_manifest(tmp_path)
        marker = reserve_nonce(auth_root, "wp6_formal_cutover", "nonce-aaaa-bbbb-cccc",
                               manifest_raw_sha=sha, cutover_id="c1", commit_sha="0" * 40,
                               pid=os.getpid(), create_time=0.0)
        assert len(marker) == 64

    def test_replay_same_nonce_rejected(self, tmp_path):
        auth_root, path, sha = self._make_manifest(tmp_path)
        reserve_nonce(auth_root, "wp6_formal_cutover", "nonce-aaaa-bbbb-cccc",
                      manifest_raw_sha=sha, cutover_id="c1", commit_sha="0" * 40,
                      pid=os.getpid(), create_time=0.0)
        with pytest.raises(NonceReplayError):
            reserve_nonce(auth_root, "wp6_formal_cutover", "nonce-aaaa-bbbb-cccc",
                          manifest_raw_sha=sha, cutover_id="c1", commit_sha="0" * 40,
                          pid=os.getpid(), create_time=0.0)

    def test_marker_deletion_still_rejects_replay(self, tmp_path):
        """Plan §3.1.1: O_EXCL alone cannot detect marker deletion; the index
        chain must reject replay even when the marker file is removed."""
        auth_root, path, sha = self._make_manifest(tmp_path)
        reserve_nonce(auth_root, "wp6_formal_cutover", "nonce-aaaa-bbbb-cccc",
                      manifest_raw_sha=sha, cutover_id="c1", commit_sha="0" * 40,
                      pid=os.getpid(), create_time=0.0)
        # Simulate an attacker deleting the marker file.
        marker = auth_root / "consumed" / "wp6_formal_cutover" / "nonce-aaaa-bbbb-cccc.json"
        marker.unlink()
        assert not marker.exists()
        # Replay must STILL be blocked by the index chain.
        with pytest.raises(NonceReplayError):
            reserve_nonce(auth_root, "wp6_formal_cutover", "nonce-aaaa-bbbb-cccc",
                          manifest_raw_sha=sha, cutover_id="c1", commit_sha="0" * 40,
                          pid=os.getpid(), create_time=0.0)

    def test_index_tamper_blocks(self, tmp_path):
        auth_root, path, sha = self._make_manifest(tmp_path)
        reserve_nonce(auth_root, "wp6_formal_cutover", "nonce-aaaa-bbbb-cccc",
                      manifest_raw_sha=sha, cutover_id="c1", commit_sha="0" * 40,
                      pid=os.getpid(), create_time=0.0)
        # Tamper with the index digest file.
        digest = auth_root / "consumed" / "wp6_formal_cutover" / "index_digest.json"
        digest.write_text(json.dumps({"digest": "tampered", "entry_count": 1}))
        with pytest.raises(NonceReplayError):
            reserve_nonce(auth_root, "wp6_formal_cutover", "nonce-dddd-eeee-ffff",
                          manifest_raw_sha=sha, cutover_id="c1", commit_sha="0" * 40,
                          pid=os.getpid(), create_time=0.0)

    def test_different_grant_isolated_nonce(self, tmp_path):
        """WP6 and canary use independent nonces; consuming one does not block the other."""
        auth_root_main = tmp_path / "auth_root_main"
        auth_root_main.mkdir()
        main = tmp_path / "main.db"; main.write_bytes(b"main")
        aux = tmp_path / "qfq_aux.db"; aux.write_bytes(b"aux")
        path, sha = generate_test_manifest(
            authorization_root=auth_root_main, cutover_id="c1",
            formal_main_path=main, formal_aux_path=aux,
            git_commit_sha="0" * 40, config_sha="c" * 64,
            checkout_root=tmp_path, grants={"wp6_formal_cutover": True, "wp7_held_canary": True},
            aux_db_path=aux)
        m = load_and_verify_manifest(path, sha)
        wp6_nonce = manifest_grant_nonce(m, "wp6_formal_cutover")
        wp7_nonce = manifest_grant_nonce(m, "wp7_held_canary")
        assert wp6_nonce != wp7_nonce
        reserve_nonce(auth_root_main, "wp6_formal_cutover", wp6_nonce,
                      manifest_raw_sha=sha, cutover_id="c1", commit_sha="0" * 40,
                      pid=os.getpid(), create_time=0.0)
        # wp7 nonce is still consumable (independent ledger dir).
        reserve_nonce(auth_root_main, "wp7_held_canary", wp7_nonce,
                      manifest_raw_sha=sha, cutover_id="c1", commit_sha="0" * 40,
                      pid=os.getpid(), create_time=0.0)


# ===========================================================================
# 4. Formal prod-guard (P2-1): independent, no migration private imports
# ===========================================================================


class TestFormalProdGuard:
    def test_module_does_not_import_migration_private(self):
        """G1 acceptance: the formal cutover module must NOT import the
        migration private state-machine symbols (_do_migrate_in_txn,
        _ReportReservation, _assert_not_production, _assert_allowed_root,
        _is_production_db).  Read-only helpers/constants are explicitly allowed
        (method 2).  We check the actual import statement, not docstrings."""
        import ast
        import quantstudio.pipeline.qfq_formal_cutover as mod
        tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "qfq_schema_migration" in node.module:
                for alias in node.names:
                    imported.add(alias.name)
        forbidden = {"_do_migrate_in_txn", "_ReportReservation",
                     "_assert_not_production", "_assert_allowed_root", "_is_production_db"}
        leaked = forbidden & imported
        assert not leaked, f"formal module imports migration private symbols: {leaked}"

    def test_no_production_collection_imports(self):
        """G1 acceptance: formal runner must not import ResidentCollector.run_once /
        execute_task / qfq_run_post_ingest / writer.advance_watermark.  Checked
        via the import statements, not docstrings."""
        import ast
        for modname in ("quantstudio.pipeline.qfq_formal_cutover",
                        "quantstudio.pipeline.qfq_formal_cutover_cli"):
            mod = __import__(modname, fromlist=["x"])
            tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    mod_src = node.module or ""
                    for alias in node.names:
                        full = f"{mod_src}.{alias.name}" if mod_src else alias.name
                        for forbidden in ("run_once", "execute_task", "qfq_run_post_ingest",
                                          "advance_watermark"):
                            assert not full.endswith(forbidden) or forbidden == "advance_watermark" and "writer" not in full, \
                                f"{modname} imports production-collection symbol: {full}"

    def test_guard_rejects_non_production_path(self, tmp_path, monkeypatch):
        """The formal guard requires the live path to BE the configured formal path."""
        auth_root = tmp_path / "auth_root"
        main = tmp_path / "formal.db"; main.write_bytes(b"formal")
        aux = tmp_path / "qfq_aux.db"; aux.write_bytes(b"aux")
        path, sha = generate_test_manifest(
            authorization_root=auth_root, cutover_id="c1",
            formal_main_path=main, formal_aux_path=aux,
            git_commit_sha="0" * 40, config_sha="c" * 64,
            checkout_root=tmp_path, grants={"wp6_formal_cutover": True},
            aux_db_path=aux)
        m = load_and_verify_manifest(path, sha)
        from quantstudio import _paths
        # Configure formal path to something else; live main is not the configured formal path.
        monkeypatch.setattr(_paths, "db_path", lambda: str(tmp_path / "other.db"))
        with pytest.raises(FormalCutoverRefused):
            _assert_production_authorized_which_matches_manifest(
                m, main_path=main, aux_path=aux)


# ===========================================================================
# 5. Six activation fault points: formal core == staging core semantics
# ===========================================================================


class TestActivationFaultMatrixEquivalence:
    """The formal runner reuses _do_activate_in_txn (the shared core), so the
    six fault points must be byte-for-byte equivalent to staging.  This test
    drives the shared core directly with the staging fixture."""

    FAULT_POINTS = ("after_retirement", "after_pointer_delete", "after_new_status",
                    "after_pointer_insert", "before_commit")

    def _setup(self, tmp_path):
        db, aux = _staging_db(tmp_path)
        _prepare_baseline_validated(db, aux)
        c = duckdb.connect(str(db))
        _insert_legacy_rows(c)
        build_cutover_evidence(c, cutover_id="cut1", main_db_path=db,
                               output_path=tmp_path / "evidence.json")
        return c, db, aux

    def _snapshot_state(self, c):
        """Capture the 8 rollback-comparison tables (plan §3.7.1)."""
        snap = {}
        snap["cutover"] = c.execute("SELECT cutover_id, status FROM qfq_source_cutover WHERE cutover_id='cut1'").fetchone()
        snap["active_count"] = c.execute("SELECT COUNT(*) FROM qfq_active_cutover").fetchone()[0]
        snap["legacy_triggers"] = dict(c.execute(
            "SELECT status, COUNT(*) FROM qfq_trigger_queue WHERE price_source='xtquant' GROUP BY status").fetchall())
        snap["legacy_intents"] = dict(c.execute(
            "SELECT status, COUNT(*) FROM qfq_watermark_intent WHERE source='xtquant' GROUP BY status").fetchall())
        snap["legacy_cycles"] = dict(c.execute(
            "SELECT status, COUNT(*) FROM qfq_cycle_run WHERE price_source='xtquant' GROUP BY status").fetchall())
        snap["committed"] = c.execute("SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='committed'").fetchone()[0]
        snap["dead_letter"] = c.execute("SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='dead_letter'").fetchone()[0]
        from quantstudio.pipeline.qfq_snapshot_evidence import table_evidence
        snap["watermark"] = table_evidence(c, "source_watermark")["content_sha256"]
        return snap

    @pytest.mark.parametrize("fault", FAULT_POINTS)
    def test_pre_commit_fault_rolls_back_all_tables(self, tmp_path, fault):
        c, db, aux = self._setup(tmp_path)
        from quantstudio.pipeline.qfq_snapshot_evidence import table_evidence
        before = self._snapshot_state(c)
        # _snapshot_state stores watermark as a content_sha256 string; recompute
        # the full table_evidence dict that the core compares against.
        pre_wm = table_evidence(c, "source_watermark")
        with pytest.raises(RuntimeError, match=fault):
            _do_activate_in_txn(c, cutover_id="cut1", price_source="mcp",
                                expected_old=None, fault_at=fault,
                                pre_wm=pre_wm, committed_before=before["committed"],
                                current_id=None)
        after = self._snapshot_state(c)
        # All 8 rollback-comparison tables must equal the pre-transaction state.
        assert after == before, f"fault {fault}: state not fully rolled back"
        c.close()

    def test_after_commit_before_report_is_committed(self, tmp_path):
        c, db, aux = self._setup(tmp_path)
        committed_before = c.execute("SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='committed'").fetchone()[0]
        from quantstudio.pipeline.qfq_snapshot_evidence import table_evidence
        pre_wm = table_evidence(c, "source_watermark")
        # after_commit_before_report raises after COMMIT, leaving the txn durable.
        with pytest.raises(RuntimeError, match="after_commit_before_report"):
            _do_activate_in_txn(c, cutover_id="cut1", price_source="mcp",
                                expected_old=None, fault_at="after_commit_before_report",
                                pre_wm=pre_wm, committed_before=committed_before, current_id=None)
        # The cutover is now active and the pointer is correct (durable commit).
        assert c.execute("SELECT status FROM qfq_source_cutover WHERE cutover_id='cut1'").fetchone()[0] == "active"
        assert c.execute("SELECT COUNT(*) FROM qfq_active_cutover WHERE price_source='mcp'").fetchone()[0] == 1
        c.close()


# ===========================================================================
# 6. committed/dead-letter conservation + watermark unchanged
# ===========================================================================


class TestConservation:
    def test_committed_and_dead_letter_preserved_on_activation(self, tmp_path):
        db, aux = _staging_db(tmp_path)
        _prepare_baseline_validated(db, aux)
        c = duckdb.connect(str(db))
        _insert_legacy_rows(c)
        build_cutover_evidence(c, cutover_id="cut1", main_db_path=db,
                               output_path=tmp_path / "evidence.json")
        from quantstudio.pipeline.qfq_snapshot_evidence import table_evidence
        pre_wm = table_evidence(c, "source_watermark")
        committed_before = c.execute("SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='committed'").fetchone()[0]
        result = _do_activate_in_txn(c, cutover_id="cut1", price_source="mcp",
                                     expected_old=None, fault_at=None,
                                     pre_wm=pre_wm, committed_before=committed_before,
                                     current_id=None)
        assert result["retired_triggers"] == 3  # pending/scheduled/in_progress
        assert result["interrupted_cycles"] == 1
        assert result["superseded_intents"] == 1
        # committed/dead_letter preserved
        assert c.execute("SELECT status FROM qfq_trigger_queue WHERE trigger_id='t-c'").fetchone()[0] == "committed"
        assert c.execute("SELECT status FROM qfq_trigger_queue WHERE trigger_id='t-d'").fetchone()[0] == "dead_letter"
        assert c.execute("SELECT status FROM qfq_trigger_queue WHERE trigger_id='t-p'").fetchone()[0] == "superseded"
        # watermark unchanged
        assert table_evidence(c, "source_watermark")["content_sha256"] == pre_wm["content_sha256"]
        c.close()
