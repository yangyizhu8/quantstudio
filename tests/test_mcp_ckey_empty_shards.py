# -*- coding: utf-8 -*-
"""jabberwock 缺陷验收：ckey 缓存命中但 shard 全缺 → 重取仍空 → **IndexError** 停摆重锚环。

现象（客户机）：`[qfq_orch] 周期异常停摆重锚环`
缺陷链（取证）：
  ① manifest 有该 ckey 条目，但 shard 文件全部缺失 → 命中校验失败 → 回退直连重取
  ② `_resolve_shard_paths()` 重取后仍无产出 → `miss_paths = []`
  ③ `_read_ckey_cached(ckey, [])` → `shard_paths[0]` → **IndexError**（周期中断）

判据：
  C1 直接调用 `_read_ckey_cached(ckey, [])` → **不得 IndexError**（降级为空 DataFrame）
  C2 端到端 `_fetch_export_cached`：manifest 命中但 shard 缺 + 重取空 → **不得抛异常**，
     返回空结果且**不污染 manifest**（不写 0-shard 条目）
  C3 正常路径不受影响：shard 存在且校验通过 → 命中缓存读取（行为不变）
"""
from __future__ import annotations

import json
import sqlite3

import pandas as pd
import pytest

from quantstudio.pipeline.sources import mcp_adapter as ma

TABLE = "stock_daily"
FREQ = "daily"
BS, BE = "2026-09-01", "2026-09-30"
CKEY = f"{TABLE}|{BS}|{BE}"


class _StubAdapter(ma.MCPAdapter):
    """最小桩：只提供 _fetch_export_cached / _read_ckey_cached 所需面。"""

    def __init__(self, landing_root, manifest, miss_paths):
        self._landing = landing_root
        self._manifest = manifest
        self._miss_paths = miss_paths
        self._shard_table_cache = {}
        self._SHARD_CACHE_MAX = 30

    # --- 被桩替换的依赖 ---
    @property
    def _landing_root(self):
        return self._landing

    def _load_cache_manifest(self):
        return self._manifest

    def _save_cache_manifest(self, manifest):
        self._saved = manifest

    def _cache_key(self, table, bs, be):
        return CKEY

    def _resolve_shard_paths(self, table, freq, batches, qdb_tbl, _is_big):
        return list(self._miss_paths), "export"


@pytest.fixture()
def env(tmp_path):
    landing = tmp_path / "landing"
    (landing / "export").mkdir(parents=True)
    # manifest 有该 ckey（shards 声明存在、size 声明 123），但**文件实际不存在**
    manifest = {TABLE: {CKEY: {"job_id": "export", "shards": ["s1", "s2"],
                              "shard_sizes": {"s1": 123, "s2": 456}, "bytes": 579, "rows": 10}}}
    return _StubAdapter(landing, manifest, miss_paths=[])


def test_c1_read_ckey_cached_empty_shards_no_indexerror(env):
    """C1：空 shard 列表必须降级（不得 IndexError）。"""
    df = env._read_ckey_cached(CKEY, [])
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_c2_end_to_end_no_raise_and_manifest_not_poisoned(env):
    """C2：命中但 shard 缺 + 重取空 → 不抛异常、返回空、不写 0-shard 条目。"""
    frames, job_id = env._fetch_export_cached(TABLE, FREQ, [(BS, BE)], "qdb_stock_daily",
                                              _is_big=False, codes=None)
    assert frames == []
    assert job_id == "export"
    saved = getattr(env, "_saved", None)
    if saved is not None:
        entry = (saved.get(TABLE) or {}).get(CKEY) or {}
        assert entry.get("shards") != [], "不得写入 0-shard 条目（防污染 manifest 使后续永久 miss/异常）"


def test_c3_normal_hit_path_unchanged(tmp_path):
    """C3：正常命中路径行为不变（shard 存在且 size 匹配 → 读缓存返回数据）。"""
    landing = tmp_path / "landing"
    (landing / "export").mkdir(parents=True)
    s1 = landing / "export" / "s1.parquet"
    pd.DataFrame({"ts_code": ["600519"], "close": [1700.0]}).to_parquet(s1)
    size = s1.stat().st_size
    manifest = {TABLE: {CKEY: {"job_id": "export", "shards": ["s1"],
                              "shard_sizes": {"s1": size}, "bytes": size, "rows": 1}}}
    stub = _StubAdapter(landing, manifest, miss_paths=[])
    frames, _ = stub._fetch_export_cached(TABLE, FREQ, [(BS, BE)], "qdb_stock_daily",
                                         _is_big=False, codes=None)
    assert len(frames) == 1 and len(frames[0]) == 1
