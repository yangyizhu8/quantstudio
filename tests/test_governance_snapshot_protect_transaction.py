# -*- coding: utf-8 -*-
"""bind --protect 跨文件事务与 prune 双源 fail-closed 故障注入测试。"""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scripts.governance_snapshot as gs


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(gs, "INDEX", tmp_path / "index.json")
    monkeypatch.setattr(gs, "PROTECT_LOG", tmp_path / "protect.log")
    monkeypatch.setattr(gs, "PROTECT_JOURNAL", tmp_path / "protect.pending.json")
    sid = "SNAP_TX"
    d = tmp_path / sid; d.mkdir()
    gs._atomic_write_json(d / "manifest.json", {
        "snapshot_id": sid, "logical_total_sha256": "abc",
        "verify_status": "PASS", "protected": False,
    })
    gs.save_index_atomic({"snapshots": [{
        "snapshot_id": sid, "protected": False,
        "logical_total_sha256": "abc",
    }]})
    out = tmp_path / "result"; out.mkdir()
    return sid, d, out


def test_manifest_success_index_failure_leaves_journal_and_no_bind(tmp_path, monkeypatch):
    sid, d, out = _setup(tmp_path, monkeypatch)
    real_save = gs.save_index_atomic

    def fail_index(_):
        raise OSError("fault injection: index write failed")

    monkeypatch.setattr(gs, "save_index_atomic", fail_index)
    with pytest.raises(OSError):
        gs.cmd_bind(sid, str(out), protect=True)
    manifest = json.loads(io.open(d / "manifest.json", encoding="utf-8").read())
    assert manifest["protected"] is True
    assert gs.PROTECT_JOURNAL.exists(), "事务未完成必须保留 journal"
    assert not (out / "snapshot_meta.json").exists(), "失败事务不得生成 bind 文件"
    monkeypatch.setattr(gs, "save_index_atomic", real_save)


def test_prune_rejects_split_state(tmp_path, monkeypatch):
    sid, d, _ = _setup(tmp_path, monkeypatch)
    manifest = json.loads(io.open(d / "manifest.json", encoding="utf-8").read())
    manifest["protected"] = True
    gs._atomic_write_json(d / "manifest.json", manifest)
    assert gs.cmd_prune(keep=0) == 4
    assert d.exists(), "状态分裂时不得删除快照"
    assert "index=False manifest=True" in io.open(
        tmp_path / "protection_mismatch.log", encoding="utf-8").read()


def test_prune_rejects_missing_manifest(tmp_path, monkeypatch):
    sid, d, _ = _setup(tmp_path, monkeypatch)
    (d / "manifest.json").unlink()
    assert gs.cmd_prune(keep=0) == 4
    assert d.exists(), "manifest 缺失时不得删除目录"


def test_recover_completed_transaction_then_bind(tmp_path, monkeypatch):
    sid, d, out = _setup(tmp_path, monkeypatch)
    manifest = json.loads(io.open(d / "manifest.json", encoding="utf-8").read())
    manifest["protected"] = True
    gs._atomic_write_json(d / "manifest.json", manifest)
    index = gs.load_index(); index["snapshots"][0]["protected"] = True
    gs.save_index_atomic(index)
    gs._atomic_write_json(gs.PROTECT_JOURNAL, {"snapshot_id": sid})

    assert gs.protect_journal_check() == 0
    assert not gs.PROTECT_JOURNAL.exists(), "两边均 true 时恢复完成并清 journal"
    assert gs.cmd_bind(sid, str(out), protect=False) == 0
    assert (out / "snapshot_meta.json").exists()


def test_inconsistent_journal_blocks_bind(tmp_path, monkeypatch):
    sid, d, out = _setup(tmp_path, monkeypatch)
    manifest = json.loads(io.open(d / "manifest.json", encoding="utf-8").read())
    manifest["protected"] = True
    gs._atomic_write_json(d / "manifest.json", manifest)
    gs._atomic_write_json(gs.PROTECT_JOURNAL, {"snapshot_id": sid})
    assert gs.cmd_bind(sid, str(out), protect=False) == 4
    assert not (out / "snapshot_meta.json").exists()
