#!/usr/bin/env python3
"""One-shot compatibility entry point for synchronizing ``etf_basic``.

The resident pipeline task is the preferred path. This script shares the exact same
canonical standardizer and DDL, so bootstrap/manual synchronization cannot drift.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import duckdb
import tushare as ts

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantstudio._secrets import load_secrets_env
from quantstudio.pipeline.etf_basic_standardizer import (
    BASELINE_COLUMNS, ETF_TYPES, build_payload, classify_etf,
)
from quantstudio.pipeline.writers import DDL_DUCKDB

load_secrets_env()


def _daily_bounds(conn: duckdb.DuckDBPyConnection):
    exists = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema='main' AND table_name='etf_daily'"
    ).fetchone()[0]
    if not exists:
        import pandas as pd
        return pd.DataFrame(columns=["code", "first_bar_ms", "last_bar_ms"])
    return conn.execute("""
        SELECT code, MIN(time) AS first_bar_ms, MAX(time) AS last_bar_ms
        FROM etf_daily GROUP BY code
    """).fetchdf()


def sync(db_path: str | Path) -> int:
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is not set")
    pro = ts.pro_api(token, timeout=30)
    raw = pro.fund_basic(
        market="E",
        fields=("ts_code,name,fund_type,list_date,delist_date,benchmark,"
                "status,invest_type,type"),
    )
    try:
        conn = duckdb.connect(str(db_path))
    except duckdb.IOException as exc:
        raise RuntimeError(
            f"cannot open {db_path}; stop the process holding the DuckDB write lock"
        ) from exc
    try:
        payload = build_payload(raw, _daily_bounds(conn))
        conn.execute(DDL_DUCKDB["etf_basic"])
        conn.register("_etf_basic_sync", payload[BASELINE_COLUMNS])
        conn.execute("BEGIN")
        conn.execute("DELETE FROM etf_basic")
        columns = ", ".join(BASELINE_COLUMNS)
        conn.execute(f"INSERT INTO etf_basic ({columns}) SELECT {columns} FROM _etf_basic_sync")
        conn.execute("COMMIT")
        count = int(conn.execute("SELECT COUNT(*) FROM etf_basic").fetchone()[0])
        equity = int(conn.execute(
            "SELECT COUNT(*) FROM etf_basic WHERE etf_type='equity' AND NOT is_cross_border"
        ).fetchone()[0])
        if count == 0 or equity == 0:
            raise RuntimeError(f"etf_basic post-write gate failed: rows={count}, equity={equity}")
        return count
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Synchronize etf_basic reference metadata")
    parser.add_argument("--db", default="data/quantstudio.db")
    args = parser.parse_args(argv)
    count = sync(args.db)
    print(f"etf_basic synchronized: {count} rows -> {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
