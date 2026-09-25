# -*- coding: utf-8 -*-
"""Q1 本机量化（核心）：聚合 `qfq_profile: PROFILE <TYPE> <code> total=… 2_fetch_capture=… 4_reanchor_apply=…`
逐证券剖面行，给出：触发次数（码次）、去重码数、各阶段耗时合计与占比、按日期分布。

只读 data/logs/*.log*。
"""
import glob
import os
import re
from collections import defaultdict

LINE = re.compile(
    r"PROFILE\s+(?P<atype>STOCK|ETF)\s+(?P<code>\S+)\s+total=(?P<total>[\d.]+)s"
    r"(?P<rest>.*)")
PHASE = re.compile(r"(?P<name>\d+_[a-z_]+)=(?P<val>[\d.]+)s")

rows = []          # (file, atype, code, total, {phase: val})
for f in glob.glob("data/logs/*.log*"):
    try:
        txt = open(f, encoding="utf-8", errors="replace").read()
    except Exception:
        continue
    for ln in txt.splitlines():
        m = LINE.search(ln)
        if not m:
            continue
        phases = {pm.group("name"): float(pm.group("val")) for pm in PHASE.finditer(m.group("rest"))}
        rows.append((os.path.basename(f), m.group("atype"), m.group("code"), float(m.group("total")), phases))

print(f"[1] 剖面行总数（= 触发次数·码次）= {len(rows)}")
if not rows:
    raise SystemExit(0)

by_file = defaultdict(list)
for r in rows:
    by_file[r[0]].append(r)
print("\n[2] 按日志文件分布（文件 | 码次 | 去重码数 | total 合计 s | 均值 s）")
for f, rs in sorted(by_file.items(), key=lambda kv: -sum(x[3] for x in kv[1])):
    codes = {x[2] for x in rs}
    tot = sum(x[3] for x in rs)
    print(f"    {f} | {len(rs)} | {len(codes)} | {tot:.1f} | {tot/len(rs):.2f}")

all_codes = {x[2] for x in rows}
tot_all = sum(x[3] for x in rows)
print(f"\n[3] 全局：码次={len(rows)}｜去重码数={len(all_codes)}｜total 合计={tot_all:.1f}s（{tot_all/3600:.2f} h）")

print("\n[4] 各阶段耗时合计与占比（基于剖面行）")
agg = defaultdict(float)
for _, _, _, _, ph in rows:
    for k, v in ph.items():
        agg[k] += v
for k, v in sorted(agg.items(), key=lambda kv: -kv[1]):
    print(f"    {k:22s} {v:10.1f}s  {(100*v/tot_all if tot_all else 0):5.1f}%")

print("\n[5] 码次最多的 3 个码（是否反复重锚同一码）")
from collections import Counter
c = Counter(x[2] for x in rows)
for code, n in c.most_common(3):
    print(f"    {code}: {n} 次")

print("\n[6] 抽样 5 行原始剖面（确认阶段名与口径）")
for r in rows[:5]:
    print(f"    {r[0]} {r[1]} {r[2]} total={r[3]}s {r[4]}")
