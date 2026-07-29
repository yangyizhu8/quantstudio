"""任务2.3：ETF/股票新 factor date 检测——factor_new trigger E2E（自包含 fixture）。

覆盖（全部通过 EventDiscovery 真实链路，不 mock 业务逻辑）：
- ETF 新 factor date（值变化超过 epsilon）→ 生成 factor_new trigger，
  detection_source=tushare_fund_adj_new，trigger_type=factor_new。
- factor 值不变（<= epsilon）→ 不触发任何 trigger。
- future factor date（factor_time > as_of_ms）→ status=scheduled；
  as_of 推进到 >= factor_time 后 → promote_scheduled_due 晋升 pending。
- factor revision（同一 factor_time 值变化）→ 走 qfq_factor_revision_alert outbox
  （与 factor_new 不同的路径，不产生 factor_new trigger）。
- bootstrap 建基线：首次见到的 (asset_type, code) 不触发 factor_new（防历史洪水）；
  第二次出现新 factor_time 且值变才触发。
"""

import sqlite3

import duckdb
import pytest

from quantstudio.pipeline.qfq_observation import FactorNewRow
from quantstudio.pipeline.qfq_orchestrator_types import QFQOrchestratorConfig
from quantstudio.pipeline.qfq_event_discovery import EventDiscovery
from quantstudio.pipeline.qfq_reanchor_schema import (
    init_duckdb_schema,
    init_sqlite_schema,
)
from quantstudio.pipeline.qfq_resident_orchestrator import QFQResidentOrchestrator


# factor_time（epoch-ms），仅比较大小，不依赖真实日期
T1 = 1_767_283_200_000  # 较早
T2 = 1_767_888_000_000  # 较晚（新 factor date）
T_FUTURE = 2_000_000_000_000  # 远未来


@pytest.fixture
def env(tmp_path):
    duck = tmp_path / "qfq_main.db"
    aux = tmp_path / "qfq_aux.db"
    dconn = duckdb.connect(str(duck))
    init_duckdb_schema(dconn)
    auxc = sqlite3.connect(str(aux), timeout=30)
    init_sqlite_schema(auxc)
    auxc.close()
    cfg = QFQOrchestratorConfig()
    disc = EventDiscovery(cfg, aux_db=str(aux))
    orch = QFQResidentOrchestrator(cfg, main_db=str(duck), aux_db=str(aux))
    yield dconn, str(aux), disc, orch
    dconn.close()


def _insert_fund_adj(aux_db, code, t, v):
    conn = sqlite3.connect(aux_db, timeout=30)
    conn.execute(
        "INSERT INTO fund_adj (code, time, adj_factor) VALUES (?,?,?)", [code, t, v]
    )
    conn.commit()
    conn.close()


def _insert_adj_factor(aux_db, code, t, v):
    conn = sqlite3.connect(aux_db, timeout=30)
    conn.execute(
        "INSERT INTO adj_factor (code, time, adj_factor) VALUES (?,?,?)", [code, t, v]
    )
    conn.commit()
    conn.close()


def _triggers(dconn):
    return dconn.execute(
        "SELECT trigger_id, asset_type, code, trigger_type, detection_source, "
        "status, effective_date, factor_old, factor_new FROM qfq_trigger_queue"
    ).fetchall()


def test_etf_new_factor_date_triggers_factor_new(env):
    dconn, aux, disc, orch = env
    # 批次1：建立基线（首次见到 510300，T1 值 1.0）
    _insert_fund_adj(aux, "510300", T1, 1.0)
    disc.observe_etf_fund_adj(dconn, as_of_ms=T1 + 1_000_000, run_id="r1")
    # 批次1 不应产生 factor_new（基线）
    assert [t for t in _triggers(dconn) if t[3] == "factor_new"] == []

    # 批次2：出现新 factor_time T2 且值变化 1.0 → 1.05
    _insert_fund_adj(aux, "510300", T2, 1.05)
    disc.observe_etf_fund_adj(dconn, as_of_ms=T2 + 1_000_000, run_id="r2")

    rows = [t for t in _triggers(dconn) if t[3] == "factor_new"]
    assert len(rows) == 1
    r = rows[0]
    assert r[1] == "ETF"
    assert r[2] == "510300"
    assert r[3] == "factor_new"
    assert r[4] == "tushare_fund_adj_new"
    assert r[6] == T2  # effective_date = factor_time
    assert r[7] == 1.0  # previous_value
    assert r[8] == 1.05  # current_value
    assert r[5] == "pending"  # factor_time <= as_of


def test_stock_new_factor_date_triggers_factor_new(env):
    dconn, aux, disc, orch = env
    _insert_adj_factor(aux, "600000", T1, 1.0)
    disc.observe_stock_adj_factor(dconn, as_of_ms=T1 + 1_000_000, run_id="r1")
    _insert_adj_factor(aux, "600000", T2, 1.10)
    disc.observe_stock_adj_factor(dconn, as_of_ms=T2 + 1_000_000, run_id="r2")

    rows = [t for t in _triggers(dconn) if t[3] == "factor_new"]
    assert len(rows) == 1
    r = rows[0]
    assert r[4] == "tushare_adj_factor_new"
    assert r[7] == 1.0 and r[8] == 1.10


def test_factor_value_unchanged_no_trigger(env):
    dconn, aux, disc, orch = env
    # 批次1：基线 T1=1.0
    _insert_fund_adj(aux, "510300", T1, 1.0)
    disc.observe_etf_fund_adj(dconn, as_of_ms=T1 + 1_000_000, run_id="r1")
    # 批次2：新 factor_time T2，但值仍为 1.0（<= epsilon 不变）
    _insert_fund_adj(aux, "510300", T2, 1.0)
    disc.observe_etf_fund_adj(dconn, as_of_ms=T2 + 1_000_000, run_id="r2")
    rows = [t for t in _triggers(dconn) if t[3] == "factor_new"]
    assert rows == []


def test_future_factor_date_scheduled_then_promoted(env):
    dconn, aux, disc, orch = env
    fn = FactorNewRow(
        asset_type="ETF", code="510300",
        factor_time=T_FUTURE, previous_value=1.0, current_value=1.05,
    )
    # as_of < factor_time → scheduled
    disc._emit_factor_new_triggers(dconn, [fn], run_id="rx", as_of_ms=T_FUTURE - 1_000)
    rows = [t for t in _triggers(dconn) if t[3] == "factor_new"]
    assert len(rows) == 1
    assert rows[0][5] == "scheduled"

    # as_of 推进到 >= factor_time → 晋升 pending
    orch.promote_scheduled_due(dconn, as_of_ms=T_FUTURE + 1_000)
    rows = [t for t in _triggers(dconn) if t[3] == "factor_new"]
    assert len(rows) == 1
    assert rows[0][5] == "pending"


def test_factor_revision_goes_to_revision_alert(env):
    dconn, aux, disc, orch = env
    # 批次1：基线 T1=1.0
    _insert_fund_adj(aux, "510300", T1, 1.0)
    disc.observe_etf_fund_adj(dconn, as_of_ms=T1 + 1_000_000, run_id="r1")

    # 批次2：同一 factor_time T1，值变化 1.0 → 1.2（revision，不是新 factor date）
    conn = sqlite3.connect(aux, timeout=30)
    conn.execute("DELETE FROM fund_adj WHERE code='510300' AND time=?", [T1])
    conn.execute("INSERT INTO fund_adj (code, time, adj_factor) VALUES (?,?,?)", ["510300", T1, 1.2])
    conn.commit()
    conn.close()
    disc.observe_etf_fund_adj(dconn, as_of_ms=T1 + 1_000_000, run_id="r2")

    # revision 不应产生 factor_new trigger
    assert [t for t in _triggers(dconn) if t[3] == "factor_new"] == []

    # revision 应写入 qfq_factor_revision_alert outbox
    auxc = sqlite3.connect(aux, timeout=30)
    alerts = auxc.execute(
        "SELECT alert_id, status FROM qfq_factor_revision_alert WHERE status='pending'"
    ).fetchall()
    auxc.close()
    assert len(alerts) == 1

    # 消费 revision alert → ETF 走 etf_fund_adj 路径（与 factor_new 路径不同）
    disc.consume_revision_alerts(dconn, run_id="rc", as_of_ms=T1 + 1_000_000)
    revs = [t for t in _triggers(dconn) if t[3] == "etf_fund_adj"]
    assert len(revs) == 1
    r = revs[0]
    assert r[4] == "tushare_fund_adj"  # ETF
    assert r[7] == 1.0 and r[8] == 1.2


def test_bootstrap_baseline_first_time_no_trigger(env):
    dconn, aux, disc, orch = env
    # 首次见到 (ETF, 510300)，即使有多个 factor_time 也只建基线，不触发 factor_new
    _insert_fund_adj(aux, "510300", T1, 1.0)
    _insert_fund_adj(aux, "510300", T2, 1.05)
    disc.observe_etf_fund_adj(dconn, as_of_ms=T2 + 1_000_000, run_id="r1")
    assert [t for t in _triggers(dconn) if t[3] == "factor_new"] == []

    # 第二次出现新 factor_time T_FUTURE 且值变 → 才触发 factor_new
    _insert_fund_adj(aux, "510300", T_FUTURE, 1.20)
    disc.observe_etf_fund_adj(dconn, as_of_ms=T_FUTURE + 1_000_000, run_id="r2")
    rows = [t for t in _triggers(dconn) if t[3] == "factor_new"]
    assert len(rows) == 1
    assert rows[0][6] == T_FUTURE
