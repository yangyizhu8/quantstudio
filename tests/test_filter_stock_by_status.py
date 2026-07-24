"""filter_stock_by_status / get_stock_status 退市整理期复写对齐测试。

验证目标（用户实现要点）：
1. ST 过滤读 is_st_reliable（不再依赖不可靠的 isST 字段）
2. DELISTING_SORTING 读 is_delisting_risk（退市兜底判定）
3. HALT 增强为 suspendFlag==1 OR volume==0
4. DELISTING 保持（无前日数据行被过滤）
5. 4 种 filter_type 全支持 + 向后兼容默认值
6. get_stock_status 同步增强
7. 源码落地检查
"""
import pytest
import pandas as pd

from quantstudio.backtest.ptrade_api import _api as ptrade_api


def _attach_with_prev_day(prev_day_df):
    """注入 _prev_day_data（绕过 engine，直接赋值给模块级 _api 单例）。"""
    ptrade_api._prev_day_data = prev_day_df


def _make_prev_day(rows):
    """构造 _prev_day_data DataFrame。
    rows: list of dict with fields code / isST / suspendFlag / volume / close
          / is_st_reliable / is_delisting_risk / is_st_reliable_source / is_delisting_risk_source
    """
    defaults = {
        "code": "600000", "isST": 0, "suspendFlag": 0, "volume": 10000,
        "close": 10.0,
        "is_st_reliable": False, "is_delisting_risk": False,
        "is_st_reliable_source": "none", "is_delisting_risk_source": "none",
    }
    rows_filled = []
    for r in rows:
        d = defaults.copy()
        d.update(r)
        rows_filled.append(d)
    return pd.DataFrame(rows_filled)


# ========== 1. ST 过滤：读 is_st_reliable（不再依赖 isST）==========

def test_st_filter_by_is_st_reliable():
    """is_st_reliable=True → 被 ST 过滤，与原 isST 值无关。
    回归：002231 isST=0 但 is_st_reliable=True，应被正确过滤。"""
    df = _make_prev_day([
        {"code": "002231", "isST": 0, "is_st_reliable": True, "is_st_reliable_source": "namechange"},
    ])
    _attach_with_prev_day(df)
    result = ptrade_api.filter_stock_by_status(["002231"], filter_type=["ST"])
    assert result == [], "is_st_reliable=True 应被 ST 过滤（即使 isST=0）"


def test_normal_stock_not_filtered_by_st():
    """正常股 is_st_reliable=False + is_delisting_risk=False → 不被过滤。"""
    df = _make_prev_day([
        {"code": "600000", "isST": 0, "is_st_reliable": False, "is_delisting_risk": False},
    ])
    _attach_with_prev_day(df)
    result = ptrade_api.filter_stock_by_status(["600000"], filter_type=["ST"])
    assert result == ["600000"], "正常股不应被 ST 过滤"


# ========== 2. HALT 增强：suspendFlag OR volume==0 ==========

def test_halt_by_suspend_flag():
    """suspendFlag==1 → 被停牌过滤。"""
    df = _make_prev_day([
        {"code": "600000", "suspendFlag": 1, "volume": 50000},
    ])
    _attach_with_prev_day(df)
    result = ptrade_api.filter_stock_by_status(["600000"], filter_type=["HALT"])
    assert result == [], "suspendFlag==1 应被停牌过滤"


def test_halt_by_volume_zero():
    """volume==0 → 被停牌过滤（增强：原实现只查 suspendFlag）。"""
    df = _make_prev_day([
        {"code": "600000", "suspendFlag": 0, "volume": 0},
    ])
    _attach_with_prev_day(df)
    result = ptrade_api.filter_stock_by_status(["600000"], filter_type=["HALT"])
    assert result == [], "volume==0 应被停牌过滤（增强行为）"


# ========== 3. DELISTING：无前日数据行被过滤 ==========

def test_delisting_by_no_prev_data():
    """无前日数据行 → 被退市过滤。"""
    df = _make_prev_day([
        {"code": "600000"},
    ])
    _attach_with_prev_day(df)
    # 002999 不在 prev_day_data 中
    result = ptrade_api.filter_stock_by_status(["600000", "002999"], filter_type=["DELISTING"])
    assert result == ["600000"], "无前日数据的 002999 应被 DELISTING 过滤"


# ========== 4. DELISTING_SORTING：读 is_delisting_risk ==========

def test_delisting_sorting_by_is_delisting_risk():
    """is_delisting_risk=True → 被退市整理期过滤。"""
    df = _make_prev_day([
        {"code": "002231", "is_st_reliable": True, "is_delisting_risk": True,
         "is_delisting_risk_source": "both", "close": 0.6},
    ])
    _attach_with_prev_day(df)
    result = ptrade_api.filter_stock_by_status(["002231"], filter_type=["DELISTING_SORTING"])
    assert result == [], "is_delisting_risk=True 应被 DELISTING_SORTING 过滤"


def test_normal_stock_not_filtered_by_delisting_sorting():
    """正常股 is_delisting_risk=False → 不被 DELISTING_SORTING 过滤。"""
    df = _make_prev_day([
        {"code": "600000", "is_delisting_risk": False, "close": 10.0},
    ])
    _attach_with_prev_day(df)
    result = ptrade_api.filter_stock_by_status(["600000"], filter_type=["DELISTING_SORTING"])
    assert result == ["600000"], "正常股不应被 DELISTING_SORTING 过滤"


# ========== 5. 混合 batch ==========

def test_mixed_batch():
    """混合 batch [正常股, ST, 停牌, 退市整理期] → 只保留正常股。"""
    df = _make_prev_day([
        {"code": "600000", "is_st_reliable": False, "is_delisting_risk": False, "close": 10.0},
        {"code": "002231", "is_st_reliable": True, "is_delisting_risk": True,  "close": 0.6},
        {"code": "000001", "suspendFlag": 1, "is_st_reliable": False, "is_delisting_risk": False},
        {"code": "002808", "is_st_reliable": True, "is_delisting_risk": True, "close": 0.22},
    ])
    _attach_with_prev_day(df)
    # 默认 filter_type=["ST","HALT","DELISTING"]
    result = ptrade_api.filter_stock_by_status(["600000", "002231", "000001", "002808"])
    assert result == ["600000"], f"混合 batch 应只保留正常股 600000，got {result}"


# ========== 6. 向后兼容：默认 filter_type 不含 DELISTING_SORTING ==========

def test_default_filter_type_backward_compatible():
    """不传 DELISTING_SORTING 时不触发该过滤，保持默认 ['ST','HALT','DELISTING']。"""
    df = _make_prev_day([
        {"code": "002231", "is_st_reliable": True, "is_delisting_risk": True, "close": 0.6},
    ])
    _attach_with_prev_day(df)
    # 默认 filter_type
    result = ptrade_api.filter_stock_by_status(["002231"])
    assert result == [], "默认 filter_type 含 ST，002231 is_st_reliable=True 应被过滤"


def test_exclude_st_from_default():
    """只传 ['HALT','DELISTING'] 不走 ST 判定，ST 股不被过滤。"""
    df = _make_prev_day([
        {"code": "002231", "is_st_reliable": True, "is_delisting_risk": False, "close": 3.0},
    ])
    _attach_with_prev_day(df)
    result = ptrade_api.filter_stock_by_status(["002231"], filter_type=["HALT", "DELISTING"])
    assert result == ["002231"], "不传 ST 时 ST 股不应被过滤"


# ========== 7. get_stock_status 同步增强 ==========

def test_get_stock_status_st_semantics():
    """get_stock_status ST 查询：读 is_st_reliable OR is_delisting_risk（与 filter 一致）。"""
    df = _make_prev_day([
        {"code": "002231", "is_st_reliable": True, "is_delisting_risk": True},
        {"code": "600000", "is_st_reliable": False, "is_delisting_risk": False},
    ])
    _attach_with_prev_day(df)
    status = ptrade_api.get_stock_status(["002231", "600000"], query_type='ST')
    assert status["002231"] is True, "is_st_reliable=True → get_stock_status ST=True"
    assert status["600000"] is False, "正常股 → ST=False"


def test_get_stock_status_delisting_sorting():
    """get_stock_status DELISTING_SORTING：仅读 is_delisting_risk。"""
    df = _make_prev_day([
        {"code": "002231", "is_st_reliable": True, "is_delisting_risk": True},
        {"code": "002808", "is_st_reliable": False, "is_delisting_risk": True,
         "is_delisting_risk_source": "price"},
    ])
    _attach_with_prev_day(df)
    status = ptrade_api.get_stock_status(["002231", "002808"], query_type='DELISTING_SORTING')
    assert status["002231"] is True
    assert status["002808"] is True, "即使 is_st_reliable=False，is_delisting_risk=True 也应返回 True"


def test_get_stock_status_halt():
    """get_stock_status HALT：suspendFlag==1 OR volume==0。"""
    df = _make_prev_day([
        {"code": "600000", "suspendFlag": 1, "volume": 50000},
        {"code": "000001", "suspendFlag": 0, "volume": 0},
        {"code": "000002", "suspendFlag": 0, "volume": 50000},
    ])
    _attach_with_prev_day(df)
    status = ptrade_api.get_stock_status(["600000", "000001", "000002"], query_type='HALT')
    assert status["600000"] is True, "suspendFlag==1"
    assert status["000001"] is True, "volume==0"
    assert status["000002"] is False, "正常"


# ========== 8. 源码落地检查 ==========

def test_source_has_four_filter_types():
    """ptrade_api.py 里 filter_stock_by_status 文档/代码含 4 种 filter_type 说明。"""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "quantstudio" / "backtest" /
           "ptrade_api.py").read_text(encoding="utf-8")
    assert "'ST'" in src, "应含 ST filter_type"
    assert "'HALT'" in src, "应含 HALT filter_type"
    assert "'DELISTING'" in src, "应含 DELISTING filter_type"
    assert "DELISTING_SORTING" in src, "应含 DELISTING_SORTING 字符串"


def test_source_has_is_st_reliable():
    """filter_stock_by_status 代码读取 is_st_reliable 字段。"""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "quantstudio" / "backtest" /
           "ptrade_api.py").read_text(encoding="utf-8")
    assert "is_st_reliable" in src, "filter_stock_by_status 应读取 is_st_reliable"
    assert "is_delisting_risk" in src, "filter_stock_by_status 应读取 is_delisting_risk"


# ========== 9. 空数据兜底 ==========

def test_empty_prev_day_data_returns_all():
    """_prev_day_data 为空时不过滤任何股票（安全兜底，避免误杀）。"""
    _attach_with_prev_day(pd.DataFrame({"code": []}))
    result = ptrade_api.filter_stock_by_status(["600000", "002231"], filter_type=["ST", "HALT", "DELISTING", "DELISTING_SORTING"])
    assert result == ["600000", "002231"], "空数据时不误杀"


def test_none_prev_day_data_returns_all():
    """_prev_day_data=None 时不过滤。"""
    _attach_with_prev_day(None)
    result = ptrade_api.filter_stock_by_status(["600000", "002231"])
    assert result == ["600000", "002231"], "None 数据时不误杀"


# ========== 10. Explicit PIT date routing (agent-first reusable component) ==========

class _StatusReferenceFixture:
    def __init__(self, by_date):
        self.by_date = by_date
        self.calls = []

    def get_stock_status(self, codes, date):
        date_key = str(date)[:10]
        self.calls.append((tuple(codes), date_key))
        rows = []
        payload = self.by_date.get(date_key, {})
        for code in codes:
            values = payload.get(code, {})
            rows.append({
                'code': code,
                'is_st': values.get('is_st', False),
                'is_halt': values.get('is_halt', False),
                'is_delisting_risk': values.get('is_delisting_risk', False),
                'is_delisted': values.get('is_delisted', False),
            })
        return pd.DataFrame(rows)


def test_get_stock_status_explicit_historical_date_uses_reference_not_prev_snapshot():
    """A five-day suspension filter must query each requested PIT date."""
    ptrade_api._prev_day_data = _make_prev_day([
        {'code': '600000', 'suspendFlag': 0, 'volume': 10000},
    ])
    ptrade_api._prev_date = '2026-07-23'
    ptrade_api._current_date = '2026-07-24'
    reference = _StatusReferenceFixture({
        '2026-07-21': {'600000': {'is_halt': True}},
    })
    original = ptrade_api._reference
    ptrade_api._reference = reference
    try:
        status = ptrade_api.get_stock_status(
            ['600000.SS'], query_type='HALT', query_date='2026-07-21')
    finally:
        ptrade_api._reference = original
    assert status['600000.SS'] is True
    assert reference.calls == [(('600000',), '2026-07-21')]


def test_filter_stock_by_status_explicit_current_date_uses_current_daily_snapshot():
    """Order-time filtering must not silently reuse yesterday's normal status."""
    ptrade_api._prev_day_data = _make_prev_day([
        {'code': '600000', 'is_st_reliable': False, 'volume': 10000},
    ])
    ptrade_api._daily_curr_data = _make_prev_day([
        {'code': '600000', 'is_st_reliable': True, 'volume': 10000},
    ])
    ptrade_api._prev_date = '2026-07-23'
    ptrade_api._current_date = '2026-07-24'
    result = ptrade_api.filter_stock_by_status(
        ['600000.SS'], filter_type=['ST'], query_date='2026-07-24')
    assert result == []



def test_get_stock_status_historical_normalized_row_without_volume_is_not_halted():
    """ReferenceDataProvider already supplies is_halt; missing raw volume must not default to zero."""
    ptrade_api._prev_day_data = _make_prev_day([
        {'code': '600000', 'suspendFlag': 0, 'volume': 10000},
    ])
    ptrade_api._prev_date = '2026-07-17'
    ptrade_api._current_date = '2026-07-20'
    reference = _StatusReferenceFixture({
        '2026-07-13': {'600000': {'is_halt': False}},
    })
    original = ptrade_api._reference
    ptrade_api._reference = reference
    try:
        status = ptrade_api.get_stock_status(
            ['600000.SS'], query_type='HALT', query_date='2026-07-13')
    finally:
        ptrade_api._reference = original
    assert status['600000.SS'] is False
