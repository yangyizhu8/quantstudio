# -*- coding: utf-8 -*-
"""逐表统计与状态判定（T015；FR-006 / FR-018 / Q2 裁定）。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from quantstudio.delivery import stats as S
from quantstudio.delivery.constants import TABLE_SET_RULE, TableState


# ── 状态判定矩阵（优先级顺序）────────────────────────────────────────────────
def test_state_empty_when_no_rows_and_no_watermark():
    assert (
        S.judge_state(row_count=0, max_date=None, watermark_date=None, basis="trade_date")
        == TableState.EMPTY
    )


def test_state_missing_when_rows_but_no_watermark():
    """MISSING 是诊断价值最高的一态：增量会退化为全历史重拉（Q2 裁定的核心理由）。"""
    assert (
        S.judge_state(row_count=100, max_date="2026-10-30", watermark_date=None, basis="trade_date")
        == TableState.MISSING
    )


def test_state_unknown_basis_when_basis_missing():
    assert (
        S.judge_state(row_count=100, max_date=None, watermark_date="2026-10-30", basis="unknown")
        == TableState.UNKNOWN_BASIS
    )


def test_state_unknown_source_when_no_task_config():
    assert (
        S.judge_state(
            row_count=10,
            max_date="2026-10-30",
            watermark_date="2026-10-30",
            basis="trade_date",
            has_task_config=False,
        )
        == TableState.UNKNOWN_SOURCE
    )


@pytest.mark.parametrize(
    "max_date, watermark, expected",
    [
        ("2026-10-30", "2026-10-30", TableState.OK),
        ("2026-10-30", "2026-10-25", TableState.BEHIND),
        ("2026-10-25", "2026-10-30", TableState.AHEAD),
    ],
)
def test_state_delta_directions(max_date, watermark, expected):
    assert (
        S.judge_state(
            row_count=5, max_date=max_date, watermark_date=watermark, basis="trade_date"
        )
        == expected
    )


# ── 日期归一 ────────────────────────────────────────────────────────────────
def test_to_date_str_handles_epoch_ms_and_iso():
    epoch_ms = int(datetime(2026, 10, 30, 12, 0).timestamp() * 1000)
    assert S.to_date_str(epoch_ms) == "2026-10-30"
    assert S.to_date_str("2026-10-30T13:00:00") == "2026-10-30"
    assert S.to_date_str("2026-10-30") == "2026-10-30"
    assert S.to_date_str(None) is None


# ── 表集边界（Q2：map ∩ 实际存在）────────────────────────────────────────────
def test_select_table_set_is_intersection_and_stable_order():
    table_map = {"b": {}, "a": {}, "c": {}}
    assert S.select_table_set(table_map, {"a", "c"}) == ["a", "c"]
    assert S.select_table_set(table_map, {"z"}) == []


def test_summarize_counts_states_and_rule():
    rows = [
        {"table": "a", "state": TableState.OK, "date_basis_col": "trade_date"},
        {"table": "b", "state": TableState.EMPTY, "date_basis_col": "trade_date"},
        {"table": "c", "state": TableState.UNKNOWN_BASIS, "date_basis_col": "unknown"},
        {"table": "d", "state": TableState.MISSING, "date_basis_col": "trade_date"},
    ]
    summary = S.summarize(rows, out_of_map=["zzz"])
    assert summary["rule"] == TABLE_SET_RULE
    assert summary["count"] == 4
    assert summary["empty_count"] == 1
    assert summary["unknown_basis"] == ["c"]
    assert summary["out_of_map"] == ["zzz"]
    assert S.defect_tables(rows) == ["d"]
    assert S.can_incremental(rows) is False
    assert S.can_incremental(rows[:2]) is True


# ── 统计（注入 fake query，无需真实 duckdb）─────────────────────────────────
def _fake_query(table_rows, max_dates):
    def query(sql: str):
        if "duckdb_tables()" in sql:
            return [(name,) for name in table_rows]
        if sql.startswith("SELECT count(*)"):
            name = sql.split('FROM "')[1].rstrip('"')
            return [(table_rows[name],)]
        if sql.startswith("SELECT max("):
            name = sql.split('FROM "')[1].rstrip('"')
            return [(max_dates.get(name),)]
        raise AssertionError(f"未预期的 SQL：{sql}")

    return query


def test_collect_table_stats_uses_watermark_and_basis():
    table_map = {
        "stock_daily": {
            "source": "questdb",
            "target_table": "stock_daily",
            "freq": "daily",
            "watermark_basis": "trade_date",
            "task_name": "mcp_stock_daily",
        },
        "stock_minute": {
            "source": "questdb",
            "target_table": "stock_minute",
            "freq": "minute",
            "watermark_basis": "ingest_time",
            "task_name": "mcp_stock_minute",
        },
        "no_basis": {"source": "questdb", "freq": "daily", "task_name": None},
    }
    query = _fake_query(
        {"stock_daily": 100, "stock_minute": 5, "no_basis": 0},
        {"stock_daily": "2026-10-30", "stock_minute": "2026-10-30"},
    )
    watermark = {
        ("questdb", "stock_daily", "daily"): "2026-10-29",
        ("questdb", "stock_minute", "minute"): "2026-10-30",
    }
    rows = S.collect_table_stats(query, table_map, list(table_map), watermark)

    by_name = {r["table"]: r for r in rows}
    assert by_name["stock_daily"]["date_basis_col"] == "trade_date"
    assert by_name["stock_daily"]["state"] == TableState.BEHIND
    assert by_name["stock_minute"]["date_basis_col"] == "ingest_time"
    assert by_name["stock_minute"]["state"] == TableState.OK
    # 缺 watermark_basis ⇒ unknown，且不得输出 max_date（不猜）
    assert by_name["no_basis"]["date_basis_col"] == "unknown"
    assert by_name["no_basis"]["basis_source"] == "none"
    assert by_name["no_basis"]["max_date"] is None
    assert by_name["no_basis"]["state"] == TableState.EMPTY


def test_collect_table_stats_progress_and_estimate():
    table_map = {
        "t1": {"source": "s", "freq": "daily", "watermark_basis": "trade_date", "task_name": "x"},
    }
    seen = []
    query = _fake_query({"t1": 7}, {"t1": "2026-10-30"})
    rows = S.collect_table_stats(
        query,
        table_map,
        ["t1"],
        {},
        progress=lambda done, total, phase: seen.append((done, total, phase)),
    )
    assert seen and seen[-1][0] == 1 and "stats:1/1" in seen[-1][2]
    assert rows[0]["row_count_method"] == "exact"

# ── FR-006 v5 / FR-018 v5：真实配置形状下的表集与标签（W2 验收判据）────────────
def test_fr006v5_table_set_and_labels_on_real_config_shape(tmp_path):
    """用**真实** table_map + alignment_rules 造小库，验证：
    ① 水位体系表进四态；② map 的 passthrough 表标 NO-WATERMARK-SYSTEM；③ 假 MISSING = 0。
    """
    import duckdb

    from quantstudio.delivery.constants import (
        BASIS_SOURCE_ALIGNER,
        BASIS_SOURCE_MAP,
        BASIS_SOURCE_NONE,
        TableState,
    )
    from quantstudio.delivery.stats import (
        collect_table_stats,
        in_watermark_system,
        load_alignment_time_keys,
        load_table_map,
        resolve_basis,
        select_table_set,
    )

    repo = Path(__file__).resolve().parents[1]
    cfg = repo / "config" / "profiles" / "mcp_only"
    table_map = load_table_map(cfg / "questdb_table_map.json")
    time_keys = load_alignment_time_keys(cfg / "alignment_rules.json")

    # 造库：5 张水位表（含 3 种基准列来源）+ 2 张 map passthrough 表 + 1 张无基准水位表
    wm_tables = ["etf_daily", "stock_daily", "balance_statement", "stock_dividend", "block_trade"]
    passthrough = ["cninfo_first_rating", "cyq_chips"]
    db_path = tmp_path / "main.db"
    con = duckdb.connect(str(db_path))
    try:
        for name in wm_tables + passthrough:
            basis, _src = resolve_basis(name, table_map, time_keys)
            col = "trade_date" if basis == "unknown" else basis
            con.execute(f'CREATE TABLE "{name}"({col} BIGINT)')
            con.execute(f'INSERT INTO "{name}" VALUES (1793308800000)')
        con.execute(
            "CREATE TABLE source_watermark(source VARCHAR, table_name VARCHAR, freq VARCHAR, last_date BIGINT)"
        )
        for name in wm_tables:
            freq = "1min" if name.endswith("minutes") else "daily"
            con.execute(
                "INSERT INTO source_watermark VALUES ('mcp', ?, ?, 1793308800000)", [name, freq]
            )
    finally:
        con.close()

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        def query(sql):
            return con.execute(sql).fetchall()

        present = [r[0] for r in query("SELECT table_name FROM duckdb_tables()")]
        wm = {str(r[1]) for r in query("SELECT source, table_name, freq, last_date FROM source_watermark")}
        names = select_table_set(table_map, present, wm)
        rows = collect_table_stats(
            query, table_map, names, {("mcp", t, "daily"): "2026-10-30" for t in wm_tables},
            time_keys=time_keys, watermark_tables=wm,
        )
    finally:
        con.close()

    by_name = {r["table"]: r for r in rows}
    # ① 水位表：有基准的进四态；无基准（block_trade 不在 alignment）标 UNKNOWN-BASIS
    for name in ("etf_daily", "stock_daily", "balance_statement", "stock_dividend"):
        row = by_name[name]
        assert row["state"] in {TableState.OK, TableState.BEHIND, TableState.AHEAD}, row
        assert row["basis_source"] in {BASIS_SOURCE_MAP, BASIS_SOURCE_ALIGNER}
    assert by_name["block_trade"]["state"] == TableState.UNKNOWN_BASIS
    assert by_name["block_trade"]["basis_source"] == BASIS_SOURCE_NONE
    assert by_name["block_trade"]["max_date"] is None
    # ② passthrough：NO-WATERMARK-SYSTEM 且不输出水位
    for name in passthrough:
        row = by_name[name]
        assert row["state"] == TableState.NO_WATERMARK_SYSTEM, row
        assert row["watermark_last_date"] is None
        assert row["max_date"] is None
        assert in_watermark_system(name, table_map, wm) is False
    # ③ 假 MISSING = 0
    assert not [r for r in rows if r["state"] == TableState.MISSING]
