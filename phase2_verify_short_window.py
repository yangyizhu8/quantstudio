#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""短窗口 C1 实测对照（harness amount->volume 修复验证）。

做法：单次引擎跑（短窗口），对【同一个真实 result】分别用
  - 修复前逻辑 old_compute_metrics（键名 amount，与修复后唯一差异 3 处）
  - 修复后逻辑 H.compute_metrics（键名 volume）
计算度量，打印 before/after 对照。

证明：
  - 核心指标(final_nav/return/MDD/n_trades/occupancy)来自真实 nav_history/trade_records/diag，
    两种逻辑下完全一致 => SAME（实测，非理论）。
  - est_slippage/total_notional：旧逻辑读不到 amount 键(trade_keys 实证无此键) => 0；
    新逻辑读 volume => 真实 >0 => CHANGED。
  - est_commission：引擎自带 commission 字段，两种逻辑一致 => SAME(4355)。

隔离：worktree qs-phase2-step1 根，复用 harness 的 run_backtest/make_variant/compute_metrics（172984e 框架）。
输出 phase2_step1_C1_short_fixed.json。
"""
import json
import os
import sys
from datetime import datetime

ROOT = r"D:/miniQMT策略实盘/qs-phase2-step1"
sys.path.insert(0, ROOT)
import phase2_step1_harness as H  # noqa: E402


def _parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d")


def old_compute_metrics(result, diag):
    """修复前逻辑：与 H.compute_metrics 唯一差异是 trade-record 键名 amount（应为 volume）。"""
    nav = result.nav_history
    dates = [r["date"] for r in nav]
    navs = [float(r["nav"]) for r in nav]
    init = 100_000.0
    final = navs[-1] if navs else init
    total_ret = final / init - 1.0
    n = len(navs)
    years = 0.0
    if n >= 2:
        span = (_parse_date(dates[-1]) - _parse_date(dates[0])).days
        years = span / 365.25
    ann = (final / init) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    rets = [navs[i] / navs[i - 1] - 1.0 for i in range(1, n)] if n >= 2 else []
    vol = 0.0
    if rets:
        m = sum(r * r for r in rets) / len(rets)
        vol = m ** 0.5 * (252 ** 0.5)
    peak = navs[0] if navs else init
    mdd = 0.0
    for v in navs:
        if v > peak:
            peak = v
        dd = v / peak - 1.0
        if dd < mdd:
            mdd = dd
    sharpe = ann / vol if vol > 0 else 0.0
    trades = list(result.trade_records)
    n_trades = len(trades)
    trade_keys = list(trades[0].keys()) if trades else []
    commission = 0.0
    slippage = 0.0
    total_notional = 0.0
    for t in trades:
        c = t.get("commission")
        s = t.get("slippage")
        if c is not None:
            commission += float(c)
        if s is not None:
            slippage += float(s)
        amt = t.get("amount")  # 修复前：错误键名（trade_keys 实证无此键 => 恒 None）
        px = t.get("price")
        if amt is not None and px is not None:
            total_notional += abs(float(amt) * float(px))
    if commission == 0.0 and total_notional > 0:
        commission = sum(max(0.00025 * abs(float(t.get("amount", 0)) * float(t.get("price", 0))), 5.0)
                         for t in trades)
    if slippage == 0.0 and total_notional > 0:
        slippage = sum(0.001 * abs(float(t.get("amount", 0)) * float(t.get("price", 0)))
                       for t in trades)
    ges = [d[1] for d in diag]
    bms = [d[2] for d in diag]
    occ_days = sum(1 for b in bms if b)
    occupancy = occ_days / len(bms) if bms else 0.0
    return {
        "final_nav": round(final, 6),
        "total_return_pct": round(total_ret * 100, 4),
        "max_drawdown_pct": round(mdd * 100, 4),
        "n_trades": n_trades,
        "occupancy_pct": round(occupancy * 100, 2),
        "total_notional": round(total_notional, 2),
        "est_commission": round(commission, 2),
        "est_slippage": round(slippage, 2),
        "trade_keys": trade_keys,
    }


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2023-07-01"
    end = sys.argv[2] if len(sys.argv) > 2 else "2023-07-31"
    base = H._read_base()
    path = H.make_variant(base, "C1", 2, 0.97, True)
    result, diag = H.run_backtest(path, start, end)

    after = H.compute_metrics(result, diag)
    before = old_compute_metrics(result, diag)

    out = os.path.join(ROOT, "phase2_step1_C1_short_fixed.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"window": [start, end], "before_bug": before, "after_fix": after},
                  f, ensure_ascii=False, indent=2)

    keys = ["final_nav", "total_return_pct", "max_drawdown_pct", "n_trades",
            "occupancy_pct", "total_notional", "est_commission", "est_slippage"]
    print("\n===== C1 短窗口实测对照（%s ~ %s，键名 amount->volume）=====" % (start, end))
    print("trade_keys = %s" % before["trade_keys"])
    print("%-18s %18s %18s %s" % ("metric", "before(bug)", "after(fix)", "unchanged?"))
    for k in keys:
        b = before.get(k)
        a = after.get(k)
        same = "SAME" if b == a else "CHANGED"
        print("%-18s %18s %18s %s" % (k, str(b), str(a), same))
    print("\n[OUT]", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
