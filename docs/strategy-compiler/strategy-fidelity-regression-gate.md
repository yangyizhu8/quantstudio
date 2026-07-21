# Strategy Fidelity Regression Gate

## Purpose

Ordinary unit tests are insufficient to detect all PTrade control-flow drift. A suffix or container change can leave APIs syntactically valid while changing direct strategy expressions such as:

```python
code in context.portfolio.positions
```

Therefore every stage after PR1 must run approved real-strategy Fidelity gates in addition to pytest.

## Frozen portfolio semantics

- `context.portfolio.positions` is a normal Python `dict`.
- Public position keys use PTrade strategy/CSV suffixes: `.SS`, `.SZ`, and `.BJ`.
- Native `in`, direct indexing, iteration, and `.keys()` use exact key semantics.
- `.XSHE` and `.SZ` are **not** the same key inside the native portfolio container.
- Cross-suffix lookup belongs to explicit APIs and adapter containers only:
  - `get_position()`
  - `DataDict`
  - `CodeDict`
  - `get_history()`
  - `get_price()`
  - order API normalization

Changing the native portfolio container to an alias-aware mapping is a blocking regression even if all ordinary tests pass.

## Required gates

Configuration:

`config/strategy_fidelity_gates.json`

Runner:

```powershell
python scripts/run_strategy_fidelity_gates.py
```

The command reruns:

1. ETF momentum against the real ETF PTrade export;
2. small-cap against the real small-cap PTrade export.

It validates both Fidelity L1-L4 and local golden results.

### ETF momentum hard gate

Required:

- verdict `PASS`;
- L1 exactly 100%;
- L3 exactly 100%;
- final asset `87,752.56 ± 1`;
- exactly 3 fills;
- exact fill sequence:
  - 2026-01-05 buy 515880;
  - 2026-01-07 sell 515880;
  - 2026-01-07 buy 159870;
- last bought security 159870;
- Fidelity deviations within the manifest limits.

The known bad 31-trade / 49,064.37 result must fail this gate.

### Small-cap non-regression gate

The currently approved cross-source baseline is `CLOSE`, not `PASS`. The gate accepts `CLOSE` only when each frozen submetric remains within its tighter approved envelope:

- L1 at least 72%;
- NAV deviation at most 0.30%;
- drawdown deviation at most 1.80%;
- Sharpe deviation at most 0.03;
- L3 overlap at least 95%;
- L4 deviation at most 0.30%;
- final asset `118,551.21 ± 10`;
- exactly 57 fills.

A generic `CLOSE` verdict alone is not sufficient.

## When this gate is mandatory

Run it after any modification involving:

- security suffixes or classification;
- `Portfolio`, `Position`, or position dictionaries;
- `get_position`, `get_positions`, `DataDict`, or `CodeDict`;
- order submission, rejection, T+1, costs, or matching;
- `next_open` and pending orders;
- lifecycle callbacks or context refresh;
- Provider routing or price/adjustment fields;
- GUI/CLI parameter construction;
- metrics/export/Fidelity comparison logic.

PR2 and every later runtime PR cannot be marked PASS without this gate.

## Fixed validation order

```powershell
# 1. New/changed unit tests
python -m pytest -q <new_tests>

# 2. PTrade semantic and code-rule tests
python -m pytest -q `
  tests/test_strategy_alignment_regressions.py `
  tests/test_strategy_fidelity_gates.py `
  tests/test_security_code_rules.py `
  tests/test_security_code_aliases.py `
  tests/test_bse_filtering.py

# 3. Core engine regression
python -m pytest -q `
  tests/test_strategy_runner.py `
  tests/test_order_rejection.py `
  tests/test_filter_stock_by_status.py `
  tests/test_match_price_mode.py `
  tests/test_etf_ptrade_compat.py `
  tests/test_providers.py

# 4. Full pytest suite
python -m pytest -q

# 5. Real PTrade Fidelity gates
python scripts/run_strategy_fidelity_gates.py
```

Failure in any required step blocks the stage. Do not update golden values merely to make a changed implementation pass. Golden changes require a separately documented PTrade re-baseline and explicit user approval.
