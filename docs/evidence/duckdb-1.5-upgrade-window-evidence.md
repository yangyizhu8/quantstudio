# T001–T004 窗前实态基线与回退点（duckdb 首跳升级）

- **日期**：2026-10-29 16:16–16:40（Asia/Shanghai）
- **归属**：duckdb 数据库升级执行会话（图节点⑲）｜spec 002-duckdb-1.5-upgrade
- **阶段**：③实施门已开（检查点 A 批准于 2026-10-29），本基线 = Phase 1（Setup）产出
- **性质**：**只读**；未停 daemon、未装包、未改主库

## 一、回退点（T001）

| 项 | 读数 |
|---|---|
| HEAD | `f0ef3d5ec23519ff5af15dd99ad436050a2df00f` |
| 工作树脏文件数 | **783**（多会话共享工作区，含他线在途改动，**非本会话产生**） |
| 本会话落点文件改动 | 实施前逐个 `git diff --numstat` 均为 **0** |

**对铁律 03「建 stash 回退点」的偏离（如实申报）**：本工作区含他线 783 项在途改动，
`git stash create` 会把他线内容整体卷入回退对象，误 `pop` 的风险高于收益；
且本会话的改动全部为**未提交的工作区修改**，逐文件 `git checkout -- <path>` 即可精确回退、
不触碰他线文件。故回退点 = **HEAD SHA + 精确路径回退**（不建 stash 对象）。

## 二、窗前实态（T002）

| 项 | 读数 | 来源 |
|---|---|---|
| daemon 进程 | **PID 32032**，exe=`C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe`，started **2026-10-29T16:16:55**，status=running | `Get-CimInstance Win32_Process`（双形态正则，排除自身 $PID）＋ `data/daemon_status.json` |
| 计划任务 | `\QS_DaemonForever`：`Status=Running`、`Scheduled Task State=Enabled`、`Next Run Time=N/A`（手动 Run 形态，无 Daily 触发） | `schtasks /Query /TN QS_DaemonForever /V /FO LIST` |
| 主库 | `data/quantstudio.db` **55.32 GB**，mtime 2026-10-29 16:14:35 | 文件属性 |
| WAL | `data/quantstudio.db.wal` **不存在**（= 0 字节） | 文件存在性 |
| 解释器 duckdb 版本 | `Python311`=**1.4.5**；`C:\python3.12.9`=**1.4.5**；`_runtime\venv_quant_studio`=**1.4.5**；`trading-battle-back\venv_miniQMT`=**1.5.3**（不升，登记只读消费者） | 逐解释器 `import duckdb` 探测 |

> 说明：daemon 进程 PID 与 2026-10-29 00:47 那一代（PID 41176）不同、启动时刻为 16:16:55，
> 说明期间有守护/计划任务再次拉起。**窗口实施必须以届时实态重新探测**（spec FR-014 / T002）。

## 三、隔离环境复核（T003）

| venv | duckdb | 用途 |
|---|---|---|
| `%TEMP%\venv_ddb156`（Python 3.11.9） | **1.5.6** | A-14 计量、F2 实验、后续升级前预演（**不需给生产解释器装包即可先量**） |
| `%TEMP%\venv_ddb155`（Python 3.11.9） | **1.5.5** | 事故版本复验 |

两 venv 均可用；**本轮未对任何生产解释器执行 pip 安装**。

## 四、F4 巡检守卫复核（T004）

```
> python scripts/check_passthrough_pk.py --check
SKIP: 库被占用（daemon 运行中），请在停服窗或副本上执行。
IOException: IO Error: Cannot open file "...quantstudio.db": 另一个程序正在使用此文件
exit=3
```

- `scripts/check_passthrough_pk.py`（5,810 B）与 `docs/evidence/pk_baseline.json`（3,966 B）**均存在**；
- 本会话首跑读数：`--check` 输出 **SKIP / 退出码 3**（IO Error：另一个程序正在使用此文件）——守卫**可用**；
- **④实测更正（2026-10-30）**：④在接入每日哨兵（第 4 步）时**三次实测**确认 `read_only` 连接可与 daemon 写连接**共存** ⇒ daemon 运行期 `--check` 可正常返回 **exit 0**。
  ⇒ 本节首跑的 SKIP 读数**不作废但不作通则**：两次读数差异说明「SKIP/3」是**特定时点的库独占态**（当时另有进程持有主库），而非 daemon 在跑时的必然结果。
  以④的三次一致实测为准；后续表述统一为「通常 exit 0；库被独占时 SKIP/3（非失败，不计失败数）」。
- **T004 关闭**：F4 已由④于 **2026-10-29 02:11** 接入总运维每日哨兵**第 4 步**并首轮全绿（接入说明与两处补丁见 `docs/handoff/duckdb-f4-sentinel-onboarding-20261029.md`）。

## 五、本轮实施期动作留痕

| 动作 | 状态 |
|---|---|
| 停/启 daemon | **未做**（Phase 2 纯代码改动，不需停服；窗口在 11-07/11-08，届时先声明占用并与 dev 线联办） |
| 装包 | **未做**（未对任何解释器执行 pip install） |
| 改主库 | **未做**（主库零写入；冒烟写入按 spec FR-014 只落独立临时库/独立文件） |

## 六、回滚路径核对（T023 · **备而不用，本轮未演练**）

| 项 | 核对结论 | 依据 |
|---|---|---|
| 降版命令 | `pip install "duckdb==1.4.5"`（**显式版本**）；**禁用** `pip install -r requirements.txt` —— 声明面已锁 `duckdb==1.5.6`，用 `-r` 会再升回 1.5.6 | quickstart §2 R2 |
| 旧版打开现库 + 抽样读取 | 降版后**同一解释器**只读打开现库，对 `stock_daily` / `trade_calendar` / `index_constituents_snapshot_meta` 执行 `SELECT count(*)`，无格式错误 | quickstart §2 R3 |
| 门禁复核 | 同一解释器跑 CLI ⇒ `PASS 1.4.5`（退出码 0），daemon 放行 | quickstart §2 R4 |
| 库异常路径 | `check_wal.ps1 -Force`（Windows）/ `check_wal.sh --force`（macOS/Linux）已具备该能力：ps1 有 `-Force` 开关（`check_wal.ps1:38`）、sh 有 `--force` 参数（`check_wal.sh:30`），两者均「跳过无需处理早退、强制执行收敛」 | 源码核对（本轮读全文） |
| 红线 | **禁手工删除 `.wal`**；运行期禁 CHECKPOINT（写者占用时脚本直接 fail 并给指引）；脚本移走陈旧 WAL 前已备份 | 铁律 23 + 两脚本注释 |
| 演练记录 | **未演练**：本轮 daemon 在跑（PID 32032），演练须停写者并触碰主库 CHECKPOINT ⇒ 属停服窗动作；窗口内如触发回滚，按上表逐条执行并把读数回填本文件 | — |

---

## 七、11-07 窗口记录（W0 前置）

- **P5 回退点已建（2026-11-07 01:30）**：铁律 03 四步配方（精确路径 add → stash create → **stash store 持久化** → reset）；
  stash hash = `dabfb020c67a850b29d966d76f9a385facb2fcd0`（含快照面：实施卡 W8 回写增量 + tasks 钟点增量 + 本证据文件）；**他线在途文件未纳入快照**（`scripts/start_daemon_guarded.ps1` 等未 add）。
- **P4 占用声明已发（01:00，R31）**：daemon 当前实态 PID 50452（00:00 快测）→ 01:10 复测 forever 已收工、once 35988 在跑（候总调度裁）；GUI 50864。
- **开窗前实态（00:00/01:10 两轮实测）**：主库 **62.41 GB**；WAL **5.8 MB**（<256MB，窗内无需处置）；`daemon_status.json` 不存在 ⇒ 判据走进程双形态。

### §7.1 窗口执行实录（2026-11-07 02:05–02:15，总时长 ≈10 分钟，SC-003 ✓）

| 步 | 实测 |
|---|---|
| W0/W1 | t0=02:05:03；GUI 无进程 ✓（用户已提前关） |
| W2 | daemon/once 零残留 ✓（用户已停 once、forever 已收工） |
| W3 基线 | 库 **62.47 GB**、WAL 0.1 MB、duckdb **1.4.5**（升级前直读） |
| W4 装包 | `pip install duckdb==1.5.6`（Python311）exit 0，65s；版本直读 **1.5.6** ✓；pip 附带警告：`event-quant-factory 0.1.0 requires duckdb==1.3.2`（陈旧声明，不阻断，记录在案） |
| W5 门禁 | `-m` 面 `PASS 1.5.6` exit 0 ✓＋单文件直调面 `PASS 1.5.6` ✓ |
| W5c 主库只读开合 | 1.5.6 下 read_only 打开 ✓（trade_calendar=3,652 / stock_daily=9,925,654） |
| W6 冒烟 | **S1–S6 ＝ 6/6 PASS**（S1 临时库读写/S2 时区/S3 upsert/S4 主库只读点查〔补跑：stock_daily=9,925,654、snapshot_meta=719〕/S5 storage_version=**64≤68** 创建串 v1.4.5/S6 替身触发 30 组合 33s） |
| **T057 回滚演练** | ①降版 1.4.5（54s）✓ ②**旧版 connect 现库成功 ×2**（两次 SELECT 失败均系我方列名笔误〔trade_calendar 实列 cal_date、stock_daily 实列 time〕，非库不兼容——「旧版可开库」判据已两次实证）③门禁 `PASS 1.4.5` ✓ ④复升 1.5.6（66s）⑤门禁 `PASS 1.5.6` ✓；数据侧抽样读数以 1.5.6 同库读数对照（见 W5c/S4） |
| W8 拉起 | **工作树版 `scripts/start_daemon_guarded.ps1`**（Start-Process 脱离 job，总调度裁定）⇒ **daemon pid 33780、02:15:00 起、`-m quantstudio.pipeline.daemon --mode forever`** ✓；QS_DaemonForever 任务在册 Ready / LastResult 0 |
| W9 | t1≈02:15:2x，窗口时长 **≈10 分钟**（SC-003 ≤60min ✓）；库 62.47 GB；WAL 窗末值见上行 |
| W10 | 占用声明随上报块拆除（本节即窗口报告）；双方复核：dev 复核采集链（首轮读数）、我方已复核门禁与库状态（本文） |

**升级结论**：**1.4.5 → 1.5.6 首跳成功**——门禁双面 PASS、冒烟 6/6、回滚演练四步全过（旧版可开库两次实证）、daemon 已以 forever 形态恢复。

### §7.2 T057-②c 旧版 SELECT 抽样补齐——**已完成（2026-11-07 19:5x，daemon 停机窗顺带）**

窗口内两次 SELECT 失败均系我方列名/类型笔误（`trade_calendar` 实列 `cal_date`；`stock_daily` 实列 `time` 为 **BIGINT epoch-ms**，非字符串）。补齐在 **daemon 停机窗**（节点1 P0 停机，总调度 2026-11-07 激活触发链）内完成，**3 秒级**：

- 执行环境：**Python312.10 + duckdb 1.4.5**（旧版独立环境；主环境 1.5.6 与 daemon 零扰动）
- 实测读数（**1.4.5 下取得**）：
  - `trade_calendar`：count=**3,652**，max(cal_date)=**1798646400000**（epoch-ms）
  - `stock_daily`（time ≥ 2026-10-01）：**116,339** 行
  - `index_constituents_snapshot_meta`：**719** 行
  - 最新样本行：`('000001', 1793808000000, 11.64)`
- **SC-004 三要素全部齐备**：①旧版本**可打开**现库 ✓（本次第三次实证）②抽样查询**返回结果** ✓（四项读数）③**无格式不兼容** ✓（1.4.5 / 1.5.6 双版本均可打开同一库文件）
- 附：执行时 WAL = 无文件（daemon 停机后库已 checkpoint）

### §7.3 首周观测 第 1 天（2026-11-08，窗口后首日）

| 项 | 读数 | 来源 |
|---|---|---|
| daemon 恢复实况 | **pid 38020，2026-11-08 01:11:01 起，`-m quantstudio.pipeline.daemon --mode forever`** | Win32_Process 实测（双形态正则） |
| 上一代 daemon 终态 | pid 33780 于 **2026-11-07 19:44:26 ExitCode=0（normal-exit）** —— P0 停机为**正常退出**非强杀 | `data/logs/daemon_guard_heartbeat.log` |
| 版本门 | `PASS 1.5.6`，exit 0 | 判定源 CLI 实测 |
| 主库 | **62.47 GB**，mtime 2026-11-08 00:02:28 | 文件系统实测 |
| WAL | **无文件**（0 字节） | 文件系统实测 |
| 上游缺陷 FATAL 关键词 | `Failed to delete all rows from index`：**30 个近期日志命中 0**（判据须 = 0）✓ | `data/logs/*.log` 扫描实测 |
| daemon 首轮耗时 | 第 1 天窗口内无完整轮次读数（daemon 01:11 刚恢复） | 待次日补 |

**首周观测窗口**：2026-11-08 ～ 2026-11-14（7 天，含 11-08/09 周末）。本节为第 1 天读数，后续每日同法补记（观测法见 `docs/handoff/duckdb-db-family-candidates-batch3-and-firstweek-prep-20261107.md` §2.1）。

### §7.3.1 day-1 追测：forever 常驻终态核因（2026-11-08 02:12）

| 项 | 读数 |
|---|---|
| forever 实例 38020 终态 | **2026-11-08 01:12:41 `ExitCode=0 (normal-exit)`**（wrapper 心跳 `daemon_guard_heartbeat.log`）——拉起后存活约 1.5 分钟即正常退出 |
| 当前进程面 | 仅 **once 轮 pid 40620**（01:13:58 起，`once_etf_daily_verify_20261108.log` mtime 01:17）⇒ 采集链有活动，但**供血主体（`--mode forever`）当前不在位** |
| 计划任务 | QS_DaemonForever：Status **Ready**、Last Run 2026/11/8 1:11:01、Last Result **0**、**Next Run N/A**（Daily 触发已移除，仅 Boot 触发） |
| `data/backtest_stop.flag` | **不存在** ⇒ 非「回测停采」跳过路径 |
| 判据结论 | day-1 **FATAL=0 ✓ / WAL<256MB ✓ / 版本门 PASS 1.5.6 ✓ / forever 常驻在位 ✗** ⇒ 观测判据如实报 **FAIL**（供血主体缺席） |

> **归属**：forever 常驻的恢复归**节点1 P0 流程**（我方不介入、不接管，铁律 19 避让）；本会话仅登记实态并按「异常才上报」口径上报。
> **注**：本会话自持的观测脚本 `scripts/check_duckdb_firstweek_obs.py` 判据**不迁就现实**——once 轮在跑不等于供血主体在位，故判据以 `--mode forever` 实例为准。

### §7.4 首周观测日表（每日一行 · 由 `scripts/check_duckdb_firstweek_obs.py --evidence` 自动追加）

| 日期 | 判据 | 判定 |
|---|---|---|
| 2026-11-08 | FATAL=0 ✓ / WAL 7.93MB<256MB ✓ / 版本门 PASS 1.5.6 ✓ / forever 常驻在位 ✗（38020 于 01:12:41 normal-exit，当前仅 once 轮 40620） | **FAIL（供血主体缺席 · 归节点1 P0）** |
| 2026-11-08 | FATAL 命中 **0**（窗口内日志）｜ WAL 0.0 MB ｜ 版本门 PASS 1.5.6 ｜ daemon 无（once 轮 04:03 收工）｜ **采集轮质量审计 CLI 级失败结论 51 条**（窗口内 60 个日志，含 `once_etf_daily_verify_20261108.log` @04:03:24、`daemon.log` @00:05:10 与 @04:03:24 等；伴随 `etf_minutes/AdjustmentAnchorDrift count=66 FAIL>0.5%` 明细）——**属 etf_minutes 复权锚域·他线 P0 案面，我方只记录不修**｜ 判据 FATAL=0 ✓ / WAL ✓ / 版本门 ✓ / daemon ✗ / 质量审计=0 ✗ | **FAIL（供血主体缺席 + 采集轮质量审计反复失败）** |

### §7.5 T056 到期复核装置「真实触发一次」验证（2026-11-08 14:02 · 真实当日）

| 项 | 实测 |
|---|---|
| 触发方式 | **真实日期触发**（`scripts/check_duckdb_eol_guard.py --check --emit …`，**无 `--as-of` 模拟**） |
| as-of | **2026-11-08**（真实 today） |
| 判定 | 1.4 线 EOL **2026-11-17**（day）｜距 EOL **+9 天**｜状态 **`close_or_accept`**（距 EOL 30 天：完成或书面接受风险）；1.5 线 EOL 2027-03-31（month）｜+143 天｜`normal` |
| 退出码 | **1**（有到期复核提醒，需人处理） |
| 提醒件落盘 | **`docs/evidence/eol-review-reminder-20261108.md`（862 B，mtime 2026-11-08 14:02:18）**——frontmatter `id: EOL-REVIEW-20261108` / `type: eol_review_reminder` / `status: open` / `as_of: "2026-11-08"` |
| 结论 | **T056「验证能被真实触发一次」判据成立**——真实日历 ＋ 真实当日 ＋ 真实判定 ＋ 真实落盘（非模拟、非构造）；此前 2026-10-30 17:02 已有首次真实触发记录（`docs/evidence/eol-review-reminder.md`） |

### §7.6 forever 恢复实况 + day-3 前置观测（2026-11-08 15:13–15:14）

| 项 | 读数 |
|---|---|
| **forever 恢复** | **pid 36788，2026-11-08 15:13:06 起，`--mode forever`**；wrapper 心跳 `GUARD start wrapper_pid=46876 daemon_pid=36788 alive=True` / `15:14:06 alive=True uptime=1min` ⇒ **供血主体恢复（节点1 P0 链完成）** |
| 上一代终态 | 38020 于 01:12:41 `ExitCode=0 (normal-exit)`；**01:12 → 15:13 供血主体缺席约 14 小时**（once 轮 44672 于 13:51:58 起补数，14:31:33 仍有库写入） |
| **day-3 前置观测（15:14:25）** | FATAL **0** ✓ ｜ WAL **0.0 MB** ✓ ｜ 版本门 `PASS 1.5.6` ✓ ｜ **forever 常驻在位 ✓（36788）** ｜ once 轮在跑 ✗（此刻无 once，属轮间空档）｜ 采集轮质量审计 CLI 级失败 **46** 条 ✗（**归⑳/A 类域·etf_minutes 复权锚**，我方只记录不修） |
| 判定 | 五项判据**四项绿**，唯一未绿项为**他线域**的采集轮质量审计 ⇒ **我方两项依赖之一已消除** |

> 说明：「once 轮在跑 ✗」仅表示采集处于轮间空档（非判据项）；判据以 **forever 常驻**为供血口径。

### §7.7 forever 供血主体「被外部终止→重拉」循环（2026-11-08 15:58 当轮实测）

| 代 | 起止 | 终态 | 存活 |
|---|---|---|---|
| 38020 | 01:11:01 → 01:12:41 | `ExitCode=0 (normal-exit)` | 1.5 分钟 |
| 36788 | 15:13:06 → **15:58:19** | **`ExitCode=-1 (external-terminate or native)`** | **45 分钟** |
| 44904 | **15:58:25 → 在位** | — | 观测时已起 |

- wrapper 心跳实证：`15:58:08 alive=True uptime=45min` → `15:58:19 GUARD exit … ExitCode=-1` → `daemon.log:15446 15:58:25 [DaemonLifecycle] status 已发布 (pid=44904)`；
- 计划任务 `QS_DaemonForever`：Last Run **15:13:06** / Last Result 0 / **Next Run N/A**（仅 Boot 触发）⇒ 重拉来自**wrapper/守护链**，非 Daily 触发；
- **观测判据「forever 常驻在位」在 15:58 成立**（44904），但**供血主体呈「反复被终止—重拉」模式**（两代终态分别为 normal-exit 与 external-terminate）；
- **归属**：daemon 生命周期与重拉链归**节点1 P0 / 运维链**（铁律 19 避让，我方不介入、不重启、不接管）；我方只登记实态并纳入首周观测项。

### §7.8 day-3 观测（2026-11-08 15:58:40）

| 判据 | 读数 | 判定 |
|---|---|---|
| FATAL 关键词 | **0**（扫 60 日志） | ✓ |
| WAL | **0.0 MB** | ✓ |
| 版本门 | `PASS 1.5.6` | ✓ |
| forever 常驻在位 | ✓（44904，15:58:24 起） | ✓ |
| 采集轮质量审计 CLI 级失败 | **45**（趋势 51→48→46→45 缓降） | ✗（⑳/A 类域·etf_minutes 复权锚，我方只记录） |

⇒ 五项判据**四项绿**；唯一红项为他线域。

### §7.9 day-4 观测 + 占用声明豁免机制（2026-11-08 20:12 当轮实测）

**day-4 读数（20:12:20）**：FATAL **0** ✓ ｜ WAL **9.12 MB** ✓ ｜ 版本门 `PASS 1.5.6` ✓ ｜ **forever 常驻不在位 ✗**（仅 once 轮 40344，18:56:11 起）｜ 采集轮质量审计 CLI 级失败 **43** ✗（趋势 51→48→46→45→43 持续缓降，**⑳/A 类域**）⇒ 判定 FAIL。

**新增「占用声明豁免」机制**（服务总调度 2026-11-08 跨线约束「⑳阶段执行若停 daemon 须补声明窗」）：

| 项 | 内容 |
|---|---|
| 落点 | **`data/daemon_occupancy.json`**（各线共用：升级线 / dev 线 / ⑳阶段执行）——机器可读的占用声明，窗口前写入、窗口后删除或置 `occupied=false` |
| 字段 | `occupied`(bool) / `window{start,end}` / `operation` / `owner` / `purpose` / `expected_recovery` / `rollback` |
| 豁免语义 | 声明窗内 **daemon 缺席判为「计划内停机」**（判据 `daemon_alive` 视为通过）⇒ 不误报供血故障；**无声明时缺席仍如实判 ✗**（不迁就现实） |
| **真伪双验（20:12 实测）** | ①写入进行中声明（owner=⑲自测）⇒ 输出「声明窗内 ⇒ 缺席属计划内停机，豁免」、`forever 常驻在位 ✓`；②**还原为无声明态** ⇒ 输出「无进行中声明」、`forever 常驻在位 ✗` ⇒ 豁免分支**只对声明生效**，无误放 |
| 边界 | 声明存在但 `window` 字段缺失 ⇒ **保守豁免**（宁可放过也不误报故障，但须由声明方补字段）；声明文件本身**不入库**（运行态文件，各线实时改写，入库必冲突）⇒ 仅入 `.gitignore`，格式约定落本文档 |

**注**：本次自测**未停任何 daemon**（声明窗内 daemon 本就缺席，豁免验证不依赖真实停机），还原动作已完成，声明文件已回到 `occupied=false`。

### §7.10 day-4 复跑 + 两条新事实（2026-11-08 21:14:35 当轮实测）

**观测读数（21:14:35，与 20:12 一致）**：FATAL **0** ✓ ｜ WAL **9.12 MB** ✓ ｜ 版本门 `PASS 1.5.6` ✓ ｜ **forever 常驻不在位 ✗** ｜ once 轮在跑 ✓ ｜ 采集轮质量审计 CLI 级失败 **43** ✗（⑳/A 类域）⇒ 判定 FAIL。

**新事实①：forever 自 15:58:19 起「被终止后未自愈」**
- wrapper 心跳尾部仍停在 `15:58:19 GUARD exit wrapper_pid=46876 daemon_pid=36788 ExitCode=-1 (external-terminate or native)`，**此后无任何 `GUARD start` 记录**（对比 15:13 那次 external-terminate 前是「正常重拉」）；
- 即 **15:58:19 → 21:14:35 约 5 小时 16 分钟，供血链处于断开且未自愈状态**（此前 15:13 的重拉成功未再复现）；
- 归属：**节点1 P0 / 运维链**（铁律 19 避让，我方不重启、不接管、不写占用声明代管）。

**新事实②：once 轮 40344 长跑未收工**
- pid 40344 起于 **2026-11-08 18:56:11**，至 21:14 仍在跑，**已持续 ≈2 小时 18 分**；对比 04:03 收工的那轮（约 1 小时内完成）明显偏长；
- 与同窗口「采集轮质量审计 CLI 级失败 43 条」并存；**候选归因（不作认定）**：①该轮正处于质量审计失败后的重试/等待路径 ②⑳阶段执行占用采集链 ③其他线在途；
- 归属：**dev 线 / ⑳ / 运维链**（我方只记录事实与候选，不擅判、不干预）。

**观察面判据不变**：无进行中占用声明 ⇒ daemon 缺席仍如实判 ✗（不迁就现实）；本次也未写入声明文件代管他线窗口（声明由动作方写）。

