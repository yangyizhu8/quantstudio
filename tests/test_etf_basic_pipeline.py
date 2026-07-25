from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from quantstudio.pipeline.aligner import FieldAligner
from quantstudio.pipeline.daemon import ResidentCollector
from quantstudio.pipeline.etf_basic_standardizer import BASELINE_COLUMNS, build_payload
from quantstudio.pipeline.sources.tushare_adapter import TushareAdapter
from quantstudio.pipeline.validator import PreIngestValidator
from quantstudio.pipeline.writers import DuckDBWriter

ROOT = Path(__file__).resolve().parent.parent


def _ms(date: str) -> int:
    return int(pd.Timestamp(date, tz="Asia/Shanghai").timestamp() * 1000)


def _raw(name="Test ETF", *, status="L", list_date="20200102"):
    return pd.DataFrame([{
        "ts_code": "510999.SH", "name": name, "fund_type": "\u80a1\u7968\u578b",
        "list_date": list_date, "delist_date": None, "benchmark": "Test Index",
        "status": status, "invest_type": "\u88ab\u52a8\u6307\u6570\u578b", "type": "\u80a1\u7968\u578b",
        "issue_amount": 12.3, "p_value": 1.0,
    }])


def test_tushare_etf_basic_adapter_fetches_only_baseline_source_fields():
    adapter = object.__new__(TushareAdapter)
    captured = {}
    def retry(fn, api_name, **kwargs):
        captured.update({"api_name": api_name, **kwargs})
        return _raw()
    adapter._retry_with_backoff = retry
    adapter._call_api = lambda *args, **kwargs: None

    raw, meta = adapter._fetch_etf_basic()

    assert captured["api_name"] == "fund_basic"
    assert captured["market"] == "E"
    assert "issue_amount" not in captured["fields"]
    assert meta["granularity"] == "snapshot"
    assert meta["units"]["list_date"] == "calendar_date_YYYYMMDD"
    assert len(raw) == 1


def test_standardizer_matches_duckdb_field_and_unit_contract():
    bounds = pd.DataFrame([{
        "code": "510999", "first_bar_ms": _ms("2020-01-02"),
        "last_bar_ms": _ms("2021-01-04"),
    }])
    payload = build_payload(
        _raw(status="D", list_date=None), bounds, update_time="2026-07-25T12:00:00+08:00")

    assert list(payload.columns) == BASELINE_COLUMNS
    row = payload.iloc[0]
    assert row["code"] == "510999"
    assert row["ts_code"] == "510999.SH"
    assert row["exchange"] == "SS"
    assert row["list_date"] == _ms("2020-01-02")
    assert row["delist_date"] == _ms("2021-01-05")
    assert row["etf_type"] == "equity"
    assert bool(row["is_cross_border"]) is False
    assert row["data_source"] == "tushare_fund_basic"
    assert "issue_amount" not in payload.columns
    assert "p_value" not in payload.columns


def test_full_alignment_validation_and_writer_use_same_etf_basic_contract(tmp_path):
    aligner = FieldAligner.from_config(ROOT / "config" / "alignment_rules.json")
    validator = PreIngestValidator.from_config(ROOT / "config" / "alignment_rules.json")
    bounds = pd.DataFrame([{
        "code": "510999", "first_bar_ms": _ms("2020-01-02"),
        "last_bar_ms": _ms("2021-01-04"),
    }])
    std, meta = aligner.align(
        _raw(status="D", list_date=None), "etf_basic", "tushare",
        etf_daily_bounds_df=bounds, freq="daily")
    result = validator.validate(std, "etf_basic", "batch", "tushare", expected_freq="daily")
    assert len(result.passed_df) == 1
    assert not result.rejected_rows
    assert "etf_basic_baseline_standardize" in meta["applied_steps"]

    writer = DuckDBWriter({"type": "duckdb", "path": str(tmp_path / "etf.db")})
    try:
        written = writer.write(result.passed_df, "etf_basic", "batch")
        assert int(written) == written.new == 1
        stored = writer.read_df("SELECT * FROM etf_basic")
        assert list(stored.columns) == BASELINE_COLUMNS
        assert stored.loc[0, "exchange"] == "SS"
        assert stored.loc[0, "fund_type"] == "\u80a1\u7968\u578b"
    finally:
        writer.close()


def test_snapshot_incremental_write_skips_unchanged_and_upserts_changes(tmp_path):
    writer = DuckDBWriter({"type": "duckdb", "path": str(tmp_path / "snapshot.db")})
    aligner = FieldAligner.from_config(ROOT / "config" / "alignment_rules.json")
    collector = object.__new__(ResidentCollector)
    collector.writer = writer
    collector.aligner = aligner
    task = {
        "dataset_kind": "snapshot", "skip_unchanged": True,
        "change_compare_exclude": ["update_time"],
        "data_source_label": "tushare_fund_basic",
    }
    first = build_payload(_raw(), update_time="2026-07-25T10:00:00+08:00")
    try:
        one = collector._stamp_and_write(SimpleNamespace(passed_df=first),
                                         "etf_basic", "b1", "tushare", task=task)
        assert one.new == 1

        same = first.copy()
        same["update_time"] = "2026-07-25T11:00:00+08:00"
        zero = collector._stamp_and_write(SimpleNamespace(passed_df=same),
                                          "etf_basic", "b2", "tushare", task=task)
        assert int(zero) == 0

        changed = same.copy()
        changed["name"] = "Renamed ETF"
        upd = collector._stamp_and_write(SimpleNamespace(passed_df=changed),
                                         "etf_basic", "b3", "tushare", task=task)
        assert upd.updated == 1
        stored = writer.read_df("SELECT name, update_time, data_source FROM etf_basic")
        assert stored.iloc[0].to_dict() == {
            "name": "Renamed ETF",
            "update_time": "2026-07-25T11:00:00+08:00",
            "data_source": "tushare_fund_basic",
        }
    finally:
        writer.close()


def test_etf_basic_task_is_single_source_snapshot_and_daily_watermark():
    cfg = json.loads((ROOT / "config" / "collector_tasks.json").read_text(encoding="utf-8"))
    task = next(t for t in cfg["tasks"] if t["name"] == "etf_basic")
    assert task["source"] == "tushare"
    assert task["source_priority"] == ["tushare"]
    assert task["dataset_kind"] == "snapshot"
    assert task["skip_unchanged"] is True
    assert ResidentCollector._snapshot_watermark("2026-07-25") == str(_ms("2026-07-25"))
