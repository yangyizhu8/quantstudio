# 最终快照（SNAP_003）周六窗口执行简报

- 执行会话：ZCode 稳定化执行会话（2026-08-22）
- 依据：docs/governance-step3-audit.md 第 14 轮（周六窗口放行）+ 交接指令

## 预检（2026-08-22 14:50-14:55）

| 项 | 结果 |
|---|---|
| 磁盘 | D 盘可用 270GB ≥ 27GB ✅ |
| SNAP_001/002 | 均 pinned+protected ✅（index.json 实读） |
| 孤儿 tmp / hash_spill 残留 | 0 ✅ |
| 分片 hash v4 | 已内建于 table_hash（O3 NULL 边界跳过、末片承载 NULL）✅ |
| guard | 扩展名锚定 + QDB 只读白名单（08-20 第 12/13 轮追认+单测）✅ |
| schtasks | 无 cyq/ETL/CloudSync 条目（QDB 侧自建调度，与第 13 轮一致）；22:30 cyq 确认以日志 mtime + guard 启动检查兜底 |

## 启动

- **2026-08-22 14:53:34**：orchestrator 后台武装（task exec_7ef95679），log 显示
  `waiting 30986s until Saturday 23:30 (2026-08-22 Saturday)` — O1 周六定位正确 ✅
- 启动证据：`data/snapshots/final_snapshot_20260822_launch_evidence.json`
- 23:30 orchestrator 断言 weekday==5 后调
  `python scripts/governance_snapshot.py create --source-task final-snapshot-pre-baseline`
- guard 启动检查自动拦截异常写者（含 cyq 未结束 → 退出码 6 REFUSED，无损失）
- 周日 03:00 TradingCloudSyncFull 与快照并行 = QDB 只读白名单设计，预期行为

## 22:30 cyq_chips 结束确认（2026-08-22 22:30:27）

| 项 | 观测 |
|---|---|
| orchestrator | 存活，log 仍为唯一启动行（无异常退出）✅ |
| studio/run_manual.log | mtime 08-20 18:34，无今晚活动 |
| qfq_maintenance.log | mtime 08-21 21:46，无今晚活动 |
| failed.log / guard_refused.log | 最后条目均 08-20，无新失败/拦截 |
| 结论 | **无 cyq_chips 活动迹象，放行 23:30 create**；QDB 侧自建调度不可直接观测，guard 启动检查兜底（未结束 → exit 6 REFUSED 零损失）；全程未 spawn pattern 字面量扫描命令 |

## ⚠️ 事故：create 于周日 04:30 guard ABORTED（rc=6）

**时间线**：
- 2026-08-22 23:30:00 orchestrator 启动 create（启动检查通过，起点干净）
- 2026-08-23 04:30:20 hash 期间 guard 命中 SYSTEM 不可读 python（PID 32312，`matched_pattern: fail_closed`）→ GuardAbort
- 04:30:22 orchestrator rc=6 退出；tmp 已由 finally 清理（failed.log 证实），数据无损，SNAP_001/002 保护不变

**拒因分析**：
- 时点（周日 04:30）与 TradingCloudSyncFull（周日 03:00 启动、历史时长 ~5.5h）高度吻合——其 python 子进程以 SYSTEM 运行、cmdline 不可读 → fail-closed 哨兵命中；
- 交接指令预期"03:00 全量与快照并行属 QDB 只读白名单"——但白名单仅覆盖 `check_etl_integrity / qdb_snapshot_backup` 两个**可读 pattern**；全量同步子进程 SYSTEM 不可读时无法进入白名单比对，退化为 fail-closed abort。**该预期与实现存在缺口**；
- 若该进程确为 DuckDB 写者，abort 属正确一致性保护（非误拦）；若为 QDB 侧只读，则为 fail-closed 保守误伤。两者在不可读条件下不可区分。

**窗口重排矛盾（重试不可行的原因）**：
- 同步预计 ~08:30 结束；create 需 10-11h → 若 08:30 重试将于 ~18:30-19:30 完成，**必然穿越周日 16:00 Trading_Daily_ETL（Daily 含周末，数据侧写者）→ 再次 abort**；
- 今晚 22:30 又有 cyq_chips。周日无干净窗口。

**处置**：按红线停止、不擅自重试（重试窗口选择权在总调度/用户：下周六 23:30 窗口 / 或临时停用 16:00 ETL 后周日白天重试）。

## 事故终版状态（周日 2026-08-23 09:30 watcher 核验）

**裁定（总调度 05:00，用户拍板）**：根因 = v1.19 排期前提错误（周日实有 03:00 全量同步）；guard fail-closed 行为正确、零损失处置正确。周六窗口作废，改排周一 23:30 夜窗口 + 白名单缺口修复加急（详见下节）。

**09:30 现场观测**：
- 快照目录干净：index.json 仍为 SNAP_001/002 两条（protected），无孤儿 tmp；failed.log/guard_refused.log 最后条目即 04:30 事故行，无新事件
- 内存自愈：可用 14.9GB / 总 32GB（04:30 同步占用已释放）
- SYSTEM 不可读 python 现存 5 个：3 个为 08-22 14:22 常驻（23:30 create 启动检查放行过，guard 未见其为写者）；**2 个为 08-23 03:46 出生（同步窗口内）且 09:30 仍存活**——全量同步实际时长超 ~5.5h 历史估计，或含收尾常驻进程；周一方案将以写目标核实判定，不据此推断
- PID 32312 已退出（现 pid 复用为无关 sleep 进程）

**结论**：本窗口（周六 23:30）彻底关闭，无遗留物；SNAP_003 改周一 23:30 窗口（orchestrator_monday 已备待审）。

## 改排期后的执行链（2026-08-23 05:00 总调度指令）

- 周末（今日）：零 create/verify；已预写 ①verify/protect 清单 ②验收模板 ③周一 orchestrator（均落盘 docs/evidence + scripts/）
- 周一 01:05：白名单缺口修复方案（docs/governance-guard-system-proc-design.md，03:05 前送总调度 zcode 审计；第一判据=写目标核实，写快照源则分支备用禁用路线）
- 周一 20:00：修复验收目标点；不及 → 备用授权（周一晚 Trading_Repair_Minutes / 周二 03:00 增量禁用，用即登记）
- 周一 23:30：create → 周二 ~09:30 完成
- 周二 10:00-12:00：按清单 verify→protect→SNAP_002 回填→gate exception 关闭
- 周二下午基线 → 周三晨用户确认 → 双仓库推送（总调度协调，本会话到验收步）

## ⚠️ 第二次事故：周一 23:29:58 create REFUSED（rc=6，零损失，等待裁定）

**拒因（guard_refused.log 原文）**：3 个 **bash.exe** 命中 `run_cloud_sync`：
- pid 25100 / 29584：zcode exec 持久 shell（`bash.exe -c . /c/Users/.../shell-snapshots/snapshot-bash-*`，完整 cmdline 含历史命令文本——本会话 Bash 工具此前多次出现 run_cloud_sync 字面量，如重命名/检索命令）；
- pid 46048：`bash /tmp/wait_until.sh 2026-08-25 10:00 ...恢复两个wrapper（正确日期版）：run_cloud_syn...`——**总调度会话的恢复等待器**，其描述文本内嵌 pattern 字面量。

**根因**：`SHELL_PROC_NAMES = {powershell, pwsh, cmd}` 未含 **bash.exe**——bash 走原子串匹配 → 命令文本裸提及 pattern 即命中。与 08-20 DSH 裁定 2（扩展名锚定，防 PowerShell 自指）**同类缺陷的 bash 遗漏**。三个命中均为监控/等待器文本自指，无一真实数据侧任务。

**拟修复（待总调度批准，同既审锚定模式的最小扩展）**：
```python
SHELL_PROC_NAMES = {"powershell.exe", "pwsh.exe", "cmd.exe", "bash.exe", "sh.exe", "wsl.exe"}
# 锚定检查扩为 pat+".ps1" / pat+".py" / pat+".sh"
```
- 语义保持：真实任务三形态全覆盖（ps1 包装器/py 本体/**sh 包装器**）；裸提及（任何 shell）不再误报；
- python fail-closed / SYSTEM 归因 / 白名单均不动；
- 单测补充：bash 内联裸提及不命中 + bash xxx.sh 命中两用例；
- 该修复属 08-20 已审计"扩展名锚定"裁定的直接补全（遗漏族补齐），非新语义。

**另记录**：23:01 / 23:26 有两次 `verify REFUSED`（pid 3 fail_closed）——非本会话动作（疑为守护/他域探测），一并知会。

**时间窗影响**：若 00:00 前修复+重启 create → 周二 ~10:00 完成，verify 顺延（清单已注"不强行中断"语义，跨 12:00 可接受）。

## verify RSS 越线决断记录（2026-08-25 23:36-23:55，总调度授权自主决断）

**实测**：verify PID 25968（22:20 启动）RSS 呈**锯齿有界**模式：90s 采样 2,134→5,648MB（Δ+3.5GB，表级分片切换的正常波动——小表低/大表 stock_minutes·etf_minutes 高）；peak WS 6,904MB，与总调度 23:36 观测（6,275MB）对比 ~80min 内峰值平台 ~6-7GB，**非单调爬升**；系统可用 6.3GB（>3GB 底线），pagefile 缓冲充足，无 OOM 征兆。

**决断：允许越线跑完**。依据：①分片设计目标=内存有界，实测峰值超 4GB 预算但呈平台不失控；②半程停止代价（~3h + 收官链顺延至交付日）大于风险；③满足总调度放行条件（RSS 平台 + 可用>3GB）。

**自动停止线（已挂监控 exec_18294ee8，5min 周期）**：系统可用 <2GB 即告警停止；进程退出即唤醒进入 protect 链。峰值 >9GB 持续亦为停止线（人工判定）。

**同场里程碑：marker 归因实战首验通过**——22:05 启动时 repair SYSTEM 不可读 python（PID 37424）被 `qdb_domain:run_repair_minutes` marker 父链归因正确豁免（marker 文件 run_repair_minutes_41396.json），v1.1 白名单机制真实环境首次生效，未误杀、未漏拦（ETL 16:00 写者仍正确 abort）。

## 后续（周日 08-23，已被上节取代）

1. ~09:30 create 完成 → manifest 核验（pending_verify=true / protected=false 预期）
2. 10:00 分片 verify（v4，`governance_snapshot.py verify <snap_id>`，严禁全量路径）
3. verify PASS → `bind <snap_id> <result_dir> --protect`
4. SNAP_002 分片 verify 回填 + 批 2 gate exception 关闭记录
5. 证据落盘 docs/evidence/，本简报滚动更新

## 红线遵守

- 无 git commit/push（只到验收步）
- 不动生产库数据；仅快照机制自身 manifest/hash 写
- 周日 11:00-12:00 SEGMENT-2 窗口内不做 governance_snapshot 之外操作
