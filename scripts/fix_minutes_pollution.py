# -*- coding: utf-8 -*-
"""精准修复脚本（dry-run/apply 两段式）：
MCP 拉取 33 只股票 + 55 只 ETF 分钟（带后缀 codes，export 缓存命中），
与库内 stock_minutes/etf_minutes 逐行对比，(code,time) 对齐更新 OHLC。
--apply 才写库；默认 dry-run 只统计。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb
import pandas as pd

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
DB = ROOT / "data" / "quantstudio.db"

STOCKS = ["000039","000429","000921","002011","002029","002181","002611","300692","300750","301172","600266","600528","600558","600628","600630","600635","600662","600824","600838","600993","601117","601298","601375","601600","601816","601958","603580","603648","603858","603893","688285","688501","688613"]
ETFS = ["159118","159119","159209","159220","159221","159232","159281","159331","159332","159333","159351","159355","159390","159399","159545","159576","159581","159589","510180","510300","510310","510500","510720","510880","512100","512250","512700","513530","513630","513820","513920","515450","520550","520660","520810","520900","520950","520960","530880","560120","560150","560350","560370","560510","560530","560570","560610","561580","563500","563660","563700","563830","563900","563980","589300"]

def suff(c: str) -> str:
    return c + ".SH" if c.startswith(("600","601","603","605","688","510","512","513","515","518","520","530","560","561","562","563","588","589")) else c + ".SZ"

def fetch_and_compare(table: str, codes: list, start: str, end: str, apply: bool) -> dict:
    from quantstudio.pipeline.sources.mcp_adapter import MCPAdapter
    cfg = {"base_url": "https://124.223.159.234/mcp", "tls_verify": False,
           "enable_qfq_injection": False, "export_cache": True,
           "main_db": str(DB)}
    adapter = MCPAdapter(cfg)
    suffixed = [suff(c) for c in codes]
    raw, meta = adapter.fetch_table(table, start, end, freq="1min", codes=suffixed)
    if raw is None or len(raw) == 0:
        return {"table": table, "fetched": 0, "note": "fetch 0 rows"}
    # 归一 time -> +08 当日 ms（分钟 bar time）
    if "trade_time" in raw.columns:
        tt = pd.to_datetime(raw["trade_time"])
        if tt.dt.tz is None:
            tt = tt.dt.tz_localize("Asia/Shanghai")
        else:
            tt = tt.dt.tz_convert("Asia/Shanghai")
        raw["time_ms"] = tt.astype("int64") // 10**6
    elif "time" in raw.columns:
        raw["time_ms"] = pd.to_numeric(raw["time"], errors="coerce")
    else:
        return {"table": table, "fetched": 0, "note": "no time col"}
    raw["code_bare"] = raw["ts_code"].astype(str).str.split(".").str[0]
    df = raw[["code_bare", "time_ms", "open", "high", "low", "close"]].dropna(subset=["time_ms"]).copy()
    df["time_ms"] = df["time_ms"].astype("int64")
    df = df.sort_values(["code_bare", "time_ms"]).drop_duplicates(subset=["code_bare", "time_ms"], keep="last")

    con = duckdb.connect(str(DB))
    tbl = "stock_minutes" if table == "stock_minutes" else "etf_minutes"
    con.register("_cloud", df)
    # 匹配 + 差异统计
    n_matched = con.execute(
        f"SELECT COUNT(*) FROM {tbl} t JOIN _cloud c ON t.code=c.code_bare AND t.time=c.time_ms").fetchone()[0]
    n_diff = con.execute(
        f"SELECT COUNT(*) FROM {tbl} t JOIN _cloud c ON t.code=c.code_bare AND t.time=c.time_ms "
        f"WHERE ABS(t.close-c.close)>1e-6 OR ABS(t.open-c.open)>1e-6").fetchone()[0]
    n_cloud = len(df)
    print(f"[{table}] fetched={len(raw)} rows, cloud_unique={n_cloud}, matched={n_matched}, close_diff={n_diff}")
    if apply and n_diff > 0:
        con.execute(
            f"UPDATE {tbl} AS t SET open=c.open, high=c.high, low=c.low, close=c.close "
            f"FROM _cloud AS c WHERE t.code=c.code_bare AND t.time=c.time_ms")
        con.commit()
        print(f"[{table}] UPDATED {n_diff} rows")
    con.close()
    return {"table": table, "fetched": len(raw), "matched": n_matched, "diff": n_diff}

if __name__ == "__main__":
    apply = "--apply" in sys.argv
    print("MODE:", "APPLY" if apply else "DRY-RUN")
    for tbl, codes in (("stock_minutes", STOCKS), ("etf_minutes", ETFS)):
        fetch_and_compare(tbl, codes, "2026-06-14", "2026-08-16", apply)
