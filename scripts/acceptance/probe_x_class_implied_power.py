"""① X 类收口：反推逐码隐含施加次数 k* = ln(f)/(ln(r)·?) 并检验整数性。

若 X 为「真实施加了某整数次、但用的是**另一个锚**」⇒ 存在整数 k 使
    f = (adj_day/adj_old)^k  ⇒  ln f - k·ln r = k·ln(adj_old/latest)
由于 adj_old 未知，改为检验**码内一致性**：
    对同码不同 r 的 code-day，k_i = ln(f_i)/ln(r_i) 应≈常数（若 f 由 r^k 生成）
  → 用最小二乘拟合 k，看残差；残差小 ⇒ 幂律成立（真实施加）；残差大 ⇒ 非幂律
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"D:\miniQMT策略实盘\trading-battle-back\logs")
det = pd.read_csv(BASE / "qfq_triage_etf_minutes_20260924_171935_detail.csv")
X = det[det["class_final"] == "X"].copy()

rows = []
for code, s in X.groupby("ts_code"):
    s = s[(s["r"] > 1) & (s["f"] > 0)]
    if len(s) < 10:
        continue
    lr, lf = np.log(s["r"].values), np.log(s["f"].values)
    # 幂律拟合：ln f = k·ln r + c（c 应=0 若纯幂律且锚一致）
    A = np.vstack([lr, np.ones_like(lr)]).T
    (k, c), res, *_ = np.linalg.lstsq(A, lf, rcond=None)
    pred = A @ np.array([k, c])
    resid = lf - pred
    r2 = 1 - resid.var() / lf.var() if lf.var() > 0 else np.nan
    # 纯幂律（c=0）假设下的 k
    k0 = float((lr @ lf) / (lr @ lr))
    resid0 = lf - k0 * lr
    rows.append({
        "code": code, "n": len(s), "k_fit": k, "c": c, "r2": r2,
        "resid_std": resid.std(), "k_pure": k0, "resid0_std": resid0.std(),
        "r_med": s["r"].median(), "f_med": s["f"].median(),
        "j_av_med": s["j_av"].median(),
    })
R = pd.DataFrame(rows).sort_values("n", ascending=False)
print(f"参与拟合的 X 码数={len(R)}（n>=10）")
print("\n=== 幂律拟合（ln f = k·ln r + c）===")
print(R[["code", "n", "k_fit", "c", "r2", "resid_std", "k_pure", "resid0_std", "j_av_med"]]
      .head(16).to_string(index=False))
print(f"\n  R² 分位: P10={R['r2'].quantile(.10):.4f} P50={R['r2'].median():.4f} "
      f"P90={R['r2'].quantile(.90):.4f}")
print(f"  c(截距) 分位: P10={R['c'].quantile(.10):.5f} P50={R['c'].median():.5f} "
      f"P90={R['c'].quantile(.90):.5f}")
print(f"  残差 std 分位: P50={R['resid_std'].median():.5f} "
      f"（对比 ln f 的量级 {np.log(X['f']).std():.5f}）")
print(f"\n  纯幂律(c=0) 的 k_pure 分位: P10={R['k_pure'].quantile(.10):.3f} "
      f"P50={R['k_pure'].median():.3f} P90={R['k_pure'].quantile(.90):.3f}")
print(f"  纯幂律残差 std 中位={R['resid0_std'].median():.5f}")

print("\n=== 判读 ===")
hi = R["r2"] > 0.9
print(f"  R²>0.9 的码数={int(hi.sum())}/{len(R)}（幂律成立 ⇒ f 确由 r 的幂次生成）")
print(f"  R²>0.9 的码：k_fit 分位 P10={R.loc[hi,'k_fit'].quantile(.10):.3f} "
      f"P50={R.loc[hi,'k_fit'].median():.3f} P90={R.loc[hi,'k_fit'].quantile(.90):.3f}")
print(f"  k_fit 距最近整数（中位）={np.abs(R.loc[hi,'k_fit'] - R.loc[hi,'k_fit'].round()).median():.3f}")
print("\n  ⇒ 若 R² 高且 k 离整数远 ⇒ **真实分数次幂**（不是整数次+错锚）")
print("  ⇒ 若 R² 高且 k 近整数但 c≠0 ⇒ 整数次 + **错锚**（锚差由 c 承载）")
print("  ⇒ 若 R² 低 ⇒ f 不由 r 的幂次生成（别的机制）")

# k 与 j_av 差异：j_av 用逐日 f，k_fit 用幂律
print(f"\n  k_fit vs j_av(中位) 差的绝对值中位="
      f"{(R['k_fit'] - R['j_av_med']).abs().median():.4f}")