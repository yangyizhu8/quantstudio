"""①② 续：口径修正 + 决定性检验。

修正：两判据相左 = class_av vs class_literal 的**方向**比较（B→A 等），
      而非 class_av != class_literal（后者含 X↔A/B/C 交叉，共 4,200）。

决定性检验（辨「旧锚」vs「u 标定偏差」）：
  H1 旧锚：X 类码的 f 应等于某个**与 latest_adj 关联的常数**（旧锚比值），
           且该常数随 latest_adj 变化而稳定偏移 —— 应可用 u_code 解释
  H2 u 逐码偏差：在**无除息事件（r≈1）**的 code-day 上，j 应为 0；若某些码
           系统性 j≠0（且日间稳定），则 u_code ≠ 1 ⇒ 全局 u=1 标定对多数码偏高/偏低
  检验：对每个码，取 r≈1（|r-1|≤R_SKIP=0.005）的日子的 f 中位 ⇒ u_code
       then  j_corrected = -ln(f/u_code)/ln(r) 是否回到整数
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"D:\miniQMT策略实盘\trading-battle-back\logs")
STEM = "qfq_triage_etf_minutes_20260924_171935"
j = json.loads((BASE / f"{STEM}.json").read_text(encoding="utf-8"))
det = pd.read_csv(BASE / f"{STEM}_detail.csv")
S, RMIN = j["u"], j["params"]["R_MIN_CLASSIFY"]
print(f"u={S}  R_SKIP={j['params']['R_SKIP']}  R_MIN_CLASSIFY={RMIN}  "
      f"J_TOL={j['params']['J_TOL']}  detail={len(det):,}")

print("\n=== 口径修正：两判据相左（方向比较）===")
det["pair"] = det["class_av"] + "->" + det["class_literal"]
print("  detail 中 pair 分布（前 12）：")
print(det["pair"].value_counts().head(12).to_string())
n_ba = int((det["pair"] == "B->A").sum())
n_ac = int((det["pair"] == "A->C").sum())
print(f"\n  B→A 实测={n_ba:,}（json 矩阵 2,162）  A→C 实测={n_ac:,}（json 22）")
for lbl in ("B->A", "A->C", "X->A", "X->B", "X->C", "A->X", "B->X"):
    sub = det[det["pair"] == lbl]
    if len(sub):
        print(f"  {lbl}: n={len(sub):,} 涉码={sub['ts_code'].nunique()} "
              f"f中位={sub['f'].median():.5f} r中位={sub['r'].median():.5f} "
              f"j_av中位={sub['j_av'].median():.3f} j_daily中位={sub['j_daily'].median():.3f}")

# ---- 决定性检验：u 逐码标定 ----
print("\n" + "=" * 74)
print("决定性检验：零除息码日（r≈1）上的 f 是否恒为 1（u=1 是否成立）")
print("=" * 74)
sub = det[det["class_final"] == "SUB"] if "SUB" in set(det["class_final"]) else det.iloc[0:0]
print(f"  detail 中 class_final=SUB 行数={len(sub):,}（json class_counts SUB={j['class_counts'].get('SUB')}）")
if len(sub):
    print(f"  SUB 行 f 分位: P5={sub['f'].quantile(.05):.5f} P50={sub['f'].median():.5f} "
          f"P95={sub['f'].quantile(.95):.5f}  ≈1(±0.5%)占比={sub['f'].between(.995,1.005).mean():.1%}")
    q = sub["f"].quantile([.01, .05, .25, .5, .75, .95, .99])
    print(f"  分位明细: { {round(k,2): round(v,5) for k, v in q.items()} }")
    print(f"  ⇒ SUB = r-1 < R_MIN_CLASSIFY(0.02) 的**不判区**（近无除息）；"
          f"其 f 若恒≈1 则 u=1 成立、X 类的 f 偏离不能由 u 解释")

# 逐码 u_code：用 N 类（r≈1 且已判 N）+ SUB 行
near = det[(det["r"] - 1).abs() <= j["params"]["R_SKIP"]]
print(f"\n  近零除息行（|r-1|<=R_SKIP={j['params']['R_SKIP']}）={len(near):,}  涉码={near['ts_code'].nunique()}")
ucode = near.groupby("ts_code")["f"].agg(["median", "std", "size"])
print(f"  u_code(=f 中位) 分位: P5={ucode['median'].quantile(.05):.5f} "
      f"P50={ucode['median'].median():.5f} P95={ucode['median'].quantile(.95):.5f}")
print(f"  u_code 偏离 1 超 0.5% 的码数={int((abs(ucode['median']-1) > .005).sum())}/{len(ucode)}")
print(f"  u_code 偏离 1 超 2% 的码数={int((abs(ucode['median']-1) > .02).sum())}")

# 用 u_code 重算 X 类 j，看是否回到整数
print("\n" + "=" * 74)
print("用 u_code 重算 X 类 j ⇒ 是否回到整数（辨『旧锚』vs『u 标定偏差』）")
print("=" * 74)
X = det[det["class_final"] == "X"].copy()
X["u_code"] = X["ts_code"].map(ucode["median"])
have = X["u_code"].notna()
print(f"  X 行={len(X):,}  其中该码有近零除息基准的={int(have.sum()):,}（{have.mean():.1%}）")
Xh = X[have].copy()
Xh["j_uc"] = -np.log(Xh["f"] / Xh["u_code"]) / np.log(Xh["r"])
Xh["frac_before"] = Xh["j_av"] - Xh["j_av"].round()
Xh["frac_after"] = Xh["j_uc"] - Xh["j_uc"].round()
Xh["dist_before"] = Xh["frac_before"].abs().clip(upper=0.5)
Xh["dist_after"] = np.minimum(Xh["frac_after"].abs(), (1 - Xh["frac_after"].abs()))
print(f"  离整数距离（中位）: 原判据={Xh['dist_before'].median():.4f}  "
      f"u_code 校正后={Xh['dist_after'].median():.4f}")
for tol in (0.05, 0.10, 0.15, 0.25):
    print(f"    落在整数 ±{tol:.2f} 内: 原={(Xh['dist_before'] <= tol).mean():.1%}  "
          f"校正后={(Xh['dist_after'] <= tol).mean():.1%}")
print(f"\n  J_TOL={j['params']['J_TOL']} ⇒ 若校正后多数落 ±{j['params']['J_TOL']} 内，"
      f"则 X 类主因是 **u 标定偏差**而非旧锚")
print(f"\n  校正后 j 最近整数分布: "
      f"{Xh['j_uc'].round().value_counts().head(6).to_dict()}")
print(f"  校正后 j 分位: P5={Xh['j_uc'].quantile(.05):.3f} P50={Xh['j_uc'].median():.3f} "
      f"P95={Xh['j_uc'].quantile(.95):.3f}")
print(f"\n  X 类 r 分位: P5={X['r'].quantile(.05):.4f} P50={X['r'].median():.4f} "
      f"P95={X['r'].quantile(.95):.4f}（§九称 r_max 1.03~1.115）")