"""Regression coverage for the full-database quality-audit repair."""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

from quantstudio.pipeline.aligner import FieldAligner
from quantstudio.pipeline.quality_audit import DataQualityAuditor
from quantstudio.pipeline.qfq_calendar import CalendarService
from quantstudio.pipeline.sources.mcp_adapter import _EXPORT_TABLES
from quantstudio.pipeline.validator import PreIngestValidator
from quantstudio.pipeline.writers import DuckDBWriter

ROOT = Path(__file__).resolve().parents[1]
PROFILE_RULES = ROOT / "config" / "profiles" / "mcp_only" / "alignment_rules.json"


def test_boolean_enum_audit_accepts_zero_one_contract(tmp_path):
    db = tmp_path / "enum.db"
    with duckdb.connect(str(db)) as conn:
        conn.execute("CREATE TABLE trade_calendar(cal_date BIGINT PRIMARY KEY, is_open BOOLEAN)")
        conn.execute("INSERT INTO trade_calendar VALUES (1, TRUE), (2, FALSE)")

    schemas = {
        "trade_calendar": {
            "primary_key": ["cal_date"],
            "columns": {
                "cal_date": {"type": "int", "required": True},
                "is_open": {"type": "int", "required": True, "enum": [0, 1]},
            },
        }
    }
    report = DataQualityAuditor(db, schemas).run()
    assert not [i for i in report.issues if i.check == "EnumCheck"]
    assert report.passed


def test_writer_rejects_legacy_calendar_partial_db(tmp_path):
    """v2.4 B-3a.3 P0-1：DuckDBWriter 不再迁移旧 trade_calendar——旧/共享表 partial 库
    必须在 writer init 第一条 DDL 前 fail-fast（_WriterSchemaMigrationRequired），
    不得让普通 writer 自动升级或修补 QFQ schema。

    （原 test_writer_migrates_legacy_calendar_without_changing_single_pk 测的"writer
    自动迁移旧 calendar"行为已被 P0-1 明确移除：partial 库禁止 writer init。）
    """
    import pytest
    from quantstudio.pipeline.writers import _WriterSchemaMigrationRequired
    db = tmp_path / "legacy.db"
    with duckdb.connect(str(db)) as conn:
        # 旧 4 列 trade_calendar（缺 exchange/pretrade_date）→ 与 target 不符 → partial
        conn.execute(
            "CREATE TABLE trade_calendar("
            "cal_date BIGINT NOT NULL, is_open BOOLEAN NOT NULL, "
            "source VARCHAR, updated_at TIMESTAMP, PRIMARY KEY(cal_date))"
        )
        conn.execute("INSERT INTO trade_calendar VALUES (1, TRUE, 'legacy', CURRENT_TIMESTAMP)")
        before = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}

    # writer init 必须写前 fail-fast（旧 calendar → partial_or_mixed）
    with pytest.raises(_WriterSchemaMigrationRequired):
        DuckDBWriter({"type": "duckdb", "path": str(db)})

    # 表集合不变（0 DDL）
    with duckdb.connect(str(db), read_only=True) as conn:
        after = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    assert before == after


def test_stock_basic_profile_preserves_ts_code_and_writer_contract(tmp_path):
    aligner = FieldAligner.from_config(PROFILE_RULES)
    validator = PreIngestValidator.from_config(PROFILE_RULES)
    raw = pd.DataFrame(
        [{
            "ts_code": "000001.SZ",
            "symbol": "000001",
            "name": "Ping An Bank",
            "area": "Shenzhen",
            "industry": "Bank",
            "market": "Main Board",
            "exchange": "SZSE",
            "list_status": "L",
            "list_date": "1991-04-03T00:00:00",
            "delist_date": None,
        }]
    )
    aligned, _ = aligner.align(raw, "stock_basic", "mcp", freq="daily")
    assert aligned.loc[0, "code"] == "000001"
    assert aligned.loc[0, "symbol"] == "000001"
    assert aligned.loc[0, "ts_code"] == "000001.SZ"

    result = validator.validate(aligned, "stock_basic", "b1", "mcp")
    assert len(result.passed_df) == 1
    assert len(result.rejected_rows) == 0

    db = tmp_path / "stock_basic.db"
    writer = DuckDBWriter({"type": "duckdb", "path": str(db)})
    to_write = result.passed_df.copy()
    to_write["data_source"] = "mcp"
    written = writer.write(to_write, "stock_basic", "b1")
    writer.close()
    assert int(written) == 1
    with duckdb.connect(str(db), read_only=True) as conn:
        row = conn.execute(
            "SELECT code, ts_code, list_status, data_source FROM stock_basic"
        ).fetchone()
    assert row == ("000001", "000001.SZ", "L", "mcp")


def test_trade_calendar_uses_snapshot_route_and_qfq_persists_sse_metadata(tmp_path):
    assert ("trade_calendar", "daily") not in _EXPORT_TABLES

    db = tmp_path / "calendar.db"
    writer = DuckDBWriter({"type": "duckdb", "path": str(db)})
    writer.close()
    service = CalendarService(main_db=db)
    with duckdb.connect(str(db)) as conn:
        n = service.persist_trade_days_on_conn(
            conn, [1000], closed_ms=[2000], source="unit", updated_at="2026-08-03T00:00:00"
        )
    assert n == 2
    with duckdb.connect(str(db), read_only=True) as conn:
        rows = conn.execute(
            "SELECT cal_date, is_open, exchange, source FROM trade_calendar ORDER BY cal_date"
        ).fetchall()
        pk_cols = [r[1] for r in conn.execute("PRAGMA table_info('trade_calendar')").fetchall() if r[5]]
    assert rows == [(1000, True, "SSE", "unit"), (2000, False, "SSE", "unit")]
    assert pk_cols == ["cal_date"]


def test_mcp_profile_calendar_schema_matches_single_calendar_storage():
    rules = json.loads(PROFILE_RULES.read_text(encoding="utf-8"))
    schema = rules["schemas"]["trade_calendar"]
    assert schema["primary_key"] == ["cal_date"]
    assert schema["code_field"] is None
    assert schema["columns"]["exchange"]["required"] is True



def test_qfq_pending_sla_measures_current_state_age(tmp_path):
    db = tmp_path / "qfq_sla.db"
    with duckdb.connect(str(db)) as conn:
        conn.execute(
            "CREATE TABLE qfq_trigger_queue("
            "trigger_id VARCHAR, status VARCHAR, created_at TIMESTAMP, "
            "updated_at TIMESTAMP, claimed_at TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO qfq_trigger_queue VALUES "
            "('fresh_retry', 'retryable_failed', NOW() - INTERVAL 100 HOUR, "
            " NOW() - INTERVAL 1 HOUR, NULL), "
            "('stale_retry', 'retryable_failed', NOW() - INTERVAL 100 HOUR, "
            " NOW() - INTERVAL 80 HOUR, NULL)"
        )
    report = DataQualityAuditor(
        db, {}, qfq_thresholds={"pending_sla_hours": 72}
    ).run()
    issue = next(i for i in report.issues if i.check == "QfqPendingSla")
    assert issue.count == 1


def test_trade_calendar_profile_aligns_and_validates_without_treating_date_as_code():
    aligner = FieldAligner.from_config(PROFILE_RULES)
    validator = PreIngestValidator.from_config(PROFILE_RULES)
    raw = pd.DataFrame(
        [{
            "exchange": "SSE",
            "cal_date": "2026-08-03T00:00:00",
            "is_open": 1,
            "pretrade_date": "2026-07-31T00:00:00",
        }]
    )
    aligned, _ = aligner.align(raw, "trade_calendar", "mcp", freq="daily")
    result = validator.validate(aligned, "trade_calendar", "cal1", "mcp")
    assert len(result.passed_df) == 1
    assert len(result.rejected_rows) == 0


def test_reference_table_adapter_shape_completion_preserves_symbol_and_stamps_calendar():
    from quantstudio.pipeline.sources.mcp_adapter import MCPAdapter

    class Client:
        def fetch_page(self, dataset_id, cursor="", page_size=50_000):
            assert cursor == ""
            if dataset_id == "stock_basic":
                rows = [{
                    "ts_code": "000001.SZ",
                    "symbol": "000001",
                    "name": "Ping An Bank",
                    "exchange": "SZSE",
                    "list_status": "L",
                }]
            else:
                rows = [{
                    "exchange": "SSE",
                    "cal_date": "2026-08-03T00:00:00",
                    "is_open": 1,
                    "pretrade_date": "2026-07-31T00:00:00",
                }]
            return {"rows": rows, "next_cursor": None}

        def query_snapshot(self, *args, **kwargs):
            raise AssertionError("reference tables must use cursor pagination")

    adapter = MCPAdapter({"base_url": "https://example.invalid", "enable_qfq_injection": False})
    adapter._client = Client()
    stock, _ = adapter._fetch_small_table(
        "stock_basic", "daily", "2018-01-01", "2026-08-03", ["ALL"])
    calendar, _ = adapter._fetch_small_table(
        "trade_calendar", "daily", "2018-01-01", "2026-08-03", ["ALL"])
    assert stock.loc[0, "symbol"] == "000001"
    assert stock.loc[0, "code"] == "000001"
    assert stock.loc[0, "ts_code"] == "000001.SZ"
    assert calendar.loc[0, "source"] == "mcp"
    assert "updated_at" not in calendar.columns
