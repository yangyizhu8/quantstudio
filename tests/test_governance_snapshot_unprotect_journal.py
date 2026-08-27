# -*- coding: utf-8 -*-
"""unprotect 对 protect journal 的对称 fail-closed 门禁测试。"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scripts.governance_snapshot as gs


def _setup(tmp_path, monkeypatch, man_protected=True, idx_protected=True):
    monkeypatch.setattr(gs, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(gs, "INDEX", tmp_path / "index.json")
    monkeypatch.setattr(gs, "PROTECT_LOG", tmp_path / "protect.log")
    monkeypatch.setattr(gs, "PROTECT_JOURNAL", tmp_path / "protect.pending.json")
    monkeypatch.setattr(gs, "UNPROTECT_LOG", tmp_path / "unprotect.log")
    sid = "SNAP_U"
    d = tmp_path / sid; d.mkdir()
    gs._atomic_write_json(d / "manifest.json", {
        "snapshot_id": sid, "protected": man_protected,
        "verify_status": "PASS", "logical_total_sha256": "abc",
    })
    gs.save_index_atomic({"snapshots": [{
        "snapshot_id": sid, "protected": idx_protected,
        "logical_total_sha256": "abc",
    }]})
    return sid, d


def test_inconsistent_protect_journal_blocks_unprotect_no_audit(tmp_path, monkeypatch):
    sid, d = _setup(tmp_path, monkeypatch, man_protected=True, idx_protected=False)
    gs._atomic_write_json(gs.PROTECT_JOURNAL, {"snapshot_id": sid})
    assert gs.cmd_unprotect(sid, "should not run") == 4
    assert not gs.UNPROTECT_LOG.exists(), "失败不得产生 unprotect 审计日志"
    man = json.loads(io.open(d / "manifest.json", encoding="utf-8").read())
    idx = gs.load_index()
    assert man["protected"] is True and idx["snapshots"][0]["protected"] is False


def test_completed_journal_recovers_then_unprotects_both_sources(tmp_path, monkeypatch):
    sid, d = _setup(tmp_path, monkeypatch, man_protected=True, idx_protected=True)
    gs._atomic_write_json(gs.PROTECT_JOURNAL, {"snapshot_id": sid})
    assert gs.cmd_unprotect(sid, "基线退役，用户批准") == 0
    assert not gs.PROTECT_JOURNAL.exists(), "完整 protect 事务应先恢复并清 journal"
    man = json.loads(io.open(d / "manifest.json", encoding="utf-8").read())
    idx = gs.load_index()
    assert man["protected"] is False
    assert idx["snapshots"][0]["protected"] is False
    text = io.open(gs.UNPROTECT_LOG, encoding="utf-8").read()
    assert sid in text and "基线退役" in text
    assert "recover-complete" in io.open(gs.PROTECT_LOG, encoding="utf-8").read()


def test_unprotect_missing_reason_after_journal_recovery_no_unprotect_log(tmp_path, monkeypatch):
    sid, _ = _setup(tmp_path, monkeypatch, man_protected=True, idx_protected=True)
    gs._atomic_write_json(gs.PROTECT_JOURNAL, {"snapshot_id": sid})
    assert gs.cmd_unprotect(sid, "") == 2
    assert not gs.PROTECT_JOURNAL.exists(), "先恢复完整 protect journal"
    assert not gs.UNPROTECT_LOG.exists(), "reason 门禁失败不得记录 unprotect"
