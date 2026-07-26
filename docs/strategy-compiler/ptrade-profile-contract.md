# PTrade Profile 契约

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
- PTrade 公共 API 仍由平台提供，但计算模块不视为注入对象；源码使用 NumPy/pandas 时必须显式 import。Renderer 不写死 `from ptrade_api import *`，且禁止数据库/框架内部 import。
- 生成代码不得访问 DuckDB、Provider 内部模块或本地文件。
- 未提供券商与版本时，只声明符合默认 Profile，不承诺适配全部 PTrade 部署。
- 分钟回调/精确定时在 Engine/Profile 未验证前标为 BLOCKED 或 PLATFORM_DEPENDENT。

## 3. 可移植性检查

PTrade Renderer 输出必须通过：语法、生命周期、API 白名单、禁止本地扩展 API、禁止文件/数据库访问、字段权限、代码后缀、双版本参数一致性检查。

## 4. Profile 演进

不同券商差异通过显式 Profile ID/版本表达，不使用隐藏条件。任何 API 白名单、回调、字段权限或撮合含义变化必须提升 `ptrade_profile_version`。

## 2026-07-25 ETF universe capability split

- `get_etf_list()` is trading-context only in the strict PTrade profile and is blocked in PTrade backtest source.
- `get_etf_list_local()` and `get_history_batch()` are registered QuantStudio-only APIs and are blocked whenever `targets` contains `ptrade`.
- Dual ETF backtests use a customer-confirmed static whitelist. Local-only ETF backtests may use the PIT local universe API.

## Candidate boundary

A QuantStudio `__candidate` file is never a PTrade artifact. PTrade formal output is generated only in R6 after hash-bound R5 PASS and is revalidated against the public profile.

## 2026-07-26 PTrade Profile 1.7.0 stock-core signature closure

- Registered exact signatures and return-shape notes for `set_benchmark`, `run_daily`, `get_Ashares`, `get_index_stocks`, `get_stock_status`, `get_positions`, `get_position`, `get_trade_days`, and `get_fundamentals`.
- Dual/PTrade validation is fail-closed: every `components.required_apis` entry and every external top-level source call must be profiled. Missing entries are `MISSING_REUSABLE_API` at R1 and `BLOCK` at R4; they cannot be waived as execution approximations.
- Portable `get_stock_status` accepts `ST`, `HALT`, or `DELISTING`. `DELISTING_SORTING` is a `filter_stock_by_status` filter type and a local backward-compatible alias only.
- Static Profile PASS proves conformance to the registered default subset, not successful execution on every broker/IQEngine deployment.

## 2026-07-26 Agent-first execution-price contract

- The selected QuantStudio backtest engine profile uses `pre_adjusted_price` for matching, fills, cash, valuation, `data[code].price`, and BarData OHLC.
- Agent-first designs must declare `signal_price_adjustment=pre` and `execution_price_basis=pre_adjusted_price`; `raw_trade_price` is rejected.
- PTrade public-API validation remains a portability gate and does not redefine the broker runtime's internal valuation basis.
