"""挂账定性批 · 输入件结构探知（只读）。

detail.csv = 逐 code-day 分类明细（39,851 行）
codes.csv  = 逐码汇总
json       = class_counts / j_hist / agree_matrix / params
"""
import json
from pathlib import Path

import pandas as pd

BASE = Path(r"D:\miniQMT策略实盘\trading-battle-back\logs")
STEM = "qfq_triage_etf_minutes_20260924_171935"

print("=== json 汇总 ===")
j = json.loads((BASE / f"{STEM}.json").read_text(encoding="utf-8"))
print("顶层键:", list(j.keys()))
for k, v in j.items():
    if isinstance(v, dict):
        print(f"  {k}: dict({len(v)}) {list(v.items())[:6]}")
    elif isinstance(v, list):
        print(f"  {k}: list({len(v)}) {v[:4]}")
    else:
        print(f"  {k}: {v}")

print("\n=== detail.csv 结构 ===")
d = pd.read_csv(BASE / f"{STEM}_detail.csv", nrows=5)
print(f"列({len(d.columns)}): {list(d.columns)}")
print("\n前 3 行:")
print(d.head(3).to_string())

print("\n=== codes.csv 结构 ===")
c = pd.read_csv(BASE / f"{STEM}_codes.csv", nrows=5)
print(f"列({len(c.columns)}): {list(c.columns)}")
print(c.head(5).to_string())