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

## 2026-07-27 PTrade Profile 1.9.0 industry + PIT contract closure

- Registered `get_industry(code)` with the exact local runnable subset: single positional (or `code=` keyword), returning the direct `{'sw_l1': {...}}` dict (NOT security-keyed) or None. PIT contract: formal SW2021 membership (`industry_classification` + `industry_membership`, interval-transformed to daily uniqueness) strictly as-of the current backtest date; no valid membership -> None; legacy `sw_industry` is audit-only. Real-PTrade shape/classification version is `PTRADE_RUNTIME_UNVERIFIED`.
- `get_index_stocks` notes now declare the strict as-of PIT contract (no history union, no future snapshots, empty when no snapshot on/before date, partial snapshots never served as complete).
- `get_stock_info` notes now declare unified stock/ETF metadata (stock_type, ETF list/delist dates, listing-date fallback marking) and explicitly separate local ETF metadata support from unverified PTrade runtime ETF support.
- Static profile PASS 仍只证明对注册子集的静态一致性；PTrade 平台成分历史深度与行业分类版本按部署核实，状态为 `PTRADE_RUNTIME_UNVERIFIED`。

## 2026-07-26 PTrade Profile 1.8.0 get_history return-shape closure

- `get_history(..., is_dict=True)` records an explicit `return_contract`: mapping items may be pandas DataFrame / NumPy structured array / recarray; `item[field]` may be Series or ndarray.
- Portable rule: extracted fields must be normalized with `np.asarray(...)` before numerical use; unguarded pandas-only attribute access on history items is BLOCKED by the agent-first validator, and `scripts/validate_runtime_shapes.py` executes the strategy's helper against the real shape fixtures.
- 静态 Profile PASS 仍只证明对注册子集的静态一致性；真实券商/IQEngine 运行证据由独立状态（`PTRADE_BROKER_RUNTIME_PASS` / `NOT_VERIFIED` / `STALE`）表达，不得混写。

## 2026-07-26 PTrade Profile 1.7.0 stock-core signature closure

- Registered exact signatures and return-shape notes for `set_benchmark`, `run_daily`, `get_Ashares`, `get_index_stocks`, `get_stock_status`, `get_positions`, `get_position`, `get_trade_days`, and `get_fundamentals`.
- Dual/PTrade validation is fail-closed: every `components.required_apis` entry and every external top-level source call must be profiled. Missing entries are `MISSING_REUSABLE_API` at R1 and `BLOCK` at R4; they cannot be waived as execution approximations.
- Portable `get_stock_status` accepts `ST`, `HALT`, or `DELISTING`. `DELISTING_SORTING` is a `filter_stock_by_status` filter type and a local backward-compatible alias only.
- Static Profile PASS proves conformance to the registered default subset, not successful execution on every broker/IQEngine deployment.

## 2026-07-26 Agent-first execution-price contract（2026-08-14 修订：raw 口径）

- The selected QuantStudio backtest engine profile uses `raw_trade_price` for matching, fills, cash, valuation, `data[code].price`, and BarData OHLC — revised 2026-08-14 by real-PTrade match-price audit (daily fill = T-day raw close 5/5; minute fill = bar raw close 6/6; valuation last_price = raw close).
- Agent-first designs must declare `signal_price_adjustment=pre` (front-adjusted signal OHLC via literal `fq='pre'`) and `execution_price_basis=raw_trade_price`; `pre_adjusted_price` is rejected (superseded 2026-08-14).
- PTrade public-API validation remains a portability gate and does not redefine the broker runtime's internal valuation basis.
