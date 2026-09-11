# -*- coding: utf-8 -*-
"""验收⑥：持仓视图热路径微基准（命中路径 / 兜底路径；持仓数 P50/P99 档）。"""
import os, sys, time, statistics
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from quantstudio.backtest.backtest_engine import Account, Position as EnginePosition, BacktestEngine
from quantstudio.backtest.ptrade_api import Portfolio, _api

def build(n):
    eng = object.__new__(BacktestEngine)
    pos = {}
    prices = {}
    for i in range(n):
        code = "%06d.SZ" % (i + 1)
        pos[code] = EnginePosition(code, volume=100 * (i + 1), avg_cost=10.0, can_sell=100 * i)
        prices[code] = 11.0
    eng.account = Account(cash=1e6, positions=pos)
    return eng, prices

def timeit(fn, iters=400):
    ts = []
    for _ in range(iters):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1e6)   # 微秒
    ts.sort()
    return ts[len(ts) // 2], ts[int(len(ts) * 0.99) - 1]

prev = (getattr(_api, "_engine", None), getattr(_api, "_prices", None))
try:
    print("持仓数档位 | 命中路径 P50/P99 (us) | 兜底路径 P50/P99 (us)")
    for n in (5, 20, 60):
        eng, prices = build(n)
        _api._engine, _api._prices = eng, prices
        pf = Portfolio(1e6, {})
        hit = timeit(lambda: pf.positions)
        _api._prices = {}
        miss = timeit(lambda: pf.positions)
        print("  %3d 只    | %8.1f / %8.1f | %8.1f / %8.1f" % (n, hit[0], hit[1], miss[0], miss[1]))
    # 对比：get_positions() 同口径
    eng, prices = build(20)
    _api._engine, _api._prices = eng, prices
    pf = Portfolio(1e6, {})
    g = timeit(lambda: _api.get_positions())
    p = timeit(lambda: pf.positions)
    print()
    print("20 只：get_positions() P50=%.1fus  |  context.portfolio.positions P50=%.1fus" % (g[0], p[0]))
finally:
    _api._engine, _api._prices = prev
