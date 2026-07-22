# PR6b-1 Implementation Report — Validation Closure (5 validators + orchestrator + install)

> Date: 2026-07-23
> Status: DELIVERED (33 PR6a + 38 PR6b-1 tests green; real case1 smoke PASS on live engine)
> Skill version: `0.2.0-pr6b1` (bumped from `0.1.0-skeleton`)
> Renderer version: still `0.1.0-pr6a-skeleton` (PR6b-2 golden cases will bump to `1.0.0`)
> Scope: PR6b split into PR6b-1 (validation closure) / PR6b-2 (golden cases). This report covers PR6b-1 only.

## 0. Scope (PR6b-1 = validation closure)

PR6b was split (authorized 2026-07-22) into:
- **PR6b-1 (this report)**: 5 validators + orchestrator + install_skill + operation expansion (`pct_change`/`rank`/`top_n`/`bottom_n`) + `manual_list` multi-stock universe + document-debt fix (minute `DATA_MISSING` → `READY`).
- **PR6b-2 (remaining)**: 9 golden cases + `index_constituents` + Factor ops (`zscore`/`winsorize`/`neutralize`/`combine`) + cost passthrough (`set_commission`/`set_slippage`) + RiskNode stop_loss/take_profit + tick smoke golden case 6.

PR6b-1 does **not** shortcut PR6b-2 scope; it ships the validation infrastructure that PR6b-2 cases will consume.

## 1. Deliverables

### New files (12)
| Path | Purpose |
|---|---|
| `quantstudio/strategy_compiler/orchestrator.py` | **End-to-end pipeline**: spec→IR→render→7 validators→`run_card.json` + `variant_consistency_report.json`. Single writer of run_card (validators stay pure). Stage machine SPEC_ONLY→STATIC_VALIDATED→SMOKE_EXECUTED. |
| `quantstudio/strategy_compiler/validators/run_smoke_backtest.py` | R6 gate: reads `capability_report.overall_execution_status`; ≠READY→BLOCKED (R6 line583 honest message, engine NOT invoked); =READY→subprocess `run_ptrade_strategy` (encoding=utf-8, PYTHONIOENCODING=utf-8). |
| `quantstudio/strategy_compiler/validators/check_hard_filters.py` | HARDFILTER-STOCK-13 (all 13) + EXECUTION-STAGE + EXECUTION-SUBSET (4) + ASSET-MATCH. |
| `quantstudio/strategy_compiler/validators/validate_ptrade_portability.py` | DENYLIST 6 APIs (batch + file/DB). PORTABILITY-LOCAL-EXTENSION-BAN / PORTABILITY-FILE-DB-ACCESS. |
| `quantstudio/strategy_compiler/validators/compare_strategy_variants.py` | 14-dimension QS vs PTrade. 11 IDENTICAL_BY_CONSTRUCTION; dim10 stop-loss EMPTY (PR6b-2); dim13 cost GAP (PR6b-2); dim14 API AST diff (load-bearing). |
| `skills/.../scripts/install_skill.py` | copytree → quick_validate → rollback on failure. Installs to `~/.agents/skills/`. |
| `tests/test_pr6b1_validators.py` | 5 validators positive + negative (遗留要求②: assert 被阻断). |
| `tests/test_pr6b1_orchestrator.py` | case1 e2e + tick BLOCKED path + golden protection + run_card schema. |
| `tests/test_pr6b1_operation_expansion.py` | pct_change/rank/top_n + manual_list + 遗留要求① batch diff (AST) + zscore PR6b-2 raise. |
| `tests/test_pr6b1_install_skill.py` | install + validate + rollback + force + pycache exclusion. |

### Modifications (8, CP1-5 carried from prior session + review fixes)
| Path | Change |
|---|---|
| `build_strategy_ir.py` | `_PR6B1_INDICATOR_OPS={ma,pct_change}`; `_PR6B1_RANKING_OPS={rank,top_n,bottom_n}`; pct_change numpy inline + RankingNode branch; manual_list codes passthrough; raise PR6a→PR6b-1. |
| `render.py` | pct_change Indicator walk + RankingNode walk in `_build_template_context`. |
| `contracts.py` | RankingNode predecessor bug fix (`_IR_PIPELINE_PREDECESSORS` cleared; `_EITHER_PREDECESSORS` extended with SignalNode). |
| `skills/.../scripts/validate_strategy_spec.py` | Converged to `contracts.validate_strategy_spec` (5 timing rules, single source of truth) + defensive import. |
| `skills/.../templates/quantstudio_daily.py.j2` + `ptrade_daily.py.j2` (+ minute) | manual_list multi-stock branch (QS emits `get_history_batch` / PTrade loops `get_history`). |
| `skills/.../templates/quantstudio_minute.py.j2` + `ptrade_minute.py.j2` | manual_list branch (review fix: previously raised NotImplementedError on non-single_stock; now renders the minute multi-stock rotation in `_trade_on_minute`). |
| `docs/strategy-compiler/frequency-and-engine-profile.md` | minute-bar-v1 BLOCKED(DATA_MISSING) → READY (data now in DB). |
| `docs/strategy-compiler/implementation-status.md` | stale →实测 row counts + smoke PASS + end-labeled VERIFIED. |

## 2. Acceptance results

### Test results
```
tests/test_pr6a_case1_e2e.py + test_pr6a_validators_negative.py  → 33 passed (no regression)
tests/test_pr6b1_validators.py + orchestrator + operation_expansion + install_skill → 38 passed
Total PR6a+PR6b-1: 71 passed
```

### Real smoke (case1, live engine, CP10)
```
orchestrate(case1_spec, start=2026-01-01, end=2026-04-29, run_smoke=True)
  → stage=SMOKE_EXECUTED, status=PASS, smoke.status=PASS
  → execution_status=READY, known_limitations=0
  → run_card.json conforms to run_card.schema.json (Draft7Validator PASS)
```
The READY→subprocess path (CP6) executed the real `quantstudio.backtest.run_ptrade_strategy` engine on the live DuckDB; engine exited 0, `结果导出` printed. This confirms CP6's subprocess + encoding handling + CP7's end-to-end assembly on real data.

### install_skill + quick_validate
```
install_skill.py --dest-root <tmp> → "installed + quick_validate PASS"
rollback on invalid Skill (missing description) → dest cleaned, "rolled back"
__pycache__ excluded from install
```

## 3. Architecture decisions

### 3.1 Orchestrator = single run_card writer
Validators are pure functions returning result tuples `(ok, violations, warnings, ...)`. The orchestrator collects these and maps each to the run_card schema fields in **one place** (handoff §1 关键决策). This keeps validators testable in isolation and avoids scattered file writes.

Field mapping (handoff §2 CP7):
| run_card field | Validator |
|---|---|
| `validation.schema` | `contracts.validate_strategy_spec` |
| `validation.timing` | `scan_lookahead` |
| `validation.hard_filters` | `check_hard_filters` |
| `validation.api_portability` | `validate_local_strategy` (QS+PTrade) + `validate_ptrade_portability` |
| `validation.variant_consistency` | `compare_strategy_variants` |
| `smoke_backtest` | `run_smoke_backtest` |
| `stage` | state machine (advances as gates pass) |

`variant_consistency_report.json` is written alongside `run_card.json` (full 14-dimension detail; run_card stores only the PASS/BLOCKED summary).

### 3.2 R6 capability gate (honest blocking)
`run_smoke_backtest` reads `capability_report.overall_execution_status`. If ≠ READY:
- `smokeResult.status = BLOCKED`
- summary = R6 line583 message ("代码已生成 / 静态检查已通过 / 执行验证被能力门禁阻止")
- engine **NOT invoked**
- stage still advances to SMOKE_EXECUTED (the smoke *step* ran honestly; the engine was gated)

This means a tick-profile strategy (invariant 4: tick never READY) produces `stage=SMOKE_EXECUTED, smoke.status=BLOCKED` — never a false PASS.

### 3.3 Operation expansion boundary (PR6b-1 vs PR6b-2)
Supported in PR6b-1: `ma`, `pct_change` (IndicatorNode), `cross` (SignalNode), `rank`/`top_n`/`bottom_n` (RankingNode).
Deferred to PR6b-2: Factor ops (`zscore`/`winsorize`/`neutralize`/`combine`) + `threshold`/`compare` Signal. These raise `ContractValidationError` with a "PR6b-2" message (not silently OK). `manual_list` multi-stock is in PR6b-1; `index_constituents` is PR6b-2.

## 4. Honest GAPs (not silently passed)

| Dimension | Status | Resolution |
|---|---|---|
| dim10 止损止盈 | EMPTY | No stop_loss/take_profit in Spec/IR/templates. PR6b-2 RiskNode extension. |
| dim13 成本滑点 | GAP | Spec.costs has 5 fields but templates emit no `set_commission`/`set_slippage`. Rendered strategies use engine-default costs. PR6b-2 cost passthrough. |
| dim14 API diff | DIFF_OK / DIFF_VIOLATION | Load-bearing: QS may emit `get_history_batch` (multi-stock), PTrade must not. AST-level check (not substring — PTrade comments mention the API name in the forbidden-list doc). |
| Factor ops | RAISE | zscore etc. raise with "PR6b-2" message. |
| tick smoke | BLOCKED | Invariant 4: tick never READY in v1. PR9 scope. |

## 5. Document-debt discipline (CP1)
- `frequency-and-engine-profile.md`: minute-bar-v1 was marked `BLOCKED(DATA_MISSING)`; real DB probe shows `stock_minutes` 18.78M / `etf_minutes` 46.94M rows → corrected to `READY`. tick stays `PLANNED` (invariant 4, PR9).
- `implementation-status.md`: stale row-count placeholders →实测 values + smoke PASS + end-labeled VERIFIED; historical notes marked superseded.

## 6. Files touched (CP1-11 full set)
New: `orchestrator.py`, `validators/run_smoke_backtest.py`, `validators/check_hard_filters.py`, `validators/validate_ptrade_portability.py`, `validators/compare_strategy_variants.py`, `scripts/install_skill.py`, 4 test files, this report.
Modified (CP1-5): `build_strategy_ir.py`, `render.py`, `contracts.py`, `scripts/validate_strategy_spec.py`, 4 templates, 2 docs.

## 7. Review follow-up (b2de47a → follow-up commit)

Code review of `b2de47a` found 5 issues; all fixed in a follow-up commit (the
data-ops files were committed separately as `4e17c3a`).

| # | Issue | Fix |
|---|---|---|
| 1 | Minute templates raised `NotImplementedError` on manual_list (only 2 daily templates done; handoff required all 4). | Implemented `manual_list` branch in both minute templates (`_trade_on_minute` multi-stock rotation). Also fixed a latent bug in all daily+minute templates: pct_change was assigning to an undefined `{{ ind.id }}[code]` instead of `scores[code]`, leaving the ranking source empty. Now all 4 templates populate `scores[code]` (verified by compile + AST + parametrized tests). |
| 2 | Stage machine contradiction: docstring claimed static BLOCK stays `STATIC_VALIDATED`, but code left it at `SPEC_ONLY`. | Fixed: `stage` advances to `STATIC_VALIDATED` once static validators *run* (the step executed, pass-or-block); only schema failure stays `SPEC_ONLY` (IR never built). Added `test_static_block_stage_static_validated_smoke_skipped` (asserts smoke inspector never called via spy) + `test_schema_fail_stage_spec_only`. |
| 3 | `quality_audit.py` was also uncommitted alongside `daemon.py` (same xtquant gate fix). | Committed both together as independent data-ops commit `4e17c3a` (not mixed into PR6b-1). |
| 4 | Report file counts stated "8 new + 12 modified"; actual is 12 new + 8 modified. | Corrected to 12 + 8. |
| 5 | `install_skill` returned success (True) when `quick_validate.py` was not found. | Fixed: missing validator now FAILS + rolls back (unverified install not allowed); `--skip-validate` remains the explicit opt-out. Added `test_quick_validate_missing_fails_and_rolls_back`. |

Test count after follow-up: PR6a+PR6b-1 = **81 passed** (was 71; +10 tests covering minute+manual_list, scores population, static-BLOCK stage, qv-missing rollback).

## 8. Next (PR6b-2)
9 golden cases (incl. real minute smoke case 6), `index_constituents`, Factor ops, cost passthrough, RiskNode stop_loss. PR6b-1's orchestrator + run_card + validators are the infrastructure these consume — no rework expected.
