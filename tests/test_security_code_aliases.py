"""PR1 suffix alias interoperability tests across existing public adapters."""

from unittest.mock import Mock

import pandas as pd

from quantstudio.backtest.backtest_engine import Account, BacktestEngine, Position as EnginePosition
from quantstudio.backtest.ptrade_api import CodeDict, DataDict, PtradeAPI
from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
from quantstudio.backtest.providers.duckdb_provider import DuckDBReferenceDataProvider


def test_engine_api_and_data_access_delegate_to_authoritative_normalizers():
    assert BacktestEngine._to_qmt("600000.SS") == "600000.SH"
    assert BacktestEngine._to_qmt("000001.XSHE") == "000001.SZ"
    assert BacktestEngine._to_qmt("920017.XBJ") == "920017.BJ"

    assert PtradeAPI._bare_to_qmt("920017") == "920017.BJ"
    assert PtradeAPI._to_ptrade_code("600000.XSHG") == "600000.SS"
    assert PtradeAPI._to_ptrade_code("920017.XBJ") == "920017.BJ"

    assert DuckDBDataAccess._to_ptrade_code("000001.SZ") == "000001.SZ"
    assert DuckDBDataAccess._to_ptrade_code("920017.BJ") == "920017.BJ"


def test_alias_aware_containers_support_bse_suffixes():
    values = CodeDict({"920017.BJ": 30})
    assert values["920017.XBJ"] == 30
    assert values["920017"] == 30

    data = DataDict()
    assert data._guess_ptrade_code("920017") == "920017.BJ"


def test_unknown_bare_code_keeps_legacy_sh_fallback():
    assert BacktestEngine._to_qmt("777777") == "777777.SH"
    assert PtradeAPI._to_ptrade_code("777777") == "777777.SS"


def test_bse_positions_are_exposed_with_bj_suffix():
    engine = object.__new__(BacktestEngine)
    engine.account = Account(cash=1000.0, positions={
        "920017.BJ": EnginePosition(
            "920017.BJ", volume=100, avg_cost=10.0, can_sell=100)
    })
    positions = engine._get_ptrade_positions({"920017.BJ": 11.0})
    assert list(positions) == ["920017.BJ"]
    assert positions["920017.BJ"].sid == "920017.BJ"
    # Portfolio mappings intentionally preserve exact PTrade membership semantics.
    assert "920017.XBJ" not in positions
    assert PtradeAPI._lookup_position(positions, "920017.XBJ").sid == "920017.BJ"


def test_reference_provider_routes_bse_market_through_authority():
    provider = object.__new__(DuckDBReferenceDataProvider)
    provider._data = Mock()
    provider._data.query_market_detail.return_value = pd.DataFrame({
        "code": ["600000", "000001", "920017", "400001"]
    })
    result = provider.get_market_detail("XBJ")
    assert result["prod_code"].tolist() == ["920017"]


def test_ptrade_market_list_exposes_bse():
    api = PtradeAPI()
    assert "BJ" in api.get_market_list()["finance_mic"].tolist()

