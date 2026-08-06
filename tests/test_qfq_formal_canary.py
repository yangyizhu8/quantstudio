"""B-6 WP7-E2 formal held-canary tests.

The held-canary requires a complete handoff + exit evidence with a matching raw
SHA and watermark_release_authorized=false.  These tests verify the gating logic
(handoff/exit evidence verification, nonce isolation, the watermark-release
hard refusal) at the contract level; the full resident-orchestrator cycle is
exercised by the staging canary tests (``test_qfq_b6_wp4``) since the formal
canary reuses the same orchestrator primitives.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quantstudio.pipeline.qfq_formal_canary import (
    DEFAULT_CANARY_CODES, FormalCanaryError, run_held_canary,
)
from quantstudio.pipeline.qfq_formal_cutover import _write_handoff, write_exit_evidence
from quantstudio.pipeline.qfq_formal_authorization import generate_test_manifest, load_and_verify_manifest


def _make_auth(tmp_path, grants):
    auth_root = tmp_path / "auth_root"
    main = tmp_path / "main.db"; main.write_bytes(b"main")
    aux = tmp_path / "qfq_aux.db"; aux.write_bytes(b"aux")
    path, sha = generate_test_manifest(
        authorization_root=auth_root, cutover_id="cut1",
        formal_main_path=main, formal_aux_path=aux,
        git_commit_sha="0" * 40, config_sha="c" * 64,
        checkout_root=tmp_path, grants=grants, aux_db_path=aux)
    return path, sha


def _write_handoff_and_exit(hdir, *, aux, watermark_release_authorized=False):
    handoff = {"kind": "quantstudio-b6-formal-cutover-handoff", "cutover_id": "cut1",
               "price_source": "mcp", "source_generation": "mcp-gen1",
               "aux_db_path": str(aux), "watermark_release_authorized": watermark_release_authorized}
    raw_sha = _write_handoff(handoff_dir=hdir, payload=handoff)
    write_exit_evidence(handoff_dir=hdir, handoff_raw_sha=raw_sha,
                        child_pid=0, child_create_time=0.0, exit_code=0,
                        locks_released_verified=True, descendant_scan=[])
    return raw_sha


class TestHeldCanaryGating:
    def test_missing_wp7_grant_rejected(self, tmp_path):
        path, sha = _make_auth(tmp_path, {"wp6_formal_cutover": True})
        hdir = tmp_path / "wp6"; hdir.mkdir()
        _write_handoff_and_exit(hdir, aux=tmp_path / "qfq_aux.db")
        with pytest.raises(FormalCanaryError, match="wp7_held_canary"):
            run_held_canary(authorization_path=str(path), authorization_sha256=sha,
                            handoff_dir=hdir, config_dir=tmp_path)

    def test_missing_handoff_rejected(self, tmp_path):
        path, sha = _make_auth(tmp_path, {"wp7_held_canary": True})
        hdir = tmp_path / "wp6"; hdir.mkdir()
        with pytest.raises(FormalCanaryError, match="missing"):
            run_held_canary(authorization_path=str(path), authorization_sha256=sha,
                            handoff_dir=hdir, config_dir=tmp_path)

    def test_watermark_release_authorized_must_be_false(self, tmp_path):
        path, sha = _make_auth(tmp_path, {"wp7_held_canary": True})
        hdir = tmp_path / "wp6"; hdir.mkdir()
        _write_handoff_and_exit(hdir, aux=tmp_path / "qfq_aux.db",
                                watermark_release_authorized=True)
        with pytest.raises(FormalCanaryError, match="watermark_release_authorized=false"):
            run_held_canary(authorization_path=str(path), authorization_sha256=sha,
                            handoff_dir=hdir, config_dir=tmp_path)

    def test_handoff_sha_mismatch_rejected(self, tmp_path):
        path, sha = _make_auth(tmp_path, {"wp7_held_canary": True})
        hdir = tmp_path / "wp6"; hdir.mkdir()
        # Write handoff, then exit evidence with a WRONG sha.
        handoff = {"kind": "quantstudio-b6-formal-cutover-handoff", "cutover_id": "cut1",
                   "price_source": "mcp", "source_generation": "mcp-gen1",
                   "aux_db_path": str(tmp_path / "qfq_aux.db"),
                   "watermark_release_authorized": False}
        _write_handoff(handoff_dir=hdir, payload=handoff)
        write_exit_evidence(handoff_dir=hdir, handoff_raw_sha="1" * 64,
                            child_pid=0, child_create_time=0.0, exit_code=0,
                            locks_released_verified=True, descendant_scan=[])
        with pytest.raises(FormalCanaryError, match="handoff raw SHA mismatch"):
            run_held_canary(authorization_path=str(path), authorization_sha256=sha,
                            handoff_dir=hdir, config_dir=tmp_path)

    def test_default_canary_codes_present(self):
        assert DEFAULT_CANARY_CODES == ("510500", "159919", "000001")
