
import os, csv
d = r"D:\miniQMT策略实盘\QuantStudio\output\backtest_results\20260923_001350_strategy"
print("== config.csv ==")
print(open(os.path.join(d, "config.csv"), encoding="utf-8").read().strip()[:400])
print()
print("== trades.csv (head) ==")
rows = list(csv.reader(open(os.path.join(d, "trades.csv"), encoding="utf-8")))
print("header:", rows[0])
print("n_trades:", len(rows)-1)
for r in rows[1:14]:
    print("   ", r)
print()
print("== daily_stats.csv ==")
rows2 = list(csv.reader(open(os.path.join(d, "daily_stats.csv"), encoding="utf-8")))
print("header:", rows2[0])
for r in rows2[1:8]:
    print("   ", r)
