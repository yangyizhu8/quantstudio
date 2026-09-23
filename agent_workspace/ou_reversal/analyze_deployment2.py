
import os, csv, statistics
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
base = os.path.join(ROOT, "output", "backtest_results")
dirs = sorted([d for d in os.listdir(base) if d.endswith("_strategy")])
for d in dirs[-2:]:
    p = os.path.join(base, d)
    rows = list(csv.DictReader(open(os.path.join(p, "daily_stats.csv"), encoding="utf-8-sig")))
    exp, pos, nav = [], [], []
    for r in rows:
        ta = float(r["total_asset"]); mv = float(r["market_value"])
        exp.append(mv/ta if ta > 0 else 0.0); pos.append(int(r["positions"])); nav.append(ta)
    print("== %s ==" % d)
    print("   days=%d  final_nav=%.2f  return=%.2f%%" % (len(rows), nav[-1], (nav[-1]/100000.0-1)*100))
    print("   positions mean=%.1f min=%d max=%d | <10 days=%d" % (statistics.mean(pos), min(pos), max(pos), sum(1 for x in pos if x<10)))
    print("   exposure mean=%.4f min=%.4f max=%.4f | <0.80 days=%d (%.1f%%)" % (
        statistics.mean(exp), min(exp), max(exp), sum(1 for e in exp if e<0.8), 100.0*sum(1 for e in exp if e<0.8)/len(exp)))
    print()
