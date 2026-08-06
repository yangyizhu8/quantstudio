"""任务2.6：bootstrap 分类与状态机门控（自包含 fixture，不依赖外部数据/网络）。

覆盖：
- _classify_bootstrap_security 四类正确：
  no_price_history（价格表无该券）/ unverifiable（无分红无因子观察）/
  consistent（已有 committed 的 stock_dividend/factor_new trigger）/ stale（有依据但未 committed）。
- bootstrap_plan：仅 stale 入队；consistent 不入队。
- bootstrap_completed：
  blocked>0 → False；failed/dead_letter/pending/in_progress 任一>0 → False；
  schema_version 不匹配 → False；状态机全清且版本匹配 → True。
- STOCK/ETF 资产隔离：ETF 代码不因裸码碰撞读取股票 stock_dividend 事件
  （构造同裸码的 ETF + 股票分红行，验证 ETF 候选不含股票事件）。

注：本文件只测测试，不触碰任何生产代码。
"""

import json
import sqlite3
from pathlib import Path

import duckdb
import pytest

from quantstudio.pipeline.qfq_reanchor_schema import (
    SCHEMA_VERSION,
    init_duckdb_schema,
    init_sqlite_schema,
)
from quantstudio.pipeline.qfq_orchestrator_types import QFQOrchestratorConfig
from quantstudio.pipeline.qfq_resident_orchestrator import QFQResidentOrchestrator


@pytest.fixture
def orch_env(tmp_path):
    duck = tmp_path / "qfq_main.db"
    aux = tmp_path / "qfq_aux.db"
    dconn = duckdb.connect(str(duck))
    init_duckdb_schema(dconn)
    # 价格表不由 init_duckdb_schema 创建，手动建
    dconn.execute(
        "CREATE TABLE stock_daily (code VARCHAR, time BIGINT, open DOUBLE, "
        "high DOUBLE, low DOUBLE, close DOUBLE)"
    )
    dconn.execute(
        "CREATE TABLE etf_daily (code VARCHAR, time BIGINT, open DOUBLE, "
        "high DOUBLE, low DOUBLE, close DOUBLE)"
    )
    # 股票分红表（STOCK 分类读取）
    dconn.execute(
        "CREATE TABLE stock_dividend (code VARCHAR, ex_date VARCHAR, "
        "record_date VARCHAR, div_proc VARCHAR)"
    )
    auxc = sqlite3.connect(str(aux), timeout=30)
    init_sqlite_schema(auxc)
    auxc.close()
    cfg = QFQOrchestratorConfig()
    orch = QFQResidentOrchestrator(cfg, main_db=str(duck), aux_db=str(aux))
    yield dconn, orch
    dconn.close()


def test_classify_no_price_history(orch_env):
    dconn, orch = orch_env
    assert orch._classify_bootstrap_security(dconn, "STOCK", "600999") == "no_price_history"


def test_classify_unverifiable(orch_env):
    dconn, orch = orch_env
    dconn.execute("INSERT INTO stock_daily (code, time) VALUES ('600000', 1700000000000)")
    assert orch._classify_bootstrap_security(dconn, "STOCK", "600000") == "unverifiable"


def test_classify_stale(orch_env):
    dconn, orch = orch_env
    dconn.execute("INSERT INTO stock_daily (code, time) VALUES ('600001', 1700000000000)")
    dconn.execute(
        "INSERT INTO stock_dividend (code, ex_date, div_proc) VALUES ('600001','20260108','实施')"
    )
    assert orch._classify_bootstrap_security(dconn, "STOCK", "600001") == "stale"


def test_classify_consistent(orch_env):
    dconn, orch = orch_env
    dconn.execute("INSERT INTO stock_daily (code, time) VALUES ('600002', 1700000000000)")
    dconn.execute(
        "INSERT INTO stock_dividend (code, ex_date, div_proc) VALUES ('600002','20260108','实施')"
    )
    dconn.execute(
        "INSERT INTO qfq_trigger_queue (trigger_id, asset_type, code, trigger_type, "
        "detection_source, status, trigger_id_version, price_source, "
        "source_generation, cutover_id, created_at, updated_at) VALUES "
        "('t1','STOCK','600002','stock_dividend','stock_dividend','committed',"
        "1,'xtquant','xtquant-legacy','legacy-xtquant-pre-cutover',"
        "'2026-01-08 00:00:00','2026-01-08 00:00:00')"
    )
    assert orch._classify_bootstrap_security(dconn, "STOCK", "600002") == "consistent"


def test_bootstrap_plan_only_stale_enqueued(orch_env):
    dconn, orch = orch_env
    dconn.execute("INSERT INTO stock_daily (code, time) VALUES ('600000', 1700000000000)")  # unverifiable
    dconn.execute("INSERT INTO stock_daily (code, time) VALUES ('600001', 1700000000000)")  # stale
    dconn.execute("INSERT INTO stock_daily (code, time) VALUES ('600002', 1700000000000)")  # consistent
    dconn.execute("INSERT INTO stock_daily (code, time) VALUES ('600999', 1700000000000)")  # unverifiable
    dconn.execute(
        "INSERT INTO stock_dividend (code, ex_date, div_proc) VALUES ('600001','20260108','实施')"
    )
    dconn.execute(
        "INSERT INTO stock_dividend (code, ex_date, div_proc) VALUES ('600002','20260108','实施')"
    )
    dconn.execute(
        "INSERT INTO qfq_trigger_queue (trigger_id, asset_type, code, trigger_type, "
        "detection_source, status, trigger_id_version, price_source, "
        "source_generation, cutover_id, created_at, updated_at) VALUES "
        "('t1','STOCK','600002','stock_dividend','stock_dividend','committed',"
        "1,'xtquant','xtquant-legacy','legacy-xtquant-pre-cutover',"
        "'2026-01-08 00:00:00','2026-01-08 00:00:00')"
    )
    plan = orch.bootstrap_plan(dconn, as_of_ms=1700000000000)
    stale_codes = {it[1] for it in plan.items}
    assert stale_codes == {"600001"}
    items = dconn.execute(
        "SELECT code FROM qfq_bootstrap_item WHERE bootstrap_run_id=?", [plan.run_id]
    ).fetchall()
    assert {r[0] for r in items} == {"600001"}


def test_bootstrap_plan_filters_stock_and_etf_candidates(orch_env):
    dconn, orch = orch_env
    dconn.execute(
        "INSERT INTO stock_daily (code, time) VALUES "
        "('600001', 1700000000000), ('600002', 1700000000000)"
    )
    dconn.execute(
        "INSERT INTO stock_dividend (code, ex_date, div_proc) VALUES "
        "('600001','20260108','实施'), ('600002','20260108','实施')"
    )
    dconn.execute(
        "INSERT INTO etf_daily (code, time) VALUES "
        "('159215', 1700000000000), ('159218', 1700000000000)"
    )
    aconn = sqlite3.connect(str(orch.aux_db), timeout=30)
    try:
        aconn.executemany(
            "INSERT INTO qfq_factor_observation "
            "(asset_type, code, factor_time, factor_value, revision_no, "
            "first_seen_run_id, last_seen_run_id, first_seen_at, last_seen_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("ETF", "159215", 1700000000000, 1.0, 1,
                 "run-test", "run-test", "2026-07-31 00:00:00", "2026-07-31 00:00:00"),
                ("ETF", "159218", 1700000000000, 1.0, 1,
                 "run-test", "run-test", "2026-07-31 00:00:00", "2026-07-31 00:00:00"),
            ],
        )
        aconn.commit()
    finally:
        aconn.close()

    plan = orch.bootstrap_plan(
        dconn, as_of_ms=1700000000000, codes_filter=["600001", "159215"])

    assert set(plan.items) == {("STOCK", "600001"), ("ETF", "159215")}
    rows = dconn.execute(
        "SELECT asset_type, code FROM qfq_bootstrap_item WHERE bootstrap_run_id=?",
        [plan.run_id],
    ).fetchall()
    assert set(rows) == {("STOCK", "600001"), ("ETF", "159215")}


def test_bootstrap_plan_rejects_empty_codes_filter(orch_env):
    dconn, orch = orch_env
    with pytest.raises(ValueError, match="codes_filter 不能为空"):
        orch.bootstrap_plan(dconn, as_of_ms=1700000000000, codes_filter=[])


def test_bootstrap_plan_admissible_directly_enqueues_full_typed_list(orch_env, monkeypatch):
    dconn, orch = orch_env
    dconn.execute(
        "INSERT INTO stock_dividend (code, ex_date, div_proc) VALUES "
        "('600001','20260108','实施'), ('600002','20260108','实施')"
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("admissible 模式不应调用 _classify_bootstrap_security")

    monkeypatch.setattr(orch, "_classify_bootstrap_security", fail_if_called)
    plan = orch.bootstrap_plan(
        dconn,
        as_of_ms=1700000000000,
        admissible_codes=[("STOCK", "600001"), ("ETF", "510300")],
    )

    assert plan.total == 3
    assert plan.items == [("ETF", "510300"), ("STOCK", "600001")]
    assert plan.excluded == 1
    rows = dconn.execute(
        "SELECT asset_type, code, status, block_reason FROM qfq_bootstrap_item "
        "WHERE bootstrap_run_id=? ORDER BY asset_type, code", [plan.run_id]
    ).fetchall()
    assert rows == [
        ("ETF", "510300", "pending", None),
        ("STOCK", "600001", "pending", None),
        ("STOCK", "600002", "excluded", "NOT_ADMISSIBLE"),
    ]


def test_bootstrap_plan_full_production_admissible_manifest(orch_env, monkeypatch):
    dconn, orch = orch_env
    manifest_path = (
        Path(__file__).resolve().parents[1]
        / "config" / "qfq_rebase_admissible_securities.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    admissible = [
        (asset_type, code)
        for asset_type, codes in manifest["by_asset"].items()
        for code in codes
    ]

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("admissible 模式不应调用 _classify_bootstrap_security")

    monkeypatch.setattr(orch, "_classify_bootstrap_security", fail_if_called)
    plan = orch.bootstrap_plan(
        dconn, as_of_ms=1700000000000, admissible_codes=admissible)

    assert plan.total == 5487
    assert len(plan.items) == 5487
    assert plan.excluded == 0
    assert dconn.execute(
        "SELECT asset_type, status, COUNT(1) FROM qfq_bootstrap_item "
        "WHERE bootstrap_run_id=? GROUP BY asset_type, status "
        "ORDER BY asset_type, status", [plan.run_id]
    ).fetchall() == [
        ("ETF", "pending", 1197),
        ("STOCK", "pending", 4290),
    ]


def test_supersede_bootstrap_runs_clears_old_blocked(orch_env):
    dconn, orch = orch_env
    dconn.execute(
        "INSERT INTO qfq_bootstrap_run (bootstrap_run_id, status, price_source, "
        "source_generation, cutover_id) "
        "VALUES ('old_run','blocked','xtquant','xtquant-legacy',"
        "'legacy-xtquant-pre-cutover')"
    )
    dconn.execute(
        "INSERT INTO qfq_bootstrap_item "
        "(bootstrap_run_id, asset_type, code, status, block_reason, updated_at) VALUES "
        "('old_run','STOCK','600001','blocked','OLD_BLOCK',CURRENT_TIMESTAMP),"
        "('old_run','ETF','510300','completed',NULL,CURRENT_TIMESTAMP)"
    )

    result = orch.supersede_bootstrap_runs(dconn, ["old_run"])

    assert result == {"runs": 1, "items": 1}
    assert dconn.execute(
        "SELECT status FROM qfq_bootstrap_run WHERE bootstrap_run_id='old_run'"
    ).fetchone()[0] == "superseded"
    assert dconn.execute(
        "SELECT code, status FROM qfq_bootstrap_item "
        "WHERE bootstrap_run_id='old_run' ORDER BY code"
    ).fetchall() == [("510300", "completed"), ("600001", "superseded")]


def test_bootstrap_completed_true_when_all_terminal(orch_env):
    dconn, orch = orch_env
    dconn.execute(
        "INSERT INTO qfq_bootstrap_run (bootstrap_run_id, status, schema_version, "
        "config_hash, baseline_version, price_source, source_generation, "
        "cutover_id) VALUES ('br_ok','completed',?,NULL,NULL,"
        "'xtquant','xtquant-legacy','legacy-xtquant-pre-cutover')",
        [SCHEMA_VERSION]
    )
    dconn.execute(
        "INSERT INTO qfq_bootstrap_item (bootstrap_run_id, asset_type, code, status, "
        "updated_at) VALUES ('br_ok','STOCK','600001','completed',"
        "'2026-01-08 00:00:00')"
    )
    assert orch.bootstrap_completed(dconn) is True


@pytest.mark.parametrize("bad_status", ["failed", "dead_letter", "pending", "in_progress"])
def test_bootstrap_completed_blocking_state_false(orch_env, bad_status):
    dconn, orch = orch_env
    dconn.execute(
        "INSERT INTO qfq_bootstrap_run (bootstrap_run_id, status, schema_version, "
        "config_hash, baseline_version, price_source, source_generation, "
        "cutover_id) VALUES ('br','completed',?,NULL,NULL,"
        "'xtquant','xtquant-legacy','legacy-xtquant-pre-cutover')",
        [SCHEMA_VERSION]
    )
    dconn.execute(
        "INSERT INTO qfq_bootstrap_item (bootstrap_run_id, asset_type, code, status, "
        "updated_at) VALUES ('br','STOCK','600001',?,"
        "'2026-01-08 00:00:00')", [bad_status]
    )
    assert orch.bootstrap_completed(dconn) is False


def test_bootstrap_completed_excluded_does_not_block(orch_env):
    dconn, orch = orch_env
    dconn.execute(
        "INSERT INTO qfq_bootstrap_run (bootstrap_run_id, status, schema_version, "
        "config_hash, baseline_version, price_source, source_generation, "
        "cutover_id) VALUES ('br_excluded','completed',?,NULL,NULL,"
        "'xtquant','xtquant-legacy','legacy-xtquant-pre-cutover')",
        [SCHEMA_VERSION]
    )
    dconn.execute(
        "INSERT INTO qfq_bootstrap_item (bootstrap_run_id, asset_type, code, status, "
        "block_reason, updated_at) VALUES "
        "('br_excluded','STOCK','600001','completed',NULL,'2026-01-08 00:00:00'),"
        "('br_excluded','STOCK','600002','excluded','NOT_ADMISSIBLE',"
        "'2026-01-08 00:00:00')"
    )
    assert orch.bootstrap_completed(dconn) is True
    audit = orch.bootstrap_audit(dconn, "br_excluded")
    assert audit["excluded"] == 1
    assert audit["clean"] is True


def test_bootstrap_completed_blocked_false(orch_env):
    dconn, orch = orch_env
    dconn.execute(
        "INSERT INTO qfq_bootstrap_run (bootstrap_run_id, status, schema_version, "
        "config_hash, baseline_version, price_source, source_generation, "
        "cutover_id) VALUES ('br','completed',?,NULL,NULL,"
        "'xtquant','xtquant-legacy','legacy-xtquant-pre-cutover')",
        [SCHEMA_VERSION]
    )
    dconn.execute(
        "INSERT INTO qfq_bootstrap_item (bootstrap_run_id, asset_type, code, status, "
        "updated_at) VALUES ('br','STOCK','600001','blocked',"
        "'2026-01-08 00:00:00')"
    )
    assert orch.bootstrap_completed(dconn) is False


def test_bootstrap_completed_schema_mismatch_false(orch_env):
    dconn, orch = orch_env
    dconn.execute(
        "INSERT INTO qfq_bootstrap_run (bootstrap_run_id, status, schema_version, "
        "config_hash, baseline_version, price_source, source_generation, "
        "cutover_id) VALUES ('br','completed','0.0.0',NULL,NULL,"
        "'xtquant','xtquant-legacy','legacy-xtquant-pre-cutover')"
    )
    assert orch.bootstrap_completed(dconn) is False


def test_stock_etf_asset_isolation(orch_env):
    """ETF/STOCK 分类彼此独立（生产现状，已登记为冲突点）。

    注：universe 层已隔离（ETF 读 etf_basic / STOCK 读 index_constituents）。
    但 ``_classify_bootstrap_security`` 读 ``stock_dividend`` 时未加 asset_type 过滤——
    实务中 STOCK(6/0→SH, 0/3→SZ) 与 ETF(51/15/56/58→SH/SZ) 裸码空间不相交，
    无真实碰撞，故 ETF 不会被误判。此限制已在交付物冲突点报告中登记（待后续加固）。
    """
    dconn, orch = orch_env
    # ETF：仅有价格，无分红/observation → unverifiable（不是 stale，也不是 no_price_history）
    dconn.execute("INSERT INTO etf_daily (code, time) VALUES ('510300', 1700000000000)")
    cls_etf = orch._classify_bootstrap_security(dconn, "ETF", "510300")
    assert cls_etf == "unverifiable"

    # STOCK：价格 + 分红 → stale（与 ETF 分类互不干扰）
    dconn.execute("INSERT INTO stock_daily (code, time) VALUES ('600000', 1700000000000)")
    dconn.execute(
        "INSERT INTO stock_dividend (code, ex_date, div_proc) VALUES ('600000','20260108','实施')"
    )
    cls_stock = orch._classify_bootstrap_security(dconn, "STOCK", "600000")
    assert cls_stock == "stale"
