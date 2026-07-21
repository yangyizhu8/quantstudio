from types import SimpleNamespace

import pandas as pd

from quantstudio.pipeline.daemon import ResidentCollector


class _Writer:
    def __init__(self, last=None):
        self.last = last
        self.advanced = []
        self.db_path = None

    def get_last_date(self, source, table, freq):
        return self.last

    def advance_watermark(self, source, table, freq, last_date, batch_id):
        self.advanced.append((source, table, freq, int(last_date), batch_id))


class _Aligner:
    schemas = {"etf_daily": {"time_key": "time"}}


class _Audit:
    def record(self, *args, **kwargs):
        pass


def _collector(tasks_cfg=None):
    collector = object.__new__(ResidentCollector)
    collector.tasks_cfg = tasks_cfg or {"quality_gate": {"max_failure_rate": 0.0001}}
    collector.aligner = _Aligner()
    collector.batch_audit = _Audit()
    return collector


def test_bump_date_converts_ms_watermark_to_adapter_date():
    watermark = int(pd.Timestamp("2026-07-17", tz="Asia/Shanghai").timestamp() * 1000)
    assert ResidentCollector._bump_date(str(watermark)) == "2026-07-18"


def test_bump_date_keeps_legacy_date_watermark_compatible():
    assert ResidentCollector._bump_date("2026-07-17") == "2026-07-18"
    assert ResidentCollector._bump_date("20260717") == "2026-07-18"


def test_failure_gate_accepts_at_most_point_zero_one_percent():
    collector = _collector()
    assert collector._failure_gate({}, failed=1, attempted=10_000)[0] is True
    assert collector._failure_gate({}, failed=2, attempted=10_000)[0] is False


def test_failure_gate_allows_per_task_override():
    collector = _collector()
    accepted, rate, threshold = collector._failure_gate(
        {"max_failure_rate": 0.001}, failed=1, attempted=1_000)
    assert accepted is True
    assert rate == threshold == 0.001


def test_max_date_uses_schema_time_key():
    collector = _collector()
    df = pd.DataFrame({"time": [1000, 3000, 2000]})
    assert collector._max_date(df, "etf_daily") == "3000"


def test_execute_task_with_quality_requires_both_task_and_audit_pass():
    collector = _collector()
    collector._execute_task = lambda task: True
    collector._run_full_quality_audit = lambda: False
    assert collector.execute_task_with_quality({"name": "x"}) is False

    collector._run_full_quality_audit = lambda: True
    assert collector.execute_task_with_quality({"name": "x"}) is True
