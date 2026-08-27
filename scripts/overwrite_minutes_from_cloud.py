# -*- coding: utf-8 -*-
"""云端 raw 精确覆盖修复（dry-run/apply 两段式）。

QFQ rebase C 检查要求：库内 raw OHLC 与云端 dividend_type=none raw 逐 bar
严格一致（|Δ|≤1e-9，STOCK）。因子恢复法有浮点差（1271.097088 vs 1271.1），
不可接受 → 直接从云端拉 raw 精确覆盖本地 (code,time) 匹配行。
--apply 才写库；默认 dry-run 只统计。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb
import pandas as pd

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
DB = ROOT / "data" / "quantstudio.db"

CANARY = ["000001", "000651", "000858", "002107", "300750",
          "600000", "600519", "601398", "601628"]

def suff(c: str) -> str:
    return c + ".SH" if c.startswith(("600", "601", "603", "605", "688", "510", "512", "513", "515", "518", "520", "530", "560", "561", "562", "563", "588", "589")) else c + ".SZ"

def fetch_and_overwrite(table: str, codes: list, start: str, end: str, apply: bool) -> dict:
    from quantstudio.pipeline.sources.mcp_adapter import MCPAdapter
    cfg = {"base_url": "https://124.223.159.234/mcp", "tls_verify": False,
           "enable_qfq_injection": False, "export_cache": False,
           "main_db": str(DB)}
    adapter = MCPAdapter(cfg)
    suffixed = [suff(c) for c in codes]
    raw, meta = adapter.fetch_table(table, start, end, freq="1min", codes=suffixed)
    if raw is None or len(raw) == 0:
        return {"table": table, "fetched": 0, "note": "fetch 0 rows"}
    # 归一 time -> ms（分钟 bar time）
    if "time" in raw.columns:
        raw["time_ms"] = pd.to_numeric(raw["time"], errors="coerce")
    elif "trade_time" in raw.columns:
        tt = pd.to_datetime(raw["trade_time"])
        if tt.dt.tz is None:
            tt = tt.dt.tz_localize("Asia/Shanghai")
        else:
            tt = tt.dt.tz_convert("Asia/Shanghai")
        raw["time_ms"] = tt.astype("int64") // 10**6
    else:
        return {"table": table, "fetched": 0, "note": "no time col"}
    raw["code_bare"] = raw["ts_code"].astype(str).str.split(".").str[0]
    df = raw[["code_bare", "time_ms", "open", "high", "low", "close"]].dropna(subset=["time_ms"]).copy()
    df["time_ms"] = df["time_ms"].astype("int64")
    df = df.sort_values(["code_bare", "time_ms"]).drop_duplicates(subset=["code_bare", "time_ms"], keep="last")

    con = duckdb.connect(str(DB))
    tbl = "stock_minutes" if table == "stock_minutes" else "etf_minutes"
    con.register("_cloud", df)
    n_matched = con.execute(
        f"SELECT COUNT(*) FROM {tbl} t JOIN _cloud c ON t.code=c.code_bare AND t.time=c.time_ms").fetchone()[0]
    # STOCK 严格 1e-9；ETF 1 tick（0.001）
    eps = 1e-9 if tbl == "stock_minutes" else 0.001
    n_diff = con.execute(
        f"SELECT COUNT(*) FROM {tbl} t JOIN _cloud c ON t.code=c.code_bare AND t.time=c.time_ms "
        f"WHERE ABS(t.close-c.close)>{eps} OR ABS(t.open-c.open)>{eps} "
        f"OR ABS(t.high-c.high)>{eps} OR ABS(t.low-c.low)>{eps}").fetchone()[0]
    print(f"[{table}] fetched={len(raw)} rows, cloud_unique={len(df)}, matched={n_matched}, diff>{eps}={n_diff}")
    # 云端有而本地无的行（缺失，不 INSERT——只覆盖既有行）
    n_cloud_only = con.execute(
        f"SELECT COUNT(*) FROM _cloud c LEFT JOIN {tbl} t ON t.code=c.code_bare AND t.time=c.time_ms "
        f"WHERE t.code IS NULL").fetchone()[0]
    print(f"[{table}] cloud-only（本地缺失，跳过）: {n_cloud_only}")
    if apply and n_diff > 0:
        con.execute(
            f"UPDATE {tbl} AS t SET open=c.open, high=c.high, low=c.low, close=c.close "
            f"FROM _cloud AS c WHERE t.code=c.code_bare AND t.time=c.time_ms "
            f"AND (ABS(t.close-c.close)>{eps} OR ABS(t.open-c.open)>{eps} "
            f"OR ABS(t.high-c.high)>{eps} OR ABS(t.low-c.low)>{eps})")
        con.commit()
        print(f"[{table}] UPDATED {n_diff} rows")
    con.close()
    return {"table": table, "fetched": len(raw), "matched": n_matched, "diff": n_diff}

if __name__ == "__main__":
    apply = "--apply" in sys.argv
    codes = CANARY
    for a in sys.argv[1:]:
        if a.startswith("--codes="):
            codes = a.split("=", 1)[1].split(",")
    print("MODE:", "APPLY" if apply else "DRY-RUN", "codes:", codes)
    fetch_and_overwrite("stock_minutes", codes, "2026-06-14", "2026-08-16", apply)
