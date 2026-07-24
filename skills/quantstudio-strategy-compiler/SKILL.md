---
name: quantstudio-strategy-compiler
description: Turn a strategy idea or spec into validated QuantStudio plus PTrade code. Triggers on intent to compile/generate/convert a strategy into backtestable code, build dual-platform versions, or move from natural-language idea to executable spec. Always gates on capability inspection and explicit user confirmation before any code generation.
---

# QuantStudio Strategy Compiler

Compile strategy ideas into validated, backtestable QuantStudio + PTrade code, gated by capability inspection and user confirmation.

**Skill version**: 0.3.0-mvp (G1-I basket + G2 CP3 reference + G3 package + G4 CLI release). Full compile pipeline delivered: Spec -> IR -> dual render (QuantStudio + PTrade) -> strategy package, drivable via the `qs-compile` CLI (`quantstudio.strategy_compiler.cli`). G2 CP3 reference closure (Hermetic Partial, data digest blocked) + G3 deterministic package closure now in main. Real market-data digest/Fidelity/Reference verification deferred.

## When to trigger

Invoke when the user expresses intent to:
- "compile / generate / build a strategy" into backtestable code
- convert a strategy idea / natural-language description / research note into a Spec or code
- produce dual-platform (QuantStudio + PTrade) versions from one source
- move from "I have a strategy idea" to "I have validated code"

Do NOT trigger for: pure backtest tuning of an existing strategy, data ingestion ops, or Fidelity gate runs (those have their own workflows).

## Mandatory first step: capability inspection (R-1)

Before drafting any Spec, run `scripts/inspect_capabilities.py` against the live DuckDB. The report's `overall_execution_status` gates everything that follows:
- `READY` -> may proceed to Spec draft (R2)
- `BLOCKED` / `PLANNED` / `UNSUPPORTED` -> STOP. Report the blockers and remediation to the user. Do not draft a Spec that pretends capabilities are ready.

Never skip R-1. Never copy status values from examples -- inspect the real environment (current state: stock_minutes/etf_minutes have real xtquant data, unlike the PR3-era DATA_MISSING example).

## Multi-round interaction order

The Skill auto-orchestrates the full pipeline. The user only needs to:
1. Describe their strategy in natural language
2. Review and confirm the Spec

Everything else (validation + package generation) is automatic.

### R0 — Idea parsing
Decompose the user's natural-language idea into:
- Universe (stock/ETF pool, single stock, dynamic pool)
- Indicators (MA, momentum, volume surge, etc.)
- Entry / exit signals
- Rebalance logic (daily, target weight)
- Execution (market/limit, next_open/close)
- Risk management (stop-loss, position limits)
- Cost model (commission, stamp tax, slippage)
- Data requirements (frequency, lookback window)

### R-1 — Capability inspection
Run `scripts/inspect_capabilities.py` against the live DuckDB. Present honest status:
- `READY` → may proceed to Spec draft
- `BLOCKED` / `PLANNED` / `UNSUPPORTED` → STOP. Report blockers and remediation.
- Never fabricate data capabilities.

### R2 — Spec draft
Generate `strategy_spec.json` conforming to `schemas/strategy_spec.schema.json`.
Validate with `scripts/validate_strategy_spec.py`.
Present to the user:
- Full Spec content
- Key assumptions and approximations
- Data capability boundaries (READY vs BLOCKED)
- Risk warnings
- Explicit confirmation items in `user_confirmations`

**Do not generate strategy code or packages before user confirms.**

### R2.5 — User confirmation (HARD GATE)
Present the Spec to the user with approximations, capability gaps, and hard-filter
settings called out. Wait for explicit CONFIRMED. No code generation until then.

### R3 — Validation (orchestrator)
After confirmation, run the orchestrator:
```
python -m quantstudio.strategy_compiler.orchestrator <spec.json> [--start] [--end] [--no-smoke]
```
This builds IR, renders dual-platform `.py`, runs 7 static validators, inspects
capabilities, runs smoke backtest (if READY), and writes:
- `run_card.json` (stage + status + validation results)
- `capability_report.json`
- `variant_consistency_report.json`
- `strategy_ir.json` + `<id>_quantstudio.py` + `<id>_ptrade.py`

**Gate**: If static validation fails, STOP. Do not proceed to package generation.
If smoke backtest is BLOCKED (capability/data boundary), record honestly — BLOCKED
is acceptable as a known deferred boundary; PASS is never faked.

### R4 — Package generation (qs-compile)
Only after R3 validation passes (or smoke is BLOCKED on a known deferred boundary),
automatically call qs-compile to generate the strategy package:
```
qs-compile package <spec.json> --out <dir> [--g2-frozen-dir <dir>] [--package-version <ver>]
```
Or via the delivery orchestration layer (preferred for Skill workflow):
```python
# Skill-local delivery script (not in the released wheel; lives in Skill scripts/)
# python skills/quantstudio-strategy-compiler/scripts/deliver_strategy.py <spec> --out <dir> [--g2-frozen-dir <dir>] [--allow-deferred-smoke]
```

This generates the structured strategy package with:
- `manifest.json` (version, entry points, artifact SHA-256 digests)
- `<id>_quantstudio.py` + `<id>_ptrade.py` (dual-rendered)
- `strategy_spec.json` + `strategy_ir.json` (frozen)
- `__init__.py` + `README.md`

Verify: manifest schema valid, all artifact digests match, both strategies
compile (ast.parse + compile), G2 linkage data_digest_status=blocked (not faked).

**If qs-compile is not in PATH**: report the installation error clearly. Do NOT
silently skip package generation. Development fallback: `python -m quantstudio.strategy_compiler.cli`.

### R5 — Delivery summary
Return to the user:
- Validation output directory (`validation/`)
- Strategy package output directory (`package/`)
- `manifest.json` — artifact digests + G2 linkage
- `run_card.json` — validation status
- `capability_report.json` — data/execution readiness
- `variant_consistency_report.json` — dual-platform consistency
- `DELIVERY_REPORT.md` — unified summary
- Known limitations + data digest/Fidelity deferred explanation
- Clear conclusion: can the user use the delivered files?

## Delivery output directory

The `deliver_strategy()` function creates a unified output:
```
output/strategy_deliveries/<strategy_id>/
├── validation/              ← orchestrator artifacts (run_card, IR, validators)
│   ├── strategy_spec.json
│   ├── strategy_ir.json
│   ├── capability_report.json
│   ├── variant_consistency_report.json
│   ├── run_card.json
│   ├── <id>_quantstudio.py
│   └── <id>_ptrade.py
├── package/                 ← qs-compile strategy package
│   └── <id>__<version>/
│       ├── manifest.json
│       ├── strategy_spec.json
│       ├── strategy_ir.json
│       ├── <id>_quantstudio.py
│       ├── <id>_ptrade.py
│       ├── __init__.py
│       └── README.md
└── DELIVERY_REPORT.md       ← unified delivery summary
```

## R2.5 hard gate -- no code before confirmation

Generating `.py` strategy code before R2.5 user confirmation is forbidden. The Spec draft must be shown to the user with approximations, capability gaps, and hard-filter settings called out. Only after the user explicitly confirms may rendering proceed. The orchestrator enforces golden protection at render time (protected IDs raise) but cannot read user intent -- present the Spec and wait for CONFIRMED first.

## When to stop

- Capability inspection returns non-READY for a required capability -> stop, report blockers.
- Data gap unfilled -> stop, do not silently shrink the universe to "make it work".
- User has not confirmed R2.5 -> stop at Spec, do not render.
- The target strategy_id collides with a golden protected strategy (gate IDs `etf_momentum` / `smallcap_guard` in config/strategy_fidelity_gates.json, or the dual-MA sample strategy) -> stop, do not auto-overwrite.
- A requested Profile is Planned/Unsupported (e.g. tick) -> stop, report.

## When code generation is allowed

Only when ALL hold:
1. R2.5 user confirmation recorded in `user_confirmations` with `status: CONFIRMED`.
2. `inspect_capabilities.py` reports `overall_execution_status: READY` for every required capability (the orchestrator gates smoke on this; non-READY produces `smoke_backtest.status=BLOCKED`, never a false PASS).
3. The renderer + validators are available (DELIVERED in PR6a/PR6b-1 via `quantstudio.strategy_compiler`).

## Profile awareness

Distinguish four Profiles precisely (see `references/frequency-and-engine-profiles.md`):
- **Daily-bar** (`daily-bar-v1`): `event_type=bar`, `bar_frequency=1d`. Most strategies. READY.
- **Minute-bar** (`minute-bar-v1`): `event_type=bar`, `bar_frequency=1m/5m/...`. READY (PR4 verified on real data).
- **Tick** (`event_type=tick`): first version execution_status must be BLOCKED/PLANNED/UNSUPPORTED -- never READY.
- **Planned**: not a real Profile; a Spec may declare intent, but execution is BLOCKED.

Never confuse daily/minute proxy modes (`daily_open_proxy` pairs with `match_price_mode=open`; `daily_close_proxy` pairs with `close`) -- the schema enforces these pairings, do not work around them.

## References (load on demand, do NOT preload all)

Read only what the current round needs:
- Spec fields/constraints -> `references/strategy-spec-contract.md`
- Capability status vocabulary & invariants -> `references/api-capability-matrix.md`
- Lifecycle/timing/no-lookahead -> `references/lifecycle-and-timing.md` + `references/no-lookahead-rules.md`
- Frequency/Profile -> `references/frequency-and-engine-profiles.md`
- A-share hard filters & code rules -> `references/ashare-hard-filters.md`
- PTrade profile -> `references/ptrade-profiles.md`
- Output & Run Card -> `references/output-contract.md`
- Architecture/invariants -> `references/framework-contract.md`
- Known limits -> `references/known-limitations.md`
- IR (PR6a/PR6b-1) -> `references/strategy-ir-contract.md` (11 node types, signals.steps mapping, 10 lookahead high-risk items)

Do not read all references up front -- that defeats the "no unnecessary large docs" acceptance criterion.

## Output and validation

- Spec lands at `output/generated_strategies/<strategy_id>/strategy_spec.json`.
- After drafting, run `scripts/validate_strategy_spec.py` self-check; fix all violations before presenting to user.
- After R2.5 confirmation, the Skill runs the full delivery flow via `deliver_strategy()`:
  - **Validation** (R3): orchestrator writes `strategy_ir.json`, `<id>_quantstudio.py`, `<id>_ptrade.py`, `capability_report.json`, `variant_consistency_report.json`, `run_card.json`.
  - **Package** (R4): qs-compile generates the structured strategy package with manifest + digests.
  - **Report** (R5): `DELIVERY_REPORT.md` provides the unified summary.
- Run Card `stage` records the pipeline step reached; `status` records PASS/BLOCKED/FAILED. Fidelity comparison (R7) is PR7 scope (`fidelity` field stays null).
- Do not auto-overwrite existing strategies; `output.overwrite` must be explicit in the Spec.

## Three-layer architecture

1. **quantstudio-strategy-compiler Skill** (AI workflow layer): understands natural language, generates Spec, gates on user confirmation, orchestrates validation + delivery.
2. **orchestrator** (validation engine): Spec schema → IR → dual render → 7 validators → capability inspection → smoke backtest → run_card.
3. **qs-compile** (CLI delivery entry): Spec → IR → dual Renderer → structured strategy package with manifest + digests. Direct CLI for advanced users / scripts / CI.

Normal users: interact with the Skill; the Skill auto-calls orchestrator then qs-compile.
Advanced users: `qs-compile package <spec> --out <dir>` directly.

## Synchronization discipline (references are derived snapshots)

`docs/strategy-compiler/` is the **authoritative source** for contracts. `references/` here are derived snapshots taken 2026-07-22 (see each file's header for its source). When a contract document changes upstream:
1. Update the corresponding `references/*.md` snapshot.
2. Bump `skill_version` if the change affects Skill behavior.
3. Do not let the two drift -- PR6b-1 already refreshed `known-limitations.md` to reflect delivered IR/render/validators.

## Golden protected strategies (never auto-overwrite)

The golden protected strategies (Chinese-named sample files referenced by `etf_momentum` / `smallcap_guard` gate configs, plus the dual-MA sample) carry frozen Fidelity baselines (ETF 87752.56+/-1). Any change requires the golden-baseline change protocol (zcode-handoff S.11), not a silent regeneration.
