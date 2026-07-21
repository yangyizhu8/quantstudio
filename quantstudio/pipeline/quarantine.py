"""
Quarantine — 校验失败数据隔离区 [E-2]

强制原则（基线 §1.2 第2条）：
- 校验失败数据不得写入 Canonical 层
- 但也不直接丢弃，进隔离区保留原 payload + 失败规则 + 批次ID
- 默认保留 30 天，超期归档（不自动删除，人工 review 后才可删）
- 可修复后重放（重跑对齐+校验+入库）

表结构（Quarantine）：
    quarantine_id (PK) / batch_id / table / source / original_payload(JSON)
    / failed_rules(JSON list) / error_values(JSON) / contract_version / status
    / ingested_at / fixed_at / replayed_at

status 流转：pending_repair → fixed → replayed / dropped(仅人工)
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


CREATE_QUARANTINE_DDL = """
CREATE TABLE IF NOT EXISTS quarantine (
    quarantine_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        TEXT    NOT NULL,
    table_name      TEXT    NOT NULL,
    source          TEXT    NOT NULL,
    original_payload TEXT   NOT NULL,    -- JSON 原始行
    failed_rules    TEXT    NOT NULL,    -- JSON list of rule names
    error_values    TEXT,                -- JSON {field: bad_value}
    contract_version TEXT,
    status          TEXT    NOT NULL DEFAULT 'pending_repair',
    ingested_at     TEXT    NOT NULL,
    fixed_at        TEXT,
    replayed_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_quarantine_batch ON quarantine(batch_id);
CREATE INDEX IF NOT EXISTS idx_quarantine_status ON quarantine(status);
"""


class Quarantine:
    """校验失败数据隔离区（SQLite 存储，轻量零依赖）

    使用：
        q = Quarantine("data/quarantine.db")
        q.write(batch_id="batch_20260713_001", table="stock_daily", source="baostock",
                rows=[{...}, {...}], failed_rules=["PricePositive"], error_values={"close": -5.0})
        pending = q.list_pending()
    """

    def __init__(self, db_path: str | Path,
                 max_rows: int = 500_000,
                 retention_days: int = 7,
                 rate_alert_threshold: int = 1000):
        """隔离区。

        防膨胀三机制（2026-07-17 新增，曾因 bug 误隔离 3天攒 949万行/9.9GB）：
            max_rows: 硬上限，总行数超过则拒绝写入 + ERROR 告警（正常 pipeline 不超几千行）
            retention_days: pending_repair 超 N 天自动转 archived（默认7天，原设计30天过松）
            rate_alert_threshold: 单规则单日隔离超 N 行告警（表示有 bug 持续触发）
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_rows = max_rows
        self.retention_days = retention_days
        self.rate_alert_threshold = rate_alert_threshold
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(CREATE_QUARANTINE_DDL)
            conn.commit()

    def write(self, batch_id: str, table: str, source: str,
              rows: List[Dict], failed_rules: List[str],
              error_values: Optional[Dict[str, Any]] = None,
              contract_version: str = "1.0") -> int:
        """写入隔离区（不丢弃）。返回写入条数。

        防膨胀三机制（每次写入顺带执行，无需调用方改动）：
            1. 硬上限：总行数超 max_rows 拒绝写入（防 bug 导致无限膨胀）
            2. 自动归档：pending_repair 超 retention_days 转 archived
            3. 速率告警：单规则单日超 rate_alert_threshold 行打 ERROR
        """
        if not rows:
            return 0
        now = datetime.now().isoformat()
        today = now[:10]  # YYYY-MM-DD
        rules_key = ",".join(sorted(failed_rules))

        with sqlite3.connect(self.db_path) as conn:
            # ---- 防护2：自动归档超期 pending_repair（在写入前清理，腾出配额）----
            self._auto_archive_expired(conn)

            # ---- 防护1：硬上限检查 ----
            # 只统计 pending_repair：archived 是历史归档（永不删），计入会让上限被占满成定时炸弹
            current = conn.execute(
                "SELECT COUNT(*) FROM quarantine WHERE status='pending_repair'").fetchone()[0]
            if current >= self.max_rows:
                # 报告隔离区「实际」规则分布（而非本次被拒批次的 failed_rules 参数），
                # 避免误导成单一规则 bug。full_range 全量拉取时依赖表(valuation/float_share)
                # 批量拒绝属正常量级，不应误判为 bug。
                dist = conn.execute(
                    "SELECT failed_rules, COUNT(*) c FROM quarantine "
                    "WHERE status='pending_repair' GROUP BY failed_rules ORDER BY c DESC"
                ).fetchall()
                dist_str = ", ".join(f"{r[0]}={r[1]}" for r in dist) or "空"
                logger.error(
                    f"[Quarantine] 达到硬上限 {self.max_rows} 行，拒绝写入 "
                    f"(batch={batch_id} table={table} {len(rows)} rows)。"
                    f"当前隔离区规则分布: {dist_str}。"
                    f"若来自全量拉取(full_range)，依赖表(valuation/float_share)批量拒绝属正常；"
                    f"可运行 archive_expired 腾出配额或调高 max_rows（当前 {self.max_rows}）。")
                return 0

            records = []
            for row in rows:
                # pandas Timestamp 等不可 JSON 序列化的对象转字符串
                payload = {k: (str(v) if hasattr(v, "isoformat") else v) for k, v in row.items()}
                records.append(
                    (batch_id, table, source, json.dumps(payload, ensure_ascii=False, default=str),
                     json.dumps(failed_rules, ensure_ascii=False),
                     json.dumps(error_values or {}, ensure_ascii=False, default=str),
                     contract_version, "pending_repair", now, None, None))
            conn.executemany(
                "INSERT INTO quarantine (batch_id, table_name, source, original_payload, "
                "failed_rules, error_values, contract_version, status, ingested_at, fixed_at, replayed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                records)
            conn.commit()

            # ---- 防护3：速率告警（单规则单日隔离数）----
            today_count = conn.execute(
                "SELECT COUNT(*) FROM quarantine WHERE failed_rules=? AND ingested_at LIKE ?",
                (json.dumps(failed_rules, ensure_ascii=False), today + "%")).fetchone()[0]
            if today_count >= self.rate_alert_threshold:
                logger.warning(
                    f"[Quarantine] 速率提示：规则 {failed_rules} 今日已隔离 {today_count} 行 "
                    f"(阈值 {self.rate_alert_threshold})。若为全量拉取依赖表批量拒绝属正常；"
                    f"否则请检查该规则或 source_priority。")

        logger.warning(f"[Quarantine] batch={batch_id} table={table} "
                       f"{len(records)} rows quarantined (rules={failed_rules})")
        return len(records)

    def _auto_archive_expired(self, conn):
        """防护2：自动归档超期 pending_repair（转 archived，不删除）"""
        cutoff = (datetime.now() - timedelta(days=self.retention_days)).isoformat()
        cur = conn.execute(
            "UPDATE quarantine SET status='archived' "
            "WHERE status='pending_repair' AND ingested_at < ?", (cutoff,))
        n = cur.rowcount
        if n > 0:
            logger.info(f"[Quarantine] 自动归档 {n} 行超期 pending_repair (>{self.retention_days}d)")

    def list_pending(self, table: Optional[str] = None) -> pd.DataFrame:
        """列出待修复的隔离数据"""
        q = "SELECT * FROM quarantine WHERE status='pending_repair'"
        params = []
        if table:
            q += " AND table_name=?"
            params.append(table)
        q += " ORDER BY ingested_at DESC"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return pd.read_sql_query(q, conn, params=params)

    def mark_fixed(self, quarantine_ids: List[int]):
        """标记已修复（待重放）"""
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "UPDATE quarantine SET status='fixed', fixed_at=? WHERE quarantine_id=?",
                [(now, qid) for qid in quarantine_ids])
            conn.commit()

    def mark_replayed(self, quarantine_ids: List[int]):
        """标记已重放成功"""
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "UPDATE quarantine SET status='replayed', replayed_at=? WHERE quarantine_id=?",
                [(now, qid) for qid in quarantine_ids])
            conn.commit()

    def archive_expired(self, retention_days: int = 30) -> int:
        """归档超期未修复的数据（status → archived，不删除）"""
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE quarantine SET status='archived' "
                "WHERE status='pending_repair' AND ingested_at < ?", (cutoff,))
            conn.commit()
            n = cur.rowcount
        if n:
            logger.info(f"[Quarantine] archived {n} expired rows (>{retention_days}d)")
        return n

    def stats(self) -> Dict[str, int]:
        """统计各状态数量"""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT status, COUNT(*) FROM quarantine GROUP BY status")
            return dict(cur.fetchall())
