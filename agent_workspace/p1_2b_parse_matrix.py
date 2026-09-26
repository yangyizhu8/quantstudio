# -*- coding: utf-8 -*-
"""P1-2b 复现矩阵（先红）：`_parse_flexible_date` 边界行为 + `_export_batches` 窗口退化。

目的（方案 7c6f36a 的强制前置）：
  ① 给出解析器在边界输入下的**实际返回值矩阵**（不再靠推测）；
  ② 判定「ckey 退化为 epoch（1970-01-01|1970-01-02）」的**触发条件**（三假设：
     A 空/None 回落 epoch；B 解析失败静默回落；C 10/13 位数字被 [:10] 截断）。
只读；不写任何库/文件。
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\miniQMT策略实盘\QuantStudio")

from quantstudio.pipeline.sources.mcp_adapter import MCPAdapter  # noqa: E402

CASES = [
    ("", "空串"),
    (None, "None"),
    ("0", "字符串 0"),
    (0, "整数 0"),
    ("1970-01-01", "epoch 日"),
    ("1970-01-02", "epoch 次日"),
    ("2026-09-25", "合法日期"),
    ("2026-9-25", "合法日期（非补零）"),
    ("1789488000", "10 位秒 epoch"),
    ("1789488000000", "13 位毫秒 epoch"),
    ("9999999999", "10 位越界秒"),
    ("not-a-date", "垃圾串"),
    (" 2026-09-25 ", "带空白"),
]

print("=== [1] _parse_flexible_date 边界矩阵 ===")
print(f"{'输入':>18} | {'说明':<18} | 返回")
print("-" * 78)
results = {}
for val, label in CASES:
    try:
        out = MCPAdapter._parse_flexible_date(str(val).strip()[:10] if val is not None else "")
        results[label] = out
        print(f"{str(val)[:18]:>18} | {label:<18} | {out!r}")
    except Exception as e:  # noqa: BLE001
        results[label] = f"EXC {type(e).__name__}: {e}"
        print(f"{str(val)[:18]:>18} | {label:<18} | EXC {type(e).__name__}: {e}")

print("\n=== [2] _export_batches 退化窗口判定（先红核心）===")
stub = object.__new__(MCPAdapter)          # 仅调静态窗口逻辑，绕开网络初始化
for start, end, is_min, label in [
    ("", "", True, "空 start/end（分钟）"),
    ("1970-01-01", "1970-01-02", True, "epoch 日窗（分钟）"),
    ("1970-01-01", "1970-01-02", False, "epoch 日窗（日线）"),
    ("2026-09-20", "2026-09-25", True, "合法窗（分钟）"),
]:
    try:
        batches = stub._export_batches(start, end, is_min, est_rows=None,
                                       grid_aligned=True, table="etf_minutes")
        keys = [f"{b[0]}|{b[1]}" for b in batches]
        flag = "**EPOCH 退化**" if any(k.startswith("1970") for k in keys) else "正常"
        print(f"  {label:<22} → {len(batches)} 批: {keys[:4]}{' …' if len(keys) > 4 else ''}  [{flag}]")
    except Exception as e:  # noqa: BLE001
        print(f"  {label:<22} → EXC {type(e).__name__}: {e}")

print("\n=== [3] 判定 ===")
epoch_hits = [k for k, v in results.items() if "1970" in str(v)]
print(f"  回落到 1970 的输入: {epoch_hits or '无'}")
print("  （据此判定假设 A/B/C 哪条成立；矩阵为『先红』证据，修复后应全为显式拒绝/默认值）")
