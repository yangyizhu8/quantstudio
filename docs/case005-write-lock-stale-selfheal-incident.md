# CASE-005 客户事故归档：写锁残留导致采集停更（三客户，2026-09-17 ~ 09-18）

> 状态：**已闭环**（用户裁定 2026-09-18）；六步全链：归因→派单→反馈审核→闭环→通知→归档
> 事件：2026-09-17 三客户数据停更（写锁残留 `.write_lock` 被已终止进程持有）
> 根因：采集程序异常终止残留写锁 × 旧版「陈锁只告警、不清理」→ 每次写库等 30 s 后失败，停更不再自恢复
> 次生项：7 张宽文本表（AI 研报快照 / 研报文本 / 新闻等）拉取 100% 抛 `'MCPAdapter' object has no attribute '_config'`
> 影响实测量级：客户A 残留锁陈龄 **390,915 s（4.52 天）**、客户B **21,295 s（5.92 h）**；两客户均为无人值守 7×24 部署
> 修复链：`6b8fde1`（批一：写锁死亡自愈 + D1 宽文本修复）→ `7192017`（使用说明失实修正）→ `698d751`（Part A：回收互斥陈旧自清，含 TOCTOU 收窄）
> 通知链：D+0 止血件（用户侧下发）→ 三份详版定稿（**未下发，留档备查**）→ 统一简版（**实际下发件**，含口径桥接）
> 客户验证：用户转述三客户回复完成（2026-09-18）
> 闭环裁定：用户裁定 2026-09-18

## 1. 事件与影响

| 项 | 内容 |
|---|---|
| 现象 | daemon 全部写任务 `WriteLockHeld`（「写锁被持有」）→ 先完成云端拉取再在写库处失败 → 采集全线停更 |
| 失败点 | `writers.write → ensure_write_lock("writers:write:<table>:<batch>")`（锁文件 `data/snapshots/.write_lock`） |
| 卡死形态 | 持有者进程已终止、锁文件残留；旧版 `STALE_SECONDS=600` **只用于错误文案**，不触发回收 |
| 客户A（Windows） | 残留持有者 pid 26168（`writers:write:stock_minutes…`），陈龄 4.52 天 |
| 客户B（Windows） | 残留持有者 pid 19968（`writers:write:etf_minutes…`），陈龄 5.92 h |
| 客户C（macOS） | 同类停更上报；安装目录 `/Volumes/ssd/quantstudio`（个案级残留指纹未入卷，如需补采随客户回复附入） |
| 代价 | 每失败任务固定空等 30 s 后失败；客户B 单笔最贵 = `etf_minutes` 23 批/1034 分片下载 26 分 22 秒后失败（云端拉取全作废） |

## 2. 根因链（逐条取证）

- **L1（主因）陈锁无自愈**：互斥原语 `os.open(O_CREAT|O_EXCL)`，释放仅 `release()` 内 `unlink`；无 `atexit`/信号兜底 → `SIGKILL`/强杀/关机/重启必留残锁，此后无人可写。原裁定「陈锁不自动清除、人工确认」以**有人值守**为前提，客户 7×24 无人值守下前提不成立。
- **L2 心跳不可作存活判据**：`.heartbeat()` 全仓**零生产调用**；长任务（分钟表分片写、`VACUUM INTO` 快照）持锁远超 10 min → 「心跳超时」≠「持有者已死」。⇒ 回收判据必须以**进程存活**为准。
- **L3 错误原因被吞**：`daemon._execute_task` 的 `last_err` 仅在 `except` 赋值，`_run_with_source` 捕获后 `return False` → 兜底行恒为 `last_err=None`（两客户日志逐字一致），告警等级够、归因线索为零。
- **次生 D1**：`mcp_adapter.fetch_table` 的宽文本路由判据读 `self._config`，而 `__init__` 从未赋值 → `_WIDE_TEXT_PASSTHROUGH` 集合 **7/7 表 100% 失败**（与客户B 日志一一对应）。
- **实现-设计不一致（批一自纠）**：设计 §4.3 声明「回收互斥超时/持有者已死自清」，实现只有 `except FileExistsError: return False` → 回收者在「建互斥」与「unlink 目标锁」之间被杀即永久卡死自愈（触发概率低、后果=回到停更）→ 立 Part A。

## 3. 修复链

| 序 | 提交 | 内容 | 验收 |
|---|---|---|---|
| 1 | `6b8fde1` | 批一：payload v2（`v/host/pid_create_time/token`）+ 六判据死亡自愈（reclaim 互斥 + CAS 四字段 + 审计 + 所有权校验 + 优雅释放）+ 文案按判定分流；D1 `MCPAdapter._config` 一行修复 | T7 验收 GREEN：硬门 **AC5**（八进程并发回收恰一获锁）、**AC-replay**（两客户真实 legacy payload 回放）双绿；全量契约 32 passed；回归 134 passed/1 预存（另立追单）；回退双分支 PASS；生产快照目录零差异（前/后基线逐字节一致） |
| 2 | `7192017` | 使用说明失实修正：Q1「次日自动补齐」→ 明确「锁残留」分支 + 四步处置小节 | 单文件 +10/−1；双远程一致 |
| 3 | `698d751` | **Part A**：`.write_lock.reclaim` 回收互斥**陈旧自清**——`RECLAIM_STALE_SECONDS=120`、`reclaim_mutex_stale()` 判据（不可解析/心跳过期/pid 不存在 → 陈旧；存活且新鲜 → 不抢占；psutil 不可用 → fail-closed）、`_clear_stale_reclaim_mutex()` 抢占（**内含 TOCTOU 收窄**：读与删之间他人可能已换成新鲜互斥）、审计锚点 `reclaim_mutex_cleared`；7 个新用例（含 `test_mutex_live_holder_not_cleared_even_when_heartbeat_stale`、`test_stale_mutex_race_eight_processes_still_exactly_one`、`test_selfheal_disabled_does_not_clear_stale_mutex`） | 数据拉取会话实施 + 总调度实测独采；trading 同步门登记 `08f0bbb`（merge 全绿含 snapshot_lock 全量） |

**口径（不得外扩）**：本链只证「**进程已终止后自愈成立**」；**不构成「残锁根治」**——`SIGKILL`/断电的**瞬时**释放不覆盖，最坏表现为心跳过期前**有界等待 ≤10 分钟**；要覆盖属另一立项（P1 `filelock` 原语迁移）。

## 4. 通知链

| 件 | 状态 | 内容要点 |
|---|---|---|
| D+0 止血件：`docs/handoff/customer-lock-rescue-20260917.md` | **用户侧下发** | 取证 → 确认 pid 已不存在（有输出即停手）→ **改名不删除** → 重启 |
| 三份详版定稿：`notice-to-custA / custB / custC-macos-20260917.md` | **未下发（留档备查）** | 完整步骤 + 逐项验证表 + 四步锁残留处置 + macOS bash 版命令 |
| 统一简版：`notice-unified-3customers-20260918.md` | **实际下发件（一条通发）** | 4 步更新（停采集→`git pull`→核对 `7192017`→重启）+ 3 项核对 + **>10 分钟兜底分流**（保持锁原样→回传日志+进程检查输出）+ 预期行为如实声明 + 压缩包用户警告 + **口径桥接**（详版「改名」四步仅适用于更新前旧版本应急） |

**更新方式裁定**：三客户统一 **`git pull`**；**禁用覆盖更新**（三风险：`data/` 丢失=从零重建、`.git` 丢失=后续回人肉、`secrets.env` 被覆盖=断连）。

## 5. 客户验证与闭环

- 客户验证：**用户转述三客户回复完成（2026-09-18）**。
- 【原文附入位】若用户后续转来具体回复文本 / 版本号 / `written=...` 成功行 / `write_lock_reclaim.log`，**原文追加于本节**（不改上文结论）。
- 闭环：**用户裁定 2026-09-18**；六步全链收口。

## 6. 残留注记（不属本案）

- **Part A-2（空互斥 mtime 佐证，约 3 行）独立待批**：属 Part A 之后的边界加固候选，**不并入本案归档结论**，由数据拉取会话按六步另行立项。
- 其他在途（与本案无因果）：`observation:own_conn` 锁生命周期复核（pending）；D2 首跑检查脚本水位线正则假阳性（追单 `tracking-d2-check-watermark-regex-20260917.md`，归 D2 线）；`qfq_bootstrap_item` manifest 漂移（追单 `tracking-qfq_bootstrap_item-manifest-drift-20260917.md`）。

## 7. 归档信息

- 卷宗编号：**CASE-005**（CASE-001/003/004 已占用；本编号取当前空号）
- 证据指针：
  - 验收：`docs/evidence/write-lock-selfheal-acceptance-20260917.md`
  - 实施侧证据：`docs/evidence/write-lock-selfheal-implementation-20260917.md`
  - 预验收（T7 按包自跑）：`docs/evidence/write-lock-selfheal-t7run-20260917_2034.md`
  - 设计：`docs/write-lock-selfheal-design.md`
  - 追单：`docs/handoff/tracking-*.md`
  - 核对惯例（本案固化 C1–C7）：`docs/ops-verification-conventions.md`
- 涉及提交：`6b8fde1`（修复）→ `7192017`（文档）→ `9edce70` / `dbfc80d` / `6cbbc76`+本批（通知与归档，docs-only）
- 跨仓：trading 副本同步门登记 `08f0bbb docs(sync): Part A 同步门登记`
