"""P1 勘误取证：回应云答复三点复核（我方证据自查）。

点2（容差口径差）：核对我方 ±1% 与云端 ±5% 容差下的占比是否自洽（代数可解释性）
点3（取数口径差异）：回查我方 2025-06-03 etf_daily 取数 —— 实际 n、去重/停牌/退市口径
点1（单位差 vs 口径分裂）：核查「~10×」是否可由单位换算解释
"""
import io
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


def pull(ds, ts, te, limit=None):
    ref = cli.create_export_job(ds, page_size=50_000,
                                time_start=ts, time_end=te, row_limit=limit)
    man = cli.get_manifest(ref)
    if not man.shards:
        return pd.DataFrame(), man
    return pd.concat([pd.read_parquet(io.BytesIO(
        cli.get_artifact(ref, f"{ref}/{s.shard_id}").parquet_bytes))
        for s in man.shards], ignore_index=True), man


DAY, NXT = "2025-06-03", "2025-06-04"

print("=" * 70)
print("点3【取数口径核查】我方 2025-06-03 etf_daily 取数明细")
print("=" * 70)
d, man = pull("qdb.etf_daily", DAY, NXT)
print(f"  manifest: total_rows={man.total_rows}  shard_count={man.shard_count}")
print(f"  实际解出 DataFrame 行数 = {len(d)}")
print(f"  唯一 ts_code 数 = {d['ts_code'].nunique()}")
if len(d):
    print(f"  trade_date 取值数 = {d['trade_date'].nunique()}")
    print(f"  时间范围 = {d['trade_date'].min()} ~ {d['trade_date'].max()}")
    dup = d.duplicated(subset=["ts_code"]).sum()
    print(f"  重复 ts_code 行数 = {dup}")
    # 有效行（按我方基准②③所用过滤）
    m = (d["vol"] > 0) & (d["close"] > 0) & (d["amount"] > 0)
    print(f"  我方统计所用过滤 (vol>0 & close>0 & amount>0) 后行数 = {int(m.sum())}")
    # 各类零值分布
    print(f"    vol=0 行数 = {int((d['vol'] == 0).sum())}")
    print(f"    amount=0 行数 = {int((d['amount'] == 0).sum())}")
    print(f"    close=0 行数 = {int((d['close'] == 0).sum())}")
    print(f"    pre_close=0 行数 = {int((d['pre_close'] == 0).sum())}")
    print(f"    adj_factor 为 NaN 行数 = {int(d['adj_factor'].isna().sum())}")
    # 代码前缀分布（是否含非 ETF 段）
    d["bare"] = d["ts_code"].astype(str).str.split(".").str[0]
    pref = d["bare"].str[:2].value_counts().head(12)
    print(f"  代码前缀 TOP12:\n{pref.to_string()}")

print()
print("=" * 70)
print("点2【容差口径差核对】同一比值序列在 ±1% 与 ±5% 下的占比")
print("=" * 70)
d = d[(d["vol"] > 0) & (d["close"] > 0) & (d["amount"] > 0)].copy()
d["bare"] = d["ts_code"].astype(str).str.split(".").str[0]
# 分钟侧同码同日收盘 bar
m, _ = pull("qdb.etf_minutes", DAY, NXT, 300_000)
m = m[(m["vol"] > 0) & (m["close"] > 0)].copy()
m["bare"] = m["ts_code"].astype(str).str.split(".").str[0]
m["hm"] = m["trade_time"].astype(str).str[11:16]
last = (m[m["hm"] == "15:00"][["bare", "close", "adj_factor"]]
        .rename(columns={"close": "m_close", "adj_factor": "m_adj"}))
j = d[["bare", "close", "adj_factor"]].rename(
    columns={"close": "d_close", "adj_factor": "d_adj"}).merge(last, on="bare", how="inner")
j = j[(j["d_close"] > 0) & (j["m_close"] > 0)].copy()
j["ratio"] = j["m_close"] / j["d_close"]
print(f"  可比码数 = {len(j)}")
for tol in (0.001, 0.005, 0.01, 0.02, 0.05):
    print(f"  容差 ±{tol*100:>4.1f}%: 命中占比 = {(j['ratio'].between(1-tol, 1+tol)).mean():.4%}")
print(f"  比值分布: P1={(j['ratio'].quantile(0.01)):.4f} P5={(j['ratio'].quantile(0.05)):.4f} "
      f"P50={(j['ratio'].median()):.4f} P95={(j['ratio'].quantile(0.95)):.4f} "
      f"P99={(j['ratio'].quantile(0.99)):.4f}")
print(f"  逐码 adj 一致占比 = {(j['m_adj'] == j['d_adj']).mean():.4%}")
# 分两组看容差效应
eq = j[j["m_adj"] == j["d_adj"]]
ne = j[j["m_adj"] != j["d_adj"]]
for tag, sub in (("adj 一致", eq), ("adj 不一致", ne)):
    if len(sub):
        print(f"  [{tag}] n={len(sub)}  ±1%={(sub['ratio'].between(0.99,1.01)).mean():.4%}  "
              f"±5%={(sub['ratio'].between(0.95,1.05)).mean():.4%}")
print(f"  注：两表 adj 数值精度不同（云端 3 位小数）⇒ 严格 == 比较会低估一致率；"
      f"相对差<0.1% 视为一致时一致率 = "
      f"{(abs(j['m_adj']-j['d_adj'])/j['d_adj'] < 0.001).mean():.4%}")

print()
print("=" * 70)
print("点1【单位差 vs 口径分裂】~10× 与「双重复权」假设检验")
print("=" * 70)
# 假设 H_unit：日表 amount 以「千元/万元」计 ⇒ close/(amount/vol) 为常数 10 或 1000
for code in ("159327",):
    sub = d[d["bare"] == code]
    if len(sub):
        r = sub.iloc[0]
        cs_mv = float(r["close"]) / (float(r["amount"]) / float(r["vol"]))
        print(f"  日表 {code}: close={r['close']} amount={r['amount']} vol={r['vol']} "
              f"close/(amount/vol)={cs_mv:.4f}")
# 全样本：日表 close/(amount/vol) 的稳定性（单位差应给「极稳的常数」）
cs = d["close"] / (d["amount"] / d["vol"])
print(f"  日表全样本 close/(amount/vol): 中位={cs.median():.6f} "
      f"std={cs.std():.6f} 变异系数={cs.std()/cs.median():.6%}")
print(f"    分位: P1={cs.quantile(0.01):.4f} P25={cs.quantile(0.25):.4f} "
      f"P50={cs.median():.4f} P75={cs.quantile(0.75):.4f} P99={cs.quantile(0.99):.4f}")
print(f"  ⇒ 若为纯单位换算，比值应恒等于同一常数（变异系数≈0）")