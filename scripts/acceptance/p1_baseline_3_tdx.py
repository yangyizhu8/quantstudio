"""P1 基准③：第三方源（tdx MCP 前复权日线）校准云端 close 口径 —— 决定性验证。

原理（本轮已实证的恒等式）：
    amount/vol 恒为**该时点的真实可成交价**（复权不变），与任何外部源无关。
    云端 close 若与 amount/vol 同基准 ⇒ close 即真实价（外部源应吻合）；
    云端 close 若已被复权缩放 ⇒ close ≠ amount/vol，且缩放比 = adj_i/adj_latest。

本次用 tdx 前复权日线 close 作为**外部基准**，判定：
  (A) 云端日线 close 与 tdx 前复权 close 的关系（是否同口径 + 缩放比）
  (B) 云端分钟 amount/vol 与 tdx 前复权 close 的关系（amount/vol 是真实价的推定检验）
  (C) 客户端还原式 raw = close × adj_latest/adj_i 是否与 tdx 吻合（还原正确性）

时间对齐：tdx 返回的是最新交易日（2026-09-23）前复权 OHLC；云端取同日前复权价对照。
"""
import io
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(ROOT))
from quantstudio.pipeline.mcp.client import MCPClient, load_mcp_api_key  # noqa: E402

# tdx MCP 返回值（2026-09-23 前复权 OHLC）—— 由本会话本次实测填入
TDX = {
    "159327": {"name": "半导体设备基金", "close": 1.15, "open": 1.16, "high": 1.16, "low": 1.14},
    "600519": {"name": "贵州茅台", "close": 1251.24, "open": 1255.03, "high": 1271.50,
               "low": 1250.89},
}

key = load_mcp_api_key()
cli = MCPClient(endpoint="https://124.223.159.234/mcp", api_key=key)
cli.handshake()

DAY = "2026-09-23"
NEXT = "2026-09-24"


def pull(ds, ts, te, limit=300_000):
    ref = cli.create_export_job(ds, page_size=50_000,
                                time_start=ts, time_end=te, row_limit=limit)
    man = cli.get_manifest(ref)
    if not man.shards:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(io.BytesIO(
        cli.get_artifact(ref, f"{ref}/{s.shard_id}").parquet_bytes))
        for s in man.shards], ignore_index=True)


# qfq_aux 最新因子
c = sqlite3.connect(f"file:{ROOT / 'data' / 'qfq_aux.db'}?mode=ro", uri=True, timeout=60)
LAT = {}
for code, adj in c.execute(
        "SELECT a.code, a.adj_factor FROM fund_adj a "
        "JOIN (SELECT code, MAX(time) mt FROM fund_adj GROUP BY code) m "
        "ON a.code=m.code AND a.time=m.mt"):
    if adj is not None and float(adj) > 0:
        LAT[str(code)] = float(adj)
c.close()

print(f"=== 基准③：云端 {DAY} 对照 tdx 前复权日线 ===")
dly = pull("qdb.etf_daily", DAY, NEXT)
print(f"  etf_daily {DAY} 行数={len(dly)}")
if not dly.empty:
    dly["bare"] = dly["ts_code"].astype(str).str.split(".").str[0]
    sub = dly[dly["bare"].isin(TDX.keys())]
    for _, r in sub.iterrows():
        b = r["bare"]
        t = TDX[b]
        print(f"\n  【{b} {t['name']}】(tdx 前复权 close={t['close']})")
        print(f"    云端 etf_daily: close={r['close']}  pre_close={r.get('pre_close')}  "
              f"pct_chg={r.get('pct_chg')}  adj_factor={r['adj_factor']}  is_qfq={r['is_qfq']}")
        print(f"    tdx 当日前复权 OHLC: O={t['open']} H={t['high']} L={t['low']} C={t['close']}")
        # 与 15:00 分钟 bar 对照
        mnt = pull("qdb.etf_minutes", DAY, NEXT, 300_000)
        if not mnt.empty:
            mnt["bare"] = mnt["ts_code"].astype(str).str.split(".").str[0]
            mnt["hm"] = mnt["trade_time"].astype(str).str[11:16]
            mm = mnt[(mnt["bare"] == b) & (mnt["hm"] == "15:00")]
            if len(mm):
                m = mm.iloc[0]
                traded = float(m["amount"]) / float(m["vol"]) if m["vol"] > 0 else float("nan")
                print(f"    云端分钟 15:00 bar: close={m['close']}  amount/vol={traded:.4f}  "
                      f"m_adj={m['adj_factor']}")
                k = float(r["adj_factor"])
                lat = LAT.get(b)
                print(f"      ⇒ 云日线close/tdx = {float(r['close'])/t['close']:.4f}"
                      f"   |  amount/vol/tdx = {traded/t['close']:.4f}"
                      f"   |  还原(cl×lat/k) = {float(r['close'])*(lat/k):.4f}" if lat else "")

# 分钟表当日是否存在
print(f"\n  etf_minutes {DAY} 是否有数据（末日边界）…")
mm23 = pull("qdb.etf_minutes", "2026-09-21", "2026-09-23", 100_000)
print(f"  窗口 2026-09-21~2026-09-23 行数={len(mm23)}  "
      f"时间范围={mm23['trade_time'].min() if len(mm23) else 'N/A'} ~ "
      f"{mm23['trade_time'].max() if len(mm23) else 'N/A'}")