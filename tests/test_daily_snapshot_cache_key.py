# -*- coding: utf-8 -*-
"""日线快照缓存键 契约测试（2026-09-18）。

修复背景（P-D14 D3 回归）：
    `preload_daily_snapshots` 的缓存键曾用 UTC 日界截断
    （`time // 86_400_000 * 86_400_000`，结果 mod 恒为 0），而
    `query_daily_snapshot` 的查询键来自 `_start_ms`（CST 日界，mod 恒为 57_600_000）
    → 两键空间交集为空，预取结果 100% 未被消费，每个交易日退化为一次
    全市场窗口扫描。修复后两者共用 `time_axis.day_start_ms`，键自然重合。

防回归断言（T-1~T-5）：
    T-1 日界同源恒等：查询键是 day_start_ms 的不动点；日内值归并到当日日界；
        `day_start_ms(x)==x ⟺ x % 86400000 == 57600000` 两表述等价。
    T-2 预取后**真命中**（断网取证：连接不可得而查询仍返回数据）。
        —— P-D14 T6 只比较行数与 code 集合、未断言命中，本项堵该验收漏洞。
    T-3 缓存命中路径 与 DB 兜底路径 按 code 对齐后逐值/列集/列序/dtype 全等。
    T-4 08:00 异常组并入当日零点键（P-D14 原始诉求不回归）。
    T-5 防御分支自身覆盖（非日界 date_ms 不落缓存；日界正常落）——防死代码。
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from quantstudio.backtest.providers.duckdb_data_access import (  # noqa: E402
    DuckDBDataAccess, day_start_ms, is_day_start_ms)
from quantstudio.backtest.providers.duckdb_provider import (  # noqa: E402
    _start_ms, _end_ms)
from quantstudio.backtest.providers.time_axis import (  # noqa: E402
    CST_OFFSET_MS, DAY_MS)

DB = str(pathlib.Path(__file__).resolve().parents[1] / "data" / "quantstudio.db")

# 跨年 / 闰年 2 月 / 月末 / P-D14 双 time 日
_SAMPLES = ["2024-02-29", "2025-12-31", "2026-01-01",
            "2026-01-31", "2026-02-28", "2026-03-12", "2026-07-01"]


def _has_db():
    return pathlib.Path(DB).exists()


def _mock_conn(df_in):
    """假连接：任何 SQL 都返回给定 DataFrame（不触库）。"""
    class _R:
        def fetchdf(self):
            return df_in.copy()

    class _C:
        def execute(self, sql):
            return _R()

    return lambda self: _C()


def _date_of(ms):
    return pd.Timestamp(int(ms), unit='ms', tz='Asia/Shanghai').strftime('%Y-%m-%d')


# ========== T-1 日界同源恒等 ==========

@pytest.mark.parametrize("d", _SAMPLES)
def test_t1_query_key_is_fixed_point(d):
    ms = _start_ms(d)
    assert day_start_ms(ms) == ms, f"{d} 查询键非 day_start_ms 不动点"
    assert is_day_start_ms(ms)


@pytest.mark.parametrize("d", _SAMPLES)
def test_t1_intraday_normalizes_to_day_start(d):
    ms = _start_ms(d)
    for h in (0, 8, 12, 16, 23):
        assert day_start_ms(ms + h * 3_600_000) == ms, f"{d} +{h}h 未归并到当日"
    assert day_start_ms(ms + DAY_MS - 1) == ms, f"{d} 当日最后一毫秒未归并"


def test_t1_mod_predicate_equivalence():
    """`day_start_ms(x)==x` 与 `x % 86400000 == 57600000` 两表述语义等价。"""
    ms = _start_ms("2026-03-12")
    for delta in (-1, 0, 1, -CST_OFFSET_MS, 8 * 3_600_000,
                  DAY_MS - 1, -DAY_MS + 1):
        x = ms + delta
        assert (day_start_ms(x) == x) == (x % DAY_MS == DAY_MS - CST_OFFSET_MS), \
            f"两表述在 x={x} 处不等价"


# ========== T-2 预取后"真命中"（断网取证） ==========

@pytest.mark.skipif(not _has_db(), reason="DB 不存在")
def test_t2_preloaded_days_are_true_cache_hits():
    dao = DuckDBDataAccess(db_path=DB)
    ms1, ms2 = _start_ms("2026-03-02"), _end_ms("2026-03-13")
    dao.preload_daily_snapshots(ms1, ms2)
    keys = sorted(dao._daily_snapshot_cache)
    if not keys:
        pytest.skip("生产库不可读（被锁/缺失），本项需真实库")

    for k in keys:
        assert day_start_ms(k) == k, f"缓存键非 CST 日界: {k}"
        assert _start_ms(_date_of(k)) == k, f"缓存键与查询键不同源: {k}"

    # 断网取证：连接不可得 → 只可能走内存；未命中会返回空 DataFrame
    dao._get_conn = lambda: None
    for k in keys:
        df = dao.query_daily_snapshot(k)
        assert not df.empty, f"预取后 {k} 未命中缓存（仍退化为 DB 兜底）"


# ========== T-3 缓存命中 vs DB 兜底 等价 ==========

def test_t3_cache_hit_equals_db_fallback(monkeypatch):
    """两路径按 code 对齐后逐值全等，列集/列序/dtype 亦全等。

    物理行序不作为契约：两路径均经 `sort_values('time')`（不稳定排序）整理，
    且引擎经 `_df_index`（缓存键 `entry_df is df`）按 code→position 映射访问，
    不依赖行序。
    """
    ms = _start_ms("2026-03-12")
    df_in = pd.DataFrame(
        [("600000", ms, 1.0), ("600000", ms + 8 * 3_600_000, 2.0),
         ("000001", ms, 3.0), ("300750", ms, 4.0)],
        columns=["code", "time", "close"])
    monkeypatch.setattr(DuckDBDataAccess, "_get_conn", _mock_conn(df_in))

    fallback = DuckDBDataAccess(db_path=DB).query_daily_snapshot(ms)

    warm = DuckDBDataAccess(db_path=DB)
    warm.preload_daily_snapshots(ms, ms + DAY_MS - 1)
    hit = warm.query_daily_snapshot(ms)

    assert list(fallback.columns) == list(hit.columns)
    assert bool((fallback.dtypes == hit.dtypes).all())
    assert len(fallback) == len(hit)
    assert set(fallback["code"]) == set(hit["code"])
    pd.testing.assert_frame_equal(
        fallback.sort_values("code").reset_index(drop=True),
        hit.sort_values("code").reset_index(drop=True))


# ========== T-4 08:00 异常组并入当日键 ==========

def test_t4_intraday_group_merged_into_day_key(monkeypatch):
    ms = _start_ms("2026-07-01")
    df_in = pd.DataFrame(
        [("515050", ms, 1.330), ("515050", ms + 8 * 3_600_000, 1.334),
         ("511260", ms, 134.0)],
        columns=["code", "time", "close"])
    monkeypatch.setattr(DuckDBDataAccess, "_get_conn", _mock_conn(df_in))

    dao = DuckDBDataAccess(db_path=DB)
    dao.preload_daily_snapshots(ms, ms + DAY_MS - 1)
    assert ms in dao._daily_snapshot_cache, "08:00 组应并入当日零点键"
    assert all(day_start_ms(k) == k for k in dao._daily_snapshot_cache)

    dao._get_conn = lambda: None
    hit = dao.query_daily_snapshot(ms)
    row = hit[hit["code"] == "515050"]
    assert len(row) == 1, "同日同 code 应去重为 1 行"
    assert abs(float(row.iloc[0]["close"]) - 1.334) < 1e-9, "去重应取最大 time 行"


# ========== T-5 防御分支自身覆盖 ==========

def test_t5_defense_skips_non_day_start_key(monkeypatch):
    """非日界 date_ms：返回值仍正确（走 DB 窗口），且不写入缓存。"""
    ms = _start_ms("2026-03-12")
    off = ms + 12 * 3_600_000
    assert not is_day_start_ms(off)

    df_in = pd.DataFrame([("600000", off, 1.0)],
                         columns=["code", "time", "close"])
    monkeypatch.setattr(DuckDBDataAccess, "_get_conn", _mock_conn(df_in))

    dao = DuckDBDataAccess(db_path=DB)
    out = dao.query_daily_snapshot(off)
    assert not out.empty, "防御不得影响返回值"
    assert off not in dao._daily_snapshot_cache, "非日界键不得落缓存"


def test_t5_defense_keeps_day_start_key(monkeypatch):
    """日界 date_ms：正常写入缓存（防御不误伤正常路径）。"""
    ms = _start_ms("2026-03-12")
    assert is_day_start_ms(ms)

    df_in = pd.DataFrame([("600000", ms, 1.0)],
                         columns=["code", "time", "close"])
    monkeypatch.setattr(DuckDBDataAccess, "_get_conn", _mock_conn(df_in))

    dao = DuckDBDataAccess(db_path=DB)
    dao.query_daily_snapshot(ms)
    assert ms in dao._daily_snapshot_cache, "日界键应正常落缓存"


# ========== T-6 空 / 无数据日边界 ==========

def test_t6_empty_day_returns_empty_frame(monkeypatch):
    """无数据日：返回空帧；不触碰预取区间变量 `_cached_min_ms` / `_cached_max_ms`。

    既有语义说明：`query_daily_snapshot` **无条件**写入缓存（含空帧），用于避免无数据日
    的重复查询；本件不改该行为，故断言与既有语义一致——要求「空帧不落缓存」会构成
    行为变更（性能行为），超出本件「纯性能优化·语义等价」的边界。
    """
    ms = _start_ms("2026-03-12")
    empty = pd.DataFrame(columns=["code", "time", "close"])
    monkeypatch.setattr(DuckDBDataAccess, "_get_conn", _mock_conn(empty))

    dao = DuckDBDataAccess(db_path=DB)
    before = (dao._cached_min_ms, dao._cached_max_ms)
    out = dao.query_daily_snapshot(ms)

    assert out.empty, "无数据日应返回空帧"
    assert (dao._cached_min_ms, dao._cached_max_ms) == before, \
        "单日查询不得触碰预取区间变量"
    assert ms in dao._daily_snapshot_cache, \
        "既有语义：空帧落缓存（本件不改，与 DB 兜底路径一致）"


def test_t6_empty_preload_range_keeps_range_vars_none(monkeypatch):
    """预取无数据区间：不新增缓存键，`_cached_min_ms` / `_cached_max_ms` 保持 None。"""
    ms1, ms2 = _start_ms("2026-03-02"), _end_ms("2026-03-13")
    empty = pd.DataFrame(columns=["code", "time", "close"])
    monkeypatch.setattr(DuckDBDataAccess, "_get_conn", _mock_conn(empty))

    dao = DuckDBDataAccess(db_path=DB)
    dao.preload_daily_snapshots(ms1, ms2)

    assert dao._daily_snapshot_cache == {}, "空区间不得新增缓存键"
    assert dao._cached_min_ms is None and dao._cached_max_ms is None


def test_t6_empty_day_matches_db_fallback(monkeypatch):
    """空数据日在两条路径下行为一致（等价性验证，非新语义）。"""
    ms = _start_ms("2026-03-12")
    empty = pd.DataFrame(columns=["code", "time", "close"])
    monkeypatch.setattr(DuckDBDataAccess, "_get_conn", _mock_conn(empty))

    cold = DuckDBDataAccess(db_path=DB)
    fallback = cold.query_daily_snapshot(ms)

    warm = DuckDBDataAccess(db_path=DB)
    warm.preload_daily_snapshots(ms, ms + DAY_MS - 1)
    hit = warm.query_daily_snapshot(ms)

    assert fallback.empty and hit.empty
    assert list(fallback.columns) == list(hit.columns)
