"""验收②（定稿）：把「因子归一消除的误拒」与「真实数据异常」分离计量。

分解（对每个样本行，比值 r_raw = amount/(raw_close×vol)，归一因子 f = adj_latest/adj_i）：
  旧行为判拒 ⇔ r_raw ∉ [0.5, 2.0]
  新行为判拒 ⇔ r_raw×f ∉ [0.5, 2.0]
交叉表给出四类：
  A 归一后放行（旧拒→新放行）  = 归一消除的**误拒**（本批修复目标）
  B 新旧都拒                   = 真实异常（不因归一而漏）
  C 新旧都放行                 = 正常行
  D 归一后新增拒（旧放行→新拒）= **净增真阳性或过度归一**（需归因，目标应为 0 或真异常）
"""
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(ROOT))
from quantstudio.pipeline.mcp.client import MCPClient, load_mcp_api_key  # noqa: E402
import sqlite3

key = load_mcp_api_key()
cli = MCPClient(endpoint="https://124.223.159.234/mcp", api_key=key)
cli.handshake()

c = sqlite3.connect(f"file:{ROOT / 'data' / 'qfq_aux.db'}?mode=ro", uri=True, timeout=60)
LAT = {}
for code, adj in c.execute(
        "SELECT a.code, a.adj_factor FROM fund_adj a "
        "JOIN (SELECT code, MAX(time) mt FROM fund_adj GROUP BY code) m "
        "ON a.code=m.code AND a.time=m.mt"):
    if adj is not None and float(adj) > 0:
        LAT[str(code)] = float(adj)
c.close()


def pull(ts, te, limit=300_000):
    ref = cli.create_export_job("qdb.etf_minutes", page_size=50_000,
                                time_start=ts, time_end=te, row_limit=limit)
    man = cli.get_manifest(ref)
    return pd.concat([pd.read_parquet(io.BytesIO(
        cli.get_artifact(ref, f"{ref}/{s.shard_id}").parquet_bytes))
        for s in man.shards], ignore_index=True)


def analyze(label, ts, te):
    d = pull(ts, te)
    d["bare"] = d["ts_code"].astype(str).str.split(".").str[0]
    d["adj_latest"] = d["bare"].map(LAT)
    ok = (d["adj_latest"].notna() & (d["adj_factor"] > 0)
          & (d["vol"] > 0) & (d["close"] > 0) & (d["amount"] > 0)).to_numpy()
    k = d["adj_i"] = d["adj_factor"].to_numpy(dtype=float)
    f = (d["adj_latest"].to_numpy(dtype=float) / k)
    # 客户端还原后的 raw close = qfq_close × f
    raw = d["close"].to_numpy(dtype=float) * f
    r_raw = d["amount"].to_numpy(dtype=float) / (raw * d["vol"].to_numpy(dtype=float))
    r_norm = r_raw * f
    old_bad = ok & ~((r_raw >= 0.5) & (r_raw <= 2.0))
    new_bad = ok & ~((r_norm >= 0.5) & (r_norm <= 2.0))
    B_mask = old_bad & new_bad
    A = int((old_bad & ~new_bad).sum())
    B = int(B_mask.sum())
    C = int((~old_bad & ~new_bad).sum())
    D = int((~old_bad & new_bad).sum())
    n = int(ok.sum())
    print(f"\n### {label}  n={n}")
    print(f"  旧拒 {int(old_bad.sum())} ({old_bad.sum()/n:.4%})   "
          f"新拒 {int(new_bad.sum())} ({new_bad.sum()/n:.4%})")
    print(f"  A 归一消除的误拒 = {A} ({A/n:.4%})")
    print(f"  B 新旧都拒（真实异常）= {B}")
    print(f"  C 正常 = {C}")
    print(f"  D 归一后新增拒 = {D}")
    if D:
        idx = np.nonzero(~old_bad & new_bad)[0][:6]
        sub = d.iloc[idx]
        r = pd.DataFrame({
            "ts_code": sub["ts_code"].to_numpy(),
            "trade_time": sub["trade_time"].astype(str).to_numpy(),
            "close": sub["close"].to_numpy(), "vol": sub["vol"].to_numpy(),
            "amount": sub["amount"].to_numpy(),
            "adj_i": sub["adj_factor"].to_numpy(),
            "adj_latest": sub["adj_latest"].to_numpy(),
            "r_raw": r_raw[idx], "f": f[idx], "r_norm": r_norm[idx]})
        print("  D 类样例：")
        print(r.to_string())
        m = ~old_bad & new_bad
        print("  D 类 r_raw 分位: " + str({q: round(float(np.quantile(r_raw[m], q)), 4)
                                       for q in (0.01, 0.25, 0.5, 0.75, 0.99)}))
        print("  D 类 f 分位: " + str({q: round(float(np.quantile(f[m], q)), 4)
                                      for q in (0.01, 0.25, 0.5, 0.75, 0.99)}))
        print("  D 类涉及 code 数 =", int(pd.Series(d.iloc[np.nonzero(m)[0]]["bare"]).nunique()))
    if B:
        sub = d.iloc[np.nonzero(B_mask)[0]]
        rr = r_raw[B_mask]
        print(f"  B 类 r_raw 分布: 中位={np.median(rr):.4f} "
              f"min={rr.min():.4f} max={rr.max():.4f}")
        print(f"  B 类涉及 code 数 = {sub['bare'].nunique()}")
    return dict(n=n, old=int(old_bad.sum()), new=int(new_bad.sum()), A=A, B=B, C=C, D=D)


res = {}
res["2025-06"] = analyze("2025-06-02 窗（客户窗口外·旧数据）", "2025-06-02", "2025-06-04")
res["2026-01"] = analyze("2026-01-05 窗（客户窗口内·拒绝率最高时段）", "2026-01-05", "2026-01-08")
res["2026-09"] = analyze("2026-09-21 窗（最新时段·对照）", "2026-09-21", "2026-09-22")

print("\n=== 汇总 ===")
tot_old = sum(v["old"] for v in res.values())
tot_new = sum(v["new"] for v in res.values())
tot_A = sum(v["A"] for v in res.values())
tot_D = sum(v["D"] for v in res.values())
print(f"三项样本：旧拒合计 {tot_old} → 新拒合计 {tot_new}；归一消除误拒 A={tot_A}；净增 D={tot_D}")
print(f"误拒消除率 = {tot_A/max(tot_old,1):.2%}")