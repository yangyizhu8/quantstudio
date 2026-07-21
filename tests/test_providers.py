from pathlib import Path
from unittest.mock import Mock

from quantstudio.backtest.providers.base import (
    CalendarProvider,
    DataProviderRegistry,
    FundamentalDataProvider,
    MarketDataProvider,
    ReferenceDataProvider,
)
from quantstudio.backtest.providers.duckdb_provider import (
    DuckDBCalendarProvider,
    DuckDBFundamentalDataProvider,
    DuckDBMarketDataProvider,
    DuckDBReferenceDataProvider,
)
from quantstudio.backtest.ptrade_api import PtradeAPI


def test_ptrade_api_accepts_mock_providers_without_database():
    market = Mock(spec=MarketDataProvider)
    fundamental = Mock(spec=FundamentalDataProvider)
    reference = Mock(spec=ReferenceDataProvider)
    calendar = Mock(spec=CalendarProvider)
    reference.get_index_constituents.return_value = ['600000', '000001']

    api = PtradeAPI(market, fundamental, reference, calendar)

    assert api.get_index_stocks('000300.XSHG') == ['600000.SS', '000001.SZ']
    reference.get_index_constituents.assert_called_once_with('000300', None)


def test_registry_from_duckdb_constructs_all_provider_types(tmp_path: Path):
    registry = DataProviderRegistry.from_duckdb(tmp_path / 'provider-test.duckdb')

    assert isinstance(registry.market, DuckDBMarketDataProvider)
    assert isinstance(registry.fundamental, DuckDBFundamentalDataProvider)
    assert isinstance(registry.reference, DuckDBReferenceDataProvider)
    assert isinstance(registry.calendar, DuckDBCalendarProvider)
