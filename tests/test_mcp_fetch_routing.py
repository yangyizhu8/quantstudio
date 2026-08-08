"""Regression tests for MCP fetch completeness and QFQ routing isolation."""
from __future__ import annotations

import pandas as pd
import pytest

from quantstudio.pipeline.sources.mcp_adapter import (
    MCPAdapter,
    _EXPORT_TABLES,
    _QFQ_ADJFACTOR_TABLES,
)


class _PagedClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def fetch_page(self, dataset_id, cursor="", page_size=50_000, columns=None):
        self.calls.append((dataset_id, cursor, page_size))
        return self.pages[cursor]


def _bare_adapter(client=None):
    adapter = MCPAdapter.__new__(MCPAdapter)
    adapter._client = client
    adapter.endpoint = "https://example.invalid/mcp"
    adapter.enable_qfq_restore = True
    return adapter


def test_qfq_restore_scope_is_exact_table_frequency_whitelist():
    assert MCPAdapter._requires_qfq_restore("stock_daily", "daily")
    assert MCPAdapter._requires_qfq_restore("etf_minutes", "1min")
    assert not MCPAdapter._requires_qfq_restore("index_constituents", "daily")
    assert not MCPAdapter._requires_qfq_restore("balance_statement", "daily")
    assert not MCPAdapter._requires_qfq_restore("stock_daily_valuation", "daily")
    assert not MCPAdapter._requires_qfq_restore("index_daily", "daily")

    assert ("stock_daily_valuation", "daily") in _EXPORT_TABLES
    assert ("stock_daily_valuation", "daily") not in _QFQ_ADJFACTOR_TABLES
    assert ("index_constituents", "daily") in _EXPORT_TABLES
    assert ("index_constituents", "daily") not in _QFQ_ADJFACTOR_TABLES


def test_non_qfq_export_table_never_touches_factor_snapshot(monkeypatch):
    adapter = _bare_adapter()
    frame = pd.DataFrame({"index_code": ["000300.SH"], "weight": [1.0]})

    def unexpected(*args, **kwargs):
        raise AssertionError("non-QFQ table must not touch factor snapshot or restore")

    monkeypatch.setattr(adapter, "_sync_factor_snapshot", unexpected)
    monkeypatch.setattr(adapter, "_restore_to_raw", unexpected)

    result, meta = adapter._restore_qfq_if_required(
        frame, "index_constituents", "daily")

    assert result is frame
    assert meta["is_qfq_restored"] is False
    assert meta["restored_rows"] == 0
    assert meta["restore_skip_reason"] == (
        "table_freq_not_in_qfq_scope:index_constituents/daily")


def test_qfq_table_still_fails_fast_when_factor_sync_fails(monkeypatch):
    adapter = _bare_adapter()
    frame = pd.DataFrame(
        {"ts_code": ["000001.SZ"], "trade_date": [20260803], "adj_factor": [1.0]})
    monkeypatch.setattr(adapter, "_sync_factor_snapshot", lambda *args: False)

    with pytest.raises(ValueError, match="factor snapshot synchronization failed"):
        adapter._restore_qfq_if_required(frame, "stock_daily", "daily")


def test_qfq_table_syncs_then_restores(monkeypatch):
    adapter = _bare_adapter()
    frame = pd.DataFrame(
        {"ts_code": ["000001.SZ"], "trade_date": [20260803], "adj_factor": [1.0]})
    calls = []

    def sync(df, table, freq):
        calls.append(("sync", table, freq, len(df)))
        return True

    def restore(df, table, freq, **kwargs):
        calls.append(("restore", table, freq, len(df)))
        return df, {"is_qfq_restored": True, "restored_rows": len(df)}

    monkeypatch.setattr(adapter, "_sync_factor_snapshot", sync)
    monkeypatch.setattr(adapter, "_restore_to_raw", restore)

    result, meta = adapter._restore_qfq_if_required(frame, "stock_daily", "daily")

    assert result is frame
    assert meta == {"is_qfq_restored": True, "restored_rows": 1}
    assert calls == [
        ("sync", "stock_daily", "daily", 1),
        ("restore", "stock_daily", "daily", 1),
    ]


def test_fetch_all_pages_concatenates_until_cursor_exhausted():
    client = _PagedClient(
        {
            "": {"rows": [{"id": 1}, {"id": 2}], "next_cursor": "c2"},
            "c2": {"rows": [{"id": 3}], "next_cursor": None},
        }
    )
    adapter = _bare_adapter(client)

    frame, pages = adapter._fetch_all_pages("index_daily", page_size=2)

    assert frame["id"].tolist() == [1, 2, 3]
    assert pages == 2
    assert client.calls == [
        ("index_daily", "", 2),
        ("index_daily", "c2", 2),
    ]


def test_fetch_all_pages_rejects_non_advancing_cursor():
    client = _PagedClient(
        {
            "": {"rows": [{"id": 1}], "next_cursor": "same"},
            "same": {"rows": [{"id": 2}], "next_cursor": "same"},
        }
    )
    adapter = _bare_adapter(client)

    with pytest.raises(ValueError, match="cursor did not advance"):
        adapter._fetch_all_pages("broken")


def test_small_mapped_table_uses_all_pages_and_skips_qfq(monkeypatch):
    client = _PagedClient(
        {
            "": {
                "rows": [{"ts_code": "000300.SH", "trade_date": "2026-08-01"}],
                "next_cursor": "c2",
            },
            "c2": {
                "rows": [{"ts_code": "000905.SH", "trade_date": "2026-08-03"}],
                "next_cursor": None,
            },
        }
    )
    adapter = _bare_adapter(client)
    monkeypatch.setattr(
        adapter, "_sync_factor_snapshot",
        lambda *args: (_ for _ in ()).throw(AssertionError("unexpected factor sync")),
    )

    frame, meta = adapter._fetch_small_table(
        "index_daily", "daily", "2026-08-01", "2026-08-03", ["ALL"])

    assert frame["ts_code"].tolist() == ["000300.SH", "000905.SH"]
    assert meta["lineage"]["pages"] == 2
    assert meta["is_qfq_capable"] is False
    assert meta["restore_skip_reason"] == "table_freq_not_in_qfq_scope:index_daily/daily"


def test_passthrough_table_uses_all_pages_before_date_filter():
    client = _PagedClient(
        {
            "": {
                "rows": [
                    {"ts_code": "000001.SZ", "trade_date": "2026-07-31", "value": 1},
                    {"ts_code": "000002.SZ", "trade_date": "2026-08-01", "value": 2},
                ],
                "next_cursor": "c2",
            },
            "c2": {
                "rows": [
                    {"ts_code": "000003.SZ", "trade_date": "2026-08-03", "value": 3}
                ],
                "next_cursor": None,
            },
        }
    )
    adapter = _bare_adapter(client)
    adapter.endpoint = "https://example.invalid/mcp"

    frame, meta = adapter._fetch_passthrough(
        "block_trade", "daily", "2026-08-01", "2026-08-03", ["ALL"])

    assert frame["value"].tolist() == [2, 3]
    assert meta["rows"] == 2
    assert meta["lineage"]["pages"] == 2


def test_index_constituents_filters_out_of_contract_indices_and_normalizes_both_codes(
        tmp_path, monkeypatch):
    class Artifact:
        artifact_id = "job/shard0"
        parquet_bytes = b"parquet"
        raw = {"job_id": "job"}

    class Client:
        def export_dataset(self, **kwargs):
            return [Artifact()]

    raw = pd.DataFrame(
        {
            "index_code": ["000300.SH", "H30066.CSI"],
            "ts_code": ["000001.SZ", "000002.SZ"],
            "trade_date": [pd.Timestamp("2026-07-31")] * 2,
            "weight": [1.0, 2.0],
        }
    )
    adapter = _bare_adapter(Client())
    adapter._landing_root = tmp_path
    monkeypatch.setattr(pd, "read_parquet", lambda path: raw.copy())

    frame, meta = adapter._fetch_export(
        "index_constituents", "daily", "2026-07-31", "2026-07-31", ["ALL"])

    assert frame["index_code"].tolist() == ["000300.SH"]
    assert meta["filtered_out_of_contract_rows"] == 1
    assert meta["filtered_out_of_contract_codes"] == ["H30066.CSI"]
    assert meta["restore_skip_reason"] == (
        "table_freq_not_in_qfq_scope:index_constituents/daily")

    from quantstudio.pipeline.aligner import FieldAligner
    from quantstudio.pipeline.validator import PreIngestValidator

    aligner = FieldAligner.from_config(
        "config/profiles/mcp_only/alignment_rules.json")
    aligned, _ = aligner.align(
        frame, "index_constituents", "mcp", freq="daily")
    assert aligned.loc[0, "index_code"] == "000300"
    assert aligned.loc[0, "code"] == "000001"

    validator = PreIngestValidator.from_config(
        "config/profiles/mcp_only/alignment_rules.json")
    result = validator.validate(
        aligned, "index_constituents", "index-constituents-contract", "mcp")
    assert len(result.passed_df) == 1
    assert len(result.rejected_rows) == 0


def test_validator_checks_every_declared_code_field():
    from quantstudio.pipeline.validator import PreIngestValidator

    validator = PreIngestValidator.from_config(
        "config/profiles/mcp_only/alignment_rules.json")
    frame = pd.DataFrame(
        {
            "index_code": ["000300"],
            "code": ["000001.SZ"],
            "time": [1_785_427_200_000],
            "weight": [1.0],
        }
    )

    result = validator.validate(
        frame, "index_constituents", "multi-code-validation", "mcp")

    assert len(result.passed_df) == 0
    assert result.rejected_rules == [["CodeFormat"]]
    assert result.error_values == [{"code": "000001.SZ"}]



def test_empty_qfq_frame_skips_factor_sync(monkeypatch):
    adapter = _bare_adapter()
    frame = pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"])

    def unexpected(*args, **kwargs):
        raise AssertionError("empty QFQ frame must not synchronize factors")

    monkeypatch.setattr(adapter, "_sync_factor_snapshot", unexpected)
    result, meta = adapter._restore_qfq_if_required(frame, "stock_daily", "daily")

    assert result is frame
    assert meta["restore_skip_reason"] == "empty_df"
    assert meta["restored_rows"] == 0
