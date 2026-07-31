#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端验收驱动：对 V1-V4 + V3b 跑 Validator（target_profile=ptrade，design 含 ptrade）。

只读取 main 的 validate_agent_strategy（只读不改），本脚本为新建验收辅助。
"""
import json
import os
import sys

SKILL_SCRIPTS = r"D:/miniQMT策略实盘/QuantStudio/skills/quantstudio-strategy-compiler/scripts"
sys.path.insert(0, SKILL_SCRIPTS)
import validate_agent_strategy as V  # noqa: E402
from agent_skill_common import load_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DESIGN = os.path.join(HERE, "design.json")
design = load_json(DESIGN)

CASES = [
    ("V1", "v1_is_dict_true.py", "PTRADE-IS-DICT-BAN"),
    ("V2", "v2_get_industry.py", "LOCAL_ONLY 相关(PTRADE-API-UNSUPPORTED/PTRADE-LOCAL-SYMBOL)"),
    ("V3", "v3_local_column.py", "PTRADE-LOCAL-COLUMN"),
    ("V4", "v4_clean.py", "PASS(无 BLOCK)"),
    ("V3b", "v3b_extract_series_canonical.py", "PASS(无 PTRADE-LOCAL-COLUMN)"),
]

print("=== 端到端验收（target_profile=ptrade, design.targets 含 ptrade → strict_ptrade 触发）===\n")
all_ok = True
for name, fn, expect in CASES:
    src = open(os.path.join(HERE, fn), encoding="utf-8").read()
    rep = V.validate_strategy(design, src, fn, "ptrade")
    blocks = [i for i in rep["issues"] if i["severity"] == "BLOCK"]
    rules = sorted({i["rule_id"] for i in blocks})
    status = rep["status"]
    print("[%s] status=%s block_count=%d rules=%s" % (name, status, rep["block_count"], rules))
    print("       expected: %s" % expect)
    if name in ("V1", "V2", "V3"):
        if status != "BLOCKED":
            all_ok = False
            print("       !! FAIL: 预期 BLOCK 但未 BLOCK")
    else:  # V4, V3b
        if status != "PASS":
            all_ok = False
            print("       !! FAIL: 预期 PASS 但未 PASS")
    print()

print("=== 汇总：", "ALL PASS ✅" if all_ok else "HAS FAILURE ❌", "===")
