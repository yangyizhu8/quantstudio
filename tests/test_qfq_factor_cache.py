# -*- coding: utf-8 -*-
"""P2 验收：单周期因子查询缓存（生命周期 / 失效 / 命中率 / 禁 mtime 轮询 / 回退开关）。

对应方案 §2.3b（失效机制修正）与 §3.6.2（命中率门）。
用户补充条件 2：不得靠逐批 stat mtime 失效——否则 post-ingest 每次写因子都会变 mtime ⇒ 永久 miss。
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from quantstudio.pipeline.daemon import ResidentCollector

DAY = 86_400_000
BAR = 1_792_373_400_000


@pytest.fixture()
def aux_db(tmp_path):
    p = tmp_path / "aux.db"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE adj_factor (code TEXT, time INTEGER, adj_factor REAL)")
    con.executemany("INSERT INTO adj_factor VALUES (?,?,?)", [
        ("A", BAR - 60_000, 1.5),
        ("A", BAR - 30_000, 1.6),
        ("B", BAR - 60_000, 2.5),
    ])
    con.commit()
    con.close()
    return p


@pytest.fixture()
def collector(aux_db, monkeypatch):
    monkeypatch.delenv("QS_QFQ_INVARIANT_FACTOR_CACHE", raising=False)
    c = ResidentCollector.__new__(ResidentCollector)     # 项目既有裸构造路径
    c._qfq_factor_cache = None
    c._qfq_factor_cache_stats = None
    monkeypatch.setattr(c, "_qfq_align_aux_path", lambda: str(aux_db), raising=False)
    return c


def test_first_call_misses_second_hits(collector):
    codes = ["A", "B"]
    v1 = collector._qfq_factor_lookup_cached("adj_factor", codes, BAR - 3 * DAY, BAR + 3 * DAY)
    v2 = collector._qfq_factor_lookup_cached("adj_factor", codes, BAR - 3 * DAY, BAR + 3 * DAY)
    assert v1 == v2
    st = collector._qfq_factor_cache_report()
    assert st["miss"] == 1 and st["hit"] == 1, st
    assert st["hit_rate"] == 50.0


def test_different_codes_fingerprint_is_a_miss(collector):
    collector._qfq_factor_lookup_cached("adj_factor", ["A"], BAR - DAY, BAR + DAY)
    collector._qfq_factor_lookup_cached("adj_factor", ["B"], BAR - DAY, BAR + DAY)
    st = collector._qfq_factor_cache_report()
    assert st["miss"] == 2 and st["hit"] == 0, st


def test_invalidate_forces_miss(collector):
    codes = ["A"]
    collector._qfq_factor_lookup_cached("adj_factor", codes, BAR - DAY, BAR + DAY)
    collector._qfq_invalidate_factor_cache("post_ingest")
    assert collector._qfq_factor_cache_report()["size"] == 0
    collector._qfq_factor_lookup_cached("adj_factor", codes, BAR - DAY, BAR + DAY)
    st = collector._qfq_factor_cache_report()
    assert st["miss"] == 2, st


def test_env_switch_disables_cache(collector, monkeypatch):
    monkeypatch.setenv("QS_QFQ_INVARIANT_FACTOR_CACHE", "0")
    codes = ["A"]
    collector._qfq_factor_lookup_cached("adj_factor", codes, BAR - DAY, BAR + DAY)
    collector._qfq_factor_lookup_cached("adj_factor", codes, BAR - DAY, BAR + DAY)
    assert collector._qfq_factor_cache is None, "开关关闭时不得建缓存"
    assert collector._qfq_factor_cache_report()["size"] == 0


def test_lru_capacity_evicts(collector):
    for i in range(10):
        collector._qfq_factor_lookup_cached("adj_factor", [f"C{i}"], BAR - DAY, BAR + DAY)
    st = collector._qfq_factor_cache_report()
    assert st["size"] <= collector._QFQ_FACTOR_CACHE_MAX, st
    assert st["evict"] >= 2, st


def test_no_per_batch_mtime_stat(collector, monkeypatch):
    """核心断言：缓存路径不得 stat aux 文件（防「逐批 mtime ⇒ 永远 miss」）。"""
    calls = {"n": 0}
    real_stat = os.stat

    def _counting_stat(path, *a, **kw):
        if str(path).endswith("aux.db"):
            calls["n"] += 1
        return real_stat(path, *a, **kw)

    monkeypatch.setattr(os, "stat", _counting_stat)
    codes = ["A"]
    for _ in range(5):
        collector._qfq_factor_lookup_cached("adj_factor", codes, BAR - DAY, BAR + DAY)
    assert calls["n"] == 0, f"缓存路径不得 stat aux 文件（实测 {calls['n']} 次）"
    st = collector._qfq_factor_cache_report()
    assert st["hit"] == 4 and st["miss"] == 1, st
