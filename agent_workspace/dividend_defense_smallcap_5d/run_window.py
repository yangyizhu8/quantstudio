# -*- coding: utf-8 -*-
"""窗口化 R5 运行器（支持 db_path 覆盖，用于副本复验）。

用法：python run_window.py <repo_root> <out_dir> <tag> <start> <end> [<db_path>]
"""
import os, sys, shutil, hashlib, time
ROOT, OUT, TAG, START, END = sys.argv[1:6]
DB = sys.argv[6] if len(sys.argv) > 6 else os.path.join(ROOT, "data", "quantstudio.db")
sys.path.insert(0, ROOT)
from quantstudio.backtest.run_ptrade_strategy import run_backtest

STRATEGY = os.path.join(ROOT, "agent_workspace", "dividend_defense_smallcap_5d", "strategy.py")
KV = dict(db_path=DB, capital=100_000, match_price_mode="close", engine_profile="daily-bar-v1")

t0 = time.time()
print("[%s] window=%s..%s db=%s" % (TAG, START, END, DB))
res, outdir, eng = run_backtest(STRATEGY, START, END, **KV)
el = time.time() - t0
outdir = str(outdir)
os.makedirs(OUT, exist_ok=True)
for name in os.listdir(outdir):
    src = os.path.join(outdir, name)
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(OUT, name))
print("[%s] elapsed_sec=%.1f (%.1f min)" % (TAG, el, el / 60.0))
for name in ("config.csv", "daily_stats.csv", "trades.csv", "round_trips.csv"):
    p = os.path.join(OUT, name)
    if os.path.exists(p):
        print("[%s] %-18s %s" % (TAG, name, hashlib.sha256(open(p, "rb").read()).hexdigest()))
    else:
        print("[%s] %-18s MISSING" % (TAG, name))
