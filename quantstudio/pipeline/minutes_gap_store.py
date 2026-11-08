# -*- coding: utf-8 -*-
"""分钟缺口账本（SQLite，aux 侧）——1.6 的持久化层。

落位理由（审核意见 2026-11-08）：
> 账本读写不该依赖主库锁，放 aux 则现在就能建表、真文件测读写（不用 mock）；
> 主库 DDL 会给解锁后的 runbook 再加一步。

因此独立为 `data/minutes_gap.db`（SQLite，与 qfq_aux.db 同族但独立文件），
不与因子库混表、不占主库锁。

语义：
- 每次批次不足时落一条（action = defer / fail）
- 下轮重拉时用 `open_days()` 取未销账日，并入 union
- 该日补齐后 `close()` 销账
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

CST = timezone.utc  # 账本只记 UTC 时间戳，展示由调用方转换

DEFAULT_DB = Path(r"D:\miniQMT策略实盘\QuantStudio\data\minutes_gap.db")

DDL = """
CREATE TABLE IF NOT EXISTS minutes_gap_ledger (
    table_name      TEXT    NOT NULL,
    batch_start     TEXT    NOT NULL,
    batch_end       TEXT    NOT NULL,
    shards_actual   INTEGER NOT NULL,
    shards_required INTEGER NOT NULL,
    action          TEXT    NOT NULL CHECK (action IN ('defer','fail')),
    reason          TEXT    DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
    created_at      TEXT    NOT NULL,
    closed_at       TEXT,
    PRIMARY KEY (table_name, batch_start, batch_end)
);
CREATE INDEX IF NOT EXISTS idx_gap_open ON minutes_gap_ledger(table_name, status);
"""


def _now() -> str:
    return datetime.now(CST).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def connect(db_path: Optional[Path] = None):
    """打开账本（自动建表）。"""
    p = Path(db_path) if db_path else DEFAULT_DB
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    try:
        con.executescript(DDL)
        yield con
        con.commit()
    finally:
        con.close()


def record_gap(
    table: str,
    batch_start: str,
    batch_end: str,
    shards_actual: int,
    shards_required: int,
    action: str,
    reason: str = "",
    db_path: Optional[Path] = None,
) -> None:
    """落一条缺口（幂等：同 (table, batch_start, batch_end) 覆盖为最新）。"""
    if action not in ("defer", "fail"):
        raise ValueError(f"action 必须是 defer/fail，实际 {action}")
    with connect(db_path) as con:
        con.execute(
            "INSERT INTO minutes_gap_ledger (table_name, batch_start, batch_end, "
            "shards_actual, shards_required, action, reason, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,'open',?) "
            "ON CONFLICT(table_name, batch_start, batch_end) DO UPDATE SET "
            "shards_actual=excluded.shards_actual, "
            "shards_required=excluded.shards_required, "
            "action=excluded.action, reason=excluded.reason, "
            "status='open', created_at=excluded.created_at, closed_at=NULL",
            [table, str(batch_start)[:10], str(batch_end)[:10],
             int(shards_actual), int(shards_required), action, reason, _now()])


def open_records(table: Optional[str] = None,
                 db_path: Optional[Path] = None) -> List[Dict[str, object]]:
    """取未销账记录（供下轮 union）。"""
    sql = ("SELECT table_name, batch_start, batch_end, shards_actual, "
           "shards_required, action, reason, created_at "
           "FROM minutes_gap_ledger WHERE status='open'")
    params: List[object] = []
    if table:
        sql += " AND table_name=?"
        params.append(table)
    sql += " ORDER BY batch_start"
    with connect(db_path) as con:
        rows = con.execute(sql, params).fetchall()
    return [{"table_name": r[0], "batch_start": r[1], "batch_end": r[2],
             "shards_actual": r[3], "shards_required": r[4], "action": r[5],
             "reason": r[6], "created_at": r[7]} for r in rows]


def open_days(table: Optional[str] = None,
              db_path: Optional[Path] = None) -> Set[str]:
    """未销账的日期集合（批次端点，供 merge_forced_days）。"""
    out: Set[str] = set()
    for r in open_records(table, db_path):
        out.add(str(r["batch_start"])[:10])
        out.add(str(r["batch_end"])[:10])
    return out


def close_gap(table: str, batch_start: str, batch_end: str,
              db_path: Optional[Path] = None) -> bool:
    """销账（该批次已补齐）。返回是否真的关闭了一条。"""
    with connect(db_path) as con:
        cur = con.execute(
            "UPDATE minutes_gap_ledger SET status='closed', closed_at=? "
            "WHERE table_name=? AND batch_start=? AND batch_end=? AND status='open'",
            [_now(), table, str(batch_start)[:10], str(batch_end)[:10]])
        return cur.rowcount > 0


def close_days(table: str, days: Iterable[str],
               db_path: Optional[Path] = None) -> int:
    """按日销账：任何 batch_start/batch_end 命中该日的 open 记录都关闭。"""
    n = 0
    for d in {str(x)[:10] for x in days}:
        with connect(db_path) as con:
            cur = con.execute(
                "UPDATE minutes_gap_ledger SET status='closed', closed_at=? "
                "WHERE table_name=? AND status='open' "
                "AND (batch_start=? OR batch_end=?)",
                [_now(), table, d, d])
            n += cur.rowcount
    return n


def stats(db_path: Optional[Path] = None) -> Dict[str, int]:
    """账本概览（供监控/汇报）。"""
    with connect(db_path) as con:
        rows = con.execute(
            "SELECT status, COUNT(*) FROM minutes_gap_ledger GROUP BY 1").fetchall()
    return {str(k): int(v) for k, v in rows}
