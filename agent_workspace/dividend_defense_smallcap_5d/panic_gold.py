# -*- coding: utf-8 -*-
"""恐慌抄底 G3.5 黄金对比 — 在指定副本库上运行并快照产物。
用法: python panic_gold.py <repo_root> <db> <start> <end> <out_dir> <tag>
"""
import os, sys, shutil, hashlib
ROOT, DB, START, END, OUT, TAG = sys.argv[1:7]
sys.path.insert(0, ROOT)
from quantstudio.backtest.run_ptrade_strategy import run_backtest

STRAT = os.path.join(ROOT, "quantstudio", "backtest", "strategies", "恐慌抄底事件驱动逆向策略.py")
print("[%s] db=%s" % (TAG, DB))
print("[%s] window=%s..%s" % (TAG, START, END))
res, outdir, eng = run_backtest(STRAT, START, END, db_path=DB, capital=1_000_000)
outdir = str(outdir)
os.makedirs(OUT, exist_ok=True)
for f in os.listdir(outdir):
    src = os.path.join(outdir, f)
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(OUT, f))
print("[%s] outdir=%s" % (TAG, outdir))
print("[%s] snapshot=%s" % (TAG, OUT))
for f in sorted(os.listdir(OUT)):
    p = os.path.join(OUT, f)
    print("   %-24s %s" % (f, hashlib.sha256(open(p, "rb").read()).hexdigest()[:24]))
tr = os.path.join(OUT, "trades.csv")
if os.path.exists(tr):
    with open(tr, encoding="utf-8") as fh:
        print("[%s] trades rows=%d" % (TAG, sum(1 for _ in fh) - 1))
else:
    print("[%s] trades.csv MISSING (no trades)" % TAG)
