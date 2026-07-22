# Known Limitations

> New file aggregated from PR3/PR4 reports + implementation-status.md @ 2026-07-22
> 权威源：docs/strategy-compiler/pr{3,4}-implementation-report.md + implementation-status.md
> 本文件为 Skill 派生快照，每个 PR 完成时必须同步更新本清单

These are documented, accepted limitations of the current compiler/runtime. They are NOT bugs — each was a conscious scope decision with a deferral trigger. The Skill must surface these to users so Specs don't assume capabilities that don't exist yet.

## PR6a (IR + render + 2 validators) — DELIVERED 2026-07-22

- **IR contract authored** (`docs/strategy-compiler/strategy-ir-contract.md`): 11 node types × 9 fields, `signals.steps`→node mapping, 12 cross-node invariants, 10 lookahead high-risk items mapped to rule IDs.
- **IR builder + renderer delivered**: `build_strategy_ir.py` (Spec→IR), `render.py` (IR→dual .py via Jinja2), 4 templates (QS/PTrade × daily/minute).
- **2 validators delivered**: `scan_lookahead` (10 high-risk items, all BLOCK; #10 PR3.5-deferred), `validate_local_strategy` (syntax + lifecycle + API whitelist + Guard + semantics contract).
- **Supported operations (PR6a)**: `ma` (IndicatorNode), `cross` (SignalNode). Others raise with step id + "PR6b 扩展" — never silently downgrade.
- **e2e stops at static validation**. `run_smoke_backtest` (actual backtest execution) is PR6b. PR6a case 1 verifies: spec→IR→render→compile→Guard→scan_lookahead→validate_local all PASS, but does NOT run a backtest.
- **33 tests**: 20 e2e (强一致性 + IR 承重 + 变体 + 差异层 + 黄金保护 + validator 正向) + 13 negative (10 lookahead high-risk + 3 local-strategy).

## PR6b — remaining PR6 scope (NOT_STARTED)

- 5 validators: `validate_ptrade_portability` / `check_hard_filters` / `compare_strategy_variants` (14-dimension consistency) / `run_smoke_backtest` (actual execution) / `validate_strategy_spec` IR-level upgrade.
- `install_skill.py` (copy project skill → user skill dir, chain quick_validate).
- 2 templates: `strategy_spec.json` / `run_card.json` skeletons.
- 9 golden cases (case 2-10 from master plan §7.37).
- `build_strategy_ir` operation expansion: `pct_change`/`ema`/`std`/`zscore`/`rank`/`top_n`/`threshold`/`compare` + Factor/Ranking full impl + RiskNode stop_loss/take_profit.
- Multi-stock universe rendering (index_constituents/list): PR6a templates only fully render single_stock.
- 1m→5m aggregation lookahead check (#10): blocked on PR3.5 IndicatorNode frequency field.

## PR5 (Skill skeleton) — completed

- **No code rendering in PR5** (PR6a now delivers it).
- **No install_skill.py** (PR6b).
- **inspect_capabilities.py covers the core subset** of the 14 data-gate checks (§8).

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
