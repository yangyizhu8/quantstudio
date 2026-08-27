# -*- coding: utf-8 -*-
"""分片 hash v4 等价性测试（T1-T9）。

核心判据：分片 hash 与全表 hash 逐字符一致（对同一数据）。
"""
import hashlib
import os
import sys
import tempfile
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scripts.governance_snapshot as gs


@pytest.fixture(autouse=True)
def _mock_guard(monkeypatch):
    monkeypatch.setattr(gs, "_data_side_tasks_running", lambda: [])


KEYS = {"tables": {"t": "code, time"}}


def _make_db(path, n=100):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE t(code VARCHAR, time BIGINT, v DOUBLE)")
    for i in range(n):
        con.execute("INSERT INTO t VALUES (?, ?, ?)",
                    [f"c{i:04d}", 1000 + i, i * 0.1])
    con.close()
    return path


def _full_table_hash(path):
    """旧版全表 hash（作为基准）。"""
    con = duckdb.connect(str(path), read_only=True)
    h = hashlib.sha256()
    rows = 0
    res = con.execute("SELECT * FROM t ORDER BY code, time")
    batch = res.fetch_record_batch(8192)
    while True:
        try:
            tbl = batch.read_next_batch()
        except StopIteration:
            break
        if tbl.num_rows == 0:
            continue
        for row in tbl.to_pylist():
            h.update(gs.encode_row(tuple(row.values())).encode("utf-8"))
            h.update(b"\x0a")
            rows += 1
    con.close()
    return h.hexdigest(), rows


class TestShardEquivalence:
    """T1-T3: 小表/大表/边界 分片与全表 hash 等价"""

    def test_t1_small_table(self, tmp_path):
        """T1: 小表（< threshold，单片）分片 hash = 全表 hash"""
        db = _make_db(tmp_path / "t1.duckdb", n=100)
        h_new, r_new = gs.table_hash(lambda: duckdb.connect(str(db), read_only=True),
                                     "t", KEYS)
        h_old, r_old = _full_table_hash(db)
        assert h_new == h_old and r_new == r_old

    def test_t2_large_table_forced_shard(self, tmp_path, monkeypatch):
        """T2: 大表（强制低 threshold 触发分片）分片 hash = 全表 hash"""
        monkeypatch.setattr(gs, "SHARD_ROW_TARGET", 30)  # 强制 100 行表分片
        db = _make_db(tmp_path / "t2.duckdb", n=100)
        h_new, r_new = gs.table_hash(lambda: duckdb.connect(str(db), read_only=True),
                                     "t", KEYS)
        h_old, r_old = _full_table_hash(db)
        assert h_new == h_old and r_new == r_old

    def test_t3_single_shard_no_boundaries(self, tmp_path, monkeypatch):
        """T3: threshold 高于行数 → 无边界 → 单片全表"""
        monkeypatch.setattr(gs, "SHARD_ROW_TARGET", 5_000_000)
        db = _make_db(tmp_path / "t3.duckdb", n=50)
        h_new, r_new = gs.table_hash(lambda: duckdb.connect(str(db), read_only=True),
                                     "t", KEYS)
        h_old, r_old = _full_table_hash(db)
        assert h_new == h_old and r_new == r_old


class TestNULLHandling:
    """T4/T8/T9: NULL 排序方向（v4：NULL 归最后片）"""

    def _make_db_with_null(self, path):
        con = duckdb.connect(str(path))
        con.execute("CREATE TABLE t(code VARCHAR, time BIGINT, v DOUBLE)")
        for i in range(50):
            con.execute("INSERT INTO t VALUES (?, ?, ?)", [f"c{i:03d}", 1000+i, i*0.1])
        # 加 5 行 NULL code
        for i in range(5):
            con.execute("INSERT INTO t VALUES (NULL, ?, ?)", [2000+i, -1.0])
        con.close()

    def test_t8_null_in_last_shard(self, tmp_path, monkeypatch):
        """T8: 含 NULL code → 分片 hash 与全表 hash 一致（NULL 在最后片）"""
        monkeypatch.setattr(gs, "SHARD_ROW_TARGET", 20)  # 强制分片
        db = tmp_path / "t8.duckdb"
        self._make_db_with_null(db)
        h_new, r_new = gs.table_hash(lambda: duckdb.connect(str(db), read_only=True),
                                     "t", KEYS)
        h_old, r_old = _full_table_hash(db)
        assert h_new == h_old, f"NULL 分片不等价:\n  new={h_new[:16]}\n  old={h_old[:16]}"
        assert r_new == r_old == 55

    def test_t9_null_count_conservation(self, tmp_path, monkeypatch):
        """T9: NULL 行计数守恒"""
        monkeypatch.setattr(gs, "SHARD_ROW_TARGET", 20)
        db = tmp_path / "t9.duckdb"
        self._make_db_with_null(db)
        _, rows = gs.table_hash(lambda: duckdb.connect(str(db), read_only=True),
                                "t", KEYS)
        con = duckdb.connect(str(db), read_only=True)
        total = con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        nulls = con.execute("SELECT COUNT(*) FROM t WHERE code IS NULL").fetchone()[0]
        con.close()
        assert rows == total == 55
        assert nulls == 5


class TestBoundaryComputation:
    """T4-T7: 边界/空片/行数守恒"""

    def test_t4_empty_shard(self, tmp_path, monkeypatch):
        """T4: 空分片不崩溃且行数守恒"""
        monkeypatch.setattr(gs, "SHARD_ROW_TARGET", 10)
        db = _make_db(tmp_path / "t4.duckdb", n=30)
        # 30 行 / threshold 10 → ~3 片，有些可能空
        h, r = gs.table_hash(lambda: duckdb.connect(str(db), read_only=True),
                             "t", KEYS)
        assert r == 30  # 行数守恒

    def test_t5_row_conservation(self, tmp_path, monkeypatch):
        """T5: 多片行数守恒"""
        monkeypatch.setattr(gs, "SHARD_ROW_TARGET", 7)
        db = _make_db(tmp_path / "t5.duckdb", n=100)
        _, r = gs.table_hash(lambda: duckdb.connect(str(db), read_only=True),
                             "t", KEYS)
        assert r == 100

    def test_t6_boundary_computation(self, tmp_path):
        """T6: 边界计算正确性"""
        db = _make_db(tmp_path / "t6.duckdb", n=100)
        con = duckdb.connect(str(db), read_only=True)
        old = gs.SHARD_ROW_TARGET
        gs.SHARD_ROW_TARGET = 25
        try:
            boundaries = gs._compute_shard_boundaries(con, "t", "code")
        finally:
            gs.SHARD_ROW_TARGET = old
        con.close()
        # 100 行 / 25 per shard → ~4 边界
        assert len(boundaries) >= 3
        # 边界递增
        assert all(boundaries[i] < boundaries[i+1] for i in range(len(boundaries)-1))

    def test_t7_null_skipped_in_boundaries(self, tmp_path):
        """T7 (O3): NULL 组不出现在边界中"""
        db = tmp_path / "t7.duckdb"
        self = TestNULLHandling()
        self._make_db_with_null(db)
        con = duckdb.connect(str(db), read_only=True)
        boundaries = gs._compute_shard_boundaries(con, "t", "code")
        con.close()
        assert None not in boundaries
