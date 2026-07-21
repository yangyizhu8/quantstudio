"""aligner._derive_market_value 专项测试。

这层是 PIT 正确性的关键防线（circ_mv/total_mv = 股本 × 报告期末日 close）。
覆盖两类已知 bug：
  1. merge_asof(by=code) 全列 _t 单调性陷阱（多 code 时必须逐组做）
  2. merge_asof 重建 RangeIndex → 后续 df.loc 赋值索引错位

若此测试回归，说明 aligner 补算层被破坏，必须立即修。
"""
import pandas as pd
import pytest

from quantstudio.pipeline.aligner import FieldAligner


@pytest.fixture
def aligner():
    """最小可用 aligner（不依赖 alignment_rules.json）。"""
    return FieldAligner({"schemas": {}, "source_mappings": {}})


def _make_df(codes_enddates, free_share=2e10, total_share=3e10):
    """构造 stock_float_share 测试 df，code+end_date 自定义。
    codes_enddates: list of (code, end_date_ms)
    """
    return pd.DataFrame(
        [{"code": c, "end_date": ed, "free_share": free_share,
          "total_share": total_share, "circ_mv": pd.NA, "total_mv": pd.NA}
         for c, ed in codes_enddates]
    )


def _make_close(rows):
    """构造 close_df。rows: list of (code, time_ms, close)."""
    return pd.DataFrame(rows, columns=["code", "time", "close"])


# ========== 1. 单 code：基础补算 + backward 语义 ==========

def test_single_code_basic_fill(aligner):
    """单 code、end_date 恰好是交易日 → close 当日值，circ_mv = free_share × close。"""
    df = _make_df([("600000", 1774886400000)])  # 2026-03-30
    close = _make_close([("600000", 1774886400000, 10.18),
                         ("600000", 1774800000000, 10.01)])
    aligner._derive_market_value(df, close)
    assert df["circ_mv"].notna().all()
    assert abs(df["circ_mv"].iloc[0] - 2e10 * 10.18) < 1.0


def test_backward_picks_recent_trading_day(aligner):
    """end_date 是非交易日（周末）→ backward 取 ≤ end_date 的最近交易日 close。"""
    # 2026-03-28 是周六，最近交易日是 03-27 周五
    df = _make_df([("600000", 1774972800000)])  # 2026-03-28 周六
    close = _make_close([("600000", 1774886400000, 10.18),    # 03-27 周五
                         ("600000", 1774800000000, 10.01)])    # 03-26 周四
    aligner._derive_market_value(df, close)
    # 应取 03-27 的 10.18（≤周六的最近交易日）
    assert abs(df["circ_mv"].iloc[0] - 2e10 * 10.18) < 1.0


def test_no_close_before_end_date_skips(aligner):
    """close 全部晚于 end_date → 无可对齐 close，circ_mv 留空。"""
    df = _make_df([("600000", 1774886400000)])
    close = _make_close([("600000", 1775059200000, 10.5),    # 全部晚于
                         ("600000", 1775145600000, 10.8)])
    aligner._derive_market_value(df, close)
    assert df["circ_mv"].isna().all()


# ========== 2. 多 code：merge_asof by=code 单调性陷阱 ==========

def test_multi_code_no_sort_error(aligner):
    """多 code 场景 merge_asof 不报 'left keys must be sorted'。
    这是已知 bug：pandas merge_asof(by=code) 仍校验整列 _t 单调，
    按 (code, _t) 排序后跨 code 不单调 → 报错。
    逐组做 merge_asof 才能避开。
    """
    # 两个 code 的 end_date 交错：600000 早 / 000001 晚
    df = _make_df([("600000", 1522425600000),    # 2018-03-31
                   ("000001", 1774886400000)])   # 2026-03-30
    close = _make_close([("600000", 1522425600000, 11.0),
                         ("600000", 1774886400000, 12.0),
                         ("000001", 1522425600000, 13.0),
                         ("000001", 1774886400000, 14.0)])
    # 不应抛异常
    result = aligner._derive_market_value(df, close)
    assert result == "derive_mv_done"
    assert df["circ_mv"].notna().all()


def test_multi_code_per_code_isolation(aligner):
    """不同 code 的 close 互不串味（by=code 隔离正确）。"""
    df = _make_df([("600000", 1774886400000),
                   ("000001", 1774886400000)])
    close = _make_close([("600000", 1774886400000, 10.0),
                         ("000001", 1774886400000, 20.0)])
    aligner._derive_market_value(df, close)
    mv = dict(zip(df["code"], df["circ_mv"]))
    assert abs(mv["600000"] - 2e10 * 10.0) < 1.0
    assert abs(mv["000001"] - 2e10 * 20.0) < 1.0


# ========== 3. 索引错位：df.index 非 RangeIndex ==========

def test_non_default_index_no_misalign(aligner):
    """df.index 非 0..n（如 [100,200,300,...]）→ 补算值仍正确写回对应行。
    这是已知 bug：merge_asof 重建 RangeIndex，df.loc[mask]=calc_mv[mask]
    索引对不上 → 全部不写。
    """
    df = _make_df([("600000", 1774886400000),
                   ("600000", 1774800000000),
                   ("600000", 1774540800000)])
    df.index = [100, 200, 300]    # 故意非默认
    close = _make_close([("600000", 1774886400000, 10.18),
                         ("600000", 1774800000000, 10.01),
                         ("600000", 1774540800000, 10.02)])
    aligner._derive_market_value(df, close)
    # 三行都应有值
    assert df["circ_mv"].notna().all()
    assert abs(df.loc[100, "circ_mv"] - 2e10 * 10.18) < 1.0
    assert abs(df.loc[200, "circ_mv"] - 2e10 * 10.01) < 1.0
    assert abs(df.loc[300, "circ_mv"] - 2e10 * 10.02) < 1.0


def test_total_mv_also_filled(aligner):
    """total_mv 同步补算（total_share × close）。"""
    df = _make_df([("600000", 1774886400000)], free_share=2e10, total_share=3e10)
    close = _make_close([("600000", 1774886400000, 10.0)])
    aligner._derive_market_value(df, close)
    assert abs(df["circ_mv"].iloc[0] - 2e10 * 10.0) < 1.0
    assert abs(df["total_mv"].iloc[0] - 3e10 * 10.0) < 1.0


# ========== 4. 源已有值：保留源值，仅补缺失 ==========

def test_preserve_existing_circ_mv(aligner):
    """源已有 circ_mv 时保留源值，不覆盖（仅缺失才补）。"""
    df = _make_df([("600000", 1774886400000)])
    df.loc[0, "circ_mv"] = 999e9   # 故意设源值
    close = _make_close([("600000", 1774886400000, 10.0)])
    aligner._derive_market_value(df, close)
    # 应保留 999e9，不被 2e10 × 10.0 = 2e11 覆盖
    assert df["circ_mv"].iloc[0] == 999e9


def test_fill_only_missing_when_partial(aligner):
    """混合：有源值的保留，无源值的补算。"""
    df = _make_df([("600000", 1774886400000),    # 这行有源值
                   ("600000", 1774800000000)])   # 这行缺失
    df.loc[0, "circ_mv"] = 999e9
    close = _make_close([("600000", 1774886400000, 10.0),
                         ("600000", 1774800000000, 11.0)])
    aligner._derive_market_value(df, close)
    assert df.loc[0, "circ_mv"] == 999e9           # 源值保留
    assert abs(df.loc[1, "circ_mv"] - 2e10 * 11.0) < 1.0   # 补算正确


# ========== 5. 边界：无 free_share / 空 df ==========

def test_no_free_share_column(aligner):
    """df 无 free_share 列 → derive_mv_skip_no_share。"""
    df = pd.DataFrame({"code": ["600000"], "end_date": [1774886400000]})
    close = _make_close([("600000", 1774886400000, 10.0)])
    result = aligner._derive_market_value(df, close)
    assert result == "derive_mv_skip_no_share"


def test_empty_df(aligner):
    """空 df → derive_mv_skip_no_share。"""
    df = pd.DataFrame(columns=["code", "end_date", "free_share"])
    close = _make_close([("600000", 1774886400000, 10.0)])
    result = aligner._derive_market_value(df, close)
    assert result == "derive_mv_skip_no_share"


def test_close_df_empty(aligner):
    """close_df 空 → 所有行 circ_mv 留空，不报错。"""
    df = _make_df([("600000", 1774886400000)])
    close = pd.DataFrame(columns=["code", "time", "close"])
    result = aligner._derive_market_value(df, close)
    assert result == "derive_mv_done"
    assert df["circ_mv"].isna().all()


# ========== 6. DuckDB ASOF JOIN 快路径（close_df > 5 万行触发）==========

def test_duckdb_path_large_close_df(aligner):
    """close_df > 5 万行 → 走 DuckDB ASOF JOIN 快路径（不再是逐组 pandas）。

    回归保护：大数据场景必须正确路由到 DuckDB 路径，
    否则全市场（5201 codes × 8.5 年 close ≈ 950 万行）pandas 路径要 348s（实测）。
    """
    # 构造 5 万 + 行 close_df（单一 code 也行，验证 ASOF JOIN 路由正确）
    # 单 code × 多日 close
    times = list(range(1700000000000, 1700000000000 + 60001 * 86400000, 86400000))
    close_rows = [("600000", t, 10.0 + (i % 100) * 0.01) for i, t in enumerate(times)]
    close = _make_close(close_rows)   # 60001 行 > 5 万阈值
    assert len(close) > 50_000   # 确认触发了快路径阈值

    # df 仅一行，end_date 取 close 中间某个时间
    target_t = times[30000]
    df = _make_df([("600000", target_t)])
    result = aligner._derive_market_value(df, close)
    assert result == "derive_mv_done"
    # 应取到 ≤ target_t 的最近 close
    assert df["circ_mv"].notna().all()
    # ASOF JOIN 找到 ≤ target_t 的最近交易日 close
    expected_close = 10.0 + (30000 % 100) * 0.01
    assert abs(df["circ_mv"].iloc[0] - 2e10 * expected_close) < 1.0


def test_duckdb_path_multi_code_correctness(aligner):
    """DuckDB 路径多 code 隔离正确（不同 code 的 close 不串味）。

    构造 2 个 code 各 3 万行 close（总 6 万 > 5 万触发快路径），
    验证 ASOF JOIN 的 by=code 隔离。
    """
    base = 1700000000000
    rows_a = [("600000", base + i * 86400000, 10.0) for i in range(30000)]
    rows_b = [("000001", base + i * 86400000, 20.0) for i in range(30000)]
    close = _make_close(rows_a + rows_b)
    assert len(close) > 50_000

    target_t = base + 15000 * 86400000   # 两边都有数据
    df = _make_df([("600000", target_t), ("000001", target_t)])
    result = aligner._derive_market_value(df, close)
    assert result == "derive_mv_done"
    mv = dict(zip(df["code"], df["circ_mv"]))
    assert abs(mv["600000"] - 2e10 * 10.0) < 1.0
    assert abs(mv["000001"] - 2e10 * 20.0) < 1.0


def test_duckdb_path_non_default_index(aligner):
    """DuckDB 路径下 df.index 非 RangeIndex 也正确回填（_row_id 映射）。

    回归保护：DuckDB 路径用 _row_id 列做结果回填，确保不依赖 df.index 形式。
    """
    base = 1700000000000
    rows = [("600000", base + i * 86400000, 15.0) for i in range(50001)]
    close = _make_close(rows)
    df = _make_df([("600000", base + 25000 * 86400000)])
    df.index = [999]   # 故意非默认
    result = aligner._derive_market_value(df, close)
    assert result == "derive_mv_done"
    assert abs(df.loc[999, "circ_mv"] - 2e10 * 15.0) < 1.0


def test_duckdb_path_preserves_existing_circ_mv(aligner):
    """DuckDB 路径下源已有 circ_mv 时保留源值，仅补缺失。"""
    base = 1700000000000
    rows = [("600000", base + i * 86400000, 15.0) for i in range(50001)]
    close = _make_close(rows)
    df = _make_df([("600000", base + 25000 * 86400000)])
    df.loc[0, "circ_mv"] = 999e9   # 源值
    result = aligner._derive_market_value(df, close)
    assert result == "derive_mv_done"
    assert df["circ_mv"].iloc[0] == 999e9   # 源值保留


def test_duckdb_path_no_close_before_end_date(aligner):
    """DuckDB 路径下 close 全部晚于 end_date → circ_mv 留空。"""
    base = 1700000000000
    # close 全部晚于 end_date
    rows = [("600000", base + (i + 100) * 86400000, 15.0) for i in range(50001)]
    close = _make_close(rows)
    df = _make_df([("600000", base)])   # end_date 早于所有 close
    result = aligner._derive_market_value(df, close)
    assert result == "derive_mv_done"
    assert df["circ_mv"].isna().all()

