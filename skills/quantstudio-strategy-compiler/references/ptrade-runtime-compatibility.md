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
