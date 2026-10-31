# -*- coding: utf-8 -*-
"""逐表统计与状态判定（FR-006 / FR-018；plan §5、data-model §2.4–2.6）。

**表集边界（②审 Q2 裁定）**：主集 = `questdb_table_map` 的 64 张表 ∩ 主库 introspection
**实际存在**的表。`source_watermark` 行只作状态的数据来源，不参与集合边界——否则
`MISSING`（有数据无水位）在定义上就不可能被报告，而它正是"增量退化为全历史重拉"的预告。

导出侧与复验侧 MUST 共用本模块（同一实现，避免口径分叉）。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .constants import (
    BASIS_SOURCE_ALIGNER,
    BASIS_SOURCE_MAP,
    BASIS_SOURCE_NONE,
    TABLE_SET_RULE,
    TableState,
    UNKNOWN_BASIS,
)

Query = Callable[[str], List[Tuple]]


# ── 配置真源 ────────────────────────────────────────────────────────────────
def load_table_map(path: Path) -> Dict[str, Dict]:
    """读 `questdb_table_map.json` 的 tables 段（逐表 `watermark_basis` 是真相源）。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    tables = doc.get("tables") if isinstance(doc, dict) else None
    if not isinstance(tables, dict):
        raise ValueError(f"{path} 缺 tables 段或结构不符")
    return tables


def load_alignment_time_keys(path: Path) -> Dict[str, str]:
    """读 `alignment_rules.json` 的 `schemas[*].time_key`（与运行时 `_get_safe_watermark` 同源）。

    map 未覆盖的水位体系表（如 `etf_daily` / `stock_daily`）靠这里取基准列。
    """
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    schemas = doc.get("schemas") if isinstance(doc, dict) else None
    if not isinstance(schemas, dict):
        return {}
    out: Dict[str, str] = {}
    for table, spec in schemas.items():
        if isinstance(spec, dict):
            key = spec.get("time_key")
            if isinstance(key, str) and key.strip():
                out[str(table)] = key.strip()
    return out


def resolve_basis(table: str, table_map: Dict[str, Dict], time_keys: Dict[str, str]):
    """返回 `(date_basis_col, basis_source)`；两处均无 ⇒ `("unknown", "none")`（不猜）。"""
    spec = table_map.get(table) or {}
    basis = spec.get("watermark_basis")
    if isinstance(basis, str) and basis.strip():
        return basis.strip(), BASIS_SOURCE_MAP
    key = time_keys.get(table)
    if isinstance(key, str) and key.strip():
        return key.strip(), BASIS_SOURCE_ALIGNER
    return UNKNOWN_BASIS, BASIS_SOURCE_NONE


def in_watermark_system(table: str, table_map: Dict[str, Dict], watermark_tables) -> bool:
    """是否属水位体系（FR-006 v5）：有水位行 **或** map 的 dataset_kind != passthrough。"""
    if table in set(watermark_tables):
        return True
    spec = table_map.get(table) or {}
    kind = spec.get("dataset_kind")
    if kind is None:
        # map 未标 dataset_kind ⇒ 保守视为属水位体系（避免把未知当 passthrough 而漏报 MISSING）
        return True
    return str(kind) != "passthrough"


def existing_tables(query: Query) -> List[str]:
    """主库 introspection：实际存在的表名（只读）。"""
    rows = query("SELECT table_name FROM duckdb_tables()")
    return sorted({str(r[0]) for r in rows})


def select_table_set(
    table_map: Dict[str, Dict], present: Iterable[str], watermark_tables: Iterable[str] = ()
) -> List[str]:
    """受统计表集（FR-006 v5）= (应推进水位表 ∪ map 覆盖表) ∩ 库内实际存在。

    顺序稳定：先按 map 声明序，再追加水位体系表（按字母序），保证两次导出可比。
    """
    present_set = {str(x) for x in present}
    names = [name for name in table_map if name in present_set]
    for name in sorted({str(x) for x in watermark_tables}):
        if name in present_set and name not in names:
            names.append(name)
    return names


# ── 水位行 ──────────────────────────────────────────────────────────────────
def load_watermark_rows(rows: Iterable[Tuple]) -> Dict[Tuple[str, str, str], str]:
    """把 `source_watermark` 行转成 {(source, table_name, freq): 'YYYY-MM-DD'}。

    库内 `last_date` 为 epoch 毫秒（见 spec「关键约束依据」表）。
    """
    out: Dict[Tuple[str, str, str], str] = {}
    for source, table_name, freq, last_date in rows:
        key = (str(source), str(table_name), str(freq))
        out[key] = to_date_str(last_date)
    return out


def to_date_str(value) -> Optional[str]:
    """epoch 毫秒 / datetime / date / 字符串 → 'YYYY-MM-DD'（本地时区）。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000.0).date().isoformat()
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(int(text) / 1000.0).date().isoformat()
    for sep in ("T", " "):
        if sep in text:
            text = text.split(sep)[0]
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except Exception:
        return None


def _neg_date(value: Optional[str]) -> str:
    """把日期串转成可用于"取较新者"的排序键（越大越新 ⇒ 排序取负序）。"""
    if not value:
        return "9999-12-31"
    return "".join(chr(0x10FFFF - ord(c)) for c in value)


def _days_between(later: Optional[str], earlier: Optional[str]) -> Optional[int]:
    if not later or not earlier:
        return None
    try:
        return (date.fromisoformat(later) - date.fromisoformat(earlier)).days
    except Exception:
        return None


# ── 状态判定（顺序即优先级，data-model §2.6）────────────────────────────────
def judge_state(
    *,
    row_count: Optional[int],
    max_date: Optional[str],
    watermark_date: Optional[str],
    basis: str,
    has_task_config: bool = True,
    in_system: bool = True,
) -> str:
    """返回 TableState 常量之一。

    优先级：NO-WATERMARK-SYSTEM → EMPTY → MISSING → UNKNOWN-BASIS → UNKNOWN-SOURCE → 四态。
    """
    if not in_system:
        return TableState.NO_WATERMARK_SYSTEM
    if row_count is None or row_count < 0:
        return TableState.UNKNOWN_BASIS if basis == UNKNOWN_BASIS else TableState.MISSING
    if row_count == 0 and watermark_date is None:
        return TableState.EMPTY
    if row_count > 0 and watermark_date is None:
        return TableState.MISSING
    if basis == UNKNOWN_BASIS:
        return TableState.UNKNOWN_BASIS
    if not has_task_config:
        return TableState.UNKNOWN_SOURCE
    delta = _days_between(max_date, watermark_date)
    if delta is None:
        return TableState.MISSING
    if delta == 0:
        return TableState.OK
    return TableState.BEHIND if delta > 0 else TableState.AHEAD


# ── 统计 ────────────────────────────────────────────────────────────────────
def collect_table_stats(
    query: Query,
    table_map: Dict[str, Dict],
    table_names: List[str],
    watermark_rows: Dict[Tuple[str, str, str], str],
    *,
    time_keys: Optional[Dict[str, str]] = None,
    watermark_tables: Iterable[str] = (),
    known_tasks: Optional[Iterable[str]] = None,
    row_count_estimate: bool = False,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> List[Dict]:
    """逐表统计（只读）。`query(sql)` 由调用方注入（导出侧=副本，复验侧=新库）。

    FR-006 v5：受统计表含两类——map 覆盖表（多 passthrough）与水位体系表（map 外）；
    后者基准列取 `alignment_rules.time_key`。passthrough 表标 `NO-WATERMARK-SYSTEM` 且**不输出**水位/max_date。
    """
    task_names = set(known_tasks) if known_tasks is not None else None
    wm_tables = set(watermark_tables)
    keys = dict(time_keys or {})
    total = len(table_names)
    out: List[Dict] = []

    for idx, name in enumerate(table_names, start=1):
        spec = table_map.get(name, {}) or {}
        source = str(spec.get("source") or "questdb")
        freq = str(spec.get("freq") or "unknown")
        target = str(spec.get("target_table") or name)
        task = spec.get("task_name")

        in_system = in_watermark_system(name, table_map, wm_tables)

        if row_count_estimate:
            rows = query(
                "SELECT estimated_size FROM duckdb_tables() WHERE table_name = "
                + _sql_literal(name)
            )
            row_count = int(rows[0][0]) if rows and rows[0][0] is not None else None
            method = "estimate"
        else:
            rows = query(f'SELECT count(*) FROM "{name}"')
            row_count = int(rows[0][0]) if rows else None
            method = "exact"

        basis, basis_source = resolve_basis(name, table_map, keys)
        max_date: Optional[str] = None
        if in_system and basis != UNKNOWN_BASIS:
            rows = query(f'SELECT max("{basis}") FROM "{name}"')
            max_date = to_date_str(rows[0][0]) if rows else None

        watermark_date: Optional[str] = None
        if in_system:
            watermark_date = watermark_rows.get((source, target, freq))
            if watermark_date is None:
                # map 未覆盖的表没有 freq/source 真源；同一表可能存在多 source 行
                # （实测：etf_minutes / stock_minutes / stock_namechange 各 2 行）
                # 选取顺序：同 freq 优先 → source 优先级 mcp > akshare > questdb → 日期较新者
                priority = {"mcp": 0, "akshare": 1, "questdb": 2}
                best = None  # (rank_freq, rank_src, neg_date, value, src, frq)
                for (src, tbl, frq), value in watermark_rows.items():
                    if tbl != target:
                        continue
                    rank = (0 if frq == freq else 1, priority.get(src, 9), _neg_date(value))
                    if best is None or rank < best[0]:
                        best = (rank, value, src, frq)
                if best is not None:
                    watermark_date, source, freq = best[1], best[2], best[3]

        has_task = True
        if task_names is not None and task is not None:
            has_task = str(task) in task_names

        state = judge_state(
            row_count=row_count,
            max_date=max_date,
            watermark_date=watermark_date,
            basis=basis,
            has_task_config=has_task,
            in_system=in_system,
        )
        out.append(
            {
                "table": name,
                "source": source,
                "freq": freq,
                "date_basis_col": basis,
                "basis_source": basis_source,
                "row_count": row_count,
                "row_count_method": method,
                "max_date": max_date,
                "watermark_last_date": watermark_date,
                "state": state,
            }
        )
        if progress:
            progress(idx, total, f"stats:{idx}/{total}")

    return out


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def summarize(stats: List[Dict], *, out_of_map: Optional[Iterable[str]] = None) -> Dict:
    """聚合 `table_set` 段（count/empty_count/unknown_basis/out_of_map）。"""
    states: Dict[str, int] = {}
    for row in stats:
        states[row["state"]] = states.get(row["state"], 0) + 1
    return {
        "rule": TABLE_SET_RULE,
        "count": len(stats),
        "empty_count": states.get(TableState.EMPTY, 0),
        "unknown_basis": [r["table"] for r in stats if r["date_basis_col"] == UNKNOWN_BASIS],
        "out_of_map": sorted(str(x) for x in (out_of_map or [])),
        "state_counts": states,
    }


def defect_tables(stats: List[Dict]) -> List[str]:
    """缺陷表清单（MISSING/BEHIND/AHEAD）——用于「可增量」结论。"""
    from .constants import DEFECT_STATES

    return [r["table"] for r in stats if r["state"] in DEFECT_STATES]


def can_incremental(stats: List[Dict]) -> bool:
    return not defect_tables(stats)
