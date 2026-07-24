from __future__ import annotations

import inspect
from types import SimpleNamespace

from quantstudio.backtest.backtest_engine import TradeCost
from quantstudio.backtest.ptrade_api import PtradeAPI


def test_slippage_signatures_match_ptrade_keyword_contract():
    assert str(inspect.signature(PtradeAPI.set_slippage)) == "(self, slippage=0.1)"
    assert str(inspect.signature(PtradeAPI.set_fixed_slippage)) == "(self, fixedslippage=0.1)"


def test_slippage_apis_apply_ptrade_named_arguments():
    api = PtradeAPI()
    api._engine = SimpleNamespace(cost=TradeCost())
    api.set_slippage(slippage=0.002)
    assert api._engine.cost.slippage_rate == 0.002
    assert api._engine.cost.fixed_slippage == 0.0
    api.set_fixed_slippage(fixedslippage=0.03)
    assert api._engine.cost.fixed_slippage == 0.03
    assert api._engine.cost.slippage_rate == 0.0


def test_local_only_slippage_alias_is_rejected_like_ptrade():
    api = PtradeAPI()
    api._engine = SimpleNamespace(cost=TradeCost())
    try:
        api.set_slippage(slippage_ratio=0.002)
    except TypeError as exc:
        assert "slippage_ratio" in str(exc)
    else:
        raise AssertionError("local adapter must reject non-PTrade slippage_ratio keyword")


def test_get_stock_info_returns_formal_ptrade_shape():
    class Reference:
        @staticmethod
        def get_security_info(code):
            return {"start_date": "2010-05-06", "display_name": "Sample"}

    api = PtradeAPI(reference=Reference())
    result = api.get_stock_info("600000.SS", field=["listed_date", "stock_name"])
    assert result == {
        "600000.SS": {"listed_date": "2010-05-06", "stock_name": "Sample"}
    }
