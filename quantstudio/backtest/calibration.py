from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest_engine import BacktestEngine, DEFAULT_TRADE_COST, EngineConfig
from .providers.ptrade_export_provider import PtradeExportProvider


@dataclass
class CalibrationReport:
    ptrade_total_return: float = 0.0
    local_total_return: float = 0.0
    total_return_diff_bps: float = 0.0
    daily_nav_correlation: float = 0.0
    l1_engine_bps: float = 0.0
    l2_data_bps: float = 0.0
    l3_source_bps: float = 0.0
    trade_match_rate: float = 0.0
    position_overlap_rate: float = 0.0

    def summary(self) -> str:
        return (
            f"校准报告：Ptrade收益 {self.ptrade_total_return:.2%}，"
            f"本地收益 {self.local_total_return:.2%}，偏差 {self.total_return_diff_bps:.2f} bps\n"
            f"净值相关性 {self.daily_nav_correlation:.4f}，交易匹配率 {self.trade_match_rate:.2%}，"
            f"持仓重叠率 {self.position_overlap_rate:.2%}\n"
            f"L1引擎 {self.l1_engine_bps:.2f} bps / L2数据 {self.l2_data_bps:.2f} bps / "
            f"L3源发散 {self.l3_source_bps:.2f} bps"
        )


class CalibrationRunner:
    def __init__(self, strategy_module, ptrade_provider: PtradeExportProvider):
        self._strategy = strategy_module
        self._ptrade = ptrade_provider

    @staticmethod
    def _strategy_functions(strategy_module):
        if isinstance(strategy_module, dict):
            return strategy_module
        if isinstance(strategy_module, (str, Path)):
            from .run_ptrade_strategy import load_strategy
            return load_strategy(str(strategy_module))[0]
        return {name: getattr(strategy_module, name) for name in
                ('initialize', 'before_trading_start', 'handle_data', 'after_trading_end', 'set_backtest')
                if hasattr(strategy_module, name)}

    @staticmethod
    def _ptrade_nav(trades: pd.DataFrame, positions: pd.DataFrame,
                    initial_capital: float = 100_000.0) -> pd.DataFrame:
        flows = trades.copy()
        signed = np.where(flows['action'].eq('buy'),
                          flows['amount'] + flows['commission'] + flows['tax'],
                          -(flows['amount'] - flows['commission'] - flows['tax']))
        flows['cash_out'] = signed
        daily_flows = flows.groupby('date')['cash_out'].sum()
        daily_values = positions.groupby('date')['market_value'].sum()
        dates = sorted(set(daily_flows.index) | set(daily_values.index))
        cumulative = 0.0
        rows = []
        for date in dates:
            cumulative += float(daily_flows.get(date, 0.0))
            total = initial_capital - cumulative + float(daily_values.get(date, 0.0))
            rows.append({'date': date, 'nav': total / initial_capital})
        return pd.DataFrame(rows)

    @staticmethod
    def _match_rate(local: pd.DataFrame, ptrade: pd.DataFrame) -> float:
        columns = ['date', 'code', 'action', 'volume']
        if local.empty and ptrade.empty:
            return 1.0
        if local.empty or ptrade.empty:
            return 0.0
        left = local.copy()
        left['date'] = pd.to_datetime(left['date']).dt.strftime('%Y-%m-%d')
        left['code'] = left['code'].astype(str).str.split('.').str[0]
        left['action'] = left['action'].astype(str).str.lower()
        left['volume'] = pd.to_numeric(left['volume']).abs().astype(int)
        right = ptrade[columns].copy()
        matches = left[columns].merge(right, on=columns, how='inner')
        return len(matches) / max(len(left), len(right))

    @staticmethod
    def _position_overlap(local_trades: pd.DataFrame, positions: pd.DataFrame) -> float:
        if positions.empty:
            return 1.0
        holdings = set()
        overlaps = []
        local = local_trades.copy()
        if not local.empty:
            local['date'] = pd.to_datetime(local['date']).dt.strftime('%Y-%m-%d')
            local['code'] = local['code'].astype(str).str.split('.').str[0]
        for date in sorted(positions['date'].unique()):
            for _, trade in local[local['date'] == date].iterrows():
                if str(trade['action']).lower() == 'buy': holdings.add(trade['code'])
                elif str(trade['action']).lower() == 'sell': holdings.discard(trade['code'])
            expected = set(positions.loc[positions['date'] == date, 'code'])
            union = holdings | expected
            overlaps.append(len(holdings & expected) / len(union) if union else 1.0)
        return float(np.mean(overlaps)) if overlaps else 1.0

    def run(self, start_date: str, end_date: str) -> CalibrationReport:
        config = EngineConfig.default()
        engine = BacktestEngine(str(config.db_path), self._strategy_functions(self._strategy),
                                start_date, end_date, capital=100_000,
                                cost=DEFAULT_TRADE_COST, config=config)
        local_result, _ = engine.run()
        local_nav = pd.DataFrame(local_result.nav_history)
        local_trades = pd.DataFrame(local_result.trade_records)
        ptrade_nav = self._ptrade_nav(self._ptrade.trades, self._ptrade.positions)

        local_total_return = (local_nav['nav'].iloc[-1] / local_nav['nav'].iloc[0] - 1
                              if len(local_nav) > 1 else 0.0)
        ptrade_total_return = (ptrade_nav['nav'].iloc[-1] / ptrade_nav['nav'].iloc[0] - 1
                               if len(ptrade_nav) > 1 else 0.0)
        total_bps = (local_total_return - ptrade_total_return) * 10_000
        merged = local_nav[['date', 'nav']].merge(ptrade_nav, on='date', suffixes=('_local', '_ptrade'))
        correlation = (float(merged['nav_local'].corr(merged['nav_ptrade']))
                       if len(merged) > 1 else 0.0)
        if np.isnan(correlation): correlation = 0.0
        trade_match = self._match_rate(local_trades, self._ptrade.trades)
        position_overlap = self._position_overlap(local_trades, self._ptrade.positions)

        l1 = total_bps * (1.0 - trade_match)
        remaining = total_bps - l1
        l2 = remaining * (1.0 - position_overlap)
        l3 = total_bps - l1 - l2
        return CalibrationReport(
            ptrade_total_return=ptrade_total_return,
            local_total_return=local_total_return,
            total_return_diff_bps=total_bps,
            daily_nav_correlation=correlation,
            l1_engine_bps=l1,
            l2_data_bps=l2,
            l3_source_bps=l3,
            trade_match_rate=trade_match,
            position_overlap_rate=position_overlap,
        )


__all__ = ['CalibrationReport', 'CalibrationRunner', 'PtradeExportProvider']
