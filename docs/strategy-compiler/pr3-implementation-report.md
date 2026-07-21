# PR3 Implementation Report

Stage: PR3  
Status: PASS (waiting for user confirmation)

## Goal

Propagate `frequency` from the PtradeAPI layer (`get_history`/`get_price`/`get_snapshot`) down to Provider and DuckDB query layers. `frequency='1d'` (default) takes the original daily path byte-for-byte; minute frequencies query `stock_minutes`/`etf_minutes` native freq columns; unsupported or missing frequencies raise a structured capability error — no silent fallback to daily data.

The change is strictly isolated to the Provider/query layer. The PR4 minute event engine, matching/order/position semantics, security-code rules, Skill, and Renderer are not touched.

## Authorized deviation (approved 2026-07-21)

Master plan 7.19 `1m→5m/15m/30m/60m` real-time aggregation and `test_minute_aggregation.py` are deferred to **PR3.5**. Rationale: the ingest pipeline writes each native freq separately (1min/5min/15min/30min/60min each stored once), so aggregation is a fallback path only; the minute tables are currently empty so aggregation boundaries (11:30-13:00 lunch break, cross-day) cannot be calibrated against real data; writing it now would埋雷. Trigger condition for PR3.5: a real native-freq gap appears after minute data is ingested. PR3 implements native-freq query only.

## Completed

1. Added `quantstudio/backtest/providers/frequency_labels.py` — single source of truth for API↔storage freq mapping (`1m`↔`1min`, `5m`↔`5min`, ...) and `FrequencyCapabilityError` carrying a structured `code` attribute (`INVALID_FREQUENCY` / `TABLE_MISSING` / `TABLE_EMPTY` / `FREQ_NOT_IN_TABLE`) for programmatic discrimination (not string matching).
2. Added `quantstudio/backtest/providers/intraday_windows.py` — Python-side epoch-millisecond trading-session windows. This corrects a factual bug in the v1 plan: `time % 1000000` and `time / 1000 % 86400` do not extract intraday time because `time` is a 13-digit epoch-ms timestamp (`aligner.py` `to_ms_timestamp`). Windows are generated per calendar day in Python and passed as indexable BETWEEN conditions.
3. `MarketDataProvider` abstract: `get_bars`/`get_bars_by_count` gained a trailing `frequency="1d"` default; added `get_snapshot(frequency="1d")` abstract declaration (补齐 5).
4. `DuckDBMarketDataProvider`: frequency dispatch — `frequency='1d'` takes the original daily path unchanged; minute frequencies route to `query_minute_bars_by_range`. Implemented `get_snapshot`. Calendar is injected via `from_duckdb` so the window generator can enumerate trading days.
5. `DuckDBDataAccess`: added `query_minute_bars_by_range` / `query_minute_bars_by_count` / `_resolve_minute_table` (ETF→etf_minutes; index/convertible-bond→None→TABLE_MISSING). Minute query SELECT only includes columns actually present in minute tables (no `turn`/`pctChg`/`peTTM`/`pbMRQ`). `fq='pre'`/`'dypre'` replaces OHLC with `*_front` columns; `fq='post'` uses `*_back`; `preClose` keeps the raw value (documented simplification).
6. PtradeAPI: `get_history` passes `unit` as `frequency`; `get_price` passes `frequency`; `get_snapshot` gains a `frequency` param. `FrequencyCapabilityError` pierces the `except Exception` blocks so a missing-minute-data error surfaces instead of being silently swallowed into an empty DataFrame.
7. `capability_report.example.json`: `stock_1m_backtest.provider_status` PROVIDER_MISSING→AVAILABLE (code link ready only). `data_status` remains DATA_MISSING; `engine_status` remains ENGINE_MISSING; `execution_status` remains BLOCKED. The message explicitly states AVAILABLE must not be interpreted as "minute backtest usable".

## Critical design decisions (from plan review)

- **Session-window generation**: Python-side epoch-ms intervals, not `time % N` arithmetic. The v1 plan's `time % 1000000` / `time / 1000 % 86400` were factual bugs (epoch-ms vs HHMMSS, and UTC vs CST 8-hour offset). Each trading day produces two windows ([09:31,11:30] ∪ [13:01,15:00] under end-labeled convention), passed as parameterized BETWEEN conditions (indexable, injection-safe).
- **end_cutoff_ms scoping**: the end_date cutoff applies only to the last day; intra-range days use full session windows (补齐 2).
- **FrequencyCapabilityError.code**: structured code attribute for programmatic discrimination, not Chinese-string matching (补齐 4). The PtradeAPI except branches re-raise on this error type.
- **Capability error four-way split**: `INVALID_FREQUENCY` (unknown label like "1s") / `TABLE_MISSING` (index/cb have no minute table) / `TABLE_EMPTY` (table exists but no data — the current production state) / `FREQ_NOT_IN_TABLE` (data present but missing the requested freq, listing available freqs).

## Known simplifications / real-data smoke TODOs

- **Bar timestamp convention**: assumes end-labeled (09:31 bar = 09:30:01-09:31:00). Cannot verify against real data (tables empty). Listed as the first verification item when minute data is ingested. If actually start-labeled, the session windows shift by one minute and the lunch-break boundary judgment changes.
- **preClose under qfq**: keeps the raw value. Minute preClose is for pct-chg reference only; impact is minimal. If the ingest side guarantees a复权 preClose, it can be mapped later.
- **Minute count query**: implements "N bars before end_date same-day" only. Cross-day count semantics are deferred to real-data calibration (documented).

## Changed files

- `quantstudio/backtest/providers/frequency_labels.py` (new)
- `quantstudio/backtest/providers/intraday_windows.py` (new)
- `quantstudio/backtest/providers/base.py` (abstract frequency params + get_snapshot)
- `quantstudio/backtest/providers/duckdb_provider.py` (frequency dispatch + get_snapshot + calendar injection)
- `quantstudio/backtest/providers/duckdb_data_access.py` (minute query methods + _resolve_minute_table)
- `quantstudio/backtest/ptrade_api.py` (pass frequency + error piercing + get_snapshot param + FrequencyCapabilityError import)
- `quantstudio/strategy_compiler/examples/capability_report.example.json` (provider_status AVAILABLE)
- `docs/strategy-compiler/frequency-and-engine-profile.md`
- `docs/strategy-compiler/implementation-status.md`
- `tests/test_etf_ptrade_compat.py` (mock signature gained frequency param)

## New tests

```text
tests/test_provider_frequency_routing.py
tests/test_minute_bars_query.py
tests/test_etf_minute_bars_query.py
tests/test_minute_qfq_query.py
tests/test_frequency_no_daily_fallback.py
```

### Test coverage

- `test_provider_frequency_routing.py`: freq label mapping; `frequency='1d'` daily path byte-level unchanged (does not touch minute query); `frequency='1m'` routes to minute query; abstract interface has frequency params; unknown freq raises INVALID_FREQUENCY.
- `test_minute_bars_query.py`: synthetic `stock_minutes` (real 32-column DDL); `get_bars(frequency='1m')` returns correct bars; all bars within [09:31-11:30]∪[13:01-15:00]; midday gap excluded; no daily-only fields; fq='pre' uses front columns; fixture matches real schema.
- `test_etf_minute_bars_query.py`: ETF→etf_minutes; stock→stock_minutes; index→TABLE_MISSING.
- `test_minute_qfq_query.py`: fq='pre'→front columns; fq=None→raw; fq='post'→back; preClose keeps raw.
- `test_frequency_no_daily_fallback.py`: empty table→TABLE_EMPTY (not empty DataFrame); missing freq→FREQ_NOT_IN_TABLE+available_freqs; source-level assertion that minute query does not enter daily fallback chain.

## Verification (fixed order)

```text
1. New frequency tests:           28 passed
2. Core regression (zero-touch):  42 passed
3. Full test suite:               309 passed (281 prior + 28 new)
4. Real Fidelity gates:
   ETF momentum:    PASS  final_asset 87752.561780  3 fills  exact sequence
   Small-cap guard: CLOSE final_asset 118551.211880 57 fills  within frozen envelope
```

ETF golden sequence and small-cap CLOSE envelope are byte-level identical to the pre-PR3 baseline, confirming the daily-path zero-touch property.

## Isolation contract

The daily-path zero-touch property is protected by dedicated assertions:

- `test_get_bars_daily_frequency_does_not_touch_minute_query` — `frequency='1d'` never calls the minute query method.
- `test_minute_query_does_not_enter_daily_fallback_chain` — source-level assertion the minute method does not call `query_bars_by_count_multi_table` (daily fallback).
- `test_get_bars_minute_does_not_fallback_to_daily` — minute path source-level isolation.

## What was not mixed in

- No minute event engine / minute matching / `run_daily(time=...)` (PR4).
- No 1m→5m/15m/30m/60m real-time aggregation (PR3.5, user-approved deferral).
- No minute data ingest (independent task; may run in background; not part of PR3 delivery).
- No Skill (PR5) / Renderer (PR6) / security-code rules (PR1) / matching口径 (PR2).

## Next gate

Waiting for user confirmation of PR3 before starting PR4 (minute event engine). PR4 must not mix in Provider frequency-routing changes or PR3.5 aggregation.
