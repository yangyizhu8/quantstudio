"""统一的策略装配与运行入口。"""
from __future__ import annotations

import ast
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Union

from . import ptrade_import
from .backtest_engine import BacktestEngine, DEFAULT_TRADE_COST, EngineConfig
from .ptrade_api import _api
from .providers.base import DataProviderRegistry


class StrategyIsolationError(ValueError):
    """Raised when strategy code reaches through the public API boundary."""


class StrategyIsolationGuard:
    """Static guard that keeps generated strategies independent of storage.

    Strategy files may import ordinary calculation libraries, but cannot import
    QuantStudio internals, database drivers or open local data files directly.
    This makes provider/adapter fixes transparent to strategy code.
    """
    FORBIDDEN_IMPORT_PREFIXES = (
        "duckdb", "sqlite3", "sqlalchemy", "psycopg2", "pymysql",
        "quantstudio.pipeline", "quantstudio.backtest.providers",
        "quantstudio._paths",
    )
    FORBIDDEN_CALLS = {"open", "read_csv", "read_parquet", "read_sql", "read_pickle"}

    @classmethod
    def validate_path(cls, strategy_path: Union[str, Path]) -> None:
        path = Path(strategy_path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except SyntaxError as exc:
            raise StrategyIsolationError(f"strategy syntax error: {exc}") from exc
        errors = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                names = []
            for name in names:
                if any(name == prefix or name.startswith(prefix + ".")
                       for prefix in cls.FORBIDDEN_IMPORT_PREFIXES):
                    errors.append(f"line {node.lineno}: forbidden import {name}")
            if isinstance(node, ast.Call):
                func = node.func
                call_name = (func.id if isinstance(func, ast.Name)
                             else func.attr if isinstance(func, ast.Attribute) else "")
                if call_name in cls.FORBIDDEN_CALLS:
                    errors.append(f"line {node.lineno}: forbidden direct I/O {call_name}()")
        if errors:
            raise StrategyIsolationError(
                "strategy must use injected PTrade/QuantStudio APIs only; " + "; ".join(errors))


@dataclass(frozen=True)
class StrategySpec:
    functions: Dict[str, Callable]
    module: object = None
    REQUIRED = ("initialize",)
    OPTIONAL = ("before_trading_start", "handle_data", "after_trading_end", "set_backtest")

    def validate(self) -> "StrategySpec":
        missing = [name for name in self.REQUIRED if not callable(self.functions.get(name))]
        if missing:
            raise ValueError(f"策略缺少必需生命周期函数: {', '.join(missing)}")
        unknown = sorted(set(self.functions) - set(self.REQUIRED) - set(self.OPTIONAL))
        if unknown:
            raise ValueError(f"策略包含未支持生命周期函数: {', '.join(unknown)}")
        return self


def load_strategy(strategy_path: Union[str, Path]):
    StrategyIsolationGuard.validate_path(strategy_path)
    spec = importlib.util.spec_from_file_location("ptrade_strategy", str(strategy_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载策略文件: {strategy_path}")
    module = importlib.util.module_from_spec(spec)
    module.__dict__.update({name: value for name, value in vars(ptrade_import).items()
                            if not name.startswith("_")})
    spec.loader.exec_module(module)
    names = StrategySpec.REQUIRED + StrategySpec.OPTIONAL
    return {name: getattr(module, name) for name in names if hasattr(module, name)}, module


class StrategyRunner:
    def __init__(self, config: Optional[EngineConfig] = None,
                 providers: Optional[DataProviderRegistry] = None,
                 engine_factory=BacktestEngine):
        self.config = config or EngineConfig.default()
        self.providers = providers
        self.engine_factory = engine_factory

    @staticmethod
    def load(strategy_path: Union[str, Path]) -> StrategySpec:
        functions, module = load_strategy(strategy_path)
        return StrategySpec(functions, module).validate()

    def run(self, strategy: Union[StrategySpec, Dict[str, Callable], str, Path],
            start: str, end: str, capital: float = 100_000,
            match_price_mode: str = "close", cost=None, progress_callback=None,
            engine_profile: str = "daily-bar-v1", etf_t0: bool = False):
        if isinstance(strategy, (str, Path)):
            strategy = self.load(strategy)
        elif isinstance(strategy, dict):
            strategy = StrategySpec(strategy).validate()
        else:
            strategy.validate()
        _api.reset_session()
        registry = self.providers or DataProviderRegistry.from_duckdb(self.config.db_path)
        engine = self.engine_factory(db_path=None, config=self.config, providers=registry,
            strategy=strategy.functions, start=start, end=end, capital=capital,
            cost=cost or DEFAULT_TRADE_COST, strategy_type="ptrade",
            match_price_mode=match_price_mode, progress_callback=progress_callback,
            engine_profile=engine_profile, etf_t0=etf_t0)
        return engine, engine.run()
