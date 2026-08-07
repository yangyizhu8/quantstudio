"""WP7-E2 held-canary dry_run parameter tests (CodeBuddy 6 red lines).

Covers:
  (a) dry_run=True → orchestrator NOT constructed (monkeypatch intercept);
  (b) run_post_ingest zero-called in dry_run;
  (c) dry_run gate failure (missing exit evidence) same rejection as real path;
  (d) dry_run snapshot is read-only (no write operations).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantstudio.pipeline.qfq_formal_canary import (
    FormalCanaryError, run_held_canary,
)
from quantstudio.pipeline.qfq_formal_authorization import (
    WP7_E3_GRANT, generate_test_manifest, hash_manifest_bytes,
)
from quantstudio.pipeline.qfq_formal_cutover import _write_handoff, write_exit_evidence


def _make_valid_handoff_and_exit(tmp_path):
    """Write a valid handoff + exit evidence pair in tmp_path."""
    _write_handoff(handoff_dir=tmp_path, payload={
        "kind": "handoff", "cutover_id": "c1", "price_source": "mcp",
        "source_generation": "mcp-gen1", "aux_db_path": str(tmp_path / "aux.db"),
        "watermark_release_authorized": False, "child_pid": 999999,
        "connections_closed": True, "locks_release_pending": True,
    })
    raw_sha = hash_manifest_bytes((tmp_path / "formal_cutover_handoff.json").read_bytes())
    write_exit_evidence(handoff_dir=tmp_path, handoff_raw_sha=raw_sha,
                        child_pid=999999, child_create_time=0.0, exit_code=0,
                        locks_released_verified=True, descendant_scan=[])
    return raw_sha


def _make_wp7_e2_manifest(tmp_path):
    """Build a TEST_ONLY manifest carrying only wp7_held_canary grant."""
    import hashlib
    main = tmp_path / "main.db"; main.write_bytes(b"main")
    aux = tmp_path / "qfq_aux.db"; aux.write_bytes(b"aux")
    payload = {
        "schema": "TEST_ONLY", "version": 1,
        "git_commit_sha": "0" * 40, "checkout_canonical_root": str(tmp_path),
        "formal_main_canonical_path": str(main.resolve()),
        "formal_aux_canonical_path": str(aux.resolve()),
        "formal_main_sha256": hashlib.sha256(b"main").hexdigest(),
        "formal_aux_sha256": hashlib.sha256(b"aux").hexdigest(),
        "formal_main_size": 4, "formal_aux_size": 3,
        "formal_main_mtime_ns": main.stat().st_mtime_ns,
        "formal_aux_mtime_ns": aux.stat().st_mtime_ns,
        "config_sha": "c" * 64, "cutover_id": "c1",
        "price_source": "mcp", "source_generation": "mcp-gen1",
        "aux_db_path": str(aux.resolve()),
        "operation_grants": {"wp7_held_canary": {"nonce": "n" * 32}},
        "maintenance_window_id": "mw", "issuer": "TEST_ONLY", "approved_by": "TEST_ONLY",
        "watermark_release_authorized": False,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
    sha = hashlib.sha256(raw).hexdigest()
    path = tmp_path / "auth_root" / "c1" / "auth.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    return str(path), sha


class TestDryRunNoOrchestrator:
    """(a) dry_run=True → orchestrator NOT constructed; (b) run_post_ingest zero-called."""

    def _setup_staging_db(self, tmp_path, monkeypatch):
        """Create a minimal valid staging DB and point _paths.db_path at it."""
        from quantstudio import _paths
        db = tmp_path / "staging.duckdb"
        monkeypatch.setattr(_paths, "db_path", lambda: str(db))
        c = duckdb.connect(str(db))
        for t, cols in [("qfq_discovery_baseline", "(cutover_id VARCHAR)"),
                        ("qfq_trigger_queue", "(source_generation VARCHAR)"),
                        ("qfq_watermark_intent", "(source_generation VARCHAR)"),
                        ("source_watermark", "(last_date BIGINT)"),
                        ("stock_daily", "(code VARCHAR, time BIGINT, close DOUBLE)"),
                        ("stock_minutes", "(code VARCHAR, time BIGINT, close DOUBLE)"),
                        ("etf_daily", "(code VARCHAR, time BIGINT, close DOUBLE)"),
                        ("etf_minutes", "(code VARCHAR, time BIGINT, close DOUBLE)")]:
            c.execute(f'CREATE TABLE IF NOT EXISTS "{t}" {cols}')
        c.close()

    def test_dry_run_does_not_construct_orchestrator(self, tmp_path, monkeypatch):
        """In dry_run, QFQResidentOrchestrator must never be instantiated."""
        manifest_path, manifest_sha = _make_wp7_e2_manifest(tmp_path)
        _make_valid_handoff_and_exit(tmp_path)
        self._setup_staging_db(tmp_path, monkeypatch)
        monkeypatch.setattr("quantstudio.pipeline.qfq_formal_canary.reserve_nonce",
                            lambda *a, **kw: "fake_marker_sha_" + "0" * 48)
        with patch("quantstudio.pipeline.qfq_formal_canary.AuxDbRouter") as mock_router:
            mock_router.from_config_dir.return_value.path_for.return_value = tmp_path / "aux.db"
            import quantstudio.pipeline.qfq_resident_orchestrator as orch_mod
            with patch.object(orch_mod, "QFQResidentOrchestrator") as mock_orch:
                mock_orch.side_effect = AssertionError("orchestrator constructed in dry_run!")
                result = run_held_canary(
                    authorization_path=manifest_path, authorization_sha256=manifest_sha,
                    handoff_dir=tmp_path, config_dir=tmp_path, dry_run=True)
        assert result["dry_run"] is True
        assert result["gates_passed"]["handoff_raw_sha_match"] is True
        assert result["gates_passed"]["locks_released_verified"] is True
        mock_orch.assert_not_called()

    def test_dry_run_does_not_call_run_post_ingest(self, tmp_path, monkeypatch):
        """run_post_ingest must never be called in dry_run."""
        manifest_path, manifest_sha = _make_wp7_e2_manifest(tmp_path)
        _make_valid_handoff_and_exit(tmp_path)
        self._setup_staging_db(tmp_path, monkeypatch)
        monkeypatch.setattr("quantstudio.pipeline.qfq_formal_canary.reserve_nonce",
                            lambda *a, **kw: "fake_marker_sha_" + "0" * 48)
        with patch("quantstudio.pipeline.qfq_formal_canary.AuxDbRouter") as mock_router:
            mock_router.from_config_dir.return_value.path_for.return_value = tmp_path / "aux.db"
            import quantstudio.pipeline.qfq_resident_orchestrator as orch_mod
            with patch.object(orch_mod, "QFQResidentOrchestrator") as mock_orch:
                mock_instance = mock_orch.return_value
                mock_instance.run_post_ingest.side_effect = AssertionError("run_post_ingest called in dry_run!")
                result = run_held_canary(
                    authorization_path=manifest_path, authorization_sha256=manifest_sha,
                    handoff_dir=tmp_path, config_dir=tmp_path, dry_run=True)
        mock_instance.run_post_ingest.assert_not_called()


class TestDryRunGateRejection:
    """(c) dry_run gate failures produce the same rejection as the real path."""

    def test_dry_run_rejects_missing_exit_evidence(self, tmp_path, monkeypatch):
        """A missing exit evidence is rejected in dry_run, same as real path."""
        manifest_path, manifest_sha = _make_wp7_e2_manifest(tmp_path)
        # Write handoff but NOT exit evidence.
        _write_handoff(handoff_dir=tmp_path, payload={
            "kind": "handoff", "watermark_release_authorized": False, "child_pid": None})
        from quantstudio import _paths
        monkeypatch.setattr(_paths, "db_path", lambda: str(tmp_path / "main.db"))
        with pytest.raises(FormalCanaryError, match="exit evidence missing"):
            run_held_canary(authorization_path=manifest_path, authorization_sha256=manifest_sha,
                            handoff_dir=tmp_path, config_dir=tmp_path, dry_run=True)

    def test_dry_run_rejects_handoff_sha_mismatch(self, tmp_path, monkeypatch):
        """A handoff SHA mismatch is rejected in dry_run."""
        manifest_path, manifest_sha = _make_wp7_e2_manifest(tmp_path)
        _write_handoff(handoff_dir=tmp_path, payload={
            "kind": "handoff", "watermark_release_authorized": False, "child_pid": None})
        # Exit evidence with WRONG sha.
        write_exit_evidence(handoff_dir=tmp_path, handoff_raw_sha="0" * 64,
                            child_pid=0, child_create_time=0.0, exit_code=0,
                            locks_released_verified=True, descendant_scan=[])
        from quantstudio import _paths
        monkeypatch.setattr(_paths, "db_path", lambda: str(tmp_path / "main.db"))
        with pytest.raises(FormalCanaryError, match="handoff raw SHA mismatch"):
            run_held_canary(authorization_path=manifest_path, authorization_sha256=manifest_sha,
                            handoff_dir=tmp_path, config_dir=tmp_path, dry_run=True)

    def test_dry_run_rejects_locks_not_released(self, tmp_path, monkeypatch):
        """exit evidence with locks_released_verified=False is rejected."""
        manifest_path, manifest_sha = _make_wp7_e2_manifest(tmp_path)
        _write_handoff(handoff_dir=tmp_path, payload={
            "kind": "handoff", "watermark_release_authorized": False, "child_pid": None})
        raw_sha = hash_manifest_bytes((tmp_path / "formal_cutover_handoff.json").read_bytes())
        # Write exit evidence with locks_released_verified=False
        import json as _json
        (tmp_path / "formal_runner_exit_evidence.json").write_text(_json.dumps({
            "handoff_raw_sha256": raw_sha, "locks_released_verified": False,
        }))
        from quantstudio import _paths
        monkeypatch.setattr(_paths, "db_path", lambda: str(tmp_path / "main.db"))
        with pytest.raises(FormalCanaryError, match="locks NOT released"):
            run_held_canary(authorization_path=manifest_path, authorization_sha256=manifest_sha,
                            handoff_dir=tmp_path, config_dir=tmp_path, dry_run=True)


class TestDryRunSnapshotReadOnly:
    """(d) dry_run snapshot is read-only — no INSERT/UPDATE/DELETE."""

    def test_dry_run_uses_read_only_connection(self, tmp_path, monkeypatch):
        """dry_run must open a read_only=True connection (no writes possible)."""
        manifest_path, manifest_sha = _make_wp7_e2_manifest(tmp_path)
        _make_valid_handoff_and_exit(tmp_path)
        # Build a minimal staging DB so the read-only snapshot queries work.
        from quantstudio import _paths
        db = tmp_path / "staging.duckdb"
        monkeypatch.setattr(_paths, "db_path", lambda: str(db))
        monkeypatch.setattr("quantstudio.pipeline.qfq_formal_canary.reserve_nonce",
                            lambda *a, **kw: "fake_marker_sha_" + "0" * 48)
        c = duckdb.connect(str(db))
        c.execute("CREATE TABLE qfq_discovery_baseline (cutover_id VARCHAR, event_logical_key VARCHAR, applied_payload_hash VARCHAR, price_source VARCHAR, source_generation VARCHAR)")
        c.execute("CREATE TABLE qfq_trigger_queue (source_generation VARCHAR)")
        c.execute("CREATE TABLE qfq_watermark_intent (source_generation VARCHAR)")
        c.execute("CREATE TABLE source_watermark (last_date BIGINT)")
        for t in ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes"):
            c.execute(f'CREATE TABLE "{t}" (code VARCHAR, time BIGINT, close DOUBLE)')
        c.close()
        # Intercept duckdb.connect to verify read_only=True is used.
        original_connect = duckdb.connect
        connect_args = []
        def spy_connect(*args, **kwargs):
            connect_args.append(kwargs.get("read_only", False))
            return original_connect(*args, **kwargs)
        with patch("quantstudio.pipeline.qfq_formal_canary.duckdb.connect", side_effect=spy_connect):
            with patch("quantstudio.pipeline.qfq_formal_canary.AuxDbRouter") as mock_router:
                mock_router.from_config_dir.return_value.path_for.return_value = tmp_path / "aux.db"
                result = run_held_canary(
                    authorization_path=manifest_path, authorization_sha256=manifest_sha,
                    handoff_dir=tmp_path, config_dir=tmp_path, dry_run=True)
        # In dry_run, the only connection opened must be read_only=True.
        assert all(ro is True for ro in connect_args), \
            f"dry_run opened non-read-only connection: {connect_args}"
        assert result["dry_run"] is True
        assert "before_snapshot" in result


class TestMainDbOverride:
    """main_db_override parameter tests (CodeBuddy 5 red lines)."""

    def test_dry_run_with_override_resolves_to_override_path(self, tmp_path, monkeypatch):
        """(a) dry_run=True + override → main_db_resolved = override path (staging copy)."""
        manifest_path, manifest_sha = _make_wp7_e2_manifest(tmp_path)
        _make_valid_handoff_and_exit(tmp_path)
        # Build a staging copy DB at an identifiable path.
        staging_db = tmp_path / "staging_copy" / "quantstudio.db"
        staging_db.parent.mkdir()
        c = duckdb.connect(str(staging_db))
        for t, cols in [("qfq_discovery_baseline", "(cutover_id VARCHAR)"),
                        ("qfq_trigger_queue", "(source_generation VARCHAR)"),
                        ("qfq_watermark_intent", "(source_generation VARCHAR)"),
                        ("source_watermark", "(last_date BIGINT)"),
                        ("stock_daily", "(code VARCHAR, time BIGINT, close DOUBLE)"),
                        ("stock_minutes", "(code VARCHAR, time BIGINT, close DOUBLE)"),
                        ("etf_daily", "(code VARCHAR, time BIGINT, close DOUBLE)"),
                        ("etf_minutes", "(code VARCHAR, time BIGINT, close DOUBLE)")]:
            c.execute(f'CREATE TABLE IF NOT EXISTS "{t}" {cols}')
        c.close()
        monkeypatch.setattr("quantstudio.pipeline.qfq_formal_canary.reserve_nonce",
                            lambda *a, **kw: "fake_marker_sha_" + "0" * 48)
        with patch("quantstudio.pipeline.qfq_formal_canary.AuxDbRouter") as mock_router:
            mock_router.from_config_dir.return_value.path_for.return_value = tmp_path / "aux.db"
            result = run_held_canary(
                authorization_path=manifest_path, authorization_sha256=manifest_sha,
                handoff_dir=tmp_path, config_dir=tmp_path, dry_run=True,
                main_db_override=str(staging_db))
        assert result["main_db_resolved"] == str(staging_db.resolve())
        assert "staging_copy" in result["main_db_resolved"]

    def test_real_execution_with_override_is_rejected(self, tmp_path, monkeypatch):
        """(b) dry_run=False + override → raises (real execution must not redirect)."""
        manifest_path, manifest_sha = _make_wp7_e2_manifest(tmp_path)
        _make_valid_handoff_and_exit(tmp_path)
        from quantstudio import _paths
        monkeypatch.setattr(_paths, "db_path", lambda: str(tmp_path / "main.db"))
        monkeypatch.setattr("quantstudio.pipeline.qfq_formal_canary.reserve_nonce",
                            lambda *a, **kw: "fake_marker_sha_" + "0" * 48)
        with pytest.raises(FormalCanaryError, match="main_db_override is only allowed with dry_run=True"):
            run_held_canary(
                authorization_path=manifest_path, authorization_sha256=manifest_sha,
                handoff_dir=tmp_path, config_dir=tmp_path, dry_run=False,
                main_db_override=str(tmp_path / "staging.db"))

    def test_default_none_resolves_to_formal_path(self, tmp_path, monkeypatch):
        """(c) override=None (default) → main_db resolves to _configured_formal_main()."""
        manifest_path, manifest_sha = _make_wp7_e2_manifest(tmp_path)
        _make_valid_handoff_and_exit(tmp_path)
        formal_db = tmp_path / "formal.duckdb"
        c = duckdb.connect(str(formal_db))
        for t, cols in [("qfq_discovery_baseline", "(cutover_id VARCHAR)"),
                        ("qfq_trigger_queue", "(source_generation VARCHAR)"),
                        ("qfq_watermark_intent", "(source_generation VARCHAR)"),
                        ("source_watermark", "(last_date BIGINT)"),
                        ("stock_daily", "(code VARCHAR, time BIGINT, close DOUBLE)"),
                        ("stock_minutes", "(code VARCHAR, time BIGINT, close DOUBLE)"),
                        ("etf_daily", "(code VARCHAR, time BIGINT, close DOUBLE)"),
                        ("etf_minutes", "(code VARCHAR, time BIGINT, close DOUBLE)")]:
            c.execute(f'CREATE TABLE IF NOT EXISTS "{t}" {cols}')
        c.close()
        from quantstudio import _paths
        monkeypatch.setattr(_paths, "db_path", lambda: str(formal_db))
        monkeypatch.setattr("quantstudio.pipeline.qfq_formal_canary.reserve_nonce",
                            lambda *a, **kw: "fake_marker_sha_" + "0" * 48)
        with patch("quantstudio.pipeline.qfq_formal_canary.AuxDbRouter") as mock_router:
            mock_router.from_config_dir.return_value.path_for.return_value = tmp_path / "aux.db"
            result = run_held_canary(
                authorization_path=manifest_path, authorization_sha256=manifest_sha,
                handoff_dir=tmp_path, config_dir=tmp_path, dry_run=True)  # no override
        assert result["main_db_resolved"] == str(formal_db.resolve())
        assert "staging" not in result["main_db_resolved"].lower()


class TestStagingDbOverride:
    """staging_db_override parameter tests (CodeBuddy 7 red lines)."""

    def _setup_staging_db(self, tmp_path):
        """Create a minimal valid staging DB at a path containing 'staging' marker."""
        staging_db = tmp_path / "staging_copy" / "quantstudio.db"
        staging_db.parent.mkdir()
        c = duckdb.connect(str(staging_db))
        for t, cols in [("qfq_discovery_baseline", "(cutover_id VARCHAR)"),
                        ("qfq_trigger_queue", "(source_generation VARCHAR)"),
                        ("qfq_watermark_intent", "(source_generation VARCHAR)"),
                        ("source_watermark", "(last_date BIGINT)"),
                        ("stock_daily", "(code VARCHAR, time BIGINT, close DOUBLE)"),
                        ("stock_minutes", "(code VARCHAR, time BIGINT, close DOUBLE)"),
                        ("etf_daily", "(code VARCHAR, time BIGINT, close DOUBLE)"),
                        ("etf_minutes", "(code VARCHAR, time BIGINT, close DOUBLE)")]:
            c.execute(f'CREATE TABLE IF NOT EXISTS "{t}" {cols}')
        c.close()
        return staging_db

    def test_staging_override_rejects_formal_path(self, tmp_path, monkeypatch):
        """(b) staging_db_override pointing at formal DB path → raise."""
        from quantstudio import _paths
        formal = tmp_path / "data" / "quantstudio.db"
        formal.parent.mkdir()
        formal.write_bytes(b"x")
        monkeypatch.setattr(_paths, "db_path", lambda: str(formal))
        manifest_path, manifest_sha = _make_wp7_e2_manifest(tmp_path)
        _make_valid_handoff_and_exit(tmp_path)
        monkeypatch.setattr("quantstudio.pipeline.qfq_formal_canary.reserve_nonce",
                            lambda *a, **kw: "fake" * 16)
        with pytest.raises(FormalCanaryError, match="formal production DB"):
            run_held_canary(
                authorization_path=manifest_path, authorization_sha256=manifest_sha,
                handoff_dir=tmp_path, config_dir=tmp_path, dry_run=False,
                staging_db_override=str(formal))

    def test_staging_override_rejects_path_without_marker(self, tmp_path, monkeypatch):
        """(b) staging_db_override without staging/output marker → raise."""
        from quantstudio import _paths
        monkeypatch.setattr(_paths, "db_path", lambda: str(tmp_path / "formal.db"))
        # Use a path that does NOT contain staging or output anywhere.
        import tempfile
        clean_tmp = Path(tempfile.mkdtemp(prefix="no_marker_"))
        bad_path = clean_tmp / "quantstudio.db"
        bad_path.write_bytes(b"x")
        manifest_path, manifest_sha = _make_wp7_e2_manifest(tmp_path)
        _make_valid_handoff_and_exit(tmp_path)
        monkeypatch.setattr("quantstudio.pipeline.qfq_formal_canary.reserve_nonce",
                            lambda *a, **kw: "fake" * 16)
        with pytest.raises(FormalCanaryError, match="staging.*output.*marker"):
            run_held_canary(
                authorization_path=manifest_path, authorization_sha256=manifest_sha,
                handoff_dir=tmp_path, config_dir=tmp_path, dry_run=False,
                staging_db_override=str(bad_path))

    def test_staging_override_rejected_with_dry_run(self, tmp_path, monkeypatch):
        """(c) staging_db_override + dry_run=True → raise."""
        from quantstudio import _paths
        monkeypatch.setattr(_paths, "db_path", lambda: str(tmp_path / "formal.db"))
        staging = tmp_path / "staging_copy" / "quantstudio.db"
        staging.parent.mkdir(); staging.write_bytes(b"x")
        manifest_path, manifest_sha = _make_wp7_e2_manifest(tmp_path)
        _make_valid_handoff_and_exit(tmp_path)
        with pytest.raises(FormalCanaryError, match="staging_db_override is only for real execution"):
            run_held_canary(
                authorization_path=manifest_path, authorization_sha256=manifest_sha,
                handoff_dir=tmp_path, config_dir=tmp_path, dry_run=True,
                staging_db_override=str(staging))

    def test_main_db_override_unaffected_by_staging_param(self, tmp_path, monkeypatch):
        """(d) main_db_override behavior unchanged (regression for red line 1)."""
        manifest_path, manifest_sha = _make_wp7_e2_manifest(tmp_path)
        _make_valid_handoff_and_exit(tmp_path)
        from quantstudio import _paths
        monkeypatch.setattr(_paths, "db_path", lambda: str(tmp_path / "formal.db"))
        # main_db_override + dry_run=False must STILL raise (unchanged).
        with pytest.raises(FormalCanaryError, match="main_db_override is only allowed with dry_run=True"):
            run_held_canary(
                authorization_path=manifest_path, authorization_sha256=manifest_sha,
                handoff_dir=tmp_path, config_dir=tmp_path, dry_run=False,
                main_db_override=str(tmp_path / "staging.db"))
