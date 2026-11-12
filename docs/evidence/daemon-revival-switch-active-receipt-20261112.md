# 守护链复活能力 · 切 active 切换回执（2026-11-12 20:50 门守执行）

> 依据：用户 2026-11-11 裁定〔甲〕（切 active 照准，收到 F8 后执行）+ 2026-11-12 15:48 转达节点1 F8 确认
> 执行点：门守 `schedule-bdd8d4c8-c191-4a99-8bbd-745e5cd11ea5`（2026-11-12 20:50 CST）

## 一、四查（分支判定依据）

| # | 查项 | 读数 | 判定 |
|---|---|---|---|
| 1 | `QS_DaemonForever` 触发器 | **3 个**：`BootTrigger`(PT2M) + `CalendarTrigger`(每日 06:00) + `CalendarTrigger`(每 6h，`PT6H`/`P1D`) | ✅ 双触发齐备 |
| 2 | `QS_DaemonHeartbeat` 参数 | 改前**不含** `-ReviveMode` | ✅ 待切 |
| 3 | F8 回执件（文件形态） | **0 件** | 以**双证**代：① 用户 15:48 转达节点1 回执（三触发/Ready/NextRun/LastResult=0）② 我方 04:02 独立实测复核逐项一致 |
| 4 | 占用声明 | `occupied=false`，`owner=会话节点⑳`，`operation=RELEASED` | ✅ 无占用 |

**⇒ 分支 A**（F8 已落地 且 无占用）

## 二、参数切换（改前 / 改后逐字）

### 改前（原文，143 字符）

```
Command   = C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
Arguments = -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "D:\miniQMT策略实盘\QuantStudio\scripts\check_daemon_alive.ps1" -Quiet -IntervalMinutes 10
```

### 改后（当前生效，162 字符）

```
Command   = C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
Arguments = -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "D:\miniQMT策略实盘\QuantStudio\scripts\check_daemon_alive.ps1" -Quiet -IntervalMinutes 10 -ReviveMode active
```

**差异**：末尾追加 ` -ReviveMode active`（19 字符），**其余一字未动**；`Command` 未变。

### 未改动项（逐项核）

| 项 | 读数 | 状态 |
|---|---|---|
| `Interval` | `PT10M` | ✅ 保持 |
| `Repetition` 结构 | 保持 | ✅ |
| `ExecutionTimeLimit` | `PT5M` | ✅ |
| `LogonType` | `S4U` | ✅ |
| `UserId` | `S-1-5-21-...-500` | ✅ |
| `QS_DaemonForever` | 全程**只读**，未动 | ✅ |

## 三、⚠️ 执行中发生的事故与修复（如实申报）

### 3.1 事故一：`schtasks /change /tr` 经 PowerShell 传参**丢失内部双引号**

- **现象**：`schtasks /change` 报 `SUCCESS`（exit 0），但写后核发现 Arguments 变为
  `... -File D:\miniQMT策略实盘\QuantStudio\scripts\check_daemon_alive.ps1 ...`（**引号消失**）
- **后果**：路径含中文空格（`D:\miniQMT策略实盘`）⇒ PowerShell 解析 `-File` 时会截断路径 ⇒ **任务必然失败**
- **根因**：PS 把 `$newTr` 作为单一参数传给 `schtasks` 时，schtasks 再按自身规则重组命令行，**内层引号被剥离**

### 3.2 事故二：回退点 XML **带 BOM** ⇒ `schtasks /create /xml` 拒绝

- **现象**：回滚时报 `ERROR: The task XML is malformed` + `(1,2)::错误: 不正确的文档语法`
- **根因**：导出用 `UTF8Encoding($true)`（带 BOM），`schtasks /create /xml` **不接受 BOM**
- **性质**：**回退点"建了但用不了"** —— 违反「回退点建后必立验可用」的实质（10:51 预演只验了"XML 可解析"，**未验"目标工具能接受"**）

### 3.3 事故三：`<Enabled>true</Enabled>` 判据假失败

- 该任务 XML 的 `<Settings>` 段**不含** `<Enabled>` 元素（启用态由 `Status=Ready` 体现），
  我的判据返回 False 属**假信号**，已剔除，改以 `Status` + `Next Run Time` 判定。

### 3.4 修复动作（三步，全部已实证）

| 步 | 动作 | 结果 |
|---|---|---|
| 1 | 改用 **`Set-ScheduledTask` + `New-ScheduledTaskAction`**（不经 shell 解析，字符串原样写入） | ✅ Arguments 恢复**带引号**且与目标**逐字相同** |
| 2 | 重建回退点：① `daemon-heartbeat-task-backup-20261112.xml`（原文态，**已去 BOM**）② `daemon-heartbeat-task-backup-20261112-active.xml`（当前态，无 BOM） | ✅ 首 3 字节 `60,63,120`（`<?x`），无 BOM |
| 3 | **回退点可用性实测**：`schtasks /create /tn QS_ProbeXmlTest /xml <备份> /f` | ✅ `SUCCESS` → 立即 `schtasks /delete` 清理（无残留） |

### 3.5 切换后任务实测可跑

```
schtasks /run /tn "QS_DaemonHeartbeat"  → SUCCESS
Last Run Time = 2026-11-12 20:51:51   Last Result = 0   Status = Ready
```

⇒ **参数正确性以实跑验证**（不只是静态比对）。

## 四、六项写后核（最终态）

| # | 项 | 判定 |
|---|---|---|
| ① | 含 `-ReviveMode active` 结尾 | ✅ |
| ② | 去掉追加后与原文**逐字相同** | ✅ |
| ③ | `Command` 未变（`powershell.exe` 绝对路径） | ✅ |
| ④ | `Interval PT10M` 保持 | ✅ |
| ⑤ | 任务启用可跑（`Status=Ready` + `LastResult=0` 实跑） | ✅ |
| ⑥ | `QS_DaemonForever` 未被改动（3 触发器原样） | ✅ |

## 五、回退点（两份，均已实证可用）

| 文件 | 内容 | BOM | 用途 |
|---|---|---|---|
| `docs/evidence/daemon-heartbeat-task-backup-20261112.xml` | **原文态**（无 `-ReviveMode`） | 无 | 一键回 observe |
| `docs/evidence/daemon-heartbeat-task-backup-20261112-active.xml` | 当前态（含 `-ReviveMode active`） | 无 | 刷新出厂态/对照 |

**命令行回退（首选，最简）**：
```powershell
$t = Get-ScheduledTask -TaskName "QS_DaemonHeartbeat"
$orig = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "D:\miniQMT策略实盘\QuantStudio\scripts\check_daemon_alive.ps1" -Quiet -IntervalMinutes 10'
Set-ScheduledTask -TaskName "QS_DaemonHeartbeat" -Action (New-ScheduledTaskAction -Execute $t.Actions[0].Execute -Argument $orig)
```

## 六、首小时观察计划（20:50 → 21:50，每 10 分钟一轮）

| 观察 | 正常态 | 需处置 |
|---|---|---|
| `daemon_revival.log` 新行 `action=` | `NONE` / `skip-threshold` / `skip-occupancy` / `skip-cooldown` | 出现 `REVIVE_CALLED` / `REVIVED` ⇒ 立即核 `status.json` 新鲜度 + 采集轮是否正常 |
| `REVIVE_FAILED` / `REVIVE_DENIED` ≠ 0 | 0 | **立即停用回落**（改回 observe 或加 `-NoRevive`）并上报 |
| Heartbeat `LastResult` | 0 | 非 0 ⇒ 查本次事故同源（引号/路径） |

**切换时基线**：复活日志 **348 行**（20:51:51 手动触发行 `verdict=OK skip-threshold action=NONE`）；`CALLED=0` / `REVIVED=0` / `ARMED=29`。

## 七、首轮实跑读数（20:51:51，active 模式）

```
2026-11-12 20:51:51 verdict=OK consecutive=0 gate=skip-threshold(0/3) action=NONE target_pid=- | 连续 FAIL 未达阈值
```

⇒ active 模式下**行为与 observe 一致**（daemon 存活 ⇒ 零计数 ⇒ 不出闸）——**符合预期，无回归**。

## 八、教训（本轮三次踩坑，同族）

**「工具替身/格式假设」家族**（前科：`schtasks /change` 未预演引号、XML BOM、Enabled 判据）：

1. **改计划任务 action 一律用 `Set-ScheduledTask`**——`schtasks /change /tr` 经 shell 传参会吃内层引号，含空格路径必断；
2. **任务 XML 备份必须无 BOM**（`New-Object System.Text.UTF8Encoding($false)`），否则 `schtasks /create /xml` 拒收；
3. **回退点立验必须用"目标工具"实测**——只验"文件可解析"不够，须 `schtasks /create` 到**临时任务名**成功后再删；
4. **判据不适用于该对象时要能识别假失败**（`<Enabled>` 缺失 ≠ 任务禁用；以 `Status` + 实跑为准）。

## 九、边界

本次变更**仅一处**：`QS_DaemonHeartbeat` 的 `<Arguments>`（追加 19 字符）。未改脚本默认值、未动 `QS_DaemonForever`、未启停 daemon、未写库、未推送。
