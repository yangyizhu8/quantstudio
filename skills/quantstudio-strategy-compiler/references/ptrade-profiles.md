# PTrade Profile 契约

> Derived from docs/strategy-compiler/ptrade-profile-contract.md @ 2026-07-26
> 权威源：docs/strategy-compiler/ 下的原始契约文档
> 本文件为 Skill 派生快照，契约变更时必须同步（见 SKILL.md 同步纪律）

## 1. Profile 决定的内容

- Profile ID 和版本；
- 策略代码后缀；
- import/API 注入方式；
- 生命周期与回调签名；
- 公共 API 白名单；
- 行情/财务字段权限；
- 定时任务能力；
- 支持的频率与撮合能力；
- 严格公共 API 模式或本地优化模式。

## 2. 默认 Profile：`ptrade-default-v1`

- 使用 QuantStudio 当前 PTrade 兼容层与目标平台公共 API 子集。
- 生命周期：`initialize`、可选 `before_trading_start`、`handle_data`、`after_trading_end`。
- 策略保持零 import 注入兼容；Renderer 不固定写死 `from ptrade_api import *`。
- 生成代码不得访问 DuckDB、Provider 内部模块或本地文件。
- 未提供券商与版本时，只声明符合默认 Profile，不承诺适配全部 PTrade 部署。
- 分钟回调/精确定时在 Engine/Profile 未验证前标为 BLOCKED 或 PLATFORM_DEPENDENT。

## 3. 可移植性检查

PTrade Renderer 输出必须通过：语法、生命周期、API 白名单、禁止本地扩展 API、禁止文件/数据库访问、字段权限、代码后缀、双版本参数一致性检查。

## 4. Profile 演进

不同券商差异通过显式 Profile ID/版本表达，不使用隐藏条件。任何 API 白名单、回调、字段权限或撮合含义变化必须提升 `ptrade_profile_version`。

## 5. Runtime shape and scheduling corrective (0.3.2)

- Historical values may be pandas objects or NumPy structured arrays.
- Daily multi-stock ranking uses previous-close history in `before_trading_start`, not `data[code]`.
- Unsupported/empty securities are skipped with logs.
- `run_daily` performs one rebalance; `handle_data` is empty for daily-factor strategies.
- Open/next-open approximations schedule 09:31 and require broker minute-period backtesting.
- Shanghai output uses `.SS`; history and position matching use bare codes.

## 2026-07-26 PTrade Profile 1.7.0 stock-core signature closure

- Registered exact signatures and return-shape notes for `set_benchmark`, `run_daily`, `get_Ashares`, `get_index_stocks`, `get_stock_status`, `get_positions`, `get_position`, `get_trade_days`, and `get_fundamentals`.
- Dual/PTrade validation is fail-closed: every `components.required_apis` entry and every external top-level source call must be profiled. Missing entries are `MISSING_REUSABLE_API` at R1 and `BLOCK` at R4; they cannot be waived as execution approximations.
- Portable `get_stock_status` accepts `ST`, `HALT`, or `DELISTING`. `DELISTING_SORTING` is a `filter_stock_by_status` filter type and a local backward-compatible alias only.
- Static Profile PASS proves conformance to the registered default subset, not successful execution on every broker/IQEngine deployment.

## 2026-07-26 Agent-first execution-price contract

- The selected QuantStudio backtest engine profile uses `pre_adjusted_price` for matching, fills, cash, valuation, `data[code].price`, and BarData OHLC.
- Agent-first designs must declare `signal_price_adjustment=pre` and `execution_price_basis=pre_adjusted_price`; `raw_trade_price` is rejected.
- PTrade public-API validation remains a portability gate and does not redefine the broker runtime's internal valuation basis.
