from pathlib import Path

import duckdb

from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig
from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
from quantstudio.backtest.strategy_runner import StrategyRunner


def _make_valuation_db(path: Path):
    with duckdb.connect(str(path)) as con:
        con.execute("""
            CREATE TABLE stock_daily_valuation (
                code VARCHAR, time BIGINT, circ_mv DOUBLE, total_mv DOUBLE,
                free_share DOUBLE, pe_ttm DOUBLE, pb DOUBLE, turnover_rate DOUBLE
            );
            CREATE TABLE stock_float_share (
                code VARCHAR, end_date BIGINT, ann_date BIGINT,
                free_share DOUBLE, total_share DOUBLE
            );
            CREATE TABLE stock_daily (
                code VARCHAR, time BIGINT, close DOUBLE, psTTM DOUBLE
            );
        """)
        con.executemany("INSERT INTO stock_daily_valuation VALUES (?,?,?,?,?,?,?,?)", [
            ("000001", 1000, 10e8, 25e8, 1e8, 10.0, 1.0, 2.0),
            ("000001", 3000, 12e8, 40e8, 1e8, 11.0, 1.1, 2.1),
            ("000002", 2000, 15e8, 28e8, 1e8, 12.0, 1.2, 2.2),
        ])
        con.executemany("INSERT INTO stock_float_share VALUES (?,?,?,?,?)", [
            ("000001", 900, 900, 1e8, 2e8),
            ("000002", 1900, 1900, 1e8, 2e8),
        ])
        con.executemany("INSERT INTO stock_daily VALUES (?,?,?,?)", [
            ("000001", 1000, 10.0, 3.0),
            ("000002", 2000, 15.0, 4.0),
        ])


def test_valuation_orm_uses_billion_yuan_and_per_code_pit(tmp_path):
    db = tmp_path / "valuation.duckdb"
    _make_valuation_db(db)
    data = DuckDBDataAccess(db)

    df = data.query_valuation_orm(2500).set_index("code")

    assert df.loc["000001", "market_cap"] == 25.0
    assert df.loc["000001", "float_value"] == 10e8
    assert df.loc["000002", "market_cap"] == 28.0
    assert set(df.index) == {"000001", "000002"}


def test_run_daily_executes_even_when_handle_data_exists(tmp_path):
    calls = []

    def initialize(context):
        from quantstudio.backtest.ptrade_api import _api
        _api.run_daily(context, lambda ctx: calls.append(("daily", ctx.current_dt.date())))

    def handle_data(context, data):
        calls.append(("handle", context.current_dt.date()))

    class _Engine(BacktestEngine):
        pass

    cfg = EngineConfig.default()
    runner = StrategyRunner(config=cfg, engine_factory=_Engine)
    # 只验证单日生命周期；真实 provider 由现有 Canonical 库提供。
    runner.run({"initialize": initialize, "handle_data": handle_data},
               "2026-07-17", "2026-07-17")

    assert [kind for kind, _ in calls] == ["handle", "daily"]


def test_portfolio_position_suffixes_match_ptrade_exact_container_semantics():
    """?? PTrade ??/CSV ?? .SS/.SZ??? dict membership ?????

    get_position()/?? CodeDict ???? .XSHG/.XSHE ???????
    context.portfolio.positions ?? alias-aware ???????????????
    ETF??????????????? PTrade ????????
    """
    from quantstudio.backtest.backtest_engine import Account, Position as EnginePosition
    from quantstudio.backtest.ptrade_api import Portfolio

    engine = object.__new__(BacktestEngine)
    engine.account = Account(cash=1000.0, positions={
        "002830.SZ": EnginePosition("002830.SZ", volume=100, avg_cost=10.0, can_sell=100)
    })
    positions = engine._get_ptrade_positions({"002830.SZ": 11.0})
    portfolio = Portfolio(1000.0, positions)
    assert list(portfolio.positions) == ["002830.SZ"]
    assert "002830.SZ" in portfolio.positions
    assert "002830.XSHE" not in portfolio.positions
    assert portfolio.positions["002830.SZ"].sid == "002830.SZ"


def test_etf_momentum_keeps_ptrade_exact_membership_regression():
    """Guard the control-flow semantic that previously caused -12% -> -51% drift."""
    from quantstudio.backtest.backtest_engine import Account, Position as EnginePosition
    from quantstudio.backtest.ptrade_api import Portfolio

    engine = object.__new__(BacktestEngine)
    engine.account = Account(cash=50.0, positions={
        "159870.SZ": EnginePosition("159870.SZ", volume=100, avg_cost=0.86, can_sell=100)
    })
    portfolio = Portfolio(50.0, engine._get_ptrade_positions({"159870.SZ": 0.85}))

    # ETF momentum stores the selected code as .XSHE, while real PTrade portfolio
    # keys are .SZ. Exact membership must remain False; alias-aware conversion here
    # changes the strategy from hold to active rotation and invalidates fidelity.
    assert "159870.XSHE" not in portfolio.positions
    assert "159870.SZ" in portfolio.positions
