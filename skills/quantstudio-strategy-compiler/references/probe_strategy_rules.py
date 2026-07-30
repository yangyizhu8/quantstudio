#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A1 — 回测前历史探测脚本（双目标裁决前置，消除"炸弹后知后觉"根因）——【日频口径 v2】

直接 import 候选策略 sw_industry_etf_rotation_8f__candidate_quantstudio.py 的
_bear_signal_s1 / _bear_signal_s2 / _bear_signal_s3（**勿重写规则，防漂移**），
对 2021-07 ~ 2026-07 **每个交易日**，用真实 quantstudio.db 数据驱动，产出：

  1. 逐日原始量：S1∈{0,1}、S2∈{0,1}、S3_frac∈[0,1]（分数本身，非布尔）
  2. S2×S3 联合分布矩阵（C1-B 命脉）：2×2 频次 + 日频 P(S2∧S3)
  3. S3 阈值扫描：对 S3_BEAR_FRAC ∈ {0.05,0.08,0.10,0.12,0.15} 算
       裸 P(OR≥1)、裸 P(count≥2)、
       OR≥1 真实占用率、count≥2 真实占用率
       （触发序列喂入 RECOVERY_DAYS=5 状态机还原——日频下滞回口径与策略
        RECOVERY_DAYS=5 完全一致，"连续 5 个交易日清空→退出"）
  4. S3 分位曲线：S3_frac 日频经验分布 10/25/50/75/90 分位

【为什么改日频】旧版月度采样（间隔 30 天）N=61：RECOVERY_DAYS=5 滞回按交易日
生效，月度间隔下恢复期几乎总落在两次采样间被"冲掉"，导致月度占用率是**低估
下界**。日频口径才是真实占用率。月度旧数字保留为对照（monthly_reference）。

自标定决策规则（写入 HISTORICAL_PROBE 段，日频口径）：
  目标带：日频 count≥2 真实占用率 ≤ 40%
  选使日频 count≥2 真实占用率 ≤40% 的最小 S3 阈值（最小化对熊市保护的削弱）
  若 0.15 仍 >40%，停在 0.15 并标注"≥2+0.15 仍不足以达标，建议人工裁决"
  选定值记为 S3_BEAR_FRAC_CAL

双端一致性：本脚本只读 quantstudio.db，不依赖框架注入；与候选策略规则零漂移。
输出：stdout 打印 HISTORICAL_PROBE 全量数字，并写 references/probe_strategy_rules_result.json。
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
RECOVERY_DAYS = 5
MIN_BARS = 250          # 每只 ETF 取最近 N 根（覆盖 s1>=25 / s3>=60）
IDX_BARS = 201          # 沪深300 取最近 N 根（覆盖 s2>=200）
SCAN_THRESHOLDS = [0.05, 0.08, 0.10, 0.12, 0.15]
TARGET_OCC = 0.40


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
    """返回 get_history 桩：仅 S2（沪深300）使用，返回 {security: recarray(close)}。"""
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
# 状态机（与策略 RECOVERY_DAYS=5 同口径：日频触发→半仓，连续 5 交易日清空→退出）
# ---------------------------------------------------------------------------
def _simulate_occupancy(flags):
    bear_mode = False
    recovery = 0
    occupied = 0
    for sig in flags:
        if sig:
            bear_mode = True
            recovery = 0
        else:
            if bear_mode:
                recovery += 1
                if recovery >= RECOVERY_DAYS:
                    bear_mode = False
        if bear_mode:
            occupied += 1
    return occupied / len(flags) if flags else 0.0


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
        s2 = bool(mod._bear_signal_s2())
        s3_default = bool(mod._bear_signal_s3(codes, valid))  # 默认 0.05
        nh = [mod._f6_newhigh(valid[c][0]) for c in codes]
        s3_frac = float(np.mean(nh)) if codes else 0.0
        d = datetime.datetime.fromtimestamp(ts / 1000).date()
        samples.append({
            "date": d.isoformat(),
            "n_valid": len(codes),
            "s1": s1, "s2": s2, "s3_default": s3_default,
            "s3_frac": s3_frac,
        })

    N = len(samples)

    # ---- 2. S2×S3 联合分布（默认 S3 阈值 0.05，日频）----
    m = {"S2=0,S3=0": 0, "S2=0,S3=1": 0, "S2=1,S3=0": 0, "S2=1,S3=1": 0}
    for r in samples:
        key = f"S2={1 if r['s2'] else 0},S3={1 if r['s3_default'] else 0}"
        m[key] += 1
    p_s2_and_s3 = m["S2=1,S3=1"] / N if N else 0.0
    print("\n=== 2. S2×S3 联合分布矩阵（日频，S3 默认阈值 0.05）===")
    for k in ["S2=0,S3=0", "S2=0,S3=1", "S2=1,S3=0", "S2=1,S3=1"]:
        print(f"  {k}: {m[k]}  ({100*m[k]/N:.1f}%)")
    print(f"  日频 P(S2∧S3) = {p_s2_and_s3:.4f}")

    # ---- 3. S3 阈值扫描（日频；OR≥1 与 count≥2 双占用率）----
    print("\n=== 3. S3 阈值扫描（日频，RECOVERY_DAYS=5 状态机）===")
    print(f"  {'thr':>6} {'裸OR>=1':>9} {'裸cnt>=2':>9} {'occ_OR':>8} {'occ_cnt2':>9}")
    scan_rows = []
    for thr in SCAN_THRESHOLDS:
        or_flags = []
        cnt2_flags = []
        for r in samples:
            s3b = r["s3_frac"] < thr
            s1 = r["s1"]
            s2 = r["s2"]
            or_flags.append(s1 or s2 or s3b)
            cnt2_flags.append((int(s1) + int(s2) + int(s3b)) >= 2)
        p_or = sum(or_flags) / N if N else 0.0
        p_cnt2 = sum(cnt2_flags) / N if N else 0.0
        occ_or = _simulate_occupancy(or_flags)
        occ_cnt2 = _simulate_occupancy(cnt2_flags)
        scan_rows.append({"thr": thr, "p_or": p_or, "p_cnt2": p_cnt2,
                          "real_occ_or": occ_or, "real_occ_cnt2": occ_cnt2})
        print(f"  {thr:>6.2f} {100*p_or:>8.1f}% {100*p_cnt2:>8.1f}% "
              f"{100*occ_or:>7.1f}% {100*occ_cnt2:>8.1f}%")

    # ---- 4. S3 分位曲线（日频）----
    fracs = np.array([r["s3_frac"] for r in samples], dtype=float)
    quantiles = {f"p{q}": float(np.percentile(fracs, q)) for q in [10, 25, 50, 75, 90]}
    print("\n=== 4. S3_frac 分位曲线（日频）===")
    for k, v in quantiles.items():
        print(f"  {k}: {v:.3f}")

    # ---- 自标定 S3_BEAR_FRAC_CAL（日频 count≥2 真实占用率 ≤40% 的最小阈值）----
    cal = None
    note = ""
    for row in scan_rows:
        if row["real_occ_cnt2"] <= TARGET_OCC:
            cal = row["thr"]
            break
    if cal is None:
        cal = 0.15
        note = "日频 count≥2 在 S3=0.15 下仍 >40%，停在 0.15，需人工裁决（勿自行启动 Phase-2 / 勿推阈值 >0.15）"
    print(f"\n=== 自标定 S3_BEAR_FRAC_CAL = {cal}（日频口径）===")
    if note:
        print(f"  ⚠ {note}")

    # ---- 月度旧结果对照（若有）----
    monthly_ref = None
    old_path = __file__.replace(".py", "_result.json")
    if os.path.exists(old_path):
        try:
            old = json.load(open(old_path, encoding="utf-8"))
            if "monthly_samples" in old:
                monthly_ref = {
                    "note": "月度=低估下界（RECOVERY_DAYS=5 滞回被 30 天采样间隔冲掉）",
                    "monthly_samples": old.get("monthly_samples"),
                    "p_s2_and_s3": old.get("p_s2_and_s3"),
                    "threshold_scan": old.get("threshold_scan"),
                    "S3_BEAR_FRAC_CAL": old.get("S3_BEAR_FRAC_CAL"),
                }
        except Exception:
            pass

    # ---- 输出 HISTORICAL_PROBE（日频口径）----
    probe = {
        "frequency": "daily",
        "window": f"{START.isoformat()}~{END.isoformat()}",
        "daily_samples": N,
        "s1_trigger_rate": sum(r["s1"] for r in samples) / N if N else 0.0,
        "s2_trigger_rate": sum(r["s2"] for r in samples) / N if N else 0.0,
        "s3_default_trigger_rate": sum(r["s3_default"] for r in samples) / N if N else 0.0,
        "s2_x_s3_matrix": m,
        "p_s2_and_s3": p_s2_and_s3,
        "threshold_scan": scan_rows,
        "s3_frac_quantiles": quantiles,
        "S3_BEAR_FRAC_CAL": cal,
        "cal_note": note,
        "target_band": "daily count>=2 real_occupancy <= 40%",
        "recovery_days": RECOVERY_DAYS,
        "monthly_reference": monthly_ref,
    }
    with open(old_path, "w", encoding="utf-8") as f:
        json.dump(probe, f, ensure_ascii=False, indent=2)
    print(f"\n[HISTORICAL_PROBE] 已写入: {old_path}")

    # 强制确认提示判定（日频 OR 口径）
    occ_or_default = next(r["real_occ_or"] for r in scan_rows if r["thr"] == 0.05)
    if occ_or_default > 0.50:
        print("\n[CONFIRM-PROMPT] 日频 OR(>=1) 真实占用率 >50%，R0 必须追加：'是否接受常年半仓？'")


if __name__ == "__main__":
    main()
