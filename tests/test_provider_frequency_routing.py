"""PR3 契约测试：多频率 Provider 路由。

验证目标（主计划 7.18 路由规则 + 7.19 任务清单）：
1. frequency='1d'（默认）走原日线路径，字节级不变（隔离契约）
2. frequency='1m'/'5m' 等分钟频率路由到分钟查询方法
3. freq 标签双向映射正确（API "1m" ↔ 存储 "1min"）
4. 未知频率（如 '1s'）raise FrequencyCapabilityError(code=INVALID_FREQUENCY)
5. 分钟查询不进入日线 fallback 链（严禁回退）

这些测试覆盖 frequency_labels 映射 + Provider 路由分流的核心契约。
"""
import pytest


# ========== freq 标签双向映射 ==========

def test_api_to_storage_maps_minute_labels():
    """API 标签 → 存储 freq 列值映射正确"""
    from quantstudio.backtest.providers.frequency_labels import api_to_storage
    assert api_to_storage("1m") == "1min"
    assert api_to_storage("5m") == "5min"
    assert api_to_storage("15m") == "15min"
    assert api_to_storage("30m") == "30min"
    assert api_to_storage("60m") == "60min"
    assert api_to_storage("1d") == "daily"


def test_api_to_storage_rejects_unknown_frequency():
    """未知频率 raise FrequencyCapabilityError(code=INVALID_FREQUENCY)"""
    from quantstudio.backtest.providers.frequency_labels import (
        api_to_storage, FrequencyCapabilityError, ERR_INVALID_FREQUENCY)
    with pytest.raises(FrequencyCapabilityError) as exc_info:
        api_to_storage("1s")
    assert exc_info.value.code == ERR_INVALID_FREQUENCY


def test_is_minute_frequency_distinguishes_daily_and_minute():
    """is_minute_frequency 正确区分日线和分钟频率"""
    from quantstudio.backtest.providers.frequency_labels import is_minute_frequency
    assert is_minute_frequency("1m") is True
    assert is_minute_frequency("5m") is True
    assert is_minute_frequency("60m") is True
    assert is_minute_frequency("1d") is False


def test_normalize_api_frequency_accepts_variants():
    """normalize_api_frequency 接受变体（daily/1min 等）归一化到标准 API 标签"""
    from quantstudio.backtest.providers.frequency_labels import normalize_api_frequency
    assert normalize_api_frequency("1min") == "1m"
    assert normalize_api_frequency("5min") == "5m"
    assert normalize_api_frequency("daily") == "1d"


def test_capability_error_carries_structured_fields():
    """FrequencyCapabilityError 携带结构化字段（code/api_freq/available_freqs 等）"""
    from quantstudio.backtest.providers.frequency_labels import (
        FrequencyCapabilityError, ERR_FREQ_NOT_IN_TABLE)
    err = FrequencyCapabilityError(
        ERR_FREQ_NOT_IN_TABLE, api_freq="5m", storage_freq="5min",
        table="stock_minutes", available_freqs=["1min", "15min"])
    assert err.code == ERR_FREQ_NOT_IN_TABLE
    assert err.api_freq == "5m"
    assert err.table == "stock_minutes"
    assert err.available_freqs == ["1min", "15min"]


# ========== Provider 路由：frequency='1d' 走日线（零触达）==========

def test_get_bars_daily_frequency_does_not_touch_minute_query(monkeypatch):
    """frequency='1d' 走日线 query_bars_by_range，不调用 query_minute_bars_by_range"""
    from quantstudio.backtest.providers.duckdb_provider import DuckDBMarketDataProvider
    provider = DuckDBMarketDataProvider.__new__(DuckDBMarketDataProvider)
    provider._data = type("D", (), {})()
    # mock 日线方法正常返回
    provider._data.query_bars_by_range = lambda code, s, e: __import__('pandas').DataFrame(
        {'code': [code], 'time': [s], 'close': [10.0]})
    # spy 分钟方法：frequency='1d' 不应调用它
    minute_called = []
    provider._data.query_minute_bars_by_range = lambda *a, **k: minute_called.append(1) or __import__('pandas').DataFrame()
    provider._calendar = None

    result = provider.get_bars(["600000"], "2026-01-05", "2026-01-06", frequency="1d")
    assert "600000" in result
    assert minute_called == []  # 分钟方法未被调用


def test_get_bars_minute_frequency_routes_to_minute_query():
    """frequency='1m' 路由到 query_minute_bars_by_range"""
    import pandas as pd
    from quantstudio.backtest.providers.duckdb_provider import DuckDBMarketDataProvider
    provider = DuckDBMarketDataProvider.__new__(DuckDBMarketDataProvider)
    provider._data = type("D", (), {})()
    # spy 日线方法：分钟频率不应调用它
    daily_called = []
    provider._data.query_bars_by_range = lambda *a, **k: daily_called.append(1) or pd.DataFrame()
    # mock 分钟方法
    provider._data.query_minute_bars_by_range = lambda code, s, e, f, fq, cal=None, bar_cutoff_ms=None: pd.DataFrame(
        {'code': [code], 'time': [1000], 'freq': [f], 'close': [10.0]})
    provider._calendar = None

    result = provider.get_bars(["600000"], "2026-01-05", "2026-01-06", frequency="1m")
    assert "600000" in result
    assert daily_called == []  # 日线方法未被调用


# ========== Provider 抽象接口含 frequency + get_snapshot ==========

def test_market_data_provider_abstract_has_frequency_params():
    """MarketDataProvider 抽象接口 get_bars/get_bars_by_count 含 frequency 参数【补齐 5】"""
    import inspect
    from quantstudio.backtest.providers.base import MarketDataProvider
    sig_bars = inspect.signature(MarketDataProvider.get_bars)
    sig_count = inspect.signature(MarketDataProvider.get_bars_by_count)
    sig_snap = inspect.signature(MarketDataProvider.get_snapshot)
    assert "frequency" in sig_bars.parameters
    assert "frequency" in sig_count.parameters
    assert "frequency" in sig_snap.parameters
    # 默认值 "1d"（日线路径零触达）
    assert sig_bars.parameters["frequency"].default == "1d"
    assert sig_count.parameters["frequency"].default == "1d"
    assert sig_snap.parameters["frequency"].default == "1d"


def test_get_bars_by_count_daily_default_unchanged():
    """get_bars_by_count 不传 frequency 时默认走日线（向后兼容）"""
    import pandas as pd
    from quantstudio.backtest.providers.duckdb_provider import DuckDBMarketDataProvider
    provider = DuckDBMarketDataProvider.__new__(DuckDBMarketDataProvider)
    provider._data = type("D", (), {})()
    # 阶段1 批量化后日线路径调用 query_bars_by_count_batch（返回 {code: df}），
    # 替代旧的逐只 query_bars_by_count_multi_table。
    provider._data.query_bars_by_count_batch = lambda codes, count, before_ms, use_qfq=False: {
        code: pd.DataFrame({'code': [code], 'time': [before_ms], 'close': [10.0]})
        for code in codes
    }
    provider._calendar = None

    # 不传 frequency → 默认 '1d' → 走日线
    result = provider.get_bars_by_count(["600000"], 5, "2026-01-06")
    assert "600000" in result
