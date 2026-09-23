
import os, csv, statistics
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
base = os.path.join(ROOT, "output", "backtest_results")
dirs = sorted([d for d in os.listdir(base) if d.endswith("_strategy")])
d = os.path.join(base, dirs[-1])
print("result dir:", dirs[-1])
rows = list(csv.DictReader(open(os.path.join(d, "daily_stats.csv"), encoding="utf-8-sig")))
print("days:", len(rows))
exp, pos = [], []
for r in rows:
    ta = float(r["total_asset"]); mv = float(r["market_value"])
    exp.append(mv/ta if ta > 0 else 0.0)
    pos.append(int(r["positions"]))
print("positions: min=%d max=%d mean=%.1f" % (min(pos), max(pos), statistics.mean(pos)))
print("exposure : min=%.4f max=%.4f mean=%.4f" % (min(exp), max(exp), statistics.mean(exp)))
below8 = sum(1 for e in exp if e < 0.8)
below5 = sum(1 for e in exp if e < 0.5)
print("days exposure<0.80: %d / %d (%.1f%%)" % (below8, len(exp), 100.0*below8/len(exp)))
print("days exposure<0.50: %d / %d (%.1f%%)" % (below5, len(exp), 100.0*below5/len(exp)))
print("positions<10 days: %d (%.1f%%)" % (sum(1 for p in pos if p < 10), 100.0*sum(1 for p in pos if p<10)/len(pos)))
print()
print("first 20 days: date / positions / exposure")
for r, e, p in list(zip(rows, exp, pos))[:20]:
    print("   %s  pos=%2d  exp=%.4f" % (r["date"], p, e))
print()
print("last 5 days:")
for r, e, p in list(zip(rows, exp, pos))[-5:]:
    print("   %s  pos=%2d  exp=%.4f" % (r["date"], p, e))
