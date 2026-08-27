# -*- coding: utf-8 -*-
"""3B 快照机制单测（DSH 批准的 7 项验收要点中的可测项；核心字节级验收另跑真实快照）
隔离原则：全部使用 tmp_path 临时库，不触碰生产库。"""
import io
import json
import os
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scripts.governance_snapshot as gs


@pytest.fixture(autouse=True)
def _mock_data_side_guard(monkeypatch):
    """T1（DSH 审计）：mock 进程扫描，防止数据侧任务时段测试被真实 guard 拦截。"""
    monkeypatch.setattr(gs, "_data_side_tasks_running", lambda: [])


# ---------------- canonical 编码 fixtures（设计 §4 全类型） ----------------

def test_encoding_fixtures():
    assert gs.encode_value(None) == "\\N"
    assert gs.encode_value(True) == "true"
    assert gs.encode_value(42) == "42"
    assert gs.encode_value(float("nan")) == "NaN"
    assert gs.encode_value(float("inf")) == "Inf"
    assert gs.encode_value(float("-inf")) == "-Inf"
    assert gs.encode_value(-0.0) == "-0.0"
    assert gs.encode_value(0.0) == "0.0"
    assert gs.encode_value(1.5) == "1.5"
    assert gs.encode_value(b"\xab") == "0xab"
    assert gs.encode_value("a\\b") == "a\\\\b"          # 反斜杠重复
    assert gs.encode_value("a\x1fb") == "a\\u1fb"        # 分隔符转义
    assert gs.encode_value("a\nb") == "a\\u0ab"          # 换行转义


def test_row_separators():
    expected = "1" + gs.SEP_COL + "\\N" + gs.SEP_COL + "x"
    assert gs.encode_row((1, None, "x")) == expected


# ---------------- hash 确定性 ----------------

KEYS = {"tables": {"t1": "code, time", "t_full": "__FULL_COLUMN__ (退化)"}}


def _mkdb(path, n=5):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE t1(code VARCHAR, time BIGINT, v DOUBLE)")
    for i in range(n):
        con.execute("INSERT INTO t1 VALUES (?, ?, ?)", [f"c{i}", 1000 + i, i * 1.5])
    con.execute("CREATE TABLE t_full(a VARCHAR, b BIGINT)")
    con.execute("INSERT INTO t_full VALUES ('x', 1), ('y', 2)")
    con.close()


def test_hash_deterministic(tmp_path):
    a, b = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    _mkdb(a); _mkdb(b)
    h1, p1 = gs.all_tables_hash(a, KEYS)
    h2, p2 = gs.all_tables_hash(b, KEYS)
    assert h1 == h2, "同内容两库 hash 必须一致"


def test_hash_detects_change(tmp_path):
    a, b = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    _mkdb(a); _mkdb(b)
    con = duckdb.connect(str(b)); con.execute("UPDATE t1 SET v = 99 WHERE code='c0'"); con.close()
    h1, _ = gs.all_tables_hash(a, KEYS)
    h2, _ = gs.all_tables_hash(b, KEYS)
    assert h1 != h2, "单值变化必须改变 hash"


# ---------------- 原子 index ----------------

def test_index_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(gs, "INDEX", tmp_path / "index.json")
    gs.save_index_atomic({"snapshots": [{"snapshot_id": "X"}]})
    assert json.loads(io.open(tmp_path / "index.json", encoding="utf-8").read())["snapshots"][0]["snapshot_id"] == "X"
    assert not (tmp_path / "index.json.tmp").exists()


# ---------------- prune 保留与保护 ----------------

def test_prune_keep_and_protect(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(gs, "INDEX", tmp_path / "index.json")
    idx = {"snapshots": []}
    for i in range(4):
        sid = f"SNAP_20260818_{i:03d}_deadbeef"
        d = tmp_path / sid; d.mkdir()
        (d / "x.db").write_text("x")
        gs._atomic_write_json(d / "manifest.json", {
            "snapshot_id": sid, "protected": i == 0,
        })
        idx["snapshots"].append({"snapshot_id": sid, "protected": i == 0})
    gs.save_index_atomic(idx)
    gs.cmd_prune(2)
    out = gs.load_index()["snapshots"]
    ids = [s["snapshot_id"] for s in out]
    assert "SNAP_20260818_000_deadbeef" in ids, "保护项不删"
    assert not (tmp_path / "SNAP_20260818_001_deadbeef").exists(), "最旧未保护项被删"
    assert len([s for s in out if not s.get("protected")]) == 2


def test_unprotect_requires_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(gs, "INDEX", tmp_path / "index.json")
    gs.save_index_atomic({"snapshots": [{"snapshot_id": "S1", "protected": True}]})
    rc = gs.cmd_unprotect("S1", "")
    assert rc == 2, "无 reason 拒绝"


def test_unprotect_audits(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(gs, "INDEX", tmp_path / "index.json")
    monkeypatch.setattr(gs, "UNPROTECT_LOG", tmp_path / "unprotect.log")
    gs.save_index_atomic({"snapshots": [{"snapshot_id": "S1", "protected": True}]})
    d = tmp_path / "S1"; d.mkdir()
    io.open(d / "manifest.json", "w", encoding="utf-8").write(
        json.dumps({"snapshot_id": "S1", "protected": True}))
    rc = gs.cmd_unprotect("S1", "基线退役，用户批准 2026-08-18")
    assert rc == 0
    assert "S1" in io.open(tmp_path / "unprotect.log", encoding="utf-8").read()
    man = json.loads(io.open(d / "manifest.json", encoding="utf-8").read())
    assert man["protected"] is False and "unprotect_reason" in man


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
