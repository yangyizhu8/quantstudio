# Known Limitations

> Updated from project implementation and runtime evidence @ 2026-07-26
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

## Agent-first validation/publish status — current 2026-07-26

Delivered:

- target-aware `validate_agent_strategy.py` with separate QuantStudio/PTrade profiles;
- `validate_dual_consistency.py` post-generation physical-file comparison;
- agent-managed and hash-bound User-PyQt R5 evidence flows;
- gated `publish_agent_strategy.py` dual-target publication;
- `install_skill.py` plus `quick_validate.py` installation validation;
- PTrade stock-core signature profile 1.7.0 and fail-closed blocking for unprofiled injected APIs.

Remaining limitations:

- there is no automated real-broker/IQEngine smoke runner; local execution and static PTrade validation are not runtime platform proof;
- broker/version-specific API differences still require runtime evidence and an explicit profile correction;
- the legacy Spec→IR/Jinja pipeline retains its PR6a operation and multi-stock rendering limits and is not the default path for new agent-authored strategies;
- 1m→5m aggregation lookahead validation remains deferred with the aggregation engine capability.

## PR5 (Skill skeleton) — completed

- **No code rendering in PR5** (PR6a now delivers it).
- **`install_skill.py` is delivered** and validates the installed copy with `quick_validate.py`.
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

- **stock_daily / etf_daily** now hold xtquant data after the daily cutover (8,936,972 / 1,318,108 rows on 2026-07-26).
- **stock_minutes / etf_minutes** hold xtquant data (22,535,730 / 47,713,321 rows on 2026-07-26).
- **xtquant back adjustment**: per-tick cumulative algorithm — same bar OHLC factors differ by 2-4% (algorithm feature, not bug). quality_audit uses 5% threshold for xtquant back, 2% for others.
- **etf_daily UnitCheck 5 rows / stock_dividend FutureTimestamp 1 row**: legacy data artifacts, documented in quality report, not blocking.

## PTrade profile scope

- Profile 1.7.0 verifies the registered public subset only. Any external top-level API absent from `ptrade-api-signatures.json` is deliberately BLOCKED rather than treated as an approximation.
- `get_stock_status` portable source uses `ST`, `HALT`, or `DELISTING`. `DELISTING_SORTING` is retained only as a local backward-compatible alias and as a `filter_stock_by_status` filter type.
- Static PASS proves signature/profile conformance, not successful execution on every broker IQEngine deployment.

## Sync reminder

When a limitation is resolved (e.g. PR3.5 aggregation implemented, or PR6 rendering arrives), this file MUST be updated and `skill_version` bumped. Stale limitations mislead users into avoiding capabilities that now work.

## ETF metadata classification

`get_etf_list_local(etf_type="equity")` depends on the synchronized `etf_basic` table. The current `etf-basic-v1` classification is auditable and sourced from Tushare `fund_basic`, with keyword/code rules for cross-border and commodity subclasses and `etf_daily` only for missing date completion. Classification defects must be repaired in the reusable metadata sync/overrides, never hidden in strategy logic.
