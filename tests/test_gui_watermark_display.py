"""firefly「水位线」案（2026-10-18）N4 / FP-1·FP-2 防回归测试。

缺陷族：QFQ 水位门禁-周期生命周期（展示层失真：只有「水位线」一列，
把"游标被门控保持"误读成"数据缺失"）。

本文件锁定的契约（A1–A8 判据）：
  A1 数据止点 == SQL ``max(time)`` 真值；
  A2 无 ``time`` 列的表 → ``no_time_column``（不适用，而非报错）；
  A3 非法表名 → ``invalid_identifier``（绝不拼进 SQL）；
  A4 60s TTL 缓存命中（第二次不查库）；
  A5 **降级不缓存**（超时/忙不得被固化成"无数据"）；
  A6 门控状态：held / deferred / normal 三态判定；
  A7 聚合只读（不改变任何表行数）；
  A8 托管表集合与 pipeline 单一真源一致 + GUI 列结构（9 列 / 索引 5,6,7,8）。

运行：.venv/Scripts/python -m pytest tests/test_gui_watermark_display.py -q
"""

from __future__ import annotations

import inspect

import duckdb
import pandas as pd
import pytest

from quantstudio.gui.db_helper import (
    QFQ_COORDINATED_TABLES,
    WATERMARK_STATE_LABELS,
    DbHelper,
)
from quantstudio.gui.tabs.task_tab import TaskTab

TS_1016 = 1792080000000      # 2026-10-16 00:00 (UTC+8)
TS_0924 = 1790233200000      # 2026-09-24 15:00


def _make_db(path) -> None:
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE stock_daily (code VARCHAR, time BIGINT, close DOUBLE)")
    con.execute("CREATE TABLE etf_daily (code VARCHAR, time BIGINT)")
    con.execute("CREATE TABLE etf_minutes (code VARCHAR, time BIGINT)")
    con.execute("CREATE TABLE no_time_tbl (code VARCHAR, ex_date BIGINT)")
    con.execute("INSERT INTO stock_daily VALUES ('000001', ?, 1.0)", [TS_1016])
    con.execute("INSERT INTO etf_daily VALUES ('510300', ?)", [TS_1016])
    con.execute("INSERT INTO no_time_tbl VALUES ('000001', ?)", [TS_1016])
    con.execute(
        "CREATE TABLE source_watermark (source VARCHAR, table_name VARCHAR, freq VARCHAR, "
        "last_date BIGINT, last_batch_id VARCHAR, updated_at TIMESTAMP, "
        "source_generation VARCHAR, cutover_id VARCHAR)")
    con.execute(
        "CREATE TABLE qfq_cycle_run (cycle_id VARCHAR, status VARCHAR, phase VARCHAR, "
        "started_at TIMESTAMP, finished_at TIMESTAMP)")
    con.execute("CREATE TABLE qfq_trigger_queue (trigger_id VARCHAR, status VARCHAR)")
    con.execute(
        "CREATE TABLE qfq_watermark_intent (cycle_id VARCHAR, table_name VARCHAR, "
        "freq VARCHAR, status VARCHAR)")
    con.close()


@pytest.fixture()
def helper(tmp_path):
    db = tmp_path / "t.db"
    _make_db(db)
    return DbHelper(db, tmp_path / "q.db", tmp_path / "b.db")


# ---------------- A1/A2/A3：数据止点契约 ----------------
def test_a1_data_bounds_equal_sql_max_time(helper):
    got = helper.get_data_bounds(["stock_daily", "etf_daily"])
    assert got["stock_daily"]["max_ts"] == TS_1016
    assert got["stock_daily"]["max_date"] == "2026-10-16"
    assert got["stock_daily"]["error"] == ""
    assert got["etf_daily"]["max_ts"] == TS_1016


def test_a1_empty_table_reports_dash_not_error(helper):
    got = helper.get_data_bounds(["etf_minutes"])
    assert got["etf_minutes"]["max_ts"] is None
    assert got["etf_minutes"]["max_date"] == ""
    assert got["etf_minutes"]["error"] == ""      # 空表 ≠ 错误（GUI 显示「—」）


def test_a2_table_without_time_column_is_not_applicable(helper):
    got = helper.get_data_bounds(["no_time_tbl"])
    assert got["no_time_tbl"]["error"] == "no_time_column"


def test_a3_invalid_identifier_never_reaches_sql(helper, monkeypatch):
    calls = []
    real = helper.query_duckdb
    monkeypatch.setattr(helper, "query_duckdb", lambda sql: calls.append(sql) or real(sql))
    got = helper.get_data_bounds(["x; DROP TABLE stock_daily", "1bad", "ok_name"])
    assert got["x; DROP TABLE stock_daily"]["error"] == "invalid_identifier"
    assert got["1bad"]["error"] == "invalid_identifier"
    assert all("DROP" not in s for s in calls), "非法表名不得进入 SQL"


# ---------------- A4/A5：缓存与降级 ----------------
def test_a4_ttl_cache_hits_avoid_second_query(helper, monkeypatch):
    helper.get_data_bounds(["stock_daily"])          # 预热（含元数据查询）
    calls = []
    real = helper.query_duckdb
    monkeypatch.setattr(helper, "query_duckdb", lambda sql: calls.append(sql) or real(sql))
    out = helper.get_data_bounds(["stock_daily"])
    assert out["stock_daily"]["max_date"] == "2026-10-16"
    assert calls == [], f"TTL 内不应再查库，实查 {calls}"


def test_a5_degraded_result_is_not_cached(helper, monkeypatch):
    real = helper.query_duckdb
    # 第一次模拟"超时降级"：返回空 DataFrame（DbHelper 超时路径的返回形态）
    monkeypatch.setattr(helper, "query_duckdb", lambda sql: pd.DataFrame())
    first = helper.get_data_bounds(["stock_daily"])
    assert first["stock_daily"]["max_date"] == ""
    # 恢复后应立即拿到真值（降级结果不得被 60s 缓存固化）
    monkeypatch.setattr(helper, "query_duckdb", real)
    second = helper.get_data_bounds(["stock_daily"])
    assert second["stock_daily"]["max_date"] == "2026-10-16", "降级态被缓存 → 假「无数据」"


# ---------------- A6：门控状态三态 ----------------
def _seed_cycle(db_path, status="started", phase="applying"):
    con = duckdb.connect(str(db_path))
    con.execute("INSERT INTO qfq_cycle_run VALUES ('cyc_t1', ?, ?, '2026-10-17 15:27:02', NULL)",
                [status, phase])
    con.close()


def _seed_queue(db_path, in_progress=0, dead=0):
    con = duckdb.connect(str(db_path))
    for i in range(in_progress):
        con.execute("INSERT INTO qfq_trigger_queue VALUES (?, 'in_progress')", [f"t{i}"])
    for i in range(dead):
        con.execute("INSERT INTO qfq_trigger_queue VALUES (?, 'dead_letter')", [f"d{i}"])
    con.close()


def _seed_intent(db_path, table="stock_daily", freq="daily", status="pending"):
    con = duckdb.connect(str(db_path))
    con.execute("INSERT INTO qfq_watermark_intent VALUES ('cyc_t1', ?, ?, ?)",
                [table, freq, status])
    con.close()


def test_a6_held_when_queue_residue_exists(helper, tmp_path):
    db = tmp_path / "t.db"
    _seed_cycle(db)
    _seed_queue(db, in_progress=3, dead=2)
    got = helper.get_watermark_gate_status()
    st = got["by_table"][("stock_daily", None)]
    assert st["state"] == "held"
    assert "in_progress=3" in st["reason"] and "dead_letter=2" in st["reason"]
    assert st["label"] == WATERMARK_STATE_LABELS["held"]


def test_a6_deferred_when_pending_intent_and_no_residue(helper, tmp_path):
    db = tmp_path / "t.db"
    _seed_cycle(db)
    _seed_intent(db)
    got = helper.get_watermark_gate_status()
    # 有候选水位待提交、且无队列残渣 → 延迟提交（非"门控保持"）
    assert got["by_table"][("stock_daily", "daily")]["state"] == "deferred"


def test_a6_normal_when_no_cycle_no_residue(helper):
    got = helper.get_watermark_gate_status()
    for (table, _freq), st in got["by_table"].items():
        if table in QFQ_COORDINATED_TABLES:
            assert st["state"] == "normal", (table, st)


def test_a6_non_coordinated_table_is_always_normal(helper, tmp_path):
    db = tmp_path / "t.db"
    _seed_cycle(db)
    _seed_queue(db, in_progress=5)
    got = helper.get_watermark_gate_status()
    assert ("no_time_tbl", None) not in got["by_table"], "非托管表不应被纳入门控判定"


# ---------------- A7：只读 ----------------
def test_a7_gate_status_is_read_only(helper, tmp_path):
    db = tmp_path / "t.db"
    _seed_cycle(db)
    _seed_queue(db, in_progress=2, dead=1)
    con = duckdb.connect(str(db), read_only=True)
    before = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("qfq_cycle_run", "qfq_trigger_queue", "qfq_watermark_intent",
                        "source_watermark")}
    con.close()
    helper.get_watermark_gate_status()
    helper.get_data_bounds(["stock_daily"])
    con = duckdb.connect(str(db), read_only=True)
    after = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in before}
    con.close()
    assert before == after, f"只读契约被破坏：{before} → {after}"


# ---------------- A8：单一真源 + GUI 列结构 ----------------
def test_a8_coordinated_tables_match_pipeline_source_of_truth():
    from quantstudio.pipeline.qfq_orchestrator_types import QFQOrchestratorConfig
    assert QFQ_COORDINATED_TABLES == set(QFQOrchestratorConfig.COORDINATED_PRICE_TABLES)


def test_a8_task_table_has_bounds_and_status_columns():
    setup_src = inspect.getsource(TaskTab._setup_ui)
    assert "setColumnCount(9)" in setup_src
    for name in ("水位线", "数据止点", "水位状态", "状态", "操作"):
        assert f'"{name}"' in setup_src, f"列头缺 {name}"
    render_src = inspect.getsource(TaskTab._render_tasks)
    assert "setItem(i, 5" in render_src and "setItem(i, 6" in render_src
    assert "setItem(i, 7" in render_src and "setCellWidget(i, 8" in render_src


# ---------------- 展示格式（降级不得伪装成"正常"）----------------
def test_format_gate_status_unknown_when_degraded():
    assert TaskTab._format_gate_status({}, "stock_daily", "daily")[0] == "未知"
    assert TaskTab._format_gate_status({"queue": {}, "by_table": {}}, "stock_daily", "daily")[0] == "未知"


def test_format_data_bound_variants():
    assert TaskTab._format_data_bound({}, "t")[0] == "—"
    assert TaskTab._format_data_bound({"t": {"error": "no_time_column"}}, "t")[0] == "—"
    text, tip = TaskTab._format_data_bound({"t": {"max_date": "2026-10-16"}}, "t")
    assert text == "2026-10-16" and "max(time)" in tip
