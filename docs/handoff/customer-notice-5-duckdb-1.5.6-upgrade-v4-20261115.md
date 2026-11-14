# 客户通知稿（**v4 修订终稿**）：QuantStudio 组件 duckdb 版本升级至 1.5.6（通知编号 5）

> **⚠ 本件已被 v5 修订终稿取代（2026-11-15 第四次退修）**。
> 退修点：①**「无写者」须双证**——v4 仅按身份清点（计划任务/daemon/GUI），v5 增**主库 `FileShare.None` 句柄独占探针**（进程清点会漏掉命令行不含特征串的持有者）；
> ②**「同一快照」须证到「产物字节」**——v4 只校验源文件前后一致，v5 增六条判据之③–⑥（备份主库 size/首尾抽样哈希、备份 WAL size/**全量 sha256**），
> 并以鉴别力自检证明「备份被截断」「WAL 备份被改一字节」这两种情形**只看源文件前后一致抓不到**。
> **现行终稿 = `customer-notice-5-duckdb-1.5.6-upgrade-v5-20261115.md`（待总调度复审，未下发）**
> ——**下发以下发 v5 为准，不得再下发本件**。本件仅作 v4 留痕与 diff 对照。

> **状态**：**v4 修订终稿（已被 v5 取代）· 未下发**。
> 本版依据总调度 2026-11-14/15 第三次审核退修：**备份须证「无写者、同一快照、可恢复」；小合成库结果不代真实窗口**。
> 逐项落实见文末「修订说明」。
> 下发须经**用户确认闸门**；本通知**仅由总调度转发**，执行会话不代下发。
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

裸 `pip` / 裸 `python` 指向的解释器**未必**是 daemon 与 GUI 实际使用的那个。

**做法一（推荐，一条命令查出真实解释器）**——在**采集进程正在运行时**执行：

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'quantstudio\.pipeline\.daemon' } |
  Select-Object ProcessId, ExecutablePath, CommandLine | Format-List
```

输出的 `ExecutablePath` 就是**后续所有 pip 命令必须使用的那个解释器**。

> 参考（本机实测样本，**您的机器可能不同，以命令输出为准**）：
> `...\Python311\python.exe -m quantstudio.pipeline.daemon --mode forever --config-dir config\profiles\mcp_only`
> 注意：旧文档提到的 `_runtime\venv_quant_studio` 在**本机实测不存在**，请勿照抄。

**做法二**：若 daemon 当前未运行，用**您平时启动 GUI 的那个解释器**：
`<解释器> -c "import sys,duckdb;print(sys.executable);print(duckdb.__version__)"`，两行即「解释器 + 当前 duckdb 版本」。

后续命令一律写 `<解释器> -m pip`：

```powershell
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
> **该结论不外推到未测路径、未测数据分布或您的特定业务负载**。若出现 §七 第 5 行日志文本，请立即反馈。

### §二·附：旧代码对 1.5.6 的处置（**限定到所核版本与默认配置**）

| 项 | 内容 |
|---|---|
| 核查对象 | `fdca384` 的**父版本**（`git show fdca384^:quantstudio/pipeline/duckdb_version_gate.py`），即本次升级前该文件的最后状态 |
| 判定实现 | 常量 `REQUIRED_SERIES = "1.4"`；判据 `return ver.startswith(REQUIRED_SERIES + ".")` ⇒ **按 `1.4.` 前缀校验** |
| **默认配置下的实测** | 未设 `QS_DUCKDB_VERSION_GATE` 时，`check_duckdb_version()` 对 `1.5.6` 返回 `(False, "1.5.6")` ⇒ `require_duckdb_version()` `SystemExit(3)` **拒启** |
| **逃生阀（一并告知）** | 支持环境变量 **`QS_DUCKDB_VERSION_GATE=0`** 临时放行：`if os.environ.get(GATE_ENV, "1").strip() == "0":` ⇒ 仅 `logger.warning` 后**继续启动，不阻断** |
| **结论边界** | 上述「拒启」**仅适用于**该父版本 **且** 默认配置（逃生阀未开）。**不能**据此断言所有客户机上的所有旧代码、所有配置组合都会拒启；若您已设 `QS_DUCKDB_VERSION_GATE=0`，旧代码配 1.5.6 也能启动（**风险自负且不受支持**，强烈不建议） |
| 复现 | `git show fdca384^:quantstudio/pipeline/duckdb_version_gate.py \| Select-String 'REQUIRED_SERIES\|GATE_ENV\|startswith'` |

## 三、升级步骤（**整段按序执行**，建议非交易时段）

> **空间准备**：备份要复制一份与主库等大的文件（本机主库约 **62 GB** 量级）。请先确认
> **可用空间 ≥ 主库大小 ×1.5**，并预留足够时间（大库复制较慢）。

### 步骤 1 · 停机与「无写者」举证（**三件套缺一不可**）

1. 关闭 GUI（主程序窗口全部退出）。
2. 停 daemon：`schtasks /End /TN QS_DaemonForever`
3. **留下无写者证据**（必须落文件，供事后举证与故障复盘）：

   ```powershell
   cd <QuantStudio 仓库根>
   $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
   $ev = ".\data\upgrade-nolock-evidence-$stamp.txt"
   "=== 无写者举证 $stamp ===" | Out-File $ev -Encoding utf8
   "--- ① 计划任务（Status 不应为 Running）---" | Out-File $ev -Append -Encoding utf8
   schtasks /Query /TN QS_DaemonForever /FO LIST | Out-File $ev -Append -Encoding utf8
   "--- ② daemon 进程（应为空；forever 与 once 都算写者）---" | Out-File $ev -Append -Encoding utf8
   Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
     Where-Object { $_.CommandLine -match 'quantstudio\.pipeline\.daemon' } |
     Select-Object ProcessId, ExecutablePath, CommandLine | Out-File $ev -Append -Encoding utf8
   "--- ③ GUI 进程（应为空）---" | Out-File $ev -Append -Encoding utf8
   Get-Process | Where-Object { $_.ProcessName -match 'QuantStudio|pythonw' } |
     Select-Object Id, ProcessName | Out-File $ev -Append -Encoding utf8
   "=== 结束 ===" | Out-File $ev -Append -Encoding utf8
   Get-Content $ev
   ```

   **判据**：三节**都必须为空**（① 的 `Status` 不是 `Running`，②③ 无输出行）。
   任一仍有内容 ⇒ **停止，不要继续**。
   > 也请注意：任何会打开主库的入口（`--mode once`、GUI 里手动跑任务）在**备份与升级窗口内都不要执行**。

### 步骤 2 · **可举证的一致备份**（主库 + WAL 同一快照）

> **为什么不直接用 `check_wal.ps1 -BackupDb`**：该脚本的备份发生在 `CHECKPOINT` **之前**，
> 而 `CHECKPOINT` 会**改写主库并合并/清除 WAL**——备份完成后你无法再用「前后指纹一致」去**反证**
> 拷贝期间没有写入。升级备份要的是**可举证**，故本步骤改为下面的手工序列（WAL 收敛仍用 `check_wal.ps1`，见 §五）。

#### 2.1 取「拷贝前」指纹

```powershell
$db  = ".\data\quantstudio.db"
function Get-DbFp($p) {
  if (Test-Path $p) {
    $i = Get-Item $p
    "{0}|size={1}|mtime={2:yyyy-MM-dd HH:mm:ss.fff}" -f $i.Name, $i.Length, $i.LastWriteTime
  } else { "$(Split-Path $p -Leaf)|ABSENT" }
}
$fpBefore = @(Get-DbFp $db; Get-DbFp "$db.wal")
$fpBefore | ForEach-Object { "  BEFORE $_" }
```

#### 2.2 顺序拷贝（**先主库、后 WAL**；无写者前提下顺序不影响结果）

```powershell
$bak = "$db.bak.$stamp"
Copy-Item $db $bak -Force
if (Test-Path "$db.wal") { Copy-Item "$db.wal" "$db.wal.bak.$stamp" -Force; "WAL 已备份" } else { "无 WAL（跳过 WAL 备份）" }
```

> ⚠ **拷贝期间绝对不要启动 GUI 或任何采集入口**（62 GB 拷贝可能耗时数十分钟，
> 这段时间正是「无写者」最容易被破坏的窗口）。

#### 2.3 取「拷贝后」指纹并判定**是否同一快照**

```powershell
$fpAfter = @(Get-DbFp $db; Get-DbFp "$db.wal")
$fpAfter | ForEach-Object { "  AFTER  $_" }
if (($fpBefore -join "`n") -eq ($fpAfter -join "`n")) {
  "SNAPSHOT-CONSISTENT: 拷贝前后指纹完全一致 ⇒ 拷贝期间无写入，主库与 WAL 属同一静止状态"
} else {
  "SNAPSHOT-BROKEN: 指纹变化 ⇒ 拷贝期间有写入，备份作废！请删除 $bak 与 .wal.bak.$stamp 后从步骤 1 重做"
}
```

**判据（写在通知里，便于您自查）**：

| 现象 | 结论 |
|---|---|
| `BEFORE` 与 `AFTER` 两行**逐字相同**（size + mtime 到毫秒） | ✅ 拷贝期间内容静止 ⇒ 主库与 WAL 的**同一快照** |
| 任一 size 或 mtime 变化 | ❌ 备份作废，删除重做 |
| WAL 在 BEFORE 存在、AFTER 消失（或相反） | ❌ 备份作废，删除重做 |

> **判据的分辨力与局限（如实说明）**：该判据基于文件 `size + mtime(毫秒)`。
> duckdb 写入必然改动主库或 WAL 的这两项之一，故「前后一致」足以支撑「拷贝期间无写入」；
> 但它**不是**内容哈希比对（62 GB 全量 sha256 代价过高，未采用），也**不覆盖**「文件被替换成同大小同 mtime 的另一份」这类极端情形。
>
> **该判据已做过鉴别力三支自检**（证明它**会**在该报警时报警，不是恒真）：① 拷贝期间无写入 → `SNAPSHOT-CONSISTENT`；
> ② 拷贝期间主库被写（size 4096→8192、mtime 变化）→ `SNAPSHOT-BROKEN`；③ WAL 在拷贝前存在、拷贝后消失 → `SNAPSHOT-BROKEN`。
> 三支结果与预期一致，判据具备鉴别力。

#### 2.4 可恢复验证（**对真实备份执行**，不是对小样本）

```powershell
$bak = "$db.bak.$stamp"    # 换成实际文件名
& "<步骤 1.2 查到的解释器>" -c @"
import duckdb
c = duckdb.connect(r'''$bak''', read_only=True)
print('duckdb      =', c.execute('SELECT version()').fetchone()[0])
print('tables      =', c.execute('SELECT count(*) FROM duckdb_tables()').fetchone()[0])
print('stock_daily =', c.execute('SELECT count(*) FROM stock_daily').fetchone()[0])
print('cols        =', [r[1] for r in c.execute(\"PRAGMA table_info('stock_daily')\").fetchall()])
c.close()
print('RESTORE-CHECK OK')
"@ | Tee-Object -FilePath ".\data\upgrade-restorecheck-$stamp.txt"
```

**判据（三条全中）**：① 只读打开无异常；② `tables` 与 `stock_daily` 行数为正；③ `cols` 含预期列；末行 `RESTORE-CHECK OK`。

> **务必注意**：请把这条命令的**完整输出另存**（上面已用 `Tee-Object` 落盘）。
> **只有在您自己的真实主库上跑出 `RESTORE-CHECK OK`，才算「备份可恢复」被验证过。**
> 我们在开发环境用**小合成库**跑通这条命令，只证明**命令与判据本身可用**，
> **不能代替**您真实窗口的结果——**没跑过就等于没验证**。

### 步骤 3 · 升级代码

```powershell
git pull
```

### 步骤 4 · 升级依赖（**顺序不可颠倒**；**必须用步骤 1.2 的解释器**）

```powershell
& "<解释器绝对路径>" -m pip install "duckdb==1.5.6"
```

> 不要用 `pip install -U duckdb`；建议同批确认 `requirements.txt` 已钉 `duckdb==1.5.6`。

### 步骤 5 · 自检

```powershell
& "<解释器绝对路径>" -m quantstudio.pipeline.duckdb_version_gate --check
```

期望 `PASS 1.5.6`、退出码 0。读法见 §四。

### 步骤 6 · 恢复采集

```powershell
schtasks /Run /TN QS_DaemonForever
```

随后确认 daemon 起来、首日采集耗时正常（§六）、日志无 `Failed to delete all rows from index`。

> **本步骤不做 `CHECKPOINT`**：升级窗口内做 CHECKPOINT 会改写主库、合并 WAL，
> 既无必要（新版首次启动会自行 replay WAL），也会破坏「升级前现场」。WAL 收敛请走 §五。

### 步骤 7 · 留存举证包

把以下文件**放在一起留档**（出问题时我们第一个要看的就是它们）：

| 文件 | 由哪一步产生 |
|---|---|
| `data\upgrade-nolock-evidence-<stamp>.txt` | 步骤 1.3 无写者举证 |
| `data\quantstudio.db.bak.<stamp>` + `data\quantstudio.db.wal.bak.<stamp>`（如有 WAL） | 步骤 2.2 备份 |
| `SNAPSHOT-CONSISTENT` 那两行 BEFORE/AFTER 输出的截图或复制文本 | 步骤 2.3 一致性判定 |
| `data\upgrade-restorecheck-<stamp>.txt` | 步骤 2.4 可恢复验证 |
| 步骤 5 自检命令的完整输出 | 步骤 5 |

## 四、自检输出怎么读

| 输出 | 含义 | 您需要做什么 |
|---|---|---|
| `PASS 1.5.6`（退出码 0） | 版本在放行集合内 | 无需操作 |
| `PASS 1.4.5`（退出码 0） | 仍在 1.4.x，**同样在放行集合内** | 可正常跑；要升级按 §三 |
| `FAIL <版本>`（退出码 1） | 版本**不在放行集合** | 按 §三 步骤 4 升到 1.5.6，或按 §五 留在 1.4.5 |
| `ERROR …`（退出码 2） | 依赖缺失或版本串不可读 | 报运维，附**输出原文** |

启动时提示「拒绝启动」并打印版本与修复指引（退出码 3）⇒ 见 §1.1 第一行。

## 五、失败回退（任一步骤失败即用）

| 失败现象 | 回退动作 |
|---|---|
| 步骤 2.3 判定 `SNAPSHOT-BROKEN` | **删除该批 `.bak`，从步骤 1 重做**（检查是否有 GUI/采集被自动拉起） |
| 步骤 2.4 可恢复验证不通过 | **停止升级**，保留 `.bak` 原件，联系我们（附 `upgrade-restorecheck-*.txt`） |
| 步骤 4 装包失败 / 版本不对 | `& "<解释器>" -m pip install "duckdb==1.4.5"` → 重跑步骤 5，确认 `PASS 1.4.5` |
| 步骤 5 自检 `FAIL`/`ERROR` | 同上降回 1.4.5；**代码可保留新版**（新门允许 1.4.x），不必回退代码 |
| 恢复采集后启动即崩 | 停 daemon → 降回 1.4.5 → 重跑自检 → 发**完整报错原文** |
| 日志出现 `Failed to delete all rows from index` | 立即停采集、保留日志、降回 1.4.5，并**立即反馈** |
| WAL 异常 / 需处置陈旧 WAL | 用 **`.\check_wal.ps1 -RecoverStaleWal -Py "<解释器>"`**（会先备份再移走陈旧 WAL）；**禁止手工删除 `.wal`** |
| 升级后 WAL 偏大想收敛 | 用 `.\check_wal.ps1 -Force -Py "<解释器>"`（该脚本内含备份 + CHECKPOINT + 表结构校验） |

> 回退后**代码与依赖版本可以不一致**（新代码 + 1.4.5 允许），但**绝不建议**旧代码 + 1.5.6。
> **1.4.x 线上游支持于 2026-11-17 结束**。

## 六、性能提示（**仅覆盖实测表、实测数据与实测路径**）

### 6.1 实测条件（缺一不可外推）

| 项 | 实测条件 |
|---|---|
| 机器 | **单台物理机**（同盘、两侧不同 venv） |
| OS / 解释器 | Windows；**Python 3.11.9**（两侧同版本） |
| duckdb | 1.4.5 vs 1.5.6（仅此一项不同） |
| 数据库 | **合成库**（建表 DDL、语句、PK 结构逐字复刻；**数据为合成数据，非您的生产数据分布**） |
| 表与语句 | 表 `index_constituents_snapshot_meta`（10 列，`PRIMARY KEY(index_code, time)`）；语句 `executemany("INSERT … ON CONFLICT … DO UPDATE SET …")`（9 个 SET 列） |
| 取样 | 每档 **3 次取中位**，各档独立新库、独立子进程；单档 >90s 记超时并终止 |

### 6.2 读数（中位）

| 单批行数 | 1.4.5 | 1.5.6 | 倍数 |
|---|---|---|---|
| 300 行 | 0.642 s | **3.943 s** | **6.14×** |
| 1000 行 | 3.314 s | **13.396 s** | **4.04×** |
| 5000 行 | 15.436 s | **≥90 s（超时终止，未测到完成时刻）** | **≥5.83×（下界）** |

### 6.3 正确性结论的**准确范围**

在**这张表、这份合成数据、这一条 `executemany` 语句**上，两版的 `row_count` 与主键校验均通过。
⇒ 可以说「**该被测路径上写入结果无差异**」。
⇒ **不能**扩大为「所有写入结果无差异」「所有表都不受影响」——其它表、其它数据分布、其它写入形态
（如 `INSERT … SELECT`）、其它 OS/解释器组合**均未测**。

### 6.4 影响面与建议

仅影响上述 `executemany` 形态下的**该辅助表刷新时延**，不影响正确性。
若您的采集批次达到千行量级，建议放宽该任务调度超时，或观察首日采集耗时，必要时拆分批次。

## 七、常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 启动报「duckdb 不在放行集合」 | 依赖已升到 1.5.x 但**代码仍是旧版**（旧门按 `1.4.` 前缀校验，默认拒） | 停采集 → `git pull` → 重跑自检（先查 §二·附 是否设过逃生阀） |
| 装了 1.5.6 但自检仍显示 1.4.5 | **装到了另一个解释器/venv** | 按 §1.2 查出真实解释器，用 `<解释器> -m pip` 重装 |
| 自检报「无法导入 duckdb」 | 解释器选错或依赖缺失 | 用 daemon 实际使用的解释器执行；附输出原文 |
| 备份判定 `SNAPSHOT-BROKEN` | 拷贝期间有写入（GUI/采集被拉起） | 删除该批 `.bak`，从 §三 步骤 1 重做 |
| 某个表刷新变慢 | 见 §六（仅 `executemany` 形态实测） | 调超时或拆批次 |
| 日志出现 `Failed to delete all rows from index` | 命中 1.5.x 上游缺陷（`duckdb#23645`） | **立即反馈并附日志片段**；按 §五 降回 1.4.5 |

## 八、需要您反馈的情况

- 自检输出与 §四 表格**不一致**；
- 升级后采集报错、重复写入，或出现 §七 第 6 行日志文本；
- 备份/一致性判定/可恢复验证任一步不通过（请附 §三 步骤 7 的举证包）；
- 环境使用了**我们未覆盖的 duckdb 版本、解释器或 OS 组合**（附 `python -c "import sys,duckdb;print(sys.executable,duckdb.__version__)"` 输出）。

请在反馈中附：自检完整输出、解释器绝对路径、Python 与 duckdb 版本、报错日志片段、举证包文件。

---

## 修订说明（v3 → v4，2026-11-14/15 第三次退修；内部留痕，下发时可删除）

| # | 审核意见 | 本版落实 |
|---|---|---|
| 1 | 旧门拒启须限定父版本和默认配置 | **§二·附** 保持四条边界（核查对象=父版本；判据=`startswith("1.4.")`；默认配置实测 `(False,"1.5.6")`；逃生阀 `QS_DUCKDB_VERSION_GATE=0` 仅警告不阻断）；并写明「**不得断言所有客户旧代码/所有配置必拒**」 |
| 2 | **备份须证「无写者」** | §三 **步骤 1.3 新增无写者举证**：三节证据（计划任务/daemon 进程/GUI 进程）落盘 `upgrade-nolock-evidence-<stamp>.txt`，判据「三节均空」 |
| 3 | **备份须证「同一快照」** | §三 **步骤 2 重写为可举证序列**：2.1 取 BEFORE 指纹（主库+WAL 的 size+mtime 毫秒）→ 2.2 顺序拷贝 → 2.3 取 AFTER 指纹并**判定 `SNAPSHOT-CONSISTENT` / `SNAPSHOT-BROKEN`**（附判据表与**判据分辨力的局限**）；并说明**为何不用 `check_wal.ps1 -BackupDb`**（其备份在 CHECKPOINT 之前，CHECKPOINT 会改主库/WAL，无法事后反证快照一致） |
| 4 | **备份须证「可恢复」** | §三 **步骤 2.4** 对**真实备份**只读打开自检 + `Tee-Object` 落盘 `upgrade-restorecheck-<stamp>.txt`；判据三条 |
| 5 | **小合成库结果不代真实窗口** | §2.4 明确：「我们只用**小合成库**跑通命令与判据，**不能代替**您真实窗口的结果——**没跑过就等于没验证**」；§三 步骤 7 新增**举证包留存清单**（5 类文件） |
| 6 | （连带）升级窗口内不做 CHECKPOINT | 步骤 6 说明理由：会改写主库/合并 WAL、破坏升级前现场，且新版首次启动会自行 replay；WAL 收敛改由 §五 的 `check_wal.ps1 -Force` 承担 |

**口径来源（内部随附）**：
- 放行集合与判定源：`quantstudio/pipeline/duckdb_version_policy.py`
- 旧门举证：`git show fdca384^:quantstudio/pipeline/duckdb_version_gate.py`（`REQUIRED_SERIES="1.4"`、`startswith`、`GATE_ENV="QS_DUCKDB_VERSION_GATE"` 默认 `"1"`）
- 解释器实测：daemon PID 39200 `ExecutablePath=...\Python311\python.exe`
- 备份脚本顺序实证：仓库根 `check_wal.ps1` L117-128（备份）先于 L130-137（CHECKPOINT）
- 主库规模：`data/quantstudio.db` **62.06 GB**（2026-11-15 00:50 只读 stat）
- 性能量化：`docs/evidence/a14-executemany-scale-20261029.md`（含 §四 局限）
- `#23645` 暴露面核验：非唯一 ART 索引 = 0；41 个主键三臂验证不丢（`docs/evidence/pt01-f2-truncate-20261029.md`）
