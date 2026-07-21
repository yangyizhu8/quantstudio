# 策略开发契约

QuantStudio 将策略逻辑、平台 API、数据访问和撮合执行分成可替换层：

```text
PTrade 策略文件
  initialize / before_trading_start / handle_data / after_trading_end
        │
        ▼
StrategyRunner + StrategyIsolationGuard
        │
        ▼
PtradeAPI（数据、指标、交易、代码后缀兼容）
        │
        ├── DataProviderRegistry ── DuckDB / 其它 provider
        └── BacktestEngine ─────── 撮合 / T+1 / 涨跌停 / 成本 / 净值
```

## 策略作者只负责

- 初始化参数和全局变量；
- 盘前选股；
- 盘中择时、目标仓位和下单；
- 可选盘后记录。

数据读取、指标计算和下单全部使用 `ptrade_import` 注入 API。策略禁止直接访问 DuckDB、pipeline、provider、CSV、Parquet 或 SQL；`StrategyIsolationGuard` 在加载时执行静态门禁。

## 统一运行入口

```python
from quantstudio.backtest.strategy_runner import StrategyRunner

runner = StrategyRunner()
engine, payload = runner.run(
    "my_strategy.py",
    "2026-01-01",
    "2026-06-30",
    match_price_mode="close",
)
result, output_dir = payload
```

## 撮合口径

- `close`：PTrade 日线兼容默认，用于平台导出对照；
- `open`：按当日开盘成交，用于口径诊断；
- `next_open`：信号后下一交易日开盘成交，用于更严格的反未来函数研究。

GUI、CLI 和 `StrategyRunner` 都将该参数传入同一个 `BacktestEngine`。

## 可替换数据层

替换数据源时，实现 `MarketDataProvider`、`FundamentalDataProvider`、`ReferenceDataProvider`、`CalendarProvider` 并组装 `DataProviderRegistry`。策略文件无需修改。

## 修复原则

- 字段、单位、复权、PIT、状态：修复 adapter/aligner；
- 查询语义：修复 provider/PtradeAPI；
- 成交、费用、订单状态：修复 BacktestEngine；
- 不在具体策略中加入数据源判断或数据库补丁。
