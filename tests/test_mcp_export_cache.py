"""WP7-E3 阶段 2A：export 缓存层测试（Raw Landing artifact 复用）。

铁律：缓存路径 vs 直连路径逐值一致（NaN 位置、index、dtype、值）。
所有测试用 mock client，不触碰真实网络。
"""
from __future__ import annotations

import json
import pandas as pd
import pytest

from quantstudio.pipeline.sources.mcp_adapter import MCPAdapter


class _Artifact:
    """Mock Artifact with parquet_bytes and job_id in raw."""

    def __init__(self, artifact_id: str, parquet_bytes: bytes, job_id: str = "job1"):
        self.artifact_id = artifact_id
        self.parquet_bytes = parquet_bytes
        self.raw = {"job_id": job_id}


class _ExportClient:
    """Mock client that records export_dataset calls and returns canned Artifacts.

    Each call returns a single Artifact with parquet_bytes for the canned df.
    Ignores the actual time range (the _norm_date filter in _fetch_export does
    the real date裁剪).
    """

    def __init__(self, df):
        self.df = df
        self.export_calls: list = []

    def export_dataset(self, *, dataset_id, page_size=50_000,
                       time_start=None, time_end=None, row_limit=None, **kw):
        self.export_calls.append((dataset_id, time_start, time_end))
        import io
        buf = io.BytesIO()
        self.df.to_parquet(buf)
        return [_Artifact(f"job1/shard0", buf.getvalue(), job_id="job1")]


def _bare_adapter(client=None, export_cache=False, landing_root=None):
    """Construct MCPAdapter bypassing __init__ (no real network)."""
    adapter = MCPAdapter.__new__(MCPAdapter)
    adapter._client = client
    adapter.endpoint = "https://example.invalid/mcp"
    adapter.enable_qfq_restore = False  # skip qfq restore for cache tests
    adapter.export_cache = export_cache
    adapter._landing_root = landing_root  # caller must provide tmp_path
    return adapter


def _make_daily_df(codes, dates, base=10.0):
    """Build a stock_daily-like DataFrame with ts_code/trade_date/open/high/low/close/adj_factor."""
    rows = []
    for code in codes:
        for i, d in enumerate(dates):
            v = base + i * 0.1
            rows.append({
                "ts_code": code, "trade_date": d,
                "open": v, "high": v + 0.05, "low": v - 0.05, "close": v,
                "vol": 1000, "amount": 10000, "pct_chg": 0.5,
                "adj_factor": 1.0,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Test 1: 缓存未命中 → export_dataset 被调用 → manifest 写入
# ---------------------------------------------------------------------------
def test_cache_miss_writes_manifest(tmp_path, monkeypatch):
    """首次 fetch → export_dataset 被调用 → manifest 写入 → parquet 落盘。"""
    df = _make_daily_df(["000001.SZ"], ["2026-07-01", "2026-07-02"])
    client = _ExportClient(df)
    adapter = _bare_adapter(client, export_cache=True, landing_root=tmp_path)

    # monkeypatch read_parquet to return the canned df (simulating parquet read)
    monkeypatch.setattr(pd, "read_parquet", lambda path: df.copy())

    frame, meta = adapter._fetch_export("stock_daily", "daily", "2026-07-01", "2026-07-02", ["000001.SZ"])

    assert len(client.export_calls) == 1  # 未命中 → 调用 export_dataset
    manifest_path = tmp_path / MCPAdapter._EXPORT_CACHE_MANIFEST
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "stock_daily" in manifest
    # 至少一个缓存键
    assert len(manifest["stock_daily"]) >= 1


# ---------------------------------------------------------------------------
# Test 2: 缓存命中 → export_dataset 不再被调用（call_count==1）→ 逐值一致
# ---------------------------------------------------------------------------
def test_cache_hit_skips_export(tmp_path, monkeypatch):
    """同 (table, grid_bs|grid_be) 二次 fetch → export_dataset 不被调用 → 结果一致。"""
    df = _make_daily_df(["000001.SZ"], ["2026-07-01", "2026-07-02"])
    client = _ExportClient(df)
    adapter = _bare_adapter(client, export_cache=True, landing_root=tmp_path)
    monkeypatch.setattr(pd, "read_parquet", lambda path: df.copy())

    # 第一次 fetch（未命中）
    frame1, _ = adapter._fetch_export("stock_daily", "daily", "2026-07-01", "2026-07-02", ["000001.SZ"])
    assert len(client.export_calls) == 1

    # 第二次 fetch（应命中缓存 → 不再调 export_dataset）
    frame2, _ = adapter._fetch_export("stock_daily", "daily", "2026-07-01", "2026-07-02", ["000001.SZ"])
    assert len(client.export_calls) == 1  # 仍然是 1，没有新增调用

    # 逐值一致
    pd.testing.assert_frame_equal(frame1, frame2)


# ---------------------------------------------------------------------------
# Test 3: manifest 损坏/文件缺失 → 回退直连 → export_dataset 被调用 → 结果正确
# ---------------------------------------------------------------------------
def test_manifest_corruption_fallback(tmp_path, monkeypatch):
    """manifest 存在但 parquet 文件被删 → 回退直连 → export_dataset 被调用 → 结果正确。"""
    df = _make_daily_df(["000001.SZ"], ["2026-07-01"])
    client = _ExportClient(df)
    adapter = _bare_adapter(client, export_cache=True, landing_root=tmp_path)
    monkeypatch.setattr(pd, "read_parquet", lambda path: df.copy())

    # 第一次：正常落盘 + 写 manifest
    adapter._fetch_export("stock_daily", "daily", "2026-07-01", "2026-07-02", ["000001.SZ"])
    assert len(client.export_calls) == 1

    # 删除 parquet 文件模拟损坏（保留 manifest）
    for f in tmp_path.rglob("*.parquet"):
        f.unlink()
    # 重置 call count
    client.export_calls.clear()

    # 第二次：应回退直连
    frame, _ = adapter._fetch_export("stock_daily", "daily", "2026-07-01", "2026-07-02", ["000001.SZ"])
    assert len(client.export_calls) == 1  # 回退直连 → 重新调用
    assert len(frame) >= 1  # 结果非空


# ---------------------------------------------------------------------------
# Test 4: 网格化批次边界一致性（同一网格单元内的不同 start → 同一边界）
# ---------------------------------------------------------------------------
def test_grid_aligned_batch_boundaries():
    """同一 10 天网格单元内的不同 start（07-01 vs 07-03）→ 对齐到同一网格边界。

    跨网格单元的 start（07-01 vs 07-08）→ 不同边界（验证网格化生效，非恒等）。
    这是缓存命中率的核心：同一单元内的证券共享缓存键。
    """
    adapter = MCPAdapter.__new__(MCPAdapter)
    # 同一网格单元（10 天）：07-01 和 07-03 落在同一 epoch-10day 格子
    batches_a = adapter._export_batches("2026-07-01", "2026-07-05", is_minute=True,
                                        grid_aligned=True)
    batches_b = adapter._export_batches("2026-07-03", "2026-07-05", is_minute=True,
                                        grid_aligned=True)
    first_bs_a = batches_a[0][0]
    first_bs_b = batches_b[0][0]
    assert first_bs_a == first_bs_b, (
        f"同一网格单元内的 start 应对齐到同一边界: A={first_bs_a} B={first_bs_b}")

    # 跨网格单元：07-01 vs 07-08（中间有 10 天边界）→ 不同边界
    batches_c = adapter._export_batches("2026-07-08", "2026-07-10", is_minute=True,
                                        grid_aligned=True)
    assert batches_c[0][0] != first_bs_a, (
        "跨网格单元的 start 应对齐到不同边界")

    # 同一缓存键 → 全证券共享
    key_a = f"stock_minutes|{batches_a[0][0]}|{batches_a[0][1]}"
    key_b = f"stock_minutes|{batches_b[0][0]}|{batches_b[0][1]}"
    assert key_a == key_b


# ---------------------------------------------------------------------------
# Test 5: 默认不网格化（grid_aligned=False）→ 按各自 start（回归保护）
# ---------------------------------------------------------------------------
def test_grid_aligned_not_default():
    """默认 grid_aligned=False → 批次边界按各自 start（与现有行为一致）。"""
    adapter = MCPAdapter.__new__(MCPAdapter)
    batches_a = adapter._export_batches("2026-07-03", "2026-07-15", is_minute=True,
                                        grid_aligned=False)
    batches_b = adapter._export_batches("2026-07-08", "2026-07-15", is_minute=True,
                                        grid_aligned=False)
    # 非网格化 → 起点不同
    assert batches_a[0][0] == "2026-07-03"
    assert batches_b[0][0] == "2026-07-08"
    assert batches_a[0][0] != batches_b[0][0]


# ---------------------------------------------------------------------------
# Test 6: 缓存路径 vs 直连路径逐值等价（铁律硬指标）
# ---------------------------------------------------------------------------
def test_cache_vs_direct_value_equivalence(tmp_path, monkeypatch):
    """缓存路径结果 vs 直连路径结果 assert_frame_equal 逐值一致。"""
    df = _make_daily_df(["000001.SZ", "000002.SZ"],
                        ["2026-07-01", "2026-07-02", "2026-07-03"])
    # 用同一 mock 数据，确保唯一变量是 export_cache 开关
    import io
    buf = io.BytesIO()
    df.to_parquet(buf)
    parquet_bytes = buf.getvalue()

    class _FixedClient:
        def __init__(self):
            self.calls = 0
        def export_dataset(self, *, dataset_id, page_size=50_000,
                           time_start=None, time_end=None, row_limit=None, **kw):
            self.calls += 1
            return [_Artifact("job1/shard0", parquet_bytes, job_id="job1")]

    # 直连路径
    client_direct = _FixedClient()
    adapter_direct = _bare_adapter(client_direct, export_cache=False, landing_root=tmp_path / "direct")
    (tmp_path / "direct").mkdir()
    monkeypatch.setattr(pd, "read_parquet", lambda path: df.copy())
    frame_direct, meta_direct = adapter_direct._fetch_export(
        "stock_daily", "daily", "2026-07-01", "2026-07-03", ["000001.SZ"])

    # 缓存路径
    client_cached = _FixedClient()
    (tmp_path / "cached").mkdir()
    adapter_cached = _bare_adapter(client_cached, export_cache=True, landing_root=tmp_path / "cached")
    frame_cached, meta_cached = adapter_cached._fetch_export(
        "stock_daily", "daily", "2026-07-01", "2026-07-03", ["000001.SZ"])

    # 逐值一致（NaN 位置、index、dtype、值）
    pd.testing.assert_frame_equal(frame_direct, frame_cached)


# ---------------------------------------------------------------------------
# Test 7: daemon 路径不受影响（export_cache=False → 无 manifest 读写）
# ---------------------------------------------------------------------------
def test_daemon_path_unaffected(tmp_path, monkeypatch):
    """export_cache=False（daemon 默认）→ _fetch_export 行为与改动前一致：每次调 export、无 manifest。"""
    df = _make_daily_df(["000001.SZ"], ["2026-07-01"])
    client = _ExportClient(df)
    adapter = _bare_adapter(client, export_cache=False, landing_root=tmp_path)
    monkeypatch.setattr(pd, "read_parquet", lambda path: df.copy())

    adapter._fetch_export("stock_daily", "daily", "2026-07-01", "2026-07-02", ["000001.SZ"])
    adapter._fetch_export("stock_daily", "daily", "2026-07-01", "2026-07-02", ["000001.SZ"])

    # 每次 fetch 都调 export_dataset（无缓存）
    assert len(client.export_calls) == 2
    # 无 manifest 文件
    manifest_path = tmp_path / MCPAdapter._EXPORT_CACHE_MANIFEST
    assert not manifest_path.exists()
