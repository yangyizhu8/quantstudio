#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""铁律归档文件级完整性轻校验（KMAP-B2，2026-10-25 ②审照准）

设计边界（写死，防「第二套假一致」）：
  - 只做**文件级完整性**：25 条归档存在性 + 首行标题 + 非空壳 + 索引表一致 + 指针可解析
  - **不**套条目 schema（铁律全文不是「坑条目」，硬套会造第二套假一致并被判违规）
  - 判据来源：AGENTS.md 铁律索引表（`| NN | 名称 | 触发 | 档 | 完整版路径 |`）

用法：
  python scripts/check_iron_rules_archive.py            # 人读
  python scripts/check_iron_rules_archive.py --json     # 机读
  python scripts/check_iron_rules_archive.py --strict   # 有 FAIL 时 exit 1（默认 exit 1 同样，保留语义）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_BYTES = 200
ROW = re.compile(r"^\|\s*(\d{2})\s*\|(.+?)\|(.+?)\|(.+?)\|\s*(\S+)\s*\|\s*$")


def read(p):
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def parse_index(agents_text):
    rows = {}
    for line in agents_text.splitlines():
        m = ROW.match(line.strip())
        if not m:
            continue
        num, name, trig, tier, path = m.groups()
        rows[num] = {"name": name.strip(), "tier": tier.strip(), "path": path.strip()}
    return rows


def main():
    ap = argparse.ArgumentParser(description="铁律归档文件级完整性轻校验")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--agents", default=os.path.join(ROOT, "AGENTS.md"))
    ap.add_argument("--archive-dir", default=os.path.join(ROOT, "knowledge", "iron-rules"))
    args = ap.parse_args()

    fails, warns, oks = [], [], []
    agents = read(args.agents) if os.path.exists(args.agents) else ""
    index = parse_index(agents)

    files = {os.path.basename(p): p for p in glob.glob(os.path.join(args.archive_dir, "*.md"))}
    nums = sorted(k for k in index)

    # 1) 索引表条数
    if len(index) != 25:
        fails.append("索引表条数=%d（期望 25）" % len(index))

    # 2) 每条：归档存在 / 首行标题 / 非空壳 / 路径指针可解析
    for num in nums:
        rec = index[num]
        ptr = rec["path"]
        target = os.path.join(ROOT, ptr.replace("/", os.sep))
        if not os.path.exists(target):
            fails.append("#%s 索引指针不存在：%s" % (num, ptr))
            continue
        size = os.path.getsize(target)
        if size < MIN_BYTES:
            fails.append("#%s 归档过小（%dB < %dB）：%s" % (num, size, MIN_BYTES, ptr))
            continue
        first = read(target).lstrip("\ufeff").splitlines()[0] if read(target).strip() else ""
        if not first.startswith("## 【铁律】"):
            fails.append("#%s 首行非「## 【铁律】」：%s" % (num, first[:40]))
            continue
        oks.append("#%s %s (%dB)" % (num, os.path.basename(target), size))

    # 3) 归档目录里存在但索引未登记（脏归档）
    indexed_names = {os.path.basename(v["path"]) for v in index.values()}
    for name in sorted(set(files) - indexed_names):
        warns.append("归档未登记于索引表：%s" % name)

    # 4) 档位取值合法
    for num, rec in index.items():
        if rec["tier"] not in ("A", "B", "C"):
            fails.append("#%s 档位非法：%s" % (num, rec["tier"]))

    res = {"index_rows": len(index), "archive_files": len(files),
           "ok_count": len(oks), "warns": warns, "fails": fails,
           "result": "PASS" if not fails else "FAIL"}
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("[iron-rules] index=%d archive=%d ok=%d" % (len(index), len(files), len(oks)))
        for w in warns:
            print("  WARN ", w)
        for f in fails:
            print("  FAIL ", f)
        print("[iron-rules] RESULT=%s" % res["result"])
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
