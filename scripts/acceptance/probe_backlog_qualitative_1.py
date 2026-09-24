"""挂账定性批 · ①X 类 / ②R 类 / ③D 类 / ④NO_ADJ —— 只读取证（D-4 口径：不回填）。

判据来源：trading-battle-back/docs/minutes-qfq-apply-count-repair-plan-20260924.md
  §2.1 符号表 + 判据链：f_m = close/(amount/vol)；j = -ln(f_m/u)/ln(r)；r = latest_adj/adj_day
  分类：j≈1→A / j≈0→B / j≈2→C / j≈-1→D / 非整数→X
工件：logs/qfq_triage_etf_minutes_20260924_171935.{json,codes.csv,detail.csv}
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

BB = Path(r"D:\miniQMT策略实盘\trading-battle-back")
BASE = BB / "logs"
STEM = "qfq_triage_etf_minutes_20260924_171935"

j = json.loads((BASE / f"{STEM}.json").read_text(encoding="utf-8"))
codes = pd.read_csv(BASE / f"{STEM}_codes.csv")
det = pd.read_csv(BASE / f"{STEM}_detail.csv")
print(f"detail 行数={len(det):,}  codes={len(codes)}  u={j['u']}  params={j['params']}")

# ① X 类
print("\n" + "=" * 74)
print("① X 类（非整数 j，4,965 code-day）—— 疑 QFQ 旧锚，定性")
print("=" * 74)
X = det[det["class_final"] == "X"].copy()
print(f"  实测 X 行数={len(X):,}（呈报 4,965）")
print(f"  涉码数={X['ts_code'].nunique()}（呈报 ~33）")
X["frac"] = X["j_av"] - X["j_av"].round()
print(f"  j_av 分位: P5={X['j_av'].quantile(.05):.3f} P50={X['j_av'].median():.3f} "
      f"P95={X['j_av'].quantile(.95):.3f}")
print(f"  (j - round(j)) 分位: P5={X['frac'].quantile(.05):.3f} P50={X['frac'].median():.3f} "
      f"P95={X['frac'].quantile(.95):.3f}")
r_gt = X["j_av"].round()
print(f"  最近整数分布: {r_gt.value_counts().head(8).to_dict()}")
print(f"  r 分位: P5={X['r'].quantile(.05):.4f} P50={X['r'].median():.4f} "
      f"P95={X['r'].quantile(.95):.4f}  max={X['r'].max():.4f}")
# 关键检验：若为「旧锚」⇒ f 应稳定等于某常数（旧锚比值）；若为噪声 ⇒ f 离散
print(f"  f 分位: P5={X['f'].quantile(.05):.4f} P50={X['f'].median():.4f} "
      f"P95={X['f'].quantile(.95):.4f}")
# 逐码：j 稳定度（同码内 j 的变异）——稳定=真实分数次施加；不稳定=数值噪声
g = X.groupby("ts_code")["j_av"].agg(["size", "median", "std", "min", "max"])
g["spread"] = g["max"] - g["min"]
print(f"\n  逐码 j 稳定度（n≥30 的码）：")
gs = g[g["size"] >= 30].sort_values("size", ascending=False)
print(f"    码数={len(gs)}  spread 中位={gs['spread'].median():.3f} "
      f"P90={gs['spread'].quantile(.90):.3f}  max={gs['spread'].max():.3f}")
print("    前 12 码：")
print(gs.head(12).to_string())
# 是否 j≈1 但 f 偏低（近整数噪声）vs j 真分数
X["near_int"] = (X["frac"].abs() <= 0.1) | (X["frac"].abs() >= 0.9)
print(f"\n  X 中「j 近整数(±0.1)」行数={int(X['near_int'].sum()):,} "
      f"（占 {X['near_int'].mean():.1%}）⇒ 这部分疑为**噪声/精度**而非真分数次施加")
print(f"  X 中「j 真分数(离整数>0.1)」行数={int((~X['near_int']).sum()):,}")

# 单个码深查（159119.SZ：样例出现 j≈1.44）
for code in ["159119.SZ"]:
    s = X[X["ts_code"] == code].sort_values("date")
    if len(s):
        print(f"\n  【{code}】n={len(s)} 日期 {s['date'].min()} ~ {s['date'].max()}")
        print(f"    adj_day 取值={sorted(s['adj_day'].unique())[:5]}  "
              f"latest_adj={s['latest_adj'].iloc[0]}")
        print(f"    f: 中位={s['f'].median():.6f} std={s['f'].std():.6f} "
              f"（若为旧锚应恒定）")
        print(f"    j_av: 中位={s['j_av'].median():.4f} std={s['j_av'].std():.4f}")
        print(f"    理论 f(若 j=1)={1/s['r'].median():.6f}  实测 f 中位={s['f'].median():.6f}  "
              f"相对偏差={(s['f'].median()*s['r'].median()-1)*100:+.3f}%")
        print(s[["date", "n", "f", "r", "j_av", "close", "daily_close", "avg_px"]]
              .head(6).to_string(index=False))

# ② R 类
print("\n" + "=" * 74)
print("② R 类（两判据相左，2,184）—— B→A 2,162 疑 amount/vol 自身异常，抽样定性")
print("=" * 74)
R = det[det["class_av"] != det["class_literal"]].copy()
print(f"  detail 中「两判据相左」行数={len(R):,}（呈报 2,184）")
print(f"  matrix={j['agree_matrix']}")
ba = det[(det["class_av"] == "B") & (det["class_literal"] == "A")].copy()
print(f"  B→A 实测行数={len(ba):,}（呈报 2,162）  涉码={ba['ts_code'].nunique()}")
ac = det[(det["class_av"] == "A") & (det["class_literal"] == "C")].copy()
print(f"  A→C 实测行数={len(ac):,}（呈报 22）  涉码={ac['ts_code'].nunique()}")
if len(ba):
    ba["close_vs_daily"] = ba["close"] / ba["daily_close"]
    ba["close_vs_avg"] = ba["close"] / ba["avg_px"]
    print(f"\n  B→A 行：close/daily_close 中位={ba['close_vs_daily'].median():.5f} "
          f"≈1 占比={(ba['close_vs_daily'].between(0.995,1.005)).mean():.2%}")
    print(f"          close/avg_px   中位={ba['close_vs_avg'].median():.5f} "
          f"≈1 占比={(ba['close_vs_avg'].between(0.995,1.005)).mean():.2%}")
    print(f"          f 中位={ba['f'].median():.5f}  r 中位={ba['r'].median():.5f} "
          f"j_av 中位={ba['j_av'].median():.4f} j_daily 中位={ba['j_daily'].median():.4f}")
    print(f"  ⇒ 判定链：日线锚说 A（close≈日线 close，比 {ba['close_vs_daily'].median():.5f}）")
    # r 分布：若 r 极小 ⇒ f 的微小误差被放大 → amount/vol 侧噪声
    print(f"  B→A 的 r 分位: P10={ba['r'].quantile(.10):.5f} P50={ba['r'].median():.5f} "
          f"P90={ba['r'].quantile(.90):.5f}")
    print(f"  B→A 的 (r-1) 分位: P50={(ba['r'].median()-1):.5f} "
          f"（分类阈值 R_MIN_CLASSIFY={j['params']['R_MIN_CLASSIFY']}）")
    print(f"\n  B→A 涉码 TOP8（按行数）:")
    print(ba["ts_code"].value_counts().head(8).to_string())
    print(f"\n  B→A 抽样 6 行（逐列）:")
    print(ba[["ts_code", "date", "n", "f", "r", "j_av", "j_daily", "class_av",
              "class_literal", "close", "avg_px", "daily_close"]].head(6).to_string(index=False))

# ③ D 类
print("\n" + "=" * 74)
print("③ D 类（反向，19）—— 512250 整码挂起")
print("=" * 74)
D = det[det["class_final"] == "D"].copy()
print(f"  D 行数={len(D)}  涉码={D['ts_code'].unique().tolist()}")
if len(D):
    print(D[["ts_code", "date", "n", "f", "r", "j_av", "class_av", "class_literal",
             "close", "avg_px", "daily_close"]].to_string(index=False))

# ④ NO_ADJ
print("\n" + "=" * 74)
print("④ NO_ADJ 因子表缺口定性（另一缺陷族）")
print("=" * 74)
no = codes[codes["NO_ADJ"] > 0].copy()
print(f"  涉码数={len(no)}/  {len(codes)}（有可判码）")
print(f"  NO_ADJ 合计（若逐码 1 计）={int(codes['NO_ADJ'].sum())}")
print(f"  各码 NO_ADJ 取值分布={codes['NO_ADJ'].value_counts().head(6).to_dict()}")
print(f"\n  注：codes.csv 的 NO_ADJ 是**逐码计数**（多为 1），而 §九 V-4 列的是**code-day 量**"
      f"（159320.SZ 314 / 159398.SZ 290 / 588760.SH 308）")
print(f"  ⇒ 两者口径不同：code-day 级缺口需从 code_class_matrix 或另源取")
ccm = j["code_class_matrix"]
no_rows = [(c, v.get("NO_ADJ", 0)) for c, v in ccm.items() if v.get("NO_ADJ")]
print(f"  code_class_matrix 中含 NO_ADJ 的码={len(no_rows)}  合计="
      f"{sum(n for _, n in no_rows)}  TOP8={sorted(no_rows, key=lambda x: -x[1])[:8]}")