"""定论：全样本统计判定还原乘数方向（H_dir = adj_i/adj_latest，H_inv = adj_latest/adj_i）。

不依赖任何外部行情。判据：amount/vol 恒为真实可成交价（复权不变量）。
对每行计算两个候选乘数下的「还原后 close 是否等于 traded」：
    c_dir = close × (adj_i/adj_latest) / traded
    c_inv = close × (adj_latest/adj_i) / traded
若 c_dir 全域紧贴 1.0 且 c_inv 偏离，即定谳方向应为 adj_i/adj_latest。
"""
import io
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(ROOT))
from quantstudio.pipeline.mcp.client import MCPClient, load_mcp_api_key  # noqa: E402

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


for label, ts, te in (("2025-06", "2025-06-02", "2025-06-05"),
                      ("2026-01", "2026-01-05", "2026-01-08"),
                      ("2026-09", "2026-09-21", "2026-09-22")):
    d = pull(ts, te)
    d["bare"] = d["ts_code"].astype(str).str.split(".").str[0]
    d["adj_latest"] = d["bare"].map(LAT)
    m = ((d["vol"] > 0) & (d["close"] > 0) & (d["amount"] > 0)
         & d["adj_latest"].notna() & (d["adj_factor"] > 0)).to_numpy()
    sub = d[m]
    traded = (sub["amount"] / sub["vol"]).to_numpy(dtype=float)
    close = sub["close"].to_numpy(dtype=float)
    ai = sub["adj_factor"].to_numpy(dtype=float)
    al = sub["adj_latest"].to_numpy(dtype=float)
    c_dir = close * (ai / al) / traded        # 假设乘数 = adj_i/adj_latest
    c_inv = close * (al / ai) / traded        # 假设乘数 = adj_latest/adj_i
    print(f"\n### {label}  n={len(sub)}")
    for nm, v in (("c_dir(adj_i/adj_latest)", c_dir), ("c_inv(adj_latest/adj_i)", c_inv)):
        print(f"  {nm}: 中位={np.nanmedian(v):.6f}  "
              f"近1.0(±1%)占比={np.mean(np.abs(v-1) < 0.01):.4%}  "
              f"分位 "
              f"{{1%: {np.nanquantile(v,0.01):.4f}, 50%: {np.nanmedian(v):.4f}, "
              f"99%: {np.nanquantile(v,0.99):.4f}}}")
    # 逐 code 判定（避免少数码拉偏）：按「还原后 close == traded」判两方向
    g = pd.DataFrame({"bare": sub["bare"].to_numpy(), "cd": c_dir, "ci": c_inv})
    agg = g.groupby("bare").agg(cd=("cd", "median"), ci=("ci", "median"),
                                n=("cd", "size"))
    print(f"  逐 code 中位：c_dir 近 1.0 的 code 数 = "
          f"{int((abs(agg['cd']-1)<0.02).sum())}/{len(agg)}"
          f" ；c_inv 近 1.0 的 code 数 = "
          f"{int((abs(agg['ci']-1)<0.02).sum())}/{len(agg)}")
    # 日内判据（与 close 口径无关的强判据）：
    #   close / traded 应等于 adj_i/adj_latest（若 close 是 qfq）
    #   close / traded 应等于 1           （若 close 已是 raw）
    ratio_ct = (sub["close"].to_numpy(dtype=float) / traded)
    pred_ct = ai / al
    print(f"  close/traded 中位={np.nanmedian(ratio_ct):.6f}；"
          f"adj_i/adj_latest 中位={np.nanmedian(pred_ct):.6f}；"
          f"两者相等行占比={np.mean(np.abs(ratio_ct-pred_ct) < 1e-3):.4%}")
    print(f"  close/traded ≈ 1（即 close 已是 raw）行占比="
          f"{np.mean(np.abs(ratio_ct-1) < 0.01):.4%}")