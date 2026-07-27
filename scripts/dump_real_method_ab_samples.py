# -*- coding: utf-8 -*-
"""三证券真实方法 A/B 报告（第四轮对抗审核交付物，2026-07-27）。

第四轮修正要点：
1. **默认容差 = 正式行为**：xtquant 为减法复权模型（front = raw − 每股分红），
   区间内不存在单一稳定乘法比率簇 → 默认容差下引擎必须 BLOCK，
   三证券不得自动写回。本脚本第 1 部分运行实际引擎（默认 ReanchorTolerances）
   并打印 status / block_reason / error 与数据未变证明。
2. **诊断模式（非验收路径，明确标注）**：复现第三轮放宽容差后的 committed
   结果，从实际 ReanchorResult.plans 读取 segment.ratio（真正落库 ratio，
   非逐日 target/stored），逐样本报告：
     R_B observation / actual segment ratio / R_golden /
     post-write front / fresh xtquant front / 绝对与相对价格误差；
   并输出 post-write vs fresh xtquant 的逐 bar 误差统计
   （最大绝对误差、>0.01 元 bar 数 / 更新窗口 bar 总数）。
   诊断模式仅用于量化"乘法模型 vs 减法模型"的可观察偏差，
   为设计决策文档提供证据，**不构成验收依据**。

运行：项目根目录
  C:/Users/Administrator/AppData/Local/Programs/Python/Python311/python.exe \
      scripts/dump_real_method_ab_samples.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from quantstudio.pipeline.qfq_reanchor_engine import (   # noqa: E402
    ReanchorTolerances, apply_reanchor_for_security, _cont_clock_set)

import test_qfq_reanchor_batch2 as T                     # noqa: E402

TZ = "Asia/Shanghai"
EX = {"600875": T.REAL_D24, "600039": T.REAL_D24, "002864": T.REAL_D22}
EXS = {"600875": "2026-07-24", "600039": "2026-07-24", "002864": "2026-07-22"}
CONT = _cont_clock_set("1min")

# 诊断容差（= 第三轮被驳回的放宽值；仅诊断复现用，非验收容差）
DIAG_TOL = {
    "600875": ReanchorTolerances(ratio_rel_tol=3e-3, golden_rel_tol=3e-3,
                                 tol_return=3e-3, tol_cross=3e-3),
    "600039": ReanchorTolerances(ratio_rel_tol=1e-2, golden_rel_tol=1e-2,
                                 tol_return=6e-3, tol_cross=1e-2),
    "002864": ReanchorTolerances(ratio_rel_tol=2e-3, golden_rel_tol=3e-3,
                                 tol_return=2e-3, tol_cross=2e-3),
}


def _tstr(ms) -> str:
    return pd.Timestamp(int(ms), unit="ms", tz=TZ).strftime("%m-%d %H:%M")


def _setup(code: str):
    tmp = Path(tempfile.mkdtemp(prefix=f"qfq_dump_{code}_"))
    env = T._make_env(tmp, T.REAL_OPEN_DAYS)
    T._load_real(env.conn, code)
    T._prealign_predecessor(env.conn, code, T.REAL_D13)
    fresh = T._fresh_xt_daily(code, T.REAL_D14)
    golden = T._fresh_xt_golden(code)
    return env, fresh, golden


def _run(code: str, tol):
    env, fresh, golden = _setup(code)
    pre_d = T._snap(env.conn, "stock_daily")
    pre_m = T._snap(env.conn, "stock_minutes")
    res = apply_reanchor_for_security(
        env.conn, asset_type="STOCK", code=code, fresh_daily=fresh,
        calendar=env.calendar, freqs=("1min",), golden_minutes=golden,
        ex_dates_ms=(EX[code],), tol=tol)
    return env, res, pre_d, pre_m


def _cluster_structure(code: str, tol: ReanchorTolerances):
    """按引擎同款 running-ref 判据展示逐日 R 与簇切分（诊断展示）。"""
    xd = pd.read_parquet(T.FXDIR / f"{code}_fresh_daily.parquet").sort_values("time")
    xd = xd[xd["time"].astype("int64") >= T.REAL_D14]
    sm = pd.read_parquet(T.FIXDIR / f"{code}_minutes.parquet").sort_values("time")
    day = (pd.to_datetime(sm["time"].astype("int64"), unit="ms", utc=True)
           .dt.tz_convert(TZ).dt.normalize().astype("int64") // 10**6)
    sm = sm.assign(day=day)
    tsc = dict(zip(xd["time"].astype("int64"),
                   xd["close_front"].astype(float) / xd["close_raw"].astype(float)))
    rows, ref, seg = [], None, 0
    for d, g in sm.groupby("day", sort=True):
        s_scale = float(np.median(g["close_front"] / g["close"]))
        r = float(tsc[int(d)]) / s_scale
        if ref is None:
            ref = r
        elif abs(r / ref - 1.0) > tol.ratio_rel_tol:
            seg += 1
            ref = r
        rows.append((pd.Timestamp(int(d), unit="ms", tz=TZ).strftime("%m-%d"),
                     r, seg))
    return rows


print("=" * 78)
print("第 1 部分（正式行为）：默认 ReanchorTolerances 运行实际引擎")
print("=" * 78)
for code in ("600875", "600039", "002864"):
    env, res, pre_d, pre_m = _run(code, ReanchorTolerances())
    print(f"\n----- {code} (ex={EXS[code]}) 默认容差 -----")
    print(f"status={res.status}  block_reason={res.block_reason}")
    print(f"error={res.error}")
    post_d = T._snap(env.conn, "stock_daily")
    post_m = T._snap(env.conn, "stock_minutes")
    pd.testing.assert_frame_equal(pre_d, post_d)
    pd.testing.assert_frame_equal(pre_m, post_m)
    print("数据未变证明: stock_daily / stock_minutes 快照逐值一致（BLOCK 未写回）")
    rows = _cluster_structure(code, ReanchorTolerances())
    n_seg = rows[-1][2] + 1
    print(f"逐日 R（target_scale/stored_scale, running-ref 判据, "
          f"ratio_rel_tol={ReanchorTolerances().ratio_rel_tol:.0e}）"
          f"→ {n_seg} 个簇:")
    for dstr, r, seg in rows:
        print(f"  {dstr}: R={r:.10f}  seg={seg}")
    env.conn.close()

def _report_errors(code: str, j: pd.DataFrame, ratio: float,
                   pre_m_idx: pd.DataFrame, ratio_label: str):
    """逐样本表 + 逐 bar 误差统计（committed 实写与离线模拟共用）。

    j 列: time / close(stored raw) / close_front(写回或模拟 front) / xt_front
    """
    j = j.copy()
    j["abs_err"] = (j["close_front"] - j["xt_front"]).abs()
    j["rel_err"] = j["abs_err"] / j["xt_front"]
    clock = (pd.to_datetime(j["time"], unit="ms", utc=True).dt.tz_convert(TZ))
    j["clock_min"] = clock.dt.hour * 60 + clock.dt.minute
    j["day"] = clock.dt.normalize().astype("int64") // 10**6

    xd = pd.read_parquet(T.FXDIR / f"{code}_fresh_daily.parquet")
    xd_t = xd["time"].astype("int64")

    # 逐样本：每个更新日等距抽 3 根合法连续竞价 bar
    print(f"{'bar_time':<12} {'R_B_obs':>12} {'plan_ratio':>12} {'R_golden':>12} "
          f"{'postwrite':>10} {'xt_front':>10} {'abs_err':>9} {'rel_err':>9}")
    for d, g in j[j["clock_min"].isin(CONT)].groupby("day", sort=True):
        g = g.sort_values("time").reset_index(drop=True)
        for i in np.linspace(0, len(g) - 1, 3).round().astype(int):
            row = g.iloc[int(i)]
            t = int(row["time"])
            stored_front_pre = float(pre_m_idx.loc[t, "close_front"])
            stored_scale = stored_front_pre / float(row["close"])
            trg = xd.loc[xd_t == int(d)]
            target_scale = (float(trg["close_front"].iloc[0])
                            / float(trg["close_raw"].iloc[0]))
            r_b_obs = target_scale / stored_scale          # 方法 B 逐日观测
            r_golden = float(row["xt_front"]) / stored_front_pre   # 方法 A 观测
            print(f"{_tstr(t):<12} {r_b_obs:>12.8f} {ratio:>12.8f} "
                  f"{r_golden:>12.8f} {row['close_front']:>10.4f} "
                  f"{row['xt_front']:>10.4f} {row['abs_err']:>9.6f} "
                  f"{row['rel_err']:>9.2e}")

    n_gt = int((j["abs_err"] > 0.01).sum())
    print(f"post-write vs fresh xtquant 逐 bar 误差（更新窗口全部 {len(j)} 根 bar，"
          f"{ratio_label}）:")
    print(f"  max_abs_err = {float(j['abs_err'].max()):.6f} 元  "
          f"max_rel_err = {float(j['rel_err'].max()):.2e}")
    print(f"  误差>0.01元 bar 数 = {n_gt}/{len(j)}")
    print("  ↑ 该偏差为乘法单比率模型与 xtquant 减法复权模型不等价的直接后果，")
    print("    不是浮点误差；诊断结论支持默认容差 BLOCK 为正确行为。")


print()
print("=" * 78)
print("第 2 部分（诊断模式，非验收路径）：量化乘法单比率写回 vs fresh xtquant")
print("减法复权的真实价格偏差。若引擎在诊断容差下仍 BLOCK（OHLC 四列 fixture")
print("暴露 fresh_daily_scale_inconsistent 等新证据），改用**离线模拟单比率写回**")
print("（simulated front = stored_raw × ratio_plan，不触碰任何库表）。")
print("=" * 78)
for code in ("600875", "600039", "002864"):
    env, res, pre_d, pre_m = _run(code, DIAG_TOL[code])
    print(f"\n----- {code} (ex={EXS[code]}) 诊断容差={DIAG_TOL[code]} -----")
    print(f"status={res.status}")
    pre_m_idx = pre_m.set_index("time")
    xm = pd.read_parquet(T.FXDIR / f"{code}_fresh_1min.parquet")[
        ["time", "close_front"]].rename(columns={"close_front": "xt_front"})
    xm["time"] = xm["time"].astype("int64")

    if res.status == "committed":
        upd = [s for s in res.plans["1min"] if s.needs_update]
        assert len(upd) == 1
        seg = upd[0]
        print(f"actual plan ratio（引擎真正落库 RatioSegment.ratio）= {seg.ratio:.10f}")
        print(f"  segment: [{_tstr(seg.t_start)}, {_tstr(seg.t_end)})  "
              f"bar_count={seg.bar_count}  dispersion={seg.dispersion:.2e}")
        post = env.conn.execute(
            "SELECT time, close, close_front FROM stock_minutes "
            "WHERE code=? AND time >= ? AND time < ? ORDER BY time",
            [code, seg.t_start, seg.t_end]).df()
        post["time"] = post["time"].astype("int64")
        j = post.merge(xm, on="time", how="inner")
        _report_errors(code, j, float(seg.ratio), pre_m_idx,
                       "实际引擎写回（诊断容差 committed）")
    else:
        print(f"block_reason={res.block_reason} error={res.error}")
        print("→ 诊断容差下引擎亦 BLOCK（新 OHLC 证据）。改用离线模拟单比率写回：")
        # ratio_plan = 除权日前各日 R_B 观测（target_scale/stored_scale）中位数，
        # 与第三轮引擎 committed 时实际落库 RatioSegment.ratio 同构
        xd = pd.read_parquet(T.FXDIR / f"{code}_fresh_daily.parquet")
        xd = xd[(xd["time"].astype("int64") >= T.REAL_D14)
                & (xd["time"].astype("int64") < EX[code])]
        sm = pre_m[(pre_m["time"] >= T.REAL_D14) & (pre_m["time"] < EX[code])].copy()
        dclk = (pd.to_datetime(sm["time"].astype("int64"), unit="ms", utc=True)
                .dt.tz_convert(TZ))
        sm["day"] = dclk.dt.normalize().astype("int64") // 10**6
        tsc = dict(zip(xd["time"].astype("int64"),
                       xd["close_front"].astype(float)
                       / xd["close_raw"].astype(float)))
        r_days = []
        for d, g in sm.groupby("day", sort=True):
            s_scale = float(np.median(g["close_front"] / g["close"]))
            r_days.append(float(tsc[int(d)]) / s_scale)
        ratio_plan = float(np.median(r_days))
        print(f"ratio_plan（模拟，除权前逐日 R_B 中位数）= {ratio_plan:.10f}")
        sim = sm[["time", "close"]].copy()
        sim["time"] = sim["time"].astype("int64")
        sim["close_front"] = sim["close"].astype(float) * ratio_plan
        j = sim.merge(xm, on="time", how="inner")
        _report_errors(code, j, ratio_plan, pre_m_idx,
                       "离线模拟 stored_raw × ratio_plan，未写库")
    env.conn.close()
