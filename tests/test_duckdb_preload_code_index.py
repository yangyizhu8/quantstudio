from __future__ import annotations

import pandas as pd

from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess


def test_daily_preload_builds_code_index_and_preserves_pit_semantics(tmp_path):
    access = DuckDBDataAccess(tmp_path / "unused.db")
    access._preload_daily = pd.DataFrame([
        {"code": "600000", "time": 300, "close": 3.0, "close_front": 30.0},
        {"code": "000001", "time": 300, "close": 13.0, "close_front": 130.0},
        {"code": "600000", "time": 200, "close": 2.0, "close_front": 20.0},
        {"code": "000001", "time": 200, "close": 12.0, "close_front": 120.0},
        {"code": "600000", "time": 100, "close": 1.0, "close_front": 10.0},
    ])
    access._preload_prev_ms = 250
    result = access.get_history_from_preload(
        ["600000.SS", "000001.SZ"], count=2, fq="pre", is_dict=True,
        fields=["close"], field_map={"close": "close"})
    assert set(access._preload_daily_code_positions) == {"600000", "000001"}
    assert result["600000.SS"]["close"].tolist() == [10.0, 20.0]
    assert result["000001.SZ"]["close"].tolist() == [120.0]


def test_daily_preload_code_index_is_reused(tmp_path):
    access = DuckDBDataAccess(tmp_path / "unused.db")
    access._preload_daily = pd.DataFrame([
        {"code": "600000", "time": 200, "close": 2.0},
        {"code": "600000", "time": 100, "close": 1.0},
    ])
    access._preload_prev_ms = 999
    access.get_history_from_preload(
        ["600000.SS"], 1, None, True, ["close"], {"close": "close"})
    first_index = access._preload_daily_code_positions
    access.get_history_from_preload(
        ["600000.SS"], 2, None, True, ["close"], {"close": "close"})
    assert access._preload_daily_code_positions is first_index
