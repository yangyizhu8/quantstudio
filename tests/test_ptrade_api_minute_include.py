"""分钟 include 语义引擎测试（2026-08-13 修复）。

背景：本地引擎分钟 include=False 原锚定前一交易日（_prev_date），9:35 时看到的是
昨天的分钟 bar。修复后锚定当天 + 排除当前 bar（前一 bar 及之前，PIT 正确语义），
与 PTrade 分钟 include 语义一致（2026-08-13 PTrade 实测：include=True 含当前 bar、
include=False 到前一 bar）。

覆盖：
- SQL 路径（fake market 观察 anchor_date / bar_cutoff_ms 传参）
- Phase 4B 内存切片路径（attach_day_minute_history 注入后返回的 bar 集合）
- 日线 include 不变（False=prev_date, True=current_date）
- 边界：09:31 首根 bar、13:01 午休后首根、5m/15m/30m/60m 频率回退
"""
from __future__ import annotations

import pandas as pd
import pytest

from quantstudio.backtest.ptrade_api import PtradeAPI
from tests.conftest import minute_row

DAY = "2026-01-05"
PREV_DAY = "2025-12-31"


def _ms(day, hh, mm):
    ts = pd.Timestamp(f"{day} {hh:02d}:{mm:02d}:00").tz_localize("Asia/Shanghai")
    return int(ts.value // 10 ** 6)


def _bar_ts(day, hh, mm):
    return pd.Timestamp(f"{day} {hh:02d}:{mm:02d}:00", tz="Asia/Shanghai")


class _FakeMarket:
    """观察 get_bars_by_count 收到的锚定参数（SQL 路径）。"""

    def __init__(self):
        self.calls = []

    def preload(self, *args, **kwargs):
        pass

    def get_bars_by_count(self, codes, count, end_date, fields=None, fq="pre",
                          frequency="1d", bar_cutoff_ms=None):
        self.calls.append({
            "codes": list(codes), "count": count, "end_date": end_date,
            "frequency": frequency, "bar_cutoff_ms": bar_cutoff_ms,
        })
        return {}


def _make_api(bar_hh, bar_mm, market=None, day=DAY, prev_day=PREV_DAY):
    api = PtradeAPI(market=market)
    api.attach_bar(None, pd.DataFrame(), day, prev_day, {}, {},
                   current_bar_ts=_bar_ts(day, bar_hh, bar_mm))
    return api


def _make_api_with_market(bar_hh, bar_mm, day=DAY, prev_day=PREV_DAY):
    """get_history 在 self._market is None 时提前返回空（line ~1167），
    内存切片测试也必须传入非 None market（全命中时不会触发调用）。"""
    return _make_api(bar_hh, bar_mm, market=_FakeMarket(), day=day, prev_day=prev_day)


def _minute_df(day, bars):
    rows = [minute_row("600000", day, hh, mm, close)
            for hh, mm, close in bars]
    return pd.DataFrame(rows)


def _times(hist, code="600000.SS"):
    frame = hist[code]
    return [pd.Timestamp(t, unit="ms", tz="Asia/Shanghai").strftime("%H:%M")
            for t in frame["time"]]


def _hist(api, count=5, unit="1m", include=False, market_ok=True):
    return api.get_history(count, frequency=unit, field=["close"],
                           security_list="600000.SS", fq="pre",
                           include=include, is_dict=True)


# ---------------------------------------------------------------------------
# SQL 路径：锚定参数传参断言（fake market）
# ---------------------------------------------------------------------------

def test_sql_minute_include_false_anchors_current_day_previous_bar():
    """分钟 include=False：anchor_date=当天，bar_cutoff_ms=前一 bar（09:34）。"""
    market = _FakeMarket()
    api = _make_api(9, 35, market=market)
    _hist(api, include=False)
    assert len(market.calls) == 1
    call = market.calls[0]
    assert call["end_date"] == DAY          # 锚定当天（原 bug：prev_date）
    assert call["frequency"] == "1m"
    assert call["bar_cutoff_ms"] == _ms(DAY, 9, 34)   # 前一 bar（排除当前 09:35）


def test_sql_minute_include_true_unchanged():
    """分钟 include=True：cutoff=当前 bar（09:35），行为不变。"""
    market = _FakeMarket()
    api = _make_api(9, 35, market=market)
    _hist(api, include=True)
    assert len(market.calls) == 1
    call = market.calls[0]
    assert call["end_date"] == DAY
    assert call["bar_cutoff_ms"] == _ms(DAY, 9, 35)


@pytest.mark.parametrize("unit,expected_cutoff", [
    ("1m", _ms(DAY, 9, 34)),
    ("5m", _ms(DAY, 9, 30)),   # 09:35 - 5min
    ("15m", _ms(DAY, 9, 20)),  # 09:35 - 15min
    ("30m", _ms(DAY, 9, 5)),   # 09:35 - 30min
    ("60m", _ms(DAY, 8, 35)),  # 09:35 - 60min
])
def test_sql_minute_include_false_different_freqs(unit, expected_cutoff):
    """include=False 按频率回退一根 bar 的间隔。"""
    market = _FakeMarket()
    api = _make_api(9, 35, market=market)
    _hist(api, unit=unit, include=False)
    assert len(market.calls) == 1
    assert market.calls[0]["bar_cutoff_ms"] == expected_cutoff


def test_sql_minute_0931_first_bar_empty_window():
    """09:31 首根 bar + include=False → cutoff=09:30（当天无已完成的前一 bar）。"""
    market = _FakeMarket()
    api = _make_api(9, 31, market=market)
    _hist(api, include=False)
    assert len(market.calls) == 1
    assert market.calls[0]["bar_cutoff_ms"] == _ms(DAY, 9, 30)


def test_sql_minute_1301_lunch_break_cutoff():
    """13:01 午休后首根 + include=False → cutoff=13:00（含 11:30 及之前）。"""
    market = _FakeMarket()
    api = _make_api(13, 1, market=market)
    _hist(api, include=False)
    assert len(market.calls) == 1
    assert market.calls[0]["bar_cutoff_ms"] == _ms(DAY, 13, 0)


def test_sql_daily_include_false_unchanged():
    """日线 include=False：anchor_date=_prev_date（前一交易日），cutoff=None（不变）。"""
    market = _FakeMarket()
    api = _make_api(15, 0, market=market)
    _hist(api, unit="1d", include=False)
    assert len(market.calls) == 1
    call = market.calls[0]
    assert call["end_date"] == PREV_DAY
    assert call["bar_cutoff_ms"] is None


def test_sql_daily_include_true_unchanged():
    """日线 include=True：anchor_date=_current_date（当天），cutoff=None（不变）。"""
    market = _FakeMarket()
    api = _make_api(15, 0, market=market)
    _hist(api, unit="1d", include=True)
    assert len(market.calls) == 1
    call = market.calls[0]
    assert call["end_date"] == DAY
    assert call["bar_cutoff_ms"] is None


# ---------------------------------------------------------------------------
# Phase 4B 内存切片路径：返回 bar 集合断言（attach_day_minute_history）
# ---------------------------------------------------------------------------

def test_mem_minute_include_false_excludes_current_bar():
    """内存切片：include=False 返回 09:31~09:34，不含当前 09:35 bar。"""
    api = _make_api_with_market(9, 35)
    api.attach_day_minute_history(
        _minute_df(DAY, [(9, 31, 10.0), (9, 32, 10.1), (9, 33, 10.2),
                         (9, 34, 10.3), (9, 35, 10.4)]), DAY)
    hist = _hist(api, include=False)
    assert _times(hist) == ["09:31", "09:32", "09:33", "09:34"]


def test_mem_minute_include_true_includes_current_bar():
    """内存切片：include=True 含当前 09:35 bar（行为不变）。"""
    api = _make_api_with_market(9, 35)
    api.attach_day_minute_history(
        _minute_df(DAY, [(9, 31, 10.0), (9, 32, 10.1), (9, 33, 10.2),
                         (9, 34, 10.3), (9, 35, 10.4)]), DAY)
    hist = _hist(api, include=True)
    assert _times(hist) == ["09:31", "09:32", "09:33", "09:34", "09:35"]


def test_mem_minute_include_false_5m_freq():
    """内存切片 5m：include=False 排除当前 5m bar（09:40），仅含 09:35。"""
    api = _make_api_with_market(9, 40)
    df = _minute_df(DAY, [(9, 35, 10.0), (9, 40, 10.5)])
    df["freq"] = "5min"
    api.attach_day_minute_history(df, DAY)
    hist = _hist(api, unit="5m", include=False)
    assert _times(hist) == ["09:35"]


def test_mem_minute_0931_include_false_empty():
    """内存切片 09:31 首根 + include=False → 无 bar（SQL 补查返回空）。"""
    market = _FakeMarket()
    api = _make_api(9, 31, market=market)
    api.attach_day_minute_history(_minute_df(DAY, [(9, 31, 10.0)]), DAY)
    hist = _hist(api, include=False)
    assert len(hist) == 0
    # 未命中缓存（cutoff=09:30 无 bar）→ 补查 SQL（fake market 返回空）
    assert len(market.calls) == 1
    assert market.calls[0]["bar_cutoff_ms"] == _ms(DAY, 9, 30)
