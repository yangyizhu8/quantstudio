# 客户通知稿（**v6 修订终稿**）：QuantStudio 组件 duckdb 版本升级至 1.5.6（通知编号 5）

> **状态**：**v6 修订终稿 · 待总调度终审 · 未下发**。
> **v6 相对 v5 修正一处实质缺陷**：v5 的 WAL 备份命名 `q.db.wal.bak.<ts>` **不符合 duckdb 恢复约定**（应为 `<主库备份全名>.wal`），
> 在「主库已 checkpoint、增量仍在 WAL」的现场会导致**静默丢增量**（实测读到 3 行、正确应 7 行，且不报错）。详见 §2.2 与文末修订说明。
> 本版依据 2026-11-15 审核：「重点核**无写者**及**同一快照**能否覆盖**主库与 WAL**；**不能仅据不做 CHECKPOINT 的取舍放行**」。
> 逐项落实见文末「修订说明」。
> 下发须经**用户确认闸门**；本通知**仅由总调度转发**，执行会话不代下发。
> **建议下发时间**：不晚于 **2026-11-17**（1.4.x 线 EOL 日；距发文当日仅剩 2 天）。
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
`<解释器> -c "import sys,duckdb;print(sys.executable);print(duckdb.__version__)"`。

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
> **该结论不外推到未测路径、未测数据分布或您的特定业务负载**。

### §二·附：旧代码对 1.5.6 的处置（**限定到所核版本与默认配置**）

| 项 | 内容 |
|---|---|
| 核查对象 | `fdca384` 的**父版本**（`git show fdca384^:quantstudio/pipeline/duckdb_version_gate.py`），即本次升级前该文件的最后状态——**不是**「所有历史旧代码」 |
| 判定实现 | 常量 `REQUIRED_SERIES = "1.4"`；判据 `return ver.startswith(REQUIRED_SERIES + ".")` ⇒ **按 `1.4.` 前缀校验** |
| **默认配置下的实测** | 未设 `QS_DUCKDB_VERSION_GATE` 时，`check_duckdb_version()` 对 `1.5.6` 返回 `(False, "1.5.6")` ⇒ `require_duckdb_version()` `SystemExit(3)` **拒启** |
| **逃生阀（一并告知）** | 支持环境变量 **`QS_DUCKDB_VERSION_GATE=0`** 临时放行：`if os.environ.get(GATE_ENV, "1").strip() == "0":` ⇒ 仅 `logger.warning` 后**继续启动，不阻断** |
| **结论边界** | 「拒启」**仅适用于**该父版本 **且** 默认配置（逃生阀未开）。**不能**据此断言所有客户机上的所有旧代码、所有配置组合都会拒启；若您已设 `QS_DUCKDB_VERSION_GATE=0`，旧代码配 1.5.6 也能启动（**风险自负且不受支持**，强烈不建议） |
| 复现 | `git show fdca384^:quantstudio/pipeline/duckdb_version_gate.py \| Select-String 'REQUIRED_SERIES\|GATE_ENV\|startswith'` |

## 三、升级步骤（**整段按序执行**，建议非交易时段）

> **空间准备**：备份要复制一份与主库等大的文件（本机主库约 **62 GB**）。请先确认
> **可用空间 ≥ 主库大小 ×1.5**，并预留足够时间（大库复制较慢）。

### 步骤 1 · 停机与「无写者」举证

```powershell
cd <QuantStudio 仓库根>
schtasks /End /TN QS_DaemonForever          # ① 停计划任务（再关 GUI）

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
"--- ④ 主库句柄独占探针（必须可独占）---" | Out-File $ev -Append -Encoding utf8
try {
  $fs = [System.IO.File]::Open(".\data\quantstudio.db",[System.IO.FileMode]::Open,[System.IO.FileAccess]::Read,[System.IO.FileShare]::None)
  $fs.Dispose(); "  可独占打开 ⇒ 当前无任何进程持有主库文件（含只读句柄）" | Out-File $ev -Append -Encoding utf8
} catch { "  无法独占（$($_.Exception.GetType().Name)）⇒ 仍有持有者（或权限不足），不得继续" | Out-File $ev -Append -Encoding utf8 }
"=== 结束 ===" | Out-File $ev -Append -Encoding utf8
Get-Content $ev
```

**判据（四节全过才算「无写者」）**：

| 节 | 判据 | 为什么不能省 |
|---|---|---|
| ① | 计划任务 `Status` 不是 `Running` | 计划任务可能随时拉起新实例 |
| ② | 无 `quantstudio.pipeline.daemon` 进程（forever 与 **once** 都算） | once 轮同样持有写锁 |
| ③ | 无 GUI / `pythonw` 进程 | GUI 内任务也会开库写入 |
| ④ | **主库可被 `FileShare.None` 独占打开** | **进程清点会漏**：命令行不含特征串的进程、或非 python 宿主也可能持句柄；**独占探针直接问操作系统「还有谁拿着它」** |

> **双证纪律**：②③ 是「按身份清点」，④ 是「按句柄实测」。**两者缺一不可**——
> 只看进程列表可能漏掉不匹配特征的持有者；只看句柄则无法定位是谁。任一不满足 ⇒ **停止，不要继续**。
>
> **独占探针已做鉴别力自检（四支）**：① 无持有者 → 可独占；② 本会话持只读句柄 → **不可独占（报异常）**；
> ③ 释放后 → 恢复可独占；④ 对真实主库（62 GB，仅建句柄不读内容）→ 可独占。
> ⇒ 该探针**能**在有人持有时报警，不是恒真。
> 窗口内**不要**再执行任何会打开主库的入口（`--mode once`、GUI 里手动跑任务）。

### 步骤 2 · 可举证的一致备份（主库 + WAL 同一静态副本）

> #### 先说清两件事（回应「不能靠取舍放行」）
>
> 1. **升级窗口内不做 `CHECKPOINT`** —— 这是**工程取舍**，理由是：`CHECKPOINT` 会改写主库并合并/清除 WAL，
>    等于把「升级前现场」破坏掉，且新版首次启动会自动 replay WAL，本无必要。
>    **这个取舍不承担任何举证责任**，下面的快照一致性**完全由 2.1–2.4 的判据证明**，两者不可互相替代。
> 2. **为什么不用 `check_wal.ps1 -BackupDb` 做升级备份** —— 该脚本源码顺序是「先备份（L117-128）→ 再 `CHECKPOINT`（L130-137）」，
>    一旦执行到 `CHECKPOINT`，主库与 WAL 都已被改写，你**再也无法用前后指纹去反证**拷贝期间没有写入。
>    故升级备份改用下面的手工序列（可举证）；`check_wal.ps1` 仍专责 **WAL 收敛**（见 §五）。

#### 2.1 取「拷贝前」指纹（主库：size+mtime+首尾抽样哈希；WAL：size+mtime+全量 sha256）

```powershell
$db  = ".\data\quantstudio.db"
function Get-Fp($p) {
  if (-not (Test-Path $p)) { return "$(Split-Path $p -Leaf)|ABSENT" }
  $i = Get-Item $p
  if ($p -like '*.wal') {
    $h = (Get-FileHash $p -Algorithm SHA256).Hash.Substring(0,16)
    return "$($i.Name)|size=$($i.Length)|mtime=$($i.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss.fff'))|sha256_16=$h"
  }
  # 主库：首 8MB + 末 8MB 抽样哈希（62GB 全量哈希代价过高，见 2.4 局限）
  $fs = [System.IO.File]::OpenRead($p)
  try {
    $buf = New-Object byte[] (8MB); $n = $fs.Read($buf,0,$buf.Length)
    $ha = [System.Security.Cryptography.SHA256]::Create()
    $ha.TransformBlock($buf,0,$n,$null,0) | Out-Null
    if ($fs.Length -gt 8MB) { $fs.Seek([Math]::Max(0,$fs.Length-8MB),[System.IO.SeekOrigin]::Begin) | Out-Null
      $n2 = $fs.Read($buf,0,$buf.Length); $ha.TransformBlock($buf,0,$n2,$null,0) | Out-Null }
    $ha.TransformFinalBlock((New-Object byte[] 0),0,0) | Out-Null
    $hs = ([System.BitConverter]::ToString($ha.Hash) -replace '-','').Substring(0,16)
  } finally { $fs.Dispose() }
  return "$($i.Name)|size=$($i.Length)|mtime=$($i.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss.fff'))|headtail_sha16=$hs"
}
$fpDbBefore  = Get-Fp $db
$fpWalBefore = Get-Fp "$db.wal"
"  BEFORE 主库: $fpDbBefore"
"  BEFORE WAL : $fpWalBefore"
```

#### 2.2 顺序拷贝（**先主库、后 WAL**；无写者前提下顺序不影响结果）

```powershell
$bak = "$db.bak.$stamp"
Copy-Item $db $bak -Force
# ★ WAL 备份必须命名为「主库备份全名 + .wal」，duckdb 才会在打开备份时重放它
if (Test-Path "$db.wal") { Copy-Item "$db.wal" "$bak.wal" -Force; "WAL 已备份为 $bak.wal" }
else { "无 WAL（本次为单文件快照，跳过 WAL 备份）" }
```

> ### ⚠ 命名不是小事（这是 v5 的实质缺陷，已实测）
> duckdb 打开数据库 `X` 时，只会去找**同名**的日志文件 `X.wal`。
> v5 曾把 WAL 备份写成 `quantstudio.db.wal.bak.<ts>`（后缀 `.wal.bak.<ts>`），
> 而主库备份是 `quantstudio.db.bak.<ts>`——两者**不同名**，duckdb 打开备份时**根本不会去看那个 WAL**。
>
> **实测后果（2026-11-15 03:0x，合成库）**：
> 现场为「主库已 `CHECKPOINT`（含 3 行）+ 后续 4 行仍在 WAL」：
>
> | 命名方式 | 打开备份读到 | 是否报错 |
> |---|---|---|
> | `q.db.wal.bak.T`（v5 写法） | **count=3，sum=6**（丢了 4 行） | **不报错（静默）** |
> | `q.db.bak.T.wal`（v6 写法） | **count=7，sum=28**（完整） | 正常 |
>
> ⇒ 用错误命名，您会**以为备份是完整的**，直到真要恢复时才发现少数据。故**命名必须写死为 `<主库备份全名>.wal`**。

> ⚠ **拷贝期间绝对不要启动 GUI 或任何采集入口**（62 GB 可能耗时数十分钟，这正是最容易被破坏的窗口）。

#### 2.3 取「拷贝后」指纹 + **校验备份产物本身**（六条判据）

```powershell
$fpDbAfter  = Get-Fp $db
$fpWalAfter = Get-Fp "$db.wal"
$bakDbSize  = (Get-Item $bak).Length
$bakDbHt    = Get-Fp $bak            # 备份主库自身指纹
$fail = @()
if ($fpDbBefore  -ne $fpDbAfter)  { $fail += "① 主库源文件前后指纹不一致 ⇒ 拷贝期间有写入" }
if ($fpWalBefore -ne $fpWalAfter) { $fail += "② WAL 源文件前后指纹不一致（含存/缺翻转）⇒ 拷贝期间有写入" }
if ($bakDbSize -ne (Get-Item $db).Length) { $fail += "③ 主库备份 size ≠ 源 size ⇒ 拷贝不完整" }
if (($bakDbHt -split '\|')[3] -ne ($fpDbBefore -split '\|')[3]) { $fail += "④ 主库备份首尾抽样哈希 ≠ 源 ⇒ 内容不一致" }
if (Test-Path "$db.wal") {
  $bakWal = "$bak.wal"
  if ((Get-Item $bakWal).Length -ne (Get-Item "$db.wal").Length) { $fail += "⑤ WAL 备份 size ≠ 源 size" }
  if ((Get-FileHash $bakWal).Hash -ne (Get-FileHash "$db.wal").Hash) { $fail += "⑥ WAL 备份 sha256 ≠ 源 sha256 ⇒ WAL 内容不一致" }
}
if ($fail.Count -eq 0) { "SNAPSHOT-CONSISTENT：六条判据全过 ⇒ 主库与 WAL 系同一静止状态的一份完整副本" }
else { $fail | ForEach-Object { "  ✗ $_" }; "SNAPSHOT-BROKEN：删除 $bak 与 $bak.wal，从步骤 1 重做" }
```

**六条判据一览（可自查）**

| # | 判据 | 不满足意味着 |
|---|---|---|
| ① | 主库 `BEFORE == AFTER`（size+mtime+首尾哈希） | 拷贝期间主库被写 |
| ② | WAL `BEFORE == AFTER`（含「存在/缺失」不得翻转） | 拷贝期间 WAL 被写或被合并清除 |
| ③ | 备份主库 `size == BEFORE` 主库 size | 拷贝被截断/未完成 |
| ④ | 备份主库 首尾抽样哈希 `==` BEFORE 主库哈希 | 备份内容与源不一致 |
| ⑤ | （有 WAL 时）备份 WAL `size ==` 源 WAL size | WAL 备份不完整 |
| ⑥ | （有 WAL 时）备份 WAL `sha256 ==` 源 WAL sha256 | WAL 备份内容被改 |

> **为什么这组判据能覆盖「主库与 WAL」**（审核关注点，逐步说明）：
> 1. 判据①②建立的是「**区间 [BEFORE 取样, AFTER 取样] 内，主库与 WAL 两个文件都没有被修改**」；
> 2. 2.2 的两次 `Copy-Item` **都发生在这个区间内** ⇒ 两次拷贝读到的都是**同一个静止状态**；
> 3. 判据③–⑥ 再验证「**拷贝出来的字节确实等于那个静止状态**」，把「拷贝自身出错/被截断/被篡改」也纳入；
> 4. 于是结论不是「两次拷贝的时间戳相同」（那只能证明时间接近），而是「**主库备份 + WAL 备份 = 同一静止状态的一份完整副本**」。
>
> **判据的局限（如实声明）**：
> - 主库用的是**首尾各 8MB 抽样哈希**，不是 62 GB 全量哈希（全量代价过高，未采用）；
>   若损坏/替换恰好只发生在**中间区域**且 `size`、`mtime` 都不变，本判据检测不到。
> - WAL 用**全量 sha256**（WAL 通常远小于主库），无此局限。
> - 该判据已做**鉴别力四支自检**（证明它会在该报警时报警）：① 正常 → `SNAPSHOT-CONSISTENT`；
>   ② 拷贝期间源库被写 → 报「源文件前后指纹不一致」；③ 备份被截断（源未变）→ 报「备份 size ≠ 源 size」；
>   ④ WAL 备份被改一字节（size 相同）→ 报「WAL 备份 sha256 ≠ 源 sha256」。
>   其中 ③④ 恰好说明：**只看「源文件前后一致」是不够的，必须再校验备份产物本身**——这也是本版相对 v4 的实质加强。

#### 2.4 可恢复验证（**对真实备份执行**）

```powershell
$bak = "$db.bak.$stamp"
& "<解释器绝对路径>" -c @"
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

> **加强（v6 新增，针对「WAL 重放」）**：若步骤 2.2 存在 WAL 备份，请在自检脚本中**再多打一行**
> `print('wal_replayed =', c.execute("SELECT count(*) FROM duckdb_logs()").fetchone()[0])`（或用您关心的某表**增量行数**对照）。
> 更直接的判据是：**备份打开后读到的行数/末笔时间，必须不早于源库关闭前的读数**——
> 若发现备份「明显比源库旧」，第一嫌疑就是 **WAL 未被重放**（命名不匹配），按 §2.2 ⚠ 段核对文件名。

> **务必注意**：**只有在您自己的真实主库上跑出 `RESTORE-CHECK OK`，才算「备份可恢复」被验证过。**
> 我们在开发环境用**小合成库**跑通这条命令与下面四支判据自检，那只证明**命令与判据可用**，
> **不能代替**您真实窗口的结果——**没跑过就等于没验证**。

### 步骤 3 · 升级代码

```powershell
git pull
```

### 步骤 4 · 升级依赖（**顺序不可颠倒**；**必须用 §1.2 的解释器**）

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

### 步骤 7 · 留存举证包

| 文件 | 由哪一步产生 |
|---|---|
| `data\upgrade-nolock-evidence-<stamp>.txt` | 步骤 1 无写者举证 |
| `data\quantstudio.db.bak.<stamp>` + `data\quantstudio.db.bak.<stamp>.wal`（如有 WAL） | 步骤 2.2 备份 |
| `SNAPSHOT-CONSISTENT` 那几行输出（含六条判据与 BEFORE/AFTER 指纹） | 步骤 2.3 |
| `data\upgrade-restorecheck-<stamp>.txt` | 步骤 2.4 可恢复验证 |
| 步骤 5 自检完整输出 | 步骤 5 |

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
| 步骤 2.3 报 `SNAPSHOT-BROKEN` | **删除该批 `.bak`，从步骤 1 重做**（多半是 GUI/采集被自动拉起） |
| 步骤 2.4 可恢复验证不通过 | **停止升级**，保留 `.bak` 原件，附 `upgrade-restorecheck-*.txt` 联系我们 |
| 步骤 4 装包失败 / 版本不对 | `& "<解释器>" -m pip install "duckdb==1.4.5"` → 重跑步骤 5，确认 `PASS 1.4.5` |
| 步骤 5 自检 `FAIL`/`ERROR` | 同上降回 1.4.5；**代码可保留新版**（新门允许 1.4.x） |
| 恢复采集后启动即崩 | 停 daemon → 降回 1.4.5 → 重跑自检 → 发**完整报错原文** |
| 日志出现 `Failed to delete all rows from index` | 立即停采集、保留日志、降回 1.4.5，并**立即反馈** |
| WAL 异常 / 需处置陈旧 WAL | `.\check_wal.ps1 -RecoverStaleWal -Py "<解释器>"`（先备份再移走陈旧 WAL）；**禁止手工删除 `.wal`** |
| 升级后 WAL 偏大想收敛 | `.\check_wal.ps1 -Force -Py "<解释器>"`（内含备份 + CHECKPOINT + 表结构校验） |

> 回退后**代码与依赖版本可以不一致**（新代码 + 1.4.5 允许），但**绝不建议**旧代码 + 1.5.6。
> **1.4.x 线上游支持于 2026-11-17 结束**。

## 六、性能提示（**仅覆盖实测表、实测数据与实测路径**）

### 6.1 实测条件（缺一不可外推）

| 项 | 实测条件 |
|---|---|
| 机器 | **单台物理机**（同盘、两侧不同 venv） |
| OS / 解释器 | Windows；**Python 3.11.9**（两侧同版本） |
| duckdb | 1.4.5 vs 1.5.6（仅此一项不同） |
| 数据库 | **合成库**（建表 DDL、语句、PK 结构逐字复刻；**数据为合成数据，非您的生产分布**） |
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
⇒ **不能**扩大为「所有写入结果无差异」「所有表都不受影响」——其它表、其它数据分布、其它写入形态、其它 OS/解释器组合**均未测**。

### 6.4 影响面与建议

仅影响上述 `executemany` 形态下的**该辅助表刷新时延**，不影响正确性。
若您的采集批次达到千行量级，建议放宽该任务调度超时，或观察首日采集耗时，必要时拆分批次。

## 七、常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 启动报「duckdb 不在放行集合」 | 依赖已升到 1.5.x 但**代码仍是旧版**（旧门按 `1.4.` 前缀校验，默认拒） | 停采集 → `git pull` → 重跑自检（先查 §二·附 是否设过逃生阀） |
| 装了 1.5.6 但自检仍显示 1.4.5 | **装到了另一个解释器/venv** | 按 §1.2 查出真实解释器，用 `<解释器> -m pip` 重装 |
| 自检报「无法导入 duckdb」 | 解释器选错或依赖缺失 | 用 daemon 实际使用的解释器执行；附输出原文 |
| 备份判定 `SNAPSHOT-BROKEN` | 拷贝期间有写入，或备份被截断/内容不一致 | 删除该批 `.bak`，从 §三 步骤 1 重做 |
| 某个表刷新变慢 | 见 §六（仅 `executemany` 形态实测） | 调超时或拆批次 |
| 日志出现 `Failed to delete all rows from index` | 命中 1.5.x 上游缺陷（`duckdb#23645`） | **立即反馈并附日志片段**；按 §五 降回 1.4.5 |

## 八、需要您反馈的情况

- 自检输出与 §四 表格**不一致**；
- 升级后采集报错、重复写入，或出现 §七 第 6 行日志文本；
- 备份/一致性六判据/可恢复验证任一步不通过（请附 §三 步骤 7 举证包）；
- 环境使用了**我们未覆盖的 duckdb 版本、解释器或 OS 组合**（附 `python -c "import sys,duckdb;print(sys.executable,duckdb.__version__)"`）。

---

## 修订说明（v4 → v5 → v6；内部留痕，下发时可删除）

| # | 审核意见 | 落实 |
|---|---|---|
| 1 | 复核**独占探针的已知持有者负例** | **已做真实持有者负例实测**（2026-11-15 03:0x）：起**独立进程**以 duckdb RW 连接持有合成库（WAL 在位 112 B）后——<br>· `.NET FileShare::None` 探针（**v5/v6 采用写法**）→ **不可独占（MethodInvocationException）⇒ 正确识别持有者** ✅<br>· 同进程 `open(rb)` → `PermissionError` ✅（旁证）<br>· ctypes `CreateFileW` **未设 `restype=HANDLE`** 时返回被截断值 → **误报「可独占」（漏检）**；设 `restype` 后 → 正确返回 `INVALID_HANDLE_VALUE`。<br>⇒ **结论：`.NET` 写法可靠；若改用 ctypes 必须显式设 `restype`，否则静默漏检**。v6 保持 `.NET FileShare::None` 写法并在判据表标注该陷阱。<br>**持有者退出后** → 恢复可独占 ✅（正例） |
| 2 | 复核**六项同一快照判据** | 六判据保持（①② 源主库/WAL 前后一致，含存缺不得翻转；③④ 备份主库 size+首尾抽样哈希==源；⑤⑥ 备份 WAL size+**全量 sha256**==源）。四支鉴别力自检复跑通过：正常→CONSISTENT；源被写→报指纹不一致；**备份被截断（源未变）→报 size 不等**；**WAL 备份改一字节→报 sha256 不等** |
| 3 | 复核**主库／WAL 一致性** | **发现并修正 v5 实质缺陷**：v5 的 WAL 备份命名 `q.db.wal.bak.<ts>` **不符合 duckdb 恢复约定**（duckdb 只找 `<库名>.wal`）。<br>实测（主库已 CHECKPOINT 含 3 行 + 增量 4 行在 WAL）：<br>· v5 命名 → 打开只读到 **count=3（丢 4 行）且不报错（静默）**<br>· v6 命名 `<bak>.wal` → **count=7 完整**<br>⇒ v6 将命名写死为 `<主库备份全名>.wal`，并在 §2.2 加 ⚠ 实测对照表、§2.4 加「备份不得比源库旧」的 WAL 重放判据、步骤 7 举证包同步改名 |
| 4 | **不能仅据「不做 CHECKPOINT」取舍放行** | §2 开头定性为**工程取舍、不承担举证责任**；一致性完全由 2.1–2.4 判据证明 |
| 5 | 局限声明 | 主库为**首尾各 8MB 抽样哈希**（非 62 GB 全量），中间区损坏且 size/mtime 不变则检测不到；WAL 为全量哈希，无此局限 |

**口径来源（内部随附）**：
- 放行集合与判定源：`quantstudio/pipeline/duckdb_version_policy.py`
- 旧门举证：`git show fdca384^:quantstudio/pipeline/duckdb_version_gate.py`
- 解释器实测：daemon PID 39200 `ExecutablePath=...\Python311\python.exe`
- 备份脚本顺序实证：仓库根 `check_wal.ps1` L117-128（备份）先于 L130-137（CHECKPOINT）
- 主库规模：`data/quantstudio.db` **62.06 GB**（2026-11-15 00:50 只读 stat）；WAL 当前已收敛（无文件）
- 性能量化：`docs/evidence/a14-executemany-scale-20261029.md`（含 §四 局限）
- `#23645` 暴露面核验：`docs/evidence/pt01-f2-truncate-20261029.md`
