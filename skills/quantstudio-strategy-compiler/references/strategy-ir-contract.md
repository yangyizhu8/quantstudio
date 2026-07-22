# Strategy IR Contract (placeholder, PR6)

> New file aggregated from master-implementation-plan-v1.0.md §7.33 @ 2026-07-22
> 权威源：docs/strategy-compiler/master-implementation-plan-v1.0.md §7.33
> 本文件为 Skill 派生快照，PR6 实现 IR 时必须同步细化

## Status

**PR6 scope.** This reference is a placeholder skeleton. The actual IR node definitions, validation rules, and platform mappings are defined in PR6 (`build_strategy_ir.py` + `render_quantstudio.py` + `render_ptrade.py`). PR5 stops at Spec (R2.5); no IR is built in PR5.

## Purpose

The Strategy IR is the platform-neutral intermediate representation between a confirmed `strategy_spec.json` and the dual-rendered QuantStudio / PTrade code. Both renderers must consume the same IR (built from the same Spec) — dual-version consistency is enforced at the IR layer, not by hand-syncing two `.py` files.

## IR nodes (planned, PR6 will define each fully)

Per master plan §7.33, the IR consists of 11 node types:

| Node | Role |
|---|---|
| `UniverseNode` | stock pool construction |
| `HardFilterNode` | exclude ST / suspended / delisted / invalid price / zero volume / limit-up-down / T+1 / round-lot |
| `DataLoadNode` | load market/fundamental/reference data by freq + PIT |
| `IndicatorNode` | raw indicator computation |
| `FactorNode` | alpha factor combination |
| `SignalNode` | buy/sell signal generation |
| `RankingNode` | score/rank universe |
| `PortfolioNode` | position sizing + rebalancing |
| `RiskNode` | risk checks |
| `ExecutionNode` | order placement (native/proxy, match price, order type) |
| `DiagnosticNode` | logging/metrics/run-card evidence |

## Each node must carry (PR6 contract)

- **input** — upstream node outputs
- **output** — what this node produces
- **parameters** — node config (from Spec)
- **required capabilities** — capability IDs this node needs READY
- **timing** — when in the lifecycle this runs (bar/tick/reference/fundamental event)
- **platform mapping** — how QuantStudio and PTrade each implement it
- **validation rules** — invariants the IR validator checks

## PR5 boundary

PR5 does NOT build IR. The Skill describes the IR contract (this reference) so that R2.5 Spec drafts are IR-aware (e.g. `signals.steps` map to future SignalNodes), but no `build_strategy_ir.py` exists until PR6. If a user requests code rendering in PR5, stop and report "rendering is PR6 scope".
