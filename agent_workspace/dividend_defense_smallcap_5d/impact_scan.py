# -*- coding: utf-8 -*-
"""影响面机械提取：18 个引用 portfolio.positions 的策略，逐个列出访问点及其邻近字段读取。"""
import os, re, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SDIR = os.path.join(ROOT, "quantstudio", "backtest", "strategies")
FIELDS = ("amount", "enable_amount", "cost_basis", "last_sale_price", "avg_cost",
          "market_value", "sid", "volume", "can_sell", "pending_sell_shares")
pat_pos = re.compile(r"portfolio\.positions")
pat_field = re.compile(r"\b(" + "|".join(FIELDS) + r")\b")
rows = []
for fn in sorted(os.listdir(SDIR)):
    if not fn.endswith(".py"):
        continue
    p = os.path.join(SDIR, fn)
    lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
    hits = [i for i, l in enumerate(lines) if pat_pos.search(l)]
    if not hits:
        continue
    detail = []
    for i in hits:
        window = "\n".join(lines[i:min(len(lines), i + 4)])
        flds = sorted({m for m in pat_field.findall(window)})
        detail.append("L%d: %s || 邻近字段=%s" % (i + 1, lines[i].strip()[:90], ",".join(flds) or "-"))
    # 函数路径读者
    funcs = [i + 1 for i, l in enumerate(lines) if re.search(r"\bget_position\(|\bget_positions\(", l)]
    fld2 = sorted({m for m in pat_field.findall("\n".join(lines))})
    rows.append({"file": fn, "pos_lines": [i + 1 for i in hits], "detail": detail,
                 "func_lines": funcs[:8], "all_fields": fld2})

print("文件数:", len(rows))
for r in rows:
    print("\n### " + r["file"])
    for d in r["detail"]:
        print("   " + d)
    print("   函数路径访问行:", r["func_lines"] or "-")
    print("   全文字段命中:", ",".join(r["all_fields"]) or "-")
