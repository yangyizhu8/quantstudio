# Strategy Spec v1 契约

> Derived from docs/strategy-compiler/strategy-spec-contract.md @ 2026-07-22
> 权威源：docs/strategy-compiler/ 下的原始契约文档
> 本文件为 Skill 派生快照，契约变更时必须同步（见 SKILL.md 同步纪律）

## 1. 唯一事实源

每个策略保存独立的 `strategy_spec.json`。本地版、PTrade 版、Run Card 和一致性检查均以此为源，不把用户策略追加到 Skill 核心说明。

机器可验证定义位于：

`quantstudio/strategy_compiler/schemas/strategy_spec.schema.json`

## 2. 顶层字段

| 字段 | 含义 |
|---|---|
| `spec_version` | 固定为 `1.0` |
| `strategy_id/name/type` | 稳定标识和策略类型 |
| `asset_class` | `stock`、`etf` 或 `mixed` |
| `target_platforms` | 目标 Renderer/Profile |
| `contract_versions` | 所有影响语义的版本 |
| `time_model` | 数据、信号、决策、成交与估值时序 |
| `engine_profile` | 事件类型、频率、深度和可执行状态 |
| `universe` | 股票池构建步骤 |
| `hard_filters` | 不可静默关闭的交易真实性规则 |
| `signals` | 平台无关信号步骤 |
| `portfolio` | 选股、权重和再平衡规则 |
| `execution` | 原生/代理模式、撮合口径与订单类型 |
| `risk/costs` | 风控与成本 |
| `data_requirements` | 数据表、频率、字段、PIT 要求 |
| `capability_requirements` | 必须 READY 的能力 ID |
| `approximations` | 代理与近似的完整披露 |
| `user_confirmations` | R2.5 等硬闸门记录 |
| `validation_policy` | 阻断策略和冒烟策略 |
| `output` | 输出根目录与覆盖规则 |

## 3. 关键约束

- 不允许单一 `frequency` 字段替代 Time Model 中的四类频率。
- Bar Profile 的 `market_data_frequency` 必须等于 `bar_frequency`。
- 因子、信号和估值频率不得细于市场数据频率。
- `match_price_mode=next_open` 必须配套 `execution_clock=next_open`。
- `daily_open_proxy` 只能配 `open`；`daily_close_proxy` 只能配 `close`。
- 代理模式必须有 `approximation=true`、原因、风险披露和 `user_confirmed=true`。
- 当日完整 close 形成信号并按同一 close 成交属于阻断项。
- 股票 Profile 必须启用 T+1、停牌、涨跌停、退市风险、无效价格、零成交量与整手规则。
- Tick 可以表达，但第一版执行状态只能是 `BLOCKED`、`PLANNED` 或 `UNSUPPORTED`。

## 4. 版本兼容

`spec_version=1.0` 的读取方必须拒绝未知必需语义，不能猜测。新增可选展示字段可保持小版本兼容；字段含义、默认值、时序或交易结果发生变化必须提升相应语义版本。迁移必须显式生成新 Spec，不使用隐藏开关。

## 5. 示例

合法示例位于 `quantstudio/strategy_compiler/examples/strategy_spec.example.json`，并由契约测试持续验证。
