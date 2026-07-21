from pathlib import Path
import pytest
from quantstudio.backtest.strategy_runner import StrategySpec, StrategyRunner
from quantstudio.backtest.ptrade_api import _api, g

def test_strategy_spec_requires_initialize():
    with pytest.raises(ValueError, match="initialize"):
        StrategySpec({"handle_data": lambda context, data: None}).validate()

def test_strategy_spec_rejects_unknown_lifecycle():
    with pytest.raises(ValueError, match="on_tick"):
        StrategySpec({"initialize": lambda context: None, "on_tick": lambda: None}).validate()

def test_runner_loads_existing_strategy():
    path = Path(__file__).parents[1] / "quantstudio" / "backtest" / "strategies" / "双均线策略.py"
    assert callable(StrategyRunner.load(path).functions["initialize"])

def test_api_session_reset_clears_strategy_and_provider_state():
    g.custom_value = 1
    _api._daily_tasks = [(lambda: None, "9:31")]
    _api._market = object()
    _api.reset_session()
    assert not hasattr(g, "custom_value")
    assert _api._daily_tasks == []
    assert _api._market is None


def test_runner_passes_db_path_placeholder_to_real_engine_signature():
    captured = {}

    class FakeEngine:
        def __init__(self, db_path, **kwargs):
            captured["db_path"] = db_path
            captured.update(kwargs)

        def run(self):
            return "ok"

    runner = StrategyRunner(engine_factory=FakeEngine)
    strategy = {"initialize": lambda context: None}
    engine, result = runner.run(strategy, "2026-07-01", "2026-07-02")

    assert captured["db_path"] is None
    assert captured["config"] is runner.config
    assert result == "ok"


def test_strategy_isolation_rejects_database_and_direct_file_access(tmp_path):
    from quantstudio.backtest.strategy_runner import StrategyIsolationError, StrategyRunner
    strategy = tmp_path / "bad_strategy.py"
    strategy.write_text(
        "import duckdb\n"
        "def initialize(context):\n"
        "    open('data.csv')\n",
        encoding="utf-8")
    with pytest.raises(StrategyIsolationError, match="forbidden"):
        StrategyRunner.load(strategy)


def test_strategy_isolation_allows_calculation_libraries(tmp_path):
    from quantstudio.backtest.strategy_runner import StrategyRunner
    strategy = tmp_path / "good_strategy.py"
    strategy.write_text(
        "import numpy as np\n"
        "def initialize(context):\n"
        "    g.answer = int(np.array([42])[0])\n",
        encoding="utf-8")
    assert callable(StrategyRunner.load(strategy).functions["initialize"])
