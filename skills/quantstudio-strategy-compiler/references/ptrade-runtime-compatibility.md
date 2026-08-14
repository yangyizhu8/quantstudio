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
- the agent-first source runtime-shape fixture (`scripts/validate_runtime_shapes.py`) for any history-field extraction helper — the legacy renderer structured-array fixture below applies to legacy Jinja output only;
- a structured-array history fixture (legacy renderer path);
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

## Profile correction 1.8.0 — get_history return-shape contract and agent-first runtime fixture

- `get_history(..., is_dict=True)` now carries an explicit return contract: the mapping item may be `pandas.DataFrame`, `numpy.structured_array` or `numpy.recarray`; `item[field]` may be `pandas.Series` or `numpy.ndarray`.
- Generated code must normalize extracted fields with `np.asarray(item[field], dtype=float)` or a `hasattr(values, 'values')` guarded helper (`_extract_history_field`) before numerical use. Unguarded `.values`/`.iloc`/`.loc`/`.to_numpy()`/`.columns`/`.index`/`.empty` on history items/fields are BLOCKED (`PTRADE-HISTORY-SHAPE-UNSAFE` / `PTRADE-HISTORY-PANDAS-ONLY` / `PTRADE-HISTORY-NORMALIZATION-MISSING`).
- Two fixture layers are now distinct and must not be conflated:
  - **legacy renderer runtime fixture** — covers old Renderer/Jinja output only;
  - **agent-first source runtime-shape fixture** — `scripts/validate_runtime_shapes.py` extracts the strategy's own helper and runs it against DataFrame/Series/structured-array/recarray/None/empty/missing-field/NaN-inf fixtures, requiring DataFrame/recarray result equality and fail-soft empties.
- "structured-array fixture covered" may only be claimed when the agent-first validator actually ran the new fixture.

## Profile correction 1.8.1 — source_import rewrites unguarded `.values` field access

- Real broker failure evidence (2026-08-13, fall_reversal): PTrade IQEngine raised `AttributeError: 'numpy.ndarray' object has no attribute 'values'` in generated code `df['close'].values.astype(float)` — `get_history` returned a `numpy.structured_array`, not a pandas DataFrame.
- Root cause: 1.8.0's normalization contract was enforced only by validators/renderers; the `source_import.py` import pipeline passed the strategy's unguarded `.values` through unchanged.
- Fix (`quantstudio/strategy_compiler/source_import.py`): new `_rewrite_values_access` pass (`NORM-HIST-VALUES`) rewrites `X['col'].values` → `np.asarray(X['col'])` and multi-column `X[['a','b']].values` → `np.asarray(X[['a','b']])` for string-literal subscripts only. `np.asarray` keeps dtype semantics on both shapes (Series `.values` equivalent for DataFrame; identity for structured array). Method calls (`dict.values()`) and non-string subscripts are untouched. Re-runs are idempotent.
- The generated strategy's own `_extract_history_field` helper (guarded by `hasattr(values, 'values')`) already conforms and is left intact.

## Skill correction 0.6.0 — runtime-shape and capital gates

- Design 2.2 adds machine-checkable `portfolio_contract`, `rebalance_funding_contract`, `history_coverage_contract`, `r5_deployment_invariants`, and verbatim `confirmation_evidence`. Contradictory capital math (e.g. 20 x 5% = 100% plus a 15% cash buffer) BLOCKs at design time.
- R5 PASS is bound to hash-verified real artifacts (`config.csv`/`daily_stats.csv`/`trades.csv`/run log); self-reported `runtime_checks` booleans are no longer authoritative. A finished exception-free backtest that never deployed the designed capital BLOCKs.
- Static PTrade PASS is reported as `PTRADE_PROFILE_PASS` with runtime `NOT_VERIFIED` / deployment `NOT_DEPLOYABLE`; a real broker runtime failure retires all old evidence via `scripts/retire_ptrade_runtime_evidence.py`.

## Skill correction 0.5.4 — pre-adjusted execution contract（2026-08-14 修订：raw 口径）

- QuantStudio's implemented engine profile now uses the raw daily/minute snapshot for matching, fills, cash, valuation, `data[code].price`, and BarData OHLC — revised 2026-08-14 by real-PTrade match-price audit (daily fill = T-day raw close 5/5; minute fill = bar raw close 6/6).
- Agent-first designs now require `execution_price_basis=raw_trade_price`; pre-adjusted execution declarations are blocked as incompatible with the selected local engine profile. Signal basis stays `signal_price_adjustment=pre`.
- This is a design/profile contract correction. It does not claim that a broker PTrade runtime internally values positions with QuantStudio's snapshot; dual validation proves source/API portability while local backtest evidence records the QuantStudio execution basis.

## 2026-08-13 PTrade 分钟 include 语义实测

检测方法：PTrade 平台分钟回测（1min 频率），09:35（第 5 根 bar）handle_data
中调 get_history(5, frequency='1m', include=True/False)。

结果：
- include=True  最后一根 = 09:35 当前 bar（含当天）
- include=False 最后一根 = 09:34 前一 bar（不含当天）

结论：PTrade 分钟 include 是"标准"语义（True=含当前 bar，False=不含），
与本地框架一致（本地引擎 2026-08-13 修复后：分钟 include=False 锚定当天 +
排除当前 bar）。PTrade 日线 include 语义与本地一致（include=True 含当天 T、
include=False 到前一交易日 T-1）——2026-08-13 第二次实测确认（含日期戳），
修正先前仅凭 close 值推断的错误结论。

转换映射结论：
- 所有频率（日线+分钟）：不映射（两端 include 语义完全一致）。
  NORM-INCLUDE-PTRADE 已删除（2026-08-13 第二次实测推翻先前结论）。

## 技术债：分钟 include=True 策略迁移（待执行）

以下策略当前使用分钟 include=True（取当前 bar close 做信号 + close 撮合），
存在同 bar lookahead 风险。引擎修复后（分钟 include=False 锚定前一 bar，
2026-08-13），应迁移到 include=False：

- smallcap_overnight_scalp_7_quantstudio.py（line 636-642）：
  get_history(1, frequency='1m', include=True) → values[-1] = 当前 bar close
  迁移后：include=False → values[-1] = 前一 bar close（无 lookahead）
- first_cover_event_daily_quantstudio.py（line 2706-2712）：
  读 09:31 已完成分钟 bar → include=True
  迁移后：include=False → 读 09:30 bar（引擎修复后 09:31 决策看到 09:30）

迁移前提：引擎分钟 include=False 修复完成（ptrade_api.py，2026-08-13）。
迁移验证：迁移前后 T5 逐位断言（行为变更，需用户确认）。

## CORRECTION (2026-08-13 re-test with date stamps)

先前结论（仅看 close 值推断）"PTrade 日线 include=True 不含当天"是错误的。

重新实测（07-03 handle_data，打印每根 bar 日期 + close）：
- include=True  最后一根 = date=20260703（当天 07-03）→ 含当天
- include=False 最后一根 = date=20260702（前一交易日）→ 不含当天

因子得分交叉验证：PTrade 07-01 159995 得分 18.263 ≈ 本地含当天 18.363
（差异 0.5%），远≠本地不含当天 8.137（差异 56%）。

结论：PTrade 日线+分钟 include 语义均与本地一致——所有频率都不需要映射。
