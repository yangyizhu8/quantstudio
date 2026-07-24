---
name: quantstudio-strategy-compiler
description: Strict agent-first strategy engineering for QuantStudio and PTrade. Use when an agent must convert a strategy idea or research specification into separately validated local and PTrade code through mandatory R0-R6 customer interaction, capability inspection, confirmed design, lifecycle/API composition, local backtesting, real PTrade public-signature validation, dual-code semantic consistency checks, and gated publication. Never skip stages or use strategy-specific renderer templates.
---

# QuantStudio Agent-first Strategy Engineering

Treat the calling agent as the strategy author. Constrain it with project lifecycle, data, timing, PTrade public API, validation, and delivery gates. Never implement a strategy by adding its name or shape to Compiler/Renderer/Jinja branches.

## Absolute execution rules

1. Execute stages strictly in order: `R0 -> R1 -> R2 -> R2.5 -> R3 -> R4 -> R5 -> R6`.
2. Never combine, infer-complete, retroactively mark, or silently skip a stage.
3. At every stage, show the customer the stage output and unresolved decisions. Stop when customer confirmation is required.
4. Do not generate executable strategy code before R2.5 explicit confirmation.
5. Do not publish before local backtest PASS, PTrade-profile validation PASS, and dual consistency PASS.
6. Persist stage/evidence in `agent_workspace/workspace_state.json`. Resume from this ledger; do not rely on conversational memory alone.
7. If any gate is BLOCKED, remain at that stage. Do not weaken requirements, substitute strategy semantics, or advance the ledger.
8. A generated strategy must target the **PTrade backtest public API subset**. QuantStudio adapts to PTrade-compatible calls, never the reverse.
9. R0 and R2.5 are real conversational stop points. The calling agent must not self-confirm, infer consent from silence, reuse an unrelated prior confirmation, or continue in the same turn without the customer's explicit answer.
10. Local backtests obtain data through QuantStudio providers. Prefer `<current-project>/data/quantstudio.db`; use a configured/external database only when the project-local database is absent or the customer explicitly approves the override. Strategy source must never open DuckDB itself.
11. Customer-provided PTrade runtime failures invalidate the previous PTrade PASS and R6 publication. Return to R1/R4, repair the reusable profile/adapter/Skill rule, regenerate both targets, and repeat post-generation consistency checks.

## Responsibility boundary

### Skill and project components own

- lifecycle signatures and callback timing;
- public API signatures and context availability;
- data/PIT/no-lookahead contracts and local backtest data-source resolution;
- A-share status, T+1, lot, cost and execution boundaries;
- local adapter compatibility with real PTrade signatures;
- static validation, local backtesting, PTrade-profile validation;
- generation of both target files and post-generation consistency checks.

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

## R0 - Customer strategy semantics

Present and confirm:

- point-in-time universe and ordering;
- exact mathematical definitions and warm-up;
- entry/exit predicates;
- signal cutoff, decision clock, execution clock and fill proxy;
- holding period, T+1/T+0, repeated-symbol and batch behavior;
- maximum simultaneous holdings, overlap, cash and leverage policy;
- order rejection, suspension and limit-state handling;
- costs, slippage and benchmark.

Identify contradictions with concrete examples. Do not choose a material interpretation for the customer.

**R0 hard stop and exit gate:** show the full R0 review table, ask for an explicit customer decision, and stop. Exit only after all material semantic contradictions are answered by the customer. Record the confirming customer statement in the workspace ledger; an agent-authored `true` value is not evidence.

## R1 - Program-specific capability inspection

After the customer has explicitly completed R0, inspect only capabilities required by this strategy:

- resolve the local backtest database in this order: `<current-project>/data/quantstudio.db`, then an explicitly approved configured/external DuckDB; record the absolute path, existence, selected tables, row/date coverage and resolution reason;
- tables, columns, PIT anchors and date coverage through the adapter/provider layer;
- local engine profile and lifecycle;
- PTrade backtest/trade context availability;
- exact function signatures and permitted keyword names;
- data return shapes and code suffixes.

Classify each item as `READY`, `APPROXIMATION_REQUIRES_CONFIRMATION`, `DATA_BLOCKED`, `LOCAL_ONLY`, `PTRADE_CONTEXT_BLOCKED`, or `MISSING_REUSABLE_API`.

For every API planned for generated code, check `ptrade-api-signatures.json`. Examples:

- use `set_slippage(slippage=...)`, never `slippage_ratio=...`;
- use `get_stock_info(..., field=['listed_date'])`, not local `get_security_info()`;
- do not use `get_snapshot` or `check_limit` in PTrade backtest code; `get_open_orders(security=None)` is allowed but must use its documented signature;
- do not assume locally injected MyTT names such as `EMA` exist on PTrade; use NumPy/pandas or define a portable helper.

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
"constraints": {
  "runtime_state_guard_required": true,
  "portable_source_required": true,
  "no_lookahead": true
}
```

Do not encode a renderer pattern or fixed strategy kind.

**R2 exit gate:** schema PASS and complete customer review package.

## R2.5 - Explicit customer hard confirmation

Show the full design, API component plan, approximations, platform differences, output paths and validation plan. Wait for explicit confirmation.

Require:

```json
{
  "open_questions": [],
  "user_confirmations": {
    "strategy_semantics": true,
    "execution_approximations": true,
    "component_plan": true
  }
}
```

All approximations require `confirmed=true`.

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

The agent implements strategy logic using the PTrade backtest public subset. Do not hand-maintain divergent local/PTrade business logic. Use only injected APIs for data; local execution must route those APIs to the selected project DuckDB provider rather than opening storage from strategy source.

**R3 exit gate:** complete canonical source with no scaffold markers.

## R4 - Separate static validation

Run the validator twice:

```powershell
python scripts/validate_agent_strategy.py strategy.py --design agent_strategy_design.json --target-profile quantstudio
python scripts/validate_agent_strategy.py strategy.py --design agent_strategy_design.json --target-profile ptrade
```

Both must pass. PTrade validation checks real keyword names for every profiled API, rejects unverifiable `**kwargs`, checks context availability, local-only symbols, state-guard idempotence, lifecycle, timing, PIT and portability. A permissive local adapter signature is never accepted as platform evidence.

Repair reusable incompatibilities in the adapter/profile/Skill first. Regenerate strategy output under the corrected rules; do not hand-patch only one target file.

**R4 exit gate:** QuantStudio PASS and PTrade PASS, with reports saved separately.

## R5 - Local backtest and runtime review

Run the confirmed engine profile. Verify callback times, orders, holdings, T+1, cash, overlap, empty pools, missing data and failure paths. Re-run both R4 validations after every material source change.

Record `workspace_state.json` with at least:

```json
{
  "stage": "BACKTEST_PASS",
  "backtest_status": "PASS",
  "backtest_data_source": "duckdb_provider",
  "backtest_db_path": "<absolute-current-project>/data/quantstudio.db",
  "backtest_db_resolution": "project_data_preferred"
}
```

Before accepting R5, verify the path actually exists and the backtest report names the same database/provider provenance. An external path requires a recorded customer-approved override.

If real PTrade runtime evidence is provided by the customer, treat every runtime exception as a profile/Skill/adapter regression first. Add a generic validator/test before regenerating outputs.

**R5 exit gate:** local backtest PASS and no unresolved PTrade-profile issue.

## R6 - Generate both targets, then compare, then publish

`publish_agent_strategy.py` must:

1. verify the workflow ledger and local backtest PASS;
2. generate both target files into a staging directory;
3. read the generated QuantStudio staging file and validate it separately;
4. read the generated PTrade staging file and validate it separately against real public signatures/context;
5. run `validate_dual_consistency.py` only after both staging files physically exist;
6. compare lifecycle functions, schedules, public API calls, strategy parameters and normalized semantic AST;
7. publish atomically only when all checks PASS;
8. write local, PTrade, consistency and publish reports.

Never claim dual delivery from a pre-generation comparison, from comparing the canonical input with itself, or from hashes calculated before target generation. Consistency is checked **after both target files are generated**, and the report must state `comparison_phase=post_generation_staging`.

**R6 exit gate:** local validation PASS, PTrade validation PASS, consistency PASS, hashes recorded, publication PASS.

# PTrade compatibility rules

- API keyword names must match the platform exactly. Python acceptance through local `**kwargs` is forbidden as compatibility evidence.
- Backtest/trading/research context availability is enforced separately.
- Use PTrade `.SS/.SZ/.BJ` security suffixes in portable source.
- Normalize comparison keys by bare six-digit code where API containers may differ.
- Use NumPy/pandas or source-defined helpers for indicators unless the PTrade public profile explicitly lists the indicator.
- A current completed minute may use `get_history(..., frequency='1m', include=True)` only in a confirmed scheduled minute callback with an explicit current-bar cutoff.
- `get_snapshot` and `check_limit` are not allowed in PTrade backtest source. `get_open_orders(security=None)` is allowed in backtest and trade contexts.
- `filter_stock_by_status` is called only from `before_trading_start`; scheduled callbacks use `get_stock_status` for current status checks.
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
- using local-only APIs in canonical source;
- assuming `initialize` always completes before other callbacks;
- publishing before both profile validations and post-generation consistency PASS;
- editing only one published target to make it pass;
- adding strategy IDs/names to Skill, Compiler, Renderer or templates;
- selecting an external database while `<current-project>/data/quantstudio.db` exists without explicit customer approval;
- patching only the currently failing strategy or only the PTrade copy instead of repairing the reusable contract and regenerating both outputs.

# Commands

- scaffold: `scripts/create_agent_workspace.py`
- profile validation: `scripts/validate_agent_strategy.py`
- consistency: `scripts/validate_dual_consistency.py`
- gated publish: `scripts/publish_agent_strategy.py`
- Skill validation: `scripts/quick_validate.py`

Legacy Spec/IR/Jinja compilation is used only when the customer explicitly requests legacy reproduction. It is never the default path for a new strategy.
