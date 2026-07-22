# PR5 Implementation Report: Strategy Compiler Skill Skeleton

Status: DONE (0.1.0-skeleton, 2026-07-22) — awaiting user confirmation gate

## Goal (master plan §7.28)

Establish the formal Skill, multi-round flow, references, schemas, and tool scripts. PR5 is scaffolding only — no runtime code touched, no IR/renderers (those are PR6).

## Deliverables (master plan §7.29, all 10 items)

Skill location: `skills/quantstudio-strategy-compiler/` (in-project, version-controlled with the contracts it derives from).

| §7.29 item | Delivery |
|---|---|
| Use skill-creator spec to init | SKILL.md frontmatter conforms to quick_validate's 7 rules |
| SKILL.md concise | 8 duties (§7.30), no knowledge dumped inline |
| Description names trigger场景 | "compile/generate/convert strategy into backtestable code" + dual-platform |
| Detailed knowledge → references | 11 reference files (8 derived from docs/ + 3 new aggregations) |
| agents/openai.yaml | short_description 51 chars (25-64 range), default_prompt contains `$quantstudio-strategy-compiler` |
| Capability inspection script | `scripts/inspect_capabilities.py` (minimal runnable, real DB probe) |
| Spec validation script | `scripts/validate_strategy_spec.py` (schema-based + 2 extra checks) |
| User confirmation gate | R2.5 hard gate documented in SKILL.md (no code before confirmation) |
| Output directory convention | `output/generated_strategies/<strategy_id>/`; enforced by inspect (output_dir_writable capability) |
| Pass quick validation | `quick_validate.py` exit 0 (verified twice) |

## File inventory

```
skills/quantstudio-strategy-compiler/
├── SKILL.md                          # frontmatter + 8 duties + R2.5 gate + sync discipline
├── agents/openai.yaml                # interface (display_name/short_description/default_prompt)
├── references/                       # 11 files, each with source-header blockquote
│   ├── framework-contract.md         # 58 lines ← architecture.md
│   ├── lifecycle-and-timing.md       # 65 lines ← lifecycle-and-timing-contract.md
│   ├── frequency-and-engine-profiles.md  # 50 lines ← frequency-and-engine-profile.md
│   ├── strategy-spec-contract.md     # 57 lines ← strategy-spec-contract.md
│   ├── strategy-ir-contract.md       # new (PR6 placeholder, node list + per-node contract)
│   ├── api-capability-matrix.md      # 40 lines ← capability-model.md (12 words + 6 rules)
│   ├── ptrade-profiles.md            # 34 lines ← ptrade-profile-contract.md
│   ├── ashare-hard-filters.md        # 81 lines ← ashare-filter-contract.md + security-code-rules.md
│   ├── no-lookahead-rules.md         # new (aggregates lifecycle §3 + master §9, 6+10 rules)
│   ├── output-contract.md            # 51 lines ← output-and-run-card-contract.md
│   └── known-limitations.md          # new (aggregates PR3/PR4 limits + PR5/PR6 scope)
├── schemas/                          # copied byte-identical from PR0
│   ├── strategy_spec.schema.json
│   ├── capability_report.schema.json
│   └── run_card.schema.json
└── scripts/
    ├── inspect_capabilities.py       # minimal runnable
    └── validate_strategy_spec.py     # minimal runnable
```

## Acceptance (master plan §7.31, all 6)

| # | Criterion | Evidence |
|---|---|---|
| 1 | Skill metadata valid | `python C:\Users\Administrator\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills/quantstudio-strategy-compiler` → "Skill is valid!" exit 0 |
| 2 | Triggers in context-free new session | description names "compile / generate / build a strategy into backtestable code", "dual-platform versions", "idea to executable spec" |
| 3 | No unnecessary large docs | SKILL.md "load on demand" section; references read per-round, not preloaded |
| 4 | Distinguishes daily/minute/proxy/Planned Profile | SKILL.md "Profile awareness" section + frequency-and-engine-profiles.md; tick never READY enforced |
| 5 | No code before R2.5 confirmation | SKILL.md "R2.5 hard gate" + "When code generation is allowed" (3 conditions, PR6 renderer never met in PR5) |
| 6 | No smoke claim when non-READY | SKILL.md "Mandatory first step" + inspect reports honest overall_execution_status |

## Live script runs (smoke evidence)

### inspect_capabilities.py (real DB, profile=minute-bar-v1)

```
$ python skills/.../inspect_capabilities.py --db <real_db> --profile minute-bar-v1 --strategy-id pr5_smoke_test
=== Capability Report (profile=minute-bar-v1) ===
Overall: READY
Capability                     Req   Event      Exec       Data           Engine
-------------------------------------------------------------------------------------
stock_daily_backtest           True  bar        READY      AVAILABLE      READY
stock_minute_backtest          True  bar        READY      AVAILABLE      READY
tick_backtest                  False tick       PLANNED    DATA_MISSING   ENGINE_MISSING
output_dir_writable            True  reference  READY      AVAILABLE      READY
Schema self-check: PASS (capability_report.schema.json)
```

Honest probe (per decision: don't copy example status): stock_minutes/etf_minutes report AVAILABLE (real xtquant data ingested 2026-07-22), unlike the PR3-era DATA_MISSING example. tick is PLANNED (invariant 4: tick never READY in v1). Schema self-check confirms output conforms to capability_report.schema.json.

### validate_strategy_spec.py (PR0 example + 3 violation variants)

```
$ python skills/.../validate_strategy_spec.py quantstudio/strategy_compiler/examples/strategy_spec.example.json
VALID: ... passes schema + consistency checks.  (exit 0)
```

3 violation variants (constructed) all caught red (exit 1):
- `tick_with_bar_freq` → schema catches bar_frequency type + execution_status (allOf line 73)
- `daily_close_proxy_lookahead` (T-close + current_bar) → schema allOf `not` rule (line 77)
- `spec_version_mismatch` (1.0 vs 2.0.0) → extra consistency check (schema can't enforce)

This confirms the division of labor: schema enforces structural rules (tick/proxy/lookahead/stock hard_filters via allOf); validate adds only what schema cannot (spec_version ↔ contract_versions major alignment, capability ID existence as warning).

## Invariant enforcement (capability-model.md §2, all 4 in inspect)

1. `execution_status=READY` → six dims only AVAILABLE/READY — `_derive_execution_status` checks all six
2. any required non-READY → overall not READY — `_derive_overall` collects blockers
3. all required READY → overall must be READY — `_derive_overall` returns READY when no blockers
4. **tick never READY in v1** — `_build_tick_capability` hardcodes PLANNED; `_tick_invariant` defensive check; `_derive_execution_status` forces non-READY for tick event_type

## Boundaries (what PR5 does NOT do — decision chain recorded)

| Item | Status | Reason |
|---|---|---|
| IR (`build_strategy_ir.py`) | NOT built (PR6) | §7.33 IR design is PR6; SKILL.md stops at Spec |
| Renderers (`render_quantstudio.py`/`render_ptrade.py`) | NOT built (PR6) | §7.34 is PR6 |
| 7 PR6 validators | NOT built (PR6) | §7.35; validate_strategy_spec here is schema-level only, not IR-level |
| templates/ (`.j2` + json) | NOT built (PR6) | Rendering output, PR6 |
| **install_skill.py** | **NOT built (scope change)** | Originally in minimal-skeleton scripts list (master plan §4.2 line 296), removed because §7.29 does not mandate it. **Re-added in PR6.** Decision: PR5 plan review 2026-07-22, user-approved. |
| Runtime code (backtest/pipeline) | NOT touched | PR5 is pure Skill scaffolding |
| Fidelity gate | NOT run | PR5 is non-runtime; does not trigger the mandatory-gate list (strategy-fidelity-regression-gate.md §83-93). pytest 389 passed confirms zero runtime regression. |

## Sync discipline (decision: derived snapshots, not links)

`docs/strategy-compiler/` is the authoritative source. `references/` here are derived snapshots (each file's header names its source + derivation date 2026-07-22). Rationale (user decision): acceptance criterion "no unnecessary large docs" forbids linking full docs; symlinks have Windows portability issues; moving docs breaks live references in handoff/reports. Tradeoff accepted: two-place sync required. SKILL.md "Synchronization discipline" section + this report record the rule; PR6/PR7 checklist must verify reference freshness.

## Verification summary

- `quick_validate.py`: PASS (exit 0, verified twice)
- `inspect_capabilities.py`: live run PASS (overall READY, schema self-check PASS, honest probe)
- `validate_strategy_spec.py`: PR0 example green (exit 0) + 3 violation variants red (exit 1)
- `pytest`: 389 passed, zero runtime regression
- Skill version: 0.1.0-skeleton

## Next gate

PR5 DONE (0.1.0-skeleton). Awaiting user confirmation before PR6 (IR + dual renderers + 7 validators). PR6 will: implement IR nodes (§7.33), build_strategy_ir.py + render_quantstudio.py + render_ptrade.py (§7.34), the 7 validators (§7.35), re-add install_skill.py, and fill templates/.
