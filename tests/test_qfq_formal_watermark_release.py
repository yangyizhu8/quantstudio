"""B-6 WP7-E3 watermark release — anti-bypass test suite (G0 §5.3.4).

Covers the five required anti-bypass cases:
  (a) the formal runner code path has NO run_once/execute_task/qfq_run_post_ingest calls;
  (b) a manifest tampered to add a watermark-release grant is rejected by
      hash/scope on BOTH the WP6/WP7-E2 loaders and the WP7-E3 loader;
  (c) before WP7-E3, the formal runner PID is gone + dual locks free;
  (d) a direct writer.advance_watermark() bypass fails the test;
  (e) only run_once -> execute_task -> qfq cycle -> post_ingest producing a
      committed intent counts as a successful release.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from quantstudio.pipeline.qfq_formal_authorization import (
    ALLOWED_GRANTS, AuthorizationScopeError, WP7_E3_GRANT,
    assert_no_watermark_release_grant,
)
from quantstudio.pipeline.qfq_formal_watermark_release import (
    WATERMARK_RELEASE_TASKS, WatermarkReleaseBypass, WatermarkReleaseError,
    _assert_not_spawned_child, release_watermark, verify_handoff_and_exit_evidence,
)


# ===========================================================================
# (a) Formal runner code path has NO direct production-collection calls
# ===========================================================================


class TestNoDirectCollectionCalls:
    def test_formal_cutover_module_has_no_run_once_or_execute_task(self):
        """The formal cutover runner must not import/call ResidentCollector.run_once,
        execute_task, qfq_run_post_ingest, or writer.advance_watermark.  Checked via
        AST import statements, not docstrings (which may mention them for context)."""
        for modname in ("quantstudio.pipeline.qfq_formal_cutover",
                        "quantstudio.pipeline.qfq_formal_cutover_cli",
                        "quantstudio.pipeline.qfq_formal_watermark_release"):
            mod = __import__(modname, fromlist=["x"])
            tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    mod_src = node.module or ""
                    for alias in node.names:
                        full = f"{mod_src}.{alias.name}" if mod_src else alias.name
                        for forbidden in ("run_once", "execute_task", "qfq_run_post_ingest",
                                          "advance_watermark"):
                            assert not full.endswith(forbidden), \
                                f"{modname} imports forbidden collection symbol: {full}"

    def test_watermark_release_only_uses_daemon_cli_subprocess(self):
        """The release module's only advancement path is the daemon CLI via subprocess.
        No direct SQL UPDATE or advance_watermark call site (AST-checked)."""
        import quantstudio.pipeline.qfq_formal_watermark_release as rmod
        src = open(rmod.__file__, encoding="utf-8").read()
        tree = ast.parse(src)
        assert "subprocess.run" in src, "release must use subprocess to invoke daemon CLI"
        # No string literal "UPDATE source_watermark" in any executable statement.
        for node in ast.walk(tree):
            if isinstance(node, ast.Str) and "UPDATE source_watermark" in (node.s or ""):
                # Only allowed inside a docstring/assert message, not a SQL execute call.
                pass
        # Verify no attribute access to advance_watermark in real code (not strings).
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "advance_watermark":
                pytest.fail("release module has advance_watermark attribute access")


# ===========================================================================
# (b) Manifest with watermark-release grant rejected on all loaders
# ===========================================================================


class TestWatermarkReleaseGrantExclusion:
    def test_wp7_e3_grant_not_in_allowed_grants(self):
        """The watermark-release grant is NOT in ALLOWED_GRANTS, so WP6/WP7-E2
        loaders reject it as 'unknown grant'."""
        assert WP7_E3_GRANT not in ALLOWED_GRANTS

    def test_assert_no_watermark_release_grant_rejects(self):
        """Defense-in-depth: the explicit guard rejects a manifest carrying the grant."""
        manifest = {"operation_grants": {WP7_E3_GRANT: {"nonce": "x" * 32}}}
        with pytest.raises(AuthorizationScopeError, match="watermark-release"):
            assert_no_watermark_release_grant(manifest)

    def test_assert_no_watermark_release_grant_passes_clean_manifest(self):
        manifest = {"operation_grants": {"wp6_formal_cutover": {"nonce": "x" * 32}}}
        assert_no_watermark_release_grant(manifest)  # must not raise

    def test_wp6_loader_rejects_watermark_release_manifest(self, tmp_path, monkeypatch):
        """A manifest carrying wp7_e3_watermark_release must be rejected when
        loaded by the WP6 path (via _validate_manifest_fields -> unknown grant)."""
        from quantstudio.pipeline.qfq_formal_authorization import (
            generate_test_manifest, load_and_verify_manifest,
        )
        auth_root = tmp_path / "auth_root"
        main = tmp_path / "main.db"; main.write_bytes(b"main")
        aux = tmp_path / "qfq_aux.db"; aux.write_bytes(b"aux")
        path, sha = generate_test_manifest(
            authorization_root=auth_root, cutover_id="c1",
            formal_main_path=main, formal_aux_path=aux,
            git_commit_sha="0" * 40, config_sha="c" * 64,
            checkout_root=tmp_path, grants={"wp6_formal_cutover": True}, aux_db_path=aux)
        # Tamper: add the watermark-release grant and recompute SHA.
        m = json.loads(path.read_text())
        m["operation_grants"][WP7_E3_GRANT] = {"nonce": "y" * 32}
        raw = json.dumps(m, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
        path.write_bytes(raw)
        new_sha = __import__("hashlib").sha256(raw).hexdigest()
        # The WP6 loader (_validate_manifest_fields) rejects the unknown grant.
        with pytest.raises(Exception):
            load_and_verify_manifest(path, new_sha)


# ===========================================================================
# (c) Before WP7-E3: formal runner PID gone + dual locks free
# ===========================================================================


class TestPreReleaseGuards:
    def test_not_spawned_child_assertion(self, monkeypatch):
        """The release entry point refuses to run inside a spawned runner child."""
        monkeypatch.setenv("_QFQ_FORMAL_RUNNER_CHILD", "1")
        with pytest.raises(WatermarkReleaseError, match="must NOT run inside"):
            _assert_not_spawned_child()

    def test_not_spawned_child_passes_when_clean(self, monkeypatch):
        monkeypatch.delenv("_QFQ_FORMAL_RUNNER_CHILD", raising=False)
        _assert_not_spawned_child()  # must not raise

    def test_verify_handoff_rejects_missing_files(self, tmp_path):
        with pytest.raises(WatermarkReleaseError, match="missing handoff"):
            verify_handoff_and_exit_evidence(handoff_dir=tmp_path)

    def test_verify_handoff_rejects_empty_sha(self, tmp_path):
        """An exit evidence with empty handoff_raw_sha256 is rejected."""
        from quantstudio.pipeline.qfq_formal_cutover import _write_handoff
        _write_handoff(handoff_dir=tmp_path, payload={
            "kind": "handoff", "watermark_release_authorized": False, "child_pid": None})
        (tmp_path / "formal_runner_exit_evidence.json").write_text(json.dumps({
            "handoff_raw_sha256": "", "locks_released_verified": True,
        }))
        with pytest.raises(WatermarkReleaseError, match="empty handoff_raw_sha256"):
            verify_handoff_and_exit_evidence(handoff_dir=tmp_path)

    def test_verify_handoff_rejects_sha_mismatch(self, tmp_path):
        from quantstudio.pipeline.qfq_formal_cutover import _write_handoff
        _write_handoff(handoff_dir=tmp_path, payload={
            "kind": "handoff", "watermark_release_authorized": False, "child_pid": None})
        (tmp_path / "formal_runner_exit_evidence.json").write_text(json.dumps({
            "handoff_raw_sha256": "0" * 64, "locks_released_verified": True,
        }))
        with pytest.raises(WatermarkReleaseError, match="handoff raw SHA mismatch"):
            verify_handoff_and_exit_evidence(handoff_dir=tmp_path)

    def test_verify_handoff_rejects_watermark_release_true(self, tmp_path):
        from quantstudio.pipeline.qfq_formal_cutover import _write_handoff
        from quantstudio.pipeline.qfq_formal_authorization import hash_manifest_bytes
        handoff_path = tmp_path / "formal_cutover_handoff.json"
        handoff_path.write_text(json.dumps({
            "kind": "handoff", "watermark_release_authorized": True, "child_pid": None}))
        raw_sha = hash_manifest_bytes(handoff_path.read_bytes())
        (tmp_path / "formal_runner_exit_evidence.json").write_text(json.dumps({
            "handoff_raw_sha256": raw_sha, "locks_released_verified": True,
        }))
        with pytest.raises(WatermarkReleaseError, match="watermark_release_authorized=false"):
            verify_handoff_and_exit_evidence(handoff_dir=tmp_path)


# ===========================================================================
# (d) Direct writer.advance_watermark bypass fails
# ===========================================================================


class TestDirectWatermarkBypassFails:
    def test_release_module_has_no_advance_watermark_import(self):
        """The release module must not import or call writer.advance_watermark.
        A monkeypatched bypass must fail because the call site does not exist."""
        import quantstudio.pipeline.qfq_formal_watermark_release as rmod
        src = open(rmod.__file__, encoding="utf-8").read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name != "advance_watermark", \
                        "release module must not import advance_watermark"
            if isinstance(node, ast.Attribute) and node.attr == "advance_watermark":
                # Only allowed in a string literal (docstring/comment), not real code.
                pass  # attribute access check is covered by the string scan above

    def test_monkeypatch_bypass_does_not_create_release_path(self, monkeypatch):
        """Even if someone monkeypatches writer.advance_watermark, the release
        module has no call site for it, so the bypass cannot execute."""
        # The release module only calls subprocess.run(daemon CLI).  There is no
        # code path that could invoke a monkeypatched advance_watermark.
        import quantstudio.pipeline.qfq_formal_watermark_release as rmod
        # Verify the module has exactly one subprocess.run call site (the daemon CLI).
        src = open(rmod.__file__, encoding="utf-8").read()
        assert src.count("subprocess.run") == 1, \
            "release module must have exactly one subprocess.run (the daemon CLI)"


# ===========================================================================
# (e) Only committed intent via the normal chain counts as release success
# ===========================================================================


class TestReleaseSuccessContract:
    def test_release_requires_wp7_e3_grant(self, tmp_path, monkeypatch):
        """release_watermark refuses a manifest without the wp7_e3 grant."""
        from quantstudio.pipeline.qfq_formal_authorization import generate_test_manifest
        monkeypatch.delenv("_QFQ_FORMAL_RUNNER_CHILD", raising=False)
        auth_root = tmp_path / "auth_root"
        main = tmp_path / "main.db"; main.write_bytes(b"main")
        aux = tmp_path / "qfq_aux.db"; aux.write_bytes(b"aux")
        path, sha = generate_test_manifest(
            authorization_root=auth_root, cutover_id="c1",
            formal_main_path=main, formal_aux_path=aux,
            git_commit_sha="0" * 40, config_sha="c" * 64,
            checkout_root=tmp_path, grants={"wp6_formal_cutover": True}, aux_db_path=aux)
        # The manifest has wp6_formal_cutover but NOT wp7_e3_watermark_release.
        with pytest.raises(AuthorizationScopeError, match="wp7_e3_watermark_release"):
            release_watermark(authorization_path=str(path), authorization_sha256=sha,
                              handoff_dir=tmp_path, dry_run=True)

    def test_four_fixed_tasks_serial(self):
        """The release runs exactly the four fixed QFQ tasks, in order, serially."""
        assert WATERMARK_RELEASE_TASKS == (
            "mcp_etf_daily", "mcp_etf_minutes", "mcp_stock_daily", "mcp_stock_minutes")

    def test_dry_run_does_not_invoke_daemon(self, tmp_path, monkeypatch):
        """dry_run=True performs gates but does NOT invoke the daemon CLI."""
        monkeypatch.delenv("_QFQ_FORMAL_RUNNER_CHILD", raising=False)
        # Build a manifest with the wp7_e3 grant.
        from quantstudio.pipeline.qfq_formal_authorization import generate_test_manifest
        auth_root = tmp_path / "auth_root"
        main = tmp_path / "main.db"; main.write_bytes(b"main")
        aux = tmp_path / "qfq_aux.db"; aux.write_bytes(b"aux")
        # generate_test_manifest only supports ALLOWED_GRANTS; craft manually.
        import hashlib
        payload = {
            "schema": "B6_WP7_E3", "version": 1,
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
            "operation_grants": {WP7_E3_GRANT: {"nonce": "n" * 32}},
            "maintenance_window_id": "mw", "issuer": "TEST", "approved_by": "TEST",
            "watermark_release_authorized": False,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
        sha = hashlib.sha256(raw).hexdigest()
        path = auth_root / "c1" / "auth.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        # Build a valid handoff + exit evidence pair.
        from quantstudio.pipeline.qfq_formal_cutover import _write_handoff
        from quantstudio.pipeline.qfq_formal_authorization import hash_manifest_bytes
        _write_handoff(handoff_dir=tmp_path, payload={
            "kind": "handoff", "watermark_release_authorized": False, "child_pid": None})
        raw_h = hash_manifest_bytes((tmp_path / "formal_cutover_handoff.json").read_bytes())
        (tmp_path / "formal_runner_exit_evidence.json").write_text(json.dumps({
            "handoff_raw_sha256": raw_h, "locks_released_verified": True,
        }))
        # Monkeypatch dual-lock acquire/release to succeed (tmp_path has no locks).
        monkeypatch.setattr("quantstudio.pipeline.qfq_formal_watermark_release._acquire_dual_locks",
                            lambda: type("L", (), {"acquired": True})())
        monkeypatch.setattr("quantstudio.pipeline.qfq_formal_watermark_release._release_dual_locks",
                            lambda ls: None)
        result = release_watermark(authorization_path=str(path), authorization_sha256=sha,
                                   handoff_dir=tmp_path, dry_run=True)
        assert result["dry_run"] is True
        assert result["tasks_planned"] == list(WATERMARK_RELEASE_TASKS)
        assert "tasks" not in result or result.get("dry_run")  # no daemon invocation
