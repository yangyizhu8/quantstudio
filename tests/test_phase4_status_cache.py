# -*- coding: utf-8 -*-
"""Phase 4.5 单元测试：get_stock_status 按日缓存（纯内部缓存，语义不变）。

铁律：性能优化不得改变 get_stock_status 的返回值/列/行/排序/dtype/空值/异常行为。
覆盖：
1. 同一 date（含不同日期格式 '2026-07-14' / '20260714'）多次调用 → 底层只查 1 次
2. 不同 date → 自动重新查询（无显式失效逻辑，key 变化即换缓存）
3. 缓存路径返回值与"每次重查"基准逐位相等（assert_frame_equal check_exact=True）
4. 'code' 列缺失（空表）场景：回退空字典行为不变
"""
import pandas as pd
import pytest

from quantstudio.backtest.providers.duckdb_provider import DuckDBReferenceDataProvider
from tests.conftest import daily_row


@pytest.fixture
def ref_provider(build_db):
    db = build_db(stock_daily=[
        daily_row("600000", "2026-07-14", 10.0),
        daily_row("600001", "2026-07-14", 11.0),
        daily_row("600000", "2026-07-15", 10.5),
    ])
    return DuckDBReferenceDataProvider(db)


def _spy_queries(provider):
    """统计 query_daily_for_status 调用（透传真实查询）。"""
    calls = []
    orig = provider._data.query_daily_for_status
    provider._data.query_daily_for_status = lambda ms: calls.append(ms) or orig(ms)
    return calls


def test_same_date_cached_single_query(ref_provider):
    """同一天多次调用（含不同日期格式）→ 底层只查 1 次。"""
    p = ref_provider
    calls = _spy_queries(p)
    r1 = p.get_stock_status(["600000", "600001"], "2026-07-14")
    r2 = p.get_stock_status(["600000"], "2026-07-14")
    r3 = p.get_stock_status(["600000", "600001"], "20260714")  # 不同格式同日
    assert len(calls) == 1, f"同一天应只查 1 次，实际 {len(calls)}"
    assert len(r1) == 2 and len(r2) == 1 and len(r3) == 2


def test_different_date_requery(ref_provider):
    """不同 date → 自动重新查询（无需显式失效）。"""
    p = ref_provider
    calls = _spy_queries(p)
    p.get_stock_status(["600000"], "2026-07-14")
    p.get_stock_status(["600000"], "2026-07-15")
    assert len(calls) == 2, f"两天应查 2 次，实际 {len(calls)}"


def test_cache_equals_uncached_baseline(ref_provider):
    """缓存路径 vs 每次重查基准：返回值逐位相等（容差 0）。"""
    p = ref_provider
    codes = ["600000", "600001", "999999"]  # 999999 无数据 → 缺省行
    r_cached = p.get_stock_status(codes, "2026-07-14")

    # 基准：内联复刻旧逻辑（每次重查 query + 构建 dict + 循环）
    source = p._data.query_daily_for_status(
        int(pd.Timestamp("2026-07-14", tz="Asia/Shanghai").value // 10**6))
    source_by_code = (source.set_index('code').to_dict('index')
                      if 'code' in source.columns else {})
    rows = []
    for code in codes:
        row = source_by_code.get(code)
        if row is None:
            rows.append({'code': code, 'is_st': False, 'is_halt': False,
                         'is_delisting_risk': False, 'is_delisted': True,
                         'preClose': None, 'close': None})
            continue
        rows.append({'code': code, 'is_st': bool(row.get('is_st_reliable', False)),
                     'is_st_reliable_source': row.get('is_st_reliable_source', 'none'),
                     'is_halt': bool(row.get('suspendFlag', 0) == 1 or row.get('volume', 0) == 0),
                     'is_delisting_risk': bool(row.get('is_delisting_risk', False)),
                     'is_delisting_risk_source': row.get('is_delisting_risk_source', 'none'),
                     'is_delisted': False, 'preClose': row.get('preClose'),
                     'close': row.get('close')})
    r_baseline = pd.DataFrame(rows)

    pd.testing.assert_frame_equal(r_cached, r_baseline,
                                  check_exact=True, check_dtype=True,
                                  obj="缓存路径与重查基准不一致")
    # 列顺序契约（原实现列序）
    assert list(r_cached.columns) == [
        'code', 'is_st', 'is_st_reliable_source', 'is_halt',
        'is_delisting_risk', 'is_delisting_risk_source', 'is_delisted',
        'preClose', 'close']


def test_repeated_calls_bitwise_stable(ref_provider):
    """缓存命中后多次调用返回值逐位稳定（同一次回测内确定性）。"""
    p = ref_provider
    a = p.get_stock_status(["600000", "600001"], "2026-07-14")
    b = p.get_stock_status(["600000", "600001"], "2026-07-14")
    pd.testing.assert_frame_equal(a, b, check_exact=True, check_dtype=True)


def test_empty_source_fallback(build_db):
    """'code' 列缺失（空表/异常表）→ 全部走缺省行，行为不变且不崩溃。"""
    db = build_db(stock_daily=[])  # 只有空表
    p = DuckDBReferenceDataProvider(db)
    r = p.get_stock_status(["600000"], "2026-07-14")
    assert len(r) == 1
    assert bool(r.iloc[0]['is_delisted']) is True   # np.bool_ == True（原实现同样行为）
    assert bool(r.iloc[0]['is_st']) is False
    assert r.iloc[0]['preClose'] is None
