from pathlib import Path
from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest

from quantstudio.backtest.backtest_engine import BacktestEngine
from quantstudio.backtest.providers.duckdb_provider import DuckDBCalendarProvider


START = "2026-01-01"
END = "2026-06-30"
ORIGINAL_MESSAGE = f"No trading days in backtest range: {START} ~ {END}"


def _to_ms(date: str) -> int:
    return int(pd.Timestamp(date, tz="Asia/Shanghai").timestamp() * 1000)


def _create_stock_daily(db_path: Path, dates=()) -> None:
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE stock_daily (time BIGINT)")
        if dates:
            conn.executemany(
                "INSERT INTO stock_daily VALUES (?)",
                [(_to_ms(date),) for date in dates],
            )
    finally:
        conn.close()


def _engine(calendar) -> BacktestEngine:
    providers = SimpleNamespace(calendar=calendar)
    return BacktestEngine(
        db_path=None,
        strategy={},
        start=START,
        end=END,
        providers=providers,
    )


def test_error_still_value_error(tmp_path):
    db_path = tmp_path / "empty.duckdb"
    _create_stock_daily(db_path)

    with pytest.raises(ValueError):
        _engine(DuckDBCalendarProvider(db_path)).run()


def test_diagnose_data_range_mismatch(tmp_path):
    db_path = tmp_path / "range-mismatch.duckdb"
    _create_stock_daily(db_path, ["2025-01-02", "2025-12-31"])

    with pytest.raises(ValueError) as exc_info:
        _engine(DuckDBCalendarProvider(db_path)).run()

    message = str(exc_info.value)
    assert ORIGINAL_MESSAGE in message
    assert "2025-01-02 ~ 2025-12-31" in message
    assert "交易日总数：2" in message
    assert "采集" in message


def test_diagnose_connection_failure(tmp_path):
    db_path = tmp_path / "missing.duckdb"

    with pytest.raises(ValueError) as exc_info:
        _engine(DuckDBCalendarProvider(db_path)).run()

    message = str(exc_info.value)
    assert ORIGINAL_MESSAGE in message
    assert "无法连接数据库" in message
    assert "文件是否存在：False" in message
    assert str(db_path) in message


def test_mock_provider_without_diagnose_keeps_original_message():
    class CalendarMock:
        def get_trade_days(self, start_date, end_date):
            return []

    with pytest.raises(ValueError) as exc_info:
        _engine(CalendarMock()).run()

    assert str(exc_info.value) == ORIGINAL_MESSAGE


def test_diagnose_never_raises():
    class BrokenDiagnosticCalendar:
        def get_trade_days(self, start_date, end_date):
            return []

        def diagnose(self):
            raise RuntimeError("diagnostic failure")

    with pytest.raises(ValueError) as exc_info:
        _engine(BrokenDiagnosticCalendar()).run()

    assert str(exc_info.value) == ORIGINAL_MESSAGE


def test_success_path_never_calls_diagnose():
    class SuccessPathReached(Exception):
        pass

    class CalendarWithForbiddenDiagnostic:
        def get_trade_days(self, start_date, end_date):
            return [pd.Timestamp("2026-01-05", tz="Asia/Shanghai").to_pydatetime()]

        def get_trading_day(self, date, offset=0):
            raise SuccessPathReached

        def diagnose(self):
            raise AssertionError("success path must not call diagnose")

    with pytest.raises(SuccessPathReached):
        _engine(CalendarWithForbiddenDiagnostic()).run()
