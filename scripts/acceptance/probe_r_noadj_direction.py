"""R 类定向核对 + ④ 缺口方向核查（只读）。

目的：
 1. detail.csv 究竟覆盖哪些 ts_code？R 类 12 码是否在内？若不在，如实说明载体边界。
 2. R 类 12 码是谁、各自 R 量级、与 A/C 共存的关系。
 3. ④ NO_ADJ 缺口方向：这些码在 adj_factor/fund_adj 中**完全不存在**，
    还是存在但某些日期缺失？（用 code 是否出现 + 是否有该日期附近记录判定）
"""
import json
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
BASE = Path(r"D:\miniQMT策略实盘\trading-battle-back\logs")
STEM = "qfq_triage_etf_minutes_20260924_171935"
j = json.loads((BASE / f"{STEM}.json").read_text(encoding="utf-8"))
codes = pd.read_csv(BASE / f"{STEM}_codes.csv")
det = pd.read_csv(BASE / f"{STEM}_detail.csv")

print("=" * 74)
print("R 类定向核对")
print("=" * 74)
rpos = codes[codes["R"] > 0].sort_values("R", ascending=False)
print(f"  codes.csv 中 R>0 的码（{len(rpos)} 只，R 合计={int(rpos['R'].sum()):,}）：")
print(rpos.to_string(index=False))
det_codes = set(det["ts_code"].unique())
print(f"\n  detail.csv 覆盖 ts_code 数={len(det_codes)}")
print(f"  R>0 的码在 detail 中命中={len(set(rpos['ts_code']) & det_codes)}/{len(rpos)}")
print(f"  ⇒ detail 的覆盖是否为**全量可判码**？codes.csv 有可判 code-day 的码="
      f"{int(((codes[['A','B','C','D','X']].sum(axis=1)) > 0).sum())}")
miss = sorted(set(rpos["ts_code"]) - det_codes)
print(f"  未见于 detail 的 R 码：{miss[:20]}")
print(f"\n  对照：A>0 的码数={int((codes['A']>0).sum())}，其中在 detail 中="
      f"{len(set(codes[codes['A']>0]['ts_code']) & det_codes)}")

print("\n" + "=" * 74)
print("④ NO_ADJ 缺口方向核查（因子表命中）")
print("=" * 74)
ccm = j["code_class_matrix"]
no = sorted([(c, v["NO_ADJ"]) for c, v in ccm.items() if v.get("NO_ADJ")],
            key=lambda x: -x[1])
top = [c for c, _ in no[:12]]
allcodes = [c for c, _ in no]
aux = ROOT / "data" / "qfq_aux.db"
con = duckdb.connect(str(aux), read_only=True)
for t in ("adj_factor", "fund_adj", "adj_factor_snapshot"):
    cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
    cc = "code" if "code" in cols else "ts_code"
    for lbl, lst in (("TOP12", top), ("全部 167 码", allcodes)):
        q = (f'SELECT COUNT(DISTINCT {cc}) FROM "{t}" WHERE {cc} IN '
             f'({",".join(["?"] * len(lst))})')
        got = con.execute(q, lst).fetchone()[0]
        print(f"  {t}.{cc}: {lbl} 命中={got}/{len(lst)}")
# 抽样：这些码在分钟表有数据，但因子表首/末时间范围
print(f"\n  因子表覆盖的时间范围（抽样检查）:")
for t, cc in (("adj_factor", "code"), ("fund_adj", "code")):
    r = con.execute(f'SELECT MIN(time), MAX(time) FROM "{t}"').fetchone()
    print(f"    {t}: time ∈ [{r[0]}, {r[1]}]")
con.close()

# 这些码是否在 etf_basic（说明是有效 ETF）
d2 = duckdb.connect(str(ROOT / "data" / "quantstudio.db"), read_only=True)
basic = set(x[0] for x in d2.execute("SELECT code FROM etf_basic").fetchall())
print(f"\n  etf_basic 码数={len(basic)}")
print(f"  NO_ADJ 全部 167 码在 etf_basic 中命中={len(set(allcodes) & basic)}/167")
print(f"  NO_ADJ TOP12 命中={len(set(top) & basic)}/12")
for c in top[:6]:
    row = d2.execute(
        "SELECT code, name, list_date, delist_date, status FROM etf_basic "
        "WHERE code = ?", [c]).fetchone()
    print(f"    {c}: {row}")
d2.close()