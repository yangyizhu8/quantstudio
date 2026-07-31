#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C1 全窗口重跑（harness amount->volume 键名修复后），仅验证度量修复不改动回测行为。

隔离：worktree qs-phase2-step1 根，复用 harness 的 run_backtest/compute_metrics（172984e 框架）。
只跑 C1（不跑 CTRL，避免 first_board 慢路径）。输出 phase2_step1_C1_rerun_fixed.json。
"""
import json
import os
import sys

ROOT = r"D:/miniQMT策略实盘/qs-phase2-step1"
sys.path.insert(0, ROOT)
import phase2_step1_harness as H  # noqa: E402


def main():
    base = H._read_base()
    path = H.make_variant(base, "C1", 2, 0.97, True)
    result, diag = H.run_backtest(path, H.FULL_START, H.FULL_END)
    m = H.compute_metrics(result, diag)

    out = os.path.join(ROOT, "phase2_step1_C1_rerun_fixed.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)

    # 与修复前基线对照
    try:
        with open(os.path.join(ROOT, "phase2_step1_C1.json"), encoding="utf-8") as f:
            base_m = json.load(f)["C1"]
    except Exception:
        base_m = {}
    keys = ["final_nav", "total_return_pct", "max_drawdown_pct", "n_trades",
            "occupancy_pct", "total_notional", "est_commission", "est_slippage"]
    print("\n===== C1 修复前后对照（键名 amount->volume）=====")
    print("%-18s %18s %18s %s" % ("metric", "before(bug)", "after(fix)", "unchanged?"))
    for k in keys:
        b = base_m.get(k)
        a = m.get(k)
        same = "SAME" if b == a else "CHANGED"
        print("%-18s %18s %18s %s" % (k, str(b), str(a), same))
    print("\n[OUT]", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
