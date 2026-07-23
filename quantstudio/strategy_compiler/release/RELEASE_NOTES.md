# QuantStudio Strategy Compiler — Release Notes

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

### Known limitations
- 5 repository tests remain non-hermetic (calendar + fidelity golden-baseline
  fixtures) — environment-only failures, not pipeline defects.
- `np.log` RuntimeWarning in one CP3 NaN-exclusion test (non-blocking).

### Reproducibility
- Deterministic builds: fixed render-timestamp sentinel, canonical JSON,
  byte-identical packages across builds and processes (PYTHONHASHSEED-stable).
