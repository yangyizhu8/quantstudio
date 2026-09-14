# -*- coding: utf-8 -*-
"""R5/G3.5 运行器：按 R5 计划跑一次主窗口回测并快照三件套 + 日志。

用法：python run_r5.py <repo_root> <out_dir> <run_tag>
"""
import os, sys, shutil, hashlib, time
ROOT, OUT, TAG = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, ROOT)
from quantstudio.backtest.run_ptrade_strategy import run_backtest

STRATEGY = os.path.join(ROOT, "agent_workspace", "dividend_defense_smallcap_5d", "strategy.py")
KV = dict(db_path=os.path.join(ROOT, "data", "quantstudio.db"),
          capital=100_000, match_price_mode="close", engine_profile="daily-bar-v1")

t0 = time.time()
print("[%s] start %s" % (TAG, time.strftime("%Y-%m-%dT%H:%M:%S")))
res, outdir, eng = run_backtest(STRATEGY, "2020-01-01", "2026-07-31", **KV)
elapsed = time.time() - t0
outdir = str(outdir)
os.makedirs(OUT, exist_ok=True)
for name in os.listdir(outdir):
    src = os.path.join(outdir, name)
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(OUT, name))
print("[%s] outdir=%s" % (TAG, outdir))
print("[%s] elapsed_sec=%.1f (%.1f min)" % (TAG, elapsed, elapsed / 60.0))
for name in ("config.csv", "daily_stats.csv", "trades.csv"):
    p = os.path.join(OUT, name)
    if os.path.exists(p):
        h = hashlib.sha256(open(p, "rb").read()).hexdigest()
        print("[%s] %-18s %s" % (TAG, name, h))
    else:
        print("[%s] %-18s MISSING" % (TAG, name))
