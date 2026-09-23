
import os, csv, statistics
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
base = os.path.join(ROOT, "output", "backtest_results")
for d in ("20260923_002432_strategy", "20260923_074940_strategy"):
    p = os.path.join(base, d)
    if not os.path.isdir(p):
        print(d, "MISSING"); continue
    rows = list(csv.DictReader(open(os.path.join(p, "daily_stats.csv"), encoding="utf-8-sig")))
    nav = [float(r["total_asset"]) for r in rows]
    bm  = [float(r["benchmark"]) for r in rows]
    print("== %s ==" % d)
    print("   strategy %.2f%% | benchmark %.2f%% | excess %.2f pp" % (
        (nav[-1]/100000.0-1)*100, (bm[-1]/100.0-1)*100, ((nav[-1]/100000.0)-(bm[-1]/100.0))*100))
    print("   days=%d  %s .. %s" % (len(rows), rows[0]["date"], rows[-1]["date"]))
    print("   first 8 days: " + " | ".join("%s pos=%s exp=%.2f" % (r["date"][5:], r["positions"], float(r["market_value"])/float(r["total_asset"]) if float(r["total_asset"])>0 else 0) for r in rows[:8]))
    print()
