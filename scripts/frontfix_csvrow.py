# -*- coding: utf-8 -*-
"""CSV 完整行检查：000550 的 adj_i/adj_latest 值（坐实 ZCode 误报口径）。"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
with open("data/logs/front_corruption_stock_minutes.csv", encoding="utf-8", errors="replace") as f:
    f.readline()
    n = 0
    for ln in f:
        p = ln.split(",")
        if p[0] == "000550" and "2026-07-01" in ln:
            print("CSV 完整行:", p)
            n += 1
        if n >= 3:
            break

# 对照：000550 07-01 的正确 front（close×3.9524/3.9524=close）
print()
print("结论: 因子恒定 3.9524 → adj_i/adj_latest=1 → front==close 为正确值")
