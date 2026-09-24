"""②R 类 / ③D 类 / ④NO_ADJ —— 只读取证（D-4：不回填）。

② R 类：两判据相左（方向）—— detail 中 B→A / A→C 的方向对。
   注意：detail.csv 为 X/D 复核子集，B→A 仅 1 行、A→C 0 行；
   §3.5 的 2,162 / 22 来自全量判据（未落 detail）⇒ 用可得载体定性：
     · json.agree_matrix 给全量计数（2,162 / 22）
     · codes.csv 的 R 列给逐码 R 计数（R = 两判据相左总数）
     · detail 中 X→A/X→B/X→C 是「主判据 X、字面判据 A/B/C」的交叉（≠ R）
③ D 类：detail 有全部 19 行（已在上轮取得）；补与 X 类机制的关联。
④ NO_ADJ：codes.csv / code_class_matrix 逐码 NO_ADJ 计数 → 定位因子表缺口规模与结构。
"""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
BB = Path(r"D:\miniQMT策略实盘\trading-battle-back")
BASE = BB / "logs"
STEM = "qfq_triage_etf_minutes_20260924_171935"
j = json.loads((BASE / f"{STEM}.json").read_text(encoding="utf-8"))
codes = pd.read_csv(BASE / f"{STEM}_codes.csv")
det = pd.read_csv(BASE / f"{STEM}_detail.csv")
det["pair"] = det["class_av"] + "->" + det["class_literal"]

# ---------- ② R 类 ----------
print("=" * 74)
print("② R 类（两判据相左）定性")
print("=" * 74)
am = j["agree_matrix"]
r_total = am["B->A"] + am["A->C"]
print(f"  §3.5 全量矩阵：B→A={am['B->A']:,}  A→C={am['A->C']:,}  ⇒ R={r_total:,}（呈报 2,184）")
print(f"  codes.csv 的 R 列：合计={int(codes['R'].sum()):,}  涉码={int((codes['R']>0).sum())}")
print(f"    R 取值分布（前 6）: {codes['R'].value_counts().head(6).to_dict()}")
rpos = codes[codes["R"] > 0].copy()
print(f"    R 行中：A 列>0 的码={int((rpos['A']>0).sum())}  B 列>0={int((rpos['B']>0).sum())} "
      f"C 列>0={int((rpos['C']>0).sum())}  X 列>0={int((rpos['X']>0).sum())}")
print(f"    R 与 X 的共现：R>0 且 X>0 的码={int(((rpos['X']>0)).sum())}/{len(rpos)}")
print(f"\n  detail 中可得的 R 方向载量："
      f"B->A={int((det['pair']=='B->A').sum())}  A->C={int((det['pair']=='A->C').sum())}"
      f"（与矩阵 2,162/22 差 {am['B->A']-1:,} / {am['A->C']:,}）—— detail 为 X/D 复核子集，非全量")
print(f"  矩阵内 B→A 的定性依据（§3.5）：amount/vol 判 B 而日线锚判 A ⇒ 分钟 close ≈ 日线 close"
      f" ⇒ **amount/vol 自身异常**，以日线锚为准 ⇒ 判 A ⇒ 不动（避免 2,162 例误改）")
print(f"\n  可得的独立佐证：json.codes_with_AC（{len(j['codes_with_AC'])} 码，含 A 与 C 并存）")
ac_codes = j["codes_with_AC"]
print(f"    前 10: {ac_codes[:10]}")
print(f"    这些码在 codes.csv 中的 R 合计={int(codes[codes['ts_code'].isin(ac_codes)]['R'].sum()):,}"
      f"（若其 R≈全部由 A↔C 贡献，则 R 的主体是 A/C 共存码）")

# ---------- ③ D 类 ----------
print("\n" + "=" * 74)
print("③ D 类（反向，19，全集中于 512250.SH）")
print("=" * 74)
D = det[det["class_final"] == "D"].copy()
print(f"  D 行数={len(D)}  涉码={D['ts_code'].unique().tolist()}")
if len(D):
    print(f"  f 分位: P5={D['f'].quantile(.05):.5f} P50={D['f'].median():.5f} "
          f"P95={D['f'].quantile(.95):.5f}  （>1 ⇒ close 高于 amount/vol）")
    print(f"  r 分位: P50={D['r'].median():.5f}  adj_day 取值={D['adj_day'].unique()[:4]}")
    print(f"  j_av 分位: P50={D['j_av'].median():.3f}（呈报 j≈-1）")
    D2 = D.copy()
    D2["implied_k"] = -D2["j_av"]
    print(f"  -j_av(k) 分位: P50={D2['implied_k'].median():.3f}")
    # 与 X 类同法拟合（该码 19 行）
    s = D2[(D2["r"] > 1) & (D2["f"] > 0)]
    if len(s) > 5:
        lr, lf = np.log(s["r"].values), np.log(s["f"].values)
        A = np.vstack([lr, np.ones_like(lr)]).T
        (k, c), *_ = np.linalg.lstsq(A, lf, rcond=None)
        pred = A @ np.array([k, c])
        r2 = 1 - (lf - pred).var() / lf.var() if lf.var() > 0 else np.nan
        print(f"  幂律拟合: k={k:.4f}(最近整数 {round(k)})  c={c:+.5f}  R²={r2:.6f}")
        print(f"  ⇒ 与 X 类同机制检验：k 近整数？{'是' if abs(k-round(k))<0.05 else '否'}  "
              f"c 与 X 类同量级（0.006~0.032）？{'是' if 0.005 < abs(c) < 0.04 else '否'}")
    print(f"  逐行（f / close / avg_px / daily_close）:")
    print(D[["date", "n", "f", "close", "avg_px", "daily_close", "j_av"]].head(8).to_string(index=False))
    print(f"  该码在 codes.csv 的行：")
    print(codes[codes["ts_code"] == "512250.SH"].to_string(index=False))

# ---------- ④ NO_ADJ ----------
print("\n" + "=" * 74)
print("④ NO_ADJ 因子表缺口定性")
print("=" * 74)
ccm = j["code_class_matrix"]
no = [(c, v["NO_ADJ"]) for c, v in ccm.items() if v.get("NO_ADJ")]
no.sort(key=lambda x: -x[1])
print(f"  含 NO_ADJ 的码数={len(no)}  合计={sum(n for _, n in no):,}")
print(f"  TOP12: {no[:12]}")
vals = np.array([n for _, n in no])
print(f"  逐码 NO_ADJ 量分位: P10={np.percentile(vals,10):.0f} P50={np.median(vals):.0f} "
      f"P90={np.percentile(vals,90):.0f} max={vals.max()}")
print(f"  ⇒ 逐码量级 ≈ 300（≈ 该码在窗口内的交易日数级）⇒ 缺口的形态是**整码级**而非零星")
print(f"  codes.csv NO_ADJ 列（口径不同，多为 1 = 该码有缺口标记）："
      f"非零码={int((codes['NO_ADJ']>0).sum())}  合计={int(codes['NO_ADJ'].sum())}")

# 交叉：主库有无 etf_adj_factor；若有则实测缺口
con = duckdb.connect(str(ROOT / "data" / "quantstudio.db"), read_only=True)
tabs = [r[0] for r in con.execute(
    "SELECT table_name FROM information_schema.tables ORDER BY 1").fetchall()]
adjt = [t for t in tabs if "adj" in t.lower()]
print(f"\n  主库含 adj 的表={adjt}（上轮已核；辅助库 qfq_aux.db 需另连）")
con.close()
aux = ROOT / "data" / "qfq_aux.db"
if aux.exists():
    c2 = duckdb.connect(str(aux), read_only=True)
    t2 = [r[0] for r in c2.execute(
        "SELECT table_name FROM information_schema.tables ORDER BY 1").fetchall()]
    print(f"  qfq_aux.db 表={t2}")
    for t in t2:
        if "adj" in t.lower():
            cols = [r[1] for r in c2.execute(f'PRAGMA table_info("{t}")').fetchall()]
            n = c2.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            nc = c2.execute(f'SELECT COUNT(DISTINCT code) FROM "{t}"').fetchone()[0]
            print(f"    {t}: 列={cols} 行数={n:,} 码数={nc}")
            # NO_ADJ TOP 码是否在因子表中
            top_codes = [c for c, _ in no[:12]]
            cc = "code" if "code" in cols else "ts_code"
            present = c2.execute(
                f'SELECT COUNT(DISTINCT {cc}) FROM "{t}" WHERE {cc} IN '
                f'({",".join(["?"]*len(top_codes))})', top_codes).fetchone()[0]
            print(f"      NO_ADJ TOP12 码在该表中命中={present}/{len(top_codes)}"
                  f" ⇒ {'缺口=表中确无该码' if present == 0 else '部分在表（缺口为日期级）'}")
    c2.close()