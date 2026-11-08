# -*- coding: utf-8 -*-
"""T3.1 索引一致性 canary —— 把「ART 索引损坏」从「突然 FATAL」变成「提前告警」。

依据（DuckDB issue #23645 原话）：
> **Detectable before the fatal**: in the corrupt-but-not-yet-fatal state,
> indexed/filtered queries return short counts while full scans are correct —
> e.g. `SELECT COUNT(*) WHERE stage = 1` returned 51 vs 84 from `GROUP BY stage`.
> **A filtered-vs-full-scan parity check makes a reliable canary.**

设计：
- 对每张关键表，用「按主键前缀分组求和」vs「全表计数」对拍
- 两者不等 ⇒ 索引已不一致（但尚未 fatal）⇒ 立即告警
- 纯只读、零副作用、可高频调用

实测锚点（2026-11-08）：`stock_minutes` 重建后跑 V3-C，
`full-scan = GROUP BY freq = 251,877,433` 一致 ⇒ 索引健康。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

# 关键表与其主键列（canary 用主键首列做分组对拍）
CANARY_TABLES: Sequence[tuple] = (
    # (表名, 分组列, 说明)
    ("stock_minutes", "freq", "分钟表主键含 freq，按 freq 分组对拍"),
    ("etf_minutes", "freq", "同上"),
    ("stock_daily", "code", "日线主键含 code，按 code 分组对拍（可加 LIMIT 采样）"),
    ("etf_daily", "code", "同上"),
    ("qfq_trigger_queue", "status", "触发队列按 status 分组"),
)


@dataclass
class CanaryResult:
    """单表 canary 结果。"""

    table: str
    group_col: str
    full_count: int
    grouped_count: int
    ok: bool
    detail: str

    @property
    def diff(self) -> int:
        return self.full_count - self.grouped_count


def is_consistent(full_count: int, grouped_count: int) -> bool:
    """计数是否一致（纯判定，便于单测）。"""
    return int(full_count) == int(grouped_count)


def judge(full_count: int, grouped_count: int, table: str, group_col: str) -> CanaryResult:
    """构造判定结果（不触库）。"""
    ok = is_consistent(full_count, grouped_count)
    diff = int(full_count) - int(grouped_count)
    if ok:
        detail = f"一致（{full_count:,}）"
    elif diff > 0:
        detail = (f"**索引不一致：filtered 少 {diff:,} 行**"
                  f"（full={full_count:,} grouped={grouped_count:,}）"
                  f" ⇒ 疑似 ART 索引损坏（#23645 早期态）")
    else:
        detail = (f"**索引不一致：filtered 多 {-diff:,} 行**"
                  f"（full={full_count:,} grouped={grouped_count:,}）"
                  f" ⇒ 疑似重复索引项")
    return CanaryResult(table=table, group_col=group_col,
                        full_count=int(full_count), grouped_count=int(grouped_count),
                        ok=ok, detail=detail)


def sql_for(table: str, group_col: str) -> str:
    """生成对拍 SQL（两条：full-scan 与 grouped）。"""
    q = f'"{group_col}"' if group_col in ("time", "close", "open") else group_col
    return (
        f"SELECT COUNT(*) FROM {table}",  # full-scan
        f"SELECT SUM(c) FROM (SELECT COUNT(*) c FROM {table} GROUP BY {q})",  # grouped
    )


def run_canary(conn, tables: Optional[Sequence[tuple]] = None) -> List[CanaryResult]:
    """对关键表逐个跑 canary（需传入已打开的 duckdb 连接）。

    任一表抛错 ⇒ 记为 failed，不阻断其他表（巡检不得因单表失败中断）。
    """
    out: List[CanaryResult] = []
    for table, group_col, _note in (tables or CANARY_TABLES):
        sql_full, sql_grouped = sql_for(table, group_col)
        try:
            full = conn.execute(sql_full).fetchone()[0] or 0
            grouped = conn.execute(sql_grouped).fetchone()[0] or 0
            out.append(judge(full, grouped, table, group_col))
        except Exception as e:  # noqa: BLE001 — 巡检不中断
            out.append(CanaryResult(
                table=table, group_col=group_col,
                full_count=-1, grouped_count=-1, ok=False,
                detail=f"查询失败: {type(e).__name__}: {str(e)[:120]}"))
    return out


def any_broken(results: List[CanaryResult]) -> bool:
    """是否有表不一致（供告警判定）。"""
    return any(not r.ok for r in results)


def format_report(results: List[CanaryResult]) -> str:
    """格式化报告（供日志/上报）。"""
    lines = ["[T3.1 index-canary] 索引一致性巡检"]
    for r in results:
        mark = "✅" if r.ok else "❌"
        lines.append(f"  {mark} {r.table}({r.group_col}): {r.detail}")
    broken = [r.table for r in results if not r.ok]
    if broken:
        lines.append(f"  ⇒ **告警：{len(broken)} 张表索引不一致：{broken}**")
        lines.append("  ⇒ 立即动作：按 docs/art-index-corruption-rootcause-20261108.md §五 T3.3 重建 SOP 处理")
    else:
        lines.append(f"  ⇒ 全部一致（{len(results)} 张表）")
    return "\n".join(lines)
