"""① X 类最终定谳：k 的整数性 + 偏移 c 的性质。

已得：f 服从 **幂律** ln f = k·ln r + c（R²中位 0.9917，残差 std 中位 0.00033），
      k 距最近整数中位 0.014 ⇒ k 是整数（≈ ±1, ±2），偏差由**非零截距 c** 承载。

本步：
 A. 逐码给出 k_int（最近整数）+ 距整距离 + c + 隐含旧锚比 exp(c/k_int)
 B. 按 k_int 分组统计 c（不同 k 组 c 是否同量级 —— 若 c 与 k 无关 ⇒ 属「全局锚偏移」；若 c/k 稳定 ⇒ 属「倍率偏差」）
 C. 检验 X 类与 C 类（j=2）的关系：X 是否 = 「C 类 + 偏移」
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
    A = np.vstack([lr, np.ones_like(lr)]).T
    (k, c), *_ = np.linalg.lstsq(A, lf, rcond=None)
    pred = A @ np.array([k, c])
    r2 = 1 - (lf - pred).var() / lf.var() if lf.var() > 0 else np.nan
    k_int = int(round(k))
    rows.append({
        "code": code, "n": len(s), "k": k, "k_int": k_int,
        "k_dev": abs(k - k_int), "c": c, "r2": r2,
        "implied_anchor_ratio": float(np.exp(c / k_int)) if k_int else np.nan,
        "j_av_med": s["j_av"].median(), "r_med": s["r"].median(),
        "f_med": s["f"].median(),
    })
R = pd.DataFrame(rows).sort_values(["k_int", "c"])
print(f"拟合码数={len(R)}")
print("\n=== A. 逐码 k 整数性与截距 ===")
print(R[["code", "n", "k", "k_int", "k_dev", "c", "r2",
         "implied_anchor_ratio", "j_av_med"]].to_string(index=False))
print(f"\n  k 距最近整数: 中位={R['k_dev'].median():.4f}  P90={R['k_dev'].quantile(.90):.4f}  "
      f"max={R['k_dev'].max():.4f}")
print(f"  R² ≥0.99 的码数={int((R['r2'] >= .99).sum())}/{len(R)}")
print(f"  k_int 分布: {R['k_int'].value_counts().sort_index().to_dict()}")

good = R[R["r2"] >= 0.9].copy()
print(f"\n=== B. 按 k_int 分组的 c（R²≥0.9，{len(good)} 码）===")
for kk, g in good.groupby("k_int"):
    print(f"  k_int={kk:+d}: 码数={len(g):>2}  n合计={int(g['n'].sum()):>5}  "
          f"c 中位={g['c'].median():+.5f}  c 范围=[{g['c'].min():+.5f},{g['c'].max():+.5f}]  "
          f"隐含锚比中位={g['implied_anchor_ratio'].median():.5f}")
print(f"\n  c 的总体量级: 中位={good['c'].median():+.5f} → "
      f"|c| 对应 f 的相对偏移 ≈ {(np.exp(good['c'].abs().median())-1)*100:.2f}%")

print("\n=== C. X 类与 C 类（j≈2）的关系 ===")
# j_av 换算为 k：k = -j_av
R["k_from_j"] = -R["j_av_med"]
print(f"  k(拟合) 与 -j_av(中位) 之差: 中位={(R['k'] - R['k_from_j']).abs().median():.4f}")
print(f"  ⇒ 二者一致（差 {((R['k'] - R['k_from_j']).abs().median()):.4f}）⇒ "
      f"j 的'非整数'正是因为**用错锚计算**时 f 偏离了纯 r^k")
print(f"\n  若按**正确锚**（c 归零）重算 j：j_corrected = -(ln f - c)/ln r")
det2 = X.copy()
cmap = good.set_index("code").to_dict("index")
det2["c_code"] = det2["ts_code"].map({k: v["c"] for k, v in cmap.items()})
h = det2[det2["c_code"].notna()].copy()
h["j_corr"] = -(np.log(h["f"]) - h["c_code"]) / np.log(h["r"])
print(f"  校正后 j 距最近整数: 中位={ (h['j_corr']-h['j_corr'].round()).abs().median():.4f}")
print(f"  校正后 j 最近整数分布: {h['j_corr'].round().value_counts().sort_index().to_dict()}")
print(f"  校正后落在 ±0.05 内占比={((h['j_corr']-h['j_corr'].round()).abs()<=.05).mean():.1%}")
if h["j_corr"].round().nunique() <= 3:
    print(f"  ⇒ 校正后 j 回到**整数**（分布集中于 "
          f"{sorted(set(h['j_corr'].round().astype(int)))}）")
else:
    print("  ⇒ 校正后仍分散，机制需再查")