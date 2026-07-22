# 能力状态模型契约

> Derived from docs/strategy-compiler/capability-model.md @ 2026-07-22
> 权威源：docs/strategy-compiler/ 下的原始契约文档
> 本文件为 Skill 派生快照，契约变更时必须同步（见 SKILL.md 同步纪律）

## 1. 多维状态

每项能力分别记录：

- `schema_status`
- `data_status`
- `adapter_status`
- `provider_status`
- `engine_status`
- `platform_status`
- `execution_status`

允许状态至少包含：`AVAILABLE`、`READY`、`DATA_MISSING`、`ADAPTER_MISSING`、`PROVIDER_MISSING`、`ENGINE_MISSING`、`PLATFORM_DEPENDENT`、`DEGRADED`、`SCHEMA_ONLY`、`PLANNED`、`UNSUPPORTED`、`BLOCKED`。

## 2. 推导规则

1. `execution_status=READY` 时六个能力维度只能为 `AVAILABLE` 或 `READY`。
2. 任何 required capability 非 READY，整体状态不得为 READY。
3. 所有 required capability 均 READY 时，整体状态必须为 READY。
4. Tick 能力在第一版不得为 READY。
5. 空表、覆盖不足、字段缺失、频率缺失或输出目录不可写均产生 blocker 和 remediation。
6. `DEGRADED` 只描述已知降级能力，不允许绕过策略的 required requirement。

## 3. 数据门禁检查表

数据库可读、表存在、表非空、日期覆盖、股票池覆盖、关键字段、频率、复权列、PIT 字段、状态字段、Provider 路由、Engine Profile、PTrade Profile、输出目录可写。

## 4. 机器契约

Schema：`quantstudio/strategy_compiler/schemas/capability_report.schema.json`

示例：`quantstudio/strategy_compiler/examples/capability_report.example.json`

**能力报告必须提供证据、可读消息和修复建议，不能只给布尔值。**
