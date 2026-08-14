"""A4 变更检测闭环：客户端侧 UpdateDetector。

让所有增量拉取入口（daemon 定时 / GUI 手动 / CLI）在拉取前检测"云端哪些
(table, trade_date) 窗口被更新过"，对 repair/full 类更新局部重拉（DEDUP 幂等覆盖）。

架构：
- UpdateDetector Protocol：query_updated_since(last_sync) → 更新记录列表
- MockUpdateDetector：始终返回空（先行集成，零行为变化）
- MCPUpdateDetector：调 MCP client 新方法（工具上线后启用）

last_sync 基准按 **table 粒度**持久化（修正 1：避免全局单基准的漏检漏洞）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)

# last_sync 持久化表（独立轻量表，不走框架 schema 指纹校验——A4 私有表）
_LAST_SYNC_DDL = """
    CREATE TABLE IF NOT EXISTS _a4_last_sync (
        table_name   VARCHAR PRIMARY KEY,
        last_sync_ts VARCHAR,
        updated_at   TIMESTAMP
    )
"""


class UpdateDetector(Protocol):
    """变更检测器协议。"""

    def query_updated_since(self, last_sync: str) -> List[Dict]:
        """查询自 last_sync（ISO 8601 UTC）以来的云端更新记录。

        Returns:
            [{"table_name": str, "trade_date": str (YYYY-MM-DD),
              "update_source": str, "rows_pushed": int}, ...]
        """
        ...


class MockUpdateDetector:
    """Mock 后端：始终返回空列表（先行集成，零行为变化）。"""

    def query_updated_since(self, last_sync: str) -> List[Dict]:
        return []


class MCPUpdateDetector:
    """MCP 后端：调 MCP client 的 query_updated_since 工具。

    工具上线后启用（任务 A/B 完成后）。当前 client 无此方法，调用会 AttributeError——
    故默认注入 MockUpdateDetector，上线后一行替换。
    """

    def __init__(self, client):
        self._client = client

    def query_updated_since(self, last_sync: str) -> List[Dict]:
        return self._client.query_updated_since(last_sync)


def ensure_last_sync_table(conn) -> None:
    """幂等建 _a4_last_sync 表（duckdb 连接）。"""
    conn.execute(_LAST_SYNC_DDL)


def load_last_sync(conn, table: str) -> Optional[str]:
    """读取指定 table 的 last_sync 基准（ISO 8601 UTC）。无基准返回 None。"""
    ensure_last_sync_table(conn)
    row = conn.execute(
        "SELECT last_sync_ts FROM _a4_last_sync WHERE table_name=?", [table]
    ).fetchone()
    return row[0] if row else None


def save_last_sync(conn, table: str, ts: Optional[str] = None) -> None:
    """持久化指定 table 的 last_sync 基准。ts 为 None 时用当前 UTC 时间。"""
    ensure_last_sync_table(conn)
    if ts is None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO _a4_last_sync (table_name, last_sync_ts, updated_at) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT (table_name) DO UPDATE SET last_sync_ts=EXCLUDED.last_sync_ts, "
        "updated_at=EXCLUDED.updated_at",
        [table, ts, now]
    )
