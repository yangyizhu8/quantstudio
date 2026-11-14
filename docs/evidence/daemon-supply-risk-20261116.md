# 11-16 数据应到风险登记（采集供血被停采 flag 阻断）

- 登记时间：**2026-11-15 02:05**
- 登记方：客户专属总运维会话（节点4，④侧）
- 裁定依据：总调度 2026-11-15「**登记 11 月 16 日应到风险，延续停采甲裁定**」
- 状态：**已登记 · 只读观察 · 不动作**（甲裁定 = 保持现状，不启停 daemon、不清 flag）

---

## 一、风险陈述

**若 `data/backtest_stop.flag` 在 2026-11-16（周一，交易日）开盘前未清除，则 11-16 的分钟/日线数据将全部落空。**

## 二、证据链（三层，均为实测）

### 2.1 第一层：11-16 是交易日（应到日）

`trade_calendar` 权威读数（列 `cal_date` / `is_open`）：

| 日期 | 星期 | `is_open` |
|---|---|---|
| 11-13 | Fri | **True**（数据已完整） |
| 11-14 | Sat | False（非交易日） |
| 11-15 | Sun | False（今天，非交易日） |
| **11-16** | **Mon** | **True** ← **下一应到日** |

### 2.2 第二层：供血机制每次尝试都被 flag 挡住

`QS_DaemonForever` 任务（3 触发器：BootTrigger + DailyTrigger + DailyTrigger `PT6H`/`P1D`）：

| 项 | 读数 |
|---|---|
| `State` | `Ready` |
| `LastRunTime` | **2026-11-15 00:30:02** |
| `LastTaskResult` | 0（任务本身执行成功） |
| **`NextRunTime`** | **2026-11-15 06:00:00** |
| `NumberOfMissedRuns` | 0 |

**但实际起不来**——`data/logs/daemon_autostart_skips.log` 逐条记录：

```
2026-11-12 17:28:14 skip auto-start: backtest_stop.flag present (manual-restore semantics, ruled 2026-10-28; GUI start or -Force to override)
2026-11-13 18:32:12 skip auto-start: backtest_stop.flag present (...)
2026-11-14 18:30:02 skip auto-start: backtest_stop.flag present (...)
2026-11-15 00:30:02 skip auto-start: backtest_stop.flag present (...)   ← 连续两次
2026-11-15 00:30:02 skip auto-start: backtest_stop.flag present (...)
```

⇒ **机制在跑、判据生效、结果是不启动**。这不是故障，是 **2026-10-28 用户裁定「尊重手动恢复语义」** 的预期行为。

### 2.3 第三层：判据代码位置（可复核）

`scripts/start_daemon_guarded.ps1`：

| 行 | 内容 |
|---|---|
| `L21` | `param([switch]$Force)   # -Force = 应急手动拉起：无视 backtest_stop.flag（2026-10-28）` |
| `L26` | `# ★ 2026-10-28 用户裁定（尊重手动恢复语义）：采集因回测被停（data/backtest_stop.flag …` |
| `L31` | `$stopFlag0 = Join-Path $RepoRoot0 ([IO.Path]::Combine('data','backtest_stop.flag'))` |
| `L34` | `$line0 = "{0} skip auto-start: backtest_stop.flag present (manual-restore semantics, ruled 2026-10-28; GUI start or -Force to override)"` |

### 2.4 当前状态（02:01 实测）

| 项 | 读数 |
|---|---|
| `data/backtest_stop.flag` | **存在**（`mtime 2026-11-14 14:50`，`owner_pid=12140`） |
| daemon 进程 | **0**（`18:18:31` EXIT `normal-exit` 后无新 START） |
| 11-15 00:00–01:58 守卫 | **12 轮全 `skip-normal-exit` / `action=NONE`** |

## 三、影响面（按表 × freq × 应到日）

| 表 | freq | 11-13（最后完整日） | **11-16 预期** |
|---|---|---|---|
| `stock_minutes` | 1min | 1,353,261 | **0（若 flag 未清）** |
| `etf_minutes` | 1min | 531,164 | **0** |
| `stock_daily` | daily | 5,571 | **0** |
| `etf_daily` | daily | 2,181 | **0** |
| `index_daily` | daily | 31 | **0** |

> 注：11-16 的日线通常于当日盘后/次日凌晨采得；若 flag 持续至 11-16 06:00 轮次，则**当日轮次整体不启动**，连带日线一并落空。

## 四、解除条件（须他人执行，本线不动作）

| # | 动作 | 执行方 | 依据 |
|---|---|---|---|
| 1 | **清除** `data/backtest_stop.flag` | 请求发起方（`owner_pid=12140`）或其授权会话 | flag 是手动恢复语义的凭证，清除即"恢复" |
| 2 | **显式启动** daemon（`schtasks /Run /TN QS_DaemonForever`，或 GUI 启动，或 `start_daemon_guarded.ps1 -Force`） | 同上 | `L21` `-Force` 可无视 flag（应急） |
| 3 | 验证：`daemon_status.json` 出现新 pid + `daemon_exitcode.log` 新 START | 执行后自检 | — |

**本线不执行以上任何一步**（甲裁定：只读观察、不启停、不接管）。

## 五、观察口径（本线后续只读核查项）

| 时点 | 核查内容 |
|---|---|
| **11-15 06:00** | `QS_DaemonForever` `NextRunTime` 到点后的结果；`daemon_autostart_skips.log` 是否新增 skip |
| 11-15 全天 | flag 是否被清；daemon 是否被显式拉起 |
| **11-16 06:00 轮次** | 是否启动；若仍未启，则本风险**转为事实缺口**，按 E1/E2/E3 同族登记 |
| 11-16 盘后 | 库内分表行数（无锁窗时）核 11-16 是否为 0 |

## 六、与既有登记的关系（不合并根因）

| 既有项 | 关系 |
|---|---|
| E2 watermark 陈旧（1min 停 10-16/09-03） | **独立**——即使 flag 清除，陈旧 watermark 仍会触发 `a4_verdict=-1/2` 走 repair/full 重拉，挤占当日采集 |
| E3 A4 熔断 951 窗口 | **独立**——同上族 |
| Q1/Q2 `qfq_trigger_queue` 死信 19,277 / 积压 45,551 | **独立**——QFQ 队列问题，与供血闸不同层 |

⇒ 本登记**只描述"flag 阻断供血"这一条**，**不与上述任一项合并为同一根因**。

## 七、边界

本登记为**只读观察产物**：
- **未启停 daemon、未清 flag、未写库、未改阈值/脚本/任务**
- 不构成"处置完成"，仅**登记待办 + 明确责任方**
- 甲裁定延续：本线**只观察、不干预**
