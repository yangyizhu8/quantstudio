"""A53 重放数据预抽取：把三码的 fresh×db join 结果落成小 parquet（供 A53 快测）。

数据源：daemon 导出分片（fresh 侧）× data/backup/quantstudio.db（库侧，只读）。
产物：data/forensics_1021/minute_rho_replay.parquet
      列：code, time, fresh_open/high/low/close, db_open/high/low/close
"""

import glob
import os
import sys
from datetime import datetime, timedelta, timezone

import duckdb
import numpy as np
import pandas as pd

REPO = r"D:\miniQMT策略实盘\QuantStudio"
BK = REPO + r"\data\backup\quantstudio.db"
OUT = REPO + r"\data\forensics_1021\minute_rho_replay.parquet"
CODES = ["000726", "000729", "000563"]
BJ = timezone(timedelta(hours=8))
SHARD_GLOB = (REPO + r"\data\mcp_landing\exp_stock_minutes_*\*.parquet").replace("\\", "/")


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    con = duckdb.connect(BK, read_only=True)
    where = " OR ".join([f"ts_code LIKE '{c}%'" for c in CODES])
    print("[extract] 扫描分片…", flush=True)
    f = con.execute(
        f"SELECT ts_code, trade_time, open, high, low, close FROM "
        f"read_parquet('{SHARD_GLOB}', union_by_name=true) WHERE {where}"
    ).fetchdf()
    _tt = pd.to_datetime(f["trade_time"])
    if getattr(_tt.dt, "tz", None) is None:
        _tt = _tt.dt.tz_localize("Asia/Shanghai")
    f["time"] = _tt.dt.tz_convert("UTC").astype("int64") // 10**6
    f["code"] = f["ts_code"].astype(str).str.split(".").str[0]
    print(f"[extract] 分片命中 {len(f)} 行", flush=True)

    frames = []
    for code in CODES:
        sub = f[f["code"] == code][["time", "open", "high", "low", "close"]]
        db = con.execute(
            "SELECT time, open, high, low, close FROM stock_minutes "
            "WHERE code=? AND freq='1min'", [code]).fetchdf()
        m = sub.merge(db, on="time", suffixes=("_f", "_b"))
        m["code"] = code
        frames.append(m)
        print(f"[extract] {code}: join={len(m)}", flush=True)
    con.close()
    out = pd.concat(frames, ignore_index=True)
    out.to_parquet(OUT, index=False)
    print(f"[extract] 落盘 {OUT} rows={len(out)} cols={list(out.columns)}")


if __name__ == "__main__":
    main()
