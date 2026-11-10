# 守护链复活能力 · 实施证据（③ 实施轮）

- **日期**：2026-11-10｜**实施方**：客户专属总运维会话（④节点）｜**方案**：`docs/daemon-guard-revival-design-20261110.md` **v1.1**（②审通过）
- **判型**：新增能力型（铁律 18）——既有 H1/H2/H3 判据、匹配式、`StaleFactor`、退出码 `0/2/3/4` **一字未改**
- **分工（Q3）**：④ 负责脚本段 + 负向对照（本件）；**节点1 负责 `QS_DaemonForever` 任务定义注册（F8）**，本件未触任务定义
- **R1 开工留痕**：`知识反哺：[无命中] knowledge/**（daemon/计划任务/守护链族）+ knowleage_base（运维域 MOC），2026-11-10`（同方案件 §首部）

## 一、改动清单

| # | 文件 | 改动 | 行数变化 |
|---|---|---|---|
| L1 | `scripts/check_daemon_alive.ps1` | **新增**复活能力段（F1–F7/F9–F11 + 观察模式）；新增 4 个参数（`-ReviveMode`/`-ReviveThreshold`/`-ReviveCooldownMinutes`/`-NoRevive`）+ 1 个测试参数（`-SimulateVerdict`）；`$isSimulated` 定义提前到复活段之前 | 287 → **574 行**（+287） |
| L2 | `.gitignore` | 新增 `data/daemon_revival_state.json` 忽略规则（运行态，与 `daemon_occupancy.json` 同族） | +3 |
| L3 | `docs/evidence/revive_negative_tests.ps1` | **新增** 6 组对照测试（负向①–⑤ + observe⑥） | 新文件 |
| L4 | `docs/evidence/revive_probe_tests.ps1` | **新增** F11 探②③④逐探单测 | 新文件 |
| — | `QS_DaemonForever` 任务定义 | **未改**（Q3 归节点1） | — |

## 二、F9 既有判据零漂移（三态 A/B 对照）

**方法**：`git show HEAD:scripts/check_daemon_alive.ps1` 取出改前版本 → 两版**均显式传同一 `-RepoRoot`**（消除 `$PSScriptRoot\..` 解析差异，这是首轮对照的假阳性来源，已排除）→ 比对退出码与 verdict 标签。

| 态 | 构造 | 旧版 | 新版 | 判定 |
|---|---|---|---|---|
| 态1 FAIL | `-SimulateStatusPid 999999` | rc=**3** / FAIL | rc=**3** / FAIL | ✅ 一致（文本亦逐字一致） |
| 态2 WARN | `-StaleFactor 0.0001` | rc=**2** / WARN | rc=**2** / WARN | ✅ 一致（文本仅「超龄 2s vs 3s」时间流逝差，非判据差异） |
| 态3 OK | 默认参数 | rc=**0** / OK | rc=**0** / OK | ✅ 一致 |

**⇒ F9 PASS：既有告警行为零漂移。**

## 三、负向对照 6/6 PASS（拉起调用计数 = 0）

| 组 | 构造 | gate（实得） | action | 判定 |
|---|---|---|---|---|
| ① 正常退出 | `exitcode.log` 末条 `ExitCode=0 (normal-exit)`，预置 consecutive=5 | **skip-normal-exit** | NONE | ✅ |
| ② 计数不足 | 预置 0（+1=1 < N=3） | **skip-threshold(1/3)** | NONE | ✅ |
| ③ 占用窗 | 预置 2 → 达 3；`daemon_occupancy.json` occupied=true 且窗口覆盖 now | **skip-occupancy** | NONE | ✅ 日志含 owner 与窗口原文 |
| ④ 冷却期 | 预置 2 → 达 3；`last_revive_at` = 5 分钟前（< 30min） | **skip-cooldown** | NONE | ✅ 日志含「已 5min < 30 min」 |
| ⑤ 自停未声明（探①） | 预置 2 → 达 3；造活跃 WAL（mtime=now） | **skip-suspected_activity** | NONE | ✅ 日志含「WAL活跃」 |
| ⑥ 全闸通过 + observe | 预置 2 → 达 3；无占用/无冷却/无 WAL/db/lock | **all-pass** | **REVIVE_ARMED** | ✅ **observe 只记不拉** |

**关键读数**：6 组全部产生 `daemon_revival.log` 一行（F10 留痕完整）；**`action` 全为 NONE 或 REVIVE_ARMED，无任何 `REVIVE_CALLED` ⇒ 拉起调用计数 = 0**。

## 四、F11 四探逐探单测（3/3 PASS + 探① 见组⑤）

| 探 | 构造 | 命中读数 |
|---|---|---|
| 探① WAL 活跃 | 造 `quantstudio.db.wal` mtime=now | ✅ 「WAL活跃(mtime 0.28s前, 0MB)」（组⑤） |
| 探② 主库被独占 | 本会话持 `ReadWrite/None` 句柄，子进程打开被拒 | ✅ 「主库被独占(不可 ReadWrite/None 打开)」 |
| 探③ 自停痕迹 | `daemon.log` 新鲜且含「收到停止请求」，exitcode 无 normal-exit | ✅ 「daemon.log 近30min有自停痕迹但 exitcode 无 normal-exit」 |
| 探④ 锁文件活跃 | 造 `data\.daemon.lock` mtime=now | ✅ 「.daemon.lock 近30min活跃(0min)」 |

**⇒ 四探各自独立能拦，R3b 防御闭环成立。**

## 五、实施中暴露并修正的三处缺陷（如实申报）

| # | 缺陷 | 发现方式 | 修正 |
|---|---|---|---|
| 1 | **`edit` 写入去掉 UTF-8 BOM ⇒ PS 5.1 按 GBK 读中文 ⇒ 37 处语法错误**（报错含 `鏃ュ織`乱码） | 插入后首次语法检查立即暴露 | 用 `[System.IO.File]::WriteAllText(..., UTF8Encoding($true))` 补 BOM；**并固化为操作纪律：本仓 `.ps1` 每次 edit 后必补 BOM 再验语法**（与 check_wal.ps1 同族坑） |
| 2 | `$isSimulated` 被复活段引用但其定义在文件后部（此时为 `$null`） | 审阅时发现 | 定义提前至复活段之前，原处改为注释说明 |
| 3 | **`Write-ReviveLog` 嵌在「四探全过」分支内 ⇒ 5 类 SKIP 决策无痕**（违反 F10） | **6 组负向对照首轮全部 `(no-log)`** | 移到评估末尾统一调用；重跑后 6/6 均有日志 |

> 缺陷 3 是负向对照的**直接产出**——若只测"能拉起"不测"该不拉时不拉且留痕"，此缺陷会带到生产。

## 六、当前生效态（重要边界）

| 项 | 状态 |
|---|---|
| 脚本能力 | 已具备（F1–F11 + observe/active 双模式） |
| **默认模式** | **`observe`**（`-ReviveMode` 缺省值）——只记 `REVIVE_ARMED`，**不实际拉起** |
| 切换 `active` 条件 | ①5 组负向对照全过（**本件已完成**）②observe 平稳 ≥24h 零误判（**待观察**） |
| F8 任务定义 | **未注册**（归节点1）；在节点1 完成前，即使 `active` 也无法拉起（`schtasks /Run /TN QS_DaemonForever` 现存的 Boot-only 任务仍可被调用一次） |
| 生产影响 | 今晚 20:30 哨兵将首次带复活段运行（`-Quiet` 调用，默认 observe）⇒ 只写 `daemon_revival.log` 与状态文件，**行为与改前一致**（H1/H2/H3 与退出码零漂移已证） |

## 七、验收对照（方案 §五）

| 验收项 | 判据 | 结果 |
|---|---|---|
| 功能验收 F1–F11 | 各点独立复跑 | ✅ ①–⑥ + 探①–④ 全覆盖 |
| 负向对照 ≥5 组 | 拉起调用计数必须为 0 | ✅ **6/6，计数 0** |
| 正向验收（真死 ⇒ 60s 内 REVIVED） | 需 `active` 模式 + 真死场景 | ⏳ **未做**（须等 observe 观察期 + 节点1 注册后，在生产窗口外构造） |
| 回归验收（三态退出码） | 改前/改后一致 | ✅ F9 PASS |
| 场景验收（窗口内停 daemon 已声明 ⇒ 零拉起） | 复跑窗口卡 W2 场景 | ⏳ 待窗口（组③已用合成 occupancy 覆盖同一逻辑） |

**如实申报**：正向验收（REVIVED）与真实窗口场景验收**未执行**——前者需 `active` 且要真停一次生产 daemon（触 19 号铁律，须窗口授权）；本件不冒充完成。

## 八、边界

- 未改 `QS_DaemonForever` / `QS_DaemonHeartbeat` 任务定义（归节点1，Q3）；
- 未改 daemon 本体、未改任何采集/QFQ/schema 逻辑；
- 所有测试在 `%TEMP%\revive_test\` / `revive_probe_test\` 隔离 RepoRoot 内，**未触碰生产 data/**（生产 `daemon_revival_state.json` 与 `daemon_revival.log` 至今不存在）；
- 未启停任何 daemon、未推送。
