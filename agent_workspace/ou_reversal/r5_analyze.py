
import os, csv, json, statistics
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
prov = json.load(open(os.path.join(ROOT, "agent_workspace", "ou_reversal_csi300_10", "r5_provenance_run1.json"), encoding="utf-8"))
d = prov["output_dir"]
rows = list(csv.DictReader(open(os.path.join(d, "daily_stats.csv"), encoding="utf-8-sig")))
nav = [float(r["total_asset"]) for r in rows]
bm  = [float(r["benchmark"]) for r in rows]
exp = [float(r["market_value"])/float(r["total_asset"]) if float(r["total_asset"])>0 else 0.0 for r in rows]
pos = [int(r["positions"]) for r in rows]
peak, mdd = nav[0], 0.0
for v in nav:
    peak = max(peak, v); mdd = min(mdd, v/peak - 1.0)
print("== R5 run1 全窗口 287 日 ==")
print("  天数=%d  %s .. %s" % (len(rows), rows[0]["date"], rows[-1]["date"]))
print("  期末净值=%.2f  总收益=%.2f%%  基准=%.2f%%  超额=%.2f pp" % (
    nav[-1], (nav[-1]/100000-1)*100, (bm[-1]/100-1)*100, ((nav[-1]/100000)-(bm[-1]/100))*100))
print("  最大回撤=%.2f%%" % (mdd*100))
print("  持仓: mean=%.2f  持满10的天数=%d/%d  空仓天数=%d" % (
    statistics.mean(pos), sum(1 for p in pos if p==10), len(pos), sum(1 for p in pos if p==0)))
print("  敞口: mean=%.4f  max=%.4f" % (statistics.mean(exp), max(exp)))
trades = list(csv.DictReader(open(os.path.join(d, "trades.csv"), encoding="utf-8-sig")))
print("  成交笔数=%d" % len(trades))
