"""① X 类决定性检验：非整数 j 是「真实分数次施加」还是「噪声/旧锚」。

可用字段（detail.csv）：ts_code,date,n,f,u,adj_day,latest_adj,r,j_av,class_av,
  class_literal,last_close,daily_close,j_daily,class_final,close,avg_px

检验设计（不需要 SUB/N 行）：
 T1 【幂律一致性】若 X 是真实分数次施加 ⇒ 同码内 j 应≈常数（f^k·r 恒定）；
     若为噪声 ⇒ j 随 r 抖动且与 r 无系统关系。
 T2 【日线锚一致性】j_daily（日线锚法）与 j_av（amount/vol 法）在 X 码上是否一致；
     一致 ⇒ 两独立通道同判非整数 ⇒ 指向**真实的价格序列状态**而非测量噪声。
 T3 【close 三元一致性】在 X 码上比较 close / avg_px / daily_close 三者关系：
     avg_px 与 close 的差 = 日内 VWAP-收盘差（应小）；daily_close 与 close 的差
     = 日线锚偏差（反映旧锚/未迁移程度）。
 T4 【结构分解】把 X 码按 r 区间分组，看 j 是否随 r 系统性单调（旧锚假说的特征）。
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"D:\miniQMT策略实盘\trading-battle-back\logs")
STEM = "qfq_triage_etf_minutes_20260924_171935"
j = json.loads((BASE / f"{STEM}.json").read_text(encoding="utf-8"))
det = pd.read_csv(BASE / f"{STEM}_detail.csv")
X = det[det["class_final"] == "X"].copy()
print(f"X 行={len(X):,} 涉码={X['ts_code'].nunique()}")

print("\n=== T2 日线锚一致性（j_daily vs j_av）===")
X["j_diff"] = (X["j_av"] - X["j_daily"]).abs()
print(f"  |j_av - j_daily| 分位: P50={X['j_diff'].median():.4f} "
      f"P90={X['j_diff'].quantile(.90):.4f} P99={X['j_diff'].quantile(.99):.4f}")
print(f"  两者差 ≤0.05 占比={(X['j_diff'] <= .05).mean():.1%}  "
      f"≤0.25(J_TOL)={(X['j_diff'] <= .25).mean():.1%}")
print(f"  ⇒ 两**独立通道**（amount/vol 法 与 日线锚法）落在同一非整数值的比例即上方")

print("\n=== T3 三元一致性（close / avg_px / daily_close）===")
X["r_vwap"] = X["close"] / X["avg_px"]
X["r_daily"] = X["close"] / X["daily_close"]
for lbl, col in (("close/avg_px", "r_vwap"), ("close/daily_close", "r_daily")):
    s = X[col].replace([np.inf, -np.inf], np.nan).dropna()
    print(f"  {lbl}: P5={s.quantile(.05):.5f} P50={s.median():.5f} P95={s.quantile(.95):.5f} "
          f"≈1(±0.5%)占比={s.between(.995,1.005).mean():.1%}")

print("\n=== T1 幂律一致性：同码内 j 是否稳定 ===")
g = X.groupby("ts_code")["j_av"].agg(["size", "median", "std", "min", "max"])
g["iqr"] = X.groupby("ts_code")["j_av"].apply(lambda s: s.quantile(.75) - s.quantile(.25))
big = g[g["size"] >= 30].sort_values("size", ascending=False)
print(f"  n≥30 的码数={len(big)}  std 中位={big['std'].median():.4f}  "
      f"IQR 中位={big['iqr'].median():.4f}")
print(f"  ⇒ 若为真实分数次施加，j 应≈常数（std 应≈0）；实测 std 中位={big['std'].median():.4f}")

print("\n=== T1b 关键检验：j 的抖动是否随 r 变化（噪声按 ln r 放大）===")
# 噪声假说：δj ≈ δf/(f·ln r) ⇒ j 的日间抖动应 ∝ 1/ln r
big2 = big.copy()
big2["lnr"] = np.log(X.groupby("ts_code")["r"].median().reindex(big2.index))
big2["std_x_lnr"] = big2["std"] * big2["lnr"]
print(f"  std(j) 中位={big2['std'].median():.4f}   std(j)×ln(r) 中位={big2['std_x_lnr'].median():.4f}")
print(f"  ⇒ 若为纯测量噪声，std(j)×ln(r) 应≈常数（=f 的相对误差量级）")
print(f"  相关系数 corr(std(j), 1/ln r) = "
      f"{big2['std'].corr(1 / big2['lnr']):.3f}（噪声假说预期显著正相关）")

print("\n=== T4 结构分解：X 码按 r 分组，j 是否随 r 单调 ===")
for lo, hi, lbl in ((1.00, 1.02, "[1.00,1.02)"), (1.02, 1.05, "[1.02,1.05)"),
                    (1.05, 1.10, "[1.05,1.10)"), (1.10, 99, "[1.10,+)")):
    s = X[(X["r"] >= lo) & (X["r"] < hi)]
    if len(s):
        print(f"  r {lbl:12s} n={len(s):>6,}  j_av 中位={s['j_av'].median():>7.3f}  "
              f"std={s['j_av'].std():.4f}  f 中位={s['f'].median():.5f}")

print("\n=== 逐码明细 TOP12（X 类，按行数）===")
cols = ["size", "median", "std", "min", "max", "iqr"]
print(big[cols].head(12).to_string())
print("\n  同码 r 取值数（X 码）:")
print(X.groupby("ts_code")["r"].nunique().sort_values(ascending=False).head(10).to_string())