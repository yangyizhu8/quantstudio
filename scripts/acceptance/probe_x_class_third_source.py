"""① X 类第三源交叉验证：etf_adj_factor 锚演进（确定性证据）。

检验 T5：X 类是否与因子表的「锚跳变/异常」结构相关。
  · 对 X 码与对照码，各取该码在窗口内的 adj_factor 序列
  · 找跳变（相邻因子相对变化 > 阈值）与因子取值个数、量级
  · 若 X 码普遍具备「多次跳变 / 大倍率跳变」而对照码平稳 ⇒ 支持锚演进类解释
"""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
BB = Path(r"D:\miniQMT策略实盘\trading-battle-back")
DB = ROOT / "data" / "quantstudio.db"

# 因子表候选（主库/辅助库）
con = duckdb.connect(str(DB), read_only=True)
tabs = [r[0] for r in con.execute(
    "SELECT table_name FROM information_schema.tables ORDER BY 1").fetchall()]
print("含 adj 的表:", [t for t in tabs if "adj" in t.lower()])

X_CODES = ["513530.SH", "159581.SZ", "563020.SH", "515080.SH", "159307.SZ",
           "513950.SH", "159333.SZ", "511260.SH", "159209.SZ", "513820.SH"]

for t in ("etf_adj_factor", "etf_basic"):
    if t not in tabs:
        continue
    cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
    print(f"\n=== {t} 列: {cols}")
    if t == "etf_adj_factor":
        n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        ncodes = con.execute(f'SELECT COUNT(DISTINCT code) FROM "{t}"').fetchone()[0] \
            if "code" in cols else None
        print(f"  行数={n:,}  码数={ncodes}")
        tcol = next((c for c in ("trade_date", "date", "time") if c in cols), None)
        ccol = "code" if "code" in cols else ("ts_code" if "ts_code" in cols else None)
        fcol = next((c for c in ("adj_factor", "factor", "adj") if c in cols), None)
        print(f"  时间列={tcol} 代码列={ccol} 因子列={fcol}")
        # 检查 X 码是否在表中
        present = con.execute(
            f'SELECT DISTINCT {ccol} FROM "{t}" WHERE {ccol} IN '
            f'({",".join(["?"] * len(X_CODES))})', X_CODES).fetchall()
        print(f"  X 码在表中命中={len(present)}/{len(X_CODES)}: "
              f"{[p[0] for p in present][:10]}")
        # 因子序列统计
        print(f"\n  逐码因子形态（X 码）：")
        for c in X_CODES[:6]:
            try:
                rows = con.execute(
                    f'SELECT {tcol}, {fcol} FROM "{t}" WHERE {ccol} = ? '
                    f'ORDER BY {tcol}', [c]).fetchall()
                if not rows:
                    print(f"    {c}: 无因子记录")
                    continue
                f = np.array([float(r[1]) for r in rows if r[1] is not None])
                nuniq = len(set(np.round(f, 6)))
                # 跳变检测（相对变化 > 1%）
                rel = np.abs(np.diff(f) / f[:-1]) if len(f) > 1 else np.array([])
                jumps = int((rel > 0.01).sum())
                big = int((rel > 0.10).sum())
                print(f"    {c}: n={len(f)} 唯一值={nuniq} f范围=[{f.min():.4f},{f.max():.4f}] "
                      f"跳变(>1%)={jumps} 大跳变(>10%)={big} "
                      f"最大相对跳变={rel.max():.2%}" if len(rel) else
                      f"    {c}: n={len(f)} 无跳变")
            except Exception as e:
                print(f"    {c}: 查询失败 {type(e).__name__}: {str(e)[:60]}")
con.close()

# X 类码的 r_max 与码级统计（来自 codes.csv）
BASE = BB / "logs"
codes = pd.read_csv(BASE / "qfq_triage_etf_minutes_20260924_171935_codes.csv")
print("\n=== X 码在有可判码池中的整体位置 ===")
xc = codes[codes["X"] > 0].copy()
print(f"  有 X 的码数={len(xc)}  X 行合计={int(xc['X'].sum()):,}")
print(f"  X 码 r_max 分位: P5={xc['r_max'].quantile(.05):.4f} "
      f"P50={xc['r_max'].median():.4f} P95={xc['r_max'].quantile(.95):.4f}")
print(f"  对照（无 X 码）r_max P50={codes[codes['X'] == 0]['r_max'].median():.4f}")
print(f"  X 码同时含 A/B/C 的情况（前 8）：")
print(xc[["ts_code", "A", "B", "C", "D", "X", "SUB", "N", "NO_ADJ", "r_max"]]
      .sort_values("X", ascending=False).head(8).to_string(index=False))