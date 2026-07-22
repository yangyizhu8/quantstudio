# Strategy IR 契约

> 权威源（本文件）。派生：`quantstudio/strategy_compiler/schemas/strategy_ir.schema.json`、`quantstudio/strategy_compiler/ir_nodes.py`、`skills/quantstudio-strategy-compiler/references/strategy-ir-contract.md`。
> 上游依据：`master-implementation-plan-v1.0.md` §6 R3（行 531-546）+ §7.33（行 1023-1049）+ §9（行 1240-1255）。
> 关联契约：`strategy-spec-contract.md`（Spec 是 IR 的输入）、`capability-model.md`（IR 节点引用 capability IDs）、`lifecycle-and-timing-contract.md`（IR timing 字段取值）、`ptrade-profile-contract.md`（platform_mapping 的 PTrade 侧）、`ashare-filter-contract.md`（HardFilterNode 的 13 项过滤）、`architecture.md`（7 条架构不变量）。
> 版本：`strategy_ir_contract_version = 1.0.0`（PR6a 引入）。

## 0. 目的与边界

Strategy IR 是 Spec 与双 Renderer 之间的平台无关中间表示。一个 Spec 经 `build_strategy_ir` 转为 IR，再由 QuantStudio / PTrade 两个 Renderer 各自消费同一份 IR 产出 `.py`。双版本一致性在 IR 层强制（master plan §2.1 行 49-50、architecture.md 不变量 1），**不得靠手改两份 .py 对账**（master plan §10.3 行 1299）。

本契约定义：
1. 11 个节点类型的完整字段 schema（§2）。
2. Spec `signals.steps` → IR 节点的映射规则（§3）。
3. IR 流水线顺序与跨节点不变量（§4）。
4. 每个节点的 `platform_mapping`（QuantStudio vs PTrade，§5）。
5. 每个节点的 `validation_rules` 不变量（§6）。

**本文件是权威源。** schema、dataclass、validator 全部从本文件派生；如本文件与派生物冲突，以本文件为准，并修正派生物。

---

## 1. IR 顶层结构

`StrategyIR` 是一个有序节点序列，外加从 Spec 透传的元数据：

```json
{
  "strategy_ir_version": "1.0",
  "strategy_id": "case1_dual_ma",
  "source_spec_sha256": "<sha256 of strategy_spec.json>",
  "contract_versions": { "...": "从 Spec 透传" },
  "engine_profile": { "...": "从 Spec 透传，决定 timing 取值域" },
  "time_model": { "...": "从 Spec 透传，决定 cutoff/execution_clock" },
  "nodes": [ <IRNode>, <IRNode>, ... ]
}
```

- `nodes` 必须是有序数组；顺序即执行顺序（§4 流水线约束）。
- 顶层字段除 `nodes` 外全部从 Spec 透传，IR 不引入 Spec 之外的业务参数源（architecture.md 不变量 1）。

---

## 2. IRNode 字段 schema

### 2.1 字段总表

每个 `IRNode` 有 **9 个字段 = 2 标识字段 + 7 属性类别**。

> **与 master plan §7.33 的关系**：§7.33（行 1041-1049）列出 7 个属性类别（输入 / 输出 / 参数 / 所需能力 / 时序 / 平台映射 / 验证规则）。本契约在此基础上加 2 个标识字段（`node_id` / `node_type`）以支持节点引用与类型分发——这是实现必需的展开，**非偏离**：`node_id` 使"输入"字段（上游引用）可落地，`node_type` 使 11 类节点可由 schema enum 约束。架构契约（architecture.md）已隐含此展开（R3 IR pipeline 要求节点可被引用）。

| # | 字段 | 类型 | 必填 | master plan §7.33 类别 | 说明 |
|---|---|---|---|---|---|
| 1 | `node_id` | string | 是 | （标识，展开） | 全局唯一，`^[a-z][a-z0-9_]*$`，下游节点 `input` 引用此值 |
| 2 | `node_type` | enum(11) | 是 | （标识，展开） | 见 §2.2 的 11 值 enum |
| 3 | `input` | array<string> | 是 | 输入 | 上游节点的 **`output` 名**列表（即上游节点产出的变量名，不是 node_id）；可为空数组（如 UniverseNode 无上游）。下游通过 output 名引用上游，保证端到端可追溯（output 名 == step id，§3）。 |
| 4 | `output` | string | 是 | 输出 | 本节点产出变量名，`^[a-z][a-z0-9_]*$`，下游 `input` 通过此名引用本节点。 |
| 5 | `parameters` | object | 是 | 参数 | 来自 Spec 的节点配置；字段集随 `node_type` 变（§2.2） |
| 6 | `required_capabilities` | array<string> | 是 | 所需能力 | capability IDs，对齐 `capability-model.md` 的 capability 清单 |
| 7 | `timing` | enum | 是 | 时序 | `pre_open` / `bar` / `tick` / `reference` / `fundamental` / `post_close`（取值域见 lifecycle-and-timing-contract.md） |
| 8 | `platform_mapping` | object | 是 | 平台映射 | `{"quantstudio": <QS 实现描述>, "ptrade-default": <PTrade 实现描述>}`（§5 逐节点给出） |
| 9 | `validation_rules` | array<string> | 是 | 验证规则 | 不变量 ID 列表，validators 引用（§6 逐节点给出） |

### 2.2 node_type enum（11 值，逐个与 §7.33 对齐）

```text
UniverseNode          HardFilterNode       DataLoadNode
IndicatorNode         FactorNode           SignalNode
RankingNode           PortfolioNode        RiskNode
ExecutionNode         DiagnosticNode
```

每个 `node_type` 的 `parameters` 子字段定义见 §2.3，`platform_mapping` 见 §5，`validation_rules` 见 §6。

### 2.3 各 node_type 的 parameters 子字段

以下仅列出 PR6a 需支持的参数键；未列出的参数键进 PR6b（如 RiskNode 的 stop_loss/take_profit 完整字段）。

#### UniverseNode
```json
{
  "kind": "index_constituents" | "single_stock" | "etf_list" | "manual_list",
  "index": "000300.SH",            // kind=index_constituents 时必填
  "code": "600570.SH",             // kind=single_stock 时必填
  "codes": ["510300.SH", ...]      // kind=manual_list/etf_list 时必填
}
```

> **证券代码后缀规范**：IR/Spec 内证券代码遵循 `security-code-rules.md` 的输入别名规则——接受 `.SH`/`.SS`/`.XSHG`/`.SZ`/`.XSHE`/`.BJ`/裸码等所有别名输入，**规范输出统一为 QMT 格式**（`.SH`/`.SZ`/`.BJ`，security-code-rules.md §1 "QMT output" 列）。Renderer 在生成 PTrade 产物时由差异层把 `.SH`→`.SS`（`.SZ`/`.BJ` 不变）。上方示例已统一为 QMT 格式。本规范承接 PR1 冻结的 security-code-rules，文档内不得出现两种后缀并存的不良示范。

#### HardFilterNode
```json
{
  "filters": ["exclude_st", "exclude_suspended", "exclude_delisted",
              "exclude_delisting_sorting", "exclude_star_market", "exclude_bse",
              "min_listing_trade_days", "exclude_invalid_price", "exclude_zero_volume",
              "block_limit_up_buy", "block_limit_down_sell", "enforce_t1", "round_lot"],
  "stage": "selection" | "execution"   // ashare-filter-contract.md §2：两阶段都要
}
```
13 个 filter key 与 `ashare-filter-contract.md` §1 逐项对齐；ETF/可转债/期货走各自 Profile 的 T+0/T+1（ashare-filter-contract.md §3），不盲继承股票 Profile。

#### DataLoadNode
```json
{
  "dataset": "stock_daily" | "stock_minutes" | "etf_daily" | "etf_minutes" | "stock_status" | "valuation" | "...",
  "frequency": "1d" | "1m" | "5m" | "15m" | "30m" | "60m",
  "fields": ["open", "high", "low", "close", "volume"],
  "pit_required": false,
  "pit_anchor": "previous_date" | "announcement_date"   // pit_required=true 时必填
}
```
`pit_anchor=previous_date` 用于行情/估值（`context.previous_date`，小市值策略ptrade.py 行 41 的 PIT 写法）；`pit_anchor=announcement_date` 用于财报（`ann_date`，no-lookahead-rules.md 高危 #6）。

> **frequency 与 dataset 语义**：`frequency` 描述 bar 数据集（stock_daily/etf_daily=1d，stock_minutes/etf_minutes=1m/5m/...）的采样频率。**非 bar 数据集**（`stock_status`、`valuation`、财报表）无 bar 频率概念，`frequency` 统一记 `"1d"`（表示"按交易日查询"，非"日线 bar"）；实现时不得据此把 status/valuation 当作日线 bar 表去取 OHLC。

#### IndicatorNode
```json
{
  "operation": "ma" | "ema" | "std" | "pct_change" | "max" | "min" | "sum" | "ref" | "...",
  "field": "close" | "open" | "high" | "low" | "volume",
  "lookback": 20,
  "source_indicator": "<上游 IndicatorNode.output>"   // 复合指标时引用
}
```
纯时序计算（单标的纵向）。

#### FactorNode
```json
{
  "operation": "zscore" | "winsorize" | "neutralize" | "combine",
  "sources": ["<IndicatorNode.output>", "..."],
  "weights": [0.5, 0.5]   // operation=combine 时
}
```
横截面因子加工。PR6a 仅占位（case 1 不用），完整实现进 PR6b。

#### SignalNode
```json
{
  "operation": "cross" | "threshold" | "compare" | "and" | "or" | "not",
  "sources": ["<IndicatorNode.output>", "..."],
  "direction": "golden" | "death",      // operation=cross 时
  "threshold": 0.0,                     // operation=threshold 时
  "comparator": "gt" | "lt" | "ge" | "le" | "eq"
}
```
产生布尔/方向信号。

#### RankingNode
```json
{
  "operation": "rank" | "top_n" | "bottom_n",
  "source": "<IndicatorNode.output> | <FactorNode.output>",
  "ascending": false,
  "top_n": 10                // operation=top_n 时
}
```

#### PortfolioNode
```json
{
  "kind": "single_position" | "equal_weight_top_n" | "signal_weighted" | "...",
  "max_positions": 10,
  "rebalance": "daily" | "weekly" | "monthly" | "signal_triggered",
  "target_weight": 1.0       // kind=single_position 时
}
```

#### RiskNode
```json
{
  "kind": "position_limits" | "stop_loss_take_profit",
  "max_single_weight": 0.1,
  "cash_buffer": 0.02
  // stop_loss_percent / take_profit_percent 进 PR6b 完整字段
}
```

#### ExecutionNode
```json
{
  "order_api": "order_target_value" | "order_value" | "order_target" | "order",
  "match_price_mode": "close" | "open" | "next_open",   // 透传 Spec.execution.match_price_mode
  "order_type": "market" | "limit",
  "allow_partial_fill": false
}
```

#### DiagnosticNode
```json
{
  "kind": "log" | "record_metric" | "run_card_evidence",
  "fields": ["entry_signal", "exit_signal", "fill_price"]
}
```

---

## 3. signals.steps → IR 节点映射规则

Spec 的 `signals.steps` 是有序操作数组（strategy_spec.example.json 行 52-57）。每个 step 的 `operation` 映射到 IR 节点类型如下：

| Spec `operation` | 目标 IR node_type | parameters 透传规则 |
|---|---|---|
| `pct_change` / `ma` / `ema` / `std` / `max` / `min` / `sum` / `ref` | `IndicatorNode` | `operation`/`field`/`lookback` 直接进 `parameters` |
| `zscore` / `winsorize` / `neutralize` / `combine` | `FactorNode` | `sources` 从 step `parameters.source` 解析为单元素数组 |
| `cross` / `threshold` / `compare` / `and` / `or` / `not` | `SignalNode` | `sources` 从 `parameters.source`（单值）或 `parameters.sources`（数组）解析 |
| `rank` / `top_n` / `bottom_n` | `RankingNode` | `source` 从 `parameters.source` 解析；`top_n` 从 `parameters.top_n` 或 Spec `portfolio.parameters.max_positions` |
| `filter` | `HardFilterNode`（前置）或追加到既有 HardFilterNode | 视语义：若过滤依赖动态状态（ST/停牌）→ HardFilterNode；若依赖计算后的指标阈值 → SignalNode。**PR6b 需给出确定性判定规则**（按 filter 的 parameters 依赖静态 universe 属性还是计算后指标），消除本行的自由裁量空间。case 1 不用 filter，PR6a 不实现此分支。 |

**上游引用解析**：step 的 `parameters.source` / `parameters.sources` 引用的是**前序 step 的 `id`**。`build_strategy_ir` 必须把 `id` 翻译为对应 IR 节点的 `output` 名（命名规则：`<step_id>` 本身即为 `output`，保证可追溯）。

**未覆盖 operation 的处理**：`build_strategy_ir` 遇到上表外的 `operation` 必须 `raise`（`ContractValidationError`），**不得静默降级**（framework-contract 不变量：能力缺口 BLOCK 非 WARN）。错误信息含 step id + operation 值 + "进 PR6b 扩展"。PR6a 仅需支持 case 1 用到的 operation：`ma`、`cross`。

**节点依赖顺序**：`build_strategy_ir` 按 step 出现顺序 emit IR 节点，每个节点的 `input` 引用其 `source` 对应节点的 `node_id`。若引用了后序 step（环依赖），`validate_strategy_ir` 必须 BLOCK。

---

## 4. IR 流水线顺序与跨节点不变量

master plan §6 R3（行 535-546）定义 10 步平台无关流水线：

```text
build_universe → apply_hard_filters → load_market_data → compute_features
→ generate_signals → rank_and_select → build_target_weights
→ apply_risk_constraints → submit_orders → record_diagnostics
```

映射到 11 节点类型的允许顺序（`validate_strategy_ir` 强制）：

| 流水线步 | 允许的 node_type | 位置约束 |
|---|---|---|
| build_universe | `UniverseNode` | 必为首节点；全 IR 仅 1 个 |
| apply_hard_filters | `HardFilterNode`（stage=selection） | 紧随 UniverseNode |
| load_market_data | `DataLoadNode` | 在 HardFilterNode 之后、IndicatorNode 之前 |
| compute_features | `IndicatorNode` / `FactorNode` | 在 DataLoadNode 之后、SignalNode 之前 |
| generate_signals | `SignalNode` | 在 Indicator/Factor 之后、RankingNode 之前 |
| rank_and_select | `RankingNode` | 在 SignalNode 之后、PortfolioNode 之前 |
| build_target_weights | `PortfolioNode` | 在 RankingNode 之后、RiskNode 之前 |
| apply_risk_constraints | `RiskNode` | 在 PortfolioNode 之后、ExecutionNode 之前 |
| submit_orders | `ExecutionNode` + `HardFilterNode`（stage=execution） | ExecutionNode 在 RiskNode 之后；execution-stage HardFilterNode 紧贴 ExecutionNode |
| record_diagnostics | `DiagnosticNode` | 必为末节点；全 IR 仅 1 个 |

### 4.1 跨节点不变量（`validate_strategy_ir` 检查清单）

| ID | 不变量 | 触发条件 |
|---|---|---|
| IR-ORDER-1 | UniverseNode 是 nodes[0] | 始终 |
| IR-ORDER-2 | DiagnosticNode 是 nodes[-1] | 始终 |
| IR-ORDER-3 | 仅 1 个 UniverseNode、1 个 DiagnosticNode | 始终 |
| IR-ORDER-4 | DataLoadNode 必须在 IndicatorNode 之前 | 当两者都存在 |
| IR-ORDER-5 | SignalNode 必须在 RankingNode/PortfolioNode 之前 | 当后者存在 |
| IR-ORDER-6 | ExecutionNode 必须在 PortfolioNode/RiskNode 之后 | 始终 |
| IR-DEP-1 | 每个 node.input 引用的 node_id 必须存在于前序节点 | 始终 |
| IR-DEP-2 | 无环依赖（DAG） | 始终 |
| IR-CAP-1 | 每个 node.required_capabilities 的 capability ID 必须在 capability-model.md 清单内 | 始终 |
| IR-TIMING-1 | DataLoadNode.timing 与 pit_anchor 一致：fundamental+announcement_date 或 reference/bar+previous_date | pit_required=true 时 |
| IR-EXEC-1 | ExecutionNode.match_price_mode 必须等于 Spec.execution.match_price_mode（透传一致性） | 始终 |
| IR-EXEC-2 | execution-stage HardFilterNode 必须存在（ashare-filter-contract.md §2：订单执行阶段必须重检） | asset_class=stock 时 |

---

## 5. platform_mapping（逐节点）

`platform_mapping` 是描述性字段（string），记录 QuantStudio 与 PTrade 各自如何实现该节点。Renderer 据此选择模板分支。PR6a 模板覆盖：QuantStudio/PTrade × daily/minute 四组合。

| node_type | quantstudio | ptrade-default |
|---|---|---|
| UniverseNode | `get_index_stocks` / 直接写 code 到 `g.security` | 同 QS（注入 API 一致） |
| HardFilterNode (selection) | `filter_stock_by_status(filter_type=[...], query_date=None)` | 同 QS |
| HardFilterNode (execution) | `check_limit(code)[code]` + `is_t1_blocked` + `round_to_lot`（shared_ashare_rules） | 同 QS；PTrade 禁止本地批量校验 |
| DataLoadNode | `get_history(count, unit, field, security_list, fq, include=False, is_dict=True)`；可选 `get_fundamentals_batch`（B1 性能优化） | 同 QS 但**禁止 `get_fundamentals_batch`/`get_history_batch`**（ptrade-profile-contract.md：不泄漏本地批量 API） |
| IndicatorNode | `MyTT.MA/EMA/STD` 或 `numpy` 内联（如 双均线策略.py 的 `get_ma`） | 同 QS |
| FactorNode | `pandas` 横截面（PR6b） | 同 QS |
| SignalNode | `numpy` 比较 / `MyTT.CROSS` | 同 QS |
| RankingNode | `pandas.DataFrame.sort_values` + `.head(n)` | 同 QS |
| PortfolioNode | `context.portfolio.cash / n` 等权；`order_target_value` | 同 QS |
| RiskNode | `max_single_weight` 校验在 PortfolioNode 后；stop_loss 进 PR6b | 同 QS |
| ExecutionNode | `order_target_value` / `order_value` / `order_target` / `order`（四选一） | 同 QS；PTrade 无本地撮合扩展 |
| DiagnosticNode | `log.info` + `g.<field>` 记录 | 同 QS |

**差异层总览**（Renderer 的 Profile 分支点，仅这 3 处）：
1. **批量 API**：QS 模板可 emit `get_fundamentals_batch`/`get_history_batch`；PTrade 模板必须退化为循环单标的调用（除非 Profile 显式支持）。
2. **代码 header**：PTrade 产物顶部注明 Profile 版本（`ptrade-default-v1`）。
3. **文件后缀**：QS `<id>_quantstudio.py`；PTrade `<id>_ptrade.py`（master plan R4 行 553-554）。

lifecycle 函数体（`initialize`/`before_trading_start`/`handle_data`/`after_trading_end`）在两平台**完全一致**（ptrade_import.py 注入式 API 决定），Renderer 不在 lifecycle 层做 Profile 分支。

---

## 6. validation_rules（逐节点）

`validation_rules` 是不变量 ID 数组，validators（scan_lookahead / validate_local_strategy / 进 PR6b 的其余 5 个）按 ID 查表执行。

| node_type | validation_rules IDs |
|---|---|
| UniverseNode | `UNIVERSE-NONEMPTY`（universe 非空，否则 BLOCK——空池若来自能力缺失，不得静默 shrink，framework-contract） |
| HardFilterNode | `HARDFILTER-STOCK-13`（stock asset_class 必须含 13 项默认过滤的**全部**，与 ashare-filter-contract.md §1 逐项对齐）、`HARDFILTER-EXECUTION-STAGE`（execution-stage 节点存在）、`HARDFILTER-ASSET-MATCH`（ETF/CB/期货走各自 Profile，不盲继承股票） |

> **HARDFILTER-STOCK-13 裁剪规则**：13 项过滤是 stock asset_class 的默认全集。`build_strategy_ir` 对任何 stock Spec 生成的 selection-stage HardFilterNode **必须列出全部 13 项**，不得隐式裁剪——对 single_stock universe，多数项是 no-op（单只股票要么通过要么整只被拒），但 13 项全部参与求值，保证规则集跨 universe kind 一致、validator 无特判。若某 Spec 因产品需求确实要关闭某项（如科创板策略关闭 `exclude_star_market`），必须在 Spec `hard_filters` 显式置 false，且 IR 节点 `validation_rules` 须追加 `HARDFILTER-EXPLICIT-OVERRIDE` 标注（PR6b 引入该子规则；PR6a case 1 不触发）。execution-stage HardFilterNode 只复查与下单直接相关的动态项（`block_limit_up_buy`/`block_limit_down_sell`/`enforce_t1`/`round_lot` + 停牌/资金），不复跑静态市场/板块排除（那些已在 selection 阶段定形）。
| DataLoadNode | `DATALOAD-PIT-PREVIOUS-DATE`（行情/估值用 `context.previous_date`）、`DATALOAD-PIT-ANN-DATE`（财报用 ann_date）、`DATALOAD-NO-INCLUDE-TRUE`（分钟 `get_history(unit='1m')` 禁 `include=True`） |
| IndicatorNode | `INDICATOR-LOOKBACK-POSITIVE`（lookback > 0）、`INDICATOR-NO-FUTURE-BAR`（不引用未来 bar） |
| FactorNode | `FACTOR-CROSS-SECTIONAL`（横截面，非时序） |
| SignalNode | `SIGNAL-TIMING-CONSISTENT`（信号 timing 与 execution_clock 一致）、`SIGNAL-NO-SAME-CLOSE-TRADE`（T-close 信号禁止同 close 成交，no-lookahead 高危 #2） |
| RankingNode | `RANK-SOURCE-EXISTS`（source 引用的 Indicator/Factor 存在） |
| PortfolioNode | `PORTFOLIO-WEIGHT-VALID`（权重 ∈ [0,1]，单标的 ≤ max_single_weight）、`PORTFOLIO-POSITIONS-EXACT-MATCH`（`code in context.portfolio.positions` 精确匹配，strategy_fidelity_gates.json semantics 契约） |
| RiskNode | `RISK-BEFORE-EXECUTION`（RiskNode 在 ExecutionNode 之前） |
| ExecutionNode | `EXEC-MATCH-PRICE-CONSISTENT`（match_price_mode 透传 Spec）、`EXEC-NEXT-OPEN-CLOCK`（next_open 模式要求 execution_clock=next_open，contracts.py 已校验，IR 层复核）、`EXEC-T1-ENFORCED`（stock asset_class 下 enforce_t1=true） |
| DiagnosticNode | `DIAG-NON-EMPTY`（至少记录 entry/exit signal 与 fill_price） |

### 6.1 validation_rules 与 scan_lookahead 10 项高危的对应

scan_lookahead（PR6a 交付）覆盖 master plan §9（行 1242-1253）的 10 项高危。对应关系：

| §9 高危项 | 触发的 validation_rules ID | 严重级 |
|---|---|---|
| #1 before_trading_start 用当日完整 close | `DATALOAD-PIT-PREVIOUS-DATE` | BLOCK |
| #2 T-close 信号 + 同 close 成交 | `SIGNAL-NO-SAME-CLOSE-TRADE` | BLOCK |
| #3 用当日完整 high/low 判断盘中触发 | `INDICATOR-NO-FUTURE-BAR` + `SIGNAL-TIMING-CONSISTENT` | BLOCK |
| #4 排名/基本面用当日未来可得数据 | `DATALOAD-PIT-ANN-DATE` + `RANK-SOURCE-EXISTS` | BLOCK |
| #5 include=True 误用 | `DATALOAD-NO-INCLUDE-TRUE` | BLOCK |
| #6 财务未按公告日 PIT | `DATALOAD-PIT-ANN-DATE` | BLOCK |
| #7 next_open 订单 T 日提前入账 | `EXEC-NEXT-OPEN-CLOCK` | BLOCK |
| #8 日线代理模式撮合口径不一致 | `EXEC-MATCH-PRICE-CONSISTENT` | BLOCK |
| #9 分钟信号误用未来分钟 | `INDICATOR-NO-FUTURE-BAR` + `DATALOAD-NO-INCLUDE-TRUE` | BLOCK |
| #10 1m→5m 聚合看到未完成 5m bar | `INDICATOR-NO-FUTURE-BAR`（分钟聚合特化） | BLOCK |

**全部高危项 BLOCK，不 WARN**（master plan §9 行 1255：高危项必须阻断生成或回测，不只发出警告；framework-contract 不变量 5）。

---

## 7. PR6a 边界与 PR6b 衔接

### 7.1 PR6a 覆盖
- 本契约全文（11 节点字段 + 映射 + 不变量）。
- `build_strategy_ir` 支持 case 1 用到的 operation：`ma`（IndicatorNode）、`cross`（SignalNode）；UniverseNode/HardFilterNode/DataLoadNode/RankingNode/PortfolioNode/RiskNode/ExecutionNode/DiagnosticNode 的 case 1 子集。
- `validate_strategy_ir`（§4.1 全部 12 条不变量）。
- Renderer 4 模板（QS/PTrade × daily/minute）。
- scan_lookahead（§9 全部 10 项高危）+ validate_local_strategy（语法/lifecycle/Guard/semantics）。

### 7.2 PR6b 补全
- `build_strategy_ir` 扩展 operation：`pct_change`/`ema`/`std`（Indicator）、`zscore`/`winsorize`/`neutralize`/`combine`（Factor）、`rank`/`top_n`（Ranking 完整）、`threshold`/`compare`（Signal 完整）。
- 5 validator：`validate_ptrade_portability` / `check_hard_filters` / `compare_strategy_variants`（§7.36 的 14 维度对比，基于 Spec→IR→Renderer 映射清单而非文本 diff）/ `run_smoke_backtest`（实际回测执行链路）/ `validate_strategy_spec`（IR 级升级）。
- `install_skill.py`。
- 2 template：`strategy_spec.json` / `run_card.json` 模板。
- 9 golden case（case 2-10）。
- RiskNode 完整字段（stop_loss_percent / take_profit_percent）。

### 7.3 不变量版本演进
本契约 `strategy_ir_contract_version = 1.0.0`。任何节点字段/映射规则/不变量的语义变更必须 bump 此版本，并在生成的 IR `contract_versions` 中记录（architecture.md 不变量 7）。PR6b 若扩展 operation 不算语义变更（向后兼容，major 不变，bump minor）；若改 node_type enum 或不变量语义则 bump major。

---

## 8. 自洽性自检（本文件交付前）

- [x] 11 节点清单与 master plan §7.33（行 1027-1039）逐个一致。
- [x] 7 属性类别与 §7.33（行 1041-1049）一致；2 标识字段为展开，已在 §2.1 注明非偏离。
- [x] 流水线顺序与 §6 R3（行 535-546）10 步对齐。
- [x] scan_lookahead 覆盖 §9（行 1242-1253）全部 10 项高危，§6.1 逐项映射。
- [x] HardFilterNode 13 项与 ashare-filter-contract.md §1 一致；execution-stage 与 §2 一致。
- [x] platform_mapping 差异层与 ptrade-profile-contract.md（批量 API 禁泄漏、Profile 决定后缀/header）一致。
- [x] capability ID 引用对齐 capability-model.md（IR-CAP-1）。
- [x] 黄金保护语义（PORTFOLIO-POSITIONS-EXACT-MATCH）对齐 strategy_fidelity_gates.json semantics 块。
- [x] 证券代码后缀统一 QMT 格式，承接 security-code-rules.md §1（Checkpoint 1 评审修正）。
- [x] DataLoadNode 非 bar 数据集 frequency 语义注明（Checkpoint 1 评审修正）。
- [x] §3 `filter` 分流标注 PR6b 确定性判定规则（Checkpoint 1 评审修正）。
