# Strategy Compiler Implementation Status

> Master plan: `QuantStudio Strategy Compiler frozen master plan v1.0`  
> Current stage: PR1  
> Stage status: **PASS (technical acceptance complete; waiting for user approval before PR2)**

## Stage overview

| Stage | Status | Summary |
|---|---|---|
| PR0 Contracts and baseline | PASS | Contracts, schemas, examples, and contract tests complete |
| PR1 Security code rules | PASS / WAITING_CONFIRMATION | Runtime authority, official BSE mapping, call-site migration, and regression tests complete |
| PR2 `next_open` semantics | NOT_STARTED | Existing behavior remains `0.1.0-legacy` |
| PR3 Multi-frequency Provider | NOT_STARTED | Minute execution remains BLOCKED |
| PR4 Minute event engine | NOT_STARTED | Minute execution remains BLOCKED |
| PR5 Skill skeleton | NOT_STARTED | No incomplete production Skill created |
| PR6 IR/renderers/validators | NOT_STARTED | Planned |
| PR7 Fidelity closure | NOT_STARTED | Planned |

## PR0 acceptance

PR0 was confirmed by the user before PR1 started.

Baseline and completion results:

```text
Initial baseline: 201 passed in 14.84s
PR0 contract tests: 19 passed
PR0 final full suite: 220 passed in 13.40s
```

PR0 report: `docs/strategy-compiler/pr0-implementation-report.md`.

## Current contract versions

| Field | Version | Meaning |
|---|---|---|
| `strategy_spec_version` | `1.0.0` | PR0 contract |
| `engine_semantics_version` | `0.1.0-legacy` | `next_open` not yet fixed |
| `provider_contract_version` | `0.1.0-daily` | frequency not yet propagated |
| `security_code_rules_version` | `1.0.0` | PR1 authoritative runtime module |
| `ptrade_profile_version` | `1.0.0-default` | default public API profile |
| `renderer_version` | `0.0.0-planned` | planned for PR6 |
| `skill_version` | `0.0.0-planned` | planned for PR5 |

## PR1 completed work

- [x] Added `quantstudio/backtest/libs/security_code_rules.py` as the code-level authority.
- [x] Exported classification and QMT/PTrade/bare normalization functions.
- [x] Delegated STAR/ChiNext/BSE/ST predicates from `shared_ashare_rules.py`.
- [x] Removed independent numeric-prefix market decisions from `ptrade_api.py`.
- [x] Migrated `backtest_engine.py`, DuckDB data access, and reference-provider routing.
- [x] Supported `.SH/.SS/.XSHG/.SZ/.XSHE/.BJ/.XBJ/.XBSE/bare` aliases.
- [x] Classified current BSE equities by `920xxx`.
- [x] Classified historical BSE equities only through the official 248-row exact mapping.
- [x] Prevented blanket BSE classification of `400xxx`, `420xxx`, and unmapped `8xxxxx`.
- [x] Kept ETF/index/convertible-bond classification ahead of stock-board classification.
- [x] Generated documentation from runtime metadata; runtime code never reads documentation.
- [x] Kept `next_open`, order execution, costs, and event lifecycle unchanged.

## BSE verification record

- Official page: `https://www.bse.cn/service/code_mapping.html`.
- Durable snapshot: `docs/strategy-compiler/sources/bse-official-code-mapping-20260720.json`.
- Packaged exact mapping: `quantstudio/backtest/libs/bse_legacy_code_mapping.json`.
- Official mapping contains 248 rows and legacy prefixes `430`, `830-839`, and `870-873`.
- Prefix presence does not classify the entire prefix. Membership is exact.
- Project database sampling found 329 current `920xxx` codes and three old, unmapped `832/833` NEEQ samples ending in 2021; those samples are not treated as BSE equities.

## PR1 verification

```text
Pre-PR1 full baseline: 220 passed in 12.28s
PR1 focused tests:     28 passed in 0.46s
Core regression:       54 passed in 4.52s
Full test suite:       249 passed in 12.67s
Daily smoke backtest:  PASS (20 trading days, 2 trades)
```

Final logs: `output/strategy-compiler-pr1/`.

## Compatibility and limitations

1. Existing Shanghai/Shenzhen `.SS/.SZ` public output and exact portfolio membership remain unchanged.
2. Alias-aware access remains available through market-data containers and explicit position APIs.
3. Beijing inputs accept `.BJ/.XBJ/.XBSE`; public output is `.BJ`.
4. Historical BSE codes are classified but not rewritten to `920`, preserving historical database keys.
5. `next_open` remains legacy behavior until PR2.
6. Minute and Tick capability status is unchanged.
7. The workspace is not a Git repository; audit evidence is the file list, tests, smoke output, and official mapping snapshot.

## PR2 gate

Do not modify `next_open` until the user confirms PR1. PR2 must implement a real pending-order queue and must not mix in new security-code changes.
