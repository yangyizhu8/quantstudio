# L3 窗口边界报告（窗口作废，读数作废）

- **窗口**：2026-10-31 15:09:04 开窗 → 15:20:01 作废（实际占用 11 分钟；批准预算 240 分钟，未用尽）
- **结论**：**窗口作废**（按占用声明 §6「任何超出预期的锁冲突 ⇒ 判窗口作废」）；本次不产出任何 W2 停点读数
- **执行会话**：SDD 执行会话（specs/003-delivery-bundle-import）

## 1 开窗事实（16:09 之前的重核与操作）

| 项 | 读数 |
|---|---|
| 他线一次性任务 | `PID 40500 · --mode once --task mcp_etf_daily · 14:47:42 启动` → **15:08:42 自然退出**（未杀，按铁律 19 避让等待 21 分钟） |
| daemon PID（重核） | 19148（`--mode forever`，started 2026-10-31T02:51:59，create_time delta **0 s**） |
| 停进程 | 精确 PID 19148，`Stop-Process`，1 s 内退出；**匹配面双形态核验**（路径形态 `daemon.py` 未命中 → 模块形态 `-m quantstudio.pipeline.daemon` 命中；首次守卫 fail-closed 未误杀） |
| 挡板 | `data/backtest_stop.flag` 已写（`{"owner_pid":27508,"reason":"delivery_window","at":"2026-10-31T15:09:04…"}`） |
| 开窗三读数 | ①目标 PID 进程不存在 = **True**；②全部 daemon 进程 = **0 个**；③flag 存在 = **True** |

## 2 作废原因（超出预期的锁冲突）

- 执行 ①「6 项隔离失败的无隔离复验」时，pytest（PID 42336）**打印 9 个通过点后卡住 >10 分钟**（CPU 采样 14.3 s/20 s，持续忙等；`PYTEST_EXIT=-1` ⇒ 未完成）。
- 同期读 `data/snapshots/.write_lock`：
  ```json
  {"v":2,"pid":40500,"task_id":"event_discovery:292","host":"USER-20251211MP",
   "pid_create_time":1793429262.7355137,"acquired_at":1793429371.1521165,"token":"70a274aa…"}
  ```
  **持有者 pid 40500 已于 15:08:42 退出** ⇒ 这是他线 `event_discovery` 任务遗留的**陈锁**；测试路径在等它，不是在测它。
- 判定：符合「任何超出预期的锁冲突」⇒ 窗口作废；**读数作废，不硬凑**。

## 3 作废后恢复动作（已执行完毕）

| 步 | 动作 | 结果 |
|---|---|---|
| 1 | 终止我自己的 pytest（PID 42336，命令行核验为本次 pytest） | 已终止 |
| 2 | 删除 `data/backtest_stop.flag` | `flag_exists=False` |
| 3 | 恢复采集（唯一路径）`schtasks /Run /TN QS_DaemonForever` | `SUCCESS: Attempted to run the scheduled task` |
| 4 | 恢复后状态 | **daemon pid=20912，started_at=2026-10-31T15:20:01，status=running**（`--mode forever`） |
| 5 | `D:\delivery_l3_tmp\` | **未创建**（两次导出均未启动，无需清理） |

## 4 生产库影响核对（窗口内零写入的证据）

| 文件 | 开窗（15:09:04） | 收窗（15:20:01） | 判定 |
|---|---|---|---|
| `quantstudio.db` | 62,766,198,784 B / mtime 03:20:48Z | 62,766,198,784 B / mtime 07:20:04Z | 大小不变；mtime 变化时点 = **15:20:04（daemon 重启后打开库）**，非窗口内写入 |
| `qfq_aux.db` | 11,643,473,920 B / 06:48:10Z | 同 | 未变 |
| `qfq_aux_mcp_gen1.db` | 53,248 B / 09-06 | 同 | 未变 |
| `qfq_aux_c4resume_staging.db` | 53,248 B / 08-07 | 同 | 未变 |
| `batch_audit.db` | 13,881,344 B / 06:49:28Z | 同 | 未变 |
| `quarantine.db` | 1,408,012,288 B / 06:48:16Z | 同 | 未变 |

> 本会话在窗口内**未执行任何导出**、**未做 CHECKPOINT**、**未改任何 `data/` 文件**；`quantstudio.db` 的 mtime 变化由 daemon 重启解释，并已在此留证。

## 5 SC-013 窗口前后

| 时点 | 结论 |
|---|---|
| 开窗前（04:30） | PASS（35 保护路径 / 0 变化） |
| 收窗后（15:20:19） | **PASS（35 保护路径 / 0 变化 / 新增未跟踪 无）** |

## 6 根因与改进建议（供审核方裁定）

1. **陈锁来源**：他线 `event_discovery:292`（pid 40500）异常退出未释放锁。`snapshot_lock` 的死亡自愈只在 **acquire 时**触发，而本次被测用例似乎在**等锁/重试**而不是触发回收，表现为忙等 10 分钟以上。
2. **建议在占用声明流程中新增「陈锁体检」前置步**（不改变锁协议、不删他人锁文件）：
   - `read_holder()` + `diagnose_holder()` 先判持有者存活；
   - 若持有者已死 ⇒ 走**既有自愈路径**（一次 `acquire_write_lock` + `release_write_lock` 显式触发回收）并留读数，然后再开窗；
   - 若自愈拒绝（跨主机/pid 缺失）⇒ **不开窗**，交由他线/用户处理，不自行删锁文件。
3. **建议向他线提请**：`event_discovery` 任务异常退出未释放写锁属可复现缺陷（应在其 `finally` 释放或补异常路径），需由该线按铁律 08 立项修复；本会话只登记不擅动。
4. **对窗口预算的影响**：本次 11 分钟即作废，未消耗大量预算；重排窗口仍可按 240 分钟安排，但应把「陈锁体检」计入前置（约 2 分钟）。
