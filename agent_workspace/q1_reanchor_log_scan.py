# -*- coding: utf-8 -*-
"""Q1 本机量化侦察：扫描本地 daemon 日志，定位重锚环标记与量级。

只读；逐文件统计标记出现次数 + 抽样行（含时间戳），为「触发次数×窗口跨度×耗时占比」打基础。
"""
import glob
import os
import re

MARKERS = {
    "分片/shard": re.compile(r"分片|shard", re.I),
    "回放/replay": re.compile(r"回放|replay", re.I),
    "重锚/reanchor": re.compile(r"重锚|reanchor", re.I),
    "hold": re.compile(r"hold", re.I),
    "gate": re.compile(r"\bgate\b", re.I),
    "fresh_capture": re.compile(r"fresh_capture", re.I),
    "trigger_queue": re.compile(r"trigger_queue", re.I),
    "watermark_intent": re.compile(r"watermark_intent", re.I),
    "bootstrap": re.compile(r"bootstrap", re.I),
    "一致性/一致": re.compile(r"一致性|一致"),
    "校验窗/窗口": re.compile(r"窗口|window", re.I),
    "db_checkpoint": re.compile(r"db_checkpoint", re.I),
    "周期": re.compile(r"周期"),
}

files = sorted(glob.glob("data/logs/*.log*"), key=os.path.getsize, reverse=True)
print(f"[扫描] 共 {len(files)} 个日志文件\n")
for f in files[:10]:
    try:
        txt = open(f, encoding="utf-8", errors="replace").read()
    except Exception as e:
        print(f"{f}: 读取失败 {e}")
        continue
    hits = {k: len(p.findall(txt)) for k, p in MARKERS.items()}
    hits = {k: v for k, v in hits.items() if v}
    print(f"=== {f} ({os.path.getsize(f)/1e6:.2f} MB, {txt.count(chr(10))} 行) ===")
    print("    " + " | ".join(f"{k}={v}" for k, v in hits.items()))
    # 抽样：最可能标识环的行
    for key in ("重锚/reanchor", "回放/replay", "分片/shard", "hold", "gate"):
        p = MARKERS[key]
        samples = [ln.strip() for ln in txt.splitlines() if p.search(ln)][:3]
        for s in samples:
            print(f"      [{key}] {s[:150]}")
    print()
