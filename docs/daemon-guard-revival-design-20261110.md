# 守护链复活能力方案（QS_DaemonHeartbeat 自动拉起 + QS_DaemonForever 双触发 + 防误拉）

- **日期**：2026-11-10｜**提出**：⑳ 运维2 R136 结构性缺口｜**起草**：客户专属总运维会话（④节点）
- **性质**：框架层改动 → **六步流水线①（本件为方案，未实施）**；实施落点与**节点1**协同
- **判型（铁律 18）**：**新增能力型**（新增自动拉起能力；既有 H1/H2/H3 判据与退出码语义**零变更**＝既有告警行为不得漂移）
- **修订**：**v1.1（2026-11-10）**——按调度补记建议增补 **R3b「自停未声明」** 形态（⑳ 23:44 实证）、新增 **F11 旁证探测（第 4 闸）**、新增 T8/T9（旁证实现 + 观察模式）；R3 拆为 R3a（误报型）/ R3b（自停未声明型）。
- **知识反哺检索留痕**：`知识反哺：[无命中] knowledge/**（daemon / 计划任务 / 守护链族）+ knowleage_base（运维域 MOC），2026-11-10`——本项目知识库中尚无「守护链复活」条目；本件若通过②审与实施验收，须按 R4 另行沉淀为 `db` 或新族条目。

## 一、背景与动机

### 1.1 现状（本次实测取证，2026-11-10 02:32）

| 组件 | 实测事实 | 来源 |
|---|---|---|
| `QS_DaemonHeartbeat` | 每 **PT10M** 重复；`ExecutionTimeLimit=PT5M`；`MultipleInstancesPolicy=IgnoreNew`；执行 `check_daemon_alive.ps1 -Quiet -IntervalMinutes 10` | `schtasks /query /xml` |
| `check_daemon_alive.ps1`（16124B，mtime **10-26 08:50**） | 有 H1（进程）/ H2（status 一致性）/ H3（心跳新鲜）三判据；退出码 `0=OK / 2=WARN / 3=FAIL`；**全文无任何 `Start-Process` / `schtasks /Run` 调用** ⇒ 确认「只告警不拉起」 | 源码审阅（A 段 grep 无拉起语句） |
| `QS_DaemonForever` | **仅 `BootTrigger`**，无 `CalendarTrigger` / `Repetition`；`S4U`；`ExecutionTimeLimit=PT0S`；`IgnoreNew`；执行 `start_daemon_guarded.ps1` | `schtasks /query /xml` |
| `daemon_occupancy.json` | **已有完整可机读契约**：`occupied / window / operation / owner / purpose / expected_recovery / rollback / note / retroactive_log`；已入 `.gitignore:121`；当前声明窗 `2026-11-09T22:30:57+08:00 → 2026-11-10T10:30:57+08:00`（owner=⑳） | 文件直读 |

### 1.2 结构性缺口（R136）

1. **无复活能力**：daemon 一旦死亡（正常退出/异常退出/外部终止），**只能等下次开机**（Forever 仅 BootTrigger）或人工 `schtasks /Run`；Heartbeat 只会每 10 分钟刷一条 FAIL 到 `daemon_heartbeat_alerts.log`，**告警不等于恢复**。
2. **实测代价**：11-02 停摆（A1 强杀后 23h+ 无 daemon）、11-04 异常退出（`ExitCode=1` 后无人拉起）、11-08/11-09 多次「死亡→人工重拉」循环 —— 每次停摆都直接造成采集轮缺失（11-02、11-04 两个缺口日已入台账）。
3. **告警疲劳副作用**：H1 判据不区分「正常退出」与「静默死亡」，优雅退出后仍连发 FAIL（10-30 三案、11-02 十九案共 40+ 条误报），使「真死」与「正常停」在日志上同形 —— 这会**削弱将来自动拉起判据的可信度**（若直接对 FAIL 计数，则每次正常停都会触发误拉）。

### 1.3 目标

在不改变既有告警判据语义的前提下，为守护链补上**受控的自我复活能力**：真死⇒限次自动拉起；正常退出/占用声明窗⇒不拉。

## 二、范围与边界

### 2.1 改动范围（实施落点，本次不动）

| # | 落点 | 改动性质 |
|---|---|---|
| L1 | `scripts/check_daemon_alive.ps1` | **新增**「连续 FAIL 计数 + 复活决策 + 调用拉起」段；**既有 H1/H2/H3 判据与退出码 0/2/3 一字不改** |
| L2 | 新文件 `data/daemon_revival_state.json` | 新增（连续 FAIL 计数 / 上次拉起时点 / 冷却窗；运行态，入 `.gitignore`） |
| L3 | `QS_DaemonForever` 任务定义 | 新增 `CalendarTrigger`（保留 `BootTrigger`）；注册用 `Register-ScheduledTask` 或 `schtasks /change` |
| L4 | `scripts/start_daemon_guarded.ps1` | **不改**（拉起仍走它，复用现有 stderr 分文件 / EXIT 补记逻辑） |
| L5 | 文档 | 本方案 + 实施后 `docs/handoff/` 变更说明 |

### 2.2 不改动范围（硬边界）

- ❌ **不改** H1/H2/H3 判据、匹配式、`StaleFactor`、退出码语义（`0/2/3`）—— 既有告警行为零漂移；
- ❌ **不改** daemon 本体（`quantstudio/pipeline/daemon.py`）任何逻辑；
- ❌ **不改** `daemon_occupancy.json` 的 schema（只**读**它）；
- ❌ **不改** 采集/写入路径、QFQ 管线、schema；
- ❌ 不引入新的守护进程（复活由**既有** Heartbeat 任务承担，不加常驻进程）。

### 2.3 兼容边界

- 老版 `daemon_occupancy.json`（缺字段）⇒ 按「未占用」处理并 WARN（不阻塞复活）；
- `daemon_revival_state.json` 缺失 ⇒ 首次运行视作计数 0（自建）；
- 手工 `Stop-Process` 停 daemon（如窗口内）⇒ 若未写占用声明，**会被自动拉起**——这是**已知且必须在文档中明示**的行为变化，缓解手段见 §四 R1。

## 三、任务拆解与功能点分解表

### 3.1 分步任务

| 步 | 任务 | 产出 |
|---|---|---|
| T1 | 复活状态文件契约定义 + 读写函数（幂等、原子写） | `daemon_revival_state.json` 读写段 |
| T2 | 连续 FAIL 计数逻辑（仅对 `verdict=FAIL` 且**非正常退出**计数） | 计数 + 归零逻辑 |
| T3 | 复活决策闸（三闸全过才拉） | 决策函数 + 日志 |
| T4 | 拉起执行（唯一路径 `schtasks /Run /TN QS_DaemonForever`）+ 拉起后自证 | 执行段 |
| T5 | `QS_DaemonForever` 加 `CalendarTrigger`（每日 06:00 + 每 6 小时保底） | 任务定义 XML / 注册脚本 |
| T6 | 负向对照测试组（4 组） | 测试记录 |
| T7 | 修订 `runbook` / `AGENTS.md` 相关条目（铁律 19 补「自动拉起例外」+ **「停共享 daemon 前必须写声明」升级为硬闸**） | 文档 diff |
| **T8** | **旁证探测实现（F11 · R3b 专项）**：WAL 活跃 / 独占试探 / 自停痕迹 / 锁文件时间 四探 + 决策分支 | `SKIP(suspected_activity)` 分支 + 日志字段 |
| **T9** | **观察模式开关**：`-ReviveMode observe|active`（默认首 24h = `observe`，只记 `REVIVE_ARMED` 不实拉） | 模式开关 + 观察期日志 |

### 3.2 功能点分解表（每行独立可验证）

| ID | 功能点 | 实现要点 | **验收判据（可机检 / 可复跑）** |
|---|---|---|---|
| **F1** | 连续 FAIL 计数 | 仅当 `verdict=FAIL` **且** `exitcode.log` 最近一条**不是** `ExitCode=0 (normal-exit)` 时 `consecutive_fail += 1`；否则归零 | 构造：①正常退出（`ExitCode=0`）连跑 5 次 ⇒ 计数**恒 0**、不拉起；②真死连跑 3 次 ⇒ 计数 **1→2→3** |
| **F2** | 第 1 闸·阈值闸 | `consecutive_fail >= N`（默认 **N=3**，可 `-ReviveThreshold` 覆盖）才继续 | 计数=2 时**不拉起**；=3 时进入下一闸（日志可证） |
| **F3** | 第 2 闸·占用声明闸 | 读 `daemon_occupancy.json`：`occupied=true` 且 `now ∈ [window.start, window.end]` ⇒ **禁拉**，日志记 `SKIP(occupancy)`；JSON 缺失/损坏/字段缺 ⇒ 视为未占用 + WARN | 构造占用窗覆盖当前时刻 ⇒ **零拉起调用**（`schtasks /Run` 计数 0，日志含 `SKIP(occupancy)`） |
| **F4** | 第 3 闸·冷却闸 | 上次拉起距今 < 冷却窗（默认 **30 分钟**）⇒ 禁拉，日志记 `SKIP(cooldown)` | 连续两次触发（间隔 <30min）⇒ 第二次 `SKIP(cooldown)`，拉起调用仅 1 次 |
| **F5** | 拉起执行（唯一路径） | 调 `schtasks /Run /TN QS_DaemonForever`；**禁 `Start-Process`**（不受 IgnoreNew 单实例保护，窗口卡 C2 已立规） | 源码 grep：`Start-Process` 仅出现在注释/文档，**代码路径零使用**；实测拉起成功（`LastTaskResult=0`） |
| **F6** | 拉起后自证 | 拉起后等待 ≤60s，核 `daemon_status.json` 的 `started_at` 已更新且进程存活 ⇒ 记 `REVIVED`；未恢复 ⇒ 记 `REVIVE_FAILED` 并保留计数（下轮再试） | 构造真死 ⇒ 60s 内 `REVIVED` 且 `status.json` 新鲜；构造「拉起失败」（临时改名任务）⇒ `REVIVE_FAILED` 且计数不清零 |
| **F7** | 计数归零时机 | 仅在**成功恢复**（F6 = REVIVED）或**正常退出**（F1 判据）时归零 | 恢复后下一轮 ⇒ `consecutive_fail=0` |
| **F8** | Forever 双触发 | 保留 `BootTrigger` + 新增 `CalendarTrigger`（**每日 06:00**，以及保底每 6 小时）；`IgnoreNew` 保单实例 | `schtasks /query /xml` 含**两个** `<Trigger>`（Boot + Calendar）；重跑注册脚本幂等（不产生重复触发器） |
| **F9** | 既有告警零漂移 | H1/H2/H3 判据、匹配式、`StaleFactor`、退出码 `0/2/3` 全不变 | `git diff` 该文件**只增不减**既有判据区；用固定 fixture 跑 OK/WARN/FAIL 三态 ⇒ 退出码与改前一致（对照记录） |
| **F10** | 留痕完整 | 每次决策落一行结构化日志（`ts / verdict / consecutive / gate_result / action / target_pid`）到 `data/logs/daemon_revival.log` | 造 4 组场景 ⇒ 日志 4 行齐全、字段可解析 |
| **F11** | **第 4 闸·旁证探测（2026-11-10 新增 · R3b「自停未声明」专项）** | 拉起前（在 F3 之后）做**四项旁证**，任一命中即 `SKIP(suspected_activity)`、不拉：<br>① **WAL 活跃探测**：`quantstudio.db.wal` 存在且**近 5 分钟内 mtime 有变**或两次读数不等 ⇒ 有写活动<br>② **独占试探**：尝试 `[System.IO.File]::Open(db,'Open','ReadWrite','None')` 成功 ⇒ 无他人持有（可拉）；失败 ⇒ 有进程持库（不拉）<br>③ **自停痕迹**：`daemon.log` 近 30 分钟内出现 `收到停止请求` / `已清理本进程 status 文件` 而 `exitcode.log` 对应时段**无** `EXIT … (normal-exit)` ⇒ 疑似他线强停（不拉）<br>④ **锁文件时间**：`data/.daemon.lock`（如存在）mtime 在 30 分钟内 ⇒ 疑似有进程刚接管（不拉） | 构造「自停未声明」场景：他线持库写 + 无 occupancy 声明 + 连续 3 轮 FAIL ⇒ **拉起调用计数必须为 0**，日志为 `SKIP(suspected_activity)` 并注明命中哪一项；反向构造「真死且无写活动」⇒ 四探全过、正常拉起 |

## 四、风险描述

| # | 风险 | 影响 | 规避 |
|---|---|---|---|
| **R1** | **误拉**：窗口内人工停 daemon 但未写占用声明 ⇒ 30 分钟内被自动拉起，污染窗口证据 | 高（窗口证据失真） | ①F3 占用闸为**主**防线；②实施件同步修订铁律 19 补「停 daemon 必须先写 `daemon_occupancy.json`，否则会被自动拉起」；③冷却 30 分钟给人工补救窗；④提供 `-NoRevive` 开关供窗口显式禁用 |
| **R2** | 拉起风暴 | 中 | F4 冷却闸 + `IgnoreNew` 单实例 + 计数在 `REVIVE_FAILED` 时保留但不重复执行（每轮至多 1 次拉起） |
| **R3a** | **H1 误报型**：正常退出被判 FAIL（10-30 三案、11-02 十九案共 **40+ 条**误报） | 高（误拉源头） | F1 引入 **`exitcode.log` 正常退出排除**：`ExitCode=0 (normal-exit)` ⇒ 不计数、不拉（这是本方案与"直接对 FAIL 计数"的关键差异） |
| **R3b** | **「自停未声明」型（2026-11-10 补记 · ⑳ 23:44 案实证）**：**他线在开回填窗/受控窗时自行 `Stop-Process` 停掉共享 daemon，但未写 `daemon_occupancy.json` 声明** ⇒ 该停不属于「正常退出」（无 `ExitCode=0 (normal-exit)` 记录）⇒ **F1 判其为真死并计数**，达 N 后自动拉起 ⇒ **与他线正在进行的写操作争 DuckDB 写锁**（破坏性远高于"拉起来空转"：可能打断回填/迁移、制造半成品或 WAL 异常） | **最高**（本方案最危险的误拉形态） | **①F3 占用闸对"未声明"天然失效**——必须补 **F11 旁证探测**（见 §3.2）；②F1 的 normal-exit 排除**覆盖不到**本形态（强停无 EXIT 正常码），需靠 F11 兜底；③铁律 19 补条款升级为**硬闸**：「停共享 daemon **前**必须写 `daemon_occupancy.json`，否则视为违规停服」；④**渐进上线**：首 24 小时为**观察模式**（`REVIVE_ARMED` 只记不拉），确认零误判后再启用实拉 |

> **R3b 实证（2026-11-10 08:59 取证）**：`daemon_heartbeat_alerts.log` L576-577 记 `2026-11-09 23:48:34` / `23:58:34` 连续 `[FAIL] H1: 未发现 daemon 进程（静默死亡）| status_pid=468`；而 `daemon_occupancy.json` 当时 owner=⑳、window `22:30–10:30`、operation=「forever 恢复常驻」，**未见任何"中途自停"记录**，`retroactive_log` 亦无 23:44 条目 ⇒ 属**典型自停未声明**。若守护已上线：23:48 → 23:58 → 00:08 三轮 FAIL 即达 N=3 ⇒ **00:08 前后就会自动拉起、正撞 ⑳ 的写窗**。此案证明 F11 不是可选加固，而是**上线前置条件**。
| **R4** | S4U 下 `schtasks /Run` 权限不足 | 中 | 实施前用 `whoami /priv` + 实测一次拉起；失败则降级为「只记 REVIVE_DENIED 并告警」，不静默 |
| **R5** | CalendarTrigger 与 BootTrigger 叠加导致开机时双启 | 低 | `MultipleInstancesPolicy=IgnoreNew` 已有单实例保护；F8 判据显式核 |
| **R6** | 复活掩盖根因（真死被自动拉起后不再追查） | 中 | F6 的 `REVIVED` 必须**同时**写一条 WARN 级告警入 `daemon_heartbeat_alerts.log`（`[REVIVED]` 前缀），令「自动恢复」仍可见、仍进入日报 |

## 五、验收要点

| 类别 | 项目 | 判据 |
|---|---|---|
| **功能验收** | F1–F10 逐点 | 见 §3.2 判据列，各点独立复跑 |
| **负向对照（≥4 组）** | ①正常退出 5 次 ⇒ 零拉起 ②计数=2 ⇒ 零拉起 ③占用窗内 ⇒ 零拉起 ④冷却窗内 ⇒ 二次不拉 | 每组「拉起调用计数」必须为 **0**（以 `daemon_revival.log` + 进程/任务调用记录双证） |
| **正向验收** | 真死（`ExitCode=1` / 无 EXIT 行）连 3 轮 ⇒ 60s 内 `REVIVED` | `daemon_status.json` `started_at` 更新 + 进程存活 + `LastTaskResult=0` |
| **回归验收** | H1/H2/H3 三态退出码对照 | 用固定 fixture 跑改前/改后 ⇒ `0/2/3` 完全一致（记录逐项对照表） |
| **场景验收** | 与窗口纪律联动 | 占用窗内窗口执行方停 daemon 且已写声明 ⇒ 全程零拉起（复跑窗口卡 W2 场景） |

## 六、质量判据

1. **纯增益可证**：既有 H1/H2/H3 判据、匹配式、`StaleFactor`、退出码 `0/2/3` **零变更**（`git diff` 只增不减该区），三态 fixture 对照一致；
2. **防回归设计结构性**：误拉的三重防线（F1 正常退出排除 / F3 占用闸 / F4 冷却闸）各自独立可测，且每道防线有专属负向对照；
3. **可回退**：①任务定义改动前导出 XML 备份（`docs/evidence/` 留档）；②`-NoRevive` 开关一键禁用复活（不改代码）；③`daemon_revival_state.json` 删除即归零；
4. **可观测**：`daemon_revival.log` 每决策一行结构化记录；`REVIVED` 同时入既有告警流（R6），日报口径沿用哨兵既有 grep；
5. **无新常驻进程**：复活由既有 Heartbeat 承载，不加 daemon；
6. **文档同批**：铁律 19 补条款 + runbook 更新与本件实施同 commit（禁止只改脚本漏文档）；
7. **F11 为上线前置**：R3b 实证表明无 F11 时该形态必然误拉 ⇒ F11 未实现并通过⑤组负向对照前，**不得开启实拉模式**（只能 observe）；
8. **渐进上线**：observe 模式须平稳运行 ≥24h 且零误判，方可切 active；切换本身与观察日志同批留档。

## 七、实施前置与协同（待②审裁定事项）

| # | 待裁/待协同 | 说明 |
|---|---|---|
| Q1 | **阈值 N**：默认 3（30 分钟）还是 2（20 分钟） | 我方倾向 **3**：H1 已证明误报花样多，宁慢勿误拉；且 11-02 停摆是 23 小时级，20/30 分钟差别无实质影响 |
| Q2 | **CalendarTrigger 时点**：每日 06:00 单点 vs 06:00 + 每 6 小时 双点 | 我方倾向 **06:00 + 每 6 小时保底**（覆盖"拉起后当天又死"的场景） |
| Q3 | **与节点1 的分工** | 建议：④出方案与测试组 → ②审 → **节点1 负责注册任务定义**（因其管计划任务面）→ ④负责脚本段实施与负向对照 → 联合验收 |
| Q4 | 是否同批把 H1 误报修复（`ExitCode=0 (normal-exit)` 判据）纳入本件 | 我方倾向**纳入**（R3 依赖它；若分两批，F1 需临时自带判据） |

> 本件为**方案**，未实施任何改动。②审通过 + 用户确认后方进 ③实施。
