---
name: quantstudio-strategy-compiler
description: Strict target-aware agent-first strategy engineering for QuantStudio and optional PTrade delivery. Use when an agent must convert a strategy idea or research specification through mandatory R0-R6 customer interaction, explicit dual-vs-local target selection, capability inspection, confirmed design, lifecycle/API composition, local backtesting, target-applicable validation, consistency checks, and gated publication. Never skip stages or use strategy-specific renderer templates.
---

# QuantStudio Agent-first Strategy Engineering

Skill release: `0.5.0-user-pyqt-candidate-flow` (built on the `0.3.2-mvp` compiler/package baseline).

Treat the calling agent as the strategy author. Constrain it with project lifecycle, data, timing, PTrade public API, validation, and delivery gates. Never implement a strategy by adding its name or shape to Compiler/Renderer/Jinja branches.

## Absolute execution rules

1. Execute stages strictly in order: `R0 -> R1 -> R2 -> R2.5 -> R3 -> R4 -> R5 -> R6`.
2. Never combine, infer-complete, retroactively mark, or silently skip a stage.
3. At every stage, show the customer the stage output and unresolved decisions. Stop when customer confirmation is required. R0 must separately confirm target platforms and who executes R5 backtesting.
4. Do not generate executable strategy code before R2.5 explicit confirmation.
5. Do not publish formal targets before the selected target-mode gates pass: every mode requires R4 validation and hash-bound R5 backtest PASS; dual mode additionally requires PTrade-profile validation and post-generation dual consistency. A user-PyQt candidate is temporary, not a formal publication.
6. Persist stage/evidence in `agent_workspace/workspace_state.json`. Resume from this ledger; do not rely on conversational memory alone.
7. If any gate is BLOCKED, remain at that stage. Do not weaken requirements, substitute strategy semantics, or advance the ledger.
8. R0 must explicitly select one target mode. Dual mode targets the **PTrade backtest public API subset** and forbids local extensions. QuantStudio-only mode may use registered local extensions but must never claim PTrade portability.
9. R0 and R2.5 are real conversational stop points. The calling agent must not self-confirm, infer consent from silence, reuse an unrelated prior confirmation, or continue in the same turn without the customer's explicit answer.
10. Local backtests obtain data through QuantStudio providers. Prefer `<current-project>/data/quantstudio.db`; use a configured/external database only when the project-local database is absent or the customer explicitly approves the override. Strategy source must never open DuckDB itself.
11. In dual mode, customer-provided PTrade runtime failures invalidate the previous PTrade PASS and R6 publication. Return to R1/R4, repair the reusable profile/adapter/Skill rule, regenerate both targets, and repeat post-generation consistency checks.
12. All OHLC prices used for indicators, ranking, entry/exit signals, or risk thresholds must come from an injected history/price API with the literal keyword `fq='pre'`. Never omit `fq`, use `None`/`post`/`dypre`, or mix raw `data[code].open/high/low/close` into a front-adjusted signal series. Raw prices remain valid only for order matching, fills, cash, and position valuation.

## Responsibility boundary

### Skill and project components own

- lifecycle signatures and callback timing;
- public API signatures and context availability;
- data/PIT/no-lookahead contracts, front-adjusted signal-price enforcement, and local backtest data-source resolution;
- A-share status, T+1, lot, cost and execution boundaries;
- local adapter compatibility with real PTrade signatures;
- static validation, local backtesting, PTrade-profile validation;
- target-aware generation: dual mode emits both files and post-generation consistency evidence; QuantStudio-only mode emits one local file and records PTrade/consistency as `NOT_APPLICABLE`.

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
- lifecycle/timing: `references/lifecycle-and-timing.md`
- no-lookahead: `references/no-lookahead-rules.md`
- A-share filters: `references/ashare-hard-filters.md`

Local injected symbols or successful local execution are not proof of PTrade support.

# Mandatory stage orchestration

## R0 - Customer strategy semantics and generation target

### R0-TARGET - mandatory first question

Before discussing implementation, ask the customer to explicitly choose:

1. **Dual target (recommended): QuantStudio local + PTrade**
2. **QuantStudio-only local backtest**

Do not infer the answer. Record it as `targets` plus `user_confirmations.generation_target=true` only after the customer replies. For ETF strategies:

- dual target uses one customer-confirmed static ETF whitelist in both files; `get_etf_list()` and every local-only API are forbidden;
- QuantStudio-only uses `get_etf_list_local(query_date, etf_type, active_only)` for a PIT dynamic ETF universe and may combine it with `get_history_batch`;
- `get_etf_list()` remains the PTrade-named API and is unavailable in the PTrade backtest profile. Never use it as a dynamic-backtest substitute.

Present and confirm:

- point-in-time universe and ordering;
- exact mathematical definitions and warm-up;
- entry/exit predicates;
- signal cutoff, decision clock, execution clock and fill proxy;
- holding period, T+1/T+0, repeated-symbol and batch behavior;
- maximum simultaneous holdings, overlap, cash and leverage policy;
- order rejection, suspension and limit-state handling;
- costs, slippage and benchmark;
- the fixed price-basis contract: signal OHLC uses front-adjusted prices (`fq='pre'`), while execution and valuation use raw tradable prices.

Identify contradictions with concrete examples. Do not choose a material interpretation for the customer.

**R0 hard stop and exit gate:** show the full R0 review table, ask for an explicit customer decision, and stop. Exit only after all material semantic contradictions are answered by the customer. Record the confirming customer statement in the workspace ledger; an agent-authored `true` value is not evidence.

## R1 - Program-specific capability inspection

After the customer has explicitly completed R0, inspect only capabilities required by this strategy:

- resolve the local backtest database in this order: `<current-project>/data/quantstudio.db`, then an explicitly approved configured/external DuckDB; record the absolute path, existence, selected tables, row/date coverage and resolution reason;
- tables, columns, PIT anchors and date coverage through the adapter/provider layer, including non-null `*_front` OHLC coverage for every required asset/frequency;
- local engine profile and lifecycle;
- PTrade backtest/trade context availability when `ptrade` is a selected target; otherwise record it as `NOT_APPLICABLE`;
- exact function signatures and permitted keyword names for the selected targets;
- data return shapes and code suffixes;
- for local dynamic ETF universes, `etf_basic` existence, classification version/source, listing/delisting coverage, `etf_daily` history coverage, and PIT checks at pre-listing/post-listing/post-delisting dates.

Classify each item as `READY`, `APPROXIMATION_REQUIRES_CONFIRMATION`, `DATA_BLOCKED`, `LOCAL_ONLY`, `PTRADE_CONTEXT_BLOCKED`, or `MISSING_REUSABLE_API`.

For every API planned for generated code, check `ptrade-api-signatures.json`. Examples:

- use `set_slippage(slippage=...)`, never `slippage_ratio=...`;
- use the literal `fq='pre'` on every signal-price `get_history`, `get_history_batch`, or `get_price` call; `attribute_history` is forbidden because its portable signature cannot prove front adjustment;
- use `get_stock_info(..., field=['listed_date'])`, not local `get_security_info()`;
- do not use `get_snapshot` or `check_limit` in PTrade backtest code; `get_open_orders(security=None)` is allowed but must use its documented signature;
- do not assume locally injected MyTT names such as `EMA` exist on PTrade; use NumPy/pandas or define a portable helper;
- dual ETF mode: block `get_etf_list`, `get_etf_list_local`, and `get_history_batch`; require the confirmed static whitelist;
- QuantStudio-only ETF mode: allow registered local extensions `get_etf_list_local` and `get_history_batch`, but do not run or claim PTrade validation.

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
  "execution_price_basis": "raw_trade_price"
},
"targets": ["quantstudio", "ptrade"],
"universe_contract": {
  "mode": "static_whitelist",
  "local_dynamic_api_allowed": false,
  "static_etf_whitelist": ["510050.SS", "510300.SS"]
},
"constraints": {
  "runtime_state_guard_required": true,
  "portable_source_required": true,
  "no_lookahead": true
}
```

Do not encode a renderer pattern or fixed strategy kind. For QuantStudio-only ETF mode set `targets=["quantstudio"]`, `portable_source_required=false`, and `universe_contract.mode="dynamic_local"` with `local_dynamic_api="get_etf_list_local"`.

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
    "component_plan": true,
    "static_etf_whitelist": true
  }
}
```

All approximations require `confirmed=true`. `static_etf_whitelist` is required only for dual ETF mode, but when required it must reflect the customer-confirmed codes rather than an agent-inferred list.

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

In dual mode, the agent implements strategy logic using the PTrade backtest public subset and the confirmed static ETF whitelist. In QuantStudio-only mode, the agent may use registered local extensions such as `get_etf_list_local` and `get_history_batch`, and the source must declare no PTrade portability. Do not hand-maintain divergent local/PTrade business logic. Use only injected APIs for data; local execution must route those APIs to the selected project DuckDB provider rather than opening storage from strategy source. Every signal-price call must spell `fq='pre'` literally. Use raw current/snapshot prices only for execution checks, order sizing, fill reconciliation, cash, and valuation; never concatenate them into a front-adjusted indicator series.

**R3 exit gate:** complete canonical source with no scaffold markers.

## R4 - Separate static validation

Run validation according to the confirmed target mode:

```powershell
# every mode
python scripts/validate_agent_strategy.py strategy.py --design agent_strategy_design.json --target-profile quantstudio

# dual mode only
python scripts/validate_agent_strategy.py strategy.py --design agent_strategy_design.json --target-profile ptrade
```

QuantStudio validation must pass in every mode. In dual mode, PTrade validation must also pass and checks real keyword names for every profiled API, rejects unverifiable `**kwargs`, checks context availability, local-only symbols, state-guard idempotence, lifecycle, timing, PIT, portability, and the mandatory literal `fq='pre'` signal-price contract. A permissive local adapter signature is never accepted as platform evidence.

Repair reusable incompatibilities in the adapter/profile/Skill first. Regenerate strategy output under the corrected rules; do not hand-patch only one target file.

**R4 exit gate:** dual mode = QuantStudio PASS + PTrade PASS; QuantStudio-only mode = QuantStudio PASS + `PTrade validation: NOT_APPLICABLE`. Reports remain separate and auditable.

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

The user runs PyQt and submits complete logs/report. Convert them into `user_backtest_evidence.json`, then run `scripts/review_user_backtest_evidence.py`. Evidence must bind to the candidate hash and include database path, selected window, capital, engine profile, match mode, completion/exception status, runtime checks, and a non-empty log excerpt.

Evidence results:

- incomplete or start before the ETF hard lower bound -> `USER_BACKTEST_EVIDENCE_INCOMPLETE`, remain R5;
- candidate hash drift -> return R4;
- strategy logic failure -> return R3;
- framework/data/API failure -> return R1;
- PTrade profile/validator failure -> return R4;
- complete PASS -> `stage=BACKTEST_PASS`, `formal_publish_allowed=true`.

Every repair invalidates old R4/R5 hashes. Regenerate the candidate after the new R4 PASS and require a new PyQt run.

**R5 exit gate:** hash-bound runtime evidence PASS for the exact candidate source, regardless of who executed the backtest.

## R6 - Target-aware generation, validation, and publication

`publish_agent_strategy.py` must branch from the confirmed `targets` contract.

### Dual mode

1. verify the workflow ledger and local backtest PASS;
2. generate both target files into staging;
3. validate the QuantStudio staging file;
4. validate the PTrade staging file against real public signatures/context;
5. run `validate_dual_consistency.py` only after both files physically exist;
6. publish atomically only when all checks PASS;
7. write both files to the default paths:
   - `quantstudio/backtest/strategies/<strategy_id>_quantstudio.py`
   - `ptrade/<strategy_id>_ptrade.py`

Never claim dual delivery from a pre-generation comparison or from comparing the canonical input with itself. The report must state `comparison_phase=post_generation_staging`.

### QuantStudio-only mode

1. verify the workflow ledger and local backtest PASS;
2. generate and validate only the QuantStudio staging file;
3. publish only `quantstudio/backtest/strategies/<strategy_id>_quantstudio.py`;
4. do not create an empty or placeholder PTrade file;
5. record exactly:
   - `PTrade validation: NOT_APPLICABLE`
   - `Dual consistency: NOT_APPLICABLE`
   - `PTrade output: NOT_GENERATED`

In user-PyQt mode, R6 must verify the validated candidate still exists with the same hash, generate fresh formal staging files from the canonical source, validate/compare them, atomically write formal targets, then remove the `__candidate` file. Record `candidate_status=PROMOTED` and `candidate_removed=true`. Never promote an edited candidate.

**R6 exit gate:** all applicable validation gates PASS, non-applicable gates explicitly recorded, hashes recorded, the temporary candidate retired when applicable, and publication PASS.

# PTrade compatibility rules

- API keyword names must match the platform exactly. Python acceptance through local `**kwargs` is forbidden as compatibility evidence.
- Backtest/trading/research context availability is enforced separately.
- Use PTrade `.SS/.SZ/.BJ` security suffixes in portable source and in local ETF-universe return values.
- Normalize comparison keys by bare six-digit code where API containers may differ.
- Use NumPy/pandas or source-defined helpers for indicators unless the PTrade public profile explicitly lists the indicator.
- Every `get_history`, `get_history_batch`, and `get_price` call used by generated backtest code must include the literal keyword `fq='pre'`; `dypre`, post-adjustment, missing/dynamic values, and `attribute_history` are blocked.
- A current completed minute may use `get_history(..., frequency='1m', fq='pre', include=True)` only in a confirmed scheduled minute callback with an explicit current-bar cutoff.
- `get_snapshot` and `check_limit` are not allowed in PTrade backtest source. `get_open_orders(security=None)` is allowed in backtest and trade contexts.
- `filter_stock_by_status` is called only from `before_trading_start`; scheduled callbacks use `get_stock_status` for current status checks.
- `get_etf_list()` is blocked in backtest source. Dual ETF strategies use the customer-confirmed static whitelist; QuantStudio-only ETF strategies use `get_etf_list_local()` through the provider/data-adapter chain.
- `get_etf_list_local()` returns metadata/PIT/code-format results only; MA, momentum, liquidity, abnormal-volume and ranking rules remain in strategy source.
- For customer-requested QuantStudio-only event strategies, external CSV/event data is ingested by the generic `strategy_events` adapter and queried with local extension `get_strategy_events`; set targets to `quantstudio` only and never claim PTrade portability.
- Use order rejection and documented price fields for backtest limit behavior; trading-only checks may be used only in a separately validated trading profile.

# Runtime failure repair protocol

When a customer supplies a real PTrade exception:

1. mark the prior PTrade validation/publication evidence stale;
2. reproduce the incompatibility with a minimal reusable API/profile test;
3. repair the local adapter signature/shape only so that local acceptance matches PTrade, and repair the Skill/profile/validator so future agents generate portable calls;
4. do not add a strategy ID, strategy-name branch, one-off template, or one-target hotfix;
5. regenerate the canonical output under the corrected generic rules;
6. rerun QuantStudio validation, PTrade validation, local backtest, physical dual-target generation, and post-generation consistency;
7. report the old and new hashes and explicitly retire the old upload artifact.

# Prohibited behavior

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
- calling `get_etf_list_local` or `get_history_batch` in dual/PTrade targets;
- calling `get_etf_list` in any backtest target;
- generating a dual ETF strategy before the static whitelist is explicitly confirmed;
- reporting PTrade PASS or dual-consistency PASS for a QuantStudio-only strategy;
- treating a PyQt candidate as a formal/PTrade upload artifact;
- accepting user backtest claims without candidate-hash, data-source, window and completion evidence;
- keeping a `__candidate` file after successful formal promotion;
- reusing R5 evidence after canonical/candidate source changes.

# Commands

- scaffold: `scripts/create_agent_workspace.py`
- profile validation: `scripts/validate_agent_strategy.py`
- consistency: `scripts/validate_dual_consistency.py`
- user-PyQt candidate: `scripts/prepare_user_backtest_candidate.py`
- user evidence review: `scripts/review_user_backtest_evidence.py`
- gated formal publish: `scripts/publish_agent_strategy.py`
- Skill validation: `scripts/quick_validate.py`

Legacy Spec/IR/Jinja compilation is used only when the customer explicitly requests legacy reproduction. It is never the default path for a new strategy.
