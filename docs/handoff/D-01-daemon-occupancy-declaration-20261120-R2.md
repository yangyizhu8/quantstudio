# D-01 daemon 占用声明（D-02 前置 · 修订版 R2）

- **声明编号**：**D-01-OCC-20261120-R2**
- **形成时间**：**2026-11-20 10:56**
- **修订原因**：①执行会话由「节点11」更正为「**节点21**」；②新增**代码级死锁结论**与解锁路径；③精确时点核算
- **替代版本**：`D-01-OCC-20261120`（R1，`docs/handoff/D-01-daemon-occupancy-declaration-20261120.md`）

---

## 一、登记表（**本声明核心**）

| 项 | 值 |
|---|---|
| **执行会话** | **节点21** |
| **协调会话** | **节点4**（本线，只读观察 + 声明协调，**不执行 D-02**） |
| **占用对象** | `QS_DaemonForever` → `scripts/start_daemon_guarded.ps1` → `python -m quantstudio.pipeline.daemon --mode forever` |
| **执行窗口** | **`2026-11-20 12:20` ~ `12:35`**（Asia/Shanghai，占用 **15 分钟**） |
| **触发时点** | **`2026-11-20 12:30:00`**（距声明时刻 **94.5 分钟**） |
| **恢复截止时点** | **`2026-11-20 12:35:00`**（触发后 5 分钟内必须见 `START`） |
| **硬截止（else 升级）** | **`2026-11-20 14:00`**——若 `12:35` 未恢复，须立即升级总调度，**不得拖至次日** |
| **执行前置** | 🔴 **责任方签署**（见 §二） |

---

## 二、backtest 收尾责任方（**实测结论**）

### 2.1 flag 元数据

```json
{ "reason": "backtest", "at": "2026-11-14T14:50:49",
  "note": "等待已取消：停止请求已发出，采集将在任务边界退出", "owner_pid": 12140 }
```

| 项 | 实测 |
|---|---|
| `owner_pid` = **12140** | **❌ 已不存在**（`Get-Process -Id 12140` 无结果）⇒ **不可通过 PID 追责** |
| `note` 溯源 | **逐字匹配 `backtest_window.py:313`** 的 `WindowCancelled` 分支 → `L314 write_stop_flag("backtest", note)` |
| **语义定性** | **回测被用户取消**，非异常、非崩溃；`stop_request` 已发出，daemon 于任务边界正常退出（`exitcode` 末条 `ExitCode=0 (normal-exit)` 与此自洽） |
| **代码注释（L7）** | 「回测结束**不重启采集**：写 `backtest_stop.flag` 留痕，界面/终端据此提示"请手动启动"」 |
| **2026-10-28 用户裁定（`start_daemon_guarded.ps1:26-29`）** | 「恢复时点**由用户决定**」 |

### 2.2 责任方结论

| 项 | 结论 |
|---|---|
| **backtest 收尾责任方** | ⚠️ **未登记、不可考**——`owner_pid=12140` 已退出；无 backtest 运行登记件；`agent_workspace` 无对应 backtest 在途目录 |
| **授权恢复方（依裁定）** | 依 2026-10-28 裁定，恢复权在「**用户**」；**业务 owner 仍未指定**（Q1–Q4 自 `11-18` 无答复） |
| **本线声明** | **不代填、不推定、不代签** |
| 🔴 **硬阻塞** | **责任方签署 = D-02 的唯一前置**。**未签署则窗口作废**，`12:30` 将产生第 **35** 次 skip |

---

## 三、🔴 代码级死锁（**本声明最关键发现**）

### 3.1 死锁链（两处代码互锁）

| 环节 | 代码位置 | 行为 |
|---|---|---|
| **拦截** | `scripts/start_daemon_guarded.ps1` **L32-37** | `if (-not $Force -and (Test-Path $stopFlag0))` → 写 `skip auto-start` → **`exit 0`** |
| **清理** | `quantstudio/pipeline/daemon_lifecycle.py` **L592-598** | `run_forever()` **内**调 `clear_stop_flag()` |
| **死锁** | — | **要清 flag 须先进 `run_forever`；要进 `run_forever` 须先过 flag 检查** ⇒ **flag 永远不会被自动清除** |

### 3.2 实测证实

| 证据 | 读数 |
|---|---|
| `QS_DaemonForever` 动作原文 | `powershell.exe … -File "…\scripts\start_daemon_guarded.ps1"` |
| **是否含 `-Force`** | **False**（正则实测） |
| 后果 | 每轮 `12:30` / `18:30` / `00:30` / `06:00` / `06:30` **必然走 skip** |
| 实测累计 | **34 次 skip**（11-14=1、11-15=5、11-16=6、11-17=6、11-18=6、11-19=6、11-20=4） |

> **架构判断**：该设计在「回测因超时/取消而**未能正常退出 `__exit__`**」路径下会**自锁**——`L307-315` 的补写 flag 是为保留归因（C1 终审阻断项），但补写后无自动清除路径，形成**留痕与恢复互斥**。

### 3.3 三条解锁路径（**D-02 必须选其一并写明**）

| # | 路径 | 动作 | 语义 | 风险 |
|---|---|---|---|---|
| **① 删 flag 后等自然触发**（推荐） | `12:20` 删除 `data/backtest_stop.flag` → `12:30` 触发时 `Test-Path=false` → 跳过 L32-37 → 进 python → `L596` 再清一次（幂等） | **不手动启 daemon**，走既定排程 | **最低**：不碰 daemon 生命周期，仅删留痕文件 |
| **② `-Force` 手动拉起** | `start_daemon_guarded.ps1 -Force` | 显式覆盖 flag 检查 | **中**：`-Force` 是 2026-10-28 专为「应急直启」设计的开关，语义正当但属**手动启 daemon** |
| **③ GUI / CLI 手动启动** | 不经 guarded 脚本，直接进 `run_forever` | `L596` 自动清 flag | **中**：同样属**手动启 daemon**，且无 `-Force` 审计痕迹 |

> **本线倾向 ①**：不触及 daemon 生命周期、不需要 `-Force` 应急语义、删留痕文件是「用户决定恢复」的**最小充分动作**。
> **注**：路径 ①②③ **均需责任方授权**（依 2026-10-28 裁定「恢复时点由用户决定」）。

---

## 四、窗口冲突核查（实测 `2026-11-20 10:55:30`）

### 4.1 窗口内写入任务

| 时刻 | 任务 | 与 `12:20~12:35` 窗关系 |
|---|---|---|
| 每 10 分钟（`10:58:32` / `11:08:32` / … / `12:28:32`…） | **`QS_DaemonHeartbeat`** | **重叠 1–2 次**——但**仅写三份监视文件**（`daemon_revival_state.json` / `daemon_revival.log` / `daemon_heartbeat_alerts.log`），**属既有口径，不构成冲突** |
| **`12:30:00`** | **`QS_DaemonForever`** | **正是本声明窗口内的事件，非冲突** |
| `13:00` 之后 | — | 窗口外 |

**其余任务**：`12:20~12:35` 内 `QuantStudio\data` **无第三方写入源**（`QuantStudio-trading` 系已排除）。

### 4.2 其他 daemon 会话 / 进程

| 项 | 实测 | 判定 |
|---|---|---|
| **QS 采集 daemon 进程** | **0**（`quantstudio\.pipeline\.daemon` 正则，单/双形态均 0） | ✅ 无占用 |
| `daemon_status.json` | **不存在** | ✅ |
| `data/quantstudio.db.wal` / `.wal.shm` | **均不存在** | ✅ 无残留锁 |
| `data/quantstudio.db` | 66,640,687,104 B｜mtime **`11-14 18:17:56`**（未变） | ✅ |
| **其他 python 进程（13 个）** | 均**非** QS daemon（`zcode_mcp_wrapper` / `main.py harness` / `main.py loop` / `monitor.py` / `openviking` / `powercontext` / `watchdog_loop.py` / `pytest` 等） | ✅ **不触 QuantStudio 库** |
| `\QS-RuntimeWatchdog` | **`Disabled`** | ✅ |
| `\DockerDesktop_Watchdog` / `\OpenClaw-StalledSessionWatchdog` | `Disabled` | ✅ |
| `\QuestDB_Watchdog` / `\Trading_Tasks_Watchdog` / `\k12-wal-watchdog` / `\dsh-web-watchdog` | `Ready`（1–2 分钟级） | ⚠️ **各属其域，不写 `QuantStudio\data`**，不构成冲突 |

### 4.3 ⚠️ 待协调项

| # | 事项 | 状态 |
|---|---|---|
| 1 | **责任方签署** | 🔴 **空缺**（硬阻塞） |
| 2 | **节点21 回执确认执行人 + 选定解锁路径** | ⚠️ **未回执** |
| 3 | 他线（客户运维1 / 节点20）是否已在同窗声明 daemon 占用 | ⚠️ **本线无该信息**，须总调度按铁律 19「避让不接管」协调 |

---

## 五、⚠️ 业务缺口（**恢复的动力来源**）

`trade_calendar` 权威读数 + 库内计数（实测 `2026-11-20 09:45`）：

| 日期 | 星期 | `is_open` | `stock_minutes` | `etf_minutes` | `stock_daily` | `etf_daily` | `index_daily` |
|---|---|---|---|---|---|---|---|
| 11-13 | 五 | True | 1,353,261 | 531,164 | 5,571 | 2,181 | 31 |
| 11-14 | 六 | False | 0 | 0 | 0 | 0 | 0 |
| 11-15 | 日 | False | 0 | 0 | 0 | 0 | 0 |
| **11-16** | **一** | **True** | **0** 🔴 | **0** 🔴 | **0** 🔴 | **0** 🔴 | **0** 🔴 |
| **11-17** | **二** | **True** | **0** 🔴 | **0** 🔴 | **0** 🔴 | **0** 🔴 | **0** 🔴 |
| **11-18** | **三** | **True** | **0** 🔴 | **0** 🔴 | **0** 🔴 | **0** 🔴 | **0** 🔴 |
| **11-19** | **四** | **True** | **0** 🔴 | **0** 🔴 | **0** 🔴 | **0** 🔴 | **0** 🔴 |
| **11-20** | **五** | **True** | **0**（`12:30` 未到） | 同 | 同 | 同 | 同 |

⇒ **11-16~11-19 = 4 个完整交易日，应到点已全部过去，数据仍为 0** ⇒ **实际业务缺口**（非预期性风险）。
⇒ **若 `12:30` 再次 skip，缺口增至 5 个交易日**。

---

## 六、恢复判据（可机检，逐项）

| # | 判据 | 命令/读法 |
|---|---|---|
| 1 | `flag` **不存在** | `Test-Path data/backtest_stop.flag` → `False` |
| 2 | `exitcode` 出现**新 `START`**（`pid` ≠ 39200） | `Get-Content data/logs/daemon_exitcode.log -Tail 1` |
| 3 | `daemon_status.json` **存在**且 `pid` 与进程实测一致 | 读 `pid` → `Get-Process -Id` 验证 |
| 4 | `daemon.log` mtime **晚于** `2026-11-14 18:18:23` | `(Get-Item data/logs/daemon.log).LastWriteTime` |
| 5 | `skip` 11-20 桶**不再增长** | `skip` 11-20 桶 = **4**（声明时基线） |
| 6 | 首轮数据非 0 | `stock_minutes` 11-20 行数由 `0` 转正（**须待采集完成，不预填**） |

**观察期**：恢复后 **30 分钟**，监控 `exitcode` 无异常 `EXIT`。

---

## 七、声明正文（**待签署生效**）

```text
【daemon 占用声明 · D-01-OCC-20261120-R2】
执行会话：节点21
协调会话：节点4
占用对象：QS_DaemonForever → start_daemon_guarded.ps1 → python -m quantstudio.pipeline.daemon --mode forever
执行窗口：2026-11-20 12:20 ~ 12:35（Asia/Shanghai，15 分钟）
触发时点：2026-11-20 12:30:00
恢复截止：2026-11-20 12:35:00（触发后 5 分钟内须见新 START）
硬截止：2026-11-20 14:00（未恢复须立即升级总调度，不得拖至次日）
解锁路径：① 12:20 删除 data/backtest_stop.flag 后等 12:30 自然触发（本线推荐，不手动启 daemon）
          ② start_daemon_guarded.ps1 -Force；③ GUI/CLI 手动启动
          三路径均需责任方授权（依 2026-10-28 裁定「恢复时点由用户决定」）
责任方：____________________（待签署）
backtest 收尾责任方：未登记不可考（owner_pid=12140 已退出；note 逐字溯源至
                    backtest_window.py:313 WindowCancelled 分支）
恢复判据：flag 不存在 + exitcode 新 START + status.json 存在且 pid 一致 + daemon.log mtime 更新
          + skip 11-20 桶停在 4 + 首轮数据非 0
占用公告对象：客户运维1 / 节点20 / 各 daemon 相关会话
声明方：节点4（只读观察线，不执行 D-02、不删 flag、不启 daemon）
```

---

## 八、本线边界（未越权）

| 项 | 状态 |
|---|---|
| 删除 / 归档 `flag` | ❌ **未做**（依指示「占用声明完成前，不删除或归档 flag」） |
| 启动 / 接管 daemon | ❌ **未做**（依指示「不启动 daemon」） |
| 改排程 / 改配置 / 写库 | ❌ **未做** |
| 代签业务责任 | ❌ **未做** |
| **已做** | 只读核查（`trade_calendar` + 五表计数 + 事件日志 + 进程扫描 + 文件 mtime + **源码逐行定位 L1-70/L280-335/L580-609/L18-57**）+ 本声明件 |

## 九、放行判据

| # | 条件 | 状态 |
|---|---|---|
| 1 | 声明件形成（含执行会话/协调会话/窗口/恢复截止） | ✅ **已完成**（本件） |
| 2 | **责任方签署** | 🔴 **未满足** |
| 3 | **节点21 回执确认 + 选定解锁路径** | ⚠️ **未满足** |
| 4 | 总调度确认无他线占用冲突 | ⚠️ **未满足** |

⇒ **结论：声明已完整形成，但 §九 第 2–4 项未满足 ⇒ 尚不具备放行节点21 进 D-02 的条件。**
