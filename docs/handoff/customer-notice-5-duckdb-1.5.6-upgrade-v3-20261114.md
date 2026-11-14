# 客户通知稿（**v3 修订终稿**）：QuantStudio 组件 duckdb 版本升级至 1.5.6（通知编号 5）

> **⚠ 本件已被 v4 修订终稿取代（2026-11-15 第三次退修）**。
> 退修点：**备份须证「无写者、同一快照、可恢复」**（v3 只笼统写了「一致性由无写者保障」，未给可举证判据）；
> 且**小合成库结果不得代替真实窗口**。v4 已把备份改为可举证序列（无写者举证 → 前后指纹判定 `SNAPSHOT-CONSISTENT/BROKEN`
> → 真实备份只读可恢复验证 → 举证包留存），并说明为何不用 `check_wal.ps1 -BackupDb` 做升级备份。
> **现行终稿 = `customer-notice-5-duckdb-1.5.6-upgrade-v4-20261115.md`（待总调度再审，未下发）**
> ——**下发以下发 v4 为准，不得再下发本件**。本件仅作 v3 留痕与 diff 对照。

> **状态**：**v3 修订终稿（已被 v4 取代）· 未下发**。
> 本版依据总调度 2026-11-14 第二次审核四点退修（解释器/pip 定位·停机与备份与可恢复验证与回退、旧门证据限定、
> 合成库结论限定、5000 行下界），逐项落实见文末「修订说明」。
> 下发须经**用户确认闸门**（铁律 01 六步第⑤步）；本通知**仅由总调度转发**，执行会话不代下发。
> **建议下发时间**：不晚于 **2026-11-17**（1.4.x 线 EOL 日）。
> **适用范围**：使用 QuantStudio 主仓（GUI / daemon / 采集链）并会启动采集的客户端。

---

## 一、这次要做什么

QuantStudio 的数据库组件（duckdb）从 **1.4.5 升级到 1.5.6**。
项目对 duckdb 设有**启动闸门**：版本不在放行集合内时程序会**拒绝启动**并打印修复指引——这是**预期保护行为，不是故障**。

### 1.1 什么组合会被拒启

| 代码版本 | duckdb 依赖 | 结果 | 说明 |
|---|---|---|---|
| **旧代码**（本次升级前） | **1.5.6** | **❌ 拒绝启动（退出码 3）** | 旧门按 **`1.4.` 前缀**校验（举证与限定见 §二·附） |
| 旧代码 | 1.4.5 | ✅ 正常 | 维持现状 |
| **新代码**（本次升级后） | **1.5.6** | ✅ 正常 | 本次目标状态 |
| **新代码** | **1.4.5** | ✅ **正常（允许）** | 新门放行集合同时包含 1.4.x 与 1.5.6 |

⇒ **唯一的强制顺序**：**要升依赖到 1.5.6，必须先（或同批）把代码升到新版**。
反过来「只升代码、依赖先留在 1.4.5」是**允许**的。

### 1.2 请先确认「您机器上的那个 python」是哪一个（**不要直接敲裸 `pip`**）

裸 `pip` / 裸 `python` 指向的解释器**未必**是 daemon 与 GUI 实际使用的那个；装错解释器会出现
「明明装了 1.5.6，程序仍报 1.4.5」或「装到了另一个 venv，服务端毫无变化」。

**做法一（推荐，一条命令查出真实解释器）**——在**采集进程正在运行**时执行：

```powershell
# 列出正在运行的 QuantStudio daemon，并显示它使用的 python 绝对路径
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'quantstudio\.pipeline\.daemon' } |
  Select-Object ProcessId, ExecutablePath, CommandLine | Format-List
```

输出的 `ExecutablePath` 就是**后续所有 pip 命令必须使用的那个解释器**。

> 参考（本机实测样本，**您的机器可能不同，请以命令输出为准**）：
> `C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m quantstudio.pipeline.daemon --mode forever --config-dir config\profiles\mcp_only`
> 注意：旧文档提到的 `_runtime\venv_quant_studio` 在**本机实测不存在**，请勿照抄该路径。

**做法二**：若 daemon 当前未运行，用**您平时启动 GUI 的那个解释器**；拿不准时执行
`<该解释器> -c "import sys,duckdb;print(sys.executable);print(duckdb.__version__)"`，两行输出即「解释器 + 当前 duckdb 版本」。

**后续命令一律把 `pip` 写成 `<解释器> -m pip`**：

```powershell
# 例（把路径换成上一命令实际输出的那个）
& "C:\Users\<您>\AppData\Local\Programs\Python\Python311\python.exe" -m pip install "duckdb==1.5.6"
```

## 二、允许的版本集合（门禁判定源）

| 版本 | 是否允许 |
|---|---|
| 1.4.5 ～ 1.4.x（`1.4.5 ≤ x < 1.5`） | ✅ 允许（维持现状亦可） |
| **1.5.6** | ✅ 允许（本次目标版本） |
| 1.5.0 ～ 1.5.5 | ❌ 不允许 |
| 2.0.x 及以上 | ❌ 不允许（本次不含跨大版本） |

判定源：`quantstudio/pipeline/duckdb_version_policy.py`（门禁 / 双平台巡检脚本 / 测试契约三面同源）。

> **关于上游缺陷 `duckdb#23645`（限定表述）**：1.5.x 存在该上游缺陷（索引删除在特定条件下触发致命错误），
> **在 1.5.6 仍未修复**，只是不再静默出错。本项目**在已执行核验与首周观测所覆盖的范围内**未观察到暴露；
> **该结论不外推到未测路径、未测数据分布或您的特定业务负载**。若出现 §七 第 4 行的日志文本，请立即反馈。

### §二·附：旧代码对 1.5.6 的处置（**限定到所核版本与默认配置**）

| 项 | 内容 |
|---|---|
| 核查对象 | `fdca384` 的**父版本**（`git show fdca384^:quantstudio/pipeline/duckdb_version_gate.py`），即本次升级前该文件的最后状态 |
| 判定实现 | 常量 `REQUIRED_SERIES = "1.4"`；判据 `return ver.startswith(REQUIRED_SERIES + ".")` ⇒ **按 `1.4.` 前缀校验** |
| **默认配置下的实测** | 在**未设置** `QS_DUCKDB_VERSION_GATE` 的情况下，`check_duckdb_version()` 对 `1.5.6` 返回 `(False, "1.5.6")` ⇒ `require_duckdb_version()` 会 `SystemExit(3)` **拒启** |
| **逃生阀（必须一并告知）** | 该版本门支持环境变量 **`QS_DUCKDB_VERSION_GATE=0`** 临时放行：`if os.environ.get(GATE_ENV, "1").strip() == "0":` ⇒ 仅记 `logger.warning` 后**继续启动，不阻断** |
| **结论的边界** | 上述「必拒」**仅适用于**该父版本 **且** 默认配置（逃生阀未开）。**不能**据此断言：所有客户机上的所有旧代码、所有配置组合都会拒启；若您的环境已设 `QS_DUCKDB_VERSION_GATE=0`，旧代码配 1.5.6 也能启动（**风险自负且不受支持**，不建议） |
| 复现 | `git show fdca384^:quantstudio/pipeline/duckdb_version_gate.py \| Select-String 'REQUIRED_SERIES\|GATE_ENV\|startswith'` |

## 三、升级步骤（**必须整段按顺序执行**，建议非交易时段）

> 前置材料：一把能登录本机的管理员会话；建议先确认磁盘剩余空间 ≥ **主库 ×1.5**
> （备份会复制一份主库；本机主库约 62 GB 量级，拷贝较慢，请预留时间）。

### 步骤 1 · 停机（**备份前必须完成，否则备份不一致**）

1. 关闭 GUI（主程序窗口全部退出）。
2. 停止 daemon：

   ```powershell
   # 停计划任务
   schtasks /End /TN QS_DaemonForever
   ```

3. **确认无写者**（关键，三项都要核）：

   ```powershell
   # ① 计划任务状态应为 Ready/Disabled，不应是 Running
   schtasks /Query /TN QS_DaemonForever /FO LIST | Select-String 'Status'

   # ② 不应再有 daemon 进程（forever 与 once 都算写者）
   Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
     Where-Object { $_.CommandLine -match 'quantstudio\.pipeline\.daemon' } |
     Select-Object ProcessId, CommandLine

   # ③ 不应有 GUI 进程
   Get-Process | Where-Object { $_.ProcessName -match 'QuantStudio|pythonw' } | Select-Object Id,ProcessName
   ```

   **三项任一仍有输出 ⇒ 不要继续**（写者运行中做备份/CHECKPOINT 属明令禁止，见铁律 23）。
   > 另需注意：任何会打开主库的入口（`--mode once`、GUI 里手动跑任务）在备份与升级窗口内**都不要执行**。

### 步骤 2 · 一致备份（主库 + WAL）

用仓库**根目录**的 `check_wal.ps1`（**不是** `scripts\check_wal.ps1`，该路径不存在）：

```powershell
cd <QuantStudio 仓库根>
powershell -ExecutionPolicy Bypass -File .\check_wal.ps1 -BackupDb -Py "<步骤0查到的解释器绝对路径>"
```

它做三件事（顺序固定）：

| 顺序 | 动作 | 说明 |
|---|---|---|
| 1 | `Copy-Item <主库> "<主库>.bak.<时间戳>"` | 由 `-BackupDb` 触发；**大库复制较慢** |
| 2 | `Copy-Item <主库>.wal "<主库>.wal.bak.<同时间戳>"` | **仅当 WAL 存在**；不存在时打印「无 WAL（跳过 WAL 备份）」 |
| 3 | `CHECKPOINT` + 表结构校验 | 失败会明确报错并给出 `-RecoverStaleWal` 指引 |

> **一致性口径（如实说明）**：两份备份**共用同一时间戳后缀**，但它们是**两次独立文件拷贝，不是文件系统级原子快照**。
> **一致性由「步骤 1 已确认无写者」保障**——只要没有进程在写，先后两次拷贝的内容就是同一静止状态。
> 若您跳过步骤 1，备份**可能不一致**，请删除该批 `.bak` 重做。

### 步骤 3 · 升级代码

```powershell
git pull
```

### 步骤 4 · 升级依赖（**顺序不可颠倒**，见 §1.1；**必须用上面的解释器**）

```powershell
& "<步骤0查到的解释器绝对路径>" -m pip install "duckdb==1.5.6"
```

> 不要用 `pip install -U duckdb`（会装到未纳入放行集合的最新版本）；
> 建议同批核对 `requirements.txt` 已钉 `duckdb==1.5.6`。

### 步骤 5 · 自检（一条命令，输出即结论）

```powershell
& "<步骤0查到的解释器绝对路径>" -m quantstudio.pipeline.duckdb_version_gate --check
```

期望 `PASS 1.5.6`、退出码 0。读数怎么读见 §四。

### 步骤 6 · **可恢复验证**（在升级后、恢复采集前做，验证备份真的能恢复）

对**备份副本**做只读自检（**不打开生产主库**）：

```powershell
$bak = "<主库>.bak.<步骤2的时间戳>"   # 换成实际文件名
& "<解释器>" -c @"
import duckdb, sys
p = r'''$bak'''
c = duckdb.connect(p, read_only=True)
print('duckdb =', c.execute('SELECT version()').fetchone()[0])
print('tables =', c.execute(\"SELECT count(*) FROM duckdb_tables()\").fetchone()[0])
print('stock_daily rows =', c.execute('SELECT count(*) FROM stock_daily').fetchone()[0])
print('stock_daily cols =', [r[1] for r in c.execute(\"PRAGMA table_info('stock_daily')\").fetchall()])
c.close()
print('RESTORE-CHECK OK')
"@
```

判据（三条全中才算通过）：① 能只读打开、无异常；② `tables` 与 `stock_daily rows` 均为正数；
③ `stock_daily cols` 含预期列。末行打印 `RESTORE-CHECK OK` 即通过。
> 该命令的**语法与判据已在小合成库上实测通过**（返回行数/列名/版本三项正常）；
> **对您 62 GB 主库的实际备份迄今未执行**——备份与恢复验证必须在您的停机窗口内完成，本通知不代为执行。
> 若验证不通过，**不要恢复采集**，按 §五 回退。

### 步骤 7 · 恢复采集

```powershell
schtasks /Run /TN QS_DaemonForever
```

随后观察首日采集耗时（见 §六），并在日志中确认无 `Failed to delete all rows from index`。

## 四、自检输出怎么读

| 输出 | 含义 | 您需要做什么 |
|---|---|---|
| `PASS 1.5.6`（退出码 0） | 版本在放行集合内 | 无需操作 |
| `PASS 1.4.5`（退出码 0） | 仍在 1.4.x，**同样在放行集合内** | 可正常跑；要升级按 §三 |
| `FAIL <版本>`（退出码 1） | 版本**不在放行集合** | 按 §三 步骤 4 升到 1.5.6，或按 §五 留在 1.4.5 |
| `ERROR …`（退出码 2） | 依赖缺失或版本串不可读 | 报运维，附**输出原文** |

启动时提示「拒绝启动」并打印版本与修复指引（退出码 3）⇒ 见 §1.1 第一行，按 §三 顺序重做。

## 五、失败回退（**任一步骤失败即用**）

| 失败现象 | 回退动作 |
|---|---|
| 步骤 4 装包失败 / 装完版本不对 | `& "<解释器>" -m pip install "duckdb==1.4.5"` → 重跑 §三 步骤 5，确认 `PASS 1.4.5` |
| 步骤 5 自检 `FAIL` / `ERROR` | 同上降回 1.4.5；**代码可保留新版**（新门允许 1.4.x），不必回退代码 |
| 步骤 6 可恢复验证不通过 | **停止**，不要恢复采集；保留 `.bak` 原件，联系我们 |
| 恢复采集后启动即崩 | 停 daemon → 降回 1.4.5 → 重跑自检 → 把**完整报错原文**发我们 |
| 日志出现 `Failed to delete all rows from index` | 立即停采集、保留日志、按本表降回 1.4.5，并**立即反馈** |
| WAL 相关报错 / 需处置陈旧 WAL | 用 `.\check_wal.ps1 -RecoverStaleWal -Py "<解释器>"`（**会先备份再移走陈旧 WAL**）；**禁止手工删除 `.wal`** |

> 回退后**代码版本与依赖版本可以不一致**（新代码 + 1.4.5 依赖是允许组合），但**绝不建议**旧代码 + 1.5.6。
> **1.4.x 线上游支持于 2026-11-17 结束**，长期留在该线将不再获得上游修复。

## 六、性能提示（**仅覆盖实测表、实测数据与实测路径**）

### 6.1 实测条件（缺一不可外推）

| 项 | 实测条件 |
|---|---|
| 机器 | **单台物理机**（同盘、两侧不同 venv） |
| OS / 解释器 | Windows；**Python 3.11.9**（两侧同版本） |
| duckdb | 1.4.5 vs 1.5.6（仅此一项不同） |
| 数据库 | **合成库**（建表 DDL、语句、PK 结构逐字复刻真实路径；**数据为合成数据，非您的生产数据分布**） |
| 表与语句 | 表 `index_constituents_snapshot_meta`（10 列，`PRIMARY KEY(index_code, time)`）；语句 `executemany("INSERT … ON CONFLICT (index_code,time) DO UPDATE SET …")`（9 个 SET 列） |
| 取样 | 每档 **3 次取中位**，各档独立新库、独立子进程；单档 >90s 记超时并终止 |

### 6.2 读数（中位）

| 单批行数 | 1.4.5 | 1.5.6 | 倍数 |
|---|---|---|---|
| 300 行 | 0.642 s | **3.943 s** | **6.14×** |
| 1000 行 | 3.314 s | **13.396 s** | **4.04×** |
| 5000 行 | 15.436 s | **≥90 s（超时终止，未测到完成时刻）** | **≥5.83×（下界）** |

### 6.3 正确性结论的**准确范围**

在上述**这张表、这份合成数据、这一条 `executemany` 语句**上，两版的 `row_count` 与主键校验均通过
（`row_count` = 输入行数、`PK = 1`）。
⇒ 可以说「**该被测路径上写入结果无差异**」。
⇒ **不能**扩大为「所有写入结果无差异」「所有表都不受影响」——其它表、其它数据分布、其它写入形态
（如 `INSERT … SELECT`）、其它 OS/解释器组合**均未测**。

### 6.4 影响面与建议

仅影响上述 `executemany` 形态下的**该辅助表刷新时延**，不影响正确性。
若您的采集批次达到千行量级，建议放宽该任务调度超时，或观察首日采集耗时，必要时拆分批次。

## 七、常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 启动报「duckdb 不在放行集合」 | 依赖已升到 1.5.x 但**代码仍是旧版**（旧门按 `1.4.` 前缀校验，默认拒） | 停采集 → `git pull` → 重跑自检（**先查 §二·附 是否设过逃生阀**） |
| 装了 1.5.6 但自检仍显示 1.4.5 | **装到了另一个解释器/venv** | 按 §1.2「做法一」查出真实解释器，用 `<解释器> -m pip` 重装 |
| 自检命令报「无法导入 duckdb」 | 解释器选错或依赖缺失 | 用 daemon 实际使用的解释器执行；附输出原文反馈 |
| 某个表刷新变慢 | 见 §六（仅 `executemany` 形态实测） | 调超时或拆批次；如影响主流程请反馈 |
| 采集正常但日志出现 `Failed to delete all rows from index` | 命中 1.5.x 上游缺陷（`duckdb#23645`） | **立即反馈并附日志片段**；按 §五 降回 1.4.5 |

## 八、需要您反馈的情况

- 自检输出与 §四 表格**不一致**；
- 升级后采集报错、重复写入，或出现 §七 第 4 行日志文本；
- 备份/可恢复验证任一步不通过（请附命令输出原文）；
- 您的环境使用了**我们未覆盖的 duckdb 版本、解释器或 OS 组合**（附 `python -c "import sys,duckdb;print(sys.executable,duckdb.__version__)"` 输出）。

请在反馈中附：自检完整输出、解释器绝对路径、Python 与 duckdb 版本、报错日志片段。

---

## 修订说明（v2 → v3，2026-11-14 第二次退修；内部留痕，下发时可删除）

| # | 审核意见 | 本版落实 |
|---|---|---|
| 1 | 升级步骤须补与客户实际运行环境一致的**解释器／pip 定位** | §1.2 新增「查出真实解释器」两条做法 + `ExecutablePath` 实测样本 + 明示「旧文档 `_runtime\venv_quant_studio` 本机实测不存在」；全文 pip 改 `<解释器> -m pip` |
| 2 | 须补**停机**、**主库+WAL 一致备份**、**可恢复验证**、**失败回退**，不能只给裸 pip/python | §三 重构为 7 步：停机（三步核无写者）→ `check_wal.ps1 -BackupDb` 备份 → 升级代码 → 装依赖 → 自检 → **备份副本只读可恢复验证** → 恢复采集；新增 §五 回退表（6 种失败现象） |
| 3 | 旧门证据须**限定到所核 fdca384 父版本及默认配置**，并写明 `QS_DUCKDB_VERSION_GATE=0` 绕过 | §二·附 重写：明确核查对象=父版本、判据=`startswith("1.4.")`、默认配置实测 `(False,"1.5.6")`、**逃生阀存在且仅警告不阻断**、结论边界「不得断言所有客户/所有配置必拒」；§七/§五 同步提示 |
| 4 | 合成库实验的性能与**正确性**结论只覆盖实测表/数据/路径；5000 行为下界 | §6.1 补「数据为合成数据，非生产分布」；**新增 §6.3「正确性结论的准确范围」**：限定为「该被测路径上写入结果无差异」，明示不得扩大；§6.2 5000 行标「≥90s 超时终止，未测到完成时刻」「≥5.83×（下界）」 |

**一致性口径（新增，防止误读）**：备份两份共用同一时间戳但**非原子快照**，一致性由「已确认无写者」保障；跳过停机则备份可能不一致。

**口径来源（内部随附）**：
- 放行集合与判定源：`quantstudio/pipeline/duckdb_version_policy.py`
- 旧门举证：`git show fdca384^:quantstudio/pipeline/duckdb_version_gate.py`（`REQUIRED_SERIES="1.4"`、`startswith`、`GATE_ENV="QS_DUCKDB_VERSION_GATE"` 默认 `"1"`）
- 解释器实测：`Get-CimInstance Win32_Process` → daemon PID 39200 `ExecutablePath=...\Python311\python.exe`
- 备份/恢复工具：仓库根 `check_wal.ps1`（`-BackupDb` / `-RecoverStaleWal` / `-Py`）；可恢复验证配方已在小合成库实测
- 性能量化：`docs/evidence/a14-executemany-scale-20261029.md`（含 §四 局限）
- `#23645` 暴露面核验：非唯一 ART 索引 = 0；41 个主键三臂验证不丢（`docs/evidence/pt01-f2-truncate-20261029.md`）
