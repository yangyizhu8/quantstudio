# Contract: run_state 扩展字段

**Date**: 2026-10-27 | **Plan**: [../plan.md](../plan.md)

`run_state`（`data/run_state.json` 一类状态文件，非数据库表）**既有字段全部不变**，本契约只**追加**字段与枚举值。读取方必须容忍未知字段（既有实现已按 `dict` 读取）。

## 追加字段

| 字段 | 类型 | 出现时机 | 取值 | 语义 |
|---|---|---|---|---|
| `skip_reason` | string | 轮发起前检测到回测窗，或窗口已过期但仍被客户端锁占用 | `"backtest_window"` / `"lock_busy_by_client"` | 本轮未发起；`backtest_window`=回测窗有效；`lock_busy_by_client`=窗口已过期但锁仍被回测侧持有（plan 5.4）。与既有 `"skip_weekday"` 并列 |
| `yield_reason` | string | 轮内让路 | `"backtest_yield"` / `"stale_window_cleared"` | 让路原因；`stale_window_cleared` 表示窗口过期被清理后恢复 |
| `yield_at` | string(ISO8601) | 同上 | — | 让路发生时间 |
| `yield_boundary` | string | 同上 | `"pre_cycle"` / `"pre_task:<name>"` / `"post_task:<name>"` / `"unit"` | 命中的边界点；`unit` 表示任务内单元边界 |
| `interrupted` | bool | 既有字段 | 保持既有语义 | 让路时为 `true`（沿用既有中断语义，不新增"假完成"） |
| `stop_requested` | bool | 既有字段 | 保持既有语义 | **让路不得置 `true`**（让路 ≠ 用户停止） |

## 兼容性规则

1. **只增不改**：不得改变既有字段含义、类型与默认值。
2. **互斥**：`skip_reason="backtest_window"` 与 `skip_reason="skip_weekday"` 不同时出现；同一轮只记一次（避免每 tick 刷盘）。
3. **不冒充完成**：`yield_reason` 非空的轮次，其 `task_summary` 中未执行任务必须为 `skipped`，`traversal_completed=false`。
4. **水位**：让路轮次的水位推进必须与既有中断路径一致（不推进），不得因新增字段而改变。

## 读取方契约

- GUI 任务页：显示"因回测让路暂停（已让路 N 次）"，与"采集故障"区分。
- 健康检查：`yield_reason` 存在不得被判为失败（属预期行为）。
