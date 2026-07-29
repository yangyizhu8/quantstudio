"""qfq_event_discovery.EventDiscovery 单元测试。

全部使用临时 DuckDB + 临时 SQLite（qfq_aux.db），不触碰正式库、不跑 git、不改任何
其他文件。覆盖：幂等去重、div_proc 过滤、scheduled/pending 分类、bootstrap 不灌
trigger、因子修订 → factor_revision / factor_new / etf_fund_adj trigger、ack 与崩溃重放。
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from quantstudio.pipeline.qfq_orchestrator_types import (
    QFQOrchestratorConfig,
    payload_hash_of,
    trigger_id_of,
)
from quantstudio.pipeline.qfq_observation import ObservationStore, alert_id_of
from quantstudio.pipeline.qfq_reanchor_schema import (
    init_duckdb_schema,
    init_sqlite_schema,
)
from quantstudio.pipeline.qfq_event_discovery import EventDiscovery, _norm_div_val

BJ_TZ = timezone(timedelta(hours=8))

# 测试用时间锚点
AS_OF = datetime(2024, 6, 1, tzinfo=BJ_TZ)
PAST_MS = int(datetime(2024, 1, 15, tzinfo=BJ_TZ).timestamp() * 1000)
PAST_REC_MS = int(datetime(2024, 1, 10, tzinfo=BJ_TZ).timestamp() * 1000)
FUTURE_MS = int(datetime(2024, 12, 15, tzinfo=BJ_TZ).timestamp() * 1000)
FUTURE_REC_MS = int(datetime(2024, 12, 10, tzinfo=BJ_TZ).timestamp() * 1000)
FT = int(datetime(2023, 6, 1, tzinfo=BJ_TZ).timestamp() * 1000)  # 因子时刻
FT2 = int(datetime(2023, 7, 1, tzinfo=BJ_TZ).timestamp() * 1000)

AUX_TABLES = """
CREATE TABLE IF NOT EXISTS adj_factor (code TEXT, time INTEGER, adj_factor REAL, PRIMARY KEY(code, time));
CREATE TABLE IF NOT EXISTS fund_adj (code TEXT, time INTEGER, adj_factor REAL, PRIMARY KEY(code, time));
"""

STOCK_DIVIDEND_DDL = """
CREATE TABLE stock_dividend (
    code VARCHAR, ex_date BIGINT, record_date BIGINT, ann_date BIGINT, end_date BIGINT,
    cash_div_before_tax DOUBLE, cash_div_after_tax DOUBLE, cash_div DOUBLE, stk_div DOUBLE,
    stk_bo_rate DOUBLE, stk_co_rate DOUBLE, div_rat DOUBLE, div_proc VARCHAR, update_time VARCHAR,
    PRIMARY KEY(code, ex_date))
"""


def _make_cfg() -> QFQOrchestratorConfig:
    return QFQOrchestratorConfig.from_dict({"enabled": True})


@pytest.fixture
def env(tmp_path):
    """搭建临时 DuckDB（含 qfq 表 + stock_dividend）与临时 qfq_aux.db。"""
    duck_path = str(tmp_path / "test.duckdb")
    aux_path = str(tmp_path / "qfq_aux.db")

    dconn = duckdb.connect(duck_path)
    init_duckdb_schema(dconn)
    dconn.execute(STOCK_DIVIDEND_DDL)

    aconn = sqlite3.connect(aux_path)
    init_sqlite_schema(aconn)
    aconn.executescript(AUX_TABLES)
    aconn.commit()
    aconn.close()

    yield dconn, aux_path

    dconn.close()


def _seed_dividend(dconn):
    dconn.execute(
        "INSERT INTO stock_dividend "
        "(code, ex_date, record_date, cash_div, stk_div, div_rat, div_proc) VALUES (?,?,?,?,?,?,?)",
        ("600000", PAST_MS, PAST_REC_MS, 0.5, 0.0, 0.5, "实施"))
    dconn.execute(
        "INSERT INTO stock_dividend "
        "(code, ex_date, record_date, cash_div, stk_div, div_rat, div_proc) VALUES (?,?,?,?,?,?,?)",
        ("600001", FUTURE_MS, FUTURE_REC_MS, 0.3, 0.0, 0.3, "实施"))
    dconn.execute(
        "INSERT INTO stock_dividend "
        "(code, ex_date, record_date, cash_div, stk_div, div_rat, div_proc) VALUES (?,?,?,?,?,?,?)",
        ("600002", PAST_MS, PAST_REC_MS, 0.2, 0.0, 0.2, "预案"))


# ---------------------------------------------------------------------------
# 1. payload_hash 稳定 + 同券同日去重
# ---------------------------------------------------------------------------
def test_dividend_dedup_and_payload_stable(env):
    dconn, aux_path = env
    _seed_dividend(dconn)
    disc = EventDiscovery(_make_cfg(), aux_db=aux_path)

    new1 = disc.scan_stock_dividend(dconn, as_of_ms=int(AS_OF.timestamp() * 1000),
                                    run_id="r1")
    # 两条 '实施'（600000 past / 600001 future），600002 '预案' 忽略
    assert len(new1) == 2
    total = dconn.execute("SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0]
    assert total == 2

    # payload_hash 确定性：与本地重算一致（任务4：13 字段完整业务 hash + _norm_div_val 规范化）
    # 种子仅提供 code/ex_date/record_date/cash_div/stk_div/div_rat/div_proc，
    # ann_date/end_date/before_tax/after_tax/stk_bo_rate/stk_co_rate 为 NULL → 规范化后 ""
    expected_div = [
        "600000", PAST_MS, PAST_REC_MS,
        None, None,  # ann_date / end_date 未提供
        _norm_div_val(None), _norm_div_val(None),  # cash_div_before_tax / after_tax 未提供
        _norm_div_val(0.5),   # cash_div
        _norm_div_val(0.0),   # stk_div
        _norm_div_val(None), _norm_div_val(None),  # stk_bo_rate / stk_co_rate 未提供
        _norm_div_val(0.5),   # div_rat
        _norm_div_val("实施"),  # div_proc
    ]
    ph = payload_hash_of(expected_div)
    stored = dconn.execute(
        "SELECT payload_hash FROM qfq_trigger_queue WHERE code='600000'"
    ).fetchone()[0]
    assert stored == ph
    # 同语义永远同 trigger_id
    tid = trigger_id_of("STOCK", "600000", PAST_MS, "stock_dividend", ph)
    assert dconn.execute(
        "SELECT trigger_id FROM qfq_trigger_queue WHERE code='600000'"
    ).fetchone()[0] == tid

    # 第二次扫描：INSERT OR IGNORE，不新增
    new2 = disc.scan_stock_dividend(dconn, as_of_ms=int(AS_OF.timestamp() * 1000),
                                    run_id="r2")
    assert new2 == []
    assert dconn.execute("SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# 2. div_proc != '实施' 被忽略
# ---------------------------------------------------------------------------
def test_dividend_ignores_non_implemented(env):
    dconn, aux_path = env
    # 只插 '预案'
    dconn.execute(
        "INSERT INTO stock_dividend "
        "(code, ex_date, record_date, cash_div, stk_div, div_rat, div_proc) VALUES (?,?,?,?,?,?,?)",
        ("600002", PAST_MS, PAST_REC_MS, 0.2, 0.0, 0.2, "预案"))
    disc = EventDiscovery(_make_cfg(), aux_db=aux_path)
    new = disc.scan_stock_dividend(dconn, as_of_ms=int(AS_OF.timestamp() * 1000),
                                   run_id="r1")
    assert new == []
    assert dconn.execute("SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 3. future → scheduled；past/today → pending
# ---------------------------------------------------------------------------
def test_dividend_status_classification(env):
    dconn, aux_path = env
    _seed_dividend(dconn)
    disc = EventDiscovery(_make_cfg(), aux_db=aux_path)
    disc.scan_stock_dividend(dconn, as_of_ms=int(AS_OF.timestamp() * 1000),
                             run_id="r1")
    statuses = dict(dconn.execute(
        "SELECT code, status FROM qfq_trigger_queue").fetchall())
    assert statuses["600000"] == "pending"   # past
    assert statuses["600001"] == "scheduled"  # future


# ---------------------------------------------------------------------------
# 4. bootstrap 不插入 trigger，但 cursor 更新
# ---------------------------------------------------------------------------
def test_bootstrap_no_trigger_but_cursor(env):
    dconn, aux_path = env
    _seed_dividend(dconn)
    disc = EventDiscovery(_make_cfg(), aux_db=aux_path)
    new = disc.scan_stock_dividend(dconn, as_of_ms=int(AS_OF.timestamp() * 1000),
                                   run_id="r1", bootstrap=True)
    assert new == []
    assert dconn.execute("SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0] == 0
    cur = dconn.execute(
        "SELECT detector_name, asset_type, cursor_as_of, status FROM qfq_observation_cursor "
        "WHERE detector_name='stock_dividend' AND asset_type='STOCK'"
    ).fetchone()
    assert cur is not None
    assert cur[2] == FUTURE_MS  # max ex_date
    assert cur[3] == "ok"


# ---------------------------------------------------------------------------
# 5. stock adj_factor 修改 → revision alert → factor_revision / factor_new
# ---------------------------------------------------------------------------
def test_stock_adj_factor_revision_and_factor_new(env):
    dconn, aux_path = env
    cfg = _make_cfg()
    disc = EventDiscovery(cfg, aux_db=aux_path)
    as_of = FT + 1000

    # 首次 observation（revision_no=1，无 alert）
    a = sqlite3.connect(aux_path)
    a.execute("INSERT INTO adj_factor VALUES ('600000', ?, 1.0)", [FT])
    a.commit(); a.close()
    res1 = disc.observe_stock_adj_factor(dconn, as_of_ms=as_of, run_id="r1")
    assert res1.new_count == 1
    assert res1.revised_count == 0

    # 修改因子值 → 修订（revision_no=2，产生 alert）
    a = sqlite3.connect(aux_path)
    a.execute("INSERT OR REPLACE INTO adj_factor VALUES ('600000', ?, 2.0)", [FT])
    a.commit(); a.close()
    res2 = disc.observe_stock_adj_factor(dconn, as_of_ms=as_of, run_id="r2")
    assert res2.revised_count == 1

    # 消费 alert → factor_revision trigger
    new = disc.consume_revision_alerts(dconn, run_id="r3", as_of_ms=as_of)
    assert len(new) == 1
    rec = new[0]
    assert rec.trigger_type == "factor_revision"
    assert rec.factor_old == 1.0
    assert rec.factor_new == 2.0
    assert rec.factor_revision == 2
    # alert 被 ack
    store = ObservationStore(aux_path)
    assert store.list_pending_alerts() == []

    # factor_new 分支（revision_no=1 的 alert，合成验证映射）
    a = sqlite3.connect(aux_path)
    a.execute("INSERT INTO adj_factor VALUES ('600001', ?, 1.0)", [FT2])
    a.execute(
        "INSERT INTO qfq_factor_observation "
        "(asset_type, code, factor_time, factor_value, revision_no, "
        " first_seen_run_id, last_seen_run_id, first_seen_at, last_seen_at) "
        "VALUES ('STOCK','600001',?,1.0,1,'b','b','2024-01-01','2024-01-01')", [FT2])
    aid = alert_id_of("STOCK", "600001", FT2, 1)
    a.execute(
        "INSERT INTO qfq_factor_revision_alert "
        "(alert_id, asset_type, code, factor_time, revision_no, status, "
        " first_seen_run_id, created_at, acknowledged_at) "
        "VALUES (?,?,?,?,?,?,?,?,NULL)",
        [aid, "STOCK", "600001", FT2, 1, "pending", "b", "2024-01-01"])
    a.commit(); a.close()
    new2 = disc.consume_revision_alerts(dconn, run_id="r4", as_of_ms=FT2 + 1000)
    assert len(new2) == 1
    assert new2[0].trigger_type == "factor_new"


# ---------------------------------------------------------------------------
# 6. ETF fund_adj → etf_fund_adj trigger
# ---------------------------------------------------------------------------
def test_etf_fund_adj_trigger(env):
    dconn, aux_path = env
    disc = EventDiscovery(_make_cfg(), aux_db=aux_path)
    as_of = FT + 1000

    a = sqlite3.connect(aux_path)
    a.execute("INSERT INTO fund_adj VALUES ('510300', ?, 1.0)", [FT])
    a.commit(); a.close()
    disc.observe_etf_fund_adj(dconn, as_of_ms=as_of, run_id="r1")

    a = sqlite3.connect(aux_path)
    a.execute("INSERT OR REPLACE INTO fund_adj VALUES ('510300', ?, 1.5)", [FT])
    a.commit(); a.close()
    disc.observe_etf_fund_adj(dconn, as_of_ms=as_of, run_id="r2")

    new = disc.consume_revision_alerts(dconn, run_id="r3", as_of_ms=as_of)
    assert len(new) == 1
    assert new[0].asset_type == "ETF"
    assert new[0].trigger_type == "etf_fund_adj"
    assert new[0].factor_new == 1.5
    assert ObservationStore(aux_path).list_pending_alerts() == []


# ---------------------------------------------------------------------------
# 7. 崩溃重放：trigger 已落库但 alert 未 ack → 再次 consume 不重复生成
# ---------------------------------------------------------------------------
def test_crash_replay_no_duplicate(env):
    dconn, aux_path = env
    disc = EventDiscovery(_make_cfg(), aux_db=aux_path)
    as_of = FT + 1000

    # 种子：revision_no=1 (值1.0) + revision_no=2 (值2.0) + pending alert(rev2)
    a = sqlite3.connect(aux_path)
    a.execute("INSERT INTO adj_factor VALUES ('600000', ?, 1.0)", [FT])
    a.execute(
        "INSERT INTO qfq_factor_observation "
        "(asset_type, code, factor_time, factor_value, revision_no, "
        " first_seen_run_id, last_seen_run_id, first_seen_at, last_seen_at) "
        "VALUES ('STOCK','600000',?,1.0,1,'b','b','2024-01-01','2024-01-01')", [FT])
    a.execute(
        "INSERT INTO qfq_factor_observation "
        "(asset_type, code, factor_time, factor_value, revision_no, "
        " first_seen_run_id, last_seen_run_id, first_seen_at, last_seen_at) "
        "VALUES ('STOCK','600000',?,2.0,2,'b','b','2024-01-02','2024-01-02')", [FT])
    aid = alert_id_of("STOCK", "600000", FT, 2)
    a.execute(
        "INSERT INTO qfq_factor_revision_alert "
        "(alert_id, asset_type, code, factor_time, revision_no, status, "
        " first_seen_run_id, created_at, acknowledged_at) "
        "VALUES (?,?,?,?,?,?,?,?,NULL)",
        [aid, "STOCK", "600000", FT, 2, "pending", "b", "2024-01-02"])
    a.commit(); a.close()

    # 模拟"已落 trigger 但崩溃未 ack"：先手动插入同语义 trigger
    ph = payload_hash_of([2.0])
    tid = trigger_id_of("STOCK", "600000", FT, "tushare_adj_factor", ph)
    dconn.execute(
        "INSERT OR IGNORE INTO qfq_trigger_queue "
        "(trigger_id, asset_type, code, trigger_type, detection_source, source_key, "
        " effective_date, payload_hash, factor_old, factor_new, factor_revision, "
        " status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [tid, "STOCK", "600000", "factor_revision", "tushare_adj_factor",
         str(FT), FT, ph, 1.0, 2.0, 2, "pending",
         "2024-01-02 00:00:00", "2024-01-02 00:00:00"])

    # 重放 consume：INSERT OR IGNORE 不重复，new_count==0，但 alert 被 ack
    new = disc.consume_revision_alerts(dconn, run_id="r_replay", as_of_ms=as_of)
    assert new == []
    assert dconn.execute("SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0] == 1
    assert ObservationStore(aux_path).list_pending_alerts() == []


# ---------------------------------------------------------------------------
# 8. establish_baseline 不灌历史 trigger
# ---------------------------------------------------------------------------
def test_establish_baseline_no_trigger_flood(env):
    dconn, aux_path = env
    _seed_dividend(dconn)
    a = sqlite3.connect(aux_path)
    a.execute("INSERT INTO adj_factor VALUES ('600000', ?, 1.0)", [FT])
    a.execute("INSERT INTO fund_adj VALUES ('510300', ?, 1.0)", [FT])
    a.commit(); a.close()

    disc = EventDiscovery(_make_cfg(), aux_db=aux_path)
    summary = disc.establish_baseline(dconn, as_of_ms=FT + 1000, run_id="base")
    assert summary["stock_adj_factor"] == 1
    assert summary["etf_fund_adj"] == 1
    assert summary["stock_dividend_cursor"] == FUTURE_MS
    # baseline 不往 trigger_queue 灌历史事件
    assert dconn.execute("SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0] == 0
