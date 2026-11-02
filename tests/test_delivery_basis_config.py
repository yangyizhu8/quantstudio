# -*- coding: utf-8 -*-
"""配置债批次（2026-11-02 用户批准）：4 张表基准列声明 + etf_basic 无水位归法。

判据来源（不改判定逻辑，只验证现状）：
- stats.resolve_basis 的第二源 = alignment_rules.schemas[*].time_key；
- stats.in_watermark_system 的例外集 = constants.NO_WATERMARK_TABLES。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quantstudio.delivery import constants as C
from quantstudio.delivery import stats as S

REPO = Path(__file__).resolve().parents[1]
CFG = REPO / "config" / "profiles" / "mcp_only"
RULES = CFG / "alignment_rules.json"
TABLE_MAP = CFG / "questdb_table_map.json"

DECLARED = {
    "ai_research_snapshot": "scan_date",
    "block_trade": "trade_date",
    "broker_recommend": "timestamp",
    "xueqiu_sentiment": "trade_date",
}


def test_declared_time_keys_are_readable_by_stats():
    """V2 前置：stats 的解析函数能读到 4 条声明（字段名/嵌套位置契约）。"""
    tk = S.load_alignment_time_keys(RULES)
    for table, col in DECLARED.items():
        assert tk.get(table) == col, f"{table} 期望 {col}，实际 {tk.get(table)}"


def test_resolve_basis_uses_alignment_source():
    """V2：basis 来自 alignment_rules 第二源，且非 unknown。"""
    table_map = S.load_table_map(TABLE_MAP)
    tk = S.load_alignment_time_keys(RULES)
    for table, col in DECLARED.items():
        basis, src = S.resolve_basis(table, table_map, tk)
        assert basis == col, f"{table}: {basis}"
        assert basis != S.UNKNOWN_BASIS
        assert src == S.BASIS_SOURCE_ALIGNER, f"{table}: {src}"


def test_table_map_left_untouched_for_these_tables():
    """边界：不得手改 questdb_table_map.json —— 这 4 张表在 map 里必须仍为 out_of_map。"""
    table_map = S.load_table_map(TABLE_MAP)
    for table in DECLARED:
        assert table not in table_map, f"{table} 不应出现在生成型 map 中"


def test_etf_basic_is_no_watermark_system():
    """etf_basic 归入 NO-WATERMARK-SYSTEM（方案 A：排除集），即便它有水位行。"""
    assert "etf_basic" in C.NO_WATERMARK_TABLES
    assert S.in_watermark_system("etf_basic", {}, {"etf_basic"}) is False
    # 其它有水位行的表不受影响
    assert S.in_watermark_system("stock_daily", {}, {"stock_daily"}) is True


def test_judge_state_no_watermark_for_etf_basic():
    state = S.judge_state(row_count=1666, max_date=None, watermark_date="2026-11-01",
                          basis="unknown", has_task_config=True, in_system=False)
    assert state == C.TableState.NO_WATERMARK_SYSTEM


def test_no_schema_entry_declares_unknown_basis():
    """结构性守卫：任何 time_key 不得写成 'unknown' 占位。"""
    doc = json.loads(RULES.read_text(encoding="utf-8"))
    for table, spec in (doc.get("schemas") or {}).items():
        key = spec.get("time_key")
        if key is not None:
            assert isinstance(key, str) and key.strip()
            assert key.strip().lower() != "unknown", f"{table} 用了占位 time_key"


@pytest.mark.parametrize("table", sorted(DECLARED))
def test_declared_entry_keeps_columns_key(table):
    """aligner 有 schema['columns'] 裸索引 ⇒ 新条目必须带 columns（空 dict 亦可）。"""
    doc = json.loads(RULES.read_text(encoding="utf-8"))
    spec = doc["schemas"][table]
    assert "columns" in spec and isinstance(spec["columns"], dict)
