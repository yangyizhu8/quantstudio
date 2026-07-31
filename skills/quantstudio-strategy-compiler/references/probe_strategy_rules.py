#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A1 — 回测前历史探测脚本（双目标裁决前置）——【日频 v3：RECOVERY_DAYS × S2变体 二维扫描】

直接 import 候选策略 sw_industry_etf_rotation_8f__candidate_quantstudio.py 的
_bear_signal_s1 / _bear_signal_s2 / _bear_signal_s3（**勿重写规则，防漂移**），
对 2021-07 ~ 2026-07 每个交易日用真实 quantstudio.db 数据驱动。

【v3 新增（Phase-2 Step 0 任务书）】
  固定 S3_BEAR_FRAC = 0.05（下界定理：裸 cnt≥2@0.05=43.3% 为 RECOVERY_DAYS→0
  理论下界；日频已证调高 S3 阈值方向错误——越高占用越糟）。
  扫描维度 = RECOVERY_DAYS ∈ {0,1,2,3,5} × S2变体 ∈
  {baseline, band0.99, band0.98, band0.97, slope_down}，另叠加 monthly_cap ∈
  {∞,10,15,20}（状态机层正交约束）。每格输出 occ_cnt2 + 相邻日状态翻转次数
  （碎单/换手摩擦代理——低占用高翻转 = 碎单反噬，不可选）。

【严格约束】
  1. 基线 S2 零漂移：baseline 格仍走 mod._bear_signal_s2()（候选策略源码），
     不碰候选策略文件。
  2. 变体在探针本地实现（复用已切好的 idx_win），绝不改策略 _bear_signal_s2。
  3. include 口径：探针桩把当日计入窗口（含当日）；变体函数显式同口径
     （全部含当日，与 baseline 桩自洽）。策略源码写 include=False，真实引擎
     下窗口截止上一交易日——本扫描内部口径统一，横向比较有效；绝对水平与
     真实引擎可能有 1 日相位差，Step 1 定案时以黄金对照回测为准。
  4. 基线复现验收：格 (baseline, RECOVERY_DAYS=5, cap=∞) 必须复现日频 v2
     occ_cnt2=59.3%（读取旧 result.json 逐位核对），不一致即停（污染基线）。

自标定输出（HISTORICAL_PROBE）：
  目标带：occ_cnt2 ≤ 40%（翻转次数仅作描述性指标，不用于自动 PASS/BLOCK）
  改动量排序：baseline < band0.99 < band0.98 < band0.97 < slope_down < monthly_cap
  同改动内 RECOVERY_DAYS 越大越好（结合翻转次数）
  只给推荐排序，不自行启动 Step 1。

双端一致性：只读 quantstudio.db，不依赖框架注入；与候选策略规则零漂移。
输出：stdout 全量矩阵 + references/probe_strategy_rules_result.json。
"""
from __future__ import annotations

import importlib.util
import datetime
import json
import os
import sys

import numpy as np
import duckdb

DB = r"D:/miniQMT策略实盘/QuantStudio/data/quantstudio.db"
CAND = (r"D:/miniQMT策略实盘/QuantStudio/quantstudio/backtest/strategies/"
        r"sw_industry_etf_rotation_8f__candidate_quantstudio.py")
BENCH = "000300.SS"
# 与候选 g.etf_pool 完全一致的 25 只白名单（防漂移）
WL = [
    "159865.SZ", "159870.SZ", "515210.SS", "512400.SS", "512480.SS",
    "159996.SZ", "512690.SS", "512010.SS", "159611.SZ", "516910.SS",
    "512200.SS", "159766.SZ", "516970.SS", "515790.SS", "512660.SS",
    "159998.SZ", "512980.SS", "515050.SS", "512800.SS", "512880.SS",
    "515030.SS", "562500.SS", "515220.SS", "159697.SZ", "512580.SS",
]
START = datetime.date(2021, 7, 1)
END = datetime.date(2026, 7, 1)
MIN_BARS = 250          # 每只 ETF 取最近 N 根（覆盖 s1>=25 / s3>=60）
IDX_BARS = 202          # 沪深300 取最近 N 根（202：slope_down 需昨日 MA200；
                        # 桩按 count 截尾，基线 mod._bear_signal_s2() 只取 201 不受影响）
S3_FIXED = 0.05         # 固定（放弃调 S3 阈值方向）
SCAN_THRESHOLDS = [0.05, 0.08, 0.10, 0.12, 0.15]   # 仅保留 v2 对照表用
TARGET_OCC = 0.40
RECOVERY_SCAN = [0, 1, 2, 3, 5]
BASELINE_RECOVERY = 5
MONTHLY_CAPS = [10, 15, 20]
# FLIP_TOLERANCE 硬门禁已移除（clean 要求）：翻转次数仅作描述性指标输出，不再用于自动过滤推荐。

# S2 变体（改动量升序；baseline 走候选策略源码）
S2_VARIANTS = ["baseline", "band0.99", "band0.98", "band0.97", "slope_down"]
VARIANT_DESC = {
    "baseline":  "close < MA200（候选源码 _bear_signal_s2，零漂移）",
    "band0.99":  "close < MA200×0.99（1% 缓冲带）",
    "band0.98":  "close < MA200×0.98（2% 缓冲带）",
    "band0.97":  "close < MA200×0.97（3% 缓冲带）",
    "slope_down": "close < MA200 且 MA200 本身在降（MA200_t < MA200_{t-1}）",
}
CHANGE_ORDER = {"baseline": 0, "band0.99": 1, "band0.98": 2, "band0.97": 3,
                "slope_down": 4}   # monthly_cap 追加视为 5


# ---------------------------------------------------------------------------
# 桩：让候选模块在无引擎环境下 exec
# ---------------------------------------------------------------------------
class _G:
    benchmark = BENCH
    etf_pool = WL
    bear_mode = False
    recovery_counter = 0
    min_bars = MIN_BARS


def _log(*a, **k):
    return None


def _run_daily(*a, **k):
    return None


def _noop(*a, **k):
    return None


def _make_history_stub(cur_index_close):
    """返回 get_history 桩：仅 S2（沪深300）使用，返回 {security: recarray(close)}。
    口径：窗口**含当日**（include 参数被忽略）——v3 变体与此口径显式对齐。"""
    import numpy as _np
    arr = _np.rec.fromrecords([(float(x),) for x in cur_index_close], names=["close"])

    def _get_history(count, frequency="1d", field=None, security_list=None,
                      fq="pre", include=False, is_dict=True):
        c = int(count)
        take = arr["close"][-c:] if len(arr["close"]) >= c else arr["close"]
        sub = _np.rec.fromrecords([(float(x),) for x in take], names=["close"])
        return {security_list: sub}

    return _get_history


def _load_candidate():
    """exec 候选模块（注入桩），返回模块对象。规则零漂移（仅桩替换引擎 API）。"""
    ns = {
        "np": np,
        "pd": _noop,  # 候选若 import pandas 也容错（实际未用）
        "datetime": datetime,
        "_uuid": __import__("uuid"),
        "g": _G(),
        "log": _log,
        "run_daily": _run_daily,
        "get_history": _make_history_stub(np.array([1.0])),
        "get_positions": lambda *a, **k: {},
        "get_position": lambda *a, **k: None,
        "get_stock_status": lambda *a, **k: {},
        "order_target_value": lambda *a, **k: None,
        "set_benchmark": lambda *a, **k: None,
        "set_commission": lambda *a, **k: None,
        "set_slippage": lambda *a, **k: None,
        "set_limit_mode": lambda *a, **k: None,
        "get_trade_days": lambda *a, **k: [],
        "context": type("C", (), {"current_dt": datetime.datetime.now(),
                                  "portfolio": type("P", (), {"total_value": 100000})()})(),
    }
    spec = importlib.util.spec_from_file_location("cand8f_probe", CAND)
    mod = importlib.util.module_from_spec(spec)
    for k, v in ns.items():
        setattr(mod, k, v)
    # numpy 兼容版 _extract_series（工具函数，非规则；仅影响探测运行时）
    def _extract_series_np(item, *names):
        for n in names:
            v = None
            if isinstance(item, dict):
                v = item.get(n)
            else:
                v = getattr(item, n, None)
                if v is None:
                    try:
                        v = item[n]
                    except Exception:
                        v = None
            if v is not None:
                return np.asarray(v, dtype=float)
        return np.asarray(item, dtype=float) if item is not None else np.array([])

    spec.loader.exec_module(mod)
    mod._extract_series = _extract_series_np  # 覆盖模块内工具函数（不影响规则）
    return mod


# ---------------------------------------------------------------------------
# 数据：一次性预载全量，再按交易日切片（日频 1200+ 天避免逐日查库）
# ---------------------------------------------------------------------------
def _preload_etf(con, code):
    bare = code.split(".")[0]
    rows = con.execute(
        "select time, close_front, volume, amount from etf_daily "
        "where code=? order by time", [bare]).fetchall()
    if not rows:
        return None
    return (
        np.array([r[0] for r in rows], dtype=np.int64),
        np.array([r[1] for r in rows], dtype=float),
        np.array([r[2] for r in rows], dtype=float),
        np.array([r[3] for r in rows], dtype=float),
    )


def _preload_index(con):
    rows = con.execute(
        "select time, close from index_daily where code='000300' order by time"
    ).fetchall()
    return (np.array([r[0] for r in rows], dtype=np.int64),
            np.array([r[1] for r in rows], dtype=float))


# ---------------------------------------------------------------------------
# S2 变体（探针本地实现，输入=已切好的 idx_win，口径=含当日，与桩自洽）
# ---------------------------------------------------------------------------
def _s2_variant(idx_win, variant):
    """band*/slope_down 变体。baseline 不在此处（走 mod._bear_signal_s2 零漂移）。"""
    w = np.asarray(idx_win, dtype=float)
    if len(w) < 200:
        return False
    c = float(w[-1])
    ma200 = float(np.mean(w[-200:]))
    if variant == "band0.99":
        return c < ma200 * 0.99
    if variant == "band0.98":
        return c < ma200 * 0.98
    if variant == "band0.97":
        return c < ma200 * 0.97
    if variant == "slope_down":
        if len(w) < 201:
            return False
        ma200_prev = float(np.mean(w[-201:-1]))
        return (c < ma200) and (ma200 < ma200_prev)
    raise ValueError(f"unknown variant {variant}")


# ---------------------------------------------------------------------------
# 状态机（参数化 RECOVERY_DAYS + 可选月度占用上限 + 翻转次数统计）
# ---------------------------------------------------------------------------
def _simulate(flags, dates, recovery_days, monthly_cap=None):
    """返回 (occupancy, flips)。
    - recovery_days=k：触发→半仓；连续 k 个无信号交易日→退出（k=0 即触发即退，
      占用率恰等于裸触发频率——下界定理的机械化）。
    - monthly_cap=c：单月 bear_mode 占用天数达 c 时强制退出并封锁至月末
      （次月需新触发才能再进入）。
    - flips：相邻日 bear_mode 状态翻转次数（进+出都计，碎单摩擦代理）。"""
    bear = False
    recovery = 0
    occupied = 0
    flips = 0
    prev = False
    month_used = {}
    blocked_month = None
    for sig, d in zip(flags, dates):
        mk = d[:7]
        if monthly_cap is not None and blocked_month == mk:
            bear = False
        else:
            if sig:
                bear = True
                recovery = 0
            elif bear:
                recovery += 1
                if recovery >= recovery_days:
                    bear = False
            if bear and monthly_cap is not None:
                if month_used.get(mk, 0) >= monthly_cap:
                    bear = False
                    blocked_month = mk
        if bear:
            month_used[mk] = month_used.get(mk, 0) + 1
            occupied += 1
        if bear != prev:
            flips += 1
        prev = bear
    n = len(flags)
    return (occupied / n if n else 0.0), flips


def main():
    mod = _load_candidate()
    con = duckdb.connect(DB, read_only=True)

    # ---- 预载 ----
    etf_data = {}
    for c in WL:
        h = _preload_etf(con, c)
        if h is not None:
            etf_data[c] = h
    idx_t, idx_c = _preload_index(con)

    start_ts = int(datetime.datetime(START.year, START.month, START.day).timestamp() * 1000)
    end_ts = int(datetime.datetime(END.year, END.month, END.day, 23, 59).timestamp() * 1000)
    day_mask = (idx_t >= start_ts) & (idx_t <= end_ts)
    trade_ts = idx_t[day_mask]
    print(f"[PROBE] 日频交易日样本数 N = {len(trade_ts)}（窗口 {START}~{END}）")

    samples = []
    for ts in trade_ts:
        # 指数窗口（含当日）
        pos = int(np.searchsorted(idx_t, ts, side="right"))
        idx_win = idx_c[max(0, pos - IDX_BARS):pos]
        mod.get_history = _make_history_stub(idx_win)

        valid = {}
        for c, (t_arr, cl, vol, amt) in etf_data.items():
            j = int(np.searchsorted(t_arr, ts, side="right"))
            if j >= MIN_BARS:
                valid[c] = (cl[j - MIN_BARS:j], vol[j - MIN_BARS:j], amt[j - MIN_BARS:j])
        codes = list(valid.keys())

        s1 = bool(mod._bear_signal_s1(valid, codes))
        s2 = bool(mod._bear_signal_s2())          # baseline：候选源码，零漂移
        s3_default = bool(mod._bear_signal_s3(codes, valid))  # 默认 0.05
        nh = [mod._f6_newhigh(valid[c][0]) for c in codes]
        s3_frac = float(np.mean(nh)) if codes else 0.0
        s2v = {v: bool(_s2_variant(idx_win, v)) for v in S2_VARIANTS if v != "baseline"}
        s2v["baseline"] = s2
        d = datetime.datetime.fromtimestamp(ts / 1000).date()
        samples.append({
            "date": d.isoformat(),
            "n_valid": len(codes),
            "s1": s1, "s2": s2, "s3_default": s3_default,
            "s3_frac": s3_frac,
            "s2v": s2v,
        })

    N = len(samples)
    dates = [r["date"] for r in samples]

    # ---- 2. S2×S3 联合分布（S3=0.05，日频）----
    m = {"S2=0,S3=0": 0, "S2=0,S3=1": 0, "S2=1,S3=0": 0, "S2=1,S3=1": 0}
    for r in samples:
        key = f"S2={1 if r['s2'] else 0},S3={1 if r['s3_default'] else 0}"
        m[key] += 1
    p_s2_and_s3 = m["S2=1,S3=1"] / N if N else 0.0
    print("\n=== 2. S2×S3 联合分布矩阵（日频，S3=0.05）===")
    for k in ["S2=0,S3=0", "S2=0,S3=1", "S2=1,S3=0", "S2=1,S3=1"]:
        print(f"  {k}: {m[k]}  ({100*m[k]/N:.1f}%)")
    print(f"  日频 P(S2∧S3) = {p_s2_and_s3:.4f}")

    # ---- 3. v2 对照：S3 阈值扫描（RECOVERY_DAYS=5，baseline S2）----
    print("\n=== 3. v2 对照：S3 阈值扫描（RECOVERY_DAYS=5, baseline S2）===")
    print(f"  {'thr':>6} {'裸OR>=1':>9} {'裸cnt>=2':>9} {'occ_OR':>8} {'occ_cnt2':>9}")
    scan_rows = []
    for thr in SCAN_THRESHOLDS:
        or_flags, cnt2_flags = [], []
        for r in samples:
            s3b = r["s3_frac"] < thr
            or_flags.append(r["s1"] or r["s2"] or s3b)
            cnt2_flags.append((int(r["s1"]) + int(r["s2"]) + int(s3b)) >= 2)
        p_or = sum(or_flags) / N
        p_cnt2 = sum(cnt2_flags) / N
        occ_or, _ = _simulate(or_flags, dates, BASELINE_RECOVERY)
        occ_cnt2, _ = _simulate(cnt2_flags, dates, BASELINE_RECOVERY)
        scan_rows.append({"thr": thr, "p_or": p_or, "p_cnt2": p_cnt2,
                          "real_occ_or": occ_or, "real_occ_cnt2": occ_cnt2})
        print(f"  {thr:>6.2f} {100*p_or:>8.1f}% {100*p_cnt2:>8.1f}% "
              f"{100*occ_or:>7.1f}% {100*occ_cnt2:>8.1f}%")

    # ---- 基线复现验收（污染检测）----
    baseline_occ = next(r["real_occ_cnt2"] for r in scan_rows if r["thr"] == S3_FIXED)
    expected = None
    old_path = __file__.replace(".py", "_result.json")
    if os.path.exists(old_path):
        try:
            old = json.load(open(old_path, encoding="utf-8"))
            for row in old.get("threshold_scan", []):
                if abs(row.get("thr", -1) - S3_FIXED) < 1e-9:
                    expected = row.get("real_occ_cnt2")
                    break
        except Exception:
            pass
    if expected is not None:
        if abs(baseline_occ - expected) > 1e-9:
            print(f"\n[FATAL] 基线复现失败：occ_cnt2={baseline_occ:.6f} != v2 记录 {expected:.6f}"
                  f"（探针改动污染了基线），停。")
            sys.exit(2)
        print(f"\n[BASELINE-CHECK] 基线格复现 OK：occ_cnt2={100*baseline_occ:.1f}%"
              f"（与 v2 记录 {100*expected:.1f}% 逐位一致，探针改动未污染基线）")
    else:
        if abs(baseline_occ - 0.593) > 0.002:
            print(f"\n[FATAL] 基线复现失败：occ_cnt2={100*baseline_occ:.2f}% 偏离 59.3%，停。")
            sys.exit(2)
        print(f"\n[BASELINE-CHECK] 基线格 occ_cnt2={100*baseline_occ:.1f}% ≈ 59.3% OK"
              f"（旧 JSON 缺失，按公报值校验）")

    # ---- 5. v3 主扫描：RECOVERY_DAYS × S2变体（S3 固定 0.05，cnt≥2）----
    # 每个 S2 变体先算 cnt2 flags（一次），再喂不同状态机
    variant_flags = {}
    for v in S2_VARIANTS:
        variant_flags[v] = [
            (int(r["s1"]) + int(r["s2v"][v]) + int(r["s3_frac"] < S3_FIXED)) >= 2
            for r in samples
        ]
    # 裸触发率（= RECOVERY_DAYS→0 下界）
    raw_rate = {v: sum(f) / N for v, f in variant_flags.items()}

    matrix = {}       # (variant, rd) -> {occ, flips}
    print(f"\n=== 5. v3 主矩阵：occ_cnt2 / 翻转次数（S3=0.05 固定，cap=∞）===")
    hdr = "  {:<11}".format("S2变体") + "".join(f"  RD={rd:<2}          " for rd in RECOVERY_SCAN)
    print(hdr + "  裸cnt2(下界)")
    for v in S2_VARIANTS:
        cells = []
        for rd in RECOVERY_SCAN:
            occ, flips = _simulate(variant_flags[v], dates, rd)
            matrix[(v, rd)] = {"occ": occ, "flips": flips}
            cells.append(f"{100*occ:>5.1f}%/{flips:<4d}")
        print("  {:<11}".format(v) + "  ".join(f"{c:<15}" for c in cells)
              + f"  {100*raw_rate[v]:.1f}%")

    baseline_flips = matrix[("baseline", BASELINE_RECOVERY)]["flips"]
    print(f"\n  baseline 参照：occ_OR(RD=5)="
          f"{100*next(r['real_occ_or'] for r in scan_rows if r['thr']==S3_FIXED):.1f}%，"
          f"baseline 翻转次数={baseline_flips}（描述性指标，不作自动过滤）")

    # ---- 6. monthly_cap 叠加扫描（对 baseline 与 band0.98 两个代表变体）----
    cap_matrix = {}
    print(f"\n=== 6. monthly_cap 叠加（occ_cnt2/翻转；单月占用达 cap 强制退出并封锁至月末）===")
    for v in ["baseline", "band0.98"]:
        print(f"  [{v}]")
        print("    {:<8}".format("cap") + "".join(f"  RD={rd:<2}          " for rd in RECOVERY_SCAN))
        for cap in MONTHLY_CAPS:
            cells = []
            for rd in RECOVERY_SCAN:
                occ, flips = _simulate(variant_flags[v], dates, rd, monthly_cap=cap)
                cap_matrix[(v, rd, cap)] = {"occ": occ, "flips": flips}
                cells.append(f"{100*occ:>5.1f}%/{flips:<4d}")
            print("    {:<8}".format(cap) + "  ".join(f"{c:<15}" for c in cells))

    # ---- 7. 达标格子清单 + 推荐（改动最小优先；翻转次数仅作描述性参考）----
    qualifying = []
    for (v, rd), cell in matrix.items():
        if cell["occ"] <= TARGET_OCC:
            qualifying.append({
                "variant": v, "recovery_days": rd, "monthly_cap": None,
                "occ_cnt2": cell["occ"], "flips": cell["flips"],
                "change_rank": CHANGE_ORDER[v],
            })
    for (v, rd, cap), cell in cap_matrix.items():
        if cell["occ"] <= TARGET_OCC:
            qualifying.append({
                "variant": v, "recovery_days": rd, "monthly_cap": cap,
                "occ_cnt2": cell["occ"], "flips": cell["flips"],
                "change_rank": 5,   # monthly_cap = 最深改动
            })
    # 排序：改动最小 → RECOVERY_DAYS 大 → 翻转少（翻转仅作排序参考，不作硬性过滤）
    qualifying.sort(key=lambda q: (q["change_rank"], -q["recovery_days"], q["flips"]))
    recommended = qualifying[:3]

    print(f"\n=== 7. 达标格子（occ_cnt2 ≤ {100*TARGET_OCC:.0f}%）共 {len(qualifying)} 格 "
          f"（翻转次数仅作描述性参考，不用于过滤）===")
    if recommended:
        print("  前 3 推荐（改动最小优先；翻转次数仅作参考，不自动过滤）：")
        for i, q in enumerate(recommended, 1):
            caps = f"+monthly_cap={q['monthly_cap']}" if q["monthly_cap"] else ""
            deep = ("  ⚠需确认结构改动深度" if q["change_rank"] >= 4 else "")
            print(f"  #{i} S2={q['variant']}{caps}, RECOVERY_DAYS={q['recovery_days']}"
                  f" → occ={100*q['occ_cnt2']:.1f}%, 翻转={q['flips']}"
                  f"（{VARIANT_DESC.get(q['variant'], '')}）{deep}")
    else:
        print("  ⚠ 全矩阵无达标格（或达标格翻转均超限）——停下汇报，需用户重估 ≤40% 目标。")

    # ---- 8. S3 分位曲线（日频，对照保留）----
    fracs = np.array([r["s3_frac"] for r in samples], dtype=float)
    quantiles = {f"p{q}": float(np.percentile(fracs, q)) for q in [10, 25, 50, 75, 90]}

    # ---- 输出 HISTORICAL_PROBE（v3）----
    probe = {
        "version": "v3-recovery-x-s2variant-scan",
        "frequency": "daily",
        "window": f"{START.isoformat()}~{END.isoformat()}",
        "daily_samples": N,
        "include_convention": (
            "探针桩窗口含当日（include 参数忽略）；S2 变体与 baseline 桩同口径（含当日）。"
            "策略源码为 include=False（真实引擎窗口止于上一交易日）：扫描内部口径统一、"
            "横向比较有效；绝对水平与真实引擎可能有 1 日相位差，Step 1 以黄金对照为准。"
        ),
        "s3_fixed": S3_FIXED,
        "baseline_check": {"occ_cnt2": baseline_occ, "expected": expected,
                           "passed": True},
        "s2_x_s3_matrix": m,
        "p_s2_and_s3": p_s2_and_s3,
        "threshold_scan_v2_reference": scan_rows,
        "s3_frac_quantiles": quantiles,
        "raw_cnt2_rate_by_variant": raw_rate,
        "matrix_occ_flips": {
            f"{v}|RD={rd}": {"occ_cnt2": cell["occ"], "flips": cell["flips"]}
            for (v, rd), cell in sorted(matrix.items(),
                                        key=lambda kv: (CHANGE_ORDER[kv[0][0]], kv[0][1]))
        },
        "monthly_cap_matrix": {
            f"{v}|RD={rd}|cap={cap}": {"occ_cnt2": cell["occ"], "flips": cell["flips"]}
            for (v, rd, cap), cell in sorted(cap_matrix.items(),
                                             key=lambda kv: (kv[0][0], kv[0][1], kv[0][2]))
        },
        "baseline_flips": baseline_flips,
        "qualifying_cells": qualifying,
        "recommended_top3": recommended,
        "variant_desc": VARIANT_DESC,
        "target_band": "daily occ_cnt2 <= 40% (flips are descriptive only, not a gate)",
        "decision_note": "仅推荐排序，不自行启动 Step 1；改动量 baseline<band<slope_down<monthly_cap 递增；翻转次数仅作参考",
    }
    with open(old_path, "w", encoding="utf-8") as f:
        json.dump(probe, f, ensure_ascii=False, indent=2)
    print(f"\n[HISTORICAL_PROBE] 已写入: {old_path}")


if __name__ == "__main__":
    main()
