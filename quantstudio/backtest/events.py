"""Generic strategy-event ingestion for storage-isolated local backtests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

import pandas as pd

from .libs.security_code_rules import normalize_to_ptrade

EVENT_COLUMNS = [
    "event_type", "event_date", "effective_date", "code", "signal",
    "name", "category", "source", "source_row_id", "source_key", "payload", "imported_at",
]


def ensure_strategy_events_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_events (
            event_type VARCHAR NOT NULL,
            event_date DATE NOT NULL,
            effective_date DATE NOT NULL,
            code VARCHAR NOT NULL,
            signal VARCHAR,
            name VARCHAR,
            category VARCHAR,
            source VARCHAR,
            source_row_id BIGINT,
            source_key VARCHAR NOT NULL,
            payload JSON,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (event_type, source_key)
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info('strategy_events')").fetchall()}
    if "source_row_id" not in columns:
        conn.execute("ALTER TABLE strategy_events ADD COLUMN source_row_id BIGINT")


def _next_trade_day_map(conn, event_dates: pd.Series) -> dict[str, str]:
    if event_dates.empty:
        return {}
    start = pd.Timestamp(event_dates.min()).normalize()
    end = pd.Timestamp(event_dates.max()).normalize() + pd.Timedelta(days=14)
    start_ms = int(start.tz_localize("Asia/Shanghai").timestamp() * 1000)
    end_ms = int((end.tz_localize("Asia/Shanghai") + pd.Timedelta(days=1)
                  - pd.Timedelta(milliseconds=1)).timestamp() * 1000)
    rows = conn.execute(
        "SELECT DISTINCT time FROM stock_daily WHERE time >= ? AND time <= ? ORDER BY time",
        [start_ms, end_ms],
    ).fetchall()
    trade_days = [pd.Timestamp(value, unit="ms", tz="Asia/Shanghai").date() for (value,) in rows]
    result = {}
    for value in event_dates:
        day = pd.Timestamp(value).date()
        next_days = [candidate for candidate in trade_days if candidate > day]
        if next_days:
            result[str(day)] = str(next_days[0])
    return result


def _portable_code(value) -> str:
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    bare = text.split(".")[0].zfill(6)
    return normalize_to_ptrade(bare)


def import_strategy_event_csv(
    db_path: str | Path,
    csv_path: str | Path,
    event_type: str,
    column_map: Mapping[str, str],
    *,
    encoding: str = "utf-8-sig",
    replace_event_type: bool = True,
) -> dict:
    """3A 写锁接入（events.py:107 MAIN 写路径）：导入期间持锁，失败即拒绝。"""
    from quantstudio.pipeline.snapshot_lock import (ensure_write_lock,
                                                    release_write_lock)
    ensure_write_lock(f"events:import:{Path(db_path).name}")
    try:
        return _import_strategy_event_csv_impl(
            db_path, csv_path, event_type, column_map,
            encoding=encoding, replace_event_type=replace_event_type)
    finally:
        release_write_lock()


def _import_strategy_event_csv_impl(
    db_path: str | Path,
    csv_path: str | Path,
    event_type: str,
    column_map: Mapping[str, str],
    *,
    encoding: str = "utf-8-sig",
    replace_event_type: bool = True,
) -> dict:
    """Import a CSV into the generic strategy_events table.

    ``column_map`` maps canonical fields (event_date, code, signal, name,
    category, source) to source CSV column names. effective_date is computed as
    the next trading day unless a mapped effective_date column is supplied.
    """
    import duckdb

    frame = pd.read_csv(csv_path, encoding=encoding)
    required = {"event_date", "code"}
    missing_map = sorted(required - set(column_map))
    if missing_map:
        raise ValueError(f"column_map missing required fields: {missing_map}")
    missing_columns = sorted({source for source in column_map.values() if source not in frame.columns})
    if missing_columns:
        raise ValueError(f"CSV missing mapped columns: {missing_columns}")

    frame = frame.copy()
    frame["_source_row_id"] = frame.index.astype(int)
    frame["_event_date"] = pd.to_datetime(
        frame[column_map["event_date"]].astype(str).str[:10], errors="coerce")
    frame = frame[frame["_event_date"].notna()].copy()
    frame["_code"] = frame[column_map["code"]].map(_portable_code)

    conn = duckdb.connect(str(Path(db_path)))
    try:
        ensure_strategy_events_table(conn)
        if "effective_date" in column_map:
            frame["_effective_date"] = pd.to_datetime(
                frame[column_map["effective_date"]].astype(str).str[:10], errors="coerce")
        else:
            day_map = _next_trade_day_map(conn, frame["_event_date"] )
            frame["_effective_date"] = frame["_event_date"].map(
                lambda value: pd.Timestamp(day_map.get(str(value.date())))
                if day_map.get(str(value.date())) else pd.NaT)
        frame = frame[frame["_effective_date"].notna()].copy()

        rows = []
        for _, row in frame.iterrows():
            payload = {str(key): (None if pd.isna(value) else value)
                       for key, value in row.items() if not str(key).startswith("_")}
            record = {
                "event_type": str(event_type),
                "event_date": str(row["_event_date"].date()),
                "effective_date": str(row["_effective_date"].date()),
                "code": row["_code"],
                "signal": str(row[column_map["signal"]]) if column_map.get("signal") else None,
                "name": str(row[column_map["name"]]) if column_map.get("name") else None,
                "category": str(row[column_map["category"]]) if column_map.get("category") else None,
                "source": str(row[column_map["source"]]) if column_map.get("source") else None,
                "source_row_id": int(row["_source_row_id"]),
                "payload": json.dumps(payload, ensure_ascii=False, default=str),
            }
            key_payload = json.dumps(
                [record["event_type"], record["event_date"], record["code"],
                 record["signal"], record["name"], record["category"], record["source"],
                  record["source_row_id"], payload],
                ensure_ascii=False, sort_keys=True, default=str,
            )
            record["source_key"] = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()
            rows.append(record)

        if replace_event_type:
            conn.execute("DELETE FROM strategy_events WHERE event_type = ?", [str(event_type)])
        if rows:
            data = pd.DataFrame(rows)
            conn.register("event_import_rows", data)
            conn.execute(
                """
                INSERT INTO strategy_events
                (event_type, event_date, effective_date, code, signal, name, category, source, source_row_id, source_key, payload)
                SELECT event_type, event_date::DATE, effective_date::DATE, code, signal, name, category, source, source_row_id, source_key, payload::JSON
                FROM event_import_rows
                """
            )
            conn.unregister("event_import_rows")
        conn.execute("CHECKPOINT")
        count = conn.execute(
            "SELECT COUNT(*) FROM strategy_events WHERE event_type = ?", [str(event_type)]
        ).fetchone()[0]
        min_date, max_date = conn.execute(
            "SELECT MIN(event_date), MAX(event_date) FROM strategy_events WHERE event_type = ?",
            [str(event_type)],
        ).fetchone()
        return {
            "event_type": str(event_type), "imported_rows": int(count),
            "min_event_date": str(min_date) if min_date else None,
            "max_event_date": str(max_date) if max_date else None,
        }
    finally:
        conn.close()
