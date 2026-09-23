"""P1 可行性勘察 · 基准①②：云端 etf_daily 同码同日对照 + 日表自洽性。

待验证问题（tech-debt REGISTERED）：
  客户端还原链 raw = qfq × adj_latest_global / adj_i 是否产出**可成交价**；
  云端 etf_minutes 的 close 与 amount/vol 同基准（前轮已证 84%~99.94% 行成立），
  但码内 adj_factor 与 close 口径并非全域自洽（约 3%~5% 行 amount/(close×vol) 越界）。

基准①：etf_daily 与 etf_minutes 同码同日 close 是否一致（跨表互证价格口径）
基准②：etf_daily 自身 pre_close / pct_chg / close 是否自洽（定口径，无外部依赖）
  自洽判据：close ≈ pre_close × (1 + pct_chg/100)
  若成立 ⇒ 云端日线 close 与 pre_close 同口径，可用于判定「close 是否可成交价」。
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


def pull(ds, ts, te, limit=300_000):
    ref = cli.create_export_job(ds, page_size=50_000,
                                time_start=ts, time_end=te, row_limit=limit)
    man = cli.get_manifest(ref)
    if not man.shards:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(io.BytesIO(
        cli.get_artifact(ref, f"{ref}/{s.shard_id}").parquet_bytes))
        for s in man.shards], ignore_index=True)


# ---------- 基准②：日表自洽性（无外部依赖，最强判据）----------
print("=== 基准②：etf_daily 自洽性 close ≈ pre_close×(1+pct_chg/100) ===")
for label, d0, d1 in (("2025-06 窗", "2025-06-03", "2025-06-06"),
                      ("2026-01 窗", "2026-01-05", "2026-01-08"),
                      ("2026-09 窗", "2026-09-18", "2026-09-22")):
    d = pull("qdb.etf_daily", d0, d1)
    if d.empty:
        print(f"  {label}: 无数据")
        continue
    d = d[(d["pre_close"] > 0) & (d["close"] > 0)]
    if d.empty:
        print(f"  {label}: 无有效行")
        continue
    pred = d["pre_close"] * (1 + d["pct_chg"] / 100.0)
    rel = (pred - d["close"]).abs() / d["close"]
    print(f"  {label}  n={len(d)}")
    print(f"    close≈pre_close×(1+pct) 相对误差: 中位={rel.median():.3e}  "
          f"P99={rel.quantile(0.99):.3e}  超 1% 行占比={(rel > 0.01).mean():.4%}")
    # 同时给「close 与 amount/vol 的关系」（日表也有 amount/vol）
    m = (d["vol"] > 0) & (d["amount"] > 0)
    if m.any():
        r = d.loc[m, "close"] / (d.loc[m, "amount"] / d.loc[m, "vol"])
        print(f"    日表 close/(amount/vol): 中位={r.median():.4f}  "
              f"≈1 占比={(r.between(0.99, 1.01)).mean():.4%}")
    print(f"    adj_factor 取值数={d['adj_factor'].nunique()}  "
          f"is_qfq={d['is_qfq'].value_counts().to_dict()}")

# ---------- 基准①：跨表互证（日线 close vs 分钟收盘 close）----------
print("\n=== 基准①：etf_daily vs etf_minutes 同码同日 close 对照 ===")
# 注意：服务端时间窗为「左闭右开」语义（实测同日窗口返回 0 行）⇒ 一律用 [day, day+1)。
def _next_day(day: str) -> str:
    from datetime import datetime, timedelta
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


for label, day in (("2026-09-21", "2026-09-21"), ("2026-01-05", "2026-01-05"),
                   ("2025-06-03", "2025-06-03")):
    nd = _next_day(day)
    dly = pull("qdb.etf_daily", day, nd)
    mnt = pull("qdb.etf_minutes", day, nd, 300_000)
    if dly.empty or mnt.empty:
        print(f"  {label}: 数据不足（daily={len(dly)} minute={len(mnt)}）")
        continue
    dly = dly[(dly["close"] > 0)].copy()
    dly["bare"] = dly["ts_code"].astype(str).str.split(".").str[0]
    mnt = mnt[(mnt["close"] > 0)].copy()
    mnt["bare"] = mnt["ts_code"].astype(str).str.split(".").str[0]
    mnt["hm"] = mnt["trade_time"].astype(str).str[11:16]
    last = mnt[mnt["hm"] == "15:00"][["bare", "close", "adj_factor", "amount", "vol"]]
    last = last.rename(columns={"close": "m_close", "adj_factor": "m_adj",
                                "amount": "m_amt", "vol": "m_vol"})
    mg = dly[["bare", "close", "adj_factor"]].rename(
        columns={"close": "d_close", "adj_factor": "d_adj"}).merge(last, on="bare", how="inner")
    print(f"  {label}  可比码数={len(mg)}")
    if mg.empty:
        continue
    rel = (mg["d_close"] - mg["m_close"]).abs() / mg["d_close"]
    print(f"    日线 close vs 分钟 15:00 close: 中位相对差={rel.median():.3e}  "
          f"两者相等(±0.1%)占比={(rel < 0.001).mean():.4%}")
    adj_eq = (mg["d_adj"] == mg["m_adj"])
    print(f"    两表 adj_factor 一致码占比={adj_eq.mean():.4%}")
    # 关键：分钟表 close 与 amount/vol 的关系（全部码 + 分组）
    mg = mg.assign(m_cs_mv=mg["m_close"] / (mg["m_amt"] / mg["m_vol"]))
    print(f"    分钟 close/(amount/vol): 中位={mg['m_cs_mv'].median():.4f}  "
          f"≈1 占比={(mg['m_cs_mv'].between(0.99, 1.01)).mean():.4%}")
    for tag, sub in (("adj 一致码", mg[adj_eq]), ("adj 不一致码", mg[~adj_eq])):
        if len(sub):
            print(f"      {tag} n={len(sub)}: close/(amount/vol) 中位={sub['m_cs_mv'].median():.4f}  "
                  f"≈1 占比={(sub['m_cs_mv'].between(0.99, 1.01)).mean():.4%}")
    bad = mg[~adj_eq]
    if len(bad):
        print("      adj 不一致码样例（前 6）：")
        print(bad.head(6).to_string())