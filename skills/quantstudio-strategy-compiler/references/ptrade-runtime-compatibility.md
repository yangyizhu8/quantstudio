# PTrade Runtime Compatibility Contract

> Runtime corrective snapshot, 2026-07-24.

## Broker differences covered

1. `get_history(..., is_dict=True)` may return a mapping whose per-security value is a pandas DataFrame/Series or a NumPy structured array. Extract a field first, then use `.values` only when the extracted object actually exposes it.
2. A symbol can be accepted by historical APIs but absent from `BarDict`, or have empty history. Generated daily ranking code must not require `data[code]`; skip unsupported/empty symbols with a log message.
3. Broker daily-period backtests may invoke `handle_data` or scheduled callbacks at 15:00. For open/next-open approximations, register `run_daily(context, rebalance, time='9:31')` and require minute-period backtesting.
4. `handle_data` can run every minute. When factors are daily, compute them once in `before_trading_start`, trade once through `run_daily`, and leave `handle_data` empty.
5. Normalize output codes per platform (`.SH` QuantStudio, `.SS` PTrade) and compare history/positions by bare six-digit code to prevent alias-key misses.
6. Use `order_target_value` for target weights. Sell non-selected holdings, then target each selected security to total portfolio value divided by `max_positions`.

## Fail-soft policy

- A single unsupported security must not terminate the strategy.
- Insufficient history, invalid prices, unsupported BarDict keys, status-filter failures, and order rejection must be logged with the security code.
- Empty candidate sets are valid and result in no buys. Existing non-selected positions are still submitted for exit.

## Validation requirements

Generated PTrade code must pass:
- AST syntax and public-API portability checks;
- a structured-array history fixture;
- an empty/unsupported BSE-symbol fixture;
- a BarDict fixture that raises on any direct access for daily ranking strategies;
- a scheduler assertion for the declared rebalance time;
- suffix and target-weight assertions.

## Public signature, initialization-safety, and data-source corrective (1.2.0)

- `set_slippage` accepts `slippage`, not the local alias `slippage_ratio`.
- `set_fixed_slippage` accepts `fixedslippage`.
- PTrade backtest code must not call trading-context-only `get_snapshot` or `check_limit`. `get_open_orders(security=None)` is available in backtest and trade contexts.
- Use `get_stock_info(..., field=['listed_date'])`; local `get_security_info` is not portable.
- Do not assume QuantStudio-injected MyTT names exist on PTrade. Define portable NumPy/pandas helpers in source.
- Real PTrade may invoke later lifecycle callbacks after `initialize` raises. Every callback therefore calls an idempotent `_ensure_runtime_state()` first.
- Local and PTrade outputs are validated separately, then compared after generation. Local PASS is never accepted as PTrade evidence.

## Local backtest data-source contract

- Strategy code remains storage-isolated and calls only injected APIs.
- The local engine/provider layer prefers `<current-project>/data/quantstudio.db` when it exists.
- Configured or external DuckDB paths are fallbacks unless the customer explicitly approves an override.
- R1 and R5 record the resolved absolute database path and provider provenance.
- Dual publication validates the physical staging files after generation; comparing the canonical input with itself is not consistency evidence.

## Profile correction 1.2.1

- Public PTrade documentation marks `get_open_orders(security=None)` as available in backtest and trade. Profile 1.2.0 incorrectly classified it as trading-only; the validator and catalog now allow it.
- `get_history` canonical keyword validation now follows the documented count-first signature: `count`, `frequency`, `field`, `security_list`, `fq`, `include`, `fill`, `is_dict`.

## Profile correction 1.2.2

- PTrade documents `filter_stock_by_status` as callable only from `before_trading_start`. Generated scheduled callbacks must use `get_stock_status` instead.
- `DELISTING_SORTING` filtering applies only to current-day data.

## Profile correction 1.5.0 — real IQEngine initialization/logger failure

- `set_backtest()` and `is_trade()` are QuantStudio-local injected extensions, not PTrade backtest public APIs. Dual/PTrade validation blocks both calls.
- A local guard such as `if not is_trade(): set_backtest()` is not portable because the guard itself is also absent on the real platform. PTrade output must omit the local-only switch.
- The verified portable logger methods are `log.debug`, `log.info`, `log.warning`, `log.error`, and `log.critical`. `log.warn` is blocked because real IQEngine `LogEngine` does not expose that alias.
- Runtime evidence was supplied on 2026-07-25 with a platform timestamp of 2026-07-26; the future timestamp is retained as evidence metadata but does not change the contract diagnosis.

## Profile correction 1.6.0 — explicit calculation-module imports

- Real IQEngine runtime evidence supplied on 2026-07-25 contains 79 `NameError: name 'np' is not defined` warnings. QuantStudio injects `np`/`pd`; PTrade does not.
- Dual/PTrade source that uses NumPy or pandas must explicitly include `import numpy as np` and/or `import pandas as pd`. Ordinary calculation-library imports are allowed; database drivers, QuantStudio internals, and direct file I/O remain forbidden.
- PTrade validation blocks every used `np`, `pd`, `numpy`, or `pandas` name that is not bound to the verified module import. This prevents fail-soft code from silently converting a runtime dependency failure into an empty portfolio.

## Profile correction 1.7.0 — registered stock-core APIs and fail-closed validation

- Registered exact public profile entries for `set_benchmark`, `run_daily`, `get_Ashares`, `get_index_stocks`, `get_stock_status`, `get_positions`, `get_position`, `get_trade_days`, and `get_fundamentals`.
- Dual/PTrade design validation now BLOCKS every `components.required_apis` item missing from the signature profile. Source validation also BLOCKS unprofiled external top-level calls while allowing Python builtins, imports, local helpers, and profiled APIs.
- `get_stock_status` portable values are `ST`, `HALT`, and `DELISTING`. `DELISTING_SORTING` remains a `filter_stock_by_status` filter type and local backward-compatible alias only.
- QuantStudio's `get_stock_status` adapter now implements public `query_type='DELISTING'` with the same `is_delisting_risk` result as the legacy local alias.
- Static profile PASS remains portability evidence only; real broker/IQEngine runtime evidence is still required before claiming deployment acceptance.

## Skill correction 0.5.4 — pre-adjusted execution contract

- QuantStudio's implemented engine profile uses the front-adjusted daily/minute snapshot for matching, fills, cash, valuation, `data[code].price`, and BarData OHLC.
- Agent-first designs now require `execution_price_basis=pre_adjusted_price`; raw execution declarations are blocked as incompatible with the selected local engine profile.
- This is a design/profile contract correction. It does not claim that a broker PTrade runtime internally values positions with QuantStudio's adjusted snapshot; dual validation proves source/API portability while local backtest evidence records the QuantStudio execution basis.
