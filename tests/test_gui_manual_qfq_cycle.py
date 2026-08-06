from types import SimpleNamespace
import sqlite3

import duckdb
import pytest

from quantstudio.pipeline.daemon import ResidentCollector


def _collector(table="etf_daily"):
    c = ResidentCollector.__new__(ResidentCollector)
    c._qfq_cycle_id = None
    c._last_qfq_cycle_summary = None
    c._qfq_config = lambda: SimpleNamespace(can_coordinate_watermark=lambda t: t in {
        "stock_daily", "stock_minutes", "etf_daily", "etf_minutes"})
    c.qfq_enabled = lambda: True
    c._run_full_quality_audit = lambda: True
    c._execute_task = lambda task: True
    c.events = []
    def begin():
        c.events.append("begin")
        c._qfq_cycle_id = "manual-cycle"
        return c._qfq_cycle_id
    def post(run_id):
        c.events.append(("post", run_id))
        c._qfq_cycle_id = None
        return SimpleNamespace(status="finalized", watermarks_committed=1, watermarks_held=0, error=None)
    c.qfq_begin_cycle = begin
    c.qfq_run_post_ingest = post
    return c


def test_direct_full_pull_owns_qfq_cycle_and_commits_watermark_gate():
    c = _collector()
    assert c.execute_task({"name": "mcp_etf_daily", "table": "etf_daily"},
                          mode="full_range", run_quality_audit=False) is True
    assert c.events[0] == "begin"
    assert c.events[1][0] == "post"
    assert c._last_qfq_cycle_summary.status == "finalized"


def test_direct_non_price_task_does_not_open_qfq_cycle():
    c = _collector()
    assert c.execute_task({"name": "stock_basic", "table": "stock_basic"},
                          mode="full_range", run_quality_audit=False) is True
    assert c.events == []


def test_existing_resident_cycle_is_not_nested():
    c = _collector()
    c._qfq_cycle_id = "resident-cycle"
    assert c.execute_task({"name": "mcp_etf_daily", "table": "etf_daily"},
                          mode="incremental", run_quality_audit=False) is True
    assert c.events == []


class _DbWriter:
    def __init__(self, conn):
        self.conn = conn
        self.db_path = ":memory:"

    def shared_conn(self):
        return self.conn

    def advance_watermark(self, source, table, freq, last_date, batch_id):
        self.conn.execute(
            "INSERT INTO source_watermark "
            "(source,table_name,freq,last_date,last_batch_id,updated_at,"
            "source_generation,cutover_id) VALUES (?,?,?,?,?,NOW(),?,?) "
            "ON CONFLICT (source,table_name,freq) DO UPDATE SET "
            "last_date=excluded.last_date,last_batch_id=excluded.last_batch_id,"
            "updated_at=excluded.updated_at,"
            "source_generation=excluded.source_generation,cutover_id=excluded.cutover_id",
            [source, table, freq, last_date, batch_id,
             "xtquant-legacy", "legacy-xtquant-pre-cutover"])


class _Calendar:
    def is_trading_day(self, ms):
        return True

    def prev_trading_day(self, ms):
        return ms - 86_400_000


def _real_manual_collector(tmp_path, table, freq, candidate, *, hold=False):
    from quantstudio.pipeline.qfq_fresh_capture import FakeFreshFetcher
    from quantstudio.pipeline.qfq_orchestrator_types import QFQOrchestratorConfig
    from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema
    from quantstudio.pipeline.qfq_resident_orchestrator import QFQResidentOrchestrator

    conn = duckdb.connect(":memory:")
    init_duckdb_schema(conn)
    for price_table in ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes"):
        conn.execute(f'CREATE TABLE "{price_table}" (code VARCHAR, time BIGINT)')
    conn.execute(
        "CREATE TABLE stock_dividend ("
        "code VARCHAR, ex_date BIGINT, record_date BIGINT, ann_date BIGINT,"
        "end_date BIGINT, cash_div_before_tax DOUBLE, cash_div_after_tax DOUBLE,"
        "cash_div DOUBLE, stk_div DOUBLE, stk_bo_rate DOUBLE, stk_co_rate DOUBLE,"
        "div_rat DOUBLE, div_proc VARCHAR, update_time VARCHAR,"
        "PRIMARY KEY(code, ex_date))")
    aux = tmp_path / f"{table}_{freq}.db"
    aux_conn = sqlite3.connect(aux)
    aux_conn.execute("CREATE TABLE adj_factor (code TEXT,time INTEGER,adj_factor REAL)")
    aux_conn.execute("CREATE TABLE fund_adj (code TEXT,time INTEGER,adj_factor REAL)")
    aux_conn.commit()
    aux_conn.close()

    writer = _DbWriter(conn)
    c = ResidentCollector.__new__(ResidentCollector)
    c.tasks_cfg = {"qfq_orchestrator": {
        "enabled": True, "require_bootstrap": False,
        "factor_refresh_enabled": False, "price_source": "xtquant"}}
    c.writer = writer
    c._qfq_cfg_obj = None
    c._qfq_cycle_id = None
    c._last_qfq_cycle_summary = None
    c._last_task_actual_source = None
    c._last_task_watermark_candidate_created = False
    c._last_task_qfq_managed = False
    cfg = QFQOrchestratorConfig.from_dict(c.tasks_cfg["qfq_orchestrator"])
    c._qfq_orch = QFQResidentOrchestrator(
        cfg, aux_db=str(aux), fetcher=FakeFreshFetcher({}), calendar=_Calendar(),
        watermark_advancer=writer.advance_watermark)
    if hold:
        c._qfq_orch._qfq_gate = lambda *args, **kwargs: (
            False, {"passed": False, "reasons": ["test hold"]})
    c._run_full_quality_audit = lambda: True

    def execute(run_task):
        c._advance_or_defer_watermark(
            "xtquant", run_task["table"], run_task.get("freq", "daily"),
            candidate, "manual_batch")
        return True

    c._execute_task = execute
    return c, conn


@pytest.mark.parametrize("table,freq,mode", [
    ("stock_daily", "daily", "full_range"),
    ("stock_daily", "daily", "incremental"),
    ("stock_minutes", "1min", "full_range"),
    ("stock_minutes", "1min", "incremental"),
    ("etf_daily", "daily", "full_range"),
    ("etf_daily", "daily", "incremental"),
    ("etf_minutes", "1min", "full_range"),
    ("etf_minutes", "1min", "incremental"),
])
def test_real_manual_cycle_commits_source_watermark_for_all_price_paths(
        tmp_path, table, freq, mode):
    candidate = 1785945600000
    c, conn = _real_manual_collector(tmp_path, table, freq, candidate)

    assert c.execute_task({"name": f"manual_{table}", "table": table, "freq": freq},
                          mode=mode, run_quality_audit=False) is True

    row = conn.execute(
        "SELECT last_date,source_generation,cutover_id FROM source_watermark "
        "WHERE source='xtquant' AND table_name=? AND freq=?", [table, freq]).fetchone()
    assert row == (candidate, "xtquant-legacy", "legacy-xtquant-pre-cutover")
    assert c._last_qfq_cycle_summary.status == "finalized"
    assert c._last_qfq_cycle_summary.watermarks_committed == 1
    assert c._last_task_watermark_candidate_created is True
    conn.close()


def test_real_manual_cycle_hold_preserves_old_watermark_and_reports_hold(tmp_path):
    table, freq = "etf_daily", "daily"
    old, candidate = 1785859200000, 1785945600000
    c, conn = _real_manual_collector(tmp_path, table, freq, candidate, hold=True)
    c.writer.advance_watermark("xtquant", table, freq, old, "old_batch")

    assert c.execute_task({"name": "manual_etf", "table": table, "freq": freq},
                          mode="full_range", run_quality_audit=False) is True

    row = conn.execute(
        "SELECT last_date FROM source_watermark "
        "WHERE source='xtquant' AND table_name=? AND freq=?", [table, freq]).fetchone()
    assert row == (old,)
    assert c._last_qfq_cycle_summary.status == "finalized_held"
    assert c._last_qfq_cycle_summary.watermarks_held == 1
    conn.close()
