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

Write `agent_strategy_design.json` using `schemas/agent_strategy_design.schema.json`. Keep rules in clear natural language. Set `market_data_contract.signal_price_adjustment` to `pre` and `execution_price_basis` to `raw_trade_price`. Signal history OHLC uses the front-adjusted snapshot (literal `fq='pre'`); engine matching, fills, cash, valuation, `data[code].price`, and BarData OHLC use the raw snapshot (PTrade match-price audit 2026-08-14: daily fill = raw close 5/5, minute fill = bar raw close 6/6). Select lifecycle and API components, but do not encode strategy logic as a renderer pattern.

Design 2.2 additionally requires machine-checkable contracts: `portfolio_contract` (sizing mode, target holdings, exposure/cash/weight bounds), `rebalance_funding_contract` (checked against `references/execution-funding-matrix.md`), `history_coverage_contract` (lookback coverage, not window truncation), `r5_deployment_invariants` (deployment thresholds R5 verifies from real artifacts), and verbatim `confirmation_evidence`. Self-contradictory capital math (20 x 5% + 15% cash buffer) BLOCKs at design time, never reaches R3.

**RISK_WARNINGS（R2 组件计划强制段）** — 凡含日频再平衡或 ETF 轮动的 R2 包，必须产出以下两段预警：

1. **碎单风险（fragmented-order friction）**：日频再平衡 × 小资金（默认 10 万）× 最低佣金 5 元，会放大换手摩擦。参考实跑量级 ≈ 445 笔 / ≈ 2200 元/年；应在组件计划建议"再平衡阈值（权重偏离 > 2% 才调）"或持仓缓冲带，避免无谓碎单。
2. **撮合模式语义（fill semantics）**：本策略语义 = 盘后信号次日开盘成交 → GUI 撮合必须选 `next_open`。提示：GUI 默认撮合 = `close` / 佣金 = 万 3.5 / 滑点 = 0，必须手动改为 `next_open` / `0.00025` / `0.001`。R4 Handoff 必须同步复述撮合模式与费率配置，否则视为 R4 未闭环。

### R2.5 - Customer confirmation gate

Present exact strategy semantics, all approximations and platform differences, selected lifecycle callbacks and public APIs, unresolved data gaps, and expected holding/overlap/cash behavior.

Do not create strategy code until `strategy_semantics`, `execution_approximations`, and `component_plan` are all true and `open_questions` is empty. Design 2.2 also requires `confirmation_evidence` entries with verbatim customer text and timezone-aware timestamps for the generation target, strategy semantics, portfolio contract, rebalance funding contract, and R5 deployment invariants.

### R3 - Scaffold and agent implementation

Run `scripts/create_agent_workspace.py`. The scaffold may wire `run_daily` callbacks, but must contain no built-in indicator, stock-pool, ranking, or strategy-pattern implementation. The calling agent edits `strategy.py` directly.

### R4 - Static validation and repair loop

Run `scripts/validate_agent_strategy.py`. Repair BLOCK findings in strategy code or the design contract. Never replace unsupported rules silently. Warnings must be reviewed and either fixed or documented.

### R5 - Local backtest and semantic review

Run the declared daily/minute profile. Review orders, holdings, trigger times, empty-universe behavior, insufficient history, suspended/limit states, and position/cash invariants. Strategy-specific failures are fixed by the calling agent.

R5 PASS is artifact-bound (evidence 2.1): the reviewer re-parses hash-verified `config.csv`/`daily_stats.csv`/`trades.csv`/run log and enforces the `portfolio_contract` capital check and `r5_deployment_invariants`. "回测跑完、无异常" is not PASS — the strategy must have actually deployed the designed capital (positions, exposure, cash ratio, zero excess insufficient-cash rejections). Strategies with `r5_deployment_invariants` must emit `QS_REBALANCE_AUDIT`/`QS_PORTFOLIO_AUDIT` key=value log lines.

**R5 复现性门禁（G3.5，硬前提）**：同一策略产物必须在两个独立进程各跑一遍（agent-managed：两次 CLI 运行；user-PyQt：两次 GUI 运行；同一窗口/资金/配置，`PYTHONHASHSEED` 固定或记录随机种子）。证据必须同时绑定两次运行的 `config.csv`/`daily_stats.csv`/`trades.csv`（`artifacts` + `reproducibility_artifacts`，证据 2.1），两侧三件套 SHA-256 **逐位一致**才 PASS；缺失第二跑 → `reproducibility_evidence_missing`（EVIDENCE_INCOMPLETE）；不一致 → `reproducibility_mismatch`（R5 FAIL 并归因：策略非确定性——dict/set 迭代顺序/随机数——或数据漂移或环境差异）。运行日志因含时间戳不参与跨运行比对。

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
