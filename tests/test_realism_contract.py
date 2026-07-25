from types import SimpleNamespace
import pandas as pd

def test_proxy_execution_uses_current_snapshot_not_daily_close():
    from quantstudio.backtest.ptrade_api import _api
    class Engine:
        engine_profile = "daily-open-close-proxy-v1"
    _api._engine = Engine()
    _api._current_bar_ts = pd.Timestamp("2025-01-03 09:31", tz="Asia/Shanghai")
    bar = pd.DataFrame([{"code":"600000","open":10.0,"close":10.0}])
    daily = pd.DataFrame([{"code":"600000","open":10.0,"close":11.0}])
    _api._current_day_data = bar
    _api._daily_curr_data = daily
    assert _api._curr_data_for_execute() is bar
    _api._current_bar_ts = None

def test_stamp_tax_historical_rate_is_halved_after_2023():
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(db_path="unused.db", strategy={}, start="2025-01-02", end="2025-01-02")
    assert engine._stamp_tax_rate("2025-01-02") == 0.0005
    assert engine._stamp_tax_rate("2023-08-27") == 0.001

def test_corporate_action_adjusts_cash_and_cost_basis():
    from quantstudio.backtest.backtest_engine import BacktestEngine, Position
    class Ref:
        def get_corporate_actions(self, date):
            return pd.DataFrame([{"code":"600000","cash_div":1.0,"stk_div":0.0,"div_rat":None}])
    engine = BacktestEngine(db_path="unused.db", strategy={}, start="2025-01-02", end="2025-01-02")
    engine._providers = SimpleNamespace(reference=Ref())
    engine.account.positions["600000.SH"] = Position("600000.SH", 100, 10.0, 100)
    engine._apply_corporate_actions("2025-01-02")
    assert engine.account.cash == 100080.0
    assert engine.account.positions["600000.SH"].avg_cost == 9.0

