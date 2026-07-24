#!/usr/bin/env python3
"""Synchronize the formal A-share listing master from Tushare stock_basic."""
from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path

import duckdb
import pandas as pd
import tushare as ts

# 自动载入 config/secrets.env（提供 TUSHARE_TOKEN 等凭证）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quantstudio._secrets import load_secrets_env
load_secrets_env()


def _date_ms(value):
    if value is None or pd.isna(value) or not str(value).strip():
        return None
    stamp = pd.Timestamp(str(value), tz="Asia/Shanghai")
    return int(stamp.timestamp() * 1000)


def sync(db_path: str | Path) -> int:
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is not set")
    pro = ts.pro_api(token)
    frames = []
    for status in ("L", "D", "P"):
        frame = pro.stock_basic(
            exchange="", list_status=status,
            fields="ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,delist_date",
        )
        if frame is not None and not frame.empty:
            frames.append(frame)
    if not frames:
        raise RuntimeError("Tushare stock_basic returned no rows")
    data = pd.concat(frames, ignore_index=True).drop_duplicates("ts_code", keep="first")
    data["code"] = data["symbol"].astype(str).str.zfill(6)
    data["list_date"] = data["list_date"].map(_date_ms)
    data["delist_date"] = data["delist_date"].map(_date_ms)
    data["update_time"] = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()
    data["data_source"] = "tushare_stock_basic"
    columns = [
        "code", "ts_code", "name", "area", "industry", "market", "exchange",
        "list_status", "list_date", "delist_date", "update_time", "data_source",
    ]
    payload = data[columns]

    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_basic (
                code VARCHAR PRIMARY KEY,
                ts_code VARCHAR,
                name VARCHAR,
                area VARCHAR,
                industry VARCHAR,
                market VARCHAR,
                exchange VARCHAR,
                list_status VARCHAR,
                list_date BIGINT NOT NULL,
                delist_date BIGINT,
                update_time VARCHAR,
                data_source VARCHAR
            )
        """)
        conn.register("_stock_basic_sync", payload)
        conn.execute("BEGIN")
        conn.execute("DELETE FROM stock_basic")
        conn.execute("INSERT INTO stock_basic SELECT * FROM _stock_basic_sync")
        conn.execute("COMMIT")
        count = conn.execute("SELECT COUNT(*) FROM stock_basic").fetchone()[0]
        nulls = conn.execute("SELECT COUNT(*) FROM stock_basic WHERE list_date IS NULL").fetchone()[0]
        if count == 0 or nulls:
            raise RuntimeError(f"stock_basic quality gate failed: rows={count}, null_list_date={nulls}")
        return count
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/quantstudio.db")
    args = parser.parse_args(argv)
    count = sync(args.db)
    print(f"stock_basic synchronized: {count} rows -> {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
