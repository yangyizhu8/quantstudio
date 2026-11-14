# QualityAudit 读数归属线明细（按规则 × 表 × freq × 采样时间）

- 采样时点：**2026-11-14 04:49:04**（单一时点快照，**非趋势**）
- 数据来源：`data/logs/daemon.log` 中 `[QualityAudit]` 行
- 总量：**9 类错误 / 31 类警告 / checks=336**
- 用途：按总调度 2026-11-14 指示「**按规则、表、freq、采样时间分别交对应归属线**」
- 边界声明见 §四

---

## 一、错误类读数明细（7 条 ERROR，逐条拆分）

| # | 规则（rule） | 表（table） | freq | **采样时间** | 读数 | 附加字段 | 建议核验线 |
|---|---|---|---|---|---|---|---|
| Q1 | `QfqDeadLetter` | —（`qfq_trigger_queue` 队列表） | — | `04:49:04` | `count=19277` | `dead_letter=19277 exceeds 0; reopen or investigate` | **QFQ/复权线** |
| Q2 | `QfqPendingSla` | —（`qfq_trigger_queue`） | — | `04:49:04` | `count=45551` | `pending/retryable_failed older than 72h SLA` | **QFQ/复权线** |
| Q3 | `AdjustmentAnchorDrift` | **`stock_minutes`** | **1min** | `04:49:04` | `count=157` | `FAIL>0.5%`；`max=0.9258`；样例 `000012,000025,000035,000039,000062,000063,000090,000096,000155,000156` | **复权线 + 分钟线** |
| Q4 | `AdjustmentAnchorDrift` | **`etf_minutes`** | **1min** | `04:49:04` | `count=60` | `FAIL>0.5%`；`max=0.5204`；样例 `159110,159119,159209,159220,159232,159307,159331,159332,159333,159355` | **复权线 + 分钟线** |
| Q5 | `RequiredValueNull` | **`etf_daily`** | **daily** | `04:49:04` | `count=6` | **字段=open** | **ETF 日线** |
| Q6 | `RequiredValueNull` | **`etf_daily`** | **daily** | `04:49:04` | `count=6` | **字段=high** | **ETF 日线** |
| Q7 | `RequiredValueNull` | **`etf_daily`** | **daily** | `04:49:04` | `count=6` | **字段=low** | **ETF 日线** |
| Q8 | `AdjustmentCompleteness` | **`etf_daily`** | **daily** | `04:49:04` | `count=6` | `front`（前复权） | **ETF 复权线** |
| Q9 | `AdjustmentFactorConsistency` | **`etf_daily`** | **daily** | `04:49:04` | `count=2` | `front`（前复权） | **ETF 复权线** |

> Q5–Q7 是**同一规则、同一表、同一 freq** 在**三个不同字段**上的三条独立读数（`open`/`high`/`low` 各 6）。**不合并为"18 条"**，也不合并为"一条根因"。

## 二、按维度汇总（便于各线按需取用）

### 2.1 按表

| 表 | 涉及规则 | 读数和 |
|---|---|---|
| `qfq_trigger_queue`（队列） | `QfqDeadLetter` / `QfqPendingSla` | 19,277 + 45,551 |
| `stock_minutes` | `AdjustmentAnchorDrift` | 157 |
| `etf_minutes` | `AdjustmentAnchorDrift` | 60 |
| `etf_daily` | `RequiredValueNull`(×3字段) / `AdjustmentCompleteness` / `AdjustmentFactorConsistency` | 6+6+6 / 6 / 2 |

### 2.2 按 freq

| freq | 涉及规则 | 说明 |
|---|---|---|
| **1min** | `AdjustmentAnchorDrift`（`stock_minutes` 157 / `etf_minutes` 60） | 分钟线复权锚漂移 |
| **daily** | `RequiredValueNull`、`AdjustmentCompleteness`、`AdjustmentFactorConsistency`（均 `etf_daily`） | 日线空值 + 复权完整性/一致性 |
| **—（队列，无 freq）** | `QfqDeadLetter`、`QfqPendingSla` | QFQ 触发队列积压/死信 |

### 2.3 按规则

| 规则 | 出现表数 | 读数组 |
|---|---|---|
| `AdjustmentAnchorDrift` | 2（`stock_minutes`, `etf_minutes`） | 157 / 60 |
| `RequiredValueNull` | 1（`etf_daily`）× 3 字段 | 6 / 6 / 6 |
| `AdjustmentCompleteness` | 1（`etf_daily`） | 6 |
| `AdjustmentFactorConsistency` | 1（`etf_daily`） | 2 |
| `QfqDeadLetter` | 1（队列） | 19,277 |
| `QfqPendingSla` | 1（队列） | 45,551 |

## 三、当前周期与停采请求（供与节点20 对齐）

### 3.1 当前采集周期

| 项 | 读数 | 测点 |
|---|---|---|
| 今日轮次 | `06:02:34 === 开始每日采集轮次 (2026-11-14, daily_time=06:00) ===` | `daemon.log` |
| 上一轮次 | `05:12:16 === 每日采集轮次结束 ===`，其中 `05:12:15 今日轮次完成但有 1 个任务失败，请检查 task_summary` | 同上 |
| **当前在跑任务** | `mcp_stock_minutes_…092127`（`09:21:27` START，**16:11 仍在**处理，已约 6h50m） | 同上 |
| 当前进度读数 | `16:11:50 validator passed=50000 rejected=0`；`16:11:51 MinuteDiff validated=50000 changed=72 unchanged=49928` | 同上 |
| daemon | pid=**39200**，`22:57:41` 起，uptime **17.23h** | 进程实测 |

### 3.2 新停采请求（**14:50:49**，本轮新增）

`data/backtest_stop.flag`（165 B，mtime `2026-11-14 14:50:49`）：

```json
{
  "reason": "backtest",
  "at": "2026-11-14T14:50:49",
  "note": "等待已取消：停止请求已发出，采集将在任务边界退出",
  "owner_pid": 12140
}
```

| 项 | 读数 |
|---|---|
| 请求时刻 | **2026-11-14 14:50:49** |
| 发起方 | `owner_pid=12140`（**不是 daemon 39200**） |
| 预期行为 | **采集将在任务边界退出**（当前仍在 `stock_minutes` 任务内，故未退） |
| 至采样时点（16:11） | 已过 **1 小时 21 分**，**daemon 仍在运行** |
| 是否影响 H1/H3 | **不影响**——进程仍存活，H1 不报 FAIL |

> **对照 11-13 同型事件**：`15:33:54` 收到停止请求 → `16:06:17` 才 `EXIT`（**33 分钟收尾**）。本次请求到采样已 81 分钟未退，因处于**长任务内部**（`stock_minutes` 已跑 6h50m）。

### 3.3 复活守护侧（未受影响）

| 项 | 读数 |
|---|---|
| `15:58:34` | `verdict=OK consecutive=0 gate=skip-threshold(0/3) action=NONE` |
| `16:08:34` | `verdict=OK consecutive=0 gate=skip-threshold(0/3) action=NONE` |

## 四、边界声明（不可越过）

| # | 声明 |
|---|---|
| 1 | 本文所有读数**均为 `04:49:04` 单一时点快照**，**不是趋势**，不可外推为"持续恶化"或"已修复" |
| 2 | **不合并根因**：同一日志内同现**不构成**同源——`qfq_trigger_queue` 队列积压与 `AdjustmentAnchorDrift` 锚漂移、与 `etf_daily` 空值，分属不同规则/表/freq，需各线独立核验 |
| 3 | **不断言数据损坏**：`FAIL>0.5%` 是**判据阈值命中**的读数，其成因（上游因子变动 / 本地写入问题 / 口径差异）**未证实** |
| 4 | **库内行数对账仍缺证**：主库被 daemon 独占（`IO Error: Cannot open file …`），本轮 `SKIP`；**日志侧任务行数（如 `rows=556,228`）只作旁证，不等于库内对账通过** |
| 5 | 本文**不含**任何启停 daemon、改阈值、关告警、接管进程的动作 |
