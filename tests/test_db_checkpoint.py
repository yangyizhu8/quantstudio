# -*- coding: utf-8 -*-
"""笔3/笔4 验收：WAL 检查点与巡检（`quantstudio/pipeline/db_checkpoint.py`）。

判据：
  C1 `wal_health` 结构化字段与阈值判定正确（含 WAL 不存在 → 0）
  C2 无 WAL 时 `checkpoint_database` 为**廉价空操作**（不打开库、立即返回 True）
  C3 有 WAL 时检查点**真正收敛 WAL**（体积归零）—— 用副本库（绝不触生产库）
  C4 库被占用/超时 → **超时放弃**返回 (False, timeout)，不抛异常、不阻塞
  C5 检查点失败（异常）→ 返回 (False, detail)，**不抛异常**（收尾路径必须安全）
"""
from __future__ import annotations

import sys
import time
import types

import pytest

from quantstudio.pipeline import db_checkpoint as dc


def test_c1_wal_health_fields_and_threshold(tmp_path):
    db = tmp_path / "x.db"
    db.write_bytes(b"")                                   # 主库占位（无 WAL）
    h = dc.wal_health(db, threshold_bytes=1000)
    assert h["wal_bytes"] == 0 and h["over_threshold"] is False
    assert h["wal_path"].endswith("x.db.wal")
    assert h["threshold_bytes"] == 1000
    assert h["threshold_mb"] == pytest.approx(0.0, abs=0.05)   # 1000B≈0.001MB，按 1 位小数取整

    dc.wal_path(db).write_bytes(b"0" * 2000)              # 造 2000B WAL
    h2 = dc.wal_health(db, threshold_bytes=1000)
    assert h2["wal_bytes"] == 2000 and h2["over_threshold"] is True


def test_c2_no_wal_is_cheap_noop(tmp_path, monkeypatch):
    db = tmp_path / "n.db"
    db.write_bytes(b"")

    called = {"connect": 0}

    class _Boom:
        @staticmethod
        def connect(*_a, **_k):
            called["connect"] += 1
            raise AssertionError("无 WAL 时不应打开库")

    monkeypatch.setitem(sys.modules, "duckdb", _Boom)
    ok, detail = dc.checkpoint_database(db, timeout_s=1.0)
    assert ok is True and "无需检查点" in detail
    assert called["connect"] == 0


def test_c3_checkpoint_converges_wal_on_copy(tmp_path):
    """副本库上制造残留 WAL → 检查点后 WAL 归零（真实 duckdb，不触生产库）。"""
    duckdb = pytest.importorskip("duckdb")
    db = tmp_path / "exp.db"
    conn = duckdb.connect(str(db))
    for pragma in ("PRAGMA disable_checkpoint_on_shutdown", "PRAGMA wal_autocheckpoint='1GB'"):
        try:
            conn.execute(pragma)
        except Exception:
            pass
    conn.execute("CREATE TABLE t AS SELECT range AS i FROM range(200000)")
    conn.execute("INSERT INTO t SELECT i+1000000 FROM t")
    conn.close()                                          # 关闭时检查点被禁用 → WAL 残留
    # 若该 duckdb 版本仍自动检查点（WAL=0），则本用例退化为「空操作」分支，跳过收敛断言
    if dc.wal_size_bytes(db) == 0:
        pytest.skip("该 duckdb 版本关闭时仍自动检查点，无法制造残留 WAL（语义不变）")

    before = dc.wal_size_bytes(db)
    ok, detail = dc.checkpoint_database(db, timeout_s=60.0)
    assert ok is True, detail
    assert dc.wal_size_bytes(db) == 0, "检查点后 WAL 应被收敛（归零）"
    assert before > 0


def test_c4_timeout_abandons_without_raising(tmp_path, monkeypatch):
    """库被占用 → 超时放弃（不抛异常、不阻塞调用方）。"""
    db = tmp_path / "busy.db"
    db.write_bytes(b"")
    dc.wal_path(db).write_bytes(b"0" * 4096)              # 有 WAL，才会真正尝试打开

    class _SlowDuckdb:
        @staticmethod
        def connect(*_a, **_k):
            time.sleep(5.0)                               # 模拟 RW open 长时间阻塞
            raise AssertionError("不应走到这里")

    monkeypatch.setitem(sys.modules, "duckdb", _SlowDuckdb)
    t0 = time.time()
    ok, detail = dc.checkpoint_database(db, timeout_s=0.4)
    elapsed = time.time() - t0
    assert ok is False and detail.startswith("timeout>")
    assert elapsed < 2.0, f"应在超时后立即返回，实际 {elapsed:.2f}s"


def test_c5_failure_returns_detail_without_raising(tmp_path, monkeypatch):
    db = tmp_path / "err.db"
    db.write_bytes(b"")
    dc.wal_path(db).write_bytes(b"0" * 4096)

    class _ErrDuckdb:
        @staticmethod
        def connect(*_a, **_k):
            raise RuntimeError("模拟打开失败")

    monkeypatch.setitem(sys.modules, "duckdb", _ErrDuckdb)
    ok, detail = dc.checkpoint_database(db, timeout_s=2.0)
    assert ok is False and "RuntimeError" in detail
