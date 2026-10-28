# Data Model: 常驻采集与回测共存

**Date**: 2026-10-27 | **Spec**: [./spec.md](./spec.md) | **Plan**: [./plan.md](./plan.md)

本文件定义协作件的数据结构、字段校验与状态迁移。所有实体均为**跨进程协作契约**，不改变主库 schema（不改任何表结构）。

---

## 实体 1：BacktestWindowRequest（回测窗请求）

存放于 `data/backtest_window.request`（JSON，原子写：临时文件 + `os.replace`）。

| 字段 | 类型 | 必填 | 校验 | 说明 |
|---|---|---|---|---|
| `request_id` | string(uuid4 hex[:8]) | ✓ | 长度 8–32，唯一 | 本次回测窗标识；删除时按此匹配 |
| `owner_pid` | int | ✓ | > 0 | 回测进程 pid（GUI 进程或 CLI 进程） |
| `strategy_path` | string | ✓ | 非空 | 仅用于可观测（哪个策略在等） |
| `started_at` | string(ISO8601, seconds) | ✓ | 可解析 | 窗口请求创建时间 |
| `heartbeat_at` | string(ISO8601, seconds) | ✓ | 可解析，单调不减 | 最近心跳时间；每 10s 刷新 |
| `ttl_sec` | int | ✓ | 30–600，默认 120 | 超过 `now - heartbeat_at > ttl_sec` 即视为失效 |
| `token` | string | ✓ | 非空 | 与 daemon `instance_token` 无关，仅防误删他人请求 |

**失效判定（daemon 侧）**：`now - heartbeat_at > ttl_sec` ⇒ 删除文件、写留痕 `stale_window_cleared`、恢复采集。

**写入竞争**：同一时刻最多一个窗口请求；若已存在未失效的他人请求，回测侧须等待（不得覆盖），并按队列串行执行。

---

## 实体 2：BacktestWindowState（回测侧状态机）

| 状态 | 含义 | 进入条件 | 退出条件 |
|---|---|---|---|
| `idle` | 无回测窗 | 初始 / 结束清理完成 | 用户发起回测 |
| `requesting` | 正在写窗口请求 | 发起回测 | 写入成功 → `waiting` |
| `waiting_round` | 等待 daemon 退出当前轮（锁被占） | 锁 `try_acquire` 失败 | 锁获取成功 → `precheck`；≥30s → `waiting_unit`；用户取消 → `cancelling` |
| `waiting_unit` | 已请求任务内单元边界中断 | `waiting_round` 超阈值 | 锁获取成功 → `precheck`；用户取消 → `cancelling` |
| `precheck` | 只读试连 + 试查 | 锁获取成功 | 成功 → `running`；失败 → `releasing`（显式错误） |
| `running` | 回测执行中 | 预检通过 | 成功/失败/取消 → `releasing` |
| `cancelling` | 用户取消，等待锁/清理 | 任意等待态取消 | 清理完成 → `idle` |
| `releasing` | 释放锁 + 删请求 + 停心跳 | 任一终态 | 清理完成 → `idle` |

**不变量**：
- `releasing` 必须幂等（重复执行安全）。
- 心跳只存在于 `requesting / waiting_* / precheck / running` 期间。
- 任何状态下进程消失 ⇒ daemon 侧 TTL 兜底。

---

## 实体 3：DaemonYieldState（采集侧让路状态）

| 状态 | 含义 | 迁移 |
|---|---|---|
| `normal` | 正常采集 | 检测到有效窗口请求 → `yielding` |
| `yielding` | 本轮内收到让路要求 | 到任务边界 / 单元边界 → `yielded` |
| `yielded` | 本轮已中断并释放锁，daemon 存活 | 窗口请求删除/失效 → `normal` |
| `skipped` | 轮发起前检测到窗口，本轮未发起 | 窗口删除/失效 → `normal` |

**采集侧不变量**：
- `yielding/yielded` **不得**置 `_running=False`（daemon 必须存活）。
- `yielded` 时本轮 `run_state` 必须写 `interrupted=true` 且 `yield_reason="backtest_yield"`。
- 水位推进必须保持"未完成不推进"（复用既有中断语义）。

---

## 实体 4：YieldRecord（让路留痕）

写于 `run_state` 与日志，供"采集为何没跑"可审计。

| 字段 | 取值 | 说明 |
|---|---|---|
| `skip_reason` | `"backtest_window"` / `"lock_busy_by_client"` | 轮发起前跳过；后者=窗口已过期但锁仍被回测侧持有 |
| `yield_reason` | `"backtest_yield"` / `"stale_window_cleared"` | 轮内让路 / 过期清理 |
| `yield_at` | ISO8601 | 让路发生时间 |
| `yield_boundary` | `"pre_cycle"` / `"pre_task:<name>"` / `"post_task:<name>"` / `"unit"` | 命中的边界点 |

留痕规则：同一窗口只写一条 `skip_reason`（避免每 tick 刷盘，参照 `skip_weekday` 的写法），`yield_reason` 每次让路必写。

---

## 实体 5：BacktestRequestQueue（回测请求队列，GUI 侧）

| 字段 | 类型 | 说明 |
|---|---|---|
| `request_id` | string | 与窗口请求一致 |
| `strategy_path` | string | 策略文件 |
| `params_hash` | string | 参数指纹（用于显示去重，不合并执行） |
| `enqueued_at` | ISO8601 | 入队时间 |
| `state` | enum | `queued` / `running` / `done:ok` / `done:err` / `cancelled` / `rejected` |

**上限**：3（`queued + running` 合计）。超出 ⇒ 新请求 `rejected` 并提示。

---

## 关系

```
BacktestRequestQueue 1 ── 0..1 BacktestWindowRequest（同一时刻仅一个 in-flight）
BacktestWindowRequest 1 ── 0..1 BacktestWindowState（回测侧状态）
BacktestWindowRequest 1 ── 0..n DaemonYieldState 观测（采集侧每 tick 判定）
DaemonYieldState 1 ── 0..n YieldRecord
```

## 与主库 schema 的关系

**无关系**：本功能不改任何表结构、不新增表、不写主库。`run_state` 为 JSON 状态文件（非数据库表），仅追加字段。
