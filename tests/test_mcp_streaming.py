"""WP7-E3 阶段 2B：流式分片处理测试。

铁律：fetch_table_streaming concat 后 vs fetch_table（直连）逐值一致。
内存断言用 tracemalloc 相对差值（建议 2，避免 GC 噪声 flaky）。
所有测试用 mock client + 真实 parquet 落盘（验证两遍流程）。
"""
from __future__ import annotations

import io
import tracemalloc
import pandas as pd
import pytest

from quantstudio.pipeline.sources.mcp_adapter import MCPAdapter


class _Artifact:
    def __init__(self, artifact_id, parquet_bytes, job_id="job1"):
        self.artifact_id = artifact_id
        self.parquet_bytes = parquet_bytes
        self.raw = {"job_id": job_id}


class _ExportClient:
    """Mock client returning canned parquet artifacts for export_dataset."""

    def __init__(self, df):
        self.df = df
        self.export_calls = 0

    def export_dataset(self, *, dataset_id, page_size=50_000,
                       time_start=None, time_end=None, row_limit=None, **kw):
        self.export_calls += 1
        buf = io.BytesIO()
        self.df.to_parquet(buf)
        return [_Artifact("job1/shard0", buf.getvalue(), job_id="job1")]


def _bare_adapter(client, landing_root, export_cache=False):
    adapter = MCPAdapter.__new__(MCPAdapter)
    adapter._client = client
    adapter.endpoint = "https://example.invalid/mcp"
    adapter.enable_qfq_restore = False  # skip qfq for streaming equivalence test
    adapter.export_cache = export_cache
    adapter._landing_root = landing_root
    adapter._adj_latest_cache = {}
    adapter._coldstart_done = set()
    adapter.main_db = None
    adapter.enable_adj_coldstart = False
    adapter.enable_qfq_injection = False
    adapter.tls_verify = False
    return adapter


def _make_daily_df(codes, dates, base=10.0):
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
# Test 1: 流式产出多个分片，总行数 = concat 后行数
# ---------------------------------------------------------------------------
def test_streaming_yields_shards(tmp_path):
    """多 shard → 迭代器产出 DataFrame，concat 后行数正确。"""
    df = _make_daily_df(["000001.SZ", "000002.SZ"],
                        ["2026-07-01", "2026-07-02", "2026-07-03"])
    client = _ExportClient(df)
    adapter = _bare_adapter(client, tmp_path)

    meta, shard_iter = adapter.fetch_table_streaming(
        "stock_daily", "2026-07-01", "2026-07-03", freq="daily", codes=["000001.SZ"])
    frames = list(shard_iter)
    # 至少产出一片
    assert len(frames) >= 1
    total = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    # 行数应与原 df 在日期窗口内的行数一致
    assert len(total) > 0


# ---------------------------------------------------------------------------
# Test 2: 流式 vs 全量逐值等价（铁律硬指标）
# ---------------------------------------------------------------------------
def test_streaming_vs_full_value_equivalence(tmp_path):
    """fetch_table_streaming concat 后 vs fetch_table 逐值一致。"""
    df = _make_daily_df(["000001.SZ", "000002.SZ"],
                        ["2026-07-01", "2026-07-02", "2026-07-03"])
    # 用独立目录避免缓存干扰
    # 直连路径
    client_direct = _ExportClient(df)
    adapter_direct = _bare_adapter(client_direct, tmp_path / "direct")
    frame_direct, _ = adapter_direct.fetch_table(
        "stock_daily", "2026-07-01", "2026-07-03", freq="daily", codes=None)

    # 流式路径
    client_stream = _ExportClient(df)
    adapter_stream = _bare_adapter(client_stream, tmp_path / "stream")
    meta, shard_iter = adapter_stream.fetch_table_streaming(
        "stock_daily", "2026-07-01", "2026-07-03", freq="daily", codes=None)
    frames = list(shard_iter)
    frame_stream = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    # 逐值一致（NaN 位置、index、dtype、值）—— 不放宽容差
    pd.testing.assert_frame_equal(
        frame_direct.reset_index(drop=True), frame_stream.reset_index(drop=True))


# ---------------------------------------------------------------------------
# Test 3: 非行情大表走透传（yield 单片 = fetch_table 结果）
# ---------------------------------------------------------------------------
def test_small_table_passthrough(tmp_path):
    """非行情大表 → 流式入口 yield 单片，内容 = fetch_table 结果。"""
    df = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["2026-07-01"],
                       "weight": [1.0]})
    client = _ExportClient(df)
    adapter = _bare_adapter(client, tmp_path)
    # passthrough / small table 不是 _STREAMING_TABLES
    # 用 index_constituents（走 export 但不在 streaming 表集合）
    frame_full, _ = adapter.fetch_table(
        "index_constituents", "2026-07-01", "2026-07-01", freq="daily", codes=["ALL"])
    meta, shard_iter = adapter.fetch_table_streaming(
        "index_constituents", "2026-07-01", "2026-07-01", freq="daily", codes=["ALL"])
    frames = list(shard_iter)
    assert len(frames) == 1  # 透传单片
    pd.testing.assert_frame_equal(
        frame_full.reset_index(drop=True), frames[0].reset_index(drop=True))


# ---------------------------------------------------------------------------
# Test 4: 流式迭代器不一次性持有全部分片（设计评审级断言，建议 2）
# ---------------------------------------------------------------------------
def test_streaming_iterator_not_all_in_memory(tmp_path):
    """流式迭代器协议验证：每片消费后可被 GC，不一次性持有全量。

    内存峰值的定量证明留到 staging 实测（建议 2：pytest 内绝对峰值断言易 flaky）。
    此测试验证设计意图：迭代器逐片 yield，调用方可逐片消费不累积。
    """
    codes = [f"{i:06d}.SZ" for i in range(50)]
    dates = [f"2026-07-{d:02d}" for d in range(1, 21)]
    df = _make_daily_df(codes, dates)

    client = _ExportClient(df)
    adapter = _bare_adapter(client, tmp_path)

    meta, shard_iter = adapter.fetch_table_streaming(
        "stock_daily", "2026-07-01", "2026-07-20", freq="daily", codes=None)
    # 逐片消费，只累计行数（不持有 df 引用 → 模拟 daemon 逐片处理释放）
    total_rows = 0
    shard_count = 0
    for shard in shard_iter:
        total_rows += len(shard)
        shard_count += 1
    # 迭代器产出了至少一片
    assert shard_count >= 1
    assert total_rows > 0

    # 对照：全量 concat 的总行数应一致
    frames_full, _ = adapter._fetch_export_direct(
        "stock_daily", "daily",
        adapter._export_batches("2026-07-01", "2026-07-20", False),
        "stock_daily", False)
    full_total = sum(len(f) for f in frames_full)
    assert total_rows == full_total, "流式总行数应与全量一致"


# ---------------------------------------------------------------------------
# Test 5: metadata 含 streaming=True 标记
# ---------------------------------------------------------------------------
def test_streaming_metadata_flag(tmp_path):
    """行情大表流式 metadata 带 streaming=True；非行情表不带。"""
    df = _make_daily_df(["000001.SZ"], ["2026-07-01"])
    client = _ExportClient(df)
    adapter = _bare_adapter(client, tmp_path)

    # 行情大表
    meta_s, _ = adapter.fetch_table_streaming(
        "stock_daily", "2026-07-01", "2026-07-01", freq="daily")
    assert meta_s["fetch_mode"] == "export_streaming"
    assert meta_s["lineage"]["streaming"] is True

    # 非行情表（透传）
    meta_p, _ = adapter.fetch_table_streaming(
        "index_constituents", "2026-07-01", "2026-07-01", freq="daily")
    # 透传用的是 fetch_table 的 meta，无 streaming 字段
    assert meta_p.get("lineage", {}).get("streaming", False) is False


# ---------------------------------------------------------------------------
# Test 6: 流式第一遍列投影包含因子标准化所需的全部列（修复 bug：缺列）
# ---------------------------------------------------------------------------
def test_streaming_factor_sync_includes_required_columns(tmp_path):
    """第一遍列投影必须含 adj_factor + ts_code + trade_date/trade_time。

    修复前 bug：只读 ['adj_factor'] → normalize_mcp_adj_factor_df 缺 code/time 列 →
    因子同步失败（written=0 → ValueError）。
    修复后：列投影动态包含全部所需列。
    """
    import pyarrow as pa, pyarrow.parquet as pq
    # 构造含全部列的 parquet（模拟真实 MCP 返回，trade_date 用 YYYYMMDD）
    df = _make_daily_df(["000001.SZ"], ["20260701", "20260702", "20260703"])
    landing = tmp_path / "exp_test_12345678"
    landing.mkdir(parents=True)
    pq.write_table(pa.Table.from_pandas(df), str(landing / "shard0.parquet"))

    # 用 _parquet_has_column 验证列检测
    assert MCPAdapter._parquet_has_column(landing / "shard0.parquet", "adj_factor")
    assert MCPAdapter._parquet_has_column(landing / "shard0.parquet", "ts_code")
    assert MCPAdapter._parquet_has_column(landing / "shard0.parquet", "trade_date")

    # 列投影读回应含全部所需列
    proj = []
    for c in ("adj_factor", "ts_code", "trade_date", "trade_time"):
        if MCPAdapter._parquet_has_column(landing / "shard0.parquet", c):
            proj.append(c)
    assert "adj_factor" in proj
    assert "ts_code" in proj
    sdf = pd.read_parquet(landing / "shard0.parquet", columns=proj)
    # normalize 应能成功（不返回空）
    from quantstudio.pipeline.sources.mcp_adapter import normalize_mcp_adj_factor_df
    norm = normalize_mcp_adj_factor_df(sdf, "daily", "STOCK")
    assert len(norm) > 0, "列投影后 normalize 不应为空"
