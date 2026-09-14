# -*- coding: utf-8 -*-
"""最小复现：ETF平滑动量轮动.py:82 的 .value —— 各 Position 形态字段可用性对照。"""
import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from quantstudio.backtest.backtest_engine import Account, Position as EnginePosition, BacktestEngine
from quantstudio.backtest.ptrade_api import Portfolio, _api

eng = object.__new__(BacktestEngine)
eng.account = Account(cash=1000.0, positions={
    "510300.SS": EnginePosition("510300.SS", volume=100, avg_cost=4.0, can_sell=100)})
prev = (getattr(_api, "_engine", None), getattr(_api, "_prices", None))
_api._engine, _api._prices = eng, {"510300.SS": 4.2}
try:
    pf = Portfolio(1000.0, {})
    pos = pf.positions["510300.SS"]
    print("修复后 Position 类型:", type(pos).__module__ + "." + type(pos).__name__)
    for attr in ("value", "market_value", "amount", "enable_amount", "cost_basis", "last_sale_price"):
        ok = hasattr(pos, attr)
        print("   %-16s exists=%s%s" % (attr, ok, "" if ok else "   <-- 策略此处会 AttributeError"))
    print()
    try:
        _ = pos.value
        print("   pos.value =", _)
    except AttributeError as e:
        print("   复现结果: AttributeError ->", e)
    print("   对照: Portfolio.positions_value =", pf.positions_value, "（真值应为 420.0）")
finally:
    _api._engine, _api._prices = prev
