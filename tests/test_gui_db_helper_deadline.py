# -*- coding: utf-8 -*-
"""笔2 验收（V2 机制）：GUI 只读查询「超时降级 + 降级后自动恢复」。

背景（2026-09-23 GUI 启动卡死案）：主库带大 WAL 时 `duckdb.connect()` 需先回放 WAL
（生产实测 22.3 分钟）——属「慢的成功」，既不抛异常也不返回，既有忙态重试永不触发，
构造期同步调用会永久阻塞。笔2 引入 deadline + 单槽后台线程。

本用例只测**行为契约**（不依赖真实 DuckDB 阻塞）：
  C1 超时 → 立即返回空 DataFrame（≤ deadline + 容差），且 is_recovering=True
  C2 后台尝试完成后，**下一次调用自动收割**结果（降级后自动恢复）
  C3 真故障（非锁冲突异常）仍向上抛（含超时后在收割时上抛）
  C4 后台尝试仍在跑时再次调用：**不新起线程**（防 09-23 卡死进程重演）
  C5 契约不变：成功路径返回值原样透传
"""
from __future__ import annotations

import threading
import time

import pandas as pd
import pytest

from quantstudio.gui import db_helper as dh


def _helper(tmp_path):
    return dh.DbHelper(
        duckdb_path=tmp_path / "fake.db",
        quarantine_path=tmp_path / "q.db",
        batch_audit_path=tmp_path / "audit.db",
    )


def test_c1_timeout_degrades_fast(tmp_path, monkeypatch):
    """C1：慢查询（模拟 WAL 回放）→ deadline 内返回空表 + 恢复态置位。"""
    h = _helper(tmp_path)
    monkeypatch.setattr(dh, "QUERY_DEADLINE_S", 0.3)

    started = threading.Event()

    def slow(_sql):
        started.set()
        time.sleep(1.5)          # 模拟「慢的成功」
        return pd.DataFrame({"n": [1]})

    monkeypatch.setattr(h, "_query_once", slow)
    t0 = time.time()
    df = h._safe_query("SELECT 1")
    elapsed = time.time() - t0

    assert started.wait(1.0), "后台尝试未启动"
    assert df.empty, "超时应返回空 DataFrame"
    assert elapsed < 1.0, f"主调方等待 {elapsed:.2f}s，应≤deadline+容差"
    assert h.is_recovering is True, "应处于恢复态"
    assert h.recovering_hint(), "恢复态应有提示文案"


def test_c2_auto_recovery_harvests_result(tmp_path, monkeypatch):
    """C2：后台尝试完成后，下一次调用自动收割（降级后自动恢复）。"""
    h = _helper(tmp_path)
    monkeypatch.setattr(dh, "QUERY_DEADLINE_S", 0.2)

    def slow(_sql):
        time.sleep(0.6)
        return pd.DataFrame({"n": [42]})

    monkeypatch.setattr(h, "_query_once", slow)
    assert h._safe_query("SELECT 1").empty          # 第一次：超时降级
    time.sleep(0.7)                                  # 等后台完成
    df = h._safe_query("SELECT 1")                   # 第二次：收割
    assert not df.empty and int(df.iloc[0]["n"]) == 42
    assert h.is_recovering is False, "恢复后应清除恢复态"


def test_c3_real_error_still_raises(tmp_path, monkeypatch):
    """C3：真故障（非锁冲突）仍上抛 —— 快速路径与收割路径都要上抛。"""
    h = _helper(tmp_path)

    def boom(_sql):
        raise ValueError("SQL 语法错（模拟真故障）")

    monkeypatch.setattr(h, "_query_once", boom)
    with pytest.raises(ValueError):
        h._safe_query("SELECT bad")

    # 收割路径：超时后台失败 → 下次调用上抛
    monkeypatch.setattr(dh, "QUERY_DEADLINE_S", 0.2)

    def slow_boom(_sql):
        time.sleep(0.5)
        raise ValueError("后台真故障")

    monkeypatch.setattr(h, "_query_once", slow_boom)
    assert h._safe_query("SELECT bad").empty
    time.sleep(0.6)
    with pytest.raises(ValueError):
        h._safe_query("SELECT bad")


def test_c4_no_new_thread_while_pending(tmp_path, monkeypatch):
    """C4：后台尝试仍在跑时不新起线程（单槽策略）。"""
    h = _helper(tmp_path)
    monkeypatch.setattr(dh, "QUERY_DEADLINE_S", 0.2)
    calls = {"n": 0}

    def slow(_sql):
        calls["n"] += 1
        time.sleep(1.0)
        return pd.DataFrame({"n": [1]})

    monkeypatch.setattr(h, "_query_once", slow)
    h._safe_query("SELECT 1")                        # 第一次：超时，后台仍在跑
    for _ in range(3):
        assert h._safe_query("SELECT 1").empty       # 后续调用：直接降级
    assert calls["n"] == 1, f"应只启动 1 次后台尝试，实际 {calls['n']} 次"
    assert h.is_recovering is True


def test_c5_success_path_passthrough(tmp_path, monkeypatch):
    """C5：成功路径返回值原样透传（契约不变）。"""
    h = _helper(tmp_path)
    want = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    monkeypatch.setattr(h, "_query_once", lambda _sql: want)
    got = h._safe_query("SELECT * FROM t")
    assert got.equals(want)
    assert h.is_recovering is False
