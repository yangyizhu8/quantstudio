# -*- coding: utf-8 -*-
"""最小复现：context.portfolio.positions 的元素类型是否满足 PTrade 契约。"""
import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from quantstudio.backtest.backtest_engine import Account, Position as EnginePosition, BacktestEngine
from quantstudio.backtest.ptrade_api import Portfolio, _api

eng = object.__new__(BacktestEngine)
eng.account = Account(cash=50.0, positions={
    "159870.SZ": EnginePosition("159870.SZ", volume=100, avg_cost=0.86, can_sell=100)})
_api._engine = eng

pf = Portfolio(50.0, {})
pos = pf.positions
print("portfolio.positions 键:", list(pos.keys()))
if pos:
    v = list(pos.values())[0]
    print("元素类型:", type(v).__module__ + "." + type(v).__name__)
    for attr in ("amount", "enable_amount", "cost_basis", "last_sale_price",
                 "volume", "can_sell", "sid"):
        print("   %-16s = %r" % (attr, getattr(v, attr, "<MISSING>")))
    amount = int(getattr(v, "amount", 0) or 0)
    enable = int(getattr(v, "enable_amount", 0) or 0)
    print()
    print("策略视角（PTrade 契约）:")
    print("   getattr(position,'amount',0)        =", amount, "-> _held_bare_codes 判定:", "有持仓" if amount > 0 else "空仓(误判)")
    print("   getattr(position,'enable_amount',0) =", enable, "-> 可卖量判定:", "可卖" if enable > 0 else "不可卖(误判)")
_api._engine = None
