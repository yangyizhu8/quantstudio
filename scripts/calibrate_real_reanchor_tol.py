# -*- coding: utf-8 -*-
"""校准真实数据回归测试容差（第三轮对抗审核，2026-07-27）。

背景：方法 A 黄金 oracle 改为直读 fresh xtquant 1min 前复权 close_front
（tests/fixtures/qfq_real_reanchor/fresh_xtquant/），不再用
stored_raw × daily_scale 合成。xtquant 客户端为**减法复权模型**
（front = raw − 每股现金分红，除权日前），per-day scale 随价格漂移，
因此 ratio 簇稳定性 / 黄金比对 / front-chain / 跨表容差必须按实测校准。

本脚本逐证券输出各硬门禁在真实 fixture 上的实际最大偏差，测试中的
ReanchorTolerances 覆盖值以此为依据（≥1.5x 安全裕度）并在测试 docstring 记录。
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "qfq_real_reanchor"
FX = FIX / "fresh_xtquant"
TZ = "Asia/Shanghai"

EX = {"600875": "2026-07-24", "600039": "2026-07-24", "002864": "2026-07-22"}


def day_ms(series):
    return (pd.to_datetime(series.astype("int64"), unit="ms", utc=True)
            .dt.tz_convert(TZ).dt.normalize().astype("int64") // 10**6)


for code in ("600875", "600039", "002864"):
    print(f"\n===== {code} (ex={EX[code]}) =====")
    stored_d = pd.read_parquet(FIX / f"{code}_daily.parquet").sort_values("time")
    stored_m = pd.read_parquet(FIX / f"{code}_minutes.parquet").sort_values("time")
    xt_d = pd.read_parquet(FX / f"{code}_fresh_daily.parquet").sort_values("time")
    xt_m = pd.read_parquet(FX / f"{code}_fresh_1min.parquet").sort_values("time")

    ex_ms = int(pd.Timestamp(EX[code], tz=TZ).timestamp() * 1000)
    lo14 = int(pd.Timestamp("2026-07-14", tz=TZ).timestamp() * 1000)

    # xt raw close 与 stored close 一致性（fixture 完整性前提）
    j = stored_d.merge(xt_d, on="time", suffixes=("_s", "_x"))
    dev_raw = (j["close"] - j["close_raw"]).abs().max()
    print(f"xt daily close_raw vs stored close 最大绝对差: {dev_raw:.3e}")

    # per-day xt scale（staged 窗口 = 07-14..07-24）
    xt = xt_d[xt_d["time"] >= lo14].reset_index(drop=True)
    xt["scale"] = xt["close_front"] / xt["close_raw"]
    upd = xt[xt["time"] < ex_ms]
    print("staged 窗口 per-day scale:",
          {pd.Timestamp(t, unit='ms', tz=TZ).strftime('%m-%d'): round(s, 6)
           for t, s in zip(xt['time'], xt['scale'])})

    # ---- 方法 B：R 序列（分钟 stored front==raw → R=当日 target scale）----
    stored_m_upd = stored_m[stored_m["time"] < ex_ms]
    stale = (stored_m["close_front"] - stored_m["close"]).abs().max() \
        if code != "002864" else None
    md = day_ms(stored_m_upd["time"])
    smap = dict(zip(xt["time"].astype("int64"), xt["scale"]))
    R = md.map(smap).astype(float)
    ratio = float(np.median(R))
    # 分段存活：running-ref 判据
    ref, worst_ref = R.iloc[0], 0.0
    for v in R:
        d = abs(v / ref - 1.0)
        worst_ref = max(worst_ref, d)
    disp = float(np.max(np.abs(R / ratio - 1.0)))
    print(f"方法B: ratio(median)={ratio:.6f} 段内dispersion={disp:.2e} "
          f"running-ref最大偏差={worst_ref:.2e} -> ratio_rel_tol 需 >= {disp:.2e}")

    # ---- 方法 A：r_golden = xt_minute_front / stored_front(=raw) vs ratio ----
    sm = stored_m_upd[["time", "close", "close_front"]].merge(
        xt_m[["time", "close_front"]].rename(columns={"close_front": "xt_front"}),
        on="time", how="left")
    miss = int(sm["xt_front"].isna().sum())
    r_g = sm["xt_front"] / sm["close_front"]
    dev_g = np.abs(r_g / ratio - 1.0)
    print(f"方法A: 缺样本={miss} max|r_golden/ratio-1|={float(dev_g.max()):.2e} "
          f"-> golden_rel_tol 需 >= {float(dev_g.max()):.2e}")

    # ---- front-chain：staged 日 fc_ret vs ref_ret（predecessor=xt 07-13 front）----
    d = stored_d.reset_index(drop=True)
    xt_all = xt_d.reset_index(drop=True)
    xt_map = dict(zip(xt_all["time"].astype("int64"), xt_all["close_front"]))
    sc_map = dict(zip(xt_all["time"].astype("int64"),
                      xt_all["close_front"] / xt_all["close_raw"]))
    worst_chain = 0.0
    times = d["time"].astype("int64").tolist()
    for i in range(1, len(times)):
        t, p = times[i], times[i - 1]
        if t < lo14:
            continue
        # 修正后 cf：staged 日=xt front；predecessor(07-13)=xt front（预对齐）
        # 002864 的 07-13 stored front 本就正确 → 用 stored 值
        if code == "002864":
            cf_t = float(d.loc[i, "close"]) * (sc_map[t] if False else
                                               float(d.loc[i, "close_front"]) / float(d.loc[i, "close"]))
            cf_t = float(d.loc[i, "close_front"])          # 已正确
            cf_p = float(d.loc[i - 1, "close_front"])
        else:
            cf_t = float(xt_map[t])
            cf_p = float(xt_map[p])
        ref = float(d.loc[i, "close"]) / float(d.loc[i, "preClose"]) - 1.0
        fc = cf_t / cf_p - 1.0
        worst_chain = max(worst_chain, abs(fc - ref))
    print(f"front-chain: max|fc_ret-ref_ret|={worst_chain:.2e} "
          f"-> tol_return 需 >= {worst_chain:.2e}")

    # ---- cross-table：15:00 bar front(=raw×ratio) vs 日线 front（xt/已正确）----
    worst_cross = 0.0
    for i, row in d.iterrows():
        t = int(row["time"])
        if t < lo14:
            continue
        day_bars = stored_m[day_ms(stored_m["time"]) == t]
        if len(day_bars) == 0:
            continue
        raw_last = float(day_bars["close"].iloc[-1])
        if code == "002864":
            dcf = float(row["close_front"])
            r_seg = ratio if t < ex_ms else 1.0
        else:
            dcf = float(xt_map[t])
            r_seg = ratio if t < ex_ms else 1.0
        mcf = raw_last * r_seg
        worst_cross = max(worst_cross, abs(mcf / dcf - 1.0))
    print(f"cross-table: max_dev={worst_cross:.2e} -> tol_cross 需 >= {worst_cross:.2e}")

    # ---- 报告用真实收益（600875/600039）----
    if code in ("600875", "600039"):
        c24 = float(d.loc[d["time"] == times[-1], "close"].iloc[0])
        c23 = float(d.loc[d["time"] == times[-2], "close"].iloc[0])
        pre24 = float(d.loc[d["time"] == times[-1], "preClose"].iloc[0])
        print(f"修复前伪跳空: {c24/c23-1.0:+.6%}  修复后真实收益: {c24/pre24-1.0:+.6%}")
