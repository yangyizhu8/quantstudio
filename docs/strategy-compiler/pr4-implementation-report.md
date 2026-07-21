# PR4 Implementation Report

Stage: PR4  
Status: CONFIRMED (user-approved 2026-07-22; real-minute smoke PASS, end-labeled verified, GIL defect fixed)

## Goal

Implement the minute event-driven backtest engine via a main-loop branch (decision 1). `run()` dispatches by `engine_profile`: `daily-bar-v1` (default, byte-level unchanged) vs `minute-bar-v1` (new `_run_minute_day`). The change is isolated; daily path zero-touch verified by Fidelity golden gates.

## Four user decisions

1. **Architecture**: main-loop branch (minimal change). Minute loop is a separate `_run_minute_day` method; daily loop untouched. GUI/CLI only pass params, no GUI refactor. Natural seam for PR7 abstraction but not abstracted now.
2. **Real data**: time-boxed small-sample attempt; fell back to synthetic (collector is full-market, no code scoping without config changes — out of PR4 scope). Synthetic suite complete (32 tests, all 6 golden use cases).
3. **run_daily**: precise HH:MM dispatch. Per master plan 7.24: update current_dt → dispatch run_daily → handle_data.
4. **ETF T+0**: implemented but minute-only. Daily profile forces etf_t0=False (golden baseline protected).

## Completed

1. `BacktestEngine.__init__`: `engine_profile` (default daily-bar-v1) + `etf_t0` (default False, minute-only). Minute+next_open rejected.
2. `engine_semantics_version`: minute → 0.3.0-minute-bar.
3. `run()` main-loop branch: minute → `_run_minute_day`.
4. `_run_minute_day`: full lifecycle (T+1 unlock → attach_day → before_trading_start → per-bar loop → after_trading_end → daily NAV). 15:00 close bar included. Per-bar progress callback (daily-once, signature-compatible).
5. `minute_scheduler.py` `_MinuteScheduler`: precise HH:MM dispatch, once-per-day, cross-day reset, time-format compat.
6. `attach` split: `attach_day` (preload, daily-once) + `attach_bar` (skip preload, per-bar). Backward-compat alias.
7. **Gap-1 fix**: `attach_bar` injects `_current_bar_ts`; get_history/get_price minute queries anchor to it via cross-layer `bar_cutoff_ms`. No future-bar leak.
8. **Gap-2 fix**: minute `attach_day` passes prev-day close prices (before_trading_start pre-09:31 must not see same-day close).
9. ETF T+0: `_execute_buy` unlocks can_sell when etf_t0+is_etf. Daily forces False.
10. Minute halt check in `_immediate_execute` (minute-only; daily unchanged).
11. Minute pctChg uses daily snapshot (not bar preClose).
12. CLI `--profile`/`--etf-t0`; StrategyRunner.run passes through.

## Critical gap fixes (from plan review)

- **Gap 1 (get_history/get_price future-bar leak)**: PR3's minute query anchored to day-start (00:00), returning all-day bars. At 10:00 bar, get_history(60,'1m') leaked 10:01-14:59. Fixed: attach_bar injects current_bar_ts; queries anchor to it. New test `test_minute_query_no_future_leak` written first (red), then implementation. "Current bar visible" semantics: minute includes current bar (end-labeled completed); daily excludes (now-undefined).
- **Gap 2 (attach_day daily_prices leak)**: minute before_trading_start (pre-09:31) saw same-day close if attach_day passed daily_prices. Fixed: minute passes prev-day close; real prices come from first attach_bar onward.

## Changed files

- `quantstudio/backtest/backtest_engine.py` (engine_profile, _run_minute_day, _execute_buy ETF T+0, _immediate_execute halt, _build_daily_pctchg_map, _load_minute_snapshots, benchmark try/except, _is_etf_code)
- `quantstudio/backtest/minute_scheduler.py` (new)
- `quantstudio/backtest/ptrade_api.py` (attach_day/attach_bar split, _current_bar_ts, _daily_curr_data, _curr_data_for_execute, reset_session, frequency alias)
- `quantstudio/backtest/providers/base.py` (bar_cutoff_ms param)
- `quantstudio/backtest/providers/duckdb_provider.py` (bar_cutoff_ms passthrough)
- `quantstudio/backtest/providers/duckdb_data_access.py` (bar_cutoff_ms in minute queries)
- `quantstudio/backtest/providers/intraday_windows.py` (both windows apply cutoff)
- `quantstudio/backtest/run_ptrade_strategy.py` (--profile, --etf-t0)
- `quantstudio/backtest/strategy_runner.py` (engine_profile, etf_t0 passthrough)
- `docs/strategy-compiler/implementation-status.md`, `frequency-and-engine-profile.md`
- `tests/conftest.py` (new shared fixtures), `tests/test_etf_ptrade_compat.py` (mock sig)

## New tests (8 files, 32 tests)

```text
tests/test_minute_query_no_future_leak.py      (gap-1, written first)
tests/test_minute_engine_lifecycle.py
tests/test_run_daily_scheduler.py
tests/test_minute_order_execution.py
tests/test_minute_t1.py
tests/test_minute_limit_halt.py
tests/test_minute_nav.py
tests/test_minute_daily_compatibility.py
```

### 6 golden use case coverage (master plan 7.26)

| Golden | Test | Impl |
|---|---|---|
| 1. 09:35 buy, 14:55 sell ETF | test_minute_order_execution | instant match + ETF T+0 |
| 2. 1m dual MA | test_minute_daily_compatibility | per-bar handle_data + daily compat |
| 3. T-day minute buy, same-day sell blocked (stock) | test_minute_t1 | stock T+1 |
| 4. ETF profile allows T+0 | test_minute_nav + test_minute_t1 | etf_t0 + is_etf |
| 5. minute limit-up buy rejected | test_minute_limit_halt | daily pctChg |
| 6. run_daily('10:00') once/day | test_run_daily_scheduler | _MinuteScheduler |

## Verification

```text
1. 8 new minute engine tests:  32 passed
2. Core regression (zero-touch): all pass
3. Full test suite:             341 passed (309 + 32)
4. Real Fidelity gates:
   ETF momentum:    PASS  final_asset 87752.561780  3 fills  exact sequence
   Small-cap guard: CLOSE final_asset 118551.211880 57 fills
```

Daily golden sequence byte-level identical → main-loop branch isolation confirmed.

## Real-minute-data status (decision 2) — closed 2026-07-22

Real minute data ingested and PR4 verified against it:
- `stock_minutes`: 18,777,900 rows (xtquant, 2026-07-01~07-21, 5201 codes)
- `etf_minutes`: 46,940,329 rows (xtquant, 2026-01-05~07-21, 1605 ETFs)

Real-data smoke (510050 ETF × 2026-07-17 × full-market universe) PASS:
- `initialize`=1, `before_trading_start`=1, `handle_data`=240, `after_trading_end`=1
- First bar 09:31 (end-labeled continuous auction), last bar 15:00 (closing auction)
- before_trading_start sees no same-day bar (gap-2 fix holds)
- GIL crash (see defect fix below) resolved by batch loading

## end-labeled convention (承接提醒 1) — VERIFIED 2026-07-22

**Verified end-labeled** (the project's last unverified load-bearing assumption, now closed):
- 600000.SH × 6 trading days + 510050 ETF: all 241 bars/day, first 09:30, last 15:00
- 09:30 bar: O=H=L=C (call auction single opening price) → confirms end-labeled (call auction 09:25-09:30 result, not 09:30:00-09:30:59 continuous)
- 09:31 bar: O≠H≠L≠C (first continuous auction bar) → matches end-labeled definition (09:30:01-09:31:00)
- 14:59 vol=0 (entered closing auction), 15:00 bar has real volume (closing auction 14:57-15:00)
- PR3 intraday_windows / PR4 _MinuteScheduler / attach_bar all built on correct assumption. No cascade review needed.

**Design note (not a bug)**: engine processes 240 continuous-auction bars (09:31-11:30 + 13:01-15:00); the 09:30 call-auction bar is intentionally excluded from the minute loop (its opening price flows through daily attach_day via preClose/open). `handle_data`=240 is correct, not 241.

## Defect fix: _load_minute_snapshots GIL crash (2026-07-22, upgraded from defer)

The deferred perf item (line below, "batch deferred") **upgraded to a functional blocker** on real full-universe data:
- Original: per-code loop calling `query_minute_bars_by_range` × 5525 codes × 4 DB calls/code = ~22k DB roundtrips/day
- Symptom: `Fatal Python error: PyEval_SaveThread: GIL must be held` — duckdb C extension GIL state corrupted after thousands of execute/fetchdf calls on the persistent read-only connection
- Fix: `query_minute_bars_by_range_batch` (new, duckdb_data_access.py) — one SQL per table (stock_minutes/etf_minutes), DB roundtrips ≤ 2/day regardless of universe size; `_load_minute_snapshots` rewritten to call it
- Secondary fix: `iter_trading_days_in_range` process-level cache (intraday_windows.py) — eliminates redundant calendar queries (same day-range was queried once per code)
- Contract preserved: FrequencyCapabilityError (TABLE_EMPTY / freq missing) raised when entire universe has no minute data; per-code absence naturally absent from result set (same "skip" semantics)
- Regression tests: `tests/test_minute_batch_loading.py` (8 tests) — query-count ≤2 independent of universe size, batch==iterative result equivalence, lifecycle not regressed, cache hits

## Known limitations (documented)

- 15:00 bar = closing auction (tested, verified on real data).
- Volume participation: not implemented (consistent with daily).
- ~~_load_minute_snapshots per-code query: perf limit (batch deferred).~~ → **Fixed 2026-07-22 (see defect fix above)**.
- Scheduler order: minute before handle_data; daily after (7.24).
- Current-bar-visible semantics differ by profile.

## Next gate

PR4 CONFIRMED by user 2026-07-22 (independent 341-test rerun + Fidelity double-gate + gap fixes reviewed + real-minute smoke PASS + end-labeled verified + GIL defect fixed). PR5 (Skill skeleton) unblocked.
