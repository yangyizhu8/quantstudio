# Strategy Compiler 架构契约（PR0）

## 1. 目的与边界

QuantStudio Strategy Compiler 采用编译器流水线，而不是分别手写本地版与 PTrade 版策略。`Strategy Spec v1` 是唯一事实源；后续 Strategy IR、双 Renderer、静态检查、冒烟回测和 Fidelity 对照都必须从同一份 Spec 派生。

```text
自然语言 → 能力探测 → 需求澄清 → Strategy Spec
         → 用户确认硬闸门 → Strategy IR
         → QuantStudio/PTrade Renderer → 验证 → Run Card
```

PR0 只冻结跨模块契约，不实现 Renderer，也不修改回测运行时语义。

## 2. 分层职责

| 层 | 输入 | 输出 | PR0 状态 |
|---|---|---|---|
| Capability Inspector | 数据库、Provider、Engine、PTrade Profile | `capability_report.json` | 契约已冻结，探测器待 PR5 |
| Spec Builder | 用户需求与能力报告 | `strategy_spec.json` | Schema 已冻结 |
| Human Gate | Spec 摘要、时序、近似和差异 | 确认记录 | 契约已冻结，交互待 PR5 |
| IR Builder | 已确认 Spec | `strategy_ir.json` | 待 PR6 |
| Renderers | IR + Profile | 两个平台策略代码 | 待 PR6 |
| Validators | Spec、IR、代码、Profile | 静态报告 | 待 PR6 |
| Smoke Runner | READY Profile 与本地代码 | 回测结果 | 待 PR6；非 READY 必须阻断 |
| Fidelity Adapter | 本地与 PTrade 导出 | 对照报告 | 复用现有比较器，闭环待 PR7 |

## 3. 强制不变量

1. 本地版和 PTrade 版不得拥有独立的业务参数源。
2. 数据、Provider、Engine、平台能力分维度记录；Schema 存在不代表可执行。
3. 未经 R2.5 用户确认不得生成 IR 或代码。
4. 不得用日线数据伪装分钟/Tick 数据。
5. 高危未来函数、硬过滤缺失、能力缺失必须阻断，不得降级为 warning 后继续。
6. 策略文件不得直接访问 DuckDB 或 QuantStudio Provider 内部实现。
7. 任何语义变化必须升级对应契约版本并写入 Run Card。

## 4. 版本化位置

- 契约文档：`docs/strategy-compiler/`
- 可执行 Schema：`quantstudio/strategy_compiler/schemas/`
- 合法示例：`quantstudio/strategy_compiler/examples/`
- 契约验证入口：`quantstudio.strategy_compiler`
- 正式 Skill 源码（PR5）：`skills/quantstudio-strategy-compiler/`

项目目录是唯一源码；安装到用户 Skill 目录的内容仅为发布产物。

## 5. 当前现实边界

- 日线引擎和现有 PTrade 生命周期可用。
- 分钟 Schema/采集基础存在，但 Provider frequency 路由与分钟事件引擎未完成，不能标记 READY。
- Tick 仅预留 Schema/Spec 表达，不能标记 READY。
- 当前 `next_open` 尚不满足真正 pending queue 语义，修复属于 PR2；PR0 不改变既有结果。
- 证券代码规则仍分散，统一属于 PR1。
## PR1 architecture update

Security code classification is now centralized in `quantstudio/backtest/libs/security_code_rules.py`.
