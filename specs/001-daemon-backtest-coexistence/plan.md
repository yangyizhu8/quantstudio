# Implementation Plan: 常驻采集与回测共存（回测前停采集 · 停止后由用户决定恢复）

**Date**: 2026-10-27（口径变更：自动恢复 → 手动重启） | **Spec**: [./spec.md](./spec.md)

## Summary

让用户在常驻采集开启时直接发起回测：回测入口先**请求采集优雅停止**（复用既有 `daemon_stop.request` 通道，任务边界 30s → 任务内单元边界中断），确认进程退出后拿应用级互斥、做可用性预检，再跑回测；回测结束（成功/失败/取消/崩溃）**不自动拉起采集**，由用户手动启动。停止原因（因回测）落到采集侧持久留痕，界面/终端据此显示「采集已因回测停止，请手动启动」。不碰回测口径、撮合、复权、采集任务与水位语义；不做真并发、不读副本、不动 duckdb 版本。

## Technical Context

**Language/Version**: Python 3.11（项目约束）
**Primary Dependencies**: PyQt（GUI 壳）、`filelock`、DuckDB 1.4.5（钉版，不改）
**Storage**: 主库 `data/quantstudio.db`；协作件为文件——`data/daemon_stop.request`、`data/.collector_run.lock`、`data/daemon_status.json`、`data/daemon_run_state.json`、`data/backtest_stop.flag`
**Testing**: pytest；新增/重写单测 + 集成场景
**Target Platform**: Windows 本地桌面（GUI + CLI）
**Project Type**: 桌面应用（单一内核 + 双薄壳）
**Performance Goals**: 等待读数 ≤1s 出现；采集停止在「30s 阈值 + 单单元耗时」内完成；回测启动后 ≤10s 界面显示已停止
**Constraints**: 不改回测取数口径/撮合/费用/复权/PIT；不改采集任务与水位推进；不做真并发/副本读/单写者服务；duckdb 版本闸不动；**不自动拉起采集**
**Scale/Scope**: 单用户本地；同一时刻最多一个回测在跑；队列上限 3

## Constitution Check

| 原则 | 核对结论 |
|---|---|
| 一·代码质量（最小可逆、落点所有者） | ✅ 复用既有停止通道（`daemon_stop.request` + 边界消费），不新造并行停止机制；**删除前一版"窗口/租约"临时机制**，避免双轨（见 fix.md 修订 R3） |
| 二·测试先行（先能复现、可机检、证据落盘） | ✅ T001 基线复现已落盘；验收可机检（哈希/比例/时延/次数） |
| 三·可维护性（单一内核 + 双薄壳） | ✅ `run_backtest` 内统一实现，GUI/CLI 共用；停止通道全系统唯一 |
| 四·纯增益（三审计、显式类型） | ✅ 新增能力型：无采集在跑时不发停止请求，行为与改动前一致；不改回测结果与采集语义 |
| 五·逐位一致与可回滚 | ✅ 验收含哈希逐位一致；回退 = `coexistence=False` 或删除新模块 |

**结论**：通过。

## Project Structure

```
specs/001-daemon-backtest-coexistence/{spec,plan,research,data-model,quickstart,fix}.md
specs/001-daemon-backtest-coexistence/contracts/
quantstudio/pipeline/backtest_window.py      # 重写为「停止采集并等待退出」内核
quantstudio/pipeline/daemon_lifecycle.py     # 改：stop.request 带 reason + 停止留痕
quantstudio/backtest/run_ptrade_strategy.py  # 改：回测前停采集、结束不重启、预检
quantstudio/backtest/providers/duckdb_data_access.py  # 改：fail-closed（保留）
quantstudio/gui/workers.py                   # 改：透传 coexistence
quantstudio/gui/tabs/backtest_tab.py         # 改：共存开关 + 等待态 + 结束提示「请手动启动」
quantstudio/gui/tabs/task_tab.py             # 改：停止文案区分「因回测停止」
tests/test_backtest_window_protocol.py       # 重写：停止请求/等待/超时语义
tests/test_daemon_yield_boundaries.py        # 重写：reason 留痕与边界中断
tests/test_backtest_empty_data_guard.py      # 保留（fail-closed）
docs/evidence/backtest-coexistence-20261027/ # 证据
```

**Structure Decision**：停止能力落在编排层（`backtest_window.py` 作为**唯一**停止客户端 + 等待器），daemon 侧只做"读 reason + 留痕"，不新增第二套中断机制。

## 核心设计

### 1. 停止通道：复用 `daemon_stop.request`，扩展 `reason`

请求件（原子写）：

```json
{ "instance_token": "<daemon token>", "reason": "backtest", "requested_at": "<ISO8601>", "owner_pid": 1234 }
```

- `reason` 兼容缺省：旧 GUI 不写该字段 ⇒ daemon 按 `"user"` 处理（零行为漂移）。
- daemon 侧新增 `consume_stop_request_with_reason(token) -> Optional[dict]`：命中即删除并返回请求体；
  既有 `consume_stop_request_if_matched()` 改为其薄包装（调用方不变）。
- daemon 在轮末把 `stop_reason` 写入 `run_state.stop_reason`；`backtest` 时额外写
  `yield_reason="backtest_stop"`、`yield_boundary`、`yield_at`（供界面判别"因回测停止"）。

### 2. 分级停止（复用既有边界，不新增中断机制）

`stop.request` 的消费点本就是分级的（`daemon_lifecycle.py:680-684` 注释 + `_check_stop_at_boundary`）：
`pre_cycle` / `pre_task:<name>` / `post_task:<name>` / `post_ingest_unit` / `pre|post_quality_audit`；
命中后置 `collector._running=False`，任务内部 `per_stock`/`per_date` 在**下一个单元边界**停下
（`daemon.py:2043/2302/2319/2530`），水位不推进、下次重跑。

- **30 秒阈值**：回测侧等待超过阈值时，界面阶段切为「已请求中断当前任务」——`stop.request`
  本身已表达"尽快停"，阈值只影响**展示语义**，daemon 侧无需新增计时。
- **不新增 `cancel_check` 注入**：上一版为"让路但存活"引入的注入在新口径下无必要，已移除（修订 R3）。

### 3. 回测侧流程（`run_backtest` 内核）

```
coexistence=True 时：
 1. 读 daemon_status：无 daemon 在跑 → 跳过停止（零动作）
 2. 写 stop.request(reason="backtest", instance_token)
 3. 轮询等退出（≤60s，每 0.5s）：on_wait(waited, phase) 每秒+阶段变化时报真实读数
    · 超时 → 抛 CollectorStopTimeout（界面给「继续等待 / 强制终止 / 取消」三选项）
    · should_cancel() → 抛 WindowCancelled；**不回滚已发生的停止**
 4. try_acquire(.collector_run.lock, 0s)：拿不到 → 继续等（可能 GUI 手动采集在跑）
 5. 预检：只读试连 + 试查（不可用 → 显式拒绝，不进入引擎）
 6. fail_closed=True → 跑引擎 → 恢复线程本地开关
 7. 结果校验：无净值记录 → 判失败
 8. finally：释放锁；**不重启采集**；写 backtest_stop.flag 留痕
```

`coexistence=False` ⇒ 完全回退（不读 status、不发停止、不等待、不预检）。

### 4. 停止状态可见性（跨界面进程）

- 采集侧：`daemon_status.json` 由 daemon 退出时清理；「已停止」由 `is_daemon_running()` + 留痕共同表达。
- 留痕件：`data/backtest_stop.flag`（JSON：`reason/at/owner_pid`），回测结束时写入，
  **下次回测启动或用户手动启动时清除**。
- GUI：`task_tab` 既有 3s 轮询发现 daemon 消失后，若 `backtest_stop.flag` 新鲜（<24h）⇒
  文案「⏸ 采集已因回测停止，请手动启动」，否则保持原文案。
- CLI：回测结束打印「采集已停止（原因：回测）。需要时请重新执行启动命令。」

### 5. 空数据防线（保留）

入口预检 + `DuckDBDataAccess.fail_closed`（线程本地开关）+ 结果非空校验 + 合法零交易不误判。

### 6. 双端入口对等

`run_backtest(..., coexistence=True, cancel_check=None)` 为唯一内核；GUI `BacktestWorker`、CLI `main` 均经它。

## 边界与兜底

| 情形 | 行为 |
|---|---|
| 60s 未退出 | 抛 `CollectorStopTimeout`；GUI 弹「继续等待 / 强制终止 / 取消回测」；CLI 给提示并以非 0 退出 |
| 用户取消（等待中） | 回测不开始；采集保持当前真实状态（已发停止请求则仍会停），不重启 |
| 回测进程崩溃 | 采集已停，无需恢复；界面重启后由 `backtest_stop.flag` + 进程不存在表达"已停止" |
| 用户忘记重启 | 不做任何自动拉起；状态持续显式提示（已接受代价） |
| 回测中途失去库访问 | fail-closed 抛错 → 显式失败，不产出空结果 |
| 锁被 GUI 手动采集占用 | 继续等待并显示真实读数；不抢占 |
| 回测期间用户手动停止采集 | 与回测停止同义；结束后不拉起 |

## Complexity Tracking

无（Constitution Check 全通过）。
