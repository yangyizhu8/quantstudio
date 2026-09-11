# -*- coding: utf-8 -*-
"""R1 addendum: get_stock_exrights call-cost benchmark (strategy-layer dividend path feasibility)"""
import os, sys, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, "data", "quantstudio.db")
import pandas as pd
from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig
from quantstudio.backtest.ptrade_api import _api

DATE, PREV = "2026-07-30", "2026-07-29"
cfg = EngineConfig(db_path=DB, output_dir=os.path.join(ROOT, "output"),
                   research_dir=os.path.join(ROOT, "output", "research"))
eng = BacktestEngine(db_path=DB, strategy={}, start="2026-01-01", end=DATE,
                     config=cfg, strategy_type="ptrade")
_api.attach(eng, None, None, DATE, PREV, {})
api = _api

days = [str(x)[:10].replace('-', '') for x in api.get_trade_days(end_date=DATE, count=245)]
print("trading days in 12m window:", len(days))

def bench(codes, day_list, label):
    t0 = time.perf_counter()
    hits = 0
    for c in codes:
        for d in day_list:
            if api.get_stock_exrights(c, d) is not None:
                hits += 1
    dt = time.perf_counter() - t0
    n = len(codes) * len(day_list)
    print("[%s] calls=%d hits=%d elapsed=%.2fs  %.3f ms/call" % (label, n, hits, dt, dt / n * 1000))
    return dt / n

# 1) single-code, 245-day sweep (miss-dominated, the strategy-layer loop shape)
c1 = bench(["600519.SS"], days, "1 code x 245 days")
# 2) 5 codes x 245 days
c5 = bench(["600519.SS", "601398.SS", "600036.SS", "000060.SZ", "000001.SZ"], days[:50], "5 codes x 50 days")

per_call = c1
print()
print("=== extrapolation (strategy-layer dividend path) ===")
for pool in [200, 300, 600, 1000]:
    calls = pool * 244
    sec = calls * per_call
    print("  defensive rebalance, pool=%4d codes -> %8d calls -> %7.1f s (%.1f min)" % (pool, calls, sec, sec / 60))
print("  whole backtest (Jan+Apr, ~5 rebalances/defensive-month x 6 years = 60 defensive rebalances, pool=300):")
calls = 60 * 300 * 244
print("    %d calls -> %.1f min" % (calls, calls * perf_divide(per_call, 1) if False else calls * per_call / 60))
print()
print("=== schema-probe cost inside query_stock_exrights (DESCRIBE per call) ===")
import duckdb
con = duckdb.connect(DB, read_only=True)
t0 = time.perf_counter()
for _ in range(200):
    con.execute("DESCRIBE stock_dividend").fetchall()
t1 = time.perf_counter()
print("  DESCRIBE x200: %.3f s -> %.3f ms each" % (t1 - t0, (t1 - t0) / 200 * 1000))
t0 = time.perf_counter()
for _ in range(200):
    con.execute("SELECT ex_date, cash_div_before_tax, stk_div FROM stock_dividend WHERE code=? AND ex_date=?", ["600519", 1782403200000]).fetchall()
t1 = time.perf_counter()
print("  indexed SELECT x200: %.3f s -> %.3f ms each" % (t1 - t0, (t1 - t0) / 200 * 1000))
con.close()
print("DONE")
