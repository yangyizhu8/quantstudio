# -*- coding: utf-8 -*-
"""3B 终态四项修正测试（tmp_path 隔离，零生产写）。"""
import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scripts.governance_snapshot as gs


def _setup(tmp_path, monkeypatch, status="pending"):
    monkeypatch.setattr(gs, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(gs, "INDEX", tmp_path / "index.json")
    monkeypatch.setattr(gs, "PROTECT_LOG", tmp_path / "protect.log")
    sid = "SNAP_T"
    d = tmp_path / sid; d.mkdir()
    man = {"snapshot_id": sid, "logical_total_sha256": "abc",
           "verify_status": status, "protected": False}
    gs._atomic_write_json(d / "manifest.json", man)
    gs.save_index_atomic({"snapshots": [{"snapshot_id": sid, "protected": False,
                                         "logical_total_sha256": "abc"}]})
    return sid, d


def test_bind_rejects_unverified(tmp_path, monkeypatch):
    sid, _ = _setup(tmp_path, monkeypatch, "pending")
    out = tmp_path / "result"; out.mkdir()
    assert gs.cmd_bind(sid, str(out)) == 4
    assert not (out / "snapshot_meta.json").exists()


def test_bind_protect_updates_manifest_index_and_audit(tmp_path, monkeypatch):
    sid, d = _setup(tmp_path, monkeypatch, "PASS")
    out = tmp_path / "result"; out.mkdir()
    assert gs.cmd_bind(sid, str(out), protect=True) == 0
    man = json.loads(io.open(d / "manifest.json", encoding="utf-8").read())
    idx = gs.load_index()
    meta = json.loads(io.open(out / "snapshot_meta.json", encoding="utf-8").read())
    assert man["protected"] is True
    assert idx["snapshots"][0]["protected"] is True
    assert meta["verify_status"] == "PASS" and meta["protected"] is True
    assert sid in io.open(tmp_path / "protect.log", encoding="utf-8").read()


def test_orphan_fail_closed_in_list(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(gs, "INDEX", tmp_path / "index.json")
    gs.save_index_atomic({"snapshots": []})
    (tmp_path / "SNAP_orphan").mkdir()
    assert gs.cmd_list() == 4
    assert "SNAP_orphan" in io.open(tmp_path / "orphan.log", encoding="utf-8").read()


def test_peak_rss_uses_os_peak_not_current_rss():
    src = io.open(gs.__file__, encoding="utf-8").read()
    assert "PeakWorkingSetSize" in src
    assert "ru_maxrss" in src
    assert "memory_info().rss" not in src
