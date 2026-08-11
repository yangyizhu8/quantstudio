# -*- coding: utf-8 -*-
"""Phase 4A 单元测试：get_bars_by_count 分钟分支批量化 vs 原逐只路径逐位相等。

铁律：性能优化不得改变 get_bars_by_count 的返回值/列/行/排序/dtype/空值/异常行为。
基准 = 测试内内联复刻的旧逐只逻辑（query_minute_bars_by_count 逐 code 循环），
与新版（batch + 分组 tail + 缺失补 raise）对相同输入逐位对比。

覆盖（修订版审核要求的用例清单）：
- 单只 / 多只 code；count=1 / count=3 / count 超当日 bar 数
- fq 三态（pre / post / None）；fields 非空与 None
- bar_cutoff 有 / 无；部分 code 当日无 bar
- 异常语义：指数 code → TABLE_MISSING；全表无数据 → TABLE_EMPTY；
  表有数据但缺 freq → FREQ_NOT_IN_TABLE（两路径异常类型一致）
"""
import pytest
import pandas as pd

from quantstudio.backtest.providers.duckdb_provider import DuckDBMarketDataProvider
from quantstudio.backtest.providers.frequency_labels import (
    FrequencyCapabilityError,
)
from tests.conftest import minute_row, daily_row

DAY = "2026-01-05"
BARS = [(9, 31), (9, 32), (10, 0), (11, 30), (13, 1), (14, 0), (15, 0)]


def _make_rows(code, day=DAY, bars=BARS, front=1.1, back=0.9, freq="1min"):
    rows = []
    for h, m in bars:
        row = minute_row(code, day, h, m, 10.0, freq=freq)
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


def _make_provider(build_db, cal, stock_minutes, stock_daily=None):
    db = build_db(stock_minutes=stock_minutes,
                  stock_daily=stock_daily or [daily_row("600000", DAY, 10.0)])
    return DuckDBMarketDataProvider(db, calendar_provider=cal)


def _iterative_old_path(provider, codes, count, end_date, fq, frequency="1m",
                        bar_cutoff_ms=None, fields=None):
    """内联复刻旧逐只路径（等价性基准）：逐 code query_minute_bars_by_count。"""
    from quantstudio.backtest.providers.frequency_labels import api_to_storage
    from quantstudio.backtest.providers.duckdb_provider import _fields
    storage_freq = api_to_storage(frequency)
    result = {}
    for code in codes:
        df = provider._data.query_minute_bars_by_count(
            code, count, end_date, storage_freq, fq, provider._calendar,
            bar_cutoff_ms=bar_cutoff_ms)
        if not df.empty:
            result[code] = _fields(df, fields)
    return result


def _assert_equal(new_res, old_res, obj=""):
    assert set(new_res.keys()) == set(old_res.keys()), (
        f"{obj} key 集合不一致: new={sorted(new_res)} old={sorted(old_res)}")
    for k in new_res:
        pd.testing.assert_frame_equal(
            new_res[k], old_res[k], check_exact=True, check_dtype=True,
            obj=f"{obj} code={k} 新路径与旧路径 DataFrame 不一致")


def _cutoff(h, m):
    return int(pd.Timestamp(f"{DAY} {h:02d}:{m:02d}:00", tz="Asia/Shanghai").value // 10**6)


CODES = ["600000", "600001", "600002"]


# ========== 核心等价性：新 batch 路径 vs 旧逐只路径 ==========

def test_single_code_count1_fq_pre(build_db, cal):
    sm = []
    for c in CODES:
        sm.extend(_make_rows(c))
    p = _make_provider(build_db, cal, sm)
    new = p.get_bars_by_count(["600000"], 1, DAY, None, "pre", frequency="1m",
                              bar_cutoff_ms=_cutoff(9, 32))
    old = _iterative_old_path(p, ["600000"], 1, DAY, "pre", frequency="1m",
                              bar_cutoff_ms=_cutoff(9, 32))
    _assert_equal(new, old, obj="single_count1_fq_pre")
    # fq='pre' 生效：close = 10.0 × 1.1
    assert float(new["600000"]["close"].iloc[-1]) == pytest.approx(11.0)


def test_multi_code_count3_fq_pre(build_db, cal):
    sm = []
    for c in CODES:
        sm.extend(_make_rows(c))
    p = _make_provider(build_db, cal, sm)
    new = p.get_bars_by_count(CODES, 3, DAY, None, "pre", frequency="1m",
                              bar_cutoff_ms=_cutoff(9, 32))
    old = _iterative_old_path(p, CODES, 3, DAY, "pre", frequency="1m",
                              bar_cutoff_ms=_cutoff(9, 32))
    _assert_equal(new, old, obj="multi_count3_fq_pre")
    assert set(new.keys()) == set(CODES)
    assert all(len(v) == 2 for v in new.values())  # cutoff 前 2 根


def test_fq_post_and_none(build_db, cal):
    sm = []
    for c in CODES:
        sm.extend(_make_rows(c))
    p = _make_provider(build_db, cal, sm)
    for fq in ("post", None):
        new = p.get_bars_by_count(["600000"], 2, DAY, None, fq, frequency="1m",
                                  bar_cutoff_ms=_cutoff(9, 32))
        old = _iterative_old_path(p, ["600000"], 2, DAY, fq, frequency="1m",
                                  bar_cutoff_ms=_cutoff(9, 32))
        _assert_equal(new, old, obj=f"fq={fq}")
        val = float(new["600000"]["close"].iloc[-1])
        if fq == "post":
            assert val == pytest.approx(9.0)
        else:
            assert val == pytest.approx(10.0)


def test_count_exceeds_day_bars(build_db, cal):
    sm = []
    for c in CODES:
        sm.extend(_make_rows(c))
    p = _make_provider(build_db, cal, sm)
    new = p.get_bars_by_count(["600000"], 100, DAY, None, "pre", frequency="1m",
                              bar_cutoff_ms=_cutoff(9, 32))
    old = _iterative_old_path(p, ["600000"], 100, DAY, "pre", frequency="1m",
                              bar_cutoff_ms=_cutoff(9, 32))
    _assert_equal(new, old, obj="count_exceeds_day")
    assert len(new["600000"]) == 2  # cutoff 前只有 2 根


def test_fields_selection_keeps_code_column(build_db, cal):
    """fields 非空时两路径列集合一致且含 code 列（_fields 契约）。"""
    sm = []
    for c in CODES:
        sm.extend(_make_rows(c))
    p = _make_provider(build_db, cal, sm)
    fields = ["open", "close", "volume"]
    new = p.get_bars_by_count(["600000"], 2, DAY, fields, "pre", frequency="1m",
                              bar_cutoff_ms=_cutoff(9, 32))
    old = _iterative_old_path(p, ["600000"], 2, DAY, "pre", frequency="1m",
                              bar_cutoff_ms=_cutoff(9, 32), fields=fields)
    _assert_equal(new, old, obj="fields_selection")
    assert list(new["600000"].columns) == ["code", "open", "close", "volume"]


def test_no_bar_cutoff_full_day(build_db, cal):
    """bar_cutoff_ms=None → 全天窗口（PR3 语义），两路径一致。"""
    sm = []
    for c in CODES:
        sm.extend(_make_rows(c))
    p = _make_provider(build_db, cal, sm)
    new = p.get_bars_by_count(["600000"], 2, DAY, None, "pre", frequency="1m")
    old = _iterative_old_path(p, ["600000"], 2, DAY, "pre", frequency="1m")
    _assert_equal(new, old, obj="no_cutoff")
    assert len(new["600000"]) == 2  # 全天 7 根 tail(2)


def test_partial_codes_no_bars_today(build_db, cal):
    """部分 code 当日无 bar（全表有数据）→ 两路径都跳过该 code，key 集合一致。"""
    sm = _make_rows("600000") + _make_rows("600001")
    # 600002 无当日 bar：给一个 2026-01-04 的 bar（全表有数据但当日窗口空）
    sm.extend(_make_rows("600002", day="2026-01-04"))
    p = _make_provider(build_db, cal, sm)
    new = p.get_bars_by_count(CODES, 2, DAY, None, "pre", frequency="1m",
                              bar_cutoff_ms=_cutoff(9, 32))
    old = _iterative_old_path(p, CODES, 2, DAY, "pre", frequency="1m",
                              bar_cutoff_ms=_cutoff(9, 32))
    _assert_equal(new, old, obj="partial_no_bars")
    assert set(new.keys()) == {"600000", "600001"}


def test_empty_codes(build_db, cal):
    sm = _make_rows("600000")
    p = _make_provider(build_db, cal, sm)
    assert p.get_bars_by_count([], 2, DAY, None, "pre", frequency="1m") == {}


# ========== 异常语义：与逐只版一致（铁律）==========

def test_index_code_raises_table_missing(build_db, cal):
    sm = _make_rows("600000")
    p = _make_provider(build_db, cal, sm)
    with pytest.raises(FrequencyCapabilityError) as ei:
        _iterative_old_path(p, ["399001"], 2, DAY, "pre", frequency="1m")
    with pytest.raises(FrequencyCapabilityError) as en:
        p.get_bars_by_count(["399001"], 2, DAY, None, "pre", frequency="1m")
    assert "TABLE_MISSING" in str(ei.value)
    assert "TABLE_MISSING" in str(en.value)


def test_code_no_data_raises_table_empty(build_db, cal):
    sm = _make_rows("600000")
    p = _make_provider(build_db, cal, sm)
    with pytest.raises(FrequencyCapabilityError) as ei:
        _iterative_old_path(p, ["600999"], 2, DAY, "pre", frequency="1m")
    with pytest.raises(FrequencyCapabilityError) as en:
        p.get_bars_by_count(["600999"], 2, DAY, None, "pre", frequency="1m")
    assert "TABLE_EMPTY" in str(ei.value)
    assert "TABLE_EMPTY" in str(en.value)


def test_missing_freq_raises_freq_not_in_table(build_db, cal):
    """表有数据但缺 freq：逐只版 raise FREQ_NOT_IN_TABLE（带 available_freqs），
    新版必须同样 raise（batch 内部静默跳过是它的实现自由，对外语义保持）。"""
    sm = _make_rows("600000", freq="1d")  # 只有 1d 频率数据
    p = _make_provider(build_db, cal, sm)
    with pytest.raises(FrequencyCapabilityError) as ei:
        _iterative_old_path(p, ["600000"], 2, DAY, "pre", frequency="1m")
    with pytest.raises(FrequencyCapabilityError) as en:
        p.get_bars_by_count(["600000"], 2, DAY, None, "pre", frequency="1m")
    assert "FREQ_NOT_IN_TABLE" in str(ei.value)
    assert "FREQ_NOT_IN_TABLE" in str(en.value)
    assert "available_freqs" in str(en.value)


def test_mixed_missing_code_preserves_raise(build_db, cal):
    """混合：正常 code + 全表无数据 code → 两路径都 raise TABLE_EMPTY（先查的正常
    code 不外漏，异常类型与消息一致）。"""
    sm = _make_rows("600000")
    p = _make_provider(build_db, cal, sm)
    with pytest.raises(FrequencyCapabilityError) as ei:
        _iterative_old_path(p, ["600000", "600999"], 2, DAY, "pre", frequency="1m")
    with pytest.raises(FrequencyCapabilityError) as en:
        p.get_bars_by_count(["600000", "600999"], 2, DAY, None, "pre", frequency="1m")
    assert "TABLE_EMPTY" in str(ei.value)
    assert "TABLE_EMPTY" in str(en.value)
