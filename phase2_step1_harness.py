#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase-2 Step 1 harness — C0/C1/C2 熊市回测定案。

隔离环境：本文件位于 worktree `qs-phase2-step1`（基于 172984e），
sys.path[0]=worktree 根，故导入的是 172984e 框架，与当前脏树完全隔离。

策略基线：sw_industry_etf_rotation_8f（quantstudio 版），从当前树作为未跟踪文件
拷入 worktree（172984e 不含该策略，属用户 Phase-2 未提交工作）。

C0 = 纯基准（不动：≥1 触发 + baseline S2 + RD=5）
C1 = band0.97 + RD=2 + ≥2 触发
C2 = band0.97 + RD=3 + ≥2 触发

控制组：first_board_pullback_daily__candidate（172984e 自带），2023 窗口，
应复现黄金 nav 94686.50607832 / 2 笔。

本 harness 不执行任何 git 写操作；仅生成变体 .py（未跟踪）+ 读取 DB（只读）。
"""
import json
import os
import sys
import importlib
import datetime as _dt

ROOT = r"D:/miniQMT策略实盘/qs-phase2-step1"
DB = r"D:/miniQMT策略实盘/QuantStudio/data/quantstudio.db"
BASE = os.path.join(ROOT, "quantstudio/backtest/strategies/sw_industry_etf_rotation_8f__candidate_quantstudio.py")
FIRST_BOARD = os.path.join(ROOT, "quantstudio/backtest/strategies/first_board_pullback_daily__candidate_quantstudio.py")
VARIANT_DIR = os.path.join(ROOT, "phase2_variants")
RESULT_JSON = os.path.join(ROOT, "phase2_step1_results.json")

FULL_START, FULL_END = "2021-07-01", "2026-07-29"   # 全窗口（与探针一致）
CTRL_START, CTRL_END = "2023-01-01", "2023-12-31"   # 控制组黄金窗口

sys.path.insert(0, ROOT)
from quantstudio.backtest.backtest_engine import BacktestEngine  # noqa: E402
from quantstudio.backtest.strategy_runner import load_strategy   # noqa: E402


def _read_base():
    with open(BASE, encoding="utf-8") as f:
        return f.read()


def make_variant(text, name, recovery_days, s2_band, trigger_ge2):
    """纯字符串改写生成变体 .py（未跟踪文件，落盘待用户确认）。"""
    t = text
    if recovery_days is not None:
        t = t.replace("RECOVERY_DAYS = 5", "RECOVERY_DAYS = %d" % recovery_days)
    if s2_band is not None:
        t = t.replace(
            "return float(close[-1]) < float(np.mean(close[-200:]))",
            "return float(close[-1]) < float(np.mean(close[-200:])) * %s" % repr(s2_band),
        )
    if trigger_ge2:
        t = t.replace(
            "any_signal = s1 or s2 or s3",
            "bear_count = int(s1) + int(s2) + int(s3)\n    any_signal = bear_count >= 2",
        )
    t = t.replace("STRATEGY_ID = ", "# VARIANT %s\nSTRATEGY_ID = " % name, 1)
    os.makedirs(VARIANT_DIR, exist_ok=True)
    path = os.path.join(VARIANT_DIR, "sw_8f_%s.py" % name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(t)
    return path


def _attach_recorder(mod):
    """包装 _log_portfolio_audit 记录每日 gross_exposure / bear_mode / any_signal。
    不改任何策略逻辑，仅观测。bear_mode 与 any_signal 取自 mod.g（引擎注入）。"""
    diag = []
    orig = getattr(mod, "_log_portfolio_audit", None)
    if orig is None:
        # 控制组等策略没有该钩子：不注入 recorder（纯观测缺省，不影响回测）
        mod._DIAG = diag
        return diag

    def wrapper(context, rebalance_id, today_str):
        orig(context, rebalance_id, today_str)
        tv = context.portfolio.total_value
        cash = getattr(context.portfolio, "cash", 0.0)
        ge = (1.0 - cash / tv) if tv > 0 else 1.0
        bm = bool(getattr(mod.g, "bear_mode", False))
        sig = mod.g.last_signal_tuple if hasattr(mod.g, "last_signal_tuple") else None
        any_sig = bool(sig[0] or sig[1] or sig[2]) if sig else bm
        diag.append((today_str, ge, bm, any_sig))

    mod._log_portfolio_audit = wrapper
    mod._DIAG = diag
    return diag


def run_backtest(path, start, end):
    functions, mod = load_strategy(path)
    diag = _attach_recorder(mod)
    engine = BacktestEngine(
        db_path=DB, strategy=functions, start=start, end=end,
        capital=100_000, match_price_mode="next_open",
        engine_profile="daily-bar-v1",
    )
    result, _ = engine.run()
    return result, diag


def _parse_date(s):
    return _dt.datetime.strptime(s, "%Y-%m-%d").date()


def compute_metrics(result, diag):
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
        amt = t.get("volume")
        px = t.get("price")
        if amt is not None and px is not None:
            total_notional += abs(float(amt) * float(px))
    # 若引擎未给 commission/slippage 字段，用费率估算
    if commission == 0.0 and total_notional > 0:
        commission = sum(max(0.00025 * abs(float(t.get("volume", 0)) * float(t.get("price", 0))), 5.0)
                         for t in trades)
    if slippage == 0.0 and total_notional > 0:
        slippage = sum(0.001 * abs(float(t.get("volume", 0)) * float(t.get("price", 0)))
                       for t in trades)
    ges = [d[1] for d in diag]
    bms = [d[2] for d in diag]
    any_sigs = [d[3] for d in diag]
    # 占用率 = bear_mode 为 True 的交易日占比（即 ≥1 触发触发的半仓状态机开启天数）
    occ_days = sum(1 for b in bms if b)
    occupancy = occ_days / len(bms) if bms else 0.0
    # 翻转次数 = bear_mode 状态切换次数（0.98↔0.49 整仓调仓）
    flips = 0
    for i in range(1, len(bms)):
        if bms[i - 1] != bms[i]:
            flips += 1
    avg_ge = sum(ges) / len(ges) if ges else 1.0
    any_days = sum(1 for a in any_sigs if a)
    any_occ = any_days / len(any_sigs) if any_sigs else 0.0
    avg_tv = sum(navs) / n if n else init
    daily_turnover = (total_notional / n) / avg_tv if n else 0.0
    return {
        "n_nav": n,
        "final_nav": round(final, 6),
        "total_return_pct": round(total_ret * 100, 4),
        "annualized_pct": round(ann * 100, 4),
        "max_drawdown_pct": round(mdd * 100, 4),
        "ann_vol_pct": round(vol * 100, 4),
        "sharpe": round(sharpe, 4),
        "n_trades": n_trades,
        "total_notional": round(total_notional, 2),
        "est_commission": round(commission, 2),
        "est_slippage": round(slippage, 2),
        "est_friction": round(commission + slippage, 2),
        "daily_turnover": round(daily_turnover, 6),
        "occupancy_pct": round(occupancy * 100, 2),
        "any_signal_occupancy_pct": round(any_occ * 100, 2),
        "avg_gross_exposure_pct": round(avg_ge * 100, 2),
        "bear_flips": flips,
        "trade_keys": trade_keys,
        "engine_metrics_summary": {k: (round(float(v), 6) if isinstance(v, (int, float)) else v)
                                   for k, v in (result.metrics_summary or {}).items()},
    }


def run_variant(name, path, start, end):
    print("\n===== [%s] %s ~ %s =====" % (name, start, end), flush=True)
    result, diag = run_backtest(path, start, end)
    m = compute_metrics(result, diag)
    print("[%s] final_nav=%.6f total_ret=%.4f%% ann=%.4f%% mdd=%.4f%% sharpe=%.4f"
          % (name, m["final_nav"], m["total_return_pct"], m["annualized_pct"],
             m["max_drawdown_pct"], m["sharpe"]))
    print("[%s] trades=%d friction=%.2f occ=%.2f%% avg_ge=%.2f%% bear_flips=%d"
          % (name, m["n_trades"], m["est_friction"], m["occupancy_pct"],
             m["avg_gross_exposure_pct"], m["bear_flips"]))
    print("[%s] trade_keys=%s" % (name, m["trade_keys"]))
    return m


def main():
    base_text = _read_base()
    variants = {
        "C0": make_variant(base_text, "C0", None, None, False),
        "C1": make_variant(base_text, "C1", 2, 0.97, True),
        "C2": make_variant(base_text, "C2", 3, 0.97, True),
    }
    out = {"variants": {}, "control": {}}
    for name in ("C0", "C1", "C2"):
        out["variants"][name] = run_variant(name, variants[name], FULL_START, FULL_END)

    # 控制组 first_board（172984e 自带策略）
    print("\n===== [CTRL] first_board %s ~ %s =====" % (CTRL_START, CTRL_END), flush=True)
    cr, cdiag = run_backtest(FIRST_BOARD, CTRL_START, CTRL_END)
    cm = compute_metrics(cr, cdiag)
    out["control"] = cm
    print("[CTRL] final_nav=%.10f (golden=94686.50607832) n_trades=%d (golden=2)"
          % (cm["final_nav"], cm["n_trades"]))
    match = (abs(cm["final_nav"] - 94686.50607832) < 1e-6) and (cm["n_trades"] == 2)
    out["control"]["golden_match"] = bool(match)
    print("[CTRL] GOLDEN MATCH =", match)

    with open(RESULT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n[RESULT] written ->", RESULT_JSON)

    # 摘要表
    print("\n================ 摘要表 ================")
    hdr = "%-4s %14s %10s %9s %9s %9s %7s %7s %9s %9s %7s" % (
        "id", "final_nav", "tot_ret%", "ann%", "mdd%", "sharpe", "trades", "fric", "occ%", "avg_ge%", "flips")
    print(hdr)
    for name in ("C0", "C1", "C2"):
        m = out["variants"][name]
        print("%-4s %14.6f %10.4f %9.4f %9.4f %9.4f %7d %7.1f %9.2f %9.2f %7d" % (
            name, m["final_nav"], m["total_return_pct"], m["annualized_pct"],
            m["max_drawdown_pct"], m["sharpe"], m["n_trades"], m["est_friction"],
            m["occupancy_pct"], m["avg_gross_exposure_pct"], m["bear_flips"]))
    print("----------------------------------------")
    # 增量 vs C0
    c0 = out["variants"]["C0"]
    for name in ("C1", "C2"):
        m = out["variants"][name]
        print("%s - C0: tot_ret %+.4fpp  ann %+.4fpp  half %+.2fpp  flips %+d  trades %+d  fric %+.1f"
              % (name, m["total_return_pct"] - c0["total_return_pct"],
                 m["annualized_pct"] - c0["annualized_pct"],
                 m["half_position_ratio"] - c0["half_position_ratio"],
                 m["bear_flips"] - c0["bear_flips"],
                 m["n_trades"] - c0["n_trades"],
                 m["est_friction"] - c0["est_friction"]))


if __name__ == "__main__":
    main()
