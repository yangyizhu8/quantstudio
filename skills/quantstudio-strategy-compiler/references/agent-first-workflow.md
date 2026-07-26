# Agent-first Strategy Engineering Workflow

## Purpose

The caller is an intelligent agent. Preserve its ability to design indicators, selection logic, state machines, and risk rules. The Skill supplies guardrails and reusable project interfaces rather than strategy-specific templates.

## Stable responsibilities of the Skill

- Translate the idea into a reviewable design contract.
- Force explicit confirmation of timing, execution approximations, position overlap, costs, and hard filters.
- Select lifecycle hooks and public API groups from `component-catalog.json`.
- R0 first records an explicit target mode: dual (QuantStudio + PTrade) or QuantStudio-only.
- Scaffold a target-aware Python strategy file with extension points only.
- Require all market, fundamental, reference, calendar, portfolio, and order access to use injected project APIs.
- Validate syntax, isolation, portability, schedules, no-lookahead rules, lifecycle coverage, and design/code consistency.
- Run local backtests for every mode. Dual mode publishes the same portable source to both targets; QuantStudio-only mode publishes one local source and records PTrade/consistency as NOT_APPLICABLE.

## Responsibilities left to the calling agent

- Implement dynamic universes and filters using the public APIs.
- Choose or implement mathematical indicators with NumPy/pandas/MyTT.
- Implement entry, exit, ranking, state tracking, cash allocation, and fail-soft behavior.
- Add helper functions appropriate to the strategy.
- Repair strategy-specific backtest failures without weakening the hard gates.

## Mandatory interaction rounds

### R0 - Clarify strategy semantics

Present a structured summary of universe, data cutoff, entry, exit, holding period, overlap, capital allocation, risk, costs, and execution time. Identify contradictions. Wait for answers to material contradictions.

### R1 - Inspect environment and platform boundaries

Inspect relevant tables, fields, coverage, engine profile, and PTrade API availability. This is an inventory, not a declaration that the entire strategy is compilable.

For dual targets, every planned API must have a verified entry in `ptrade-api-signatures.json`. Missing signatures are `MISSING_REUSABLE_API` and keep R1 blocked; they are never customer-waivable approximations.

### R2 - Draft design contract

Write `agent_strategy_design.json` using `schemas/agent_strategy_design.schema.json`. Keep rules in clear natural language. Set `market_data_contract.signal_price_adjustment` to `pre` and `execution_price_basis` to `pre_adjusted_price`. Signal history, engine matching, fills, cash, valuation, `data[code].price`, and BarData OHLC use the same front-adjusted snapshot contract. Select lifecycle and API components, but do not encode strategy logic as a renderer pattern.

### R2.5 - Customer confirmation gate

Present exact strategy semantics, all approximations and platform differences, selected lifecycle callbacks and public APIs, unresolved data gaps, and expected holding/overlap/cash behavior.

Do not create strategy code until `strategy_semantics`, `execution_approximations`, and `component_plan` are all true and `open_questions` is empty.

### R3 - Scaffold and agent implementation

Run `scripts/create_agent_workspace.py`. The scaffold may wire `run_daily` callbacks, but must contain no built-in indicator, stock-pool, ranking, or strategy-pattern implementation. The calling agent edits `strategy.py` directly.

### R4 - Static validation and repair loop

Run `scripts/validate_agent_strategy.py`. Repair BLOCK findings in strategy code or the design contract. Never replace unsupported rules silently. Warnings must be reviewed and either fixed or documented.

### R5 - Local backtest and semantic review

Run the declared daily/minute profile. Review orders, holdings, trigger times, empty-universe behavior, insufficient history, suspended/limit states, and position/cash invariants. Strategy-specific failures are fixed by the calling agent.

### R6 - Target-aware publish

Run `scripts/publish_agent_strategy.py`. It validates then publishes the exact same canonical strategy source to:
- `quantstudio/backtest/strategies/<strategy_id>_quantstudio.py`
- `ptrade/<strategy_id>_ptrade.py`

Platform headers may differ; executable strategy content must have the same SHA-256 digest.

## Prohibited implementation pattern

Never add strategy names or strategy shapes to Skill, Compiler, renderer, or templates. Do not introduce `if strategy_pattern == "some_strategy"` branches.

A new strategy normally adds only its design/workspace/output files. A project source change is justified only for a genuinely reusable API/lifecycle capability and must be tested independently of the requesting strategy.

## ETF universe routing

- `get_etf_list()` retains the PTrade-named contract and is blocked in backtest source.
- Dual ETF mode uses a customer-confirmed static whitelist and forbids local-only APIs.
- QuantStudio-only ETF mode may call `get_etf_list_local()` and `get_history_batch()`; the local API routes through ReferenceDataProvider and the DuckDB adapter, never direct strategy storage access.

## User-PyQt candidate branch

R0 independently records `validation_execution.mode=agent_managed|user_pyqt`. In user-PyQt mode, R4 PASS creates only `<strategy_id>__candidate_quantstudio.py` with canonical/candidate hashes and `formal_publish_allowed=false`. The user chooses dates in PyQt and submits complete evidence. Hash drift returns R4; strategy failures return R3; framework/data/API failures return R1; profile/validator failures return R4. R6 regenerates formal artifacts, removes the candidate, and records promotion evidence.
