# -*- coding: utf-8 -*-
"""3B 审计四项修正测试（隔离 tmp_path，零生产写）。"""
import io
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scripts.governance_snapshot as gs


def test_file_sha_stream_matches_reference(tmp_path):
    p = tmp_path / "big.bin"
    data = (b"0123456789abcdef" * 1024 * 1024) + b"tail"
    p.write_bytes(data)
    import hashlib
    assert gs.file_sha256_stream(p, chunk_bytes=1024) == hashlib.sha256(data).hexdigest()


def test_file_sha_stream_uses_chunks(tmp_path, monkeypatch):
    p = tmp_path / "x.bin"
    p.write_bytes(b"a" * 10000)
    # 小 chunk 仍给同 hash，证明分块 API 路径可用
    assert gs.file_sha256_stream(p, 97) == gs.file_sha256_stream(p, 4096)


def test_manifest_audit_fields_are_in_create_source():
    """静态契约：create manifest 必含三 hash + RSS + verify 状态字段。"""
    src = io.open(gs.__file__, encoding="utf-8").read()
    for key in ("source_hash_pre", "source_hash_post", "copy_hash",
                "peak_rss_mb", "verify_status", "verified_at"):
        assert f'"{key}"' in src, key


def test_manifest_written_before_rename():
    src = io.open(gs.__file__, encoding="utf-8").read()
    write_pos = src.index('_atomic_write_json(tmp_dir / "manifest.json"')
    rename_pos = src.index("os.rename(tmp_dir, final_dir)")
    index_pos = src.index("save_index_atomic(idx)", rename_pos)
    assert write_pos < rename_pos < index_pos


def test_integrity_check_is_explicit_not_assert():
    src = io.open(gs.__file__, encoding="utf-8").read()
    assert "if integrity != \"ok\"" in src
    assert 'assert chk.execute("PRAGMA integrity_check")' not in src


def test_orphan_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "SNAP_DIR", tmp_path)
    known = tmp_path / "SNAP_known"; known.mkdir()
    orphan = tmp_path / "SNAP_orphan"; orphan.mkdir()
    tmp = tmp_path / "SNAP_pending.tmp"; tmp.mkdir()
    out = gs.detect_orphans({"snapshots": [{"snapshot_id": "SNAP_known"}]})
    assert out == ["SNAP_orphan"]


def test_verify_updates_manifest_status(tmp_path, monkeypatch):
    # 只验证原子字段更新 helper 的契约（真正 verify 在首份快照后台任务完成后留证）
    d = tmp_path / "S"; d.mkdir()
    man = {"logical_total_sha256": "abc", "verify_status": "pending", "verified_at": None}
    gs._atomic_write_json(d / "manifest.json", man)
    loaded = json.loads(io.open(d / "manifest.json", encoding="utf-8").read())
    assert loaded["verify_status"] == "pending" and loaded["verified_at"] is None
