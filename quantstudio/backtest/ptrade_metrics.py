from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List
import json

import numpy as np
import pandas as pd


RISK_FREE_RATE = 0.03
TRADING_DAYS_PER_YEAR = 250


@dataclass
class PtradeMetricsResult:
    summary: Dict[str, float | int | str]
    round_trips: List[Dict]


def _safe_float(value, default=0.0) -> float:
    try:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return default
        return float(value)
    except Exception:
        return default


def _compute_sortino_ratio(strategy_returns: pd.Series, benchmark_returns: pd.Series, annual_return: float) -> float:
    n = len(strategy_returns)
    if n < 2:
        return 0.0
    downside_diff_squared = []
    for sr, br in zip(strategy_returns, benchmark_returns):
        sr = _safe_float(sr)
        br = _safe_float(br)
        if sr < br:
            diff = sr - br
            downside_diff_squared.append(diff * diff)
        else:
            downside_diff_squared.append(0.0)
    downside_risk = np.sqrt((TRADING_DAYS_PER_YEAR / n) * np.sum(downside_diff_squared))
    if downside_risk == 0 or pd.isna(downside_risk):
        return float('inf') if annual_return > RISK_FREE_RATE else 0.0
    sortino = (annual_return - RISK_FREE_RATE) / downside_risk
    if np.isnan(sortino) or np.isinf(sortino):
        return 0.0
    return float(sortino)


def _pair_round_trips(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty:
        return pd.DataFrame(columns=['buy_date', 'sell_date', 'code', 'pnl', 'hold_days'])
    trades_df = trades_df.sort_values('datetime').reset_index(drop=True)
    pairs = []
    buy_queue: list[dict] = []
    for _, row in trades_df.iterrows():
        action = str(row.get('action', '')).lower()
        if action == 'buy':
            buy_queue.append(row.to_dict())
        elif action == 'sell':
            sell_dt = pd.to_datetime(row.get('datetime'))
            sell_code = row.get('code', '')
            pnl = _safe_float(row.get('pnl'))
            # 当前双均线策略无部分成交；这里按同代码 FIFO 配对，作为通用兜底。
            buy_idx = next((i for i, item in enumerate(buy_queue) if item.get('code') == sell_code), None)
            if buy_idx is None:
                buy_row = buy_queue.pop(0) if buy_queue else None
            else:
                buy_row = buy_queue.pop(buy_idx)
            if buy_row is None:
                continue
            buy_dt = pd.to_datetime(buy_row.get('datetime'))
            pairs.append({
                'buy_date': buy_dt.strftime('%Y-%m-%d'),
                'sell_date': sell_dt.strftime('%Y-%m-%d'),
                'code': sell_code,
                'pnl': pnl,
                'hold_days': int((sell_dt - buy_dt).days),
            })
    return pd.DataFrame(pairs)


def calculate_ptrade_like_metrics(result, engine) -> PtradeMetricsResult:
    """Calculate metrics with PTrade-compatible capital and benchmark baselines."""
    nav_df = pd.DataFrame(result.nav_history)
    if nav_df.empty:
        return PtradeMetricsResult(summary={}, round_trips=[])

    nav_df['date'] = pd.to_datetime(nav_df['date'])
    nav_df = nav_df.sort_values('date').reset_index(drop=True)
    nav_df = nav_df.rename(columns={'nav': 'total_asset'})

    initial_capital = float(getattr(engine, '_initial_capital', 0.0) or nav_df['total_asset'].iloc[0])
    nav_df['strategy_nav'] = nav_df['total_asset'] / initial_capital
    if 'benchmark' in nav_df.columns:
        # Engine benchmark values are already normalized to the previous trading-day close.
        nav_df['benchmark_nav'] = pd.to_numeric(nav_df['benchmark'], errors='coerce') / 100.0
    else:
        nav_df['benchmark_nav'] = 1.0

    # Keep the first replay day's return instead of normalizing it away.
    nav_df['strategy_return'] = nav_df['strategy_nav'].pct_change()
    nav_df.loc[0, 'strategy_return'] = nav_df.loc[0, 'strategy_nav'] - 1.0
    nav_df['benchmark_return'] = nav_df['benchmark_nav'].pct_change()
    nav_df.loc[0, 'benchmark_return'] = nav_df.loc[0, 'benchmark_nav'] - 1.0

    # PTrade displays benchmark return from the previous trading-day close, but its
    # relative metrics (excess/alpha/beta/information) use the replay-period benchmark
    # curve normalized at the first replay day. Preserve both baselines explicitly.
    nav_df['relative_benchmark_nav'] = nav_df['benchmark_nav'] / nav_df['benchmark_nav'].iloc[0]
    nav_df['relative_benchmark_return'] = nav_df['relative_benchmark_nav'].pct_change().fillna(0.0)
    merged = nav_df[['date', 'strategy_nav', 'benchmark_nav', 'relative_benchmark_nav',
                     'strategy_return', 'benchmark_return', 'relative_benchmark_return']].copy()

    trade_days = len(merged)
    strategy_total_return = float(merged['strategy_nav'].iloc[-1] - 1)
    benchmark_total_return = float(merged['benchmark_nav'].iloc[-1] - 1)
    relative_benchmark_total_return = float(merged['relative_benchmark_nav'].iloc[-1] - 1)
    excess_total_return = strategy_total_return - relative_benchmark_total_return
    strategy_annual_return = (pow(1 + strategy_total_return, TRADING_DAYS_PER_YEAR / trade_days) - 1) if trade_days > 0 else 0.0
    benchmark_annual_return = (pow(1 + benchmark_total_return, TRADING_DAYS_PER_YEAR / trade_days) - 1) if trade_days > 0 else 0.0
    relative_benchmark_annual_return = (pow(1 + relative_benchmark_total_return, TRADING_DAYS_PER_YEAR / trade_days) - 1) if trade_days > 0 else 0.0
    annual_excess_return = (pow(1 + excess_total_return, TRADING_DAYS_PER_YEAR / trade_days) - 1) if trade_days > 0 and (1 + excess_total_return) > 0 else np.nan

    drawdown = merged['strategy_nav'] / merged['strategy_nav'].cummax() - 1
    max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0

    full_strategy_returns = merged['strategy_return'].reset_index(drop=True)
    full_benchmark_returns = merged['benchmark_return'].reset_index(drop=True)
    strategy_returns = merged['strategy_nav'].pct_change().iloc[1:].reset_index(drop=True)
    benchmark_returns = merged['relative_benchmark_return'].iloc[1:].reset_index(drop=True)
    excess_returns = strategy_returns - benchmark_returns

    # Match the PTrade desktop report: compounded annual return / annualized volatility,
    # with a 3% risk-free rate and the calendar business-day span as volatility divisor.
    business_days = max(1, len(pd.bdate_range(merged['date'].iloc[0], merged['date'].iloc[-1])))
    centered = full_strategy_returns - full_strategy_returns.mean()
    volatility = float(np.sqrt((TRADING_DAYS_PER_YEAR / business_days) * np.sum(centered ** 2)))
    sharpe = ((strategy_annual_return - RISK_FREE_RATE) / volatility
              if volatility > 0 and pd.notna(volatility) else 0.0)

    downside = np.minimum(full_strategy_returns.to_numpy(dtype=float), 0.0)
    downside_risk = float(np.sqrt((TRADING_DAYS_PER_YEAR / len(full_strategy_returns)) *
                                  np.sum(downside ** 2)))
    sortino = ((strategy_annual_return - RISK_FREE_RATE) / downside_risk
               if downside_risk > 0 and pd.notna(downside_risk) else 0.0)

    beta = 0.0
    if len(strategy_returns) > 1:
        benchmark_variance = np.var(benchmark_returns)
        if benchmark_variance != 0:
            beta = float(np.cov(strategy_returns, benchmark_returns)[0, 1] / benchmark_variance)
    alpha_ratio = strategy_annual_return - (
        RISK_FREE_RATE + beta * (relative_benchmark_annual_return - RISK_FREE_RATE))

    information_ratio = 0.0
    if len(excess_returns) > 1 and excess_returns.std(ddof=0) > 0:
        information_ratio = float(excess_returns.mean() / excess_returns.std(ddof=0) *
                                  np.sqrt(TRADING_DAYS_PER_YEAR))
    daily_win_rate = float((full_strategy_returns > full_benchmark_returns).mean()) if trade_days else 0.0

    trades_df = pd.DataFrame(result.trade_records)
    if not trades_df.empty:
        if 'date' in trades_df.columns:
            trades_df = trades_df.rename(columns={'date': 'datetime'})
        trades_df['datetime'] = pd.to_datetime(trades_df['datetime'])
        sells = trades_df[trades_df['action'].astype(str).str.lower() == 'sell'].copy()
    else:
        sells = pd.DataFrame(columns=['pnl'])

    winning_trades = sells[sells['pnl'] > 0]
    losing_trades = sells[sells['pnl'] < 0]
    win_rate = float(len(winning_trades) / len(sells)) if len(sells) else 0.0
    total_profit = float(winning_trades['pnl'].sum()) if len(winning_trades) else 0.0
    total_loss = float(abs(losing_trades['pnl'].sum())) if len(losing_trades) else 0.0
    profit_loss_ratio = float(total_profit / total_loss) if total_loss > 0 else 0.0

    empty_trades = pd.DataFrame(columns=['datetime', 'action', 'code', 'pnl'])
    round_trips_df = _pair_round_trips(trades_df if not trades_df.empty else empty_trades)
    avg_hold_days = float(round_trips_df['hold_days'].mean()) if not round_trips_df.empty else 0.0

    summary = {
        'strategy_name': getattr(engine, '_strategy_name', 'strategy'),
        'start_date': merged['date'].iloc[0].strftime('%Y-%m-%d'),
        'end_date': merged['date'].iloc[-1].strftime('%Y-%m-%d'),
        'trade_days': int(trade_days),
        'benchmark_code': getattr(getattr(engine, '_ptrade_api', None), '_benchmark', None) or '000300',
        'strategy_return_pct': strategy_total_return * 100,
        'max_drawdown_pct': abs(max_drawdown) * 100,
        'win_rate_pct': win_rate * 100,
        'benchmark_return_pct': benchmark_total_return * 100,
        'annual_return_pct': strategy_annual_return * 100,
        'profit_loss_ratio_pct': profit_loss_ratio * 100,
        'alpha_ratio': float(alpha_ratio),
        'benchmark_annual_return_pct': benchmark_annual_return * 100,
        'profit_count': int(len(winning_trades)),
        'beta_ratio': float(beta),
        'excess_return_pct': excess_total_return * 100,
        'loss_count': int(len(losing_trades)),
        'sharpe_ratio': float(sharpe),
        'annual_excess_return_pct': float(annual_excess_return * 100) if pd.notna(annual_excess_return) else None,
        'information_ratio': float(information_ratio),
        'sortino_ratio': float(sortino),
        'daily_win_rate_pct': daily_win_rate * 100,
        'avg_hold_days': avg_hold_days,
    }
    return PtradeMetricsResult(summary=summary, round_trips=round_trips_df.to_dict(orient='records'))

def export_ptrade_like_metrics(result, engine, output_dir: Path):
    if getattr(result, 'metrics_summary', None):
        metrics = PtradeMetricsResult(
            summary=result.metrics_summary,
            round_trips=getattr(result, 'round_trips', []) or [],
        )
    else:
        metrics = calculate_ptrade_like_metrics(result, engine)
        result.metrics_summary = metrics.summary
        result.round_trips = metrics.round_trips
    if not metrics.summary:
        return None
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / 'ptrade_metrics.json'
    csv_path = output_dir / 'ptrade_metrics.csv'
    roundtrip_path = output_dir / 'round_trips.csv'
    json_path.write_text(json.dumps({'summary': metrics.summary, 'round_trips': metrics.round_trips}, ensure_ascii=False, indent=2), encoding='utf-8')
    pd.DataFrame([metrics.summary]).to_csv(csv_path, index=False, encoding='utf-8-sig')
    pd.DataFrame(metrics.round_trips).to_csv(roundtrip_path, index=False, encoding='utf-8-sig')
    return json_path
