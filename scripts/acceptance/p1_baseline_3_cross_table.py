"""P1 基准③ 收口：云端日表 amount/vol 与分钟表对照（判定两表内部一致性）。

已得（2025-06-03，159327.SZ）：
  云端 etf_daily close = 0.398   ≈ tdx 前复权 0.40（比值 0.9950）  adj=1.0
  云端 etf_minutes close 中位 = 0.1330 ; amount/vol 中位 = 1.1930 ; close/(amount/vol)=0.1112
  ⇒ 两表 close 相差 ~3×（0.398 vs 0.133）

本步补：云端日表 amount/vol，判定「两表 amount/vol 是否一致」——
  一致 ⇒ 两表 amount/vol 同口径，分歧只在 close 的口径（用户侧无从修正，属云端两表口径分裂）
  不一致 ⇒ 进一步定位。
"""
import io
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(ROOT))
from quantstudio.pipeline.mcp.client import MCPClient, load_mcp_api_key  # noqa: E402

key = load_mcp_api_key()
cli = MCPClient(endpoint="https://124.223.159.234/mcp", api_key=key)
cli.handshake()


def pull(ds, ts, te, limit=300_000):
    ref = cli.create_export_job(ds, page_size=50_000,
                                time_start=ts, time_end=te, row_limit=limit)
    man = cli.get_manifest(ref)
    if not man.shards:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(io.BytesIO(
        cli.get_artifact(ref, f"{ref}/{s.shard_id}").parquet_bytes))
        for s in man.shards], ignore_index=True)


DAY, NXT = "2025-06-03", "2025-06-04"
d = pull("qdb.etf_daily", DAY, NXT)
d["bare"] = d["ts_code"].astype(str).str.split(".").str[0]
r = d[d["bare"] == "159327"]
print("=== 云端 etf_daily 2025-06-03 · 159327.SZ ===")
if len(r):
    r = r.iloc[0]
    traded_d = float(r["amount"]) / float(r["vol"]) if r["vol"] else float("nan")
    print(f"  close={r['close']}  pre_close={r['pre_close']}  pct_chg={r['pct_chg']}")
    print(f"  amount={r['amount']}  vol={r['vol']}  amount/vol={traded_d:.4f}")
    print(f"  close/(amount/vol)={float(r['close'])/traded_d:.4f}")
    print(f"  adj_factor={r['adj_factor']}  is_qfq={r['is_qfq']}")
else:
    print("  无该码")

# 全样本（当日所有 ETF）：两表 amount/vol 口径是否一致 + 日表 close 是否 = amount/vol
print("\n=== 当日全样本：日表内部关系 ===")
d = d[(d["vol"] > 0) & (d["close"] > 0) & (d["amount"] > 0)]
traded = d["amount"] / d["vol"]
print(f"  n={len(d)}")
print(f"  日表 close/(amount/vol): 中位={(d['close']/traded).median():.4f}  "
      f"≈1 占比={(d['close']/traded).between(0.99,1.01).mean():.4%}")
print(f"  日表 close/(amount/vol) 分位: "
      f"P10={ (d['close']/traded).quantile(0.10):.4f} "
      f"P50={ (d['close']/traded).quantile(0.50):.4f} "
      f"P90={ (d['close']/traded).quantile(0.90):.4f}")
print(f"  adj_factor 种类数={d['adj_factor'].nunique()}")

print("\n=== 同码两表 close 比值分布（判定 3× 是否为普遍现象）===")
m = pull("qdb.etf_minutes", DAY, NXT, 300_000)
m = m[(m["vol"] > 0) & (m["close"] > 0)]
m["bare"] = m["ts_code"].astype(str).str.split(".").str[0]
mg_m = m.groupby("bare").agg(m_close=("close", "median"),
                             m_adj=("adj_factor", "first"))
mg_d = d.set_index("bare")[["close", "adj_factor"]].rename(
    columns={"close": "d_close", "adj_factor": "d_adj"})
j = mg_d.join(mg_m, how="inner")
j = j[(j["d_close"] > 0) & (j["m_close"] > 0)]
j["ratio"] = j["m_close"] / j["d_close"]
print(f"  可比码数={len(j)}")
print(f"  minute_close/daily_close 分位: P10={j['ratio'].quantile(0.10):.4f} "
      f"P50={j['ratio'].median():.4f} P90={j['ratio'].quantile(0.90):.4f}")
print(f"  ≈1（±1%）占比={(j['ratio'].between(0.99,1.01)).mean():.4%}")
print(f"  比值≈1/3（±5%）占比={(j['ratio'].between(0.317,0.35)).mean():.4%}")
print(f"  两表 adj 一致占比={(j['m_adj']==j['d_adj']).mean():.4%}")
print("\n  样例（前 8）：")
print(j.head(8).to_string())