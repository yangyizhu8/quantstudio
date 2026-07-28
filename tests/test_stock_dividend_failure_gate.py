"""
Test the stock_dividend daemon failure gate logic using a temporary DuckDB
and a mock adapter with controllable success/failure behaviour.

All tests are standalone — no Tushare credentials required.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import duckdb
import pandas as pd
import pytest

# Ensure project root is on sys.path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from quantstudio.pipeline.daemon import ResidentCollector, BatchAudit
from quantstudio.pipeline.writers import DuckDBWriter, DDL_DUCKDB
from quantstudio.pipeline.validator import ValidationResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TZ = "Asia/Shanghai"


def _ms(day_str: str) -> int:
    """Convert a YYYY-MM-DD string to Asia/Shanghai midnight epoch ms."""
    ts = pd.Timestamp(day_str).tz_localize(TZ)
    return int(ts.value // 10**6)


def _make_dividend_row(code: str, ex_date_str: str, cash_before: float = 0.5,
                       cash_after: float = 0.45) -> dict:
    """Build one stock_dividend row dict."""
    ex_ms = _ms(ex_date_str)
    return {
        "code": code,
        "ex_date": ex_ms,
        "record_date": ex_ms - 86_400_000,
        "ann_date": ex_ms - 7 * 86_400_000,
        "end_date": ex_ms - 30 * 86_400_000,
        "cash_div_before_tax": cash_before,
        "cash_div_after_tax": cash_after,
        "cash_div": None,
        "stk_div": None,
        "stk_bo_rate": None,
        "stk_co_rate": None,
        "div_rat": None,
        "div_proc": "实施",
        "update_time": ex_date_str,
    }


def _make_mini_db(db_path: str):
    """Create a temp DuckDB with source_watermark + stock_dividend tables."""
    conn = duckdb.connect(db_path)
    # source_watermark
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_watermark (
            source VARCHAR, table_name VARCHAR, freq VARCHAR,
            last_date BIGINT, last_batch_id VARCHAR, updated_at TIMESTAMP,
            PRIMARY KEY(source, table_name, freq)
        )
    """)
    # stock_dividend (includes data_source column for writer compatibility)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_dividend (
            code VARCHAR, ex_date BIGINT, record_date BIGINT,
            ann_date BIGINT, end_date BIGINT,
            cash_div_before_tax DOUBLE, cash_div_after_tax DOUBLE,
            cash_div DOUBLE, stk_div DOUBLE,
            stk_bo_rate DOUBLE, stk_co_rate DOUBLE,
            div_rat DOUBLE, div_proc VARCHAR,
            update_time VARCHAR, data_source VARCHAR,
            PRIMARY KEY(code, ex_date)
        )
    """)
    conn.close()


def _make_writer(db_path: str) -> DuckDBWriter:
    """Create a DuckDBWriter pointed at our temp DB."""
    return DuckDBWriter({"type": "duckdb", "path": db_path})


def _make_batch_audit(temp_dir: str) -> BatchAudit:
    """Create a BatchAudit with a temp SQLite file."""
    audit_path = Path(temp_dir) / "batch_audit.db"
    return BatchAudit(audit_path)


def _pass_through_align(aligner_mock):
    """Configure the aligner mock to pass data through unchanged."""
    def _align(raw_df, table, source, **kwargs):
        return raw_df, {}
    aligner_mock.align = _align


def _accept_all_validate(validator_mock):
    """Configure the validator mock to accept all rows."""
    def _validate(df, table, batch_id, source, expected_freq="daily"):
        if df is None or len(df) == 0:
            return ValidationResult(pd.DataFrame(), [], [], 0)
        return ValidationResult(df.copy(), [], [], 0)
    validator_mock.validate = _validate


# ---------------------------------------------------------------------------
# Test: _failure_gate unit tests
# ---------------------------------------------------------------------------

class TestFailureGateUnit:
    """Unit tests for the _failure_gate method directly (threshold logic only)."""

    def test_failure_gate_all_pass(self):
        """Zero failures should always pass."""
        collector = ResidentCollector.__new__(ResidentCollector)
        collector.tasks_cfg = {}
        ok, rate, threshold = collector._failure_gate({}, 0, 100)
        assert ok is True
        assert rate == 0.0
        assert threshold == pytest.approx(0.0001)  # default 0.01%

    def test_failure_gate_below_default_threshold(self):
        """1 failure out of 10001 should be below the default 0.01% threshold."""
        collector = ResidentCollector.__new__(ResidentCollector)
        collector.tasks_cfg = {}
        ok, rate, _ = collector._failure_gate({}, 1, 10001)
        assert ok is True   # 1/10001 ≈ 0.009999% < 0.01%

    def test_failure_gate_above_default_threshold(self):
        """1 failure out of 5000 should be above the default 0.01% threshold."""
        collector = ResidentCollector.__new__(ResidentCollector)
        collector.tasks_cfg = {}
        ok, rate, _ = collector._failure_gate({}, 1, 5000)
        assert ok is False  # 1/5000 = 0.02% > 0.01%

    def test_failure_gate_custom_task_threshold(self):
        """Task-level max_failure_rate overrides the global default."""
        collector = ResidentCollector.__new__(ResidentCollector)
        collector.tasks_cfg = {"quality_gate": {"max_failure_rate": 0.0001}}
        task = {"max_failure_rate": 0.1}  # 10% threshold from task
        ok, rate, threshold = collector._failure_gate(task, 5, 100)
        assert ok is True   # 5% < 10%
        assert threshold == 0.1

    def test_failure_gate_xtquant_reject_relaxed(self):
        """xtquant reject rate uses max_reject_rate_xtquant (1%) threshold."""
        collector = ResidentCollector.__new__(ResidentCollector)
        collector.tasks_cfg = {"quality_gate": {"max_reject_rate_xtquant": 0.01}}
        # 2 failures out of 300 = 0.667% < 1% → should pass despite being >> 0.01%
        ok, rate, threshold = collector._failure_gate(
            {}, 2, 300, source="xtquant", is_reject=True)
        assert ok is True
        assert threshold == pytest.approx(0.01)

    def test_failure_gate_zero_attempted(self):
        """Zero attempted should always pass (rate = 0)."""
        collector = ResidentCollector.__new__(ResidentCollector)
        collector.tasks_cfg = {}
        ok, rate, _ = collector._failure_gate({}, 0, 0)
        assert ok is True
        assert rate == 0.0

    def test_failure_gate_all_failed(self):
        """All failed should fail the gate."""
        collector = ResidentCollector.__new__(ResidentCollector)
        collector.tasks_cfg = {"quality_gate": {"max_failure_rate": 0.5}}
        ok, rate, _ = collector._failure_gate({}, 100, 100)
        assert ok is False
        assert rate == 1.0

    def test_failure_gate_negative_threshold_clamped(self):
        """Negative thresholds should be clamped to 0."""
        collector = ResidentCollector.__new__(ResidentCollector)
        collector.tasks_cfg = {}
        task = {"max_failure_rate": -0.5}
        ok, _, threshold = collector._failure_gate(task, 0, 100)
        assert ok is True   # 0 <= 0
        assert threshold == 0.0


# ---------------------------------------------------------------------------
# Test: per-stock flow with mock adapter
# ---------------------------------------------------------------------------

class _StockDividendTask:
    """Minimal task dict matching the stock_dividend config."""
    name = "stock_dividend"
    table = "stock_dividend"
    source = "tushare"
    freq = "daily"
    authoritative_source = "tushare"
    allow_fallback = False
    codes = ["ALL"]
    start_date = "2024-01-01"
    max_workers = 1

    def to_dict(self) -> dict:
        return {
            "name": self.name, "table": self.table, "source": self.source,
            "freq": self.freq, "authoritative_source": self.authoritative_source,
            "allow_fallback": self.allow_fallback, "codes": self.codes,
            "start_date": self.start_date, "max_workers": self.max_workers,
        }


class TestStockDividendPerStockFlow:
    """Integration tests: mock adapter + real DuckDB writer + batch audit."""

    @pytest.fixture
    def setup(self, tmp_path):
        """Prepare a ResidentCollector with mock adapter/algor/validator, real DB."""
        db_path = str(tmp_path / "test.db")
        _make_mini_db(db_path)

        writer = _make_writer(db_path)
        batch_audit = _make_batch_audit(str(tmp_path))

        # Build collector
        collector = ResidentCollector.__new__(ResidentCollector)
        collector.data_cfg = {"path": db_path}
        collector.sources_cfg = {"sources": {"tushare": {"enabled": True}}}
        collector.tasks_cfg = {"quality_gate": {}}
        collector.writer = writer
        collector.batch_audit = batch_audit
        collector._adapters = {}
        collector._running = True

        # Mock aligner (pass-through)
        aligner_mock = MagicMock()
        _pass_through_align(aligner_mock)
        aligner_mock.schemas = {
            "stock_dividend": {
                "primary_key": ["code", "ex_date"],
                "time_key": "ex_date",
                "columns": {"code": {"type": "str"}, "ex_date": {"type": "int"}},
            }
        }
        collector.aligner = aligner_mock

        # Mock validator (accept all)
        validator_mock = MagicMock()
        _accept_all_validate(validator_mock)
        collector.validator = validator_mock

        # Mock quarantine
        quarantine_mock = MagicMock()
        collector.quarantine = quarantine_mock

        return collector, writer, batch_audit, tmp_path, db_path

    # ---- helper ----

    def _run_per_stock(self, collector, mock_get_adapter, task_dict, mode="full_range"):
        """Run _execute_task which dispatches to _execute_task_per_stock for stock_dividend."""
        with patch.object(collector, "_get_adapter", return_value=mock_get_adapter):
            with patch.object(collector, "_resolve_source_chain", return_value=["tushare"]):
                return collector.execute_task(task_dict, mode=mode, run_quality_audit=False)

    def _assert_watermark(self, writer, expected_last_date: int):
        """Assert the source_watermark for tushare/stock_dividend/daily matches."""
        last = writer.get_last_date("tushare", "stock_dividend", "daily")
        if expected_last_date is None:
            assert last is None
        else:
            assert int(last) == expected_last_date

    def _read_batch_audit(self, batch_audit, batch_id: str) -> dict | None:
        """Read a specific batch audit record."""
        import sqlite3
        with sqlite3.connect(batch_audit.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM batch_audit WHERE batch_id=?", [batch_id]).fetchone()
            if row is None:
                return None
            cols = [c[0] for c in conn.execute("PRAGMA table_info(batch_audit)").fetchall()]
            return dict(zip(cols, row))

    # ---- tests ----

    def test_success_with_data(self, setup):
        """adapter returns dividend data for all stocks → watermark advances."""
        collector, writer, batch_audit, tmp_path, db_path = setup
        codes = ["000001", "000002", "000003"]

        # Insert initial watermark (old)
        writer.advance_watermark("tushare", "stock_dividend", "daily", str(_ms("2024-06-01")), "init")

        # Mock adapter
        adapter = MagicMock()
        adapter.get_all_stock_codes.return_value = codes

        def fetch_table(table, start, end, freq, codes):
            code = codes[0] if codes else "000000"
            rows = [_make_dividend_row(code, "2024-06-30")]
            return pd.DataFrame(rows), {}
        adapter.fetch_table = fetch_table

        task = _StockDividendTask().to_dict()
        task_ok = self._run_per_stock(collector, adapter, task)

        assert task_ok is True
        # Watermark should have advanced beyond the initial value
        last = writer.get_last_date("tushare", "stock_dividend", "daily")
        assert last is not None
        assert int(last) >= _ms("2024-06-30")

    def test_success_empty(self, setup):
        """Watermark up to date → date range empty in incremental mode → success."""
        collector, writer, batch_audit, tmp_path, db_path = setup
        codes = ["000001", "000002"]

        # Set watermark to today so incremental mode sees range as empty
        today_ms = _ms(datetime.now().strftime("%Y-%m-%d"))
        writer.advance_watermark("tushare", "stock_dividend", "daily", str(today_ms), "init")

        adapter = MagicMock()
        adapter.get_all_stock_codes.return_value = codes
        # fetch_table should never be called because date range is empty
        adapter.fetch_table.return_value = (pd.DataFrame(), {})

        task = _StockDividendTask().to_dict()
        # Use incremental mode: start = watermark + 1 day (tomorrow) > end (today)
        task_ok = self._run_per_stock(collector, adapter, task, mode="incremental")

        assert task_ok is True
        # Watermark stays at today (already up to date)
        last = writer.get_last_date("tushare", "stock_dividend", "daily")
        assert last is not None
        assert int(last) == today_ms

        # Adapter should NOT have been called (date range empty → skip)
        adapter.fetch_table.assert_not_called()

    def test_single_api_failure(self, setup):
        """One stock raises an exception → fail_count increments, task may still pass."""
        collector, writer, batch_audit, tmp_path, db_path = setup
        codes = ["000001", "000002", "000003"]  # 3 stocks

        writer.advance_watermark("tushare", "stock_dividend", "daily", str(_ms("2024-06-01")), "init")

        adapter = MagicMock()
        adapter.get_all_stock_codes.return_value = codes

        call_count = [0]
        def fetch_table(table, start, end, freq, codes):
            call_count[0] += 1
            code = codes[0]
            if code == "000002":
                raise RuntimeError(f"API error for {code}")
            rows = [_make_dividend_row(code, "2024-07-01")]
            return pd.DataFrame(rows), {}
        adapter.fetch_table = fetch_table

        task = _StockDividendTask().to_dict()
        task_ok = self._run_per_stock(collector, adapter, task)

        # 1 failure out of 3 → rate = 33.3%, above default 0.01% threshold
        # Since task fails gate, watermark should stay at old value
        assert task_ok is False
        last = writer.get_last_date("tushare", "stock_dividend", "daily")
        assert int(last) == _ms("2024-06-01")

    def test_partial_failure_below_threshold(self, setup):
        """Low failure rate below threshold → task succeeds, watermark advances."""
        collector, writer, batch_audit, tmp_path, db_path = setup

        # 100 codes, only 1 fails → 1% failure rate
        codes = [f"{i:06d}" for i in range(100)]

        writer.advance_watermark("tushare", "stock_dividend", "daily", str(_ms("2024-06-01")), "init")

        adapter = MagicMock()
        adapter.get_all_stock_codes.return_value = codes

        fail_code = codes[50]
        def fetch_table(table, start, end, freq, codes):
            code = codes[0]
            if code == fail_code:
                raise RuntimeError(f"API error for {code}")
            rows = [_make_dividend_row(code, "2024-07-01")]
            return pd.DataFrame(rows), {}
        adapter.fetch_table = fetch_table

        task = _StockDividendTask().to_dict()
        # Set a generous failure threshold (10%) so 1% passes
        task["max_failure_rate"] = 0.10
        task_ok = self._run_per_stock(collector, adapter, task)

        assert task_ok is True
        last = writer.get_last_date("tushare", "stock_dividend", "daily")
        assert int(last) >= _ms("2024-07-01")

    def test_partial_failure_above_threshold(self, setup):
        """Failure rate above threshold → watermark does NOT advance."""
        collector, writer, batch_audit, tmp_path, db_path = setup

        # 10 codes, 3 fail → 30% failure rate
        codes = [f"{i:06d}" for i in range(10)]

        old_ms = _ms("2024-06-01")
        writer.advance_watermark("tushare", "stock_dividend", "daily", str(old_ms), "init")

        adapter = MagicMock()
        adapter.get_all_stock_codes.return_value = codes

        fail_codes = {codes[2], codes[5], codes[8]}
        def fetch_table(table, start, end, freq, codes):
            code = codes[0]
            if code in fail_codes:
                raise RuntimeError(f"API error for {code}")
            rows = [_make_dividend_row(code, "2024-07-01")]
            return pd.DataFrame(rows), {}
        adapter.fetch_table = fetch_table

        task = _StockDividendTask().to_dict()
        task["max_failure_rate"] = 0.10  # 10% threshold, 30% fails → above
        task_ok = self._run_per_stock(collector, adapter, task)

        assert task_ok is False
        last = writer.get_last_date("tushare", "stock_dividend", "daily")
        # Watermark should NOT have advanced beyond the old value
        assert int(last) == old_ms

    def test_all_failure(self, setup):
        """All stocks fail → watermark stays old, batch status = failed."""
        collector, writer, batch_audit, tmp_path, db_path = setup

        codes = ["000001", "000002", "000003", "000004"]

        old_ms = _ms("2024-06-01")
        writer.advance_watermark("tushare", "stock_dividend", "daily", str(old_ms), "init")

        adapter = MagicMock()
        adapter.get_all_stock_codes.return_value = codes

        def fetch_table(table, start, end, freq, codes):
            code = codes[0]
            raise RuntimeError(f"API error for {code}")
        adapter.fetch_table = fetch_table

        task = _StockDividendTask().to_dict()
        task_ok = self._run_per_stock(collector, adapter, task)

        assert task_ok is False

        # Watermark must NOT advance
        last = writer.get_last_date("tushare", "stock_dividend", "daily")
        assert int(last) == old_ms
