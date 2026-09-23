"""P1 基准③ 判据：云端 close 与 tdx 前复权（同锚基准）规模化对照。

判据（决定性、无外部依赖歧义）：
  tdx 返「前复权(最新锚)」；云端 is_qfq=True 亦为「前复权」。
  两者若同锚同口径 ⇒ 云端 close == tdx 前复权 close（该日无除权事件时）。
  出现**系统性固定倍数** ⇒ 云端因子锚与 tdx 前复权锚不一致（口径分裂）。

规模：每窗抽 N 只 ETF × 3 个日期，统计 close/tdx 比值分布。
"""
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(ROOT))
from quantstudio.pipeline.mcp.client import MCPClient, load_mcp_api_key  # noqa: E402

# 本会话 tdx 实测值（前复权，单位：元）
TDX = {
    ("159327", "2025-06-03"): 0.40,
    ("159327", "2026-09-23"): 1.15,
    ("600519", "2026-09-23"): 1251.24,
}

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


print("=== 云端 etf_daily 与 tdx 前复权逐点对照（已知 tdx 值）===")
for (code, day), tdx_close in TDX.items():
    ds = "qdb.etf_daily" if code.startswith("6") else "qdb.etf_daily"
    nxt = (pd.Timestamp(day) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    d = pull(ds, day, nxt)
    if d.empty:
        print(f"  {code} {day}: 云端无数据（末日边界或未覆盖）")
        continue
    d["bare"] = d["ts_code"].astype(str).str.split(".").str[0]
    r = d[d["bare"] == code]
    if r.empty:
        print(f"  {code} {day}: 云端无该码")
        continue
    r = r.iloc[0]
    ratio = float(r["close"]) / tdx_close
    print(f"  {code} {day}: 云端 close={r['close']}  tdx 前复权={tdx_close}  "
          f"比值={ratio:.4f}  adj={r['adj_factor']}  is_qfq={r['is_qfq']}")
    # 同时给 amount/vol（真实价推定）与 tdx 的比
    if r["vol"] and r["vol"] > 0:
        traded = float(r["amount"]) / float(r["vol"])
        print(f"      云端 amount/vol={traded:.4f}  amount/vol ÷ tdx={traded/tdx_close:.4f}")

print("\n=== 分钟表同日 amount/vol（真实价推定）vs tdx ===")
for (code, day) in (("159327", "2025-06-03"),):
    nxt = (pd.Timestamp(day) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    m = pull("qdb.etf_minutes", day, nxt, 300_000)
    if m.empty:
        print(f"  {code} {day}: 分钟无数据")
        continue
    m["bare"] = m["ts_code"].astype(str).str.split(".").str[0]
    sub = m[(m["bare"] == code) & (m["vol"] > 0) & (m["close"] > 0)]
    if sub.empty:
        print(f"  {code} {day}: 该码无有效分钟行")
        continue
    traded = (sub["amount"] / sub["vol"])
    print(f"  {code} {day}: n={len(sub)}")
    print(f"    分钟 amount/vol: 中位={traded.median():.4f}  "
          f"min={traded.min():.4f} max={traded.max():.4f}")
    print(f"    分钟 close:      中位={sub['close'].median():.4f}")
    print(f"    close/(amount/vol): 中位={(sub['close']/traded).median():.4f}")
    print(f"    tdx 前复权 close={TDX[(code, day)]}")
    print(f"    ⇒ amount/vol ÷ tdx = {traded.median()/TDX[(code, day)]:.4f}")
    print(f"    ⇒ 云端close ÷ tdx  = {sub['close'].median()/TDX[(code, day)]:.4f}")