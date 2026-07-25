# Strategy Compiler Implementation Status

> Master plan: `QuantStudio Strategy Compiler frozen master plan v1.0`  
> Current stage: 0.3.0-mvp RELEASED; Delivery Flow Integration — MERGED TO MAIN / PASS
> Stage status: **G1-G4 + Delivery Flow Integration all merged to `main` (0.3.0-mvp released). Delivery flow: Skill auto-orchestrates orchestrator + Skill-local deliver_strategy.py (`545f6bd`→`f5a1821`→`6b076de`→`98bf145`, all main ancestors). R2.5 HARD GATE (ALL confirmations CONFIRMED), strict static gate, smoke truth table (unknown always fail closed), 29 delivery tests PASS. Wheel SHA-256 unchanged (ccda32d9...). Data digest/Fidelity/Reference DEFERRED (blocked, not faked). CP3 Oracle `8931430` independent truth source.**

## Stage overview

| Stage | Status | Summary |
|---|---|---|
| PR0 Contracts and baseline | PASS | Contracts, schemas, examples, and contract tests complete |
| PR1 Security code rules | PASS | Runtime authority, official BSE mapping, call-site migration, and regression tests complete; user confirmed |
| PR2 `next_open` semantics | PASS | Real pending-order queue implemented; close/open zero-touch verified by real Fidelity gates; user confirmed |
| PR3 Multi-frequency Provider | PASS / WAITING_CONFIRMATION | Provider frequency routing implemented (native freq only; aggregation deferred to PR3.5); daily path byte-level unchanged |
| PR4 Minute event engine | CONFIRMED (2026-07-22) | Minute event loop implemented (main-loop branch + precise run_daily + ETF T+0 minute-only); daily path byte-level unchanged; **real-minute smoke PASS** (510050 ETF × 2026-07-17 × full-universe); **end-labeled verified** (241 bars/day, 09:30 call-auction O=H=L=C); **GIL defect fixed** (batch loading, was deferred perf item that became functional blocker on real data) |
| PR5 Skill skeleton | DONE (0.1.0-skeleton, 2026-07-22) | Skill created at `skills/quantstudio-strategy-compiler/` (SKILL.md + agents/openai.yaml + 11 references + 3 schemas + 2 scripts); quick_validate PASS; inspect_capabilities live run READY (honest probe, tick PLANNED); validate_strategy_spec PR0 example green + 3 violation variants red; awaiting user confirmation before PR6 |
| PR6 IR/renderers/validators | DONE — G1+G2+G3+G4 MERGED TO MAIN; 0.3.0-mvp RELEASED | PR6a + PR6b-1 已在 `main`；G1-I `bcdc85d` + G2 `53d90f5` + G3 `9a99b18` + G4 `09e2d29` 均为 `main` 祖先。G4 Release Closure 已合并 main 并发布 0.3.0-mvp（dist `0.3.0+mvp`，2026-07-24）。G1-G4 Skill MVP 路线完成。G2 = Hermetic Reference Partial Closure，input_data_digest=null/data_digest_status=blocked，真实数据 digest/Fidelity/Reference 后置。CP3 Oracle reference `8931430` 保留为独立 truth source。 |
| PR7 Fidelity closure | NOT_STARTED | Planned |

## PR0 acceptance

PR0 was confirmed by the user before PR1 started.

Baseline and completion results:

```text
Initial baseline: 201 passed in 14.84s
PR0 contract tests: 19 passed
PR0 final full suite: 220 passed in 13.40s
```

PR0 report: `docs/strategy-compiler/pr0-implementation-report.md`.

## Current contract versions

| Field | Version | Meaning |
|---|---|---|
| `strategy_spec_version` | `1.0.0` | PR0 contract |
| `engine_semantics_version` | `0.1.0-legacy` / `0.2.0-next_open_pending` | PR2: mode-dependent. close/open still `0.1.0-legacy`; next_open now uses real pending-order queue |
| `provider_contract_version` | `0.2.0-frequency-aware` | PR3: get_bars/get_bars_by_count/get_snapshot now accept frequency; native minute freq routing (aggregation deferred to PR3.5) |
| `security_code_rules_version` | `1.0.0` | PR1 authoritative runtime module |
| `ptrade_profile_version` | `1.0.0-default` | default public API profile |
| `renderer_version` | `0.0.0-planned` | planned for PR6 |
| `skill_version` | `0.1.0-skeleton` | PR5: Skill skeleton created (SKILL.md + references + schemas + 2 scripts); rendering arrives in PR6 |

## PR1 completed work

- [x] Added `quantstudio/backtest/libs/security_code_rules.py` as the code-level authority.
- [x] Exported classification and QMT/PTrade/bare normalization functions.
- [x] Delegated STAR/ChiNext/BSE/ST predicates from `shared_ashare_rules.py`.
- [x] Removed independent numeric-prefix market decisions from `ptrade_api.py`.
- [x] Migrated `backtest_engine.py`, DuckDB data access, and reference-provider routing.
- [x] Supported `.SH/.SS/.XSHG/.SZ/.XSHE/.BJ/.XBJ/.XBSE/bare` aliases.
- [x] Classified current BSE equities by `920xxx`.
- [x] Classified historical BSE equities only through the official 248-row exact mapping.
- [x] Prevented blanket BSE classification of `400xxx`, `420xxx`, and unmapped `8xxxxx`.
- [x] Kept ETF/index/convertible-bond classification ahead of stock-board classification.
- [x] Generated documentation from runtime metadata; runtime code never reads documentation.
- [x] Kept `next_open`, order execution, costs, and event lifecycle unchanged.

## BSE verification record

- Official page: `https://www.bse.cn/service/code_mapping.html`.
- Durable snapshot: `docs/strategy-compiler/sources/bse-official-code-mapping-20260720.json`.
- Packaged exact mapping: `quantstudio/backtest/libs/bse_legacy_code_mapping.json`.
- Official mapping contains 248 rows and legacy prefixes `430`, `830-839`, and `870-873`.
- Prefix presence does not classify the entire prefix. Membership is exact.
- Project database sampling found 329 current `920xxx` codes and three old, unmapped `832/833` NEEQ samples ending in 2021; those samples are not treated as BSE equities.

## PR1 verification

```text
Pre-PR1 full baseline: 220 passed in 12.28s
PR1 focused tests:     28 passed in 0.46s
Core regression:       54 passed in 4.52s
Full test suite:       249 passed in 12.67s
Daily smoke backtest:  PASS (20 trading days, 2 trades)
```

Final logs: `output/strategy-compiler-pr1/`.

## Compatibility and limitations

1. Existing Shanghai/Shenzhen `.SS/.SZ` public output and exact portfolio membership remain unchanged.
2. Alias-aware access remains available through market-data containers and explicit position APIs.
3. Beijing inputs accept `.BJ/.XBJ/.XBSE`; public output is `.BJ`.
4. Historical BSE codes are classified but not rewritten to `920`, preserving historical database keys.
5. `next_open` now implements a real pending-order queue (PR2). T-day signal creates a pending order with symmetric pre-deduction (`locked_cash` for buys, `pending_sell_shares` for sells); T+1 open event drains the queue with T+1 price and state. `close`/`open` modes are untouched.
6. Minute capability now READY (post-PR4 + real data ingested 2026-07-22); Tick still BLOCKED (PR9 scope). [Updated 2026-07-22: at PR1 time both were BLOCKED; minute has since flipped to READY.]
7. The workspace is not a Git repository; audit evidence is the file list, tests, smoke output, and official mapping snapshot.


## Cross-stage Fidelity hardening after PR1

A real ETF regression exposed that full pytest success alone cannot guarantee PTrade behavioral fidelity. The public portfolio container had temporarily become alias-aware, changing direct strategy membership and producing 31 fills and final asset 49,064.37 instead of the approved 3-fill result.

The repaired semantics are now protected by:

- `config/strategy_fidelity_gates.json`;
- `scripts/run_strategy_fidelity_gates.py`;
- `tests/test_strategy_fidelity_gates.py`;
- `docs/strategy-compiler/strategy-fidelity-regression-gate.md`.

Current real-data gate result on July 21, 2026:

```text
ETF momentum: PASS
  final asset 87,752.561780
  3 fills, exact approved sequence
  L1 100%, L3 100%
  NAV deviation 0.004385%

Small-cap guard: CLOSE accepted inside frozen envelope
  final asset 118,551.211880
  57 fills
  L1 72.3077%, L3 95.2381%
  NAV deviation 0.26347%
```

Evidence: `output/strategy-fidelity-gates/current-full-run/summary.json`.

This Fidelity command is mandatory for PR2 and every later runtime stage. A stage cannot be marked PASS based only on pytest. Golden values cannot be changed without a separately documented real PTrade re-baseline and explicit user approval.

### smallcap_guard re-baseline (2026-07-22, user-approved)

After the xtquant daily cutover (stock_daily 100% xtquant as of 2026-07-22), the smallcap_guard envelope was re-baselined from the tushare-era values to the xtquant-data values. Full attribution in `docs/strategy-compiler/pr6a-smallcap-baseline-drift-analysis.md`.

| Element | Old (tushare, until 2026-07-22) | New (xtquant, from 2026-07-22) | Note |
|---|---|---|---|
| expected_final_asset | 118551.21 | 118160.53 | center value updated; ±10 tolerance unchanged |
| expected_trade_count | 57 | 59 | center value updated |
| fidelity (L1/L2/L3/L4) | unchanged | unchanged | tolerances NOT relaxed |
| sim_terminal_warning | n/a | added | miniQMT sim ST board empty (isST=0); is_st_reliable backstops; re-verify on real terminal |

Approval basis: xtquant single-source lock is an approved architecture decision (2026-07-21); etf_momentum hard gate PASSES on the same xtquant data (87752.56 exact); the 0.33% smallcap drift is within the reasonable magnitude of back-adjustment algorithm differences. Old envelope archived in `config/strategy_fidelity_gates.json` `_baseline_history[0]`.

## PR2 completed work

PR2 implemented a real pending-order queue, eliminating the cross-day look-ahead in the legacy `next_open` mode. The change is strictly isolated to `match_price_mode == "next_open"`; `close`/`open` execution paths are untouched line-by-line.

### Runtime changes

- [x] Added `PendingOrder` dataclass with lifecycle `created → pending → filled/rejected/expired/cancelled` and 7 instruction enums.
- [x] Added `Account.locked_cash` (pending-buy fund pre-deduction; 0 in close/open).
- [x] Added `Position.pending_sell_shares` (pending-sell share pre-deduction; 0 in close/open).
- [x] Added `Order.filled_dt` (T+1 fill date for next_open; empty in close/open).
- [x] `_build_match_prices` next_open branch returns same-day close as "strategy-visible price"; matching duty moved to drain's T+1 open (eliminates T+1 prefetch look-ahead).
- [x] `_create_pending_order`: T-day creates pending order + symmetric pre-deduction (buy→locked_cash, sell→pending_sell_shares). T-day volume/trade_records unchanged.
- [x] `_drain_pending_orders`: T+1 open event executes pending queue with T+1 open price + T+1 state (halt/limit/cash/lot). Fills record T+1 date; rejections/expire/cancel refund pre-deductions exactly.
- [x] Main loop order: unlock → drain → refresh_portfolio → strategy (drain placed after unlock so newly filled buys keep `can_sell=0`).
- [x] PtradeAPI four trade methods (`order_target_value`/`order`/`order_value`/`order_target`) branch to `_create_pending_order` before any value→shares/delta conversion in next_open mode.
- [x] `get_open_orders`/`get_order`/`cancel_order` populated in next_open mode; close/open keep legacy empty/no-op behavior.
- [x] Run Card config.csv records `engine_semantics_version` (mode-dependent).
- [x] End-of-backtest `_expire_remaining_pending` marks still-pending orders expired with refund.

### PR2 verification

```text
Pre-PR2 full baseline:        255 passed in 13.20s
PR2 new pending-order tests:  26 passed in 0.44s
Core regression (zero-touch): 66 passed in 3.60s
Full test suite:              281 passed in 9.92s
```

Real Fidelity gates (close-mode golden sequence byte-level unchanged):

```text
ETF momentum: PASS
  final asset 87752.561780, 3 fills, exact approved sequence
  L1 100%, L3 100%, NAV deviation 0.004385%

Small-cap guard: CLOSE accepted inside frozen envelope
  final asset 118551.211880, 57 fills
  L1 72.3077%, L3 95.2381%
```

Evidence: `output/strategy-fidelity-gates/summary.json`.

### PR2 isolation contract

The close/open zero-touch property is protected by dedicated assertions:

- `test_close_mode_account_has_no_locked_cash` / `test_open_mode_account_has_no_locked_cash`
- `test_close_mode_immediate_execute_does_not_touch_locked_cash` (immediate path never touches `locked_cash` / `pending_sell_shares`)
- `test_engine_semantics_version_reflects_match_price_mode`

Drain fill price uses a separately-built T+1 open dictionary (not `match_prices`, which is T-day close in next_open mode) — locked by `test_drain_pending_buy_fills_at_t1_open_not_close`.

## PR2 gate (closed)

PR2 implemented and verified. Next stage requires explicit user approval. PR3 must implement multi-frequency Provider routing and must not mix in next_open pending-order changes.

## PR3 completed work

PR3 propagated `frequency` from the PtradeAPI layer down to Provider and DuckDB query layers. The change is isolated: `frequency='1d'` (default) takes the original daily path byte-for-byte; minute frequencies query `stock_minutes`/`etf_minutes` native freq columns; unsupported or missing frequencies raise a structured capability error (no silent fallback to daily).

### Authorized deviation (approved 2026-07-21)

The master plan 7.19 `1m→5m/15m/30m/60m` real-time aggregation and `test_minute_aggregation.py` are **deferred to PR3.5**. Trigger condition: a real native-freq gap appears after minute data is ingested. PR3 implements native-freq query only; missing freq returns `FrequencyCapabilityError(code=FREQ_NOT_IN_TABLE)` listing available freqs.

### Runtime changes

- [x] Added `quantstudio/backtest/providers/frequency_labels.py`: API↔storage freq mapping (`1m`↔`1min` etc.) and `FrequencyCapabilityError` with `code` attribute (INVALID_FREQUENCY / TABLE_MISSING / TABLE_EMPTY / FREQ_NOT_IN_TABLE).
- [x] Added `quantstudio/backtest/providers/intraday_windows.py`: Python-side epoch-millisecond trading-session windows (corrects the v1 `time % N` arithmetic bug — `time` is 13-digit epoch ms, not HHMMSS). Documents the end-labeled bar convention assumption.
- [x] `MarketDataProvider` abstract: `get_bars`/`get_bars_by_count` gained `frequency="1d"` trailing default; added `get_snapshot(frequency="1d")` abstract declaration.
- [x] `DuckDBMarketDataProvider`: frequency dispatch in `get_bars`/`get_bars_by_count`; implemented `get_snapshot`; calendar injected via `from_duckdb` for trading-day enumeration.
- [x] `DuckDBDataAccess`: added `query_minute_bars_by_range`/`query_minute_bars_by_count`/`_resolve_minute_table` (ETF→etf_minutes, index/cb→None→TABLE_MISSING).
- [x] PtradeAPI: `get_history` passes `unit`, `get_price` passes `frequency`, `get_snapshot` gains `frequency` param; `FrequencyCapabilityError` pierces the `except Exception` (no silent swallow).
- [x] `capability_report.example.json`: `stock_1m_backtest.provider_status` PROVIDER_MISSING→AVAILABLE (code link ready only). [Updated 2026-07-22: data_status now AVAILABLE, execution_status now READY post-ingestion — the PR3-time "still DATA_MISSING/BLOCKED" parenthetical is superseded.]

### PR3 known simplifications / verification TODOs (real-data smoke)

- Bar timestamp convention assumes **end-labeled** (09:31 bar = 09:30:01-09:31:00). [Updated 2026-07-22: VERIFIED on real data — 09:30 bar O=H=L=C 集合竞价签名 confirms end-labeled. The PR3-time "Cannot verify against real data (tables empty)" caveat is closed.]
- `preClose` keeps the raw value under `fq='pre'` (minute preClose is for pct-chg reference only; impact minimal). Documented simplification.
- Minute `count` query implements "N bars before end_date same-day" only; cross-day count semantics deferred to real-data calibration.

### PR3 verification

```text
Pre-PR3 full baseline:        281 passed in 13.20s
PR3 new frequency tests:      28 passed in 1.43s
Core regression (zero-touch): 42 passed in 5.75s
Full test suite:              309 passed in 11.68s
```

Real Fidelity gates (daily path byte-level unchanged):

```text
ETF momentum: PASS  final asset 87752.561780  3 fills  exact sequence
Small-cap guard: CLOSE final asset 118551.211880 57 fills
```

Evidence: `output/strategy-fidelity-gates/summary.json`.

### PR3 isolation contract

The daily-path zero-touch property is protected by:

- `test_get_bars_daily_frequency_does_not_touch_minute_query` — `frequency='1d'` never calls the minute query method.
- `test_minute_query_does_not_enter_daily_fallback_chain` — source-level assertion that the minute method does not call `query_bars_by_count_multi_table` (daily fallback).
- `test_get_bars_minute_does_not_fallback_to_daily` — minute path source-level isolation.

## PR3 gate (closed)

PR3 implemented and verified. Next stage requires explicit user approval. PR4 must implement the minute event engine and must not mix in Provider frequency-routing changes or PR3.5 aggregation.

## PR4 completed work

PR4 implemented the minute event-driven backtest engine via a main-loop branch (decision 1: minimal change, daily loop untouched line-by-line). `run()` dispatches by `engine_profile`: `daily-bar-v1` (default, unchanged) vs `minute-bar-v1` (new `_run_minute_day`). Shared Account/_immediate_execute/涨跌停/cost; minute-only EventStream+Scheduler.

### Runtime changes

- [x] `BacktestEngine.__init__` gained `engine_profile` (default `daily-bar-v1`) + `etf_t0` (default False, minute-only). Minute+next_open rejected at construction.
- [x] `engine_semantics_version`: minute → `0.3.0-minute-bar`.
- [x] `run()` main loop branches: minute → `_run_minute_day(i, day, ...)`; daily unchanged.
- [x] `_run_minute_day`: full day lifecycle (T+1 unlock → attach_day → before_trading_start → per-bar [update current_dt → attach_bar → run_daily dispatch → handle_data] → after_trading_end → daily NAV). Includes 15:00 close bar.
- [x] New `minute_scheduler.py` `_MinuteScheduler`: precise HH:MM dispatch (run_daily('10:00') fires at 10:00 bar), once-per-day, cross-day reset. time format compat '9:31'/'09:30'/'9:31:00'.
- [x] `attach` split into `attach_day` (preload, daily-once) + `attach_bar` (skip preload, per-bar). Backward-compat `attach` aliases `attach_day`.
- [x] Gap-1 fix: `attach_bar` injects `_current_bar_ts`; `get_history`/`get_price` minute queries anchor to it (end_cutoff = bar_ts), no future-bar leak. Cross-layer `bar_cutoff_ms` param through Provider → DataAccess → intraday_windows.
- [x] Gap-2 fix: minute `attach_day` passes prev-day close prices (before_trading_start runs pre-09:31, must not see same-day close).
- [x] ETF T+0 (decision 4, minute-only): `_execute_buy` unlocks `can_sell=new_volume` when `etf_t0 and is_etf(code)`. Daily profile forces `etf_t0=False` (golden baseline protected). `is_etf` via PR1 security_code_rules.
- [x] Minute halt check added to `_immediate_execute` (minute profile only; daily unchanged to protect baseline).
- [x] Minute pctChg uses daily snapshot (via `_curr_data_for_execute`), not bar preClose (avoids non-qfq preClose除权 risk).
- [x] CLI `--profile` + `--etf-t0`; StrategyRunner.run passes them through. No GUI changes (decision 1).

### PR4 verification

```text
Pre-PR4 full baseline:        309 passed
PR4 new minute engine tests:  32 passed
Full test suite:              341 passed (309 + 32)
Real Fidelity gates:          ETF PASS 87752.561780/3  smallcap CLOSE 118551.211880/57 (daily zero-touch)
```

### PR4 real-minute-data status (decision 2)

**[更新 2026-07-22]** Minute tables now have real xtquant data: stock_minutes 18,777,900 rows + etf_minutes 46,940,329 rows (ingested 2026-07-21/22). PR4 real-data smoke PASS (510050 ETF × 2026-07-17 × full universe). The original PR4-time text below is retained as historical context (superseded by the 2026-07-22 closure):

~PR4 original: Minute tables (`stock_minutes`/`etf_minutes`) remain empty (0 rows). The daemon collector is full-market and time-uncontrollable (tushare rate-limited). Per decision 2, small-sample real smoke is the intended validation. Fell back to synthetic tests (32 passing). Real minute data ingestion is an independent task.~ — superseded; data ingested + smoke PASS.

### PR4 end-labeled convention (承接前置提醒 1)

**[更新 2026-07-22]** VERIFIED end-labeled on real data (09:30 bar O=H=L=C 集合竞价签名). The original "Cannot verify against real data (tables empty)" caveat is closed.

### PR4 known limitations / reports

- 15:00 bar = closing auction; run_daily('15:00') fires; 15:00 bar orders fill at close price (tested).
- Volume participation constraint: not implemented (consistent with daily engine; documented).
- `_load_minute_snapshots`: per-code query, thousands/day for full universe — known perf limit; batch optimization deferred to real-data phase.
- Scheduler fires before handle_data in minute profile; daily fires after (per master plan 7.24; documented).
- "Current bar visible" semantics: minute profile includes current bar (end-labeled completed history); daily profile excludes it (now-undefined conservative choice). Two profiles differ; documented.

## PR4 gate (closed)

PR4 implemented and verified. Next stage requires explicit user approval. PR5 must create the Skill skeleton and must not mix in minute engine changes.

## Minute data source switch to xtquant (2026-07-21)

User directed switching `stock_minutes`/`etf_minutes` authority source to xtquant, to enable real minute data ingestion for the PR3+PR4 minute stack (which was entirely built on the unverified end-labeled assumption with no real data contact).

### Authority decision (复权一致性)

xtquant native 3-stage复权 (none/front/back via `dividend_type`) direct-passthrough through aligner `qfq_native_passthrough:xtquant`. **Single-source lock**: `source_priority=["xtquant"]` for both `kline_1m` and `etf_minutes` tasks — no tushare/akshare fallback. Rationale: xtquant's front/back differs from tushare's adj_factor normalization; cross-source mixing silently corrupts复权 baseline. Explicit capability error (data stops on miniQMT downtime) > silent mixed-source pollution (undetectable wrong answers). This aligns with PR3's core philosophy.

### Implemented

- [x] `config/collector_tasks.json`: kline_1m + etf_minutes `source_priority=["xtquant"]`, `max_workers=8`, `_note` recording the decision.
- [x] `gui/tabs/config_editor_tab.py:42,44`: DEFAULT_SOURCE_MAP minute tables → xtquant (required for pyqt collection path; without this the guard rejects the collection).
- [x] `gui/tabs/quality_tab.py:41`: EXPECTED_EMPTY removed minute tables (post-ingestion emptiness should alert).
- [x] `daemon.py` `_run_with_source`: minute-table authority guard — rejects non-xtquant source write with explicit error.
- [x] `xtquant_adapter.py`: windowed download (`_parse_windows` 31-day chunks + `_fetch_one_code_windowed` + count probe guardrail). New reliability layer (adapter previously had full-range one-shot only).
- [x] `scripts/probe_xtquant_minute_depth.py`: pre-batch depth probe (1-2 sample codes).
- [x] `tests/test_minute_source_guard.py`: 11 tests (guard rejection, GUI default source consistency, config check, windowing logic).

### Verification

```text
Full test suite:  352 passed (341 + 11 new guard tests)
Real Fidelity gates: ETF PASS 87752.561780/3  smallcap CLOSE 118551.211880/57 (daily zero-touch)
```

### Known limitations

- Windowed download量级: ~72 API calls/code/2yr × 5800 codes ≈ 420k calls. Full-market backfill estimated days~weeks (miniQMT download speed constrained).
- miniQMT downtime: minute tables stop updating (data_status reflects honestly). Single-source lock means no silent tushare fallback.
- Historical depth: pre-batch probe determines actual xtquant cache depth; start_date adjusted if <2018.

### Post-ingestion smoke (end-labeled first real verification) — DONE 2026-07-22

Real xtquant minute data ingested (18.78M stock_minutes + 46.94M etf_minutes). 510050 ETF × 2026-07-17 × full-universe smoke PASS. end-labeled VERIFIED (09:30 bar O=H=L=C). Session filtering, gap-1 no-leak, match timing all confirmed on real data.

## Incident log: xtquant volume unit gap (2026-07-21, first real ingestion)

**Same class as the ETF drift incident**: each link in the chain is individually correct, but the first true end-to-end run exposed a seam.

**Symptom**: First real ingestion of `stock_minutes` from xtquant (kline_1m task) appeared to hang — `进度: 0/5202 (0%)` never advanced. Diagnostic logging traced it to the validator: 31,395 rows/code quarantined by `UnitCheck` (ratio = amount/(close×volume) ≈ 100, far outside [0.5, 2.0]). The validator itself was also unvectorized (~50 s per code × 5,202 codes = days), masking the real cause behind a "stuck" symptom.

**Root cause (two compounding faults)**:

1. **Validator performance** — `PreIngestValidator.validate` used per-row Python loops + `df[col].iloc[i]` across 13 rules. Fine for daily tables (hundreds of rows), pathological for minute tables (31,571 rows/code). Profile: OHLCLogic genexpr 7.1 s, UnitCheck genexpr 5.3 s, `reject()` × 60,330 calls 5.4 s.
2. **xtquant volume unit gap** — xtquant `get_market_data_ex` returns `volume` in **手 (1 lot = 100 shares)** per official docs, but schema mandates **股 (shares)**. `config/alignment_rules.json` had `volume ×100` configured for tushare/akshare daily tables, but **all 4 xtquant OHLC tables were empty**. The xtquant `_note` even claimed "vol=股" — an unverified assertion that was the cognitive root of the gap. Why daily never caught it: `stock_daily` is configured for tushare (correctly converted); xtquant was never used for ingestion until minute tables went live.

**Fix (config-only for the unit gap + vectorization for the validator)**:

- `config/alignment_rules.json`: xtquant `stock_daily`/`stock_minutes`/`etf_daily`/`etf_minutes` each got `unit_conversions: {volume: {factor: 100, _note: ...}}`. Top-level xtquant `_note` corrected to "vol=手". tushare minute tables' `_note` changed from the unverified "已是股/元" to an explicit "未经验证" warning (do **not** blindly copy ×100 — that's an unverified guess).
- `quantstudio/pipeline/validator.py`: full vectorization rewrite (boolean masks + sparse dict for hit_rules/error_vals). 31,571 rows: 51.57 s → 0.108 s (470×).
- `quantstudio/pipeline/daemon.py`: `as_completed` → `wait(timeout=30)` with count + heartbeat dual-trigger progress; ETA shows `--` when speed=0 (was misleadingly `0s`).
- Cleanup: deleted 1,150 xtquant rows from `stock_minutes` (pre-fix 手-unit data), purged 125,614 UnitCheck incident rows from `quarantine.db`. Watermark was already None (clean slate).
- Regression guards: `tests/test_xtquant_volume_unit.py` (8 tests, parametrized over 4 tables + reverse-test that UnitCheck still catches raw 手-unit data) and `tests/test_validator_behavior.py` (10 tests locking validator semantics).

**End-to-end verification (single code, real xtquant data)**: 600000.SH one week, 964 rows → 959 valid ratios all in [0.9966, 1.0035] (100% within UnitCheck threshold), 5 NaN rows all volume=0/amount=0 (suspended minutes, correctly skipped), `passed=964 rejected=0 UnitCheck=0`.

**Lesson**: A new source's first real end-to-end ingestion is itself a test that no config-level review can replace. Unit-convention assertions in `_note` fields are not evidence — they must be verified against real data on first use. The same pattern (per-link correctness, seam failure at integration) matches the ETF drift incident. Mitigation: when adding a new source, run a single-code end-to-end (fetch→align→validate→write) and inspect the ratio/unit distribution **before** unleashing full-market ingestion.

## Daily data source switch to xtquant (2026-07-21, config + code complete, awaiting user cutover)

**Status**: Code and config changes complete (381 tests pass). User will perform the actual data cutover (backup + sample compare + full DELETE + re-ingest + Fidelity gate) per `docs/strategy-compiler/xtquant-daily-cutover-runbook.md`. Until cutover completes, stock_daily/etf_daily storage still holds tushare data; testing continues on tushare.

**Scope**: Switch stock_daily and etf_daily authority source from tushare to xtquant, single-source locked (matching stock_minutes/etf_minutes).

**Changes**:

1. **Config**: `config/collector_tasks.json` — `kline_1d` and `etf_daily` tasks set `source=xtquant`, `source_priority=["xtquant"]` (single-source lock), `max_workers=8`.
2. **GUI default source**: `config_editor_tab.py` `DEFAULT_SOURCE_MAP["etf_daily"]` from `tushare` to `xtquant` (stock_daily was already xtquant). This closes the same-class gap found in the minute table switch (GUI default source must match daemon guard).
3. **Adapter isST fill**: `xtquant_adapter.py` `fetch_table` fills `isST` column for stock_daily (from ST sector membership, coarse label) and etf_daily (constant 0). Without this, etf_daily.isST.required=true triggers validator IsSTNull whole-table rejection (validator.py:176-180).
4. **Aligner valuation PIT JOIN**: `aligner.py` new `_derive_valuation_fields` — PIT ASOF JOIN stock_daily_valuation to fill peTTM/pbMRQ/turn (xtquant doesn't provide these; tushare era got them from daily_basic). `daemon.py` `_prepare_valuation_df` extended to read pe_ttm/pb/turnover_rate. This keeps duckdb_data_access.py:99 SELECT peTTM/pbMRQ/turn from getting NULL on xtquant segments.
5. **column_map preClose/suspendFlag**: `alignment_rules.json` xtquant stock_daily/etf_daily column_map added preClose (+ suspendFlag for stock_daily). Previously missing → pctChg degraded to compute_from_raw (junk values on ex-div days). `pctchg_source` set to `derived_from_front` (aligner Step 6 uses close_front.pct_change).
6. **DAILY_AUTHORITY guard**: `daemon.py` new guard symmetric to MINUTE_AUTHORITY, refuses non-xtquant writes to stock_daily/etf_daily to prevent fallback-chain source mixing.
7. **Doc fixes**: xtquant_adapter docstring/metadata corrected (vol=手, was wrongly "股").

**Regression guards**: `tests/test_xtquant_daily_switch.py` (11 tests) — config assertions (single-source, preClose mapping, pctchg_source), adapter isST fill (mock ST sector), aligner valuation PIT JOIN, pctChg derived_from_front within limit, etf_daily not rejected by IsSTNull, reverse-lock that missing isST still triggers IsSTNull (proves fix is adapter补列 not schema放宽).

**Field coverage after switch** (xtquant segment vs tushare era):
- Filled: OHLCV, amount, preClose, suspendFlag, isST, front/back 8 cols, pctChg (derived), peTTM/pbMRQ/turn (PIT JOIN valuation), is_st_reliable/is_delisting_risk (namechange PIT)
- NULL on xtquant segment: psTTM (valuation table has no source), ratio 8 cols (passthrough fills NULL per design)

**Data adapter layer** (`duckdb_data_access.py`): unchanged. Its fallback path (:146-165) already handles NULL peTTM/turn by joining stock_daily_valuation/stock_float_share. isST uses COALESCE(isST,0). Main valuation path goes through stock_daily_valuation PIT (:115-173), not affected by stock_daily column NULLs.

**Key risks and mitigations**:
- **Fidelity gate drift**: front-price baseline differs ~0.01% across sources; ETF gate ±1 yuan tolerance is sensitive. Mitigation: mandatory Fidelity gate after re-ingest; if drift, follow golden-baseline change protocol (§11), no silent golden updates.
- **Mixing window**: config switch + guard take effect immediately, but full DELETE + re-ingest is a separate manual step. Mitigation: runbook defines atomic sequence (stop daemon → backup → sample compare → DELETE → re-ingest → gate), no window between config and cutover.
- **per_stock speed**: full-market xtquant daily is slow (5000+ codes × 3 gets). Mitigation: max_workers=8, progress log + 30s heartbeat (fixed in prior round).

**Lesson (same class as ETF drift + minute volume gap)**: Per-link correctness again. The chain (config → adapter → aligner → validator → writer → data-access) is individually correct for tushare, but switching authority source exposed three seams at once: (a) GUI default source map lagged daemon guard (minute-table same-class gap), (b) xtquant doesn't provide peTTM/turn/isST that tushare era silently assumed came from the OHLCV source, (c) column_map missed preClose that xtquant does return. Each was invisible in isolation. Mitigation: when switching authority source, audit field coverage end-to-end (what does data-access layer SELECT, what does each source provide, what's the gap).

## DuckDB connection configuration conflict fix (2026-07-21)

**Symptom**: PyQt "数据源检查失败: Connection Error: Can't open a connection to same database file with a different configuration than existing connections" — blocked minute full-range ingestion.

**Root cause**: DuckDB disallows concurrent read_only and read_write connections to the same db file (even across threads). The collector process mixed both: `DuckDBWriter.write` opened short read_write connections (per-code upsert), while daemon internal queries (`_get_safe_watermark`, `_prepare_namechange_df`, `_prepare_valuation_df`, `_prepare_close_df`, namechange count checks) opened short `read_only=True` connections. Under per_stock 8-thread concurrency these crossed configurations triggered the conflict. Verified by isolation: two threads (read_write holder + read_only opener) deterministically reproduce.

**Fix** (`writers.py` + `daemon.py`): DuckDBWriter now maintains a single persistent read_write connection (`_shared_conn`, lazy-created, `_conn_lock`-guarded via `_ensure_shared_conn`). New `execute_read`/`read_df` methods reuse it. All 5 daemon internal `duckdb.connect(read_only=True)` call sites replaced with `self.writer.execute_read()` / `self.writer.read_df()`. Now the entire collector process only ever opens read_write connections (write short connections + one persistent shared), which DuckDB allows to coexist. Added `close()`/`__del__` for cleanup on collector rebuild.

**Thread-safety detail**: `_ensure_shared_conn` is lock-free (caller holds `_conn_lock`); `shared_conn` (external) self-locks. This avoids the reentrant-deadlock of execute_read → shared_conn both grabbing `_conn_lock`.

**Verification**: 6-thread concurrent read/read_df stress test passes (no conflict, no deadlock). 381 tests pass, zero regression.

**Lesson**: DuckDB's read_only/read_write mutual exclusion is process-global, not per-thread. Any mixed-config design (writer + reader in same process) is a latent deadlock. Unify to one config (read_write) within a process; isolate read_only consumers to separate processes (the GUI's `DbHelper` runs in the same process but only during non-collecting moments — if that becomes a conflict point later, route GUI reads through the writer's shared connection too).

## DuckDB connection conflict fix — second pass (2026-07-22)

**Symptom**: First full-range minute ingestion (5202 codes, 18,777,900 rows, 2598s) succeeded after the initial connection fix, but the post-ingestion `_run_full_quality_audit` failed: `quality_audit.py:53 duckdb.connect(read_only=True)` conflicted with the writer's persistent read_write `_shared_conn`. Task marked failed despite successful ingestion.

**Root cause (incomplete first-pass fix)**: The initial fix only covered the 5 `duckdb.connect(read_only=True)` sites in `daemon.py` proper. Two independent modules still opened their own read_only connections inside the collecting process:
- `quality_audit.py:53` (`DataQualityAuditor.run`, mandatory post-ingestion audit) — direct trigger
- `aligner.py:957` (`_fix_pctchg_from_db`, pctChg DB fallback) — latent, would surface when daily tables switch to xtquant (pctChg derived_from_front path hits DB fallback on batch-first rows)

**Fix (connection injection pattern)**:
- `DataQualityAuditor.__init__` accepts optional `shared_conn`; `run()` uses it if provided, else opens its own read_only (CLI compatible). daemon's `_run_full_quality_audit` passes `self.writer.shared_conn()`.
- `FieldAligner.__init__` accepts optional `shared_conn_provider` (callback returning the persistent connection); `_fix_pctchg_from_db` uses it if set, else falls back to read_only (CLI/backtest compatible). daemon's `from_configs` injects `writer.shared_conn`.
- Both modules remain decoupled (no hard writer dependency; injection optional, CLI behavior unchanged).

**Unchanged (out of collector process scope)**: `exporter.py`, `duckdb_data_access.py` (backtest provider), `ptrade_baseline.py`, `run_ptrade_strategy.py` — these run in separate processes/CLI invocations, no concurrent read_write connection, read_only is safe.

**Lesson**: When fixing a connection-configuration conflict, audit **all** connection sites reachable within the process, not just the obvious ones. Independent modules that "happen to" open their own connections are latent conflicts. The injection pattern (optional shared_conn param) preserves decoupling while allowing reuse — preferred over forcing every module to depend on writer.

**Side note (environment)**: During verification, 5 tests failed with "Cannot open file...另一个程序正在使用" — traced to a residual python process (PID 35168) holding the db file handle after the user's ingestion run. Once that process exited (OS released the lock), all 381 tests passed. Not a code issue; reminder that PyQt/daemon processes must clean-exit to release DuckDB file locks on Windows.

## xtquant back factor consistency check fix (2026-07-22)

**Symptom**: Post-ingestion quality audit reported `stock_minutes/AdjustmentFactorConsistency: 419 back` as the only error, blocking the "all checks pass" status despite 18.78M rows ingested cleanly.

**Root cause** (data-driven, not a code bug): xtquant's `dividend_type="back"` for minute bars uses **per-tick cumulative backward-adjustment**, not a single daily factor. Within one minute bar, open/high/low/close originate from different tick moments, so each gets a slightly different back factor (2-4% spread). The quality rule `AdjustmentFactorConsistency` assumed "one factor per bar per side" (true for tushare's daily `adj_factor`), threshold 2%, so xtquant minute back tripped it 419 times.

**Evidence**:
- Direct adapter probe (`600829.SH` 2026-07-14): same bar `r_o=9.030, r_h=8.995, r_l=9.058, r_c=9.054` — OHLC factors genuinely differ by ~0.5-1% per bar.
- Distribution across 18,777,900 xtquant minute rows: ≤2% deviation = 18,777,481 (99.998%); 2-3% = 274; 3-5% = 145; >5% = **0**. Max deviation 3.99%.
- Cross-check: xtquant minute **front** factor inconsistency = 0; tushare daily back/front = 0. So it's specifically xtquant minute back's algorithm, not a general corruption.

**Fix** (`quality_audit.py` `_audit_prices`): `AdjustmentFactorConsistency` now distinguishes by `data_source`:
- xtquant rows: threshold 5% (covers the 2-4% algorithmic spread, still catches real misassignment which would be >10%)
- Other sources (tushare/baostock/akshare) and xtquant front: threshold 2% unchanged
- Tables without `data_source` column: keep 2% blanket

**Verification**: re-ran on the live DB — xtquant back bad rows 419 → **0**, other-source bad rows 0 (unchanged). Full audit `errors: 0` (258 checks). 381 tests pass.

**Lesson**: When a quality rule fires on a new data source, first characterize the source's algorithm against the rule's assumption before "fixing data" or relaxing thresholds blanket. xtquant's per-tick back-adjustment is a legitimate algorithm; the rule's per-bar-uniform-factor assumption was the mismatch. Source-aware thresholding preserves the rule's strength for sources where the assumption holds, while accommodating sources where it doesn't. The >5% guard ensures real corruption (e.g., front factor mistakenly written to back columns) still gets caught.

## PR4 real-minute closure (2026-07-22): end-labeled verified + GIL defect fixed

Two load-bearing items closed during PR4 real-data smoke (required before PR5):

**1. end-labeled convention VERIFIED** (project's last unverified assumption).

Direct miniQMT probe + DB query: 600000.SH × 6 days + 510050 ETF × 1 day, all 241 bars/day, first 09:30, last 15:00. The 09:30 bar has O=H=L=C (call-auction single opening price) — definitive proof of end-labeled (call auction 09:25-09:30 result, not 09:30:00-09:30:59 continuous). 09:31 bar is first continuous-auction bar (O≠H≠L≠C). 14:59 vol=0 (closing auction begins 14:57), 15:00 bar has real volume (closing auction 14:57-15:00). PR3 `intraday_windows` / PR4 `_MinuteScheduler` / `attach_bar` all built on the correct assumption — no cascade review.

Design clarification (not a bug): engine processes **240 continuous-auction bars** (09:31-11:30 + 13:01-15:00); the 09:30 call-auction bar is intentionally excluded from the minute loop (opening price flows through daily `attach_day` via preClose/open). `handle_data`=240 is correct.

**2. GIL crash defect FIXED** (deferred perf item upgraded to functional blocker).

Original `_load_minute_snapshots` (per-code loop × 5525 codes × 4 DB calls = ~22k roundtrips/day) caused `Fatal Python error: PyEval_SaveThread: GIL must be held` on real full-universe data — duckdb C extension GIL corrupted after thousands of execute/fetchdf on persistent read-only connection. Fix: `query_minute_bars_by_range_batch` (new method, one SQL per table, ≤2 roundtrips/day regardless of universe size) + `iter_trading_days_in_range` process cache. Real smoke (510050 × 2026-07-17 × full universe) now passes: GIL crash gone, 240 bars processed, 09:31 first / 15:00 last, before_trading_start leak-free.

**Lesson**: A deferred "performance optimization" can be a latent functional blocker. The per-code loop "worked" on synthetic data (7 bars) but crashed on real data (5525 codes). When deferring optimizations, mark whether they've ever run on production-scale data — if not, they're unverified, not "merely slow".

## ZCode handoff

The executable handoff document is docs/strategy-compiler/zcode-handoff-20260721.md; repository entry point is ZCODE_START_HERE.md.
## QFQ A' PR2 Commit 2 Review FAIL (2026-07-23)

- Worktree/branch: `D:\miniQMT策略实盘\QuantStudio-pr2` / `codex/qfq-a-pr2`.
- Commit: `58200514500080e697cc25967229a65f216f48aa`, parent `c99fcb230b0fc2689927fe72931b8a3bba30b50e`.
- Merge/scope: feature branch only, not merged to `main`; worktree clean; four-file 1352-line delta limited to qfq_revision, audit CLI integration, and tests.
- Tests: four-file py_compile PASS; targeted 87 passed; full 583 passed / 8 skipped / 5 failed, with the five failures matching the known isolated DB/output fixture baseline. A reviewer trigger-based counterexample confirmed rollback after event insertion is functionally atomic.
- Review result: **FAIL / NEEDS CORRECTIVE COMMIT**. PR2 closeout/main merge is not authorized.
- Material blockers:
  1. `load_observations_from_adj_factor()` silently drops NULL factor rows, bypassing finite-value validation; a target ETF with NULL factor produces a completed run with observed_count=0 instead of an audit failure.
  2. `_bare_code(None)` accepts the logical code `NONE` because the shared bare-code helper stringifies None.
  3. CLI persistence failures are recorded under a newly generated `r_fail_*` rather than the failed attempt's run_id, breaking run traceability and terminal run-id semantics.
  4. `record_failed_run()` does not validate ETF-only asset_type or epoch-ms as_of and can persist invalid ledger rows.
- Test gaps: committed rollback injection fails before run/event/observation writes; add a failure after event insertion. Revised-path adj_factor immutability should compare a snapshot taken after the test's manual source update against post-persist rows.
- Accepted direction: revision-vs-LAG semantics, explicit three-table schema, default dry-run, explicit persist boundary, BEGIN IMMEDIATE transaction, seed/unchanged/revised behavior, epsilon/as-of handling, baseline preservation, and isolated E2E evidence.
- Next gate: narrowly scoped corrective commit fixing the four blockers and adding counterexample tests; no PR2 closeout or main merge before PASS.


## Daemon v3 PR1 review status (2026-07-23)

- Workspace/branch: `D:\miniQMT策略实盘\QuantStudio` / `main`.
- Commit: `e78ec25ba9c136982d46fc58ca2862bc40b68d2c` (`daemon v3: decouple resident collector into independent OS process`).
- Git status: commit is directly on `main`; unrelated GUI/theme/source changes and `scripts/audit_qfq_staleness.py` remain uncommitted.
- Test evidence: critical-file `py_compile` PASS; full repository suite `486 passed in 32.70s`; live daemon identity/parent-detachment/single-instance lock/idle DuckDB-open checks PASS.
- Review result: **FAIL / NEEDS FIX**. The stage is not accepted and QFQ PR2 must not start yet.
- Material blockers:
  1. `run_one_cycle()` does not consume `daemon_stop.request` while a cycle is running, so graceful stop is not observed at task boundaries.
  2. An interrupted task traversal can still be persisted as `completed`.
  3. `collector.close()` failure is swallowed and the run is still persisted as `completed`, contradicting the strict completion contract.
  4. `publish_status()` continues and overwrites status when stale-process identity verification returns `AccessDenied`.
  5. No versioned daemon-specific tests were committed; current semantic failures were reproduced with isolated inline tests.
- Next gate: corrective commit + committed daemon lifecycle tests + 2026-07-23 21:00 real scheduling/runtime validation, then re-review. No approval for QFQ A' PR2 before this gate passes.
## Daemon v3 PR1 re-review PASS (2026-07-23)

This supersedes the earlier `e78ec25` FAIL entry.

- Workspace/branch: `D:\miniQMT策略实盘\QuantStudio` / `main`.
- Commits: base `e78ec25ba9c136982d46fc58ca2862bc40b68d2c`; corrective `bdece953b6811150d97336dfc11a8be31ca57767`.
- Merge status: both commits are directly on `main`.
- Accepted scope: detached daemon lifecycle, GUI token handshake, psutil identity verification, single-instance and collector-run locks, idle DuckDB release, task-boundary stop consumption, strict interrupted/completed/failed_cleanup state, AccessDenied startup abort, bounded bootstrap behavior, and exact git-commit runtime logging.
- Test evidence: `tests/test_daemon_lifecycle.py` 23 passed; full suite 509 passed in 26.24s.
- Runtime evidence: PID 50528 launched at 20:44:16 from exact commit `bdece95`; identity `alive`; parent process absent; bootstrap remained 0 bytes; idle DuckDB read-only open succeeded; real `daily_time=21:00` cycle triggered at 21:04:20 and wrote `daemon_run_state.status=running`; first `valuation_daily` task succeeded.
- Review result: **APPROVED / PASS**. QFQ A' PR2 may start from a clean branch/worktree based on `bdece95`.
- Remaining risks: current full cycle is still running and final completed/task summary remains operational monitoring; stop is guaranteed at task boundaries but not yet by an independent watcher inside a single long task; POSIX runtime is not validated; the current main worktree has unrelated dirty GUI/theme/source files and must not be used as the PR2 write set.
- Next stage: create an isolated clean PR2 worktree from `bdece95`; do not perform PR2 writes against the live DuckDB until the active 21:00 collection releases `collector_run.lock`.
## QFQ A' PR2 isolation gate PASS (2026-07-23)

- Worktree: `D:\miniQMT策略实盘\QuantStudio-pr2`.
- Branch: `codex/qfq-a-pr2`.
- Base HEAD: `bdece953b6811150d97336dfc11a8be31ca57767`.
- Merge status: no PR2 commit yet; not merged to `main`.
- Worktree status: only `?? scripts/audit_qfq_staleness.py`.
- Audit script SHA-256 in source and target: `eba1f0d7f51d18cb2ee934ec1b1f34ef4ec670fef859fa4da1fa8a2723f904f6`.
- Isolation evidence: main worktree remains dirty but unchanged; PR6b-2A worktree remains at `21382b3`; PR2 `DATA_ROOT` resolves to `D:\miniQMT策略实盘\QuantStudio-pr2\data`; no live DB copied and no `QUANTSTUDIO_DATA_ROOT` override.
- Runtime separation: main daemon PID 50528 remains alive and owns the active 21:00 collection; PR2 tests must use temporary DATA_ROOT/DB and mocked xtquant, and must not touch the live collector lock.
- Review result: **APPROVED / PASS** for the isolation checkpoint. Commit 1 audit-baseline work may begin.
- Commit 1 gate: fix SQLite `adj_factor` ETF selection, real ETF universe, date/as-of semantics, keyed merges, NULL mismatch statistics, parameterized SQL/market-code routing, deterministic full-history reporting, and add hermetic tests. Commit 1 must contain audit script + tests only, no repair writes.
## QFQ A' PR2 Commit 1 review FAIL (2026-07-23)

- Worktree/branch: `D:\miniQMT策略实盘\QuantStudio-pr2` / `codex/qfq-a-pr2`.
- Commit: `27619ef537fb0492d1fd5e3bee2e4fde446fce0a`, base `bdece95`.
- Merge status: feature branch only; not merged to `main`.
- Scope evidence: exactly two new files (`scripts/audit_qfq_staleness.py`, `tests/test_audit_qfq_staleness.py`); worktree clean after commit; main dirty worktree unchanged.
- Tests: audit suite 33 passed; full suite 529 passed / 8 skipped / 5 failed. The five failures are the known isolated-worktree DB/output fixture failures, not introduced by this commit.
- Review result: **FAIL / NEEDS FIX**. Commit 2 is blocked.
- Material blockers:
  1. Stock candidate SQL assumes YYYYMMDD while live `stock_dividend.ex_date` is epoch-ms; future rows can consume the LIMIT and hide current active events.
  2. ETF `no_record` is computed from `codes_with_change`, so stable ETFs with factor history are falsely reported as having no records.
  3. `_default_etf_universe()` claims Canonical ∪ xtquant but implements Canonical only.
  4. `null_mismatch_cells` and `numeric_diff_cells` count OR-combined rows, not cells across four price columns.
  5. `canonical_earliest` is calculated after inner merge and duplicates overlap earliest; fresh/canonical coverage metadata is incomplete.
  6. Factor-change epsilon remains hard-coded at 0.001 and can miss small true revisions.
  7. Several tests are vacuous or schema-inaccurate: rolling window does not call production logic, injected provider is not asserted, union is not tested, and stock candidate integration covers YYYYMMDD but not the live epoch-ms schema.
- Next gate: corrective audit commit with real-schema and counterexample tests; no revision-detection/audit-schema work before re-review passes.
## Strategy Compiler Skill MVP delivery route approved (2026-07-23)

The user approved a compressed four-gate delivery route. The original sequencing
note (`docs/strategy-compiler/skill-mvp-delivery-roadmap-20260723.md`) was
referenced here as an "authoritative addendum" but that file is not present in
the repository. Authoritative sequencing is therefore this section itself plus
the G1/G2/G3/G4 gate records below; do not treat the roadmap filename as a
live document. (G2 CP3 note: CP3 = an independent hand-written Reference Oracle
per `pr6b2a-plan-etf-rotation.md` §CP3, with anti-circular-validation — the
Oracle must not be fed from Codegen/Renderer output.)

### Delivery boundary

The first formal Skill MVP release is limited to:

1. G1 — `next_open` basket engine semantics;
2. G2 — CP3 frozen Reference Oracle package;
3. G3 — Local/Strict-PTrade dual Renderer, dual stub, and strategy package;
4. G4 — CLI E2E, Skill install/validation, version `0.3.0-mvp`, and release documentation.

The following no longer block the first Skill MVP release and are deferred as independent product increments: CP5a GUI integration, CP5b real PTrade result import/comparison, PR7 automatic Fidelity closure, PR6b-2B PIT-safe dynamic ETF universe, Tick/L1/L2, and deployment packaging.

### Current accepted state

- Existing Skill version: `0.2.0-pr6b1`.
- `main`: PR6a and PR6b-1 delivered.
- Feature worktree/branch: `D:\miniQMT策略实盘\QuantStudio-pr6b2a` / `pr6b2a-etf-rotation`.
- Feature HEAD: `893143063f33450c5abe1ea670d126f494cf2197`; not merged to `main`.
- CP1/CP2: PASS.
- CP3: BLOCKED. The partial Oracle fixes are retained, but final signal/order/NAV reference artifacts depend on G1.
- Verified test evidence at `8931430`: CP3 42 passed; selective compiler suite 233 passed; full repository 622 collected / 609 passed / 8 skipped / 5 failed. The five failures are the known isolated-worktree DB/output fixture failures.

### Next gate

G1-I commit `799fb43` failed Review. Submit one corrective implementation commit on `codex/next-open-basket`; do not merge to `main` and do not start G2. Keep `pr6b2a-etf-rotation` untouched.

### Review compression rule

Target remaining formal Reviews: G1-D design, G1-I implementation, G2 Reference closure, G3 package closure, and G4 release — five Reviews across four major gates. Micro-fixes are reviewed only after a gate fails; test-count growth alone is not acceptance evidence.
## G1-D next_open basket design Review FAIL (2026-07-23)

- Worktree/branch: `D:\miniQMT策略实盘\QuantStudio-basket-design` / `engine-basket-design`.
- Base: `main` commit `bdece95`.
- Delivery commit: `be909fc14490846be16c6de446bc71182f8e5d91`.
- Scope/merge status: exactly one new design document (`docs/strategy-compiler/engine-basket-rebalance-design.md`, 340 lines); no runtime/test code changes; worktree clean; not merged to `main`; isolated from `pr6b2a-etf-rotation`.
- Review result: **FAIL / NEEDS CORRECTIVE DESIGN**. G1-I implementation is not authorized.
- Material blockers:
  1. Automatic basket context around every `handle_data` call contradicts the promise that existing next_open non-basket behavior remains unchanged. Activation must be explicit and versioned; the supported callback/profile boundary must be frozen rather than left in §15.
  2. Phase 2 double-counts sell proceeds: existing `_execute_sell` already adds net proceeds to `account.cash`, while the design computes `available_cash = account.cash + realized_sell_proceeds`.
  3. The documented “all-or-nothing” buy policy is not atomic: buys are executed sequentially and only remaining buys are rejected after a shortage. It must preflight all T+1 actual buy prices/shares/fees before executing any buy.
  4. Cancel/expire incorrectly says no reservation needs restoration, although basket sell orders pre-debit `pending_sell_shares`. Exact sell reservation refund is mandatory.
  5. The price-limit sell direction is reversed. A limit-up blocks buys, while a limit-down blocks sells; the current table states the opposite for sells.
  6. Deterministic sorting by ETF pool index is not implementable in the generic engine because order calls do not carry the strategy pool. Use an engine-visible frozen key/sequence and define duplicate/conflicting order handling.
  7. Basket status semantics are ambiguous: sells-filled/buys-rejected cannot be indistinguishably marked `rejected`; cancelled/expired, sell-only, buy-only, mixed independent/basket, and partial market-state outcomes need explicit status rules.
  8. Version activation and scope are incomplete: define the EngineConfig/Spec switch for `0.4.0-next_open_basket`, daily/minute interaction, old-engine behavior, target-value direction changes at T+1, and same-day multi-basket policy.
- Required corrective design: fix the accounting/state-machine contradictions, freeze all §15 items, expand the test matrix with activation compatibility, cash-delta accounting, atomic buy preflight, reservation refund, mixed-order priority, status semantics, duplicate code/direction, and daily/minute gating.
- Next gate: corrective design-only commit + independent Review. No BacktestEngine/PtradeAPI code before PASS.
## G1-D corrective basket design Review PASS (2026-07-23)

This supersedes the G1-D `be909fc` FAIL entry.

- Worktree/branch: `D:\miniQMT策略实盘\QuantStudio-basket-design` / `engine-basket-design`.
- Base: `main` commit `bdece95`.
- Corrective commit: `8f7d0e4dd04304f259816da117a63dcaec268c75`.
- Merge status: feature branch only; not merged to `main`.
- Scope evidence: exactly one modified design document (`docs/strategy-compiler/engine-basket-rebalance-design.md`); 259 insertions / 189 deletions; zero Python/test runtime changes; worktree clean; isolated from `pr6b2a-etf-rotation`.
- Accepted design scope: explicit default-off `rebalance_mode`; daily+next_open+callback_basket activation for `0.4.0-next_open_basket`; minute basket BLOCK; no sell-proceeds double count; mandatory-sell success gate; T+1 actual-price atomic buy preflight; reservation refund; corrected limit directions; bare-code uniqueness/sorting; direction-lock MVP restriction; status truth table; independent-order priority; old-engine rejection; 25-item test matrix.
- Review result: **APPROVED / PASS for G1-D**. G1-I implementation may begin.
- Binding G1-I acceptance constraints:
  1. Preflight is read-only. `_execute_buy` (or one explicitly named execution helper) is the sole cash mutation for buys; implementation must not pre-deduct `actual_required_cash` and then call `_execute_buy` again.
  2. Cancelling any mandatory sell must make the basket buy leg ineligible (`mandatory_sell_cancelled`) or cancel the whole basket; remaining buys must not execute while the old position remains.
  3. Runtime `Order` visibility must include `basket_id`. Strategy `trigger_reason` remains a separate G2/reference-layer field unless an explicit API is designed; the engine must not fabricate it from order rejection reasons.
  4. `basket_id`/sequence formatting and ordering must be lexically deterministic (fixed-width sequence or numeric sort).
  5. G1-I engine scope is daily-bar basket semantics and engine-level reporting/tests. Strategy Spec/Renderer/package plumbing may be finalized in G3 to avoid conflicts with `pr6b2a-etf-rotation`, but `EngineConfig.rebalance_mode` and engine semantics version must be real and tested in G1-I.
  6. Add tests for mandatory-sell cancel abort, buy preflight no-mutation/sole execution deduction, `Order.basket_id` visibility, deterministic basket sequence, and unexpected post-preflight execution failure handling, in addition to the 25-item matrix.
- Branch guidance: create a new implementation branch/worktree (recommended `codex/next-open-basket`) from `8f7d0e4`; do not implement on the live main worktree and do not touch `pr6b2a-etf-rotation`.
- Next gate: one complete G1-I implementation delivery with code, committed hermetic tests, original targeted/full-suite summaries, runtime artifacts, and merge status. Do not split phases A-F into separate formal Reviews.
## QFQ A' PR2 audit-fix Review FAIL (2026-07-23)

- Worktree/branch: `D:\miniQMT策略实盘\QuantStudio-pr2` / `codex/qfq-a-pr2`.
- Corrective commit: `02c94f2282e36d0639b0b3ce1070deded3bf87b7`, parent `27619ef537fb0492d1fd5e3bee2e4fde446fce0a`, project base `bdece953b6811150d97336dfc11a8be31ca57767`.
- Merge/scope: feature branch only, not merged to `main`; worktree clean; exactly two modified files (audit script + audit tests), 469 insertions / 284 deletions.
- Tests: audit suite 38 passed; PR2 full suite 534 passed / 8 skipped / 5 failed. Clean detached `bdece95` baseline reproduced the same five failures with 496 passed / 8 skipped, confirming they are pre-existing isolated-worktree DB/output fixture failures.
- Review result: **FAIL / NEEDS ANOTHER AUDIT-FIX**. Commit 2 remains blocked.
- Remaining blockers:
  1. Default production path calls `_default_etf_universe(..., None)`; CLI never wires `XtquantAdapter.get_etf_codes`, so claimed Canonical ∪ xtquant remains canonical-only outside injected tests.
  2. Real xtquant ETF codes are suffixed while Canonical/adj_factor uses bare codes; union lacks suffix-to-bare normalization, causing duplicates and false `no_record` classifications.
  3. Mixed YYYYMMDD + epoch-ms stock selection is not supported by SQL. The named mixed-format test explicitly accepts omission of the YYYYMMDD row and asserts only the epoch-ms row.
  4. ETF factor queries lack an as-of upper bound, so future factor rows can be reported as changed/recorded for a historical as-of date.
- Confirmed fixes retained: epoch-ms active/future LIMIT separation, stable/no-record base separation, per-cell mismatch counts, canonical/fresh/overlap metadata, factor epsilon, and direct window-function tests.
- Next gate: audit-only corrective commit with production-default union wiring, real code normalization, exact mixed-format assertions, and future-factor exclusion tests; no revision-detection/audit-schema work before PASS.
## QFQ A' PR2 audit-fix2 Review FAIL (2026-07-23)

- Worktree/branch: `D:\miniQMT策略实盘\QuantStudio-pr2` / `codex/qfq-a-pr2`.
- Corrective commit: `426736144182b1204966686e80e144db06b37bb6`, parent `02c94f2282e36d0639b0b3ce1070deded3bf87b7`.
- Merge/scope: feature branch only, not merged to `main`; exactly two committed files (audit script + audit tests), 269 insertions / 34 deletions. Worktree is not strictly clean because `docs/pr2-audit-fix2-handoff.md` is untracked.
- Tests: audit script py_compile PASS; audit suite 43 passed; full suite 539 passed / 8 skipped / 5 failed. The five failures match the previously verified clean `bdece95` isolated baseline and are unrelated.
- Review result: **FAIL / NEEDS AUDIT-FIX3**. Commit 2 remains blocked.
- Remaining blocker: `_build_default_xtquant_etf_provider()` assumes `config/sources_config.json["sources"]` is a list of objects with `name`, but the committed config uses a dict keyed by source name. The real builder therefore raises `'str' object has no attribute 'get'`, catches it, and returns an empty provider, leaving the production default universe canonical-only.
- Test gap: `test_default_run_audit_path_calls_xtquant_provider` monkeypatches the builder itself with a sentinel, so it validates wiring but not real config parsing/adapter construction and cannot catch the production failure.
- Confirmed fixes retained: suffix-to-bare normalization/deduplication, adj_factor as-of upper bounds, future-only no-record classification, changed-code deduplication, and explicit epoch-ms-only stock-dividend scope.
- Next gate: audit-fix3 using the actual dict config schema plus a hermetic builder test with real-shaped temporary config and a fake adapter; no Commit 2 work before PASS.
## G1-I basket engine implementation Review FAIL (2026-07-23)

- Worktree/branch: `D:\miniQMT策略实盘\QuantStudio-basket-impl` / `codex/next-open-basket`.
- Base: approved G1-D commit `8f7d0e4dd04304f259816da117a63dcaec268c75`.
- Delivery commit: `799fb43a7a80cfded6d0214cfa2ec2931ffcea56`.
- Merge status: feature branch only; not merged to `main`.
- Scope: `backtest_engine.py`, `ptrade_api.py`, and new `tests/test_engine_basket_rebalance.py`; 1597 insertions / 15 deletions; worktree clean; `pr6b2a-etf-rotation`, GUI, daemon, QFQ, and config/theme untouched.
- Test evidence: basket suite 41 passed. Full repository independently rerun: 550 collected / 537 passed / 8 skipped / 5 failed. The five failures are the known isolated-worktree calendar/fidelity artifact failures, but the delivery report's `522 passed / 8 skipped` summary is not the actual full pytest summary.
- Review result: **FAIL / NEEDS CORRECTIVE IMPLEMENTATION**. No merge and no G2 authorization.
- Reproduced blockers:
  1. `EngineConfig.rebalance_mode` is ignored when callers pass `config=` without also passing the duplicate constructor argument; a config containing `callback_basket` produces `engine.rebalance_mode == legacy` and `basket_active == False`.
  2. `run_daily` callbacks execute while `_current_basket` is active, contradicting the approved boundary that run_daily orders remain legacy.
  3. Runtime `Order` has no `basket_id`; basket pending/filled/rejected events are not appended to `_today_orders`; `get_open_orders`, `get_order`, and public `cancel_order` only inspect legacy `_pending_orders`. Basket order lifecycle is therefore not publicly visible/cancellable.
  4. Cancelling a mandatory sell merely removes it. The buy leg can still execute while the old position remains. Reproduced: old `600000` volume 1000 remained while new `600001` volume 400 was bought and basket status became completed.
  5. After atomic preflight, an unexpected first `_execute_buy` failure does not reject remaining buys. Reproduced: first buy rejected, second buy filled, basket partial; required `execution_failed_after_preflight` behavior is absent.
  6. If `handle_data` raises after creating orders, the exception path calls `submit_basket`, so a partially constructed failed callback can still execute on T+1. It must abort/refund instead.
  7. The 41-test suite omits the binding G1-I tests for config propagation, run_daily legacy routing, full Order/get/cancel lifecycle, mandatory-sell cancel abort, post-preflight failure, and callback-exception abort.
- Corrective gate: one audit-fix commit on the same implementation branch, with hermetic counterexample tests first. Do not split into multiple formal Reviews.
## G1-I basket engine corrective Review PASS (2026-07-23)

- Date: 2026-07-23.
- Worktree/branch: `D:\miniQMT策略实盘\QuantStudio-basket-impl` / `codex/next-open-basket`; worktree clean.
- Base and commit: approved G1-D base `8f7d0e4`; corrective commit `bcdc85d` (parent `799fb43`).
- Accepted scope: `quantstudio/backtest/backtest_engine.py`, `quantstudio/backtest/ptrade_api.py`, and `tests/test_engine_basket_rebalance.py`; commit delta `592 insertions / 22 deletions`; no GUI/daemon/QFQ/config/pr6b2a changes.
- Accepted fixes: config rebalance-mode resolution, run_daily/before_trading_start legacy routing, basket Order/public lifecycle visibility, mandatory-sell cancel abort, post-preflight execution-failure abort, and callback-exception basket abort.
- Test evidence: basket suite `59 passed`; feature full suite `555 passed / 8 skipped / 5 failed`; clean baseline `8f7d0e4` full suite `496 passed / 8 skipped / 5 failed`; the same five calendar/output-fixture failures occur on both and no new failure is introduced.
- Generated/runtime evidence: isolated feature worktree lacks the fidelity golden artifacts, so four existing fidelity tests remain non-hermetic; this is retained as a known risk, not accepted as a new G1-I failure.
- Merge status: **not merged to `main`**. Review authorizes the merge only; no merge is claimed.
- Remaining risks: run_card basket metadata, CP3 Oracle/reference closure, and trigger_reason/Spec/Renderer/manifest propagation remain post-G1 work; full suite fixture hermeticity remains open. The current `main` worktree has unrelated dirty GUI/config/QFQ/docs changes, so merge must use a clean integration worktree or safely preserve those changes first.
- Review result: **PASS / APPROVED**.
- Next stage: merge `bcdc85d` into `main` and verify with Git; only then start G2 CP3 Reference closure.

---

## QFQ A' PR2 audit-fix3 Review PASS (2026-07-23)

This supersedes the PR2 Commit 1 / audit-fix / audit-fix2 FAIL entries while retaining them as history.

- Date: 2026-07-23.
- Worktree/branch: `D:\miniQMT策略实盘\QuantStudio-pr2` / `codex/qfq-a-pr2`.
- Accepted commit: `c99fcb230b0fc2689927fe72931b8a3bba30b50e`; chain `c99fcb2 → 4267361 → 02c94f2 → 27619ef → bdece95`.
- Merge status: feature branch only; not merged to `main`; worktree clean after review/tests.
- Accepted scope: audit script + committed hermetic audit tests only. The final corrective delta is two files, 198 insertions / 35 deletions. Accepted cumulative behavior includes real dict-schema xtquant provider construction, bare-code Canonical∪xtquant ETF universe, epoch-ms stock candidates, deterministic adj_factor as-of classification, changed-code deduplication, keyed front merges, per-cell NULL/numeric statistics, separate canonical/fresh/overlap coverage, parameterized factor epsilon, and accurate side-effect/window reporting. Revision detection, audit schema, and repair writes are not part of this accepted commit.
- Tests: audit script py_compile PASS; targeted audit suite 46 passed; full suite 542 passed / 8 skipped / 5 failed. The five failures are the previously reproduced isolated-worktree Canonical DB/output fixture failures and are not introduced by PR2.
- Runtime evidence: real project `config/sources_config.json` plus a fake adapter exercised the unmodified builder and returned `['510050.SH', '159919.SZ']`; captured config was `enabled=True, qmt_path='', name='xtquant'`. No live QMT, formal DuckDB, or collector lock was touched.
- Review result: **APPROVED / PASS**. Commit 2 (revision detection + audit schema) is authorized.
- Remaining risks: explicit canonical-only degradation when xtquant is unavailable/disabled; epoch-ms-only stock-dividend contract; five repository tests remain non-hermetic; branch is not merged to `main`.
- Next gate: implement Commit 2 in the same isolated branch/worktree with auditable scope, hermetic tests, and no formal Canonical repair writes; submit commit/diff/test/runtime evidence for Review.
## G1-I final Review PASS (2026-07-23)

- Main/worktree: `D:\miniQMT策略实盘\QuantStudio` / `main`, clean; PR2 merge commit=`62870d4`; current main HEAD=`b3da10b` (latest docs-only descendant; ancestry `bcdc85d → 09bf151 → fa3ee1c → 62870d4 → b2d940a → 30e4a9a → 271bb0e → a85835f → b3da10b`; this docs commit itself advances main by one, accepted as docs-only descendant per reviewer).
- Merge evidence: `bcdc85d` is an ancestor of `main`; G1-I entered main through the prior `--ff-only` path. The later `62870d4` merge is a disjoint QFQ PR2 integration and does not modify the G1 basket files.
- WIP preservation: `codex/main-dirty-wip` HEAD=`9858c26`, retaining `7f223af`, `faabc20`, and `9858c26`; no WIP changes are merged to main.
- Accepted G1-I scope: G1-D design v2, basket engine/API implementation, 6 corrective blocker fixes, and 59 basket tests.
- Test evidence: current main basket suite `59 passed`; prior full-suite evidence `563 passed / 5 failed` had only resident-DB/calendar environment failures and no G1-I regression. The subsequent QFQ merge is disjoint from G1-I; no G1 basket file changed after `bcdc85d`.
- Review result: **PASS / APPROVED**. G1-I is complete and G2 CP3 Reference closure is authorized, but G2 has not started.
- Remaining risks: CP3 Reference fixtures/digests/Oracle, G3 dual Renderer/package, G4 CLI E2E/release, and known non-hermetic DB/calendar tests remain open.
---

## PTrade profile 1.5.0 runtime-contract repair (2026-07-25)

- Trigger: customer-supplied real IQEngine traceback carries platform timestamp `2026-07-26` (future relative to the review date) and fails in `initialize` with `NameError: set_backtest is not defined`; the same run also reports `LogEngine` has no `warn` attribute.
- Root cause: the Skill component catalog exposed QuantStudio-local `set_backtest`/`is_trade`, but the PTrade signature profile did not classify them as `LOCAL_ONLY`; the validator also had no explicit logger-method contract, so a dual-target source containing both defects could receive PTrade PASS.
- Generic repair: PTrade profile `1.5.0` marks `set_backtest` and `is_trade` local-only, validates `log.debug/info/warning/error/critical`, blocks `log.warn`, and updates Skill rules plus README/strategy-toolbox/prompt-engineering references.
- Regression evidence: PTrade validator targeted suite `17 passed`; related Skill/delivery/release suites `101 passed`; Skill quick validation, JSON parse, Python compile, and installed-skill hash comparison all PASS.
- Artifact status: the previous `tech_etf_mvo_rotation` PTrade PASS/publication is stale. Revalidation now blocks the source on both local-only calls and five `log.warn` call sites, in addition to pre-existing schedule/design mismatches. The old upload artifact must not be reused.
- Synchronization status: local repair only. No staging, commit, push, or pull request is authorized until explicit post-repair customer confirmation.
