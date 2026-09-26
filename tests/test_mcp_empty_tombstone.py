# -*- coding: utf-8 -*-
"""P2-1 验收：空窗墓碑（4 谓词 + 2 硬不变量）。

方案：docs/jabberwock-four-findings-fix-design.md（P2-1）｜Trae 兼容判定回执口径

判据：
  T1 真·空窗（服务端零分片/零落盘 + 无既有条目）→ 写墓碑（empty/empty_ts/empty_ttl_s 字段名）
  T2 墓碑命中（TTL 内）→ 跳过重取（_resolve_shard_paths 未被调用）且**不置 manifest_dirty**
     （empty_ts 不变 ⇒ 防 TTL 无限续期）
  T3 TTL 过期 → 视为 miss（重取被调用）
  T4 谓词②拒绝：服务端非零分片（重取无产出场景）→ 不写墓碑
  T5 谓词④拒绝：该 ckey 已有条目 → 不写墓碑（互斥）
  T6 硬不变量①：写入为**整体替换**（墓碑条目不含 shards；旧条目被整体替换而非 merge）
  T7 `_empty_ttl_s`：默认 604800；env QS_EXPORT_EMPTY_TTL_S 可配
  T8 `_tombstone_active` 边界：无 empty_ts / ttl<=0 / 时间格式错 → False
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from quantstudio.pipeline.sources import mcp_adapter as ma

TABLE, FREQ, BS, BE = "etf_minutes", "1min", "2026-09-20", "2026-09-21"
CKEY = f"{TABLE}|{BS}|{BE}"


class _Stub(ma.MCPAdapter):
    def __init__(self, manifest, miss_paths=(), server_shards=0, server_written=0, landing=None):
        self._landing = landing
        self._manifest = manifest
        self._miss_paths = list(miss_paths)
        self._server = (server_shards, server_written)
        self._shard_table_cache = {}
        self._SHARD_CACHE_MAX = 30
        self.resolve_calls = []
        self.saved = None

    @property
    def _landing_root(self):
        return self._landing

    def _load_cache_manifest(self):
        return self._manifest

    def _save_cache_manifest(self, manifest):
        self.saved = manifest

    def _cache_key(self, table, bs, be):
        return CKEY

    def _resolve_shard_paths(self, table, freq, batches, qdb_tbl, _is_big):
        self.resolve_calls.append((table, freq, tuple(batches)))
        self._last_export_meta = {"key": (table, freq, tuple(batches)),
                                 "shards": self._server[0], "written": self._server[1]}
        return list(self._miss_paths), "export"


def _call(stub):
    return stub._fetch_export_cached(TABLE, FREQ, [(BS, BE)], "qdb_etf_minutes",
                                    _is_big=False, codes=None)


def test_t1_true_empty_window_writes_tombstone():
    stub = _Stub({}, server_shards=0, server_written=0)
    frames, _ = _call(stub)
    assert frames == []
    entry = (stub.saved or {}).get(TABLE, {}).get(CKEY)
    assert entry is not None and entry.get("empty") is True, "真·空窗应写墓碑"
    assert "empty_ts" in entry and int(entry["empty_ttl_s"]) > 0
    assert "shards" not in entry, "硬不变量①：墓碑不得含 shards（整体替换、无 merge）"


def test_t2_tombstone_hit_skips_refetch_without_renewing_ttl():
    old_ts = "2026-09-20T10:00:00"
    manifest = {TABLE: {CKEY: {"empty": True, "empty_ts": old_ts, "empty_ttl_s": 604800}}}
    stub = _Stub(manifest)
    frames, _ = _call(stub)
    assert stub.resolve_calls == [], "TTL 内命中墓碑 → 不得重取"
    assert stub.saved is None, "命中不得置 manifest_dirty（防 TTL 无限续期）"
    assert manifest[TABLE][CKEY]["empty_ts"] == old_ts, "empty_ts 不得被刷新"


def test_t3_expired_tombstone_falls_back_to_refetch():
    manifest = {TABLE: {CKEY: {"empty": True, "empty_ts": "2020-01-01T00:00:00",
                               "empty_ttl_s": 60}}}
    stub = _Stub(manifest, server_shards=1, server_written=0)   # 服务端有分片但落盘 0（异常态）
    _call(stub)
    assert len(stub.resolve_calls) == 1, "TTL 过期 → 应重取"


def test_t4_server_nonzero_shards_no_tombstone():
    """谓词②拒绝：服务端非零分片（重取无产出）→ 不写墓碑（保护 c44c81f 语义）。"""
    stub = _Stub({}, miss_paths=[], server_shards=2, server_written=2)
    _call(stub)
    entry = (stub.saved or {}).get(TABLE, {}).get(CKEY)
    assert entry is None, "非真·空窗不得写墓碑"


def test_t5_existing_entry_no_tombstone(tmp_path):
    """谓词④拒绝：该 ckey 已有条目 → 不写墓碑（互斥，防止 empty 与 shards 共存）。"""
    manifest = {TABLE: {CKEY: {"job_id": "export", "shards": ["s1"], "shard_sizes": {"s1": 5}}}}
    stub = _Stub(manifest, server_shards=0, server_written=0, landing=tmp_path)
    _call(stub)
    assert stub.saved is None, "既有条目存在 → 不得发生任何 manifest 写入（无墓碑）"
    assert manifest[TABLE][CKEY]["shards"] == ["s1"], "既有条目应保持原样"
    assert "empty" not in manifest[TABLE][CKEY]


def test_t6_real_entry_write_replaces_stale_tombstone(tmp_path):
    """硬不变量①（真实条目方向）：真实条目写入为**整体替换**——过期墓碑的 `empty` 字段必须消失。"""
    landing = tmp_path / "landing"
    (landing / "export").mkdir(parents=True)
    f = landing / "export" / "s1.parquet"
    pd.DataFrame({"a": [1]}).to_parquet(f)          # 真 parquet（避 pyarrow 反序列化失败）
    manifest = {TABLE: {CKEY: {"empty": True, "empty_ts": "2020-01-01T00:00:00",
                               "empty_ttl_s": 1}}}
    stub = _Stub(manifest, miss_paths=[f], server_shards=1, server_written=1, landing=landing)
    _call(stub)
    entry = (stub.saved or {}).get(TABLE, {}).get(CKEY)
    assert entry is not None and entry.get("empty") is None, "旧 empty 字段必须消失（整体替换，无 merge）"
    assert entry.get("shards") == ["s1"]


def test_t7_ttl_env_and_default(monkeypatch):
    monkeypatch.delenv("QS_EXPORT_EMPTY_TTL_S", raising=False)
    assert ma.MCPAdapter._empty_ttl_s() == 604800
    monkeypatch.setenv("QS_EXPORT_EMPTY_TTL_S", "3600")
    assert ma.MCPAdapter._empty_ttl_s() == 3600
    monkeypatch.setenv("QS_EXPORT_EMPTY_TTL_S", "bad")
    assert ma.MCPAdapter._empty_ttl_s() == 604800


def test_t8_tombstone_active_edges():
    from datetime import datetime
    now = datetime(2026, 9, 25, 12, 0, 0)
    A = ma.MCPAdapter._tombstone_active
    assert A({"empty": True, "empty_ts": "2026-09-25T11:00:00", "empty_ttl_s": 7200}, now=now)
    assert not A({"empty": True, "empty_ts": "2026-09-25T11:00:00", "empty_ttl_s": 60}, now=now)
    assert not A({"empty": True, "empty_ttl_s": 7200}, now=now)              # 无 ts
    assert not A({"empty": True, "empty_ts": "2026-09-25T11:00:00"}, now=now)  # 无 ttl
    assert not A({"empty": True, "empty_ts": "bad-format", "empty_ttl_s": 7200}, now=now)
    assert not A({"shards": ["s"]}, now=now)                                  # 非墓碑
    assert not A(None, now=now) and not A("x", now=now)
