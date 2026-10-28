# W-2 族 ②③ 合并方案件：stderr/ExitCode/faulthandler 三件套 · 告警推送通道 · status 陈旧自愈

**日期**：2026-10-26 ｜ **出件**：主仓 dev 会话 ｜ **状态**：**②③ 方案，待 ②审**（未实施）
**前置**：①外部心跳巡检已实施并上线（`8db6bb9` / `d3eea01` / `3667861`，任务 `QS_DaemonHeartbeat` 10min 自动轮在载）
**合并依据**：用户 2026-10-26 裁定「②③ 合并一次 daemon 窗口」（同域，共享一次受控窗与一次回归）
**实证基线**：**daemon 五死**（10-22 14476 / 10-25 52232 / 10-26 01:01 34820 / 10-26 09:55 2232 / 10-26 15:28 34232）

---

## 知识反哺检索留痕

知识反哺：[无命中] `knowledge/**`（族 `qd` / `pd`；domain D1–D5）+ 通用库 `knowleage_base` 对应域 MOC，2026-10-26
**检索面说明**：两族主题为 QMT 转换 / PTrade 部署对齐，检索词「daemon 静默死亡 / 进程取证 / ExitCode / faulthandler / status 陈旧 / 告警去重」无条目。本件结论均来自**五死实测与代码实读**。

---

## 一、①已落地部分（三件套中的两件半已在 34232 上生效）

| 件 | 实现 | 实测结果（第五死 15:28:36） |
|---|---|---|
| **stderr 落盘** | `start_daemon_guarded.ps1` → `-RedirectStandardError data\logs\daemon_stderr.log` | ✅ **有效**：34232 生命期内累计 **14,744 行**、含 **10 个 `Traceback`** —— 证明捕获链路真实工作；但**无致命栈**（10 个 traceback 全为任务级 `MCPExportBudgetError`，被 task 层 catch，非进程死因） |
| **faulthandler** | 同脚本注入 `$env:PYTHONFAULTHANDLER='1'` | ⚠️ **未能验证**：第五死未产生 native 崩溃（无 WER、stderr 无 `Fatal Python error`）⇒ 该项在「非 native crash」死亡上天然沉默，**保留待真 native 场景** |
| **ExitCode 记录** | 同脚本 `Start-Process -PassThru` → `WaitForExit()` → 落 `daemon_exitcode.log` | 🔴 **失效（本轮自纠）**：`exitcode.log` **仅 1 行 START、无 EXIT 行**；核查 `start_daemon_guarded` 进程数 = **0** ⇒ **wrapper 与 daemon 同时消失**，`WaitForExit` 从未返回 |

### 1.1 ExitCode 件失效的根因与修法（本件必改项）

**现象**：wrapper 进程与 daemon 进程在 15:28:36 同时不存在 ⇒ 无 EXIT 行。
**两种可能**（本件不定谳，但两种都要有对策）：

| 可能 | 机制 | 对策 |
|---|---|---|
| **A. wrapper 被外部终止** | wrapper 以后台 job 方式启动，宿主/父 shell 回收时整个进程树被终止 | wrapper 改为**独立常驻进程**（`Start-Process -WindowStyle Hidden` 脱离 job）+ **看护心跳**：wrapper 每 60s 往 `daemon_guard_heartbeat.log` 写一行（wrapper 若静默也成可检测事件） |
| **B. daemon 先死、wrapper 随即被杀** | 同 A，只是顺序差异 | 同上（心跳能区分：EXIT 行缺失 + 心跳中断 = wrapper 被杀；EXIT 行存在 = 正常记录） |

**修法（本件定稿）**：wrapper 增加
1. **独立启动**：由计划任务或 `Start-Process` 脱离调用者 job 启动（不再作为调用方子进程）；
2. **wrapper 自身心跳**：每 60s 追加 `{ts} GUARD pid=<wrapper_pid> daemon_pid=<pid> alive=<bool>`；
3. **双路 ExitCode 落盘**：`WaitForExit` 正常路径 + **兜底路径**——若 `WaitForExit` 之后才发现 daemon 已不在（重启后补记 `ExitCode=UNKNOWN(lost)` 并标明「wrapper 亦被终止」）；
4. **重启后对账**：daemon 启动时读 `daemon_exitcode.log` 末行，若「有 START 无 EXIT」则把该 pid 记为 `EXIT=UNKNOWN` 并在启动日志 WARNING 一行——**让缺失本身成为可读事件**，而不是静默断链。

> ①～④ 的目的不是"猜出死因"，而是**让取证件自己也不会静默失效**——这是第五死给出的最重要教训：探针需要探针。

---

## 二、②告警推送通道（R-3 型推送 + 30min 去重）

### 2.1 实证依据（本次「无人即时知晓」）

第五死 15:28:36 发生后，巡检 `QS_DaemonHeartbeat` 在下一轮（≤10min）即写出 FAIL 告警到 `data\logs\daemon_heartbeat_alerts.log`——**但全程无任何推送**。第三者（用户）是**通过我主动巡检才发现**，不是被通知。
⇒ 通道缺口坐实：**发现能力已具备，触达能力为零**。

### 2.2 设计（R-3 型 + 30min 去重）

| 项 | 设计 |
|---|---|
| **触发** | 巡检判级 `FAIL` 或 `WARN`（`OK` 不推，保持静默设计） |
| **通道** | 复用项目既有 `feishu_webhook.ps1`（`trading-battle-back\scripts\feishu_webhook.ps1` 已存在，读 env/.env 的 `FEISHU_WEBHOOK`；`scripts\tasks_watchdog.ps1` L31-40 已有调用范式可参考）；webhook 缺失时**降级为仅落盘 + 明确 WARN**（不失败） |
| **R-3 型** | 消息体三段固定：**①判级与原因**（含判据名 H1/H2/H3）②**归因提示**（「宿主重启所致」/「daemon 自身异常退出」/「疑似假死」）③**取证指针**（stderr 路径 + exitcode 路径 + 告警日志路径），让收信人一条消息内知道下一步去哪看 |
| **30min 去重** | 同判级 + 同归因在 30min 窗口内只推一次；去重键 = `verdict|attribution`，状态存 `data\logs\heartbeat_notify_state.json`（`{key: last_push_ts}`）；**判级变化**（FAIL→OK / OK→FAIL）**立即推送并重置窗口**，确保恢复也被知晓 |
| **静默恢复可选** | 建议：FAIL→OK 时推一条「已恢复」——否则收信人只知道死了、不知道好了 |

### 2.3 改动范围

| # | 文件 | 性质 |
|---|---|---|
| 1 | `scripts/check_daemon_alive.ps1` | **改**：非 OK 分支增加推送调用 + 去重状态读写（现有 `-Webhook` 参数已预留，本轮补默认接线与去重） |
| 2 | `data\logs\heartbeat_notify_state.json` | **新增**（运行时状态，不入库） |
| 3 | `scripts\feishu_webhook.ps1` | **复用不改**（若不存在则新建最小实现） |

---

## 三、③ status 陈旧自愈评估

### 3.1 实测：启动时自愈**已生效**，运行中死亡**仍无自愈**

| 场景 | 实测证据 | 结论 |
|---|---|---|
| **启动时自愈** | 34232 启动日志：`[DaemonLifecycle] 清理陈旧 status 文件（原进程已退出）` | ✅ **已生效**——启动路径会清掉上一个死人留下的 status |
| **运行中死亡** | 第五死后 `daemon_status.json` 仍写 `pid=34232 status=running`，直到下一次拉起才被清理 | 🔴 **无自愈能力**（R-2 语义即"需新进程触发才清理" ⇒ 永不清理的死角，与既有登记一致） |

⇒ **结论：③ 不是"修 status 自愈"，而是"承认运行期无自愈、把真值来源交给外部巡检"**。当前 `check_daemon_alive.ps1` 的 H2 判据（status pid 必须 «活着且命令行匹配»）**已经覆盖**这一死角——它正是靠"pid 不存在"识破陈旧的。

### 3.2 因此 ③ 的处置：**不新增 daemon 侧改动**

理由（避免为已解决的问题再动 daemon 代码）：
1. 运行期陈旧 status **无法**由 daemon 自身清理（它已经死了，这是定义）；
2. 唯一可能的"自愈"是**外部**写一个 `status: dead` 标记 —— 但那是**消费者侧责任**，且会与 daemon 写入产生竞态；
3. H2 判据已给出正确答案（`h2_consistent=false`）。

⇒ ③ 收敛为**评估结论**而非代码改动：
- **建议①**：巡检在 FAIL 时**额外写一行 `data\logs\daemon_status_stale.json`**（记录「所读 status 的 pid / 该 pid 不存在 / 结论 stale」），供后续任何读 status 的消费者识别（纯新增文件，不改 status 本体，零竞态）；
- **建议②**：在 `docs/` 记录**消费者契约**——「任何人不得以 `daemon_status.json` 单独判活，必须校验 pid 存活」。

---

## 四、④ 根因取证路径收口

### 4.1 已排除项（九排除 + OOM）

| 层 | 排除项 | 依据 |
|---|---|---|
| 系统 | 宿主重启（10-22/10-26 型无 1074/6006/41）、**python 崩溃（无 WER 1000）**、WU 维护窗（ActiveHours 09–03）、虚拟内存耗尽（无 1003） | 事件日志全量核查 |
| 进程 | 5 个 watchdog 全排除（QS-Runtime 只碰 paper-runner；Trading_Tasks 只记快照+重挂；AgentQuant 只匹配 main.py harness/loop；QuestDB_Watchdog 只杀 questdb/java；Trading_Main_Guard 只匹配 main.py） | 逐脚本读匹配式与动作 |
| 自身 | 日志轮转（`when="midnight"` ⇒ 00:00 非死点） | 代码实证 |
| 旁证 | xtquant `MSVCP140.dll` 0xc0000005 风暴（15min 周期 7 次）**与五死非因果**（前 5 次 daemon 均存活；且该链已退役） | 时间线与存活交叉验证 |

### 4.2 第五死新增证据（本件核心增量）

| 观察 | 读数 | 含义 |
|---|---|---|
| **无 WER** | 15:20–15:45 事件 **0 条** | 非 native crash、非系统级事件 |
| **无致命 traceback** | stderr 末行 15:28:36 为正常 `export_cache 命中` | **非 Python 异常退出**（否则必留栈） |
| **wrapper 同时消失** | `start_daemon_guarded` 进程数 = **0** | ⇒ **进程树被整体终止**的高度指示（外部终止 / 父进程连带）——这是五死以来**第一个指向"外部终止"的正面证据** |
| **WAL** | 收敛后 0MB → 第五死时 **0MB** | 五死中 3 例在写库期，但本次 WAL 未累积 ⇒ **WAL 峰值不是死因**（该假设本轮否定） |

### 4.3 取证路径收口结论

```
五死（10-22 / 10-25 / 01:01 / 09:55 / 15:28）
 ├─ 10-25 = 宿主 3 次重启（已定谳，非缺陷）
 ├─ 其余四死 = 「无声消失」：无 WER / 无致命栈 / 无重启
 │    └─ 第五死新增：wrapper 同消失 ⇒ 指向「外部终止」
 └─ 系统级九排除 + OOM 虚拟层排除 + WAL 排除 ⇒ **已无系统级证据可查**
      ⇒ 唯一剩余路径 = 三件套（stderr / ExitCode / faulthandler）
      ⇒ 而 ExitCode 件本轮失效 ⇒ **必须先修取证件本身**（§1.1）
```

**⇒ 本件的真正结论：在取证件自身不可靠的前提下，继续追根因是无效的。**
**⇒ 因此 W-2② 的第一优先级不是"多拿证据"，而是"让取证件不会静默失效"**（§1.1 四项），其次才是推送通道（§2）。

---

## 五、ws_asfund 无锚退化全量：生产轮耗时冲击评估（本件附评估，不并案）

**现象**（用户 2026-10-26 报）：`ws_asfund` 无锚点 ⇒ 退化「建基线」全量 **1029 天**，拖住采集轮。

**机理**（代码实证 `daemon.py` L1056-1072）：
```
无锚点（首次/升级后）⇒「增量」静默退化为全量：原实现照常全区间拉取
处置：显式 WARNING +「建基线」语义 + 天数/量级暴露
```
⇒ 即：**新表首接必然全量**，且该全量**单表串行**吃掉采集轮时间。

**耗时冲击量化（本机实测参照）**：
| 参照 | 读数 | 推算 |
|---|---|---|
| `stock_minutes` 单批 50,000 行写入 | 约 70–110s/批（06:37–09:55 批间观测） | — |
| 34232 生命期 3.4h（12:03–15:28）内完成 | export_cache 从 2026-01 走到 2026-02 命中 | **历史深度按「月」推进**，非按日 |
| `ws_asfund` 1029 天 | — | 若按日分片：1029 次 export+还原+写；按实测 ~2–5s/分片 ⇒ **34–86 分钟单表独占**（估） |

**结论与建议（不并案，仅登记评估）**：
1. **冲击成立且显著**：单表 30–90min 级，足以把 06:00 批推过办公时间，并与 04:00 回补、19:00 批叠加；
2. **是否需要「无锚新表拉取窗上限」** ⇒ **建议另案**（本件不并），但**给出判据**：
   - 触发线：单表无锚全量 `天数 > 365` 或 `预估时长 > 30min`；
   - 手段（三选，另案定）：① 无锚全量**分夜（或分轮）续拉**（记 cursor 到 `daemon_run_state`）② 无锚全量**降级为近 N 年**（业务确认）③ 无锚新表**移出 06:00 主轮**，独立低峰任务；
3. **本件只做登记**，不改代码（避免与 ②③ 窗口混办）。

---

## 六、实施范围与验收（②③ 合并窗）

### 6.1 改动清单（精确）

| # | 文件 | 改动 | 必要 |
|---|---|---|---|
| 1 | `scripts/start_daemon_guarded.ps1` | §1.1 四项（独立启动 / wrapper 心跳 / 双路 ExitCode / 重启对账） | **必改**（取证件自愈） |
| 2 | `scripts/check_daemon_alive.ps1` | 推送接线 + 30min 去重 + 恢复通知 + FAIL 时写 `daemon_status_stale.json` | **必改** |
| 3 | `data\logs\heartbeat_notify_state.json` | 运行时状态（不入库） | 新增 |
| 4 | `quantstudio/pipeline/**` | **零改动**（③ 收敛为评估，不动 daemon 代码） | — |

**⇒ 关键：②③ 合并不再需要"一次 daemon 改动窗口"**——因 ③ 收敛为评估、② 全在脚本层 ⇒ **本件可在任意时间实施，无需停 daemon**。这是相对原计划的**范围缩小**（原判"②③ 触 daemon 代码"，实测 ③ 无需改代码）。

### 6.2 验收标准

| # | 项 | 判据 |
|---|---|---|
| 1 | 取证件自愈 | 人为 kill wrapper（不动 daemon）⇒ 心跳日志中断可查、ExitCode 记为 `UNKNOWN(lost)` |
| 2 | ExitCode 正常路径 | 正常停 daemon（声明窗内）⇒ `exitcode.log` 出 `EXIT` 行，ExitCode=0 或对应信号值 |
| 3 | 推送触达 | 构造 FAIL（停 daemon）⇒ 30min 内收到飞书消息，含判级/归因/取证指针三段 |
| 4 | 去重 | 连续 3 轮 FAIL（30min 内）⇒ **只推 1 条** |
| 5 | 恢复通知 | 拉起后判 OK ⇒ 推「已恢复」1 条并重置窗口 |
| 6 | 零回归 | daemon 本体零改动 ⇒ `py_compile` 通过；巡检 `-AsJson` 字段不变（仅增 stale 文件） |

### 6.3 回退条件

脚本级改动 ⇒ `git revert` 单个 commit 即完全回退；无 daemon 代码、无 schema、无数据写入变更；运行时状态文件可删。

---

## 七、呈 ②审 的裁定点

1. **§1.1 的"wrapper 独立进程 + 自身心跳"是否接受**（代价：多一个常驻 powershell 进程，约 40–60MB）；
2. **§3.2 ③ 收敛为「不改 daemon 代码」是否认可**（我的判断：运行期 status 陈旧无法自愈，外置 stale 标记 + 消费者契约更正确）；
3. **§5 无锚全量另案的上限判据（365 天 / 30min）是否采纳**（本件不实施）；
4. **§6.1 范围缩小（无需停 daemon 窗口）是否认可** —— 若认可，本件可于明日直接实施，不必等"合并窗"。

---

*本件为 ②③ 方案，未实施。①（心跳巡检）已上线运行。第五死实证已并入。*
