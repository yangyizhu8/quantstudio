"""BacktestResult → CSV 文件导出（供结果窗口读取）"""
import pandas as pd
from pathlib import Path
from datetime import datetime

from .ptrade_metrics import export_ptrade_like_metrics


def export_result(result, engine, output_dir=None):
    """导出回测结果到 CSV 文件目录

    产出文件:
        config.csv        — 回测参数配置
        trades.csv        — 交易记录
        daily_stats.csv   — 每日净值统计
        benchmark.csv     — 基准数据
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    strategy_name = getattr(engine, '_strategy_name', 'strategy')
    output_dir = Path(output_dir or f"output/backtest_results/{timestamp}_{strategy_name}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # config.csv — 回测参数（字段名适配结果窗口期望的格式）
    config_data = {
        'strategy_file': strategy_name + ".py",
        'strategy': strategy_name,
        'start_time': engine.start,
        'end_time': engine.end,
        'init_capital': engine._initial_capital,
        'commission_rate': engine.cost.commission_rate,
        'min_commission': engine.cost.min_commission,
        'stamp_tax_rate': engine.cost.stamp_tax_rate,
        'transfer_fee_rate': engine.cost.transfer_fee_rate,
        'slippage_rate': engine.cost.slippage_rate,
        'fixed_slippage': getattr(engine.cost, 'fixed_slippage', 0.0),
        'match_price_mode': engine.match_price_mode,
        'min_rebalance_pct': engine.min_rebalance_pct,
    }
    pd.DataFrame([config_data]).to_csv(
        output_dir / "config.csv", index=False, encoding='utf-8-sig')

    # trades.csv — 交易记录（适配结果窗口期望的列名）
    if result.trade_records:
        trades_df = pd.DataFrame(result.trade_records)
        # 列名映射：date→datetime, 计算 amount=price×volume
        if 'date' in trades_df.columns:
            trades_df = trades_df.rename(columns={'date': 'datetime'})
        if 'price' in trades_df.columns and 'volume' in trades_df.columns:
            trades_df['amount'] = trades_df['price'] * trades_df['volume']
        trades_df.to_csv(output_dir / "trades.csv", index=False, encoding='utf-8-sig')

    # daily_stats.csv — 每日净值
    if result.nav_history:
        nav_df = pd.DataFrame(result.nav_history)
        nav_df['daily_return'] = nav_df['nav'].pct_change()
        nav_df['daily_return'] = nav_df['daily_return'].fillna(0)
        # 适配结果窗口期望的列名
        nav_df = nav_df.rename(columns={'nav': 'total_asset'})
        nav_df.to_csv(output_dir / "daily_stats.csv", index=False, encoding='utf-8-sig')

        # benchmark.csv — 基准
        if 'benchmark' in nav_df.columns:
            bench_df = nav_df[['date', 'benchmark']].rename(columns={'benchmark': 'close'})
            bench_df.to_csv(output_dir / "benchmark.csv", index=False, encoding='utf-8-sig')

    # Ptrade 风格指标汇总（便于与平台报表逐项对照）
    export_ptrade_like_metrics(result, engine, output_dir)

    return output_dir
