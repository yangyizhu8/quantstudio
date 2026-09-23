
import os, csv, json, statistics
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
prov = json.load(open(os.path.join(ROOT, "agent_workspace", "ou_reversal_csi300_10", "r5_provenance_run1.json"), encoding="utf-8"))
d = prov["output_dir"]
rows = list(csv.DictReader(open(os.path.join(d, "daily_stats.csv"), encoding="utf-8-sig")))
# 分段：按持仓数把时间轴切成「持有段」与「空仓段」
segs = []
cur = None
for r in rows:
    p = int(r["positions"])
    state = "hold" if p > 0 else "flat"
    if cur is None or cur["state"] != state:
        cur = dict(state=state, start=r["date"], end=r["date"], nav0=float(r["total_asset"]), nav1=float(r["total_asset"]))
        segs.append(cur)
    cur["end"] = r["date"]; cur["nav1"] = float(r["total_asset"])
print("== 持有/空仓分段（run1）==")
print("  %-6s %-12s %-12s %6s %9s" % ("状态", "起", "止", "天数", "段收益%"))
hold_ret, flat_ret = 1.0, 1.0
for s in segs:
    n = sum(1 for r in rows if s["start"] <= r["date"] <= s["end"])
    ret = (s["nav1"]/s["nav0"] - 1)*100
    print("  %-6s %-12s %-12s %6d %9.2f" % ("持有" if s["state"]=="hold" else "空仓", s["start"], s["end"], n, ret))
    if s["state"] == "hold": hold_ret *= (1+ret/100)
    else: flat_ret *= (1+ret/100)
print()
print("  持有段累计: %.2f%%   空仓段累计: %.2f%%" % ((hold_ret-1)*100, (flat_ret-1)*100))
hold_days = sum(1 for r in rows if int(r["positions"]) > 0)
print("  持有天数=%d / %d (%.1f%%)  空仓天数=%d" % (hold_days, len(rows), 100.0*hold_days/len(rows), len(rows)-hold_days))
