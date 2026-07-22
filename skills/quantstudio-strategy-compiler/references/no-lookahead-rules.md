# No-Lookahead Rules

> New file aggregated from lifecycle-and-timing-contract.md §3 + master-implementation-plan-v1.0.md §9 @ 2026-07-22
> 权威源：docs/strategy-compiler/lifecycle-and-timing-contract.md §3 + master plan §9
> 本文件为 Skill 派生快照，契约变更时必须同步

The following patterns leak future information into a decision and **MUST block** strategy generation or backtest — not merely warn. `validation_policy.no_lookahead` is `const: "BLOCK"` in the schema (not configurable to ALLOW).

## Hard blocks (6, from lifecycle contract §3)

1. **Pre-open read of same-day full close/high/low for trading.** `before_trading_start` must not see current-day close.
2. **T-day close forms signal AND trades on the same close.** Signal from T-close can only trade at next_open or later — never same-bar.
3. **Using current complete daily high/low to judge intraday triggers that already happened.** Intraday triggers must use intraday data, not daily H/L after the fact.
4. **Fundamentals not PIT by announcement date.** Financial data must use `ann_date` (announcement), not `end_date` (report period), for point-in-time correctness.
5. **`next_open` order changing cash/positions/NAV on T-day.** next_open execution settles at T+1 open; T-day NAV must not reflect it.
6. **Minute signal reading future minutes; aggregated bar used before it completes.** A 5m aggregate must not be consumed before the 5m window closes.

## High-risk items (10, from master plan §9)

Static + semantic checks must cover these — all are blocking, not warning:

1. `before_trading_start` uses same-day full close.
2. T-day close forms signal AND trades on the same close.
3. Using current complete daily high/low to judge intraday triggers.
4. Ranking or fundamentals using current-day future-available data.
5. `include=True` used incorrectly (future row leakage in joins).
6. Financial data not PIT by announcement date.
7. `next_open` orders booked into T-day ahead of time.
8. Daily-proxy mode match-price口径 inconsistent.
9. Minute signals incorrectly using future minutes.
10. 1m → 5m aggregation seeing an unfinished 5m bar early.

## How the schema enforces this

The `strategy_spec.schema.json` `allOf` rules encode several of these structurally:
- `daily_close_proxy` + `signal_data_cutoff=T-close` + `execution_clock=current_bar` → `not` (hard reject at schema validation, line 77).
- Proxy modes force `market_data_frequency=1d` + `bar_frequency=1d` + matching `match_price_mode` + an `approximations` entry (lines 75-76).

What the schema does NOT catch (validate_strategy_spec.py and downstream IR checks must):
- Fundamentals PIT discipline (ann_date vs end_date) — runtime/IR-level.
- Minute-aggregation timing — runtime/IR-level.
- `include=True` misuse — IR-level (PR6 scan_lookahead.py).

## PR5 boundary

PR5's `validate_strategy_spec.py` enforces schema-level no-lookahead (via jsonschema). IR-level and runtime-level lookahead scans belong to PR6 (`scan_lookahead.py`). A Spec that passes schema validation is not guaranteed lookahead-free — it must still pass PR6 IR checks before smoke backtest.
