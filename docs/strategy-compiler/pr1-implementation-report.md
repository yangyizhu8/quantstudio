# PR1 Implementation Report

Stage: PR1  
Status: PASS (waiting for user confirmation)

## Completed

1. Added a single runtime authority for security classification and suffix normalization.
2. Migrated PTrade API, backtest engine, DuckDB data access, reference-provider routing, and A-share rules to that authority.
3. Removed independent numeric `startswith()` market classification from `ptrade_api.py`.
4. Added Shanghai, Shenzhen, and Beijing QMT/PTrade suffix interoperability.
5. Built an exact 248-row historical BSE mapping from the official BSE old/new-code table.
6. Separated BSE equities from delisted-board, old-third-board, and arbitrary unmapped 4xx/8xx codes.
7. Prevented ETF, index, and convertible-bond ranges from being classified as stock boards.
8. Added generated documentation whose source is the runtime module; the engine never depends on documentation.
9. Did not change `next_open`, matching, costs, orders, or lifecycle semantics.

## BSE decision

- Current BSE equity codes use the `920xxx` range.
- Historical compatibility is exact: only old codes present in the official mapping are BSE equities.
- The official mapping contains old prefixes `430`, `830-839`, and `870-873`, but no entire prefix is classified as BSE.
- Boundary samples such as `400001`, `420001`, `832317`, `833874`, and `839999` are not given BSE 30% price-limit semantics unless present in the exact mapping.
- Durable source snapshot: `docs/strategy-compiler/sources/bse-official-code-mapping-20260720.json`.

## Changed files

- `pyproject.toml`
- `quantstudio/backtest/libs/security_code_rules.py`
- `quantstudio/backtest/libs/bse_legacy_code_mapping.json`
- `quantstudio/backtest/libs/shared_ashare_rules.py`
- `quantstudio/backtest/ptrade_api.py`
- `quantstudio/backtest/backtest_engine.py`
- `quantstudio/backtest/providers/duckdb_data_access.py`
- `quantstudio/backtest/providers/duckdb_provider.py`
- `scripts/generate_security_code_rules_doc.py`
- `docs/strategy-compiler/security-code-rules.md`
- `docs/strategy-compiler/sources/bse-official-code-mapping-20260720.json`
- `docs/strategy-compiler/architecture.md`
- `docs/strategy-compiler/ashare-filter-contract.md`
- `docs/strategy-compiler/implementation-status.md`
- `quantstudio/strategy_compiler/examples/strategy_spec.example.json`
- `quantstudio/strategy_compiler/examples/run_card.example.json`
- `tests/test_security_code_rules.py`
- `tests/test_security_code_aliases.py`
- `tests/test_bse_filtering.py`

## New tests

```text
tests/test_security_code_rules.py
tests/test_security_code_aliases.py
tests/test_bse_filtering.py
```

Coverage includes classification, suffix conversion, delegation, current and historical BSE boundaries, delisted-board boundaries, mapping-snapshot equality, generated-document equality, position access, and Beijing market routing.

## Test results

```text
Pre-PR1 full baseline: 220 passed in 12.28s
PR1 focused tests:     28 passed in 0.46s
Core regression:       54 passed in 4.52s
Full test suite:       249 passed in 12.67s
Daily smoke backtest:  PASS
```

Smoke strategy: built-in double moving-average strategy  
Range: 2026-01-05 through 2026-01-30  
Trading days: 20  
Trades: 2

The first PowerShell smoke attempt failed because the host GBK console could not print Unicode check/cross glyphs. Re-running the same command with `PYTHONIOENCODING=utf-8` passed. This is a CLI output-encoding issue, not a PR1 semantic regression.

## Compatibility impact

- Shanghai/Shenzhen public `.SS/.SZ` output is unchanged.
- `.XSHG/.XSHE` aliases remain usable through existing alias-aware APIs.
- Beijing aliases are accepted and normalize to `.BJ`.
- The intentional correction is that not every 4/8-prefixed code is considered BSE.
- `next_open` and all execution semantics remain unchanged.

## Known limitations

- Historical BSE security numbers are not automatically rewritten to new `920` numbers.
- `next_open` remains legacy until PR2.
- Minute and Tick capabilities are unchanged.
- No Git commit/diff is available because the workspace is not a Git repository.

## Next-stage prerequisite

User confirmation is required before PR2. PR2 is limited to true delayed `next_open` order semantics.
