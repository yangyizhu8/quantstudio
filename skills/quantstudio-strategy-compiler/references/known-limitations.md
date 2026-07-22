# Known Limitations

> New file aggregated from PR3/PR4 reports + implementation-status.md @ 2026-07-22
> 权威源：docs/strategy-compiler/pr{3,4}-implementation-report.md + implementation-status.md
> 本文件为 Skill 派生快照，每个 PR 完成时必须同步更新本清单

These are documented, accepted limitations of the current compiler/runtime. They are NOT bugs — each was a conscious scope decision with a deferral trigger. The Skill must surface these to users so Specs don't assume capabilities that don't exist yet.

## PR5 (Skill skeleton) — current scope limits

- **No code rendering**. PR5 stops at Spec (R2.5 user confirmation). Rendering to `.py` (QuantStudio + PTrade) is PR6. A user asking for code in PR5 must be told "rendering is PR6 scope".
- **No IR**. `build_strategy_ir.py` is PR6. The Spec's `signals.steps` map conceptually to future SignalNodes but no IR is built.
- **No install_skill.py**. Originally listed in the minimal-skeleton scripts (master plan §4.2 line 296), removed from PR5 because §7.29 does not mandate it. Re-added in PR6. (Decision chain: PR5 plan review, user-approved 2026-07-22.)
- **inspect_capabilities.py covers the core subset** of the 14 data-gate checks (§8). Remaining checks marked NOT_IMPLEMENTED, completed in PR6.

## PR6 — not yet started

- IR nodes (11 types), dual renderers, 7 validators (validate_local_strategy / validate_ptrade_portability / scan_lookahead / check_hard_filters / compare_strategy_variants / run_smoke_backtest). All `0.0.0-planned`.

## PR3.5 — 1m aggregation deferred

- `1m → 5m/15m/30m/60m` real-time aggregation is **not implemented**. Trigger: a real native-freq gap appears after minute data is ingested. Currently each native freq is stored separately, so aggregation is a fallback path only.
- **Minute count query**: implements "N bars before end_date same-day" only. Cross-day count semantics deferred.

## PR4 — minute engine limits (all accepted, not bugs)

- **15:00 bar = closing auction** (tested, verified on real data 2026-07-22).
- **09:30 call-auction bar intentionally excluded** from the minute loop (240 bars processed, not 241). Opening price flows through daily `attach_day` via preClose/open.
- **Volume participation**: not implemented (consistent with daily profile).
- **Scheduler order**: minute `run_daily` fires before `handle_data`; daily fires after (per master plan 7.24).
- **Current-bar-visible semantics differ by profile**: minute profile's `handle_data` sees the current bar; daily does not. Strategy code must not assume one语义.

## Tick / L2 / high-frequency

- Tick engine: first version execution_status must be `BLOCKED` / `PLANNED` / `UNSUPPORTED` — never `READY` (capability-model.md invariant 4, schema allOf line 73). PR9 scope.

## Data source caveats (post-xtquant switch)

- **stock_daily / etf_daily still hold tushare data**; xtquant daily cutover (runbook) is user manual ops, not yet executed. Testing continues on tushare until cutover.
- **stock_minutes / etf_minutes**: real xtquant data ingested (18.78M / 46.94M rows). PR4 real-minute smoke PASS.
- **xtquant back adjustment**: per-tick cumulative algorithm — same bar OHLC factors differ by 2-4% (algorithm feature, not bug). quality_audit uses 5% threshold for xtquant back, 2% for others.
- **etf_daily UnitCheck 5 rows / stock_dividend FutureTimestamp 1 row**: legacy data artifacts, documented in quality report, not blocking.

## Sync reminder

When a limitation is resolved (e.g. PR3.5 aggregation implemented, or PR6 rendering arrives), this file MUST be updated and `skill_version` bumped. Stale limitations mislead users into avoiding capabilities that now work.
