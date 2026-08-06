from types import SimpleNamespace

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
