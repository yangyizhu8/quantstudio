# QuantStudio Strategy Compiler ? Release Notes

## 0.5.0-user-pyqt-candidate-flow (2026-07-25)

- Added independent R0 backtest-execution ownership: agent-managed or user-PyQt.
- Added hash-bound `__candidate_quantstudio.py` generation after R4 PASS.
- Added structured user backtest evidence validation with ETF lower-bound, database, profile, completion and runtime-check gates.
- Added failure routing to R1/R3/R4 and source-drift invalidation.
- R6 now promotes user-tested candidates by regenerating formal targets and removing the candidate.


## 0.4.0-target-aware (2026-07-25)

- Added QuantStudio-only PIT ETF universe API `get_etf_list_local(query_date=None, etf_type="equity", active_only=True)` through injected API -> ReferenceDataProvider -> DuckDB data access.
- Added `etf_basic` metadata synchronization via `scripts/sync_etf_basic.py`; ETF classification/listing metadata remains separate from strategy indicators.
- Kept `get_etf_list()` as the PTrade-named contract and blocked it in backtest validation.
- Added R0 target selection, schema-conditional portability, static-whitelist dual ETF mode, dynamic-local ETF mode, target-aware validation, and target-aware publication.
- QuantStudio-only publication creates no PTrade placeholder and records PTrade validation / dual consistency as `NOT_APPLICABLE`.


## 0.3.2-mvp (Runtime Compatibility and Stable Publish Corrective)

- PTrade daily templates accept pandas and NumPy structured-array `get_history` results.
- Unsupported or history-empty securities are skipped instead of crashing on `BarDict`.
- Daily ranking is computed before trading from previous-close history; trading is scheduled with `run_daily`; `handle_data` does not repeatedly recalculate or access unsupported symbols.
- Multi-stock portfolios use `order_target_value` target sizing for daily equal-weight restoration.
- Shanghai/Broker suffix aliases are normalized and QuantStudio batch-history keys use bare-code lookup.
- Skill delivery publishes QuantStudio strategies to `quantstudio/backtest/strategies/` and PTrade strategies to project-root `ptrade/`.
- Delivery prefers the live project package over stale site-packages and can locate the capability inspector from the project Skill.
- Real data digest and cross-platform Fidelity remain deferred.

## 0.3.0-mvp (G4 Release Closure)

End-to-end hermetic strategy compile pipeline: **Spec → IR → dual Renderer
(QuantStudio + Strict-PTrade) → strategy package**, driven by a CLI, with Skill
install/validation and this release metadata.

### What's included
- **G1-I basket engine** (`bcdc85d`, merged): next_open `callback_basket` rebalance
  (0.4.0-next_open_basket) — sell-then-buy rotation with T+1 drain state machine.
- **G2 CP3 Reference closure** (`53d90f5`, merged): independent hand-written Oracle
  producing frozen signal/order/NAV reference artifacts + source/data digests.
  Hermetic/Synthetic Reference Partial Closure.
- **G3 Package Closure** (`9a99b18`, merged): dual Renderer → deterministic strategy
  package with manifest (version, entry points, artifact digests), import boundary,
  and optional G2 reference linkage (portable logical IDs).
- **G4 Release** (this): `qs-compile package` CLI, Skill install/validation flow,
  0.3.0-mvp version alignment, release metadata + docs.

### CLI usage
```
python -m quantstudio.strategy_compiler.cli package <spec.json> --out <dir> \
    [--g2-frozen-dir <frozen_dir>] [--package-version <semver>]
```
- Builds a strategy package dir under `--out` containing manifest.json, the frozen
  Spec/IR, both rendered strategies, `__init__.py`, README.md.
- Retains G3 manifest/digest verification (every artifact_digest matches the file;
  manifest never self-references its own digest).
- Surfaces Golden Protection (exit 3) and invalid-spec/missing-file errors (exit 2)
  honestly — never silent success on failure.
- `--g2-frozen-dir` links G2 frozen closure; records `data_digest_status=blocked`
  honestly (never faked as frozen).

### Skill install/validation
```
python skills/quantstudio-strategy-compiler/scripts/install_skill.py --dest <skills_root>
```
Copies the Skill, runs quick_validate on the installed copy, rolls back on failure.

### Honest boundaries (IMPORTANT)
- **data digest: blocked.** `input_data_digest=null`, `data_digest_status=blocked`.
  Real market-data digest is **deferred**, not faked. This release does NOT validate
  real-data Fidelity or real-market Reference.
- **No real market data / live QMT / resident daemon** in this release. All tests
  are hermetic (synthetic scenarios / frozen artifacts).
- **Real Fidelity/Reference verification: deferred** to a later release.

### Delivery flow integration (0.3.0-mvp)
- The Skill **auto-orchestrates** orchestrator validation + qs-compile package
  generation. Normal users do NOT need to manually run qs-compile.
- `qs-compile` remains the formal CLI entry for advanced users, scripts, and CI.
- CLI direct entry: `qs-compile package <spec> --out <dir>` (preferred).
  `python -m quantstudio.strategy_compiler.cli` as dev/diagnostic fallback.
- `deliver_strategy()` API chains both steps into a unified output dir:
  `validation/` (orchestrator artifacts) + `package/` (strategy package) +
  `DELIVERY_REPORT.md` (unified summary).

### Known limitations
- 5 repository tests remain non-hermetic (calendar + fidelity golden-baseline
  fixtures) — environment-only failures, not pipeline defects.
- `np.log` RuntimeWarning in one CP3 NaN-exclusion test (non-blocking).


### Reproducibility
- Deterministic builds: fixed render-timestamp sentinel, canonical JSON,
  byte-identical packages across builds and processes (PYTHONHASHSEED-stable).