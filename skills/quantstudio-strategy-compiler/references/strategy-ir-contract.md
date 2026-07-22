# Strategy IR Contract

> Derived from `docs/strategy-compiler/strategy-ir-contract.md` @ 2026-07-22 (PR6a)
> 权威源：docs/strategy-compiler/strategy-ir-contract.md
> 本文件为 Skill 派生快照，契约变更时必须同步（见 SKILL.md 同步纪律）
> 上游依据：master-implementation-plan-v1.0.md §6 R3 + §7.33 + §9

## Status

**PR6a delivered.** The IR contract is now authoritative in `docs/strategy-compiler/strategy-ir-contract.md`. Implementation: `build_strategy_ir.py` (Spec→IR), `ir_nodes.py` (11 dataclasses), `render.py` (IR→dual .py), `validate_strategy_ir` (12 invariants), `scan_lookahead` (10 high-risk items). PR6b adds FactorNode/RankingNode full impl + 5 more validators + 9 golden cases.

## Purpose

Strategy IR is the platform-neutral intermediate between a confirmed `strategy_spec.json` and dual-rendered QuantStudio / PTrade code. Both renderers consume the same IR — dual-version consistency is enforced at the IR layer (architecture.md invariant 1), not by hand-syncing two `.py` files.

## IR nodes (11 types, PR6a fully defines each)

Per master plan §7.33 + the authoritative contract doc:

| Node | Role | PR6a status |
|---|---|---|
| `UniverseNode` | stock pool construction | impl (single_stock/index/list) |
| `HardFilterNode` | exclude ST/suspended/delisted/invalid/zero-vol/limit/T+1/lot | impl (13-filter full set + execution-stage subset) |
| `DataLoadNode` | load market/fundamental/reference data by freq + PIT | impl (previous_date/announcement_date anchors) |
| `IndicatorNode` | raw indicator computation | impl (`ma` only; others PR6b) |
| `FactorNode` | alpha factor combination | placeholder (PR6b) |
| `SignalNode` | buy/sell signal generation | impl (`cross` only; others PR6b) |
| `RankingNode` | score/rank universe | placeholder (PR6b) |
| `PortfolioNode` | position sizing + rebalancing | impl (single_position/equal_weight) |
| `RiskNode` | risk checks | impl (position_limits; stop_loss PR6b) |
| `ExecutionNode` | order placement | impl (4 order APIs, match_price passthrough) |
| `DiagnosticNode` | logging/metrics/run-card evidence | impl |

## Each node carries 9 fields (7 attribute categories + 2 identifiers)

Per master plan §7.33 (7 categories: input/output/parameters/required_capabilities/timing/platform_mapping/validation_rules) + 2 identifier fields (node_id for reference, node_type for schema enum). See contract doc §2.1 for the expansion rationale.

## Key invariants (12 cross-node, validate_strategy_ir)

- IR-ORDER-1/2/3: UniverseNode first, DiagnosticNode last, exactly 1 each
- IR-ORDER-4/5/6: pipeline predecessor ordering (DataLoad before Indicator, Signal before Ranking/Portfolio, etc.)
- IR-DEP-1/2: input references上游 output names (not node_id); DAG (no cycles)
- IR-CAP-1: required_capabilities reference valid capability IDs
- IR-TIMING-1: DataLoadNode timing/pit_anchor consistency
- IR-EXEC-1: match_price_mode passthrough consistency
- IR-EXEC-2: stock asset_class requires execution-stage HardFilterNode

## 10 lookahead high-risk items (scan_lookahead, all BLOCK)

See contract doc §6.1 for the full mapping of master plan §9 items to validation rule IDs. #1-#9 have red-light tests; #10 (1m→5m aggregation) deferred to PR3.5 (IndicatorNode has no frequency field yet).

## PR6a boundary

PR6a builds IR + renders + statically validates. It does NOT run smoke backtest (`run_smoke_backtest` is PR6b) — the e2e test stops at static validation. Supported operations: `ma` (IndicatorNode), `cross` (SignalNode). Others raise with step id + "PR6b 扩展".
