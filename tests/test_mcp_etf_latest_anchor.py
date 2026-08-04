from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from quantstudio.pipeline.sources.mcp_adapter import MCPAdapter
from quantstudio.pipeline.validator import PreIngestValidator


def _adapter(tmp_path):
    main_db = tmp_path / "quantstudio.db"
    main_db.touch()
    aux_db = tmp_path / "qfq_aux.db"
    with sqlite3.connect(aux_db) as conn:
        conn.execute(
            "CREATE TABLE fund_adj ("
            "code TEXT, time INTEGER, adj_factor REAL, PRIMARY KEY(code, time))"
        )
        conn.executemany(
            "INSERT INTO fund_adj VALUES (?,?,?)",
            [
                ("510500", 1_700_000_000_000, 1.0),
                ("510500", 1_785_686_400_000, 0.3401),
            ],
        )
    adapter = MCPAdapter.__new__(MCPAdapter)
    adapter.main_db = str(main_db)
    adapter._adj_latest_cache = {}
    adapter.enable_adj_coldstart = False
    adapter._coldstart_done = set()
    return adapter


def test_etf_restore_anchor_uses_factor_at_latest_time_not_historical_max(tmp_path):
    adapter = _adapter(tmp_path)

    latest = adapter._get_adj_latest_global(["510500.SH"], asset_type="ETF")

    assert latest == {"510500": pytest.approx(0.3401)}


def test_nonmonotonic_etf_factor_restores_current_and_historical_raw_prices(tmp_path):
    adapter = _adapter(tmp_path)
    latest = 0.3401
    historical_raw = 2.5
    historical_qfq = historical_raw * 1.0 / latest
    current_raw = 7.42
    frame = pd.DataFrame(
        {
            "ts_code": ["510500.SH", "510500.SH"],
            "trade_date": [20130315, 20260803],
            "open": [historical_qfq, current_raw],
            "high": [historical_qfq, current_raw],
            "low": [historical_qfq, current_raw],
            "close": [historical_qfq, current_raw],
            "pre_close": [historical_qfq, current_raw],
            "vol": [1000.0, 1000.0],
            "amount": [historical_raw * 1000.0, current_raw * 1000.0],
            "adj_factor": [1.0, latest],
            "is_qfq": [True, True],
        }
    )

    restored, meta = adapter._restore_to_raw(frame.copy(), "etf_daily", "daily")

    assert restored["close"].tolist() == pytest.approx([historical_raw, current_raw])
    assert meta["restored_rows"] == 2


def test_factor_snapshot_sync_invalidates_cached_anchor(tmp_path):
    adapter = _adapter(tmp_path)
    adapter._adj_latest_cache = {"ETF": {"510500": 1.0}}
    frame = pd.DataFrame(
        {
            "ts_code": ["510500.SH"],
            "trade_date": [pd.Timestamp("2026-08-03")],
            "adj_factor": [0.3401],
        }
    )

    assert adapter._sync_factor_snapshot(frame, "etf_daily", "daily") is True
    assert "ETF" not in adapter._adj_latest_cache
    assert adapter._get_adj_latest_global(["510500"], "ETF")["510500"] == pytest.approx(0.3401)


def test_unit_check_uses_raw_close_not_derived_close_front():
    import json
    from pathlib import Path

    config = json.loads(
        Path("config/profiles/mcp_only/alignment_rules.json").read_text(encoding="utf-8")
    )
    validator = PreIngestValidator(config["schemas"])
    frame = pd.DataFrame(
        {
            "code": ["510500"],
            "time": [1_785_686_400_000],
            "open": [7.4],
            "high": [7.5],
            "low": [7.3],
            "close": [7.42],
            "volume": [100_000.0],
            "amount": [742_000.0],
            "open_front": [22.2],
            "high_front": [22.5],
            "low_front": [21.9],
            "close_front": [22.26],
        }
    )

    result = validator.validate(frame, "etf_daily", "anchor-regression", "mcp")

    assert len(result.rejected_rows) == 0
    assert all("UnitCheck" not in rules for rules in result.rejected_rules)
