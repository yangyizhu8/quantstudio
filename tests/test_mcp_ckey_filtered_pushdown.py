"""P1.1（2026-09-29）：per-code 过滤下推到 per-shard 读取 —— 等价性 + 复用面回归。

硬不变量（对照设计件 §2 P1.1 / §6 回退）：
  E1 下推结果 vs 旧「全市场 concat → isin」逐行一致（同 shard 集、同 codes）；
  E2 过滤结果**不得**写入 `_shard_table_cache`（该 LRU 语义为「未过滤全市场帧」）；
  E3 `codes=None` / `["ALL"]` 仍走 `_read_ckey_cached`（全市场语义逐位不变）；
  E4 空 shard 列表 → 空 DataFrame（不上抛）；
  E5 开关 `QFQ_REANCHOR_FILTER_PUSHDOWN=0` → 回退旧行为，结果与 E1 一致。

全部用 tmp_path 真实 parquet，不 mock read_parquet（否则下推路径不被真正执行）。
"""
from __future__ import annotations

from collections import OrderedDict

import pandas as pd

from quantstudio.pipeline.sources import mcp_adapter as ma

TABLE, FREQ, BS, BE = "stock_daily", "daily", "2026-07-01", "2026-07-02"
CKEY = f"{TABLE}|{BS}|{BE}"
JOB = "exp_stock_daily_test"


def _full_market(n_codes=40, n_days=3):
    codes = [f"{i:06d}.SZ" for i in range(1, n_codes + 1)]
    rows = []
    for d in range(n_days):
        for c in codes:
            rows.append({"ts_code": c,
                         "trade_date": pd.Timestamp("2026-07-01") + pd.Timedelta(days=d),
                         "open": 1.0 + d, "close": 2.0 + d, "adj_factor": 1.0})
    return pd.DataFrame(rows)


def _write_shards(landing_root, df, n_parts=2):
    """在 landing_root/JOB 下写 n_parts 个 parquet 分片，返回 manifest 条目与路径。"""
    d = landing_root / JOB
    d.mkdir(parents=True, exist_ok=True)
    chunks = [df.iloc[i::n_parts].reset_index(drop=True) for i in range(n_parts)]
    names, sizes, paths = [], {}, []
    for i, ch in enumerate(chunks):
        p = d / f"part_{i:05d}.parquet"
        ch.to_parquet(p, index=False)
        names.append(p.stem)
        sizes[p.stem] = p.stat().st_size
        paths.append(p)
    entry = {"job_id": JOB, "shards": names, "shard_sizes": sizes,
             "bytes": sum(sizes.values()), "rows": len(df),
             "ts": "2026-09-29T22:00:00"}
    return entry, paths


class _Stub(ma.MCPAdapter):
    """最小桩：绕 __init__，只提供 _fetch_export_cached 所需面。"""

    def __init__(self, manifest, landing, miss_paths=()):
        self._landing = landing
        self._manifest = manifest
        self._miss_paths = list(miss_paths)
        self._shard_table_cache = OrderedDict()
        self._SHARD_CACHE_MAX = 30
        self._ckey_meta_cache = OrderedDict()
        self._CKEY_META_CACHE_MAX = 256
        self.resolve_calls = []

    @property
    def _landing_root(self):
        return self._landing

    def _load_cache_manifest(self):
        return self._manifest

    def _save_cache_manifest(self, manifest):
        pass

    def _cache_key(self, table, bs, be):
        return CKEY

    def _resolve_shard_paths(self, table, freq, batches, qdb_tbl, _is_big):
        self.resolve_calls.append((table, freq, tuple(batches)))
        self._last_export_meta = {"key": (table, freq, tuple(batches)),
                                 "shards": len(self._miss_paths),
                                 "written": len(self._miss_paths)}
        return list(self._miss_paths), "export"


def _call(stub, codes):
    return stub._fetch_export_cached(TABLE, FREQ, [(BS, BE)], "qdb_stock_daily",
                                    _is_big=False, codes=codes)


def _hit_stub(tmp_path, df, codes):
    entry, paths = _write_shards(tmp_path, df)
    manifest = {TABLE: {CKEY: entry}}
    stub = _Stub(manifest, tmp_path)
    frames, _ = _call(stub, codes)
    return stub, pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _legacy_expected(paths, want):
    """旧路径语义：全市场 concat → astype(str).isin。"""
    parts = [pd.read_parquet(p) for p in paths]
    full = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if len(full):
        full = full[full["ts_code"].astype(str).isin(want)]
    return full


def test_e1_e5_pushdown_equals_legacy(tmp_path, monkeypatch):
    """E1/E5：下推开启与关闭，均与旧「全市场 concat → isin」逐行一致。"""
    df = _full_market()
    want = {"000002.SZ", "000007.SZ"}
    _entry, paths = _write_shards(tmp_path, df)
    expected = _legacy_expected(paths, want)

    for flag in (True, False):
        monkeypatch.setattr(ma, "_QFQ_FILTER_PUSHDOWN", flag)
        adapter = _Stub({}, tmp_path)
        got = adapter._read_ckey_filtered(CKEY, paths, want)
        pd.testing.assert_frame_equal(
            got.sort_values(["ts_code", "trade_date"]).reset_index(drop=True),
            expected.sort_values(["ts_code", "trade_date"]).reset_index(drop=True))


def test_e1_multi_codes_and_single_code(tmp_path, monkeypatch):
    """E1：单码 / 多码两种口径均与旧路径一致。"""
    df = _full_market()
    _entry, paths = _write_shards(tmp_path, df, n_parts=3)
    adapter = _Stub({}, tmp_path)
    for want in ({"000001.SZ"}, {"000003.SZ", "000010.SZ", "000039.SZ"}):
        expected = _legacy_expected(paths, want)
        got = adapter._read_ckey_filtered(CKEY, paths, want)
        pd.testing.assert_frame_equal(
            got.sort_values(["ts_code", "trade_date"]).reset_index(drop=True),
            expected.sort_values(["ts_code", "trade_date"]).reset_index(drop=True))


def test_e2_filtered_result_not_written_to_full_market_lru(tmp_path):
    """E2：下推/过滤结果**不得**污染 `_shard_table_cache`（全市场 LRU）。"""
    df = _full_market()
    stub, frame = _hit_stub(tmp_path, df, ["000002.SZ"])
    assert len(frame) == 3 and set(frame["ts_code"].unique()) == {"000002.SZ"}
    assert len(stub._shard_table_cache) == 0, "过滤结果不得写入全市场帧 LRU"


def test_e3_all_path_uses_full_market_reader(tmp_path, monkeypatch):
    """E3：codes=["ALL"] 仍走 `_read_ckey_cached`（全市场语义），且不碰下推路径。"""
    df = _full_market(n_codes=10)
    monkeypatch.setattr(ma.MCPAdapter, "_read_ckey_filtered",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("ALL 路径不得走过滤下推")))
    stub, frame = _hit_stub(tmp_path, df, ["ALL"])
    assert len(frame) == len(df)
    assert len(stub._shard_table_cache) == 1, "ALL 路径应仍填充全市场帧 LRU"


def test_e4_empty_shards_returns_empty(tmp_path):
    """E4：空 shard 列表 → 空 DataFrame，不上抛（沿用 jabberwock 守卫语义）。"""
    adapter = _Stub({}, tmp_path)
    got = adapter._read_ckey_filtered(CKEY, [], {"000001.SZ"})
    assert len(got) == 0


def test_e6_miss_path_also_filtered(tmp_path, monkeypatch):
    """E6（未命中分支）：落盘后同口径过滤，不回退全市场 concat。"""
    df = _full_market()
    _entry, paths = _write_shards(tmp_path, df)
    stub = _Stub({}, tmp_path, miss_paths=paths)
    frames, _ = _call(stub, ["000005.SZ"])
    frame = pd.concat(frames, ignore_index=True)
    assert set(frame["ts_code"].unique()) == {"000005.SZ"}
    assert len(stub.resolve_calls) == 1  # 确实走了未命中分支
    assert len(stub._shard_table_cache) == 0

