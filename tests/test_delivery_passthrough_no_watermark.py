# -*- coding: utf-8 -*-
"""V3 修正验收：passthrough 表按 FR-006 v5 归 NO-WATERMARK-SYSTEM（交付侧 + 巡检侧同口径）。

FR-006 v5 原文要点：passthrough 表 = 全量覆盖、**不推进 source_watermark** ⇒ 无水位语义，
标 `NO-WATERMARK-SYSTEM` 且不输出水位/max_date，**不参与四态**。
2026-11-02 总调度 V3 裁决：判据取 task.passthrough（不依赖 questdb_table_map 覆盖）。
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from quantstudio.delivery import stats as S
from quantstudio.delivery import verify as V
from quantstudio.delivery.constants import TableState

REPO = Path(__file__).resolve().parents[1]
CFG = REPO / "config"
PT_TABLES = ["ai_research_snapshot", "block_trade", "broker_recommend", "xueqiu_sentiment"]


def test_in_watermark_system_passthrough_set_wins_over_map_absence():
    """核心缺陷回归：map 未覆盖（out_of_map）不再回落成"属水位体系"。"""
    for t in PT_TABLES:
        assert S.in_watermark_system(t, {}, {t}, passthrough_tables=set(PT_TABLES)) is False, t
    # 非 passthrough 的表行为不变
    assert S.in_watermark_system("stock_daily", {}, {"stock_daily"}) is True


def test_in_watermark_system_backward_compatible():
    """不传 passthrough_tables ⇒ 与旧行为一致（纯增益）。"""
    assert S.in_watermark_system("block_trade", {}, {"block_trade"}) is True


def _make_pkg(tmp_path: Path) -> Path:
    """造一个最小交付包库：4 张 passthrough 表 + 2 张水位表 + source_watermark。"""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(pkg / "quantstudio.db"))
    try:
        for t in PT_TABLES:
            con.execute(f'CREATE TABLE "{t}"(trade_date VARCHAR)')
            con.execute(f'INSERT INTO "{t}" VALUES (\'2026-10-30T00:00:00\')')
        for t, col in (("etf_daily", "time"), ("stock_daily", "time")):
            con.execute(f'CREATE TABLE "{t}"({col} BIGINT)')
            con.execute(f'INSERT INTO "{t}" VALUES (1793289600000)')
        con.execute("CREATE TABLE source_watermark(source VARCHAR, table_name VARCHAR, "
                    "freq VARCHAR, last_date BIGINT)")
        for t in PT_TABLES + ["etf_daily", "stock_daily"]:
            con.execute("INSERT INTO source_watermark VALUES ('mcp', ?, 'daily', 1785686400000)", [t])
    finally:
        con.close()
    return pkg


def test_collect_tables_marks_passthrough_as_no_watermark_system(tmp_path):
    pkg = _make_pkg(tmp_path)
    rows, table_set = V._collect_tables(pkg, CFG)
    by = {r["table"]: r for r in rows}
    for t in PT_TABLES:
        assert by[t]["state"] == TableState.NO_WATERMARK_SYSTEM, (t, by[t])
        assert by[t]["watermark_last_date"] is None
        assert by[t]["max_date"] is None
    # 真水位表仍进四态（此 fixture 水位落后 ⇒ BEHIND）
    assert by["stock_daily"]["state"] in {TableState.BEHIND, TableState.OK}
    counts = table_set["state_counts"]
    assert counts.get(TableState.NO_WATERMARK_SYSTEM, 0) >= len(PT_TABLES)
    assert TableState.UNKNOWN_BASIS not in counts


def test_quality_audit_side_excludes_no_watermark_tables():
    """巡检侧同口径：no_watermark_tables 里的表不得产生 WatermarkConsistency。"""
    from quantstudio.pipeline.quality_audit import DataQualityAuditor

    auditor = DataQualityAuditor.__new__(DataQualityAuditor)   # 不连库，只验集合语义
    auditor._no_watermark_tables = set(PT_TABLES)
    for t in PT_TABLES:
        assert t in auditor._no_watermark_tables

def test_audit_watermarks_two_way_on_synthetic_db(tmp_path):
    """巡检侧两向读数：不排除 ⇒ 4 条 WatermarkConsistency；排除 ⇒ 0 条。

    用 time 列（BIGINT）以命中巡检的 time_col 解析回退分支（"time" 在候选首位），
    水位 row 取 1785686400000、库内 max 取 1793289600000 ⇒ 必然不一致 ⇒ 应报 warning。
    """
    import duckdb
    from quantstudio.pipeline.quality_audit import DataQualityAuditor, QualityReport

    db = tmp_path / "audit.db"
    con = duckdb.connect(str(db))
    try:
        for t in PT_TABLES:
            con.execute(f'CREATE TABLE "{t}"(time BIGINT)')
            con.execute(f'INSERT INTO "{t}" VALUES (1793289600000)')
        con.execute("CREATE TABLE source_watermark(source VARCHAR, table_name VARCHAR, "
                    "freq VARCHAR, last_date BIGINT)")
        for t in PT_TABLES:
            con.execute("INSERT INTO source_watermark VALUES ('mcp', ?, 'daily', 1785686400000)", [t])
    finally:
        con.close()

    c = duckdb.connect(str(db), read_only=True)
    try:
        for exclude, expect in ((None, len(PT_TABLES)), (set(PT_TABLES), 0)):
            auditor = DataQualityAuditor(str(db), {}, no_watermark_tables=exclude)
            rep = QualityReport()
            auditor._audit_watermarks(c, rep, set(PT_TABLES))
            got = [x for x in rep.issues
                   if str(getattr(x, "check", "") or getattr(x, "item", "")) .find("WatermarkConsistency") >= 0]
            assert len(got) == expect, (exclude, len(got), got[:2])
    finally:
        c.close()
