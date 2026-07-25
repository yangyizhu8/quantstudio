#!/usr/bin/env python3
"""Synchronize PIT ETF reference metadata from Tushare ``fund_basic``.

The resulting ``etf_basic`` table is the classification/listing authority used by
``get_etf_list_local``. Daily bars remain in ``etf_daily`` and are used only to
prove historical data availability and to fill missing listing/delisting dates.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

import duckdb
import pandas as pd
import tushare as ts

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantstudio._secrets import load_secrets_env

load_secrets_env()

CLASSIFICATION_VERSION = "etf-basic-v1"
ETF_TYPES = {"equity", "bond", "money", "commodity", "gold", "qdii", "other"}

_CROSS_BORDER_TOKENS = (
    "\u6e2f\u80a1", "\u9999\u6e2f", "\u6052\u751f", "\u6052\u6307", "H\u80a1",
    "\u6d77\u5916", "\u5168\u7403", "\u4e9a\u592a", "\u6b27\u6d32",
    "\u7eb3\u65af\u8fbe\u514b", "\u7eb3\u6307", "\u6807\u666e", "\u9053\u743c\u65af",
    "\u7f8e\u80a1", "\u7f8e\u56fd", "\u65e5\u7ecf", "\u65e5\u672c", "\u5fb7\u56fd",
    "\u6cd5\u56fd", "\u82f1\u56fd", "\u5370\u5ea6", "\u8d8a\u5357", "\u4e1c\u5357\u4e9a",
    "\u6c99\u7279", "\u65b0\u52a0\u5761", "\u97e9\u56fd", "\u4e2d\u97e9", "\u5df4\u897f",
    "\u58a8\u897f\u54e5", "\u52a0\u62ff\u5927", "\u6fb3\u6d32", "\u6fb3\u5927\u5229\u4e9a",
)
_GOLD_TOKENS = ("\u9ec4\u91d1", "\u91d1\u4ef7", "\u9ec4\u91d1\u73b0\u8d27")
_COMMODITY_TOKENS = (
    "\u539f\u6cb9", "\u8c46\u7c95", "\u767d\u94f6", "\u6709\u8272\u91d1\u5c5e\u671f\u8d27",
    "\u80fd\u6e90\u5316\u5de5", "\u5546\u54c1\u671f\u8d27", "\u5546\u54c1\u6307\u6570",
    "\u671f\u8d27\u578b",
)


def _date_ms(value):
    if value is None or pd.isna(value) or not str(value).strip():
        return None
    text = str(value).strip()
    stamp = pd.Timestamp(text, tz="Asia/Shanghai").normalize()
    return int(stamp.timestamp() * 1000)


def _exchange(ts_code: str) -> str:
    suffix = str(ts_code).upper().rsplit(".", 1)[-1]
    return {"SH": "SS", "SZ": "SZ"}.get(suffix, suffix)


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token.upper() in text.upper() for token in tokens)


def classify_etf(row: pd.Series) -> tuple[str, bool, str]:
    """Classify one exchange-traded fund using auditable Tushare metadata rules."""
    name = str(row.get("name") or "")
    benchmark = str(row.get("benchmark") or "")
    fund_type = str(row.get("fund_type") or row.get("type") or "")
    invest_type = str(row.get("invest_type") or "")
    text = " ".join((name, benchmark, fund_type, invest_type))
    code = str(row.get("ts_code") or "").split(".", 1)[0]

    is_cross_border = code.startswith(("513", "520")) or _contains_any(
        f"{name} {benchmark}", _CROSS_BORDER_TOKENS
    )
    if "\u8d27\u5e01" in text:
        return "money", False, "raw_type:money"
    if ("\u503a\u5238" in text or "\u56fd\u503a" in text or "\u653f\u91d1\u503a" in text or "\u53ef\u8f6c\u503a" in text):
        return "bond", False, "raw_type:bond"
    if _contains_any(text, _GOLD_TOKENS):
        return "gold", False, "keyword:gold"
    if _contains_any(text, _COMMODITY_TOKENS) or "\u671f\u8d27" in invest_type:
        return "commodity", is_cross_border, "keyword:commodity"
    if is_cross_border:
        return "qdii", True, "keyword_or_code:cross_border"
    if "\u80a1\u7968" in fund_type or "\u80a1\u7968" in str(row.get("type") or ""):
        return "equity", False, "raw_type:equity"
    return "other", False, "fallback:other"


def _daily_bounds(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    exists = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema='main' AND table_name='etf_daily'"
    ).fetchone()[0]
    if not exists:
        return pd.DataFrame(columns=["code", "first_bar_ms", "last_bar_ms"])
    return conn.execute("""
        SELECT code, MIN(time) AS first_bar_ms, MAX(time) AS last_bar_ms
        FROM etf_daily
        GROUP BY code
    """).fetchdf()


def build_payload(raw: pd.DataFrame, daily_bounds: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        raise RuntimeError("Tushare fund_basic returned no rows")
    data = raw.copy()
    names = data["name"].fillna("").astype(str).str.upper()
    data = data[names.str.contains("ETF", regex=False)]
    data = data[~data["name"].fillna("").astype(str).str.contains("\u8054\u63a5", regex=False)]
    data = data.drop_duplicates("ts_code", keep="first").copy()
    data["code"] = data["ts_code"].astype(str).str.split(".").str[0].str.zfill(6)
    data["exchange"] = data["ts_code"].map(_exchange)

    classified = data.apply(classify_etf, axis=1, result_type="expand")
    classified.columns = ["etf_type", "is_cross_border", "classification_method"]
    data = pd.concat([data, classified], axis=1)
    data["list_date"] = data["list_date"].map(_date_ms)
    data["delist_date"] = data["delist_date"].map(_date_ms)

    if daily_bounds is not None and not daily_bounds.empty:
        data = data.merge(daily_bounds, on="code", how="left")
        data["list_date"] = data["list_date"].where(
            data["list_date"].notna(), data["first_bar_ms"]
        )
        missing_delist = (
            data["status"].eq("D") & data["delist_date"].isna()
            & data["last_bar_ms"].notna()
        )
        data.loc[missing_delist, "delist_date"] = (
            data.loc[missing_delist, "last_bar_ms"] + 86_400_000
        )
    else:
        data["first_bar_ms"] = None
        data["last_bar_ms"] = None

    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()
    data["tracking_index"] = data.get("benchmark")
    data["update_time"] = now
    data["data_source"] = "tushare_fund_basic"
    data["classification_version"] = CLASSIFICATION_VERSION
    columns = [
        "code", "ts_code", "name", "exchange", "list_date", "delist_date",
        "etf_type", "tracking_index", "is_cross_border", "status",
        "fund_type", "invest_type", "type", "classification_method",
        "classification_version", "update_time", "data_source",
    ]
    return data[columns].sort_values("code").reset_index(drop=True)


def sync(db_path: str | Path) -> int:
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is not set")
    pro = ts.pro_api(token, timeout=30)
    raw = pro.fund_basic(market="E")

    try:
        conn = duckdb.connect(str(db_path))
    except duckdb.IOException as exc:
        raise RuntimeError(
            f"cannot open {db_path} for ETF metadata synchronization; "
            "stop any active QuantStudio backtest/daemon process that holds the DuckDB "
            "write lock, then rerun scripts/sync_etf_basic.py"
        ) from exc
    try:
        payload = build_payload(raw, _daily_bounds(conn))
        if payload.empty:
            raise RuntimeError("ETF metadata filter produced zero rows")
        invalid_types = sorted(set(payload["etf_type"]) - ETF_TYPES)
        invalid_exchange = payload[~payload["exchange"].isin(["SS", "SZ"])]
        if invalid_types or not invalid_exchange.empty or payload["code"].duplicated().any():
            raise RuntimeError(
                "etf_basic quality gate failed: "
                f"invalid_types={invalid_types}, invalid_exchange={len(invalid_exchange)}, "
                f"duplicate_codes={int(payload['code'].duplicated().sum())}"
            )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS etf_basic (
                code VARCHAR PRIMARY KEY,
                ts_code VARCHAR,
                name VARCHAR,
                exchange VARCHAR,
                list_date BIGINT,
                delist_date BIGINT,
                etf_type VARCHAR NOT NULL,
                tracking_index VARCHAR,
                is_cross_border BOOLEAN NOT NULL,
                status VARCHAR,
                fund_type VARCHAR,
                invest_type VARCHAR,
                type VARCHAR,
                classification_method VARCHAR NOT NULL,
                classification_version VARCHAR NOT NULL,
                update_time VARCHAR,
                data_source VARCHAR
            )
        """)
        conn.register("_etf_basic_sync", payload)
        conn.execute("BEGIN")
        conn.execute("DELETE FROM etf_basic")
        conn.execute("INSERT INTO etf_basic SELECT * FROM _etf_basic_sync")
        conn.execute("COMMIT")
        count = conn.execute("SELECT COUNT(*) FROM etf_basic").fetchone()[0]
        equity_count = conn.execute(
            "SELECT COUNT(*) FROM etf_basic WHERE etf_type='equity' AND NOT is_cross_border"
        ).fetchone()[0]
        if count == 0 or equity_count == 0:
            raise RuntimeError(
                f"etf_basic quality gate failed after write: rows={count}, equity={equity_count}"
            )
        return int(count)
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
