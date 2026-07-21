import pandas as pd

from quantstudio.pipeline.aligner import FieldAligner
from quantstudio.backtest.ptrade_api import _api


def _aligner():
    rules = {
        "schemas": {"etf_daily": {"columns": {
            "code": {"type": "str"}, "time": {"type": "int"},
            "open": {"type": "float"}, "high": {"type": "float"},
            "low": {"type": "float"}, "close": {"type": "float"},
            "preClose": {"type": "float"}, "volume": {"type": "float"},
            "amount": {"type": "float"}, "pctChg": {"type": "float"},
        }}},
        "source_mappings": {"tushare": {"etf_daily": {
            "identity": True, "time_to_ms": False
        }}},
    }
    return FieldAligner(rules)


def test_etf_prices_keep_three_decimal_precision():
    raw = pd.DataFrame({
        "code": ["159870"], "time": [1],
        "open": [0.8654], "high": [0.8684], "low": [0.8584],
        "close": [0.8614], "preClose": [0.8646],
        "volume": [1000], "amount": [861.4], "pctChg": [-0.37],
    })
    out, _ = _aligner().align(raw, "etf_daily", "tushare")
    assert out.loc[0, "open"] == 0.865
    assert out.loc[0, "close"] == 0.861
    assert out.loc[0, "preClose"] == 0.865


class _HistoryMarket:
    def __init__(self):
        self.end_dates = []

    def get_bars_by_count(self, codes, count, end_date, fields, fq, frequency="1d",
                          bar_cutoff_ms=None):
        # PR3: frequency 参数；PR4: bar_cutoff_ms 参数（缺口 1）。mock 同步签名。
        self.end_dates.append(end_date)
        return {codes[0]: pd.DataFrame({"close": [1.0] * count})}


def test_get_history_include_flag_selects_correct_anchor_date():
    market = _HistoryMarket()
    _api._market = market
    _api._current_date = "2026-01-05"
    _api._prev_date = "2025-12-31"
    _api._query_cache = {}

    _api.get_history(5, frequency="1d", field=["close"],
                     security_list=["159870.XSHE"], include=False, is_dict=True)
    _api._query_cache = {}
    _api.get_history(5, frequency="1d", field=["close"],
                     security_list=["159870.XSHE"], include=True, is_dict=True)

    assert market.end_dates == ["2025-12-31", "2026-01-05"]


def test_strategy_slippage_apis_update_engine_cost_without_leaking_defaults():
    from types import SimpleNamespace
    from quantstudio.backtest.backtest_engine import DEFAULT_TRADE_COST, TradeCost
    from quantstudio.backtest.ptrade_api import PtradeAPI

    engine = SimpleNamespace(cost=TradeCost())
    api = PtradeAPI()
    api._engine = engine
    api.set_slippage(slippage=0.002)
    assert engine.cost.slippage_rate == 0.002
    assert engine.cost.fixed_slippage == 0.0
    api.set_fixed_slippage(0.03)
    assert engine.cost.fixed_slippage == 0.03
    assert engine.cost.slippage_rate == 0.0
    assert DEFAULT_TRADE_COST.slippage_rate == 0.0
    assert DEFAULT_TRADE_COST.fixed_slippage == 0.0


def test_check_limit_result_accepts_two_and_four_letter_suffixes():
    import pandas as pd
    from quantstudio.backtest.ptrade_api import PtradeAPI
    api = PtradeAPI()
    api._current_day_data = pd.DataFrame({
        "code": ["002830"], "close": [10.0], "preClose": [10.0]})
    result = api.check_limit("002830.SZ")
    assert result["002830.SZ"] == 0
    assert result["002830.XSHE"] == 0
