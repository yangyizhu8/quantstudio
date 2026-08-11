# -*- coding: utf-8 -*-
"""Phase 4B 单元测试：当日分钟历史内存切片 vs SQL 路径逐位相等。

铁律：性能优化不得改变 get_history 返回值/列/行/排序/dtype/空值/异常行为。
本测试用真实 DuckDB（conftest build_db）+ 真实 DuckDBMarketDataProvider +
真实 PtradeAPI，构造"带当日缓存注入"与"不带缓存"两个实例，对相同输入
逐位对比（pd.testing.assert_frame_equal check_exact=True）。

覆盖（修订版审核要求的用例清单）：
- 单只 / 多只 code；count=1 / count=3 / count 超当日 bar 数
- fq 三态（pre / post / None）——front/back 列与原始列不同的数据
- bar_cutoff 有 / 无（无 cutoff 必须 fallback SQL，防未来泄漏）
- 日期不匹配（include=False 锚定前一日）→ fallback SQL
- 请求 code 不在缓存 → debug 日志 + 空结果（与 SQL 同窗口空语义）
- fields=None 列集合一致（内存切片保留全部列含 code 列）
"""
import logging

import pandas as pd
import pytest

from quantstudio.backtest.ptrade_api import PtradeAPI, CodeDict
from tests.conftest import minute_row, daily_row, make_providers

DAY = "2026-01-05"
PREV_DAY = "2026-01-02"
BARS = [(9, 31), (9, 32), (10, 0), (11, 30), (13, 1), (14, 0), (15, 0)]


class _FakeEngine:
    engine_profile = "minute-bar-v1"
    config = None


def _rows_with_front(code, day, bars, front=1.1, back=0.9):
    """分钟行，front/back 列与原始 OHLC 不同（验证 fq 替换真的发生）。"""
    rows = []
    for h, m in bars:
        row = minute_row(code, day, h, m, 10.0)
        row["open_front"] = row["open"] * front
        row["high_front"] = row["high"] * front
        row["low_front"] = row["low"] * front
        row["close_front"] = row["close"] * front
        row["open_back"] = row["open"] * back
        row["high_back"] = row["high"] * back
        row["low_back"] = row["low"] * back
        row["close_back"] = row["close"] * back
        rows.append(row)
    return rows


def _make_api(db_path, cal, with_cache, codes, current_bar_ts=None):
    """构造独立 PtradeAPI 实例（内存路径 / SQL 路径各一个，避免 _query_cache 串扰）。

    current_bar_ts 默认 09:32 且为 tz-aware（与引擎 _load_minute_snapshots 注入的
    bar_ts 一致——naive 字符串会让 bar_cutoff_ms 按 UTC 解释导致截断失效）。
    """
    if current_bar_ts is None:
        current_bar_ts = pd.Timestamp(f"{DAY} 09:32:00", tz="Asia/Shanghai")
    providers = make_providers(db_path, cal)
    api = PtradeAPI(market=providers.market, calendar=cal)
    api.attach_bar(_FakeEngine(), None, DAY, PREV_DAY, {},
                   current_bar_ts=current_bar_ts)
    if with_cache:
        all_bars = providers.market._data.query_minute_bars_by_range_batch(
            codes, DAY, DAY, "1min", None, cal)
        api.attach_day_minute_history(all_bars, DAY)
    return api, providers


def _assert_code_dict_equal(sql_res, mem_res, obj=""):
    assert set(sql_res.keys()) == set(mem_res.keys()), (
        f"{obj} key 集合不一致: sql={sorted(sql_res)} mem={sorted(mem_res)}")
    for k in sql_res:
        pd.testing.assert_frame_equal(
            sql_res[k], mem_res[k], check_exact=True, check_dtype=True,
            obj=f"{obj} code={k} 两路径 DataFrame 不一致")


def _get_history(api, count, freq, fq, codes, fields=None, include=True, is_dict=True):
    return api.get_history(count, frequency=freq, field=fields,
                           security_list=codes, fq=fq, include=include,
                           is_dict=is_dict)


@pytest.fixture
def mini_db(build_db, cal):
    """3 只股票 × 7 根分钟 bar + 当日日线行。front=1.1x, back=0.9x。

    build_db 只建 4 张核心表；PR7 日线 batch 在"股票窗口无数据"时会继续
    fallback 检查 etf_daily/index_daily（见 _ensure_bars_in_cache），因此
    补建 index_daily 空表，避免 CatalogException 被 get_history 吞成空结果。
    """
    sm_rows, sd_rows = [], []
    for code in ("600000", "600001", "600002"):
        sm_rows.extend(_rows_with_front(code, DAY, BARS))
        sd_rows.append(daily_row(code, DAY, 10.0))
    db = build_db(stock_minutes=sm_rows, stock_daily=sd_rows)
    from quantstudio.pipeline.writers import DDL_DUCKDB
    import duckdb
    con = duckdb.connect(str(db))
    con.execute(DDL_DUCKDB["index_daily"])
    con.close()
    return db


CODES = ["600000", "600001", "600002"]


# ========== 核心等价性：内存切片 vs SQL 路径逐位相等 ==========

def test_single_code_count1_fq_pre(mini_db, cal):
    """单只、count=1、fq='pre'（smallcap 真实调用形态）。"""
    db = mini_db
    api_sql, _ = _make_api(db, cal, with_cache=False, codes=CODES)
    api_mem, _ = _make_api(db, cal, with_cache=True, codes=CODES)
    r_sql = _get_history(api_sql, 1, "1m", "pre", ["600000.SH"],
                         fields=["open", "close", "volume"])
    r_mem = _get_history(api_mem, 1, "1m", "pre", ["600000.SH"],
                         fields=["open", "close", "volume"])
    _assert_code_dict_equal(r_sql, r_mem, obj="single_count1_fq_pre")
    # fq='pre' 生效：close 应为 front 值 11.0（原始 10.0 × 1.1）
    assert float(r_mem["600000.SH"]["close"].iloc[-1]) == pytest.approx(11.0)


def test_multi_code_count3_fq_pre(mini_db, cal):
    """多只、count=3、fq='pre'。"""
    db = mini_db
    api_sql, _ = _make_api(db, cal, with_cache=False, codes=CODES)
    api_mem, _ = _make_api(db, cal, with_cache=True, codes=CODES)
    r_sql = _get_history(api_sql, 3, "1m", "pre", [f"{c}.SH" for c in CODES],
                         fields=["open", "high", "low", "close", "volume"])
    r_mem = _get_history(api_mem, 3, "1m", "pre", [f"{c}.SH" for c in CODES],
                         fields=["open", "high", "low", "close", "volume"])
    _assert_code_dict_equal(r_sql, r_mem, obj="multi_count3_fq_pre")
    # 每只 3 根（09:32 及之前的最近 3 根：09:31/09:32 + 更早？cutoff=09:32 只有 2 根）
    # cutoff 09:32 含 09:31、09:32 两根 → count=3 时返回 2 根（数据不足取全部）
    assert len(r_mem["600000.SH"]) == 2


def test_fq_post_and_none(mini_db, cal):
    """fq='post' 与 fq=None（缓存为原始值 → 切片时按请求 fq 替换）。"""
    db = mini_db
    for fq in ("post", None):
        api_sql, _ = _make_api(db, cal, with_cache=False, codes=CODES)
        api_mem, _ = _make_api(db, cal, with_cache=True, codes=CODES)
        r_sql = _get_history(api_sql, 2, "1m", fq, ["600000.SH"],
                             fields=["close"])
        r_mem = _get_history(api_mem, 2, "1m", fq, ["600000.SH"],
                             fields=["close"])
        _assert_code_dict_equal(r_sql, r_mem, obj=f"fq={fq}")
        val = float(r_mem["600000.SH"]["close"].iloc[-1])
        if fq == "post":
            assert val == pytest.approx(9.0)   # 原始 10.0 × 0.9
        else:
            assert val == pytest.approx(10.0)  # 不复权


def test_count_exceeds_day_bars(mini_db, cal):
    """count 超当日 bar 数（100 > 7）→ 返回当日全部 bar，两路径一致。"""
    db = mini_db
    api_sql, _ = _make_api(db, cal, with_cache=False, codes=CODES)
    api_mem, _ = _make_api(db, cal, with_cache=True, codes=CODES)
    r_sql = _get_history(api_sql, 100, "1m", "pre", ["600000.SH"],
                         fields=["close"])
    r_mem = _get_history(api_mem, 100, "1m", "pre", ["600000.SH"],
                         fields=["close"])
    _assert_code_dict_equal(r_sql, r_mem, obj="count_exceeds_day")
    assert len(r_mem["600000.SH"]) == 2  # cutoff 09:32 前只有 2 根


def test_fields_none_columns_equal(mini_db, cal):
    """fields=None：内存切片保留全部列（含 code 列），与 SQL 路径列集合一致。"""
    db = mini_db
    api_sql, _ = _make_api(db, cal, with_cache=False, codes=CODES)
    api_mem, _ = _make_api(db, cal, with_cache=True, codes=CODES)
    r_sql = _get_history(api_sql, 2, "1m", "pre", ["600000.SH"], fields=None)
    r_mem = _get_history(api_mem, 2, "1m", "pre", ["600000.SH"], fields=None)
    _assert_code_dict_equal(r_sql, r_mem, obj="fields_none")
    assert "code" in r_mem["600000.SH"].columns


def test_pit_no_future_bar_leak(mini_db, cal):
    """PIT：cutoff 09:32 时结果不含 09:33 之后的 bar（防未来泄漏核心）。"""
    db = mini_db
    api_mem, _ = _make_api(db, cal, with_cache=True, codes=CODES)
    r = _get_history(api_mem, 100, "1m", "pre", ["600000.SH"], fields=["time"])
    times = pd.to_datetime(r["600000.SH"]["time"], unit="ms", utc=True)
    local = times.dt.tz_convert("Asia/Shanghai").dt.strftime("%H:%M").tolist()
    assert local == ["09:31", "09:32"], f"应只含 <= 09:32 的 bar，got {local}"


# ========== fallback 场景：内存不命中 → SQL 路径（行为与无缓存一致）==========

def test_fallback_include_false_prev_date(mini_db, cal):
    """include=False 锚定前一日 → 缓存日期不匹配 → fallback SQL，结果一致。"""
    db = mini_db
    api_sql, _ = _make_api(db, cal, with_cache=False, codes=CODES)
    api_mem, _ = _make_api(db, cal, with_cache=True, codes=CODES)
    r_sql = _get_history(api_sql, 2, "1m", "pre", ["600000.SH"],
                         fields=["close"], include=False)
    r_mem = _get_history(api_mem, 2, "1m", "pre", ["600000.SH"],
                         fields=["close"], include=False)
    _assert_code_dict_equal(r_sql, r_mem, obj="fallback_include_false")


def test_fallback_no_bar_cutoff(mini_db, cal):
    """无 bar_cutoff（日线 Profile / attach_bar 前）→ 不触发内存切片（防未来泄漏）。"""
    db = mini_db
    # attach_bar 不传 current_bar_ts → _current_bar_ts=None → bar_cutoff_ms=None
    providers = make_providers(db, cal)
    api = PtradeAPI(market=providers.market, calendar=cal)
    api.attach_bar(_FakeEngine(), None, DAY, PREV_DAY, {})
    all_bars = providers.market._data.query_minute_bars_by_range_batch(
        CODES, DAY, DAY, "1min", None, cal)
    api.attach_day_minute_history(all_bars, DAY)
    r = _get_history(api, 2, "1m", "pre", ["600000.SH"], fields=["close"])
    # SQL 路径（bar_cutoff=None → 全天窗口 7 根）→ count=2 → tail(2) → 2 根
    assert len(r["600000.SH"]) == 2


def test_missing_code_sql_backfill_preserves_raise(mini_db, cal):
    """请求 code 不在当日缓存 → 补查 SQL：异常语义与无缓存路径完全一致。

    600999 在 stock_minutes 全表无数据：无缓存路径 raise TABLE_EMPTY，
    内存路径（缺失补查）必须同样 raise（铁律：不改变异常行为）。
    """
    from quantstudio.backtest.providers.frequency_labels import FrequencyCapabilityError

    db = mini_db
    api_sql, _ = _make_api(db, cal, with_cache=False, codes=CODES)
    api_mem, _ = _make_api(db, cal, with_cache=True, codes=CODES)
    # 混合：600000 在缓存、600999 全表无数据 → 两路径都必须 raise TABLE_EMPTY
    with pytest.raises(FrequencyCapabilityError):
        _get_history(api_sql, 1, "1m", "pre", ["600000.SH", "600999.SH"],
                     fields=["close"])
    with pytest.raises(FrequencyCapabilityError):
        _get_history(api_mem, 1, "1m", "pre", ["600000.SH", "600999.SH"],
                     fields=["close"])


def test_missing_code_sql_backfill_debug_logged(mini_db, cal, caplog):
    """缺失 code 补查时打 debug 日志（修订要求：code ∉ 缓存场景可观察）。"""
    db = mini_db
    api_mem, _ = _make_api(db, cal, with_cache=True, codes=CODES)
    with caplog.at_level(logging.DEBUG, logger="quantstudio.backtest.ptrade_api"):
        # 600002 不在缓存（缓存只含 600000/600001）→ 补查 SQL → 有数据 → 结果完整
        all_bars = api_mem._day_minute_history
        api_mem._day_minute_history = all_bars[all_bars['code'] != '600002']
        r = _get_history(api_mem, 1, "1m", "pre", ["600000.SH", "600002.SH"],
                         fields=["close"])
    assert set(r.keys()) == {"600000.SS", "600002.SS"}
    assert any("[4B]" in rec.message for rec in caplog.records)


def test_daily_frequency_untouched(mini_db, cal):
    """日线路径不受 4B 影响：frequency='1d' 不触发内存切片，走原日线 SQL。"""
    db = mini_db
    api_mem, _ = _make_api(db, cal, with_cache=True, codes=CODES)
    r = _get_history(api_mem, 2, "1d", "pre", ["600000.SH"],
                     fields=["close"], include=False)
    assert isinstance(r, CodeDict)
    # 日线窗口 2026-01-02 无数据（daily 行在 2026-01-05）→ 空结果（日线路径不受 4B 影响）
    assert len(r) == 0
