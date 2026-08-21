---
name: quantstudio-strategy-compiler
description: Local-only QuantStudio strategy engineering (agent-first R0-R6 pipeline). PTrade conversion is handled by a separate PyQt tab / CLI, not by this skill. Use when an agent must convert a strategy idea or research specification through mandatory R0-R6 customer interaction, capability inspection, confirmed design, lifecycle/API composition, local backtesting, validation, and gated publication. Never skip stages or use strategy-specific renderer templates.
---

# QuantStudio Agent-first Strategy Engineering

Skill release: `0.7.0-framework-repair-f1-f6` (built on the `0.3.2-mvp` compiler/package baseline).

Contract versions at this release: agent design `2.2`, PTrade profile `1.10.0`, user backtest evidence `2.1`, validation report `2.1`. Design `2.1` artifacts must not be auto-migrated to PASS; regenerate the design under 2.2 and repeat the R2.5 confirmation.

Treat the calling agent as the strategy author. Constrain it with project lifecycle, data, timing, PTrade public API, validation, and delivery gates. Never implement a strategy by adding its name or shape to Compiler/Renderer/Jinja branches.

## Absolute execution rules

0. **Full-process enforcement (no stage may be skipped, short-circuited, or self-exempted).** Every invocation of this skill — regardless of the strategy's apparent simplicity, the customer's familiarity, or any prior similar work — must run the complete mandatory pipeline `R0 -> R1 -> R2 -> R2.5 -> R3 -> R4 -> R5 -> R6` in order, with no stage omitted, abbreviated, merged, "fast-tracked", or judged by the agent to be "obviously unnecessary". The agent has **no authority** to self-decide that a stage does not apply and skip it; if a stage appears inapplicable, the agent must still enter it, record the explicit `NOT_APPLICABLE` / `READY` / `BLOCKED` classification and the customer-facing output, persist it to `agent_workspace/workspace_state.json`, and only then advance. A stage is "complete" only when its written exit-gate criteria are satisfied and recorded; a conversational shortcut, an unconfirmed assumption, or a silent pass is never a valid completion. The agent must not emit any summary, conclusion, or formal deliverable until R6 has actually completed. If the customer asks to "just generate the code" or skip ahead, the agent must decline the shortcut, run every mandatory stage, and (where appropriate) offer the user-PyQt path — but never skip the R0/R2.5 customer-confirmation gates or the R1/R4/R5 checks.

1. Execute stages strictly in order: `R0 -> R1 -> R2 -> R2.5 -> R3 -> R4 -> R5 -> R6`.
2. Never combine, infer-complete, retroactively mark, or silently skip a stage. Each stage's entry and exit must be materially performed, not asserted.
3. At every stage, show the customer the stage output and unresolved decisions. Stop when customer confirmation is required. R0 must separately confirm target platforms and who executes R5 backtesting.
4. Do not generate executable strategy code before R2.5 explicit confirmation.
5. Do not publish before the local gate passes: R4 validation and hash-bound R5 backtest PASS are required. A user-PyQt candidate is temporary, not a formal publication.
6. Persist stage/evidence in `agent_workspace/workspace_state.json`. Resume from this ledger; do not rely on conversational memory alone.
7. If any gate is BLOCKED, remain at that stage. Do not weaken requirements, substitute strategy semantics, or advance the ledger.
8. The skill is local-only: `targets=["quantstudio"]`. Registered local extensions are allowed; the source must never claim PTrade portability (PTrade conversion is handled by the PyQt tab / CLI).
9. R0 and R2.5 are real conversational stop points. The calling agent must not self-confirm, infer consent from silence, reuse an unrelated prior confirmation, or continue in the same turn without the customer's explicit answer.
10. Local backtests obtain data through QuantStudio providers. Prefer `<current-project>/data/quantstudio.db`; use a configured/external database only when the project-local database is absent or the customer explicitly approves the override. Strategy source must never open DuckDB itself.
11. NOT_APPLICABLE (local-only skill; no PTrade target is produced). If a later PyQt-tab conversion fails on the real platform, the conversion pipeline (source_import) is repaired — not this skill.
12. Signal and execution/valuation use separated price bases (PTrade platform audit 2026-08-14). Indicator/ranking/entry/exit/risk OHLC must come from an injected history/price API with literal `fq='pre'` (pre-adjusted, continuous series); engine matching, fills, cash, position valuation, `data[code].price` and BarData OHLC use the raw snapshot (raw close/open — PTrade fills at raw close, 5/5 daily + 6/6 minute exact match; raw OHLC is the only allowed Agent-first execution contract: `execution_price_basis=raw_trade_price`). PTrade's own default fq is unadjusted, so source_import injects `fq='pre'` into converted get_history/get_price calls to keep the signal basis identical on both ends.
13. `set_backtest()` and `is_trade()` are QuantStudio-local extensions and may appear in local strategy source (the source_import conversion pipeline strips or inlines them for PTrade). PTrade logging uses `log.debug/info/warning/error/critical`; `log.warn` is BLOCKED.
14. Source using NumPy or pandas must explicitly declare `import numpy as np` and/or `import pandas as pd`; only storage/internal imports remain forbidden. Unimported calculation aliases are BLOCKED.
15. Validation is fail-closed for injected APIs. Every external top-level call and every `components.required_apis` entry must exist in the API signature registry; an unprofiled call is `MISSING_REUSABLE_API` at R1 and `BLOCK` at validation, never an approximation that the customer can waive.
16. The registered stock-core portable subset includes `set_benchmark`, `run_daily`, `get_Ashares`, `get_index_stocks`, `get_stock_status`, `get_positions`, `get_position`, `get_trade_days`, and `get_fundamentals`. `get_stock_status` uses `query_type='ST'|'HALT'|'DELISTING'`; `DELISTING_SORTING` belongs only to `filter_stock_by_status`.
17. `get_history(..., is_dict=True)` returns a mapping whose per-security item may be a pandas DataFrame, a NumPy structured array, or a recarray; `item[field]` may be a Series or an ndarray. Generated code must normalize extracted fields with `np.asarray(item[field], dtype=float)` (or a `hasattr(values, 'values')` guarded helper such as `_extract_history_field`) before any numerical use. Unguarded `.values`, `.iloc`, `.loc`, `.to_numpy()`, `.columns`, `.index`, `.empty` on history items/fields are BLOCKED. Design 2.2 strategies using `is_dict=True` must define the standard `_extract_history_field(history_item, field, dtype=float)` helper and route every extraction through it, so the agent-first runtime-shape fixture executes the exact production extraction code. The fixture is a hard gate inside `prepare_user_backtest_candidate.py` and is re-verified by hash at publication — it is not a manual step; the legacy renderer/Jinja fixture is not a substitute.
18. Design capital and backtest capital must be one machine-checkable contract. `portfolio_contract.sizing_mode=runtime_total_value` derives per-position targets from the runtime portfolio value and forbids hardcoded `g.capital`/`g.per_target`/fixed positive `order_target_value` amounts; `fixed_notional` binds `required_initial_cash` and `fixed_target_value`, and R5 BLOCKs any run whose actual `init_capital` differs. `order_target_value(code, 0)` is a liquidation and is always allowed.
19. Rebalance funding must follow `references/execution-funding-matrix.md`: `close`/`open` + `run_daily` executes immediately (same-batch sell proceeds may fund buys); `next_open` + `run_daily`/`before_trading_start` is legacy pending (same-batch proceeds are unavailable); `next_open` + `handle_data` + basket uses basket-atomic sell-first **only when** `engine_profile.profile_id='daily-bar-v1'` and `engine_profile.rebalance_mode='callback_basket'` are also declared (engine semantics `0.4.0-next_open_basket`, re-verified from config.csv at R5); unknown combinations are BLOCKED. A cash buffer only covers fees/lot-rounding/minor drift — never claim it solves full-turnover funding under legacy pending; use basket or two-phase rebalancing instead. Never hardcode `0.85` or any fixed exposure as a universal template. [DEPRECATED 2026-08-13: the `next_open` entries above are deprecated — `next_open` match_price_mode introduces T+1 data (next-day open) into the T-day time slice, violating strict PIT semantics. All generated strategies must use `close` mode (T-day close execution). `callback_basket` (next_open-specific) is also deprecated — close mode executes orders immediately, so sell proceeds are available for same-bar buys without basket atomicity. The `next_open`/basket rules are retained for historical audit only.]
20. R5 PASS requires hash-bound real artifacts, not self-reported booleans: `result_dir` plus SHA-256-bound `config.csv`, `daily_stats.csv`, `trades.csv` and run log, **plus the G3.5 reproducibility gate: a second independent-process run of the same strategy whose config/daily_stats/trades SHA-256 must match the first run's exactly** (`reproducibility_artifacts`; missing → EVIDENCE_INCOMPLETE, mismatch → R5 FAIL with attribution). The reviewer verifies the hashes and then analyzes **exactly those verified files** — the CSV paths must equal `result_dir/<canonical name>`, all paths must be absolute and traversal-free, and `config.csv` must match the declared window, candidate/strategy identity, match mode and `expected_engine_semantics_version`. Deployment invariants are enforced **per rebalance** from the `QS_REBALANCE_AUDIT`/`QS_PORTFOLIO_AUDIT` lines (positions/fill/exposure/cash at each rebalance date), never from a historical max or whole-period average. A finished, exception-free backtest that never deployed the designed capital BLOCKs (`capital_contract_mismatch` / `deployment_invariant_failed` / `execution_funding_failed` / `artifact_*`). `runtime_checks=true` is informational only. A legitimate `signal_dependent` no-trade run may bind `trades_csv=null` with a clean completion log; strict-target strategies must always bind a real `trades.csv`.
21. Designs carrying `r5_deployment_invariants` must emit machine-parseable audit lines: `QS_REBALANCE_AUDIT rebalance_id=... date=... selected=... tradable=... sell_submitted=... buy_submitted=...` per rebalance and `QS_PORTFOLIO_AUDIT rebalance_id=... date=... positions=... cash_ratio=... gross_exposure=...` after trading. Fixed key=value format; do not rely on JSON-module-only serialization or free-text logs. `rebalance_id` is the only authoritative one-to-one association: every id is unique, each rebalance matches exactly one portfolio audit with the same id, a portfolio audit can never prove two rebalances, its date must not predate its rebalance nor reach past the next rebalance (next_open may audit on the next trading day via the same id). Duplicate/missing/orphan ids are `evidence_incomplete`. [DEPRECATED 2026-08-13: the "(next_open may audit on the next trading day via the same id)" allowance is deprecated — `next_open` match_price_mode is deprecated (see rule 19); all audits occur on the same rebalance day under `close` mode.]
22. Design 2.2 confirmations require verbatim evidence in `confirmation_evidence`: `confirmed=true`, non-empty `customer_text`, timezone-aware ISO `confirmed_at`, `source=customer_reply` for `generation_target`, `strategy_semantics`, `portfolio_contract`, `rebalance_funding_contract`, and `r5_deployment_invariants`. A bare boolean is not evidence.
23. Static PTrade validation PASS is reported as `PTRADE_PROFILE_PASS` with `runtime_validation_status=NOT_VERIFIED` and `deployment_status=NOT_DEPLOYABLE`. It must never be phrased as "PTrade 可上线/已验证/部署通过". Broker runtime PASS requires customer-supplied real-platform evidence; a later real runtime failure makes all old evidence `STALE` via `scripts/retire_ptrade_runtime_evidence.py`.
24. `history_coverage_contract.lookback_bars` is an indicator lookback, not a demand that the selected backtest window be preceded by that many in-window trading days; provider history before the window start remains available. R1 checks full-history candidate counts at the first possible decision date; R5 reads `history_eligible_count` from the first rebalance audit and classifies shortfalls as DATA_BLOCKED or the confirmed fail-soft rule — never as a guessed "start date too late".

## Responsibility boundary

### Skill and project components own

- lifecycle signatures and callback timing;
- public API signatures and context availability;
- data/PIT/no-lookahead contracts, front-adjusted signal-price enforcement, and local backtest data-source resolution;
- A-share status, T+1, lot, cost and execution boundaries;
- local adapter compatibility with real PTrade signatures;
- static validation, local backtesting, PTrade-profile validation;
- local-only generation: skill emits one local QuantStudio file into `quantstudio/backtest/strategies/`; PTrade conversion is out of scope (handled by the PyQt "转 PTrade" tab / CLI `qs-compile import`).

### Calling agent owns

- strategy-specific universe, indicators, signals, ranking and state;
- portfolio allocation, exit logic and fail-soft behavior;
- implementation and repair inside confirmed semantics.

A missing reusable API is fixed in the adapter/profile/Skill with unrelated component tests. Never fix it by adding a branch for the requesting strategy.

## Authoritative references

Load only as needed:

- component inventory: `references/component-catalog.json`
- real PTrade signatures/context: `references/ptrade-api-signatures.json`
- runtime shapes: `references/ptrade-runtime-compatibility.md`
- execution funding matrix: `references/execution-funding-matrix.md`
- lifecycle/timing: `references/lifecycle-and-timing.md`
- no-lookahead: `references/no-lookahead-rules.md`
- A-share filters: `references/ashare-hard-filters.md`

Local injected symbols or successful local execution are not proof of PTrade support.

# Mandatory stage orchestration

## R0 - Customer strategy semantics and generation target

### R0-TARGET - default local-only generation target

The skill is local-only by default: `targets=["quantstudio"]`. PTrade conversion is out of scope —
it is handled by the PyQt "转 PTrade" tab or the CLI `qs-compile import`. There is **no dual/QS-only
selection question** in R0. For ETF strategies:

- the strategy uses `get_etf_list_local(query_date, etf_type, active_only)` for a PIT dynamic ETF universe and may combine it with `get_history_batch`;
- `get_etf_list()` remains the PTrade-named API and is unavailable in the PTrade backtest profile. Never use it as a dynamic-backtest substitute;
- a later PTrade conversion freezes the dynamic pool into a static `ETF_POOL_STATIC` at the customer-confirmed backtest start date (see 07-ETF动态池固化补充规格 §2).

Present and confirm:

- point-in-time universe and ordering;
- exact mathematical definitions and warm-up;
- entry/exit predicates;
- signal cutoff, decision clock, execution clock and fill proxy;
- holding period, T+1/T+0, repeated-symbol and batch behavior;
- maximum simultaneous holdings, overlap, cash and leverage policy;
- order rejection, suspension and limit-state handling;
- costs, slippage and benchmark;
- the fixed price-basis contract: signal OHLC uses the front-adjusted engine snapshot via literal `fq='pre'` strategy history calls; execution, fills, cash and valuation use the raw engine snapshot (`execution_price_basis=raw_trade_price`, PTrade platform audit 2026-08-14).

Identify contradictions with concrete examples. Do not choose a material interpretation for the customer.

**R0 hard stop and exit gate:** show the full R0 review table, ask for an explicit customer decision, and stop. Exit only after all material semantic contradictions are answered by the customer. Record the confirming customer statement in the workspace ledger; an agent-authored `true` value is not evidence.

## R1 - Program-specific capability inspection

After the customer has explicitly completed R0, inspect only capabilities required by this strategy:

- resolve the local backtest database in this order: `<current-project>/data/quantstudio.db`, then an explicitly approved configured/external DuckDB; record the absolute path, existence, selected tables, row/date coverage and resolution reason;
- tables, columns, PIT anchors and date coverage through the adapter/provider layer, including non-null `*_front` OHLC coverage for every required asset/frequency;
- local engine profile and lifecycle;
- PTrade backtest/trade context availability: `NOT_APPLICABLE` (local-only skill; PTrade conversion is out of scope);
- exact function signatures and permitted keyword names for the selected targets;
- data return shapes and code suffixes;
- for local dynamic ETF universes, `etf_basic` existence, classification version/source, listing/delisting coverage, `etf_daily` history coverage, and PIT checks at pre-listing/post-listing/post-delisting dates;
- `get_stock_info` (F2): stock listing dates, ETF listing dates, ETF delisting dates, the `{security: {...}}` return shape, and unknown-security compat behavior — table existence alone is not sufficient;
- `get_index_stocks(date)` (F3): verify on live data that an explicit date really changes the result (no history union), the snapshot coverage start, absence of future-snapshot leakage, and snapshot completeness — completeness is decided **only** by the `index_constituents_snapshot_meta` batch contract (n/expected_count/status at ingest, never from future snapshots); indices without meta are DATA_BLOCKED;
- SW industry classification (F4): classification system/version (`SW`/`SW2021`), PIT effective ranges in `industry_membership`, structural gates (orphan / bad ranges must be 0), and that no pseudo `SW_<name>` codes exist. Official `index_member` has no conflict-resolution rule: raw overlapping intervals are preserved 1:1, ambiguous dates fail closed at API level, and while any overlap exists the capability is APPROXIMATION_REQUIRES_CONFIRMATION / DATA_BLOCKED — never formal PIT READY. The legacy `sw_industry` table is audit-only;
- SW industry index daily (F5): SW2021 L1 index count (31), daily coverage in unified `index_daily`, OHLC/amount sanity, `fq='pre'` raw-OHLC fallback for indices, the latest source watermark, and formal resident reachability (enabled `index_daily` daemon task consuming `get_index_daily_universe()`); one-off backfills never count as pipeline READY;
- engine rebalance mode (F1): `next_open + callback_basket` requires `daily-bar-v1` + `handle_data` + `expected_engine_semantics_version='0.4.0-next_open_basket'`; `run_daily` orders never enter the basket; PyQt exposes `rebalance_mode` (default `legacy`) since 2026-07-27. [DEPRECATED 2026-08-13: `next_open + callback_basket` rebalance mode is deprecated — it introduces T+1 data into the T-day time slice, violating strict PIT semantics. All generated strategies must use `close` mode (T-day close execution). The engine-semantics `0.4.0-next_open_basket` check remains only for auditing legacy strategies.]

Classify each item as `READY`, `APPROXIMATION_REQUIRES_CONFIRMATION`, `DATA_BLOCKED`, `LOCAL_ONLY`, `PTRADE_CONTEXT_BLOCKED`, or `MISSING_REUSABLE_API`.

`APPROXIMATION_REQUIRES_CONFIRMATION` is reserved for explicitly modeled timing, fill, or execution proxies whose APIs are already verified. A missing PTrade signature/context is never customer-waivable: classify it as `MISSING_REUSABLE_API`, keep R1 BLOCKED, repair the reusable profile/adapter plus tests, then repeat R1.

Machine-checkable capability identifiers (emitted by `inspect_capabilities.py` in the capability report, with `status_detail` tokens `API_PROFILE_READY` / `LOCAL_DATA_READY` / `LOCAL_RUNTIME_READY` / `PTRADE_STATIC_PROFILE_READY` / `PTRADE_RUNTIME_UNVERIFIED` / `DATA_BLOCKED`): `security_metadata_stock`, `security_metadata_etf`, `index_constituents_pit`, `index_constituents_history_coverage`, `industry_classification_sw2021`, `industry_membership_pit`, `sw_l1_index_daily`, `gui_rebalance_mode`, `callback_basket_pyqt`. When a design declares the related APIs, R1 must cite the matching capability entry: data missing -> `DATA_BLOCKED`; API not in the PTrade profile -> `MISSING_REUSABLE_API`; locally READY but real-PTrade unverified -> never write "PTrade verified"; historical constituents without PIT coverage -> BLOCK; historical industry with only a current snapshot -> BLOCK; `next_open + callback_basket` with `run_daily` order lifecycle -> BLOCK; PyQt R5 evidence not activating `0.4.0-next_open_basket` semantics when the design requires basket -> BLOCK. [DEPRECATED 2026-08-13: the `callback_basket_pyqt` capability and all `next_open + callback_basket` checks are deprecated — `next_open` match_price_mode introduces T+1 data into the T-day time slice, violating strict PIT semantics. All generated strategies must use `close` mode; basket atomicity is unnecessary because close mode executes orders immediately.]

For every API planned for generated code, check `ptrade-api-signatures.json`. Examples:

- use `set_slippage(slippage=...)`, never `slippage_ratio=...`;
- use the literal `fq='pre'` on every signal-price `get_history`, `get_history_batch`, or `get_price` call; `attribute_history` is forbidden because its portable signature cannot prove front adjustment;
- use `get_stock_info(..., field=['listed_date'])`, not local `get_security_info()`;
- do not use `get_snapshot` or `check_limit` in PTrade backtest code; `get_open_orders(security=None)` is allowed but must use its documented signature;
- do not assume locally injected MyTT names such as `EMA` exist on PTrade; use NumPy/pandas or define a portable helper;
- ETF mode (default local-only): allow registered local extensions `get_etf_list_local` and `get_history_batch`; do not run or claim PTrade validation (PTrade conversion by the tab/CLI freezes the pool later).

Do not let strategy source import `duckdb`, `quantstudio._paths`, or provider modules. The framework selects DuckDB; the strategy calls injected public APIs.

**R1 exit gate:** no unknown API signature/context, no hidden data gap, and a recorded local data-source path/provenance. If project-local DuckDB exists but another source is selected without explicit customer approval, R1 is BLOCKED.

## R2 - Design contract and component plan

Write and schema-validate:

```text
output/generated_strategies/<strategy_id>/agent_strategy_design.json
output/generated_strategies/<strategy_id>/R2_AGENT_COMPONENT_PLAN.md
```

The contract records natural-language semantics, lifecycle callbacks, public APIs, state fields, approximations, tests, and output paths. It must include:

```json
"market_data_contract": {
  "signal_price_adjustment": "pre",
  "execution_price_basis": "raw_trade_price",
  "etf_t0_enforcement": "engine_per_code",
  "stop_deferral_semantics": "trigger_lock_defer_next_sellable_day"
},
"targets": ["quantstudio"],
"universe_contract": {
  "mode": "dynamic_local",
  "local_dynamic_api": "get_etf_list_local"
},
"constraints": {
  "runtime_state_guard_required": true,
  "portable_source_required": true,
  "no_lookahead": true
}
```

Do not encode a renderer pattern or fixed strategy kind. The skill always emits `targets=["quantstudio"]`; ETF strategies use `universe_contract.mode="dynamic_local"` with `local_dynamic_api="get_etf_list_local"`. PTrade conversion happens later via the PyQt tab / CLI (static pool freeze).

Design 2.2 additionally requires these machine-checkable contracts (schema-validated and cross-checked for contradictions):

```json
"portfolio_contract": {
  "sizing_mode": "runtime_total_value",
  "required_initial_cash": null,
  "allocation_mode": "equal_weight",
  "allocation_denominator": "configured_target_count",
  "target_holdings": 20,
  "gross_exposure_target": 0.85,
  "cash_buffer_ratio": 0.15,
  "per_position_target_weight": 0.0425,
  "max_single_weight": 0.05,
  "allow_leverage": false
},
"rebalance_funding_contract": {
  "requires_same_cycle_sell_proceeds": true,
  "implementation_mode": "sell_then_buy_immediate",
  "cash_only_for_new_buys": false
},
"history_coverage_contract": {
  "lookback_bars": 252,
  "frequency": "1d",
  "history_required_before_first_decision": true,
  "backtest_start_does_not_truncate_provider_history": true,
  "minimum_candidates_with_full_history": 20
},
"r5_deployment_invariants": {
  "holding_count_mode": "strict_target_when_candidates_available",
  "target_holdings": 20,
  "minimum_fill_ratio": 0.9,
  "minimum_gross_exposure": 0.8,
  "maximum_cash_ratio_after_rebalance": 0.2,
  "maximum_insufficient_cash_rejections": 0,
  "require_at_least_one_rebalance": true
}
```

The cross-checks BLOCK self-contradicting capital math before R3: `gross_exposure_target + cash_buffer_ratio <= 1`, `target_holdings x per_position_target_weight <= gross_exposure_target`, `per_position_target_weight <= max_single_weight`, `fixed_notional` must bind `required_initial_cash`/`fixed_target_value`, and `runtime_total_value` must not carry fixed amounts. A design claiming 20 x 5% = 100% deployment plus a 15% cash buffer is a contradiction (`PORTFOLIO-CASH-BUFFER-CONTRADICTION`), not an approximation. `rebalance_funding_contract` must be compatible with the confirmed `match_price_mode` and decision lifecycle per `references/execution-funding-matrix.md`.

**R2 exit gate:** schema PASS and complete customer review package.

## R2.5 - Explicit customer hard confirmation

Show the full design, API component plan, approximations, platform differences, output paths and validation plan. Wait for explicit confirmation.

Require:

```json
{
  "open_questions": [],
  "user_confirmations": {
    "generation_target": true,
    "strategy_semantics": true,
    "execution_approximations": true,
    "component_plan": true
  }
}
```

All approximations require `confirmed=true`. (No `static_etf_whitelist` confirmation: local-only skill uses the dynamic `get_etf_list_local` pool; static freeze happens at PTrade conversion time.)

Design 2.2 also requires verbatim `confirmation_evidence` entries (customer_text + timezone-aware confirmed_at + source=customer_reply) for `generation_target`, `strategy_semantics`, `portfolio_contract`, `rebalance_funding_contract`, and `r5_deployment_invariants`; the boolean flags alone are not accepted.

**R2.5 hard stop and exit gate:** present the complete package, ask the customer to confirm it, and stop. Confirmation flags may become true only from that explicit reply; persist the confirming text/time/evidence verbatim. No code before this gate.

## R3 - Scaffold and agent implementation

Run `scripts/create_agent_workspace.py`. The scaffold wires lifecycle and schedules only.

Every generated strategy must define an idempotent:

```python
def _ensure_runtime_state():
    ...
```

It must use `hasattr`/missing-field checks and never reset existing state. It must be the first executable statement in:

- `initialize`;
- `before_trading_start`;
- `handle_data`;
- `after_trading_end`;
- every scheduled callback.

Reason: real PTrade may continue later lifecycle calls after `initialize` raises. State safety cannot depend on successful initialization.

The agent implements strategy logic with QuantStudio local extensions (e.g. `get_etf_list_local`, `get_history_batch`); the source must declare no PTrade portability (PTrade conversion is out of scope). Do not hand-maintain divergent local/PTrade business logic. Use only injected APIs for data; local execution must route those APIs to the selected project DuckDB provider rather than opening storage from strategy source. Every signal-price call must spell `fq='pre'` literally. Use raw current/snapshot prices only for execution checks, order sizing, fill reconciliation, cash, and valuation; never concatenate them into a front-adjusted indicator series.

**R3 performance best practices (PR7):**
- **Vectorized computation (RECOMMENDED)**: When computing cross-sectional
  indicators (MA/momentum/rank) over a universe from `get_history_batch`,
  prefer numpy 2D matrix operations over per-code loops:
  1. Collect valid codes' arrays (after length/liquidity filters)
  2. Right-align pad into a 2D matrix: `closes[i, -len(v):] = v`
  3. Vectorize: `np.nanmean(closes[:, -N:], axis=1)`
  This is semantically equivalent to per-code `np.nanmean` (verified) and
  ~3x faster for 800+ code universes.
- Cross-sectional percentile ranks via `pd.Series(...).rank(pct=True).values` (ties default
  `average`, matching a helper `_pct_rank` defined as `s.rank(pct=True)`).
- Keep per-code loops only where branch logic cannot be vectorized.

Equivalence requirement: vectorized output must be bit-identical to the per-code version
(`np.nanmean(axis=1)` per row equals per-row `np.nanmean`; `rank(pct=True)` on the same input
is deterministic). Do not mix `dypre`/`pre` semantics; keep `fq='pre'` literal.

**R3 exit gate:** complete canonical source with no scaffold markers.

## R4 - Separate static validation

Run validation for the local-only target:

```powershell
python scripts/validate_agent_strategy.py strategy.py --design agent_strategy_design.json --target-profile quantstudio
```

QuantStudio validation must pass. `PTrade validation: NOT_APPLICABLE` (local-only skill; PTrade conversion is handled by the PyQt tab / CLI `qs-compile import`).

For strategies that consume `get_history(is_dict=True)`, the agent-first runtime-shape fixture remains available (enforced inside `scripts/prepare_user_backtest_candidate.py` / `scripts/publish_agent_strategy.py` when configured): the recorded `runtime_shape_fixture_source_sha256` must equal the canonical hash being published, and `runtime_shape_fixture_report.json` must still exist, match its recorded SHA-256, still record `status=PASS`, and reference the same canonical source file. It may also be run standalone:

```powershell
python scripts/validate_runtime_shapes.py strategy.py
```

R4 reports distinguish static and runtime truth: `profile_validation_status=PASS` means the static contract passed; `runtime_validation_status` stays `NOT_VERIFIED` and `deployment_status` stays `NOT_DEPLOYABLE` until the customer supplies real broker runtime evidence.

Repair reusable incompatibilities in the adapter/profile/Skill first. Regenerate strategy output under the corrected rules; do not hand-patch only one target file.

**R4 exit gate:** QuantStudio static PASS + agent-first runtime-shape fixture PASS (when the source consumes `get_history(is_dict=True)`); `PTrade validation: NOT_APPLICABLE`. Reports remain separate and auditable.

## R5 - Backtest execution and evidence review

R5 execution owner comes from the confirmed R0 contract.

### Agent-managed mode

Run only the customer-confirmed window/profile. Record provider/database provenance, runtime checks, and PASS/FAIL as before. Do not invent dates.

### User-PyQt mode

After R4 PASS, run `scripts/prepare_user_backtest_candidate.py`. It must:

1. revalidate the canonical source for every selected target;
2. write only `quantstudio/backtest/strategies/<strategy_id>__candidate_quantstudio.py`;
3. prepend a comment-only `UNVALIDATED_BY_BACKTEST / NOT_FOR_PTRADE_UPLOAD` marker;
4. record canonical and candidate SHA-256 hashes;
5. set `stage=AWAITING_USER_BACKTEST`, `formal_publish_allowed=false`;
6. show the recommended window while leaving actual dates to the user.

The user runs PyQt and submits complete logs/report. Convert them into `user_backtest_evidence.json` (evidence_version `2.1`), then run `scripts/review_user_backtest_evidence.py`. Evidence must bind to the candidate hash and include database path, selected window, capital, engine profile, match mode, completion/exception status, a non-empty log excerpt, **and the real run artifacts**: `result_dir` plus SHA-256-bound `config.csv`, `daily_stats.csv`, `trades.csv`, and run log (`artifacts`). **R5 复现性门禁（G3.5，硬前提）**：证据还必须包含第二次独立进程运行的 `config.csv`/`daily_stats.csv`/`trades.csv`（`reproducibility_artifacts`，同一窗口/资金/配置重跑，`PYTHONHASHSEED` 固定或记录随机种子），两侧三件套 SHA-256 **逐位一致**才 PASS；缺失 → `reproducibility_evidence_missing`（EVIDENCE_INCOMPLETE），不一致 → `reproducibility_mismatch`（R5 FAIL 并归因：策略非确定性——dict/set 迭代顺序/随机数——或数据漂移或环境差异）。运行日志因含时间戳不参与跨运行比对。The reviewer first verifies every hash, then analyzes **exactly those verified files** (`scripts/analyze_backtest_artifacts.py`): the CSV paths must resolve to `result_dir/config.csv|daily_stats.csv|trades.csv`, all paths absolute and traversal-free. It cross-checks `config.csv` against the declared window, candidate/strategy identity, match mode, `engine_semantics_version`, and actual `init_capital`; computes trade counts, unique bought symbols, rebalance days, turnover, and `insufficient_cash`/`insufficient_sellable`/limit/halt/no-price/exception counters; and enforces `r5_deployment_invariants` **per rebalance** from the `QS_REBALANCE_AUDIT`/`QS_PORTFOLIO_AUDIT` lines. Self-reported `runtime_checks` booleans are informational and never authoritative.

R5 PASS requires all of: candidate hash match, database path match, engine profile/match mode match, actual initial cash satisfying `portfolio_contract` (fixed_notional equality), complete exception-free run, at least one rebalance when required, and every `r5_deployment_invariants` threshold (positions, fill ratio, gross exposure, cash ratio, insufficient-cash rejections) verified from the parsed artifacts.

Evidence results:

- incomplete, evidence 1.0, missing/hash-mismatched artifacts, or start before the ETF hard lower bound -> `USER_BACKTEST_EVIDENCE_INCOMPLETE` / `artifact_missing` / `artifact_hash_mismatch`, remain R5;
- candidate hash drift -> return R4;
- strategy logic failure or `deployment_invariant_failed` -> return R3;
- framework/data/API failure (including DATA_BLOCKED history coverage) -> return R1;
- PTrade profile/validator failure -> return R4;
- `capital_contract_mismatch` (e.g. designed 1,000,000 but ran 100,000) -> remain R5, rerun with the designed capital;
- `execution_funding_failed` (insufficient-cash rejections beyond threshold) -> return R2/R3 for funding-mode redesign;
- complete artifact-verified PASS -> `stage=BACKTEST_PASS`, `formal_publish_allowed=true`.

Every repair invalidates old R4/R5 hashes. Regenerate the candidate after the new R4 PASS and require a new PyQt run.

**R5 exit gate:** hash-bound runtime evidence PASS for the exact candidate source, regardless of who executed the backtest.

## R6 - Target-aware generation, validation, and publication

`publish_agent_strategy.py` must branch from the confirmed `targets` contract.

### Local-only mode

1. verify the workflow ledger and local backtest PASS;
2. generate and validate the QuantStudio staging file;
3. publish only `quantstudio/backtest/strategies/<strategy_id>_quantstudio.py`;
4. do not create an empty or placeholder PTrade file;
5. record exactly:
   - `PTrade validation: NOT_APPLICABLE`
   - `Dual consistency: NOT_APPLICABLE`
   - `PTrade conversion: out of scope (PyQt tab / CLI qs-compile import)`
   - `PTrade output: NOT_GENERATED`

In user-PyQt mode, R6 must verify the validated candidate still exists with the same hash, generate fresh formal staging files from the canonical source, validate/compare them, atomically write formal targets, then remove the `__candidate` file. Record `candidate_status=PROMOTED` and `candidate_removed=true`. Never promote an edited candidate.

**R6 exit gate:** all applicable validation gates PASS, non-applicable gates explicitly recorded, hashes recorded, the temporary candidate retired when applicable, and publication PASS.

# PTrade compatibility rules

- API keyword names must match the platform exactly. Python acceptance through local `**kwargs` is forbidden as compatibility evidence.
- `set_backtest()` and `is_trade()` are LOCAL_ONLY extensions; they may be used in local strategy source (conversion strips/inlines them for PTrade). Do not wrap one local-only call with another.
- Use `log.warning(...)`, never `log.warn(...)`, in portable source; real IQEngine `LogEngine` does not expose the alias.
- Backtest/trading/research context availability is enforced separately.
- **Security suffix rule (HARD)**: All security code constants, variables,
  and string literals in generated strategy source MUST use PTrade suffixes
  `.SS` (Shanghai), `.SZ` (Shenzhen), `.BJ` (Beijing). NEVER use JoinQuant
  aliases `.XSHG`/`.XSHE` or QMT alias `.SH` in generated code.
  Rationale (verified 2026-08-11):
  - DuckDB stores bare 6-digit codes; the data-access layer normalizes output
    to `.SS/.SZ` via normalize_to_ptrade (duckdb_data_access.py:978,1008,1883).
    Strategy code faces `.SS/.SZ` regardless of data source (xtquant/MCP).
  - context.portfolio.positions keys are `.SS/.SZ` with EXACT dict membership
    (backtest_engine.py:2091-2108 deliberately non-alias-aware). A strategy
    using `.XSHE` for `code in positions` will silently fail branch logic
    (verified: ETF动量 platform original died with .XSHE mix, 09 report).
  - Do NOT assume "`.XSHE` works locally so it's fine" — the local engine
    deliberately simulates the platform's exact-match behavior.
  - If the customer's input uses JoinQuant/QMT suffixes (.XSHG/.XSHE/.SH),
    normalize them to .SS/.SZ/.BJ in the generated source. Do not pass
    through customer suffixes unchanged.
    Example: customer says "159915.XSHE" → generate '159915.SZ'.
    Example: customer says "510300.SH" → generate '510300.SS'.
- Normalize comparison keys by bare six-digit code where API containers may differ.
- Use NumPy/pandas or source-defined helpers for indicators unless the PTrade public profile explicitly lists the indicator. When used, import them explicitly (`import numpy as np`, `import pandas as pd`); real PTrade does not inject QuantStudio aliases.
- Every `get_history`, `get_history_batch`, and `get_price` call used by generated backtest code must include the literal keyword `fq='pre'` AND `include=False` (both daily and minute frequency). `include=True` is FORBIDDEN in generated backtest code — it leaks the current bar into the signal, creating a lookahead bias (signal contains the current bar's close while execution occurs at that same close price in close mode = circular). `dypre`, post-adjustment, missing/dynamic values, and `attribute_history` are blocked.
- Daily-frequency rationale (verified 2026-08-13): strict PIT semantics require signal computation using only completed bars (T-1 and earlier); execution at T-day close is the only price known within the T-day time slice. PTrade platform confirmed 2026-08-13 (re-verified with date stamps): PTrade include semantics are identical to local for ALL frequencies — include=True contains current-day bar, include=False stops at previous trading day (daily) or previous bar (minute). No mapping needed; source_import passes include through unchanged.
- PTrade platform match-price audit 2026-08-14 (4-probe, real platform): daily fill = T-day raw close (5/5 exact match); minute fill = bar raw close (6/6 exact match); `fq='pre'` returns pre-adjusted prices (10/10 match local close_front); PTrade default fq returns raw (≠ local default `fq='pre'`) — hence source_import injects `fq='pre'` on conversion. Execution/valuation basis is raw (`execution_price_basis=raw_trade_price`); signal basis stays front-adjusted via literal `fq='pre'`.
- A field extracted from `get_history(..., is_dict=True)` must be normalized with `np.asarray(...)` before numerical use. Do not unconditionally use `.values`, `.iloc`, `.loc`, `.to_numpy()`, `.columns`, `.index` or `.empty` on a per-security history item or extracted field; the item may be a DataFrame, structured array, or recarray.
- Minute-frequency `get_history` must also use `include=False`. At 9:35 handle_data, the strategy may only see bars up to 9:34 — the current 9:35 bar is not yet completed and must not form the signal. The current bar's close is the execution price (close mode), and using it as signal input is a lookahead bias. Engine fix verified (2026-08-13): minute include=False correctly anchors to previous bar (not previous trading day). PTrade platform confirmed: ALL frequencies (daily + minute) include semantics match local — no mapping needed for any frequency.
- ETF ex-rights handling is engine-side on every profile (verified 2026-08-16): `_apply_factor_derived_split` (preClose-derived share splits/consolidations, ETF-only) is hooked into the daily main loop AND `minute-bar-v1` AND `daily-open-close-proxy-v1` before strategy callbacks; ETF cash dividends are credited via `_apply_etf_cash_dividends` (etf_dividend × 0.80, PTrade-measured). Generated strategies must never implement their own split/dividend adjustment in code; the signal basis stays `fq='pre'` (no ex-date gap in signals).
- `get_snapshot` and `check_limit` are not allowed in PTrade backtest source. `get_open_orders(security=None)` is allowed in backtest and trade contexts.
- `filter_stock_by_status` is called only from `before_trading_start`; scheduled callbacks use `get_stock_status` for current status checks. Portable status checks use only `query_type='ST'`, `'HALT'`, or `'DELISTING'`; never pass `DELISTING_SORTING` to `get_stock_status`.
- Stock-core strategies may use the registered signatures for `set_benchmark`, `run_daily`, `get_Ashares`, `get_index_stocks`, `get_stock_status`, `get_positions`, `get_position`, `get_trade_days`, and `get_fundamentals`. Any other injected top-level API remains BLOCKED until its exact public signature, context and return shape are added to the profile and local adapter regression tests.
- **平台差异吸收（2026-08-22，PTrade 平台对齐治理 v4，必须遵守）**：市价单单笔上限（创业板/科创板 50,000 股）、交易日历全量/格式混用、`listed_date` 格式差异均由**框架与转换管线吸收**（source_import 注入拆单/归一化），策略层**零平台知识**。**`current_price` / `get_current_data` 不是真实 PTrade API**（官方文档全文核实 + 真实平台模块加载期 NameError，2026-08-22 双证）——双端/PTrade 目标策略**禁止调用**（profile `local_only_symbols` 登记，Validator `TARGET-LOCAL-EXTENSION-BAN` BLOCK）；平台官方取当前价方式 = `data[code].price`（当前周期最新价）/ `get_history` 最新 bar / `get_price`（`get_snapshot` 仅交易场景，回测不可用）。**禁止手写平台兜底**——`def _normalize_date_str`、`def _current_raw_price`、`g.last_close` 自维护赋值均为校验器 BLOCK（`PTRADE-PLATFORM-FALLBACK-BAN`，函数定义级防误伤）；禁止发现即修，新平台差异一律进 D4 登记序列排队。
- `get_etf_list()` is blocked in backtest source. ETF strategies use `get_etf_list_local()` through the provider/data-adapter chain (dynamic PIT pool); PTrade conversion later freezes the pool into a static `ETF_POOL_STATIC`.
- `get_etf_list_local()` returns metadata/PIT/code-format results only; MA, momentum, liquidity, abnormal-volume and ranking rules remain in strategy source.
- **ETF T+0/T+1 per-code 契约（必须遵守，详见 `references/etf-t0-rules.md`）**：引擎按 `etf_basic.fund_type` 逐码执行 T+0/T+1（`{qdii,gold,commodity,bond,money}`→T+0，`equity`→T+1，未知→fail-closed T+1；仅 minute-bar-v1 + `--etf-t0 true` 生效，默认全部 T+1）。生成策略必须：
  - 在 design JSON 声明 `market_data_contract.etf_t0_enforcement`（engine_per_code / all_t1）与 `stop_deferral_semantics`（engine_per_code 时必填，校验器 BLOCK）；
  - 止损采用"触发即锁 → 尝试卖出 → 持仓对账 → 未成交挂起 → 次日首个可卖窗口重试"模式，**不查询 ETF 类别**；买入日期由策略自维护账本（PTrade Position 无 buy_date）；
  - 订单返回值只做真值判断，**禁止读取 `.status`/`.reason` 等本地 Order 字段**（该禁令仅限 skill 生成的 PTrade 可移植策略；本地专用策略不受限），事实以 `get_position()` 对账为准（受理但未成交场景两平台返回值真值不一致）；
  - **禁止 `set()`/`frozenset()` 与依赖哈希迭代顺序的决策逻辑**（跨进程结果不稳定，校验器 BLOCK）。
- For customer-requested QuantStudio-only event strategies, external CSV/event data is ingested by the generic `strategy_events` adapter and queried with local extension `get_strategy_events`; set targets to `quantstudio` only and never claim PTrade portability.
- Use order rejection and documented price fields for backtest limit behavior; trading-only checks may be used only in a separately validated trading profile.

# Runtime failure repair protocol

When a customer supplies a real PTrade exception:

1. run `scripts/retire_ptrade_runtime_evidence.py` with the verbatim reason: the prior PTrade profile PASS, local backtest evidence, candidate hash and formal publish permission become `STALE`, and the old candidate/upload artifacts are renamed `*.RETIRED_DO_NOT_UPLOAD` (kept for audit, never reused or rehashed);
2. reproduce the incompatibility with a minimal reusable API/profile test;
3. repair the local adapter signature/shape only so that local acceptance matches PTrade, and repair the Skill/profile/validator so future agents generate portable calls;
4. do not add a strategy ID, strategy-name branch, one-off template, or one-target hotfix;
5. regenerate the canonical output under the corrected generic rules;
6. rerun QuantStudio validation, local backtest, and (for conversions) the source_import round-trip;
7. report the old and new hashes and explicitly confirm the old upload artifact stayed retired.

# Prohibited behavior

- skipping, short-circuiting, or self-exempting any mandatory stage (`R0`/`R1`/`R2`/`R2.5`/`R3`/`R4`/`R5`/`R6`) because the task seems simple, the customer is familiar, or prior similar work exists; every stage must be entered, performed, and recorded;
- marking a stage "done" from a conversational shortcut, an unconfirmed assumption, or a silent pass instead of satisfying its written exit-gate criteria;
- skipping a stage because the answer seems obvious;
- generating code while R2.5 is unconfirmed;
- treating local execution as PTrade validation;
- using undocumented keyword aliases;
- omitting `fq='pre'`, using another adjustment mode, using `attribute_history`, or mixing raw bar OHLC into front-adjusted signal calculations;
- using local-only APIs when `ptrade` is a selected target;
- assuming `initialize` always completes before other callbacks;
- publishing before all target-applicable validations pass;
- editing only one published target to make it pass;
- adding strategy IDs/names to Skill, Compiler, Renderer or templates;
- selecting an external database while `<current-project>/data/quantstudio.db` exists without explicit customer approval;
- patching only the currently failing strategy or only one target copy instead of repairing the reusable contract and regenerating all selected outputs;
- calling `get_etf_list` in any backtest target;
- reporting PTrade PASS for a local-only strategy;
- treating a PyQt candidate as a formal/PTrade upload artifact;
- producing PTrade code during strategy development (PTrade conversion is owned by the PyQt "转 PTrade" tab / CLI `qs-compile import`);
- accepting user backtest claims without candidate-hash, data-source, window and completion evidence;
- accepting self-reported `runtime_checks=true` booleans as R5 PASS evidence instead of hash-bound real artifacts;
- claiming R5 PASS for a backtest that finished without exceptions but never deployed the designed capital/positions;
- unguarded pandas-only access (`.values`/`.iloc`/`.loc`/`.to_numpy()`/`.columns`/`.index`/`.empty`) on `get_history(..., is_dict=True)` items or extracted fields;
- hardcoding `g.capital`/`g.per_target`/fixed positive `order_target_value` amounts under `sizing_mode=runtime_total_value`;
- treating a fixed cash buffer (e.g. 0.85 exposure / 15% cash) as a universal solution for sell-proceeds timing under `next_open` legacy pending semantics; [DEPRECATED 2026-08-13: `next_open` itself is deprecated — it introduces T+1 data into the T-day time slice, violating strict PIT semantics; all strategies must use `close` mode.]
- claiming `next_open` + `run_daily` same-batch sell proceeds can fund buys, or routing `close` + `run_daily` to pending semantics; [DEPRECATED 2026-08-13: `next_open` itself is deprecated — it introduces T+1 data into the T-day time slice, violating strict PIT semantics; all strategies must use `close` mode.]
- recording user confirmations as bare booleans without verbatim customer text and timezone-aware timestamps (design 2.2);
- phrasing static `PTRADE_PROFILE_PASS` as "PTrade 可上线/已验证/部署通过";
- reusing old R4/R5 PASS, candidate, or staging files after a real PTrade runtime failure without regenerating fresh hashes;
- interpreting `lookback_bars` as a requirement that the backtest window itself contain that many extra pre-start trading days;
- keeping a `__candidate` file after successful formal promotion;
- reusing R5 evidence after canonical/candidate source changes.

# Commands

- scaffold: `scripts/create_agent_workspace.py`
- profile validation: `scripts/validate_agent_strategy.py`
- agent-first runtime-shape fixture: `scripts/validate_runtime_shapes.py`
- PTrade conversion: out of scope (PyQt "转 PTrade" tab / CLI `qs-compile import`)
- user-PyQt candidate: `scripts/prepare_user_backtest_candidate.py`
- artifact analysis: `scripts/analyze_backtest_artifacts.py`
- user evidence review: `scripts/review_user_backtest_evidence.py`
- PTrade runtime-failure retirement: `scripts/retire_ptrade_runtime_evidence.py`
- gated formal publish: `scripts/publish_agent_strategy.py`
- Skill validation: `scripts/quick_validate.py`

Legacy Spec/IR/Jinja compilation is used only when the customer explicitly requests legacy reproduction. It is never the default path for a new strategy.
