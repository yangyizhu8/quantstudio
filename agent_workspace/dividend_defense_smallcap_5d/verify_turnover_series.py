# -*- coding: utf-8 -*-
"""R1 补证据：B2 修复后，近 N 交易日换手率序列是否可用（策略层按日循环写法）。"""
import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, "data", "quantstudio.db")
import pandas as pd, duckdb, statistics
from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig
from quantstudio.backtest.ptrade_api import _api

DATE, PREV = "2026-07-30", "2026-07-29"
cfg = EngineConfig(db_path=DB, output_dir=os.path.join(ROOT, "output"),
                   research_dir=os.path.join(ROOT, "output", "research"))
eng = BacktestEngine(db_path=DB, strategy={}, start="2026-01-01", end=DATE,
                     config=cfg, strategy_type="ptrade")
_api.attach(eng, None, None, DATE, PREV, {})

POOL = ["600519.SS", "000060.SZ", "000001.SZ"]
days = [str(x)[:10] for x in _api.get_trade_days(end_date=DATE, count=21)][:-1]
days_api = [d.replace("-", "") for d in days]
print("窗口交易日数:", len(days_api), days_api[0], "->", days_api[-1])

series = {c.split(".")[0]: [] for c in POOL}
for d in days_api:
    df = _api.get_fundamentals(POOL, "valuation", fields=["turnover_ratio"], date=d)
    for i, r in df.iterrows():
        series[str(i).split(".")[0]].append(float(r["turnover_ratio"]))

con = duckdb.connect(DB, read_only=True)
ok = True
for c, vals in series.items():
    rows = con.execute("""SELECT turnover_rate FROM stock_daily_valuation
        WHERE code=? AND time IN ({}) ORDER BY time""".format(",".join(["?"]*len(days_api))),
        [c] + [int(pd.Timestamp(d, tz="Asia/Shanghai").timestamp()*1000) for d in days]).fetchall()
    truth = [float(x[0]) for x in rows]
    match = (len(vals) == len(truth)) and all(abs(a-b) < 1e-9 for a, b in zip(vals, truth))
    ok = ok and match
    print("  %s  n=%d  逐值一致=%s  首值=%s  末值=%s  20日std=%.6f" % (
        c, len(vals), match, vals[0], vals[-1], statistics.pstdev(vals)))
con.close()
print("RESULT:", "PASS - 近20日换手率序列可用且与库内真值逐值一致" if ok else "FAIL")
sys.exit(0 if ok else 1)
