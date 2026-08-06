"""Deterministic snapshot evidence for B-5 cutover staging.

Evidence is content based: fixed column order, fixed row order, explicit NULL
normalisation and SHA-256.  The helpers are read-only and work with DuckDB or
SQLite DB-API connections.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Sequence

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ident(value: str) -> str:
    if not isinstance(value, str) or not _IDENT.fullmatch(value):
        raise ValueError(f"非法 SQL 标识符: {value!r}")
    return value


def _json_value(value):
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__bytes__": bytes(value).hex()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float):
        return format(value, ".17g")
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in sorted(value.items())}
    return str(value)


def canonical_rows(columns: Sequence[str], rows: Iterable[Sequence]) -> bytes:
    payload = [{str(c): _json_value(v) for c, v in zip(columns, row)}
               for row in rows]
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                       sort_keys=False) + "\n").encode("utf-8")


def table_evidence(conn, table: str, *, order_by: Optional[Sequence[str]] = None) -> Dict:
    table = _ident(table)
    desc = conn.execute(f'DESCRIBE "{table}"').fetchall()
    columns = [str(r[0]) for r in desc]
    order = list(order_by or columns)
    for c in order:
        _ident(c)
    order_sql = ", ".join(f'"{c}"' for c in order)
    rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY {order_sql}').fetchall()
    blob = canonical_rows(columns, rows)
    times = [c for c in columns if c.lower() in {"time", "factor_time", "ex_date"}]
    time_min = time_max = None
    if times:
        col = times[0]
        mm = conn.execute(f'SELECT MIN("{col}"), MAX("{col}") FROM "{table}"').fetchone()
        if mm:
            time_min, time_max = mm[0], mm[1]
    return {
        "table": table,
        "columns": columns,
        "row_count": len(rows),
        "min_time": _json_value(time_min),
        "max_time": _json_value(time_max),
        "content_sha256": hashlib.sha256(blob).hexdigest(),
    }


def manifest_hash(entries: Iterable[Mapping]) -> str:
    normal = [dict(e) for e in entries]
    normal.sort(key=lambda x: (str(x.get("table", "")), json.dumps(x, sort_keys=True)))
    blob = json.dumps(normal, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def database_evidence(conn, tables: Sequence[str]) -> Dict:
    entries = [table_evidence(conn, t) for t in sorted(tables)]
    return {"tables": entries, "manifest_sha256": manifest_hash(entries)}


def file_evidence(path: str | Path) -> Dict:
    p = Path(path).resolve()
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return {"path": str(p), "size": p.stat().st_size, "sha256": h.hexdigest()}
