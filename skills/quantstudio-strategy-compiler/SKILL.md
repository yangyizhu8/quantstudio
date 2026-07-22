---
name: quantstudio-strategy-compiler
description: Turn a strategy idea or spec into validated QuantStudio plus PTrade code. Triggers on intent to compile/generate/convert a strategy into backtestable code, build dual-platform versions, or move from natural-language idea to executable spec. Always gates on capability inspection and explicit user confirmation before any code generation.
---

# QuantStudio Strategy Compiler

Compile strategy ideas into validated, backtestable QuantStudio + PTrade code, gated by capability inspection and user confirmation.

**Skill version**: 0.1.0-skeleton (PR5). Code rendering (R3+) arrives in PR6; this Skill currently stops at Spec (R2.5).

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

1. **R0 -- Idea parsing**: decompose the user's natural-language idea into universe / indicators / entry / exit / rebalance.
2. **R-1 -- Capability inspection**: run `inspect_capabilities.py`, present the honest status.
3. **R2 -- Spec draft**: produce a `strategy_spec.json` conforming to `schemas/strategy_spec.schema.json`. Validate with `scripts/validate_strategy_spec.py`.
4. **R2.5 -- User confirmation (HARD GATE)**: present the Spec to the user. **No code generation until explicit confirmation.**
5. **R3+ -- Rendering (PR6, NOT this Skill version)**: IR -> dual renderers -> smoke backtest. SKILL.md notes this is out of PR5 scope.

## R2.5 hard gate -- no code before confirmation

Generating `.py` strategy code before R2.5 user confirmation is forbidden. The Spec draft must be shown to the user with approximations, capability gaps, and hard-filter settings called out. Only after the user explicitly confirms may rendering proceed -- and rendering itself is PR6, so in PR5 the workflow stops at the confirmed Spec.

## When to stop

- Capability inspection returns non-READY for a required capability -> stop, report blockers.
- Data gap unfilled -> stop, do not silently shrink the universe to "make it work".
- User has not confirmed R2.5 -> stop at Spec, do not render.
- The target strategy_id collides with a golden protected strategy (gate IDs `etf_momentum` / `smallcap_guard` in config/strategy_fidelity_gates.json, or the dual-MA sample strategy) -> stop, do not auto-overwrite.
- A requested Profile is Planned/Unsupported (e.g. tick) -> stop, report.

## When code generation is allowed

Only when ALL hold:
1. R2.5 user confirmation recorded in `user_confirmations` with `status: CONFIRMED`.
2. `inspect_capabilities.py` reports `overall_execution_status: READY` for every required capability.
3. (PR6) Renderer is available -- in PR5 this condition is never met, so PR5 never generates code.

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
- IR (PR6) -> `references/strategy-ir-contract.md` (placeholder until PR6)

Do not read all references up front -- that defeats the "no unnecessary large docs" acceptance criterion.

## Output and validation

- Spec lands at `output/generated_strategies/<strategy_id>/strategy_spec.json`.
- After drafting, run `scripts/validate_strategy_spec.py` self-check; fix all violations before presenting to user.
- Run Card stage after PR5 = `SPEC_ONLY` (smoke/Fidelity stages need PR6 rendering).
- Do not auto-overwrite existing strategies; `output.overwrite` must be explicit in the Spec.

## Synchronization discipline (references are derived snapshots)

`docs/strategy-compiler/` is the **authoritative source** for contracts. `references/` here are derived snapshots taken 2026-07-22 (see each file's header for its source). When a contract document changes upstream:
1. Update the corresponding `references/*.md` snapshot.
2. Bump `skill_version` if the change affects Skill behavior.
3. This sync check is on the PR6/PR7 checklist -- do not let the two drift.

## Golden protected strategies (never auto-overwrite)

The golden protected strategies (Chinese-named sample files referenced by `etf_momentum` / `smallcap_guard` gate configs, plus the dual-MA sample) carry frozen Fidelity baselines (ETF 87752.56+/-1). Any change requires the golden-baseline change protocol (zcode-handoff S.11), not a silent regeneration.
