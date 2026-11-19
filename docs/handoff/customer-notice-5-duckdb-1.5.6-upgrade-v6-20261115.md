# 客户通知稿（**v6 修订终稿**）：QuantStudio 组件 duckdb 版本升级至 1.5.6（通知编号 5）

> **状态**：**v6 修订候选稿 · 待总调度终审 · 未下发 · 非已批准下发版 · 本轮未提交**。
> **S0b 已于 2026-11-16 19:59:17 通过（stamp `20261116T195917`）—— §2.4.10 的有限合成验证关闭。**
> **执行件**：已审 r4 候选 `DAFAB11E…A24C0`，与批准值**逐位一致**；宿主 **PS 5.1.26100.9711**、解释器 `Python311`，**均未更换**。
> **13 个阶段全部 rc=0，verdict=PASS，退出码 0。**
> **①新证据根先于 HOST**：`STAGE BOOTSTRAP` 在建根之前排他新建证据目录并立即落 `runner-identity.txt`。
> **②原生命令先完整取输出并保存 rc**：`$pyVerOut = @(& $py -c …)` → `$pyRunRc = $LASTEXITCODE`（实测 `PY-RUN-RC = 0`）→ 再从变量取首行。
> **③五值实测全对**：`scanRc=0` / `SCAN-LINKS 0` / `SCAN-DIRS 1` / `SCAN-ROOT-ENTRIES 3` / `WALK-ERRORS 0` / `LSTAT-ERRORS 0`。
> **④PS1 BOM 与目标 Parser 均过**：`GATE-BOM = EF BB BF`；`GATE-PARSE = OK（宿主自身 Parser，读盘）`。
> **⑤基线消费闸接受**：输出 `PY-SRC-LINKS  = 0（dirs=1, root_entries=3, walk_err=0, lstat_err=0…）`，未抛异常。
> **⑥六负控 6/6 按预期原因被拒**，且**跑完后重跑基线仍通过**（还原有效）。
> **⑦独立复算（不信自报）**：根实测 3 文件 / 0 子目录 / 0 重解析点；扫描器提取件 2165 B 与通知 L425..L481 **二进制逐字节一致**；
> 消费闸提取件 2209 B（含 BOM）与 L488..L507 **逐字节一致**；根目录 `st_file_attributes=0x10`（**非 ReparsePoint**）。
> **⑧按边界未清理**：`143939` / `154753` / `195917` 三处现场与候选目录**全部在位**；未建 junction、未改 ACL、未复制环境、未 pip。
> **T041 仍 BLOCKED**；**62 GB 未测**；**11-17 原闸保持**。**本轮未 commit、未推送**。
> **S0b 关闭 ≠ 升级可执行**：它只证明「合法平根上，扫描器五值与消费闸按设计工作」，**不是**对 62 GB 生产库或升级窗口的验证。

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

### 步骤 0 · 会话变量（**`$stamp` 全篇只在这里生成一次**）

> **为什么单列一步**：本通知所有产物——无写者举证、备份（`$bak`）、基线、隔离副本、验证日志、
> 通行证 / 阻断标记——**全部用同一个 `$stamp` 命名**。若中途再执行一次 `Get-Date`，
> **跨秒就会得到另一个 stamp**，后续步骤便会去找**另一份根本不存在（或另一时刻）的备份**。
> 这与上一版 `{stamp}` 缺陷是同一类错误：**命名源不唯一**。
> ⇒ **`$stamp` 只在步骤 0 生成一次**；此后**任何小节只引用、不重定义**（本通知已无第二处定义）。

```powershell
cd <QuantStudio 仓库根>
$dataDir = (Resolve-Path '.\data').Path
$py      = '<解释器绝对路径>'          # 必须与 §1.2 已确认的是同一个 python
$stamp   = Get-Date -Format 'yyyyMMdd_HHmmss'

# 立即把 stamp 落成「唯一权威」，供重开窗口 / 分段执行时找回（不得手改）
# 统一用 UTF-8 无 BOM + 读回比对（stamp 本身是纯数字下划线，ASCII 也无损；此处统一口径，
# 免去"某处 ASCII 某处 UTF-8"的分歧——后面 pypin 里含中文路径，绝不能 ASCII）
$stampFile = Join-Path $dataDir 'upgrade-stamp.txt'
$utf8s = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($stampFile, "$stamp", $utf8s)
if (([System.IO.File]::ReadAllText($stampFile, $utf8s)).Trim() -ne "$stamp") {
    throw "stamp 文件读回不一致，拒绝继续：$stampFile"
}
"STAMP       = $stamp"
"STAMP-FILE  = $stampFile"
"DATA-DIR    = $dataDir"
"PY          = $py"
```

> **重开窗口 / 分段执行时**（**不得**重新 `Get-Date`）：
> ```powershell
> cd <QuantStudio 仓库根>
> $dataDir = (Resolve-Path '.\data').Path
> $py      = '<解释器绝对路径>'
> $stamp   = (Get-Content (Join-Path $dataDir 'upgrade-stamp.txt') -Raw).Trim()
> "STAMP (recovered) = $stamp"
> ```

### 步骤 1 · 停机与「无写者」举证

```powershell
schtasks /End /TN QS_DaemonForever          # ① 停计划任务（再关 GUI）
# $stamp / $dataDir / $py 已由步骤 0 定义，本节不重定义
$ev = Join-Path $dataDir "upgrade-nolock-evidence-$stamp.txt"
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

#### 2.3 取「拷贝后」指纹 + 校验备份产物本身（六条判据）

```powershell
$fpDbAfter  = Get-Fp $db
$fpWalAfter = Get-Fp "$db.wal"
$bakDbSize  = (Get-Item $bak).Length
$bakDbHt    = Get-Fp $bak
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
if ($fail.Count -eq 0) { "COPY-FIDELITY-OK：六条判据全过 ⇒ 备份字节与源一致、且所测层面源文件在拷贝期间未变（注意：这不是『副本完整』的证明，见下）" }
else { $fail | ForEach-Object { "  ✗ $_" }; "SNAPSHOT-BROKEN：删除 $bak 与 $bak.wal，从步骤 1 重做" }
```

| # | 判据 | 不满足意味着 |
|---|---|---|
| ① | 主库 `BEFORE == AFTER`（size+mtime+**首尾各 8MB** 抽样哈希） | 拷贝期间主库在首/尾/大小/时间任一处有变化 |
| ② | WAL `BEFORE == AFTER`（含「存在/缺失」不得翻转） | 拷贝期间 WAL 被写或被合并清除 |
| ③ | 备份主库 `size == BEFORE` 主库 size | 拷贝被截断/未完成 |
| ④ | 备份主库 首尾抽样哈希 `==` BEFORE 主库哈希 | 备份首/尾 8MB 或大小与源不一致 |
| ⑤ | （有 WAL 时）备份 WAL `size ==` 源 WAL size | WAL 备份不完整 |
| ⑥ | （有 WAL 时）备份 WAL `sha256 ==` 源 WAL sha256 | WAL 备份内容被改 |

> ### ⚠ 这六条判据**证不到**什么（务必读）
> **① 不能证明「主库中间区域未变」**——主库只取**首尾各 8MB** 抽样哈希（62 GB 全量代价过高）。
> 若写入恰好发生在**中间区域**且 `size`/`mtime` 都不变，判据①会**照样通过**。
> ⇒ 准确表述只能是「在 size / mtime / 首尾各 8MB 这几个所测层面，源文件在拷贝期间未观察到变化」，
> **不能**说「区间内主库完全静止」。
>
> **② 不能证明「主库备份 + WAL 备份 = 一份完整副本」**——本版**删除**该结论。六判据只证明**拷贝忠实性**。
>
> **③ 不能替代恢复验证**——判据全过仍可能「打开副本比源库少数据」（WAL 命名错配就是这样）。

#### 2.4 建立「同基线指纹」并在隔离副本上比对恢复等价性

> 本节修复两处**确定性缺陷**（2026-11-15 终审指出）：
> **(a)** 旧稿输出路径写作字面量 `{stamp}`、消费端读展开后的 `$stamp` ⇒ 文件名对不上、必然找不到文件。
> 本版**生产端与消费端一律用同一 PowerShell 变量**，路径**显式作为参数**传入，并做**端到端断言**。
> **(b)** 旧稿用 `SELECT * ... ORDER BY ALL` + `fetchall()` 做整表内容指纹 ⇒ 62 GB 未验证、整表物化 + 排序必爆内存。
> 本版改为**有界流式聚合**，见 2.4.6。

##### 2.4.0 前置：**留存「原版本解释器」**（唯一配方 + 机器可用性判据）

**为什么必须前置**：版本门要求「基线」与「验证」**同版本同 `source_id`**（见下方版本门）。
步骤 4 会把 `duckdb` 就地升到 1.5.6 —— **同一个 `$py` 升级后必然不再是 1.4.5**。
所以必须在升级前留存原版本环境，否则升级后**无法走同版本链**，恢复验证只能落
`CROSS-VERSION-INCOMPARABLE`（exit=5，不判成功）。

> **`$stamp` / `$dataDir` / `$py` 一律来自步骤 0**，本节**不重定义**（重定义就会跨秒换名）。

**唯一配方：把当前解释器环境整体复制到 `.\data\pykeep-$stamp`，复制后必须通过机器可用性验证。**

```powershell
# ---- (0) 编码策略（本轮定稿，最简）----
# 三条：①文本由 Python 以 UTF-8 直写文件；②PS 侧用 **ReadAllText(UTF-8)** 直接读文件，
# 绝不把 JSON 经子进程 stdout 交回；③关键字段走纯 ASCII(hex) 对拍。
# 上轮的错：校验用了 hex，但取对象时又 `& $py -c "...ensure_ascii=False...print..."` 经 stdout 回来，
# 等于把"已经验过的链"在最后一步又过一遍会损坏的解码 —— 本轮彻底去掉这条路。
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$utf8 = New-Object System.Text.UTF8Encoding($false)

# ---- (1) 路径策略：**逐级拒绝链接**（不解析、不跳转）----
# 上轮的错：解析 reparse 得到"真实路径"后，**新路径的祖先仍可能是链接**，
# while 只在跳转后检查自身、并不重新逐段校验 —— 仍有绕过面。
# 本轮改为最简单且可证的方案：**从根到末级逐级检查，任一级是 reparse 就拒绝**；
# 不解析、不跳转 ⇒ 不存在"跳转后祖先未校验"的问题。
# 代价：若您的环境本身含 junction（某些 OneDrive / ProgramFiles 布局），会被直接拒绝。
#       这是有意的：那种链路上无法用本配方证明"留存独立"。
# ⚠ 本判据只是**检查时刻的静态快照**，不等于"运行期路径不变"：
#   检查与下面 robocopy 之间目录仍可能被改成 junction（TOCTOU），本脚本**不检测该竞态**。
#   故结论口径只能是「**检查时刻**全链无重解析点」，不得写成「运行期路径稳定」。

# 路径形式闸：只接受**盘根绝对形式**（D:\...）。拒绝三类语义不确定的输入：
#   ① 驱动器相对形式（D:foo / D:）—— GetFullPath 按「该盘的当前目录」解释，指向何处取决于进程状态；
#   ② 非绝对路径（foo\bar）—— 取决于当前工作目录；
#   ③ UNC（\\server\share\...）—— 本判据不覆盖，避免制造"已查过"的错觉。
function Assert-AbsoluteDrivePath([string]$p) {
    if ([string]::IsNullOrWhiteSpace($p)) { throw "路径为空，拒绝继续。" }
    if ($p -match '^[A-Za-z]:(?![\\/])') {
        throw "路径是驱动器相对形式（$p）：其含义取决于「该盘的当前目录」，不是固定位置。请改为全限定路径（如 D:\...）。"
    }
    if (-not [System.IO.Path]::IsPathRooted($p)) { throw "路径不是绝对路径（$p），拒绝继续。" }
    $full = [System.IO.Path]::GetFullPath($p)
    if ($full.StartsWith('\\')) { throw "UNC 路径不在本判据内（$full），拒绝继续。" }
    if ($full -notmatch '^[A-Za-z]:\\') { throw "规范化后仍非盘根绝对形式（$full），拒绝继续。" }
    return $full
}

function Test-ChainNoReparse([string]$p, [switch]$AllowMissingLeaf) {
    $full = Assert-AbsoluteDrivePath $p
    # 盘根必须**原样保留**（形如 D:\）。上一版写成 `$root.TrimEnd('\')`，
    # 对 `D:\` 会得到 `D:` —— 那不是绝对路径，是**驱动器相对**形式（含义取决于该盘当前目录），
    # 与本函数自己的形式闸自相矛盾。这里不做任何裁剪。
    $root = [System.IO.Path]::GetPathRoot($full)
    $segs = @($full.Substring($root.Length).Split('\') | Where-Object { $_ -ne '' })
    $cur = $root
    for ($i = 0; $i -lt $segs.Count; $i++) {
        $cur = Join-Path $cur $segs[$i]
        $isLeaf = ($i -eq $segs.Count - 1)
        if (-not (Test-Path -LiteralPath $cur)) {
            if ($isLeaf -and $AllowMissingLeaf) { return $cur }
            return $null
        }
        $it = Get-Item -LiteralPath $cur -Force
        if ($it.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "路径链上存在重解析点（junction/symlink）：$cur —— 拒绝继续（本配方要求全链无链接）。"
        }
    }
    # 出口再断言一次：结果必须仍是**盘根绝对形式**（防任何规范化把 D:\ 退化成 D:）。
    if ($cur -notmatch '^[A-Za-z]:\\') { throw "路径链检查结果为非盘根绝对形式（$cur），拒绝继续。" }
    return $cur
}

# ---- (2) 采集原身份：Python 直写 UTF-8 文件；命令行只回显 ASCII(hex) ----
$pyPin = Join-Path $dataDir "pypin-$stamp.json"
$PY_COLLECT = @'
import sys, json, hashlib, duckdb
c = duckdb.connect()
r = c.execute("SELECT * FROM pragma_version()").fetchone()
d = {"python": sys.version.split()[0], "exe": sys.executable, "prefix": sys.prefix,
     "duckdb": duckdb.__version__, "source_id": r[1], "duckdb_file": duckdb.__file__}
s = json.dumps(d, ensure_ascii=False, sort_keys=True)
open(sys.argv[1], "w", encoding="utf-8", newline="\n").write(s)
print("META-HEX " + hashlib.sha256(s.encode("utf-8")).hexdigest())
print("PFX-HEX  " + hashlib.sha256(sys.prefix.encode("utf-8")).hexdigest())
'@
$collectPy = Join-Path $dataDir "_pyid_collect.py"
[System.IO.File]::WriteAllText($collectPy, $PY_COLLECT, $utf8)
$metaOut = & $py $collectPy $pyPin
if ($LASTEXITCODE -ne 0 -or -not $metaOut) { throw "采集原身份失败（exit=$LASTEXITCODE）" }
$metaOut
$META_HEX = (($metaOut | Select-String '^META-HEX').Line -split '\s+')[1]
$PFX_HEX0 = (($metaOut | Select-String '^PFX-HEX').Line -split '\s+')[1]
if (-not $META_HEX -or -not $PFX_HEX0) { throw "未取得 META-HEX / PFX-HEX，拒绝继续。输出：$metaOut" }

# ---- (3) PS 侧读对象：**直接读文件（UTF-8）**，不经子进程 stdout ----
$pyObj = ([System.IO.File]::ReadAllText($pyPin, $utf8)) | ConvertFrom-Json
"PY-PINNED     = $pyPin"
"PY-META-HEX   = $META_HEX"
"PY-PFX-HEX    = $PFX_HEX0"
"PY-PYTHON     = $($pyObj.python)"
"PY-DUCKDB     = $($pyObj.duckdb)"
"PY-SOURCE-ID  = $($pyObj.source_id)"

# ---- (4) 文件字节对拍：Python 读文件复算，证明写入链无损 ----
$PY_RT = @'
import sys, json, hashlib
b = open(sys.argv[1], "rb").read()
print("FILE-HEX " + hashlib.sha256(b).hexdigest())
d = json.loads(b.decode("utf-8"))
print("PFX-HEX  " + hashlib.sha256(d["prefix"].encode("utf-8")).hexdigest())
print("FIELDS   " + ",".join(sorted(d.keys())))
'@
$rtPy = Join-Path $dataDir "_pyid_check.py"
[System.IO.File]::WriteAllText($rtPy, $PY_RT, $utf8)
$rtOut = & $py $rtPy $pyPin
if ($LASTEXITCODE -ne 0) { throw "文件字节对拍失败（exit=$LASTEXITCODE）" }
$rtOut
$FILE_HEX = (($rtOut | Select-String '^FILE-HEX').Line -split '\s+')[1]
$PFX_HEX1 = (($rtOut | Select-String '^PFX-HEX').Line -split '\s+')[1]
$FIELDS   = (($rtOut | Select-String '^FIELDS').Line -split '\s+')[1]
if (-not $FILE_HEX -or -not $PFX_HEX1 -or -not $FIELDS) { throw "对拍输出不完整，拒绝继续：$rtOut" }
if ($FILE_HEX -ne $META_HEX) { throw "FILE-HEX != META-HEX（写入编码或截断损坏），拒绝继续。" }
if ($PFX_HEX1 -ne $PFX_HEX0) { throw "路径字段 prefix 对拍不一致（采集值 vs 文件内值），拒绝继续。" }
if ($FIELDS -ne "duckdb,duckdb_file,exe,prefix,python,source_id") { throw "身份字段不齐：$FIELDS" }

# ---- (5) 源目录链接扫描：**用 Python 扫并核退出码**，失败不得当作"零链接" ----
# 上轮的错：`cmd /c dir /AL ... 2>$null` 把错误吞掉，且不检查 $LASTEXITCODE；
# 扫描失败时输出为空 ⇒ 被误判为"0 个链接"。
$PY_SCAN = @'
import os, sys, stat
root = sys.argv[1]
REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

# 根有效断言：不能"扫了个不存在/读不了的地方"还报 0 链接
if not os.path.isdir(root):
    print("SCAN-ROOT-INVALID " + root)
    sys.exit(8)
try:
    root_entries = os.listdir(root)
except OSError as ex:
    print("SCAN-ROOT-INVALID " + root + " :: " + str(ex))
    sys.exit(8)
print("SCAN-ROOT-ENTRIES " + str(len(root_entries)))

# onerror 必须显式给：默认 onerror=None 时 scandir 被拒会被**静默忽略**，
# 内层循环根本看不到那个目录的内容，错误数于是仍是 0 —— 这正是上一版的漏。
walk_errors, lstat_errors, hits = [], [], []
dirs_visited = 0

def onerror(ex):
    walk_errors.append("%s :: %s" % (getattr(ex, "filename", "?"), ex))

for dp, dns, fns in os.walk(root, onerror=onerror, followlinks=False):
    dirs_visited += 1
    # Windows 上 junction 的 is_dir() 为 True、is_symlink() 为 False，
    # 只靠 followlinks=False **挡不住**它 —— 必须自行检测并剪枝，否则会钻进链接目标。
    for name in list(dns):
        q = os.path.join(dp, name)
        try:
            st = os.lstat(q)
        except OSError as ex:
            lstat_errors.append("%s :: %s" % (q, ex))
            dns.remove(name)
            continue
        if getattr(st, "st_file_attributes", 0) & REPARSE:
            hits.append(q)
            dns.remove(name)
    for name in fns:
        q = os.path.join(dp, name)
        try:
            st = os.lstat(q)
        except OSError as ex:
            lstat_errors.append("%s :: %s" % (q, ex))
            continue
        if getattr(st, "st_file_attributes", 0) & REPARSE:
            hits.append(q)

print("SCAN-LINKS " + str(len(hits)))
print("SCAN-DIRS " + str(dirs_visited))
print("SCAN-WALK-ERRORS " + str(len(walk_errors)))
print("SCAN-LSTAT-ERRORS " + str(len(lstat_errors)))
for h in hits[:10]:
    print("SCAN-HIT " + h)
for err in (walk_errors + lstat_errors)[:10]:
    print("SCAN-ERR " + err)
sys.exit(0 if (not walk_errors and not lstat_errors) else 7)
'@
$scanPy = Join-Path $dataDir "_pyid_scan.py"
[System.IO.File]::WriteAllText($scanPy, $PY_SCAN, $utf8)
$scanOut = & $py $scanPy "$($pyObj.prefix)"
$scanRc = $LASTEXITCODE
$scanOut
if ($scanRc -eq 8) { throw "源目录根无效（不存在或不可读），不能当作零链接，拒绝继续。" }
$SCAN_LINKS = (($scanOut | Select-String '^SCAN-LINKS').Line -split '\s+')[1]
$SCAN_DIRS  = (($scanOut | Select-String '^SCAN-DIRS').Line -split '\s+')[1]
$SCAN_WERR  = (($scanOut | Select-String '^SCAN-WALK-ERRORS').Line -split '\s+')[1]
$SCAN_LERR  = (($scanOut | Select-String '^SCAN-LSTAT-ERRORS').Line -split '\s+')[1]
$SCAN_ROOTE = (($scanOut | Select-String '^SCAN-ROOT-ENTRIES').Line -split '\s+')[1]
if (-not $SCAN_LINKS -or -not $SCAN_DIRS -or -not $SCAN_WERR -or -not $SCAN_LERR -or -not $SCAN_ROOTE) {
    throw "扫描输出缺字段（LINKS/DIRS/WALK-ERRORS/LSTAT-ERRORS/ROOT-ENTRIES），拒绝继续：$scanOut"
}
if ($scanRc -ne 0) { throw "源目录扫描失败（exit=$scanRc，遍历错误 $SCAN_WERR 项 / lstat 错误 $SCAN_LERR 项）——不能当作零链接，拒绝继续。" }
if ([int]$SCAN_WERR -ne 0) { throw "遍历期有 $SCAN_WERR 个目录无法列出（scandir 被拒），未进入其内部，结论不可靠，拒绝继续。" }
if ([int]$SCAN_LERR -ne 0) { throw "逐项 lstat 有 $SCAN_LERR 个失败，结论不可靠，拒绝继续。" }
# 只保留"walk 至少产出过根目录"这一条**结构性**不变量（walk 对有效根必然产出 1 次）。
# ⚠ 上一版还有一条 `DIRS==1 且 ROOT-ENTRIES>0 ⇒ 拒` 的兜底，**是错的，已删除**：
#   合法情形「根下只有普通文件、没有子目录」时，walk 恰好只产出根 1 次、而根下确有 N 个文件项 ——
#   这条规则会把完全正常的目录判成"扫描异常"，属于**凭空造的兜底**，与真实错误通道无关。
# 判"扫描是否可信"只认两条真实门：SCAN-WALK-ERRORS（onerror 收集）与 SCAN-LSTAT-ERRORS（逐项 lstat）。
if ([int]$SCAN_DIRS -lt 1) { throw "walk 未产出任何目录（根断言异常），拒绝继续。" }
if ([int]$SCAN_LINKS -ne 0) { throw "源环境内部存在 $SCAN_LINKS 个重解析点，无法保证留存完整且独立，拒绝复制。" }
"PY-SRC-LINKS  = 0（dirs=$SCAN_DIRS, root_entries=$SCAN_ROOTE, walk_err=0, lstat_err=0，结论仅限检查时刻）"

# ---- (6) 源/目标链路检查（逐级拒绝链接）+ 写前拒绝 ----
$srcChk = Test-ChainNoReparse $pyObj.prefix
if (-not $srcChk) { throw "源环境路径不存在：$($pyObj.prefix)" }
"PY-SRC-CHAIN  = 全链无链接 OK"

# 同样不 TrimEnd：`GetFullPath` 已给出规范形式，这里再裁尾只会在边界上制造 `D:` 之类的驱动器相对形态。
$pyKeep = [System.IO.Path]::GetFullPath((Join-Path $dataDir "pykeep-$stamp"))
if ($pyKeep -notmatch '^[A-Za-z]:\\') { throw "留存目标不是盘根绝对形式（$pyKeep），拒绝继续。" }
# 目标允许"叶子不存在"，但其**所有祖先必须存在且无链接**；叶子若已存在且是链接会被函数直接 throw
$keepChk = Test-ChainNoReparse $pyKeep -AllowMissingLeaf
if (-not $keepChk) { throw "留存目标的父目录不可用：$pyKeep" }
"PY-KEEP-CHAIN = 全链无链接 OK（或叶子尚未创建）"

if ($keepChk -eq $srcChk) { throw "留存目标与源环境同一路径，拒绝执行：$keepChk" }
if ($keepChk.StartsWith($srcChk + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "留存目标位于源环境子树内：$keepChk 在 $srcChk 之下。复制会自我递归，已拒绝。"
}
$keepRoot = $keepChk

# ---- (7) 复制（/XJ 兜底）----
robocopy $srcChk $keepRoot /E /XJ /NFL /NDL /NJH /NJS /NP | Out-Null
$rcCopy = $LASTEXITCODE
"PY-COPY-RC  = $rcCopy"
if ($rcCopy -ge 8) { throw "环境复制失败（robocopy rc=$rcCopy），禁止升级。" }

# ---- (8) 留存身份：采集 + 直读 + **真正比较 FILE/META/PFX** + 相等三项 + 归属三项 ----
$pyVerify = $null
foreach ($cand in @((Join-Path $keepRoot 'python.exe'), (Join-Path $keepRoot 'Scripts\python.exe'))) {
    if (Test-Path -LiteralPath $cand) { $pyVerify = $cand; break }
}
if (-not $pyVerify) { throw "复制目录里找不到 python.exe（$keepRoot），留存环境不可用，禁止升级。" }
$chkPin = Join-Path $dataDir "pykeep-id-$stamp.json"
$chkOut = & $pyVerify $collectPy $chkPin
if ($LASTEXITCODE -ne 0 -or -not $chkOut) { throw "复制的解释器跑不起来（或 import duckdb 失败），留存环境不可用。输出：$chkOut" }
$chkOut
$CHK_META_HEX = (($chkOut | Select-String '^META-HEX').Line -split '\s+')[1]
$CHK_PFX_HEX0 = (($chkOut | Select-String '^PFX-HEX').Line -split '\s+')[1]
if (-not $CHK_META_HEX -or -not $CHK_PFX_HEX0) { throw "留存环境采集输出缺 hex，拒绝继续。" }

# 8a 留存文件字节对拍：**必须真正比较值**，不能只看有没有摘要行
$chkRtOut = & $py $rtPy $chkPin
if ($LASTEXITCODE -ne 0) { throw "留存身份文件校验失败（exit=$LASTEXITCODE）：$chkRtOut" }
$CHK_FILE_HEX = (($chkRtOut | Select-String '^FILE-HEX').Line -split '\s+')[1]
$CHK_PFX_HEX1 = (($chkRtOut | Select-String '^PFX-HEX').Line -split '\s+')[1]
$CHK_FIELDS   = (($chkRtOut | Select-String '^FIELDS').Line -split '\s+')[1]
if (-not $CHK_FILE_HEX -or -not $CHK_PFX_HEX1 -or -not $CHK_FIELDS) { throw "留存对拍输出不完整，拒绝继续：$chkRtOut" }
if ($CHK_FILE_HEX -ne $CHK_META_HEX) { throw "留存 FILE-HEX != 其 META-HEX（写入编码/截断损坏），拒绝继续。" }
if ($CHK_PFX_HEX1 -ne $CHK_PFX_HEX0) { throw "留存 prefix 字段对拍不一致，拒绝继续。" }
if ($CHK_FIELDS -ne "duckdb,duckdb_file,exe,prefix,python,source_id") { throw "留存身份字段不齐：$CHK_FIELDS" }
"PY-KEEP-HASHDIGEST = OK（FILE==META, PFX 对拍一致, 六字段齐）"

$chkObj = ([System.IO.File]::ReadAllText($chkPin, $utf8)) | ConvertFrom-Json
"PY-VERIFY     = $pyVerify"

# 8b 相等三项：python / duckdb / source_id
$mismatch = @()
if ($chkObj.python -ne $pyObj.python) { $mismatch += "python: $($chkObj.python) != $($pyObj.python)" }
if ($chkObj.duckdb -ne $pyObj.duckdb) { $mismatch += "duckdb: $($chkObj.duckdb) != $($pyObj.duckdb)" }
if ("$($chkObj.source_id)" -ne "$($pyObj.source_id)") { $mismatch += "source_id: $($chkObj.source_id) != $($pyObj.source_id)" }
if ($mismatch.Count -gt 0) { throw ("留存环境身份与原身份不符（不是同版本链，禁止升级）：" + ($mismatch -join '; ')) }

# 8c 归属三项：exe / duckdb 模块 / prefix 必须"全链无链接"且落在留存目录内
$exeChk = Test-ChainNoReparse $chkObj.exe
$dfChk  = Test-ChainNoReparse $chkObj.duckdb_file
$pfxChk = Test-ChainNoReparse $chkObj.prefix
if (-not $exeChk -or -not $dfChk -or -not $pfxChk) { throw "留存环境的 exe/prefix/duckdb 模块路径不可解析，拒绝继续。" }
if (-not $exeChk.StartsWith($keepRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "解释器 exe 不在留存目录内（$exeChk），留存不独立，禁止升级。"
}
if (-not $dfChk.StartsWith($keepRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "duckdb 模块不在留存目录内（$dfChk）——仍在用原 site-packages，升级会把它一起改掉，留存不独立，禁止升级。"
}
if ($pfxChk -ne $keepRoot) { throw "sys.prefix 不等于留存目录（$pfxChk != $keepRoot），留存不独立，禁止升级。" }

# ---- (9) 落证（纯 ASCII）----
$kv = @(
  "duckdb=$($pyObj.duckdb)"
  "source_id=$($pyObj.source_id)"
  "python=$($pyObj.python)"
  "meta_hex=$META_HEX"
  "file_hex=$FILE_HEX"
  "pfx_hex=$PFX_HEX0"
  "keep_meta_hex=$CHK_META_HEX"
  "keep_file_hex=$CHK_FILE_HEX"
  "keep_pfx_hex=$CHK_PFX_HEX0"
  "src_chain=$srcChk"
  "keep_chain=$keepRoot"
  "exe_chain=$exeChk"
  "duckdb_file_chain=$dfChk"
  "prefix_chain=$pfxChk"
  "src_links=$SCAN_LINKS"
  "src_scan_dirs=$SCAN_DIRS"
  "src_scan_root_entries=$SCAN_ROOTE"
  "src_scan_walk_errors=$SCAN_WERR"
  "src_scan_lstat_errors=$SCAN_LERR"
) -join "`r`n"
$kvPath = Join-Path $dataDir "pykeep-verify-$stamp.txt"
[System.IO.File]::WriteAllText($kvPath, $kv, $utf8)
if ([System.IO.File]::ReadAllText($kvPath, $utf8) -ne $kv) { throw "pykeep-verify 读回不一致，拒绝继续。" }
"PY-KEEP-VERIFIED: duckdb=$($pyObj.duckdb) source_id=$($pyObj.source_id) independent=YES"
"PY-KEEP-EVIDENCE = $kvPath"
```

**若 (3)/(4) 任一步失败**：说明本机环境无法整体复制，**此时不允许凭 wheel 下载或 venv 重建"凑合"**——
那两条路同样要过**上面 (4)(5) 的全部机器校验**才算数；过不了就 **`throw` 停止升级**，
把 `pypin-*.json` 与失败信息反馈给我们，由我们给出适配您环境的配方（**不请客户承担首测**）。

> **⚠ 未实测声明（如实）**：本节命令**未在客户现场验证**（本轮边界不跑补测、不执行复制/pip）。
> 我们只保证：①**写前拒绝**逻辑覆盖「目标等于源」与「目标在源子树内」两种递归自吞情形；
> ②**可用性判据是机器比对**（用复制出的 python 实跑 `import duckdb` + `pragma_version()`），
> 而不是"复制完成"或"目录里有文件"。真实环境是否复制得动，**以 (4)(5) 的实际输出为准**。

##### 2.4.1 落盘比对工具（**单引号 here-string，PowerShell 不做任何插值**）

```powershell
# $stamp / $dataDir / $py 由 §2.4.0 前置统一定义；本节**不得重定义**
# （重定义会让 stamp 漂移、基线与验证指向不同文件名——正是上一版 {stamp} 缺陷的同类风险）
$tool = Join-Path $dataDir 'restorecheck_tool.py'

@'
import duckdb, hashlib, json, argparse, os, sys

SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "temp", "system"}

# Fixed probe: identical input must yield identical hash on both sides.
# Its value is stored in the baseline and re-checked on verify. If it differs,
# the two sides do NOT share hash semantics and data digests must not be compared.
PROBE_EXPR = "hash(1::BIGINT, 'a'::VARCHAR, DATE '2026-11-12', 1.5::DOUBLE, TRUE)"

def rowhash(cols, salt=""):
    parts = ", ".join('"%s"' % c for c in cols)
    if salt:
        return "hash(%s, '%s')" % (parts, salt)
    return "hash(%s)" % parts

def probe_values(con):
    g = lambda e: str(con.execute("SELECT %s" % e).fetchone()[0])
    return {"h1": g(PROBE_EXPR),
            "h2": g("hash(1::BIGINT, 'a'::VARCHAR, DATE '2026-11-12', 1.5::DOUBLE, TRUE, 'S2')"),
            "h3": g("hash(1::BIGINT, 'a'::VARCHAR, DATE '2026-11-12', 1.5::DOUBLE, TRUE, 'S3')")}

def table_block(con, schema, table):
    q = '"%s"."%s"' % (schema, table)
    cols = con.execute(
        "SELECT column_name, data_type, is_nullable, column_default FROM duckdb_columns() "
        "WHERE schema_name=? AND table_name=? ORDER BY column_index",
        [schema, table]).fetchall()
    names = [c[0] for c in cols]
    rows = con.execute("SELECT count(*) FROM %s" % q).fetchone()[0]
    # Bounded: streaming aggregate, no ORDER BY, no fetchall, constant memory.
    h = con.execute(
        "SELECT coalesce(sum(CAST(%s AS HUGEINT)),0), coalesce(sum(CAST(%s AS HUGEINT)),0),"
        "       coalesce(sum(CAST(%s AS HUGEINT)),0)"
        " FROM %s" % (rowhash(names), rowhash(names, "S2"), rowhash(names, "S3"), q)).fetchone()
    return {"columns": [[c[0], c[1], bool(c[2]), c[3]] for c in cols],
            "rows": rows,
            "sum_h1": str(h[0]), "sum_h2": str(h[1]), "sum_h3": str(h[2])}

def schema_block(con, schema):
    tabs = [r[0] for r in con.execute(
        "SELECT table_name FROM duckdb_tables() WHERE schema_name=? ORDER BY table_name",
        [schema]).fetchall()]
    return {t: table_block(con, schema, t) for t in tabs}

def indexes_of(con):
    rows = con.execute(
        "SELECT schema_name, table_name, index_name, is_unique, is_primary, expressions, sql "
        "FROM duckdb_indexes() ORDER BY schema_name, table_name, index_name").fetchall()
    return [[str(x) for x in r] for r in rows]

def constraints_of(con):
    rows = con.execute(
        "SELECT schema_name, table_name, constraint_type, constraint_text, "
        "       constraint_column_names, constraint_name, referenced_table "
        "FROM duckdb_constraints() ORDER BY schema_name, table_name, constraint_type, constraint_text").fetchall()
    return [[str(x) for x in r] for r in rows]

class SetupError(Exception):
    pass

def snapshot(db, tempdir, mem):
    con = duckdb.connect(db, read_only=True)
    try:
        # REQUIRED settings: without them the read is not bounded and may spill
        # anywhere. If either fails we must STOP, not continue on an implicit
        # default -- a silent fallback would make every later digest describe a
        # read whose limits were never established.
        try:
            con.execute("SET memory_limit='%s'" % mem)
        except Exception as e:
            raise SetupError("SET memory_limit='%s' failed: %s" % (mem, str(e)[:120]))
        try:
            con.execute("SET temp_directory='%s'" % tempdir.replace("\\", "/"))
        except Exception as e:
            raise SetupError("SET temp_directory failed: %s" % str(e)[:120])
        # OPTIONAL setting: purely a performance/ordering hint, never affects
        # digests (aggregates are order-independent by construction). A failure
        # here is reported but does not stop the run.
        try:
            con.execute("SET preserve_insertion_order=false")
        except Exception as e:
            print("  [note] optional SET preserve_insertion_order=false declined: %s"
                  % str(e)[:80], file=sys.stderr)
        out = {"duckdb_version": con.execute("SELECT * FROM pragma_version()").fetchone()[0],
               "duckdb_source_id": con.execute("SELECT * FROM pragma_version()").fetchone()[1],
               "hash_probe": probe_values(con),
               "schemas": {}, "indexes": [], "constraints": []}
        names = [r[0] for r in con.execute(
            "SELECT schema_name FROM duckdb_schemas() ORDER BY schema_name").fetchall()]
        for s in names:
            if s.lower() in SYSTEM_SCHEMAS:
                continue
            # Register EVERY covered schema, EMPTY ONES INCLUDED.
            # A schema with no tables must still appear (as {}), otherwise
            # schema-level add/drop would be invisible: an empty schema
            # appearing or vanishing changes nothing in schema_set/table_set,
            # so the check would silently MISS exactly the change it exists to
            # catch. With empty schemas registered, "table_set" carries an
            # explicit empty list for them, so both the set of names and the
            # per-schema table lists stay complete.
            out["schemas"][s] = schema_block(con, s)
        out["indexes"] = indexes_of(con)
        out["constraints"] = constraints_of(con)
        return out
    finally:
        con.close()

def sha(o):
    return hashlib.sha256(json.dumps(o, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

def faces(s):
    return {
        "schema_set": sorted(s["schemas"]),
        "table_set": {k: sorted(v) for k, v in s["schemas"].items()},
        "columns": {k: {t: v["columns"] for t, v in tabs.items()} for k, tabs in s["schemas"].items()},
        "rows": {k: {t: v["rows"] for t, v in tabs.items()} for k, tabs in s["schemas"].items()},
        "data192": {k: {t: [v["sum_h1"], v["sum_h2"], v["sum_h3"]] for t, v in tabs.items()}
                    for k, tabs in s["schemas"].items()},
        "indexes": s["indexes"],
        "constraints": s["constraints"],
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["baseline", "verify"])
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--tempdir", default=None)
    ap.add_argument("--memory-limit", default="4GB")
    ap.add_argument("--schema", default=None, help="only this schema (lossy coverage)")
    a = ap.parse_args()
    tempdir = a.tempdir or os.path.join(os.path.dirname(os.path.abspath(a.out)), "_duckdb_tmp")
    os.makedirs(tempdir, exist_ok=True)

    try:
        snap = snapshot(a.db, tempdir, a.memory_limit)
    except SetupError as e:
        print("SETUP-FAILED")
        print("   " + str(e))
        print("   -> memory_limit / temp_directory are REQUIRED for a bounded, spill-safe")
        print("      read. A failure here means the read limits were never established,")
        print("      so no digest from this run would mean anything. Refusing to continue")
        print("      on an implicit default (exit=6).")
        return 6
    if a.schema:
        snap["schemas"] = {k: v for k, v in snap["schemas"].items() if k == a.schema}
        snap["indexes"] = [x for x in snap["indexes"] if x[0] == a.schema]
        snap["constraints"] = [x for x in snap["constraints"] if x[0] == a.schema]
        print("  [note] TRIMMED to schema '%s' -> LOSSY coverage, verdict covers this schema only"
              % a.schema)
    f = faces(snap)
    digests = {k: sha(v) for k, v in f.items()}
    snap["_digests"] = digests

    if a.mode == "baseline":
        snap["_db"] = os.path.abspath(a.db)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, ensure_ascii=False, sort_keys=True, indent=2)
        print("BASELINE-WRITTEN:", os.path.abspath(a.out))
        print("  duckdb_version =", snap["duckdb_version"], "(source_id", snap["duckdb_source_id"] + ")")
        print("  hash_probe     =", snap["hash_probe"]["h1"])
        print("  schemas        =", list(snap["schemas"]))
        for s, tabs in snap["schemas"].items():
            print("   [%s] %s%s" % (s, {t: v["rows"] for t, v in tabs.items()},
                                    "   <EMPTY-SCHEMA>" if not tabs else ""))
        for k in ("columns", "rows", "data192", "indexes", "constraints"):
            print("  digest.%-12s = %s" % (k, digests[k]))
        return 0

    if not a.baseline or not os.path.exists(a.baseline):
        print("BASELINE-MISSING:", a.baseline)
        return 3
    b = json.load(open(a.baseline, encoding="utf-8"))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False, sort_keys=True, indent=2)

    bad = []
    # --- 0) hash semantics binding (must gate before any digest comparison) ---
    if b.get("hash_probe") != snap["hash_probe"]:
        print("HASH-SEMANTICS-DIFFER")
        print("   baseline probe:", b.get("hash_probe"))
        print("   current  probe:", snap["hash_probe"])
        print("   -> duckdb hash() semantics differ between the two sides; data digests are")
        print("      NOT comparable. Refusing to compare. Re-create the baseline with the SAME")
        print("      duckdb version that will run the verify step.")
        return 4
    # Version gate: only a SAME-version chain may report success.
    # A matching 3-constant probe does NOT establish cross-version compatibility,
    # so it must never unlock the success verdict.
    ver_b, ver_c = b.get("duckdb_version"), snap["duckdb_version"]
    src_b, src_c = b.get("duckdb_source_id"), snap["duckdb_source_id"]
    if ver_b != ver_c or src_b != src_c:
        print("CROSS-VERSION-INCOMPARABLE")
        print("   baseline: version=%s source_id=%s" % (ver_b, src_b))
        print("   current : version=%s source_id=%s" % (ver_c, src_c))
        print("   hash_probe baseline:", b.get("hash_probe"))
        print("   hash_probe current :", snap["hash_probe"])
        print("   -> This is NOT a same-version chain. A matching probe only shows that these")
        print("      3 constant expressions happen to agree; it does NOT establish that hash()")
        print("      is identical for all types/values across versions. Therefore NO success")
        print("      verdict is issued here (exit=5). To get a success verdict, run --mode verify")
        print("      with the SAME duckdb version that produced the baseline. Cross-version")
        print("      logical restore requires separate review.")
        print("   (digests below are printed for reference only, NOT as a pass/fail verdict)")
        for k in ("columns", "rows", "data192", "indexes", "constraints"):
            print("   ref.%-12s baseline=%s" % (k, b["_digests"][k]))
            print("   ref.%-12s current =%s" % (k, digests[k]))
        return 5

    bd, cd = b["_digests"], digests
    if bd["schema_set"] != cd["schema_set"]:
        bad.append("SCHEMA SET: %s vs %s" % (f["schema_set"], faces(b)["schema_set"]))
    if bd["table_set"] != cd["table_set"]:
        bad.append("TABLE SET differs (per schema)")
    if bd["columns"] != cd["columns"]:
        for s in sorted(set(f["columns"]) & set(faces(b)["columns"])):
            for t in sorted(set(f["columns"][s]) & set(faces(b)["columns"][s])):
                if f["columns"][s][t] != faces(b)["columns"][s][t]:
                    bad.append("COLUMNS [%s.%s] differs" % (s, t))
    if bd["indexes"] != cd["indexes"]:
        bad.append("INDEXES: %d vs %d entries differ" % (len(f["indexes"]), len(faces(b)["indexes"])))
    if bd["constraints"] != cd["constraints"]:
        bad.append("CONSTRAINTS: %d vs %d entries differ" % (len(f["constraints"]), len(faces(b)["constraints"])))
    for s in sorted(set(f["rows"]) & set(faces(b)["rows"])):
        for t in sorted(set(f["rows"][s]) & set(faces(b)["rows"][s])):
            if f["rows"][s][t] != faces(b)["rows"][s][t]:
                bad.append("ROWS [%s.%s]: %s != %s" % (s, t, f["rows"][s][t], faces(b)["rows"][s][t]))
    if bd["data192"] != cd["data192"]:
        for s in sorted(set(f["data192"]) & set(faces(b)["data192"])):
            for t in sorted(set(f["data192"][s]) & set(faces(b)["data192"][s])):
                if f["data192"][s][t] != faces(b)["data192"][s][t]:
                    bad.append("CONTENT [%s.%s] digest differs" % (s, t))

    print("RESTORE-EQUIV:", "OK" if not bad else "MISMATCH")
    for x in bad:
        print("   x", x)
    print("  scope: schemas=%s tables=%d" % (sorted(snap["schemas"]),
          sum(len(v) for v in snap["schemas"].values())))
    return 0 if not bad else 1

if __name__ == "__main__":
    sys.exit(main())
'@ | Set-Content -Path $tool -Encoding UTF8
"TOOL-WRITTEN: $tool"
```

> **为什么必须用单引号 here-string**：双引号 here-string 会让 PowerShell 把 `$xxx` 当变量展开，
> Python 代码里的 `$` 会被吃掉。单引号 here-string **逐字保留**；所有路径一律走**命令行参数**，不在源码里拼字符串。
>
> **编码（我们实测踩过，务必注意）**：Windows PowerShell 5.1 读取**无 BOM 的 .ps1** 时按系统 ANSI(GBK) 解析，
> UTF-8 中文注释会变乱码，**甚至吞掉换行把两条语句粘成一行**（实测报 `SyntaxError` 于第 17 行）。
> 因此：①本工具源码已**全部改为 ASCII**（注释亦为英文），从根上消除该风险；
> ②若你把命令段存成 `.ps1` 文件，请存为 **UTF-8 带 BOM**（或在控制台里直接逐段粘贴执行）；
> ③`Set-Content -Encoding UTF8` 在 5.1 下写出的即带 BOM，Python 3 读带 BOM 源码是允许的。

##### 2.4.2 生产端：采集基线（升级前、**只读**打开源库）

```powershell
$baseline = Join-Path $dataDir "upgrade-baseline-fp-$stamp.json"
& $py $tool --mode baseline --db (Join-Path $dataDir 'quantstudio.db') --out $baseline

if (-not (Test-Path $baseline)) { throw "基线未生成，停止升级：$baseline" }

# 硬要求：本节必须与 §2.2 备份取自**同一状态、同一停写窗口**，且备份完成后立即执行。
# 我们实测过反例：若基线取自「增量进 WAL 之前」，而备份取自「之后」，正例必然 MISMATCH
# （表现为 rows N != M）——那不是备份坏了，而是基线与备份不是同一状态。
"BASELINE-PATH = $baseline"
"BASELINE-SHA256 = $((Get-FileHash $baseline).Hash)"
```

**端到端断言（必须做）**：2.4.4 的 `--baseline` 参数**必须直接引用同一个 `$baseline` 变量**，
不得重新拼字符串、不得写第二个字面量路径。请在 2.4.4 输出里核对回显的 `CONSUMED-BASELINE`
与本节 `BASELINE-PATH` **完全一致**（含 `$stamp` 展开后的实际时间戳）。

##### 2.4.3 冻结原件 → 建隔离复制品（**配对命名不可改**）

```powershell
$bak = Join-Path $dataDir "quantstudio.db.bak.$stamp"
if (-not (Test-Path $bak)) { throw "备份不存在，停止：$bak" }

$iso = Join-Path $dataDir "restorecheck-$stamp"
New-Item -ItemType Directory -Path $iso -Force | Out-Null
Copy-Item $bak (Join-Path $iso 'r.db') -Force
if (Test-Path "$bak.wal") {
    Copy-Item "$bak.wal" (Join-Path $iso 'r.db.wal') -Force
    "配对 WAL 已就位：r.db.wal"
} else {
    "现场无 WAL 备份（主库已收敛）⇒ 复制品只需 r.db"
}
```

> **三条铁律**：①**原件永不打开**（只用 `Copy-Item` 复制出去）；②只在**隔离复制品**上重放，
> 并保持 `r.db` / `r.db.wal` **配对命名**；③**只用只读方式打开**复制品——实测**读写打开会把 WAL
> 合并进主库并清除该 WAL**（`r.db=536576B + r.db.wal=124B` → 单文件 `798720B`），
> **副本形态被永久改变**，就再也不能代表「备份那一刻」；确需读写验证，**另复制一份**专用。

##### 2.4.4 消费端：与基线比对（正例）

```powershell
# 必须用 2.4.0 留存并验证过的原版本解释器（$pyVerify）来跑验证。
# 用升级后的 $py 跑会必然触发工具的版本门 => CROSS-VERSION-INCOMPARABLE / exit=5。
if (-not $pyVerify) { throw "未找到 2.4.0 的留存解释器 \$pyVerify，禁止做同版本链验证。" }
"VERIFY-PY   = $pyVerify"

$rcOut = Join-Path $dataDir "upgrade-restorecheck-$stamp.json"
$rcLog = Join-Path $dataDir "upgrade-restorecheck-$stamp.txt"
& $pyVerify $tool --mode verify --db (Join-Path $iso 'r.db') --baseline $baseline --out $rcOut |
    Tee-Object -FilePath $rcLog
$rc = $LASTEXITCODE                       # 先落变量，再判定（后面还会有别的语句）
"CONSUMED-BASELINE = $baseline"

# ---- 真阻断：非 0 立即中止，且撤销/写入标记，供步骤 3 硬闸复查 ----
$gateOk  = Join-Path $dataDir "UPGRADE-GATE-OK-$stamp.txt"
$blocked = Join-Path $dataDir "UPGRADE-BLOCKED-$stamp.txt"
if ($rc -ne 0) {
    $why = switch ($rc) {
        1       { "MISMATCH：内容/结构/行数/schema/索引/约束 存在不一致" }
        3       { "BASELINE-MISSING：基线文件缺失" }
        4       { "HASH-SEMANTICS-DIFFER：两侧 hash 语义不同，已拒绝比对" }
        5       { "CROSS-VERSION-INCOMPARABLE：非同一版本链，不判成功" }
        6       { "SETUP-FAILED：必需 SET（memory_limit/temp_directory）失败，读取未受限" }
        default { "未知失败" }
    }
    if (Test-Path $gateOk) { Remove-Item $gateOk -Force }   # 撤销历史通行证，防误用
    "RESTORE-EQUIV 未通过：exit=$rc ==> $why" | Tee-Object -FilePath $blocked
    # 双重阻断：①throw 终止当前脚本；②留下 $blocked 供步骤 3 复查（交互式粘贴时 throw 后仍可继续敲命令）
    throw "升级中止：恢复验证 exit=$rc（$why）。禁止执行步骤 3 及其后任何步骤；保留 .bak 原件与 $rcLog 并联系我们。"
}

# ---- 只有 exit=0 才签发通行证 ----
if (Test-Path $blocked) { Remove-Item $blocked -Force }
@(
    "stamp=$stamp"
    "verify_exit=0"
    "CONSUMED-BASELINE=$baseline"
    "restorecheck_log=$rcLog"
    "issued_at=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
) | Set-Content -Path $gateOk -Encoding UTF8
"UPGRADE-GATE-OK = $gateOk"
```

##### ⚠ 版本门：**只有同版本链才给 `RESTORE-EQUIV: OK`**

**问题**：本工具用 DuckDB 内置 `hash()` 生成内容摘要，而**基线**与**比对**可能在不同 DuckDB 版本下执行
（典型场景：升级前用 1.4.5 建基线，升级后用 1.5.6 验证）。
**我们没有「跨版本全类型兼容」的依据** —— 只测过 10 种类型的一个样本，**不能推广到全类型/全边界**。

⇒ **因此本工具的版本门是「同版本才判成功」，跨版本一律不判成功**：

| 情形 | 工具行为 | 结论强度 |
|---|---|---|
| **版本 + `source_id` 完全一致** | 正常比对，通过则 `RESTORE-EQUIV: OK`，**exit=0** | ✅ **唯一的成功口径** |
| **版本或 `source_id` 不一致** | **不进入成功判据**：打印 `CROSS-VERSION-INCOMPARABLE` 与两侧版本、`hash_probe` 值，**exit=5**，且**不输出 `OK`** | ⚠ **不判成功**；仅供人工参考 |
| `hash_probe` 不一致 | `HASH-SEMANTICS-DIFFER`，**exit=4**，**拒绝比对任何数据摘要** | ❌ 拒比 |
| **必需 `SET` 失败** | `SETUP-FAILED` + 失败项与原因，**exit=6**，**不以默认值继续** | ❌ 读取未受限，结论无意义 |

> **`hash_probe` 的角色（已降级）**：它**只用来"拒比"，不用来"放行"**。
> 探针相同**仅表示 3 个常量表达式在这两版下碰巧一致**，**不足以证明**两版 `hash()` 对所有类型/所有取值同语义——
> 十个类型的样本也只是样本。所以：**探针不同 ⇒ 拒比（强结论）；探针相同 ⇒ 仍不解除跨版本限制**。

> **同版本链怎么走**（**本通知推荐路径**）：
> 1. 基线**在升级前的原版本**下建立并留存（记录 `duckdb_version` + `source_id`）；
> 2. **升级后的恢复验证，请用与基线同版本的解释器**执行——即先保留旧版本环境（或重建一个同版本 venv）跑 `--mode verify`；
> 3. 若必须在**新版本**下做逻辑恢复验证，那属于**跨版本逻辑恢复**，须**另行单独评审**（本通知不覆盖、不承诺）。

##### 内容摘要的强度（**据实，不做强度承诺**）

| 项 | 事实（可复核） |
|---|---|
| 摘要形式 | `sum(CAST(hash(全部列) AS HUGEINT))`，**每张表三次调用**（无盐 / 盐 `S2` / 盐 `S3`），三者**分别与基线的同一位置比对** |
| **为什么用 `sum` 不用 `bit_xor`** | `sum` **对行重复敏感**（实测：3 行 vs 同样 3 行 + 1 重复行 ⇒ 摘要不等）；`bit_xor` 在成对重复时会**相互抵消**。这是**能检出重复行**的理由，**不是**强度理由 |
| 不溢出 | 累加器 `HUGEINT`（上限 `2^127−1`）。实测 3×(2^64−1) 正确；62 GB 按 6.2 亿行外推上界约 `2^93`，**不溢出** |
| 顺序无关 | 聚合与行序无关（实测乱序同表摘要相等）⇒ 集合语义 |
| **⚠ 撤回的表述** | 我们**不再**声称「三路盐 ⇒ 独立 192 位」「生日界 2^96 行对」「构造代价 ~2^96」。<br>**理由**：`hash(x)` 与 `hash(x,'S2')`、`hash(x,'S3')` 是**同一次调用的不同参数形式**，<br>**没有任何依据证明它们相互独立**——把它们当作三个独立 64 位分量做位宽/生日界推算，**属于无依据外推**，已撤回 |
| **实际定位** | 这是一个**工程摘要（engineering digest）**：用来**发现意外损坏、漏拷、错配、少行/多行、内容漂移**，<br>**不提供任何密码学或概率强度保证**，**不用于对抗蓄意伪造** |

> 三盐的实际好处**只在于**：单盐下「行内容不同但 `sum(hash)` 恰好相同」的意外情况，多一路盐能多一层区分机会——
> 这是**经验性的冗余**，**不是可量化的安全强度**。

##### ⚠ 本判据证不到什么（**摘要一致 ≠ 逐位等价**）

| 证不到 | 为什么 |
|---|---|
| **逐位（bit-for-bit）等价** | 摘要是有损聚合，不是逐字节比对 |
| **哈希碰撞** | 工程摘要**无强度承诺**（见上，已撤回位宽/生日界表述） |
| **未被比对的 schema** | `--schema` 裁剪时只比一个 schema（工具打印 `TRIMMED -> LOSSY coverage`） |
| **视图 / 序列 / 宏 / 自定义类型** | **未纳入**本判据 |
| **物理布局与统计信息** | `CHECKPOINT`、压缩、行组、`ANALYZE` 结果均不在判据内 |
| **主库中间区域字节** | 见 §2.3⚠：拷贝只抽首尾 8 MB |
| **62 GB 实跑** | 耗时与峰值内存**未实测**（见 §2.4.6） |

> **关于「全文件 SHA256」——它证明的是另一件事，不能替代本节**：
> 对源与副本做全文件 `Get-FileHash`，能证明的是
> **「这两个文件的物理字节完全相同」**（即拷贝忠实）；它**不能**证明
> **「打开这份副本后，逻辑上读到的数据等于源库的逻辑内容」**——后者要靠本节七面比对。
> 两者**互不替代**：全文件 SHA 抓不到「WAL 未重放导致打开后少数据」（v5 缺陷正是如此，字节一致但逻辑缺失）；
> 七面比对也抓不到「中间区域字节被改但逻辑不可见」。
> 若要两者都覆盖，需**同时**做全文件 SHA + 七面比对——本通知默认做七面比对，全文件 SHA 作为可选加强。

**判据（七个面各自独立核验，任一不等即 MISMATCH）**

| 面 | 判据 | 依据视图 | 独立性实测 |
|---|---|---|---|
| A | **schema 集合**一致 | `duckdb_schemas()`（排除 `information_schema`/`pg_catalog`/`temp`/`system`） | 加一个 `main3` schema ⇒ 只报 `SCHEMA SET` |
| B | **表集**一致（逐 schema） | `duckdb_tables()` + `WHERE schema_name=?` | 建表 ⇒ 报 `TABLE SET` |
| C | **列定义**一致 | `duckdb_columns()`：`data_type`+`is_nullable`+`column_default`+列序 | 加列 ⇒ 只报 `COLUMNS` |
| D | **逐表行数**一致 | `SELECT count(*)`（schema 限定） | 加一行 ⇒ 报 `ROWS 4 != 3` |
| E | **逐表内容**一致（工程摘要） | 三次 `sum(CAST(hash(列…) AS HUGEINT))`（无盐/`S2`/`S3`，分别比对；**不主张三者独立**） | 改 1 个字节 ⇒ 只报 `CONTENT192` |
| F | **索引**一致 | `duckdb_indexes()`：`is_unique`+`is_primary`+`expressions`+**`sql` 完整 DDL** | 加索引 ⇒ 只报 `INDEXES: 2 vs 1`；删索引 ⇒ `0 vs 1` |
| G | **约束**一致 | `duckdb_constraints()`：`constraint_type`+`constraint_text`+`column_names`+`referenced_table` | 去 `NOT NULL` ⇒ 报 `COLUMNS`+`CONSTRAINTS` |

> **索引与约束为何必须分开取**（实测，1.5.6）：`PRIMARY KEY` **只出现在 `duckdb_constraints()`**，
> **不出现在 `duckdb_indexes()`**；而普通 `CREATE INDEX` 只出现在后者。**只取其一必漏**。
> 另注：`duckdb_constraints()` 中同一 `NOT NULL` 可能出现**多行重复记录**（实测 `s1.a` 两条 `NOT NULL`），
> 故按排序后整体比对，不做去重，避免掩盖差异。

**口径一致性（审核要求，逐条落实）**

| 项 | 做法 |
|---|---|
| **内容编码** | 一律用 duckdb `hash(列...)` 对**有类型的列**直接哈希（不做字符串拼接，避免浮点/日期 repr 差异）；`SET preserve_insertion_order=false` 使聚合与顺序无关 |
| **完整 SHA** | 结构面 / 数据面各输出**完整 64 位十六进制** `sha256`（不截断） |
| **schema 限定** | 查询写 `"schema"."table"`；元数据查询一律带 `WHERE schema_name=?` |
| **索引定义** | 取 `is_unique` / `is_primary` / **`sql`（完整 DDL 文本）**，不只取名字 |
| **版本门** | **同版本才判成功**，跨版本 exit=5 不判成功（见下方版本门专节） |
| **摘要强度** | 工程摘要，无强度承诺（见下方「内容摘要的强度」） |

##### 2.4.5 负例自检（**证明这套比对真的会报警**）

**关键前提**：负例必须来自「**含 WAL 增量**」的现场。若本次备份**现场本来就没有 WAL**，
「把 WAL 改名」无从谈起（没有 WAL 可改名）——此时必须**另用合成的含增量 fixture**（分支 2）。

**分支 1：本次备份有 WAL 备份**（`Test-Path "$bak.wal"` 为真）

```powershell
$isoNeg = Join-Path $dataDir "restorecheck-$stamp-neg"
New-Item -ItemType Directory -Path $isoNeg -Force | Out-Null
Copy-Item $bak (Join-Path $isoNeg 'r.db') -Force
Copy-Item "$bak.wal" (Join-Path $isoNeg 'r.db.wal.displaced') -Force
& $pyVerify $tool --mode verify --db (Join-Path $isoNeg 'r.db') --baseline $baseline --out (Join-Path $dataDir "upgrade-restorecheck-$stamp-neg.json") |
    Tee-Object -FilePath (Join-Path $dataDir "upgrade-restorecheck-$stamp-neg.txt")
"负例 exit = $LASTEXITCODE（期望 1 = MISMATCH）"
```

**分支 2：本次备份无 WAL**（现场已收敛）⇒ 用**与生产库完全隔离**的合成 fixture 造负例

```powershell
$fxDir = Join-Path $env:TEMP "qs-wal-fix-$stamp"
New-Item -ItemType Directory -Path $fxDir -Force | Out-Null
$fx = Join-Path $fxDir 'fx.db'

# (1) 建表 + CHECKPOINT：主库落盘 3 行（脚本用 here-string 写盘，避免任何引号转义）
$mkFx = Join-Path $fxDir 'mk_fixture.py'
@'
import duckdb, sys
c = duckdb.connect(sys.argv[1])
c.execute("CREATE TABLE t(i INT, s VARCHAR)")
c.execute("INSERT INTO t VALUES (1,'a'),(2,'b'),(3,'c')")
c.execute("CHECKPOINT")
c.close()
print("FIXTURE-CHECKPOINTED")
'@ | Set-Content -Path $mkFx -Encoding UTF8
& $py $mkFx $fx

# (2) 另起进程追加 4 行到 WAL，随后强杀 ⇒ WAL 留下未合并增量
$w = Join-Path $fxDir 'w.py'
@'
import duckdb, sys, time
c = duckdb.connect(sys.argv[1])
c.execute("INSERT INTO t VALUES (4,'d'),(5,'e'),(6,'f'),(7,'g')")
print("APPENDED", flush=True)
time.sleep(60)
'@ | Set-Content -Path $w -Encoding UTF8
$pp = Start-Process -FilePath $py -ArgumentList @($w, $fx) -PassThru -NoNewWindow
Start-Sleep -Seconds 4
Stop-Process -Id $pp.Id -Force
Start-Sleep -Seconds 2
"fixture: fx.db=$((Get-Item $fx).Length)B, wal=$(if(Test-Path "$fx.wal"){(Get-Item "$fx.wal").Length}else{0})B"

# (3) 采基线（此时读到的应是 3+4=7 行），再造「WAL 错配」副本
$fxBase = Join-Path $fxDir 'fx-baseline.json'
& $pyVerify $tool --mode baseline --db $fx --out $fxBase
Copy-Item $fx (Join-Path $fxDir 'neg.db') -Force
Copy-Item "$fx.wal" (Join-Path $fxDir 'neg.db.wal.displaced') -Force
& $pyVerify $tool --mode verify --db (Join-Path $fxDir 'neg.db') --baseline $fxBase --out (Join-Path $fxDir 'fx-neg.json')
"合成 fixture 负例 exit = $LASTEXITCODE（期望 1 = MISMATCH）"
```

**负例判据**：正例 `RESTORE-EQUIV: OK` 且 **负例必须 `MISMATCH`**。
**若负例也报 OK ⇒ 这套比对没有鉴别力，恢复验证结论一律无效**，请勿据此认为备份可用。

##### 2.4.6 有界性说明（62 GB 上**未实测**，如实声明）

| 项 | 说明 |
|---|---|
| **不做整表物化** | 不用 `SELECT *` 后 `fetchall()`，不用 `ORDER BY ALL`；只取 `count(*)` 与一个**流式聚合** |
| **排序临时空间** | 新写法**不含任何排序**（聚合与顺序无关），故不产生排序溢写；仍显式 `SET temp_directory` 指向 `.\data\_duckdb_tmp`、`SET memory_limit='4GB'`，让意外溢写落盘而非撑爆内存 |
| **内存量级** | 每表只回一行聚合 ⇒ 内存与表大小无关（常量级）；元数据查询返回行数 = 表/列/索引数量级 |
| **实测（合成库，非 62 GB）** | 200 万行 × ~210 B（59.8 MB）、`memory_limit='128MB'`：<br>· 旧写法（`ORDER BY ALL` + `fetchall()`）⇒ **`OutOfMemoryException` 失败**（121.9/122.0 MiB）<br>· 新写法（流式聚合）⇒ **0.06 s 成功**，`rows=2000000` |
| **未验证项（必须承认）** | **62 GB 真实库的耗时与峰值内存我们没跑过**。本设计只保证「内存不随表规模线性增长」，**不保证**「全库行数哈希在您的窗口内一定跑得完」——以首次实跑耗时为准；过慢则按 2.4.7 裁剪 |

##### 2.4.7 可裁剪（时间不够时的降级口径）

```powershell
& $py $tool --mode baseline --db (Join-Path $dataDir 'quantstudio.db') --out $baseline --schema main
```

> 裁剪会**缩小验证覆盖**，属「有损验收」：裁剪后通过的结论**只覆盖被比对到的表**，
> 不得表述为「整个备份已完整验证」。时间允许时**不要裁剪**。

> **务必注意**：**只有在您自己的真实主库上跑出正例 `RESTORE-EQUIV: OK` 且负例 `MISMATCH`，才算「备份可恢复」被验证过。**
> 我们在开发环境用合成库跑通了**同一条命令链**（正例 OK / WAL 错配负例 MISMATCH / 结构负例 MISMATCH），
> 那只能证明**命令与判据可用**，**不能代替**您真实窗口的结果——**没跑过就等于没验证**。

##### 2.4.7b 单 stamp 关联链（**所有产物共用步骤 0 的那一个 `$stamp`**）

> 这张表是"命名一致性"的验收依据：**任一产物若用了别的 stamp，整条链就断了**。
> 重开窗口时请用它逐个核对文件名里的时间戳是否**完全相同**。

| # | 产物 | 生成于 | 文件名 | 谁消费它 |
|---|---|---|---|---|
| 0 | **stamp 权威文件** | 步骤 0 | `data\upgrade-stamp.txt` | 所有后续步骤（找回 stamp） |
| 1 | 无写者举证 | 步骤 1 | `data\upgrade-nolock-evidence-<stamp>.txt` | 人工留档 |
| 2 | 备份主库 | 2.2 | `data\quantstudio.db.bak.<stamp>` | 2.4.3 冻结复制 |
| 3 | 备份 WAL（若有） | 2.2 | `data\quantstudio.db.bak.<stamp>.wal` | 2.4.3 配对复制 |
| 4 | 解释器身份 | 2.4.0 | `data\pypin-<stamp>.json` | 2.4.0 可用性比对 |
| 5 | 留存环境 | 2.4.0 | `data\pykeep-<stamp>\` | **2.4.4 验证解释器** |
| 6 | 留存可用性证据 | 2.4.0 | `data\pykeep-verify-<stamp>.txt` | 人工留档 / 2.4.4 前核对 |
| 7 | 比对工具 | 2.4.1 | `data\restorecheck_tool.py` | 2.4.2 / 2.4.4 |
| 8 | **基线** | 2.4.2 | `data\upgrade-baseline-fp-<stamp>.json` | **2.4.4 `--baseline`（必须是同一个变量）** |
| 9 | 隔离副本 | 2.4.3 | `data\restorecheck-<stamp>\r.db` + `r.db.wal` | 2.4.4 只读比对 |
| 10 | 验证输出 | 2.4.4 | `data\upgrade-restorecheck-<stamp>.json` / `.txt` | 人工留档 |
| 11 | **通行证** | 2.4.4（仅 exit=0） | `data\UPGRADE-GATE-OK-<stamp>.txt` | **步骤 3 硬闸** |
| 12 | **阻断标记** | 2.4.4（exit≠0） | `data\UPGRADE-BLOCKED-<stamp>.txt` | **步骤 3 硬闸** |
| 13 | 负例日志 | 2.4.5 | `data\upgrade-restorecheck-<stamp>-neg.txt` | 鉴别力证据 |
| 14 | 合成 fixture 负例 | 2.4.5 分支2 | `%TEMP%\qs-wal-fix-<stamp>\` | 鉴别力证据（与本机库无关） |

**自检一行**：`Get-ChildItem .\data\*<从 upgrade-stamp.txt 读到的 stamp>* | Select-Object Name`
—— 上面该有文件的项目都要出现，且名字里的时间戳**逐字相同**。

##### 2.4.9b 纯合成候选（**可自行复现的最小验证集；不接触生产库与真实解释器**）

> 本节只是**配方**，我们侧**未执行**（见 §2.4.9 状态）。目的是让上面的路径与扫描判据
> 可以在一个**完全合成、随时可删**的目录里被验证：不复制真实 python 环境、不开库、不 pip。

**① 建树（全部为空目录/空文件，`mklink /J` 建 junction 不需要管理员权限）**

```powershell
$syn = 'D:\qssyn'                                  # 任意空盘符位置均可，此处仅作示例
New-Item -ItemType Directory -Force -Path "$syn\env\Lib\site-packages\duckdb" | Out-Null
Set-Content -Path "$syn\env\python.exe" -Value '' -NoNewline
New-Item -ItemType Directory -Force -Path "$syn\data" | Out-Null
New-Item -ItemType Directory -Force -Path "$syn\flat" | Out-Null
'a.txt','b.txt','c.txt' | ForEach-Object { Set-Content -Path "$syn\flat\$_" -Value 'x' -NoNewline }
Copy-Item -Recurse "$syn\env" "$syn\env_with_link"
cmd /c mklink /J "$syn\alias_to_env" "$syn\env"
cmd /c mklink /J "$syn\chain2"       "$syn\env"
cmd /c mklink /J "$syn\chain1"       "$syn\chain2"
cmd /c mklink /J "$syn\env_with_link\Lib\sp_link" "$syn\env"
```

树形：

```
D:\qssyn\
├── env\                             ← 模拟"python 环境"（空壳）
│   ├── python.exe
│   └── Lib\site-packages\duckdb\
├── env_with_link\                   ← env 的副本，内部再挂一个链接
│   └── Lib\sp_link  → D:\qssyn\env        (junction)
├── flat\                            ← 根下只有普通文件、无子目录（S0b 用）
│   ├── a.txt  b.txt  c.txt
├── data\                            ← 模拟 dataDir
├── alias_to_env     → D:\qssyn\env        (junction)   ← 单级祖先链接
├── chain2           → D:\qssyn\env        (junction)
└── chain1           → D:\qssyn\chain2     (junction)   ← 多级 Target
```

**② 预期读数（逐条对得上才算通过）**

| # | 调用 | 预期输出 | 进程退出码 |
|---|---|---|---|
| S0 | 扫描 `D:\qssyn\env` | `SCAN-LINKS 0` / `SCAN-DIRS ≥ 2` / `SCAN-WALK-ERRORS 0` / `SCAN-LSTAT-ERRORS 0` / `SCAN-ROOT-ENTRIES ≥ 2` | **0** |
| **S0b** | **扫描「根下只有普通文件、无子目录」** `D:\qssyn\flat`（= `a.txt`,`b.txt`,`c.txt`，无子目录） | `SCAN-DIRS 1` / `SCAN-ROOT-ENTRIES 3` / `SCAN-LINKS 0` / 两个错误计数 0 | **0**（**必须放行**） |
| S1 | 扫描 `D:\qssyn\env_with_link` | `SCAN-LINKS 1` + `SCAN-HIT …\Lib\sp_link` | **0** |
| S2 | 扫描 `D:\qssyn\nope`（不存在） | `SCAN-ROOT-INVALID …` | **8** |
| S3 | 扫描含"无法列出"子目录的树（需构造 ACL，本轮**不构造**） | `SCAN-WALK-ERRORS ≥ 1` | **7** |
| P0 | `Test-ChainNoReparse D:\qssyn\data\keep -AllowMissingLeaf` | 返回该路径（叶子不存在被允许） | 见下※ |
| P1 | `Test-ChainNoReparse D:\qssyn\alias_to_env\data` | `路径链上存在重解析点…alias_to_env` | 见下※ |
| P2 | `Test-ChainNoReparse D:\qssyn\chain1\data` | 同上，命中 `chain1`（多级 Target 也在第一级被拦） | 见下※ |
| P3 | `Assert-AbsoluteDrivePath D:qssyn\data` | `路径是驱动器相对形式…` | 见下※ |
| P4 | `Assert-AbsoluteDrivePath qssyn\data` | `路径不是绝对路径…` | 见下※ |
| P5 | `Assert-AbsoluteDrivePath \\srv\share\x` | `UNC 路径不在本判据内…` | 见下※ |

> **S0b 是「误拦」的判别用例**：合法情形「根下只有普通文件、没有子目录」时，
> `os.walk` 恰好只产出根 1 次（`SCAN-DIRS 1`），而根下确有 3 个文件项（`SCAN-ROOT-ENTRIES 3`）。
> 上一版规则 `DIRS==1 且 ROOT-ENTRIES>0 ⇒ 拒` 会把这个**完全正常**的目录判成"扫描异常"，
> **已删除该规则**。S0b 通过（exit 0）即证明误拦已消失；若 S0b 被拒，说明误拦仍在。

**※ 退出码口径（如实说明，不含糊）**：`Assert-AbsoluteDrivePath` / `Test-ChainNoReparse` 是**脚本内函数**，
其失败方式是 `throw` —— 它**没有自己的退出码**，效果是**中止当前的升级步骤**。
只有在 `powershell -File xxx.ps1` 里**未被 `try/catch` 捕获**时，宿主才会给进程一个 **1**。
本节表里对 P0–P5 写"见下※"而不写具体数字，就是因为**这个数字属于宿主行为、不属于判据本身**——
把它们写成 0/7/8 那样的"判据退出码"会让人误以为函数在报告状态。
**只有扫描脚本（S0–S3）有独立、可判定的退出码 0 / 7 / 8。**

**③ 清理（⚠ 删 junction 必须用 `rmdir`）**

```powershell
cmd /c rmdir "$syn\alias_to_env"
cmd /c rmdir "$syn\chain1"
cmd /c rmdir "$syn\chain2"
cmd /c rmdir "$syn\env_with_link\Lib\sp_link"
Remove-Item -Recurse -Force "$syn"
```

> ⚠ **不要对 junction 用 `Remove-Item -Recurse` / `del /S`**：那会**删掉链接目标里的真实内容**
> （这里是 `D:\qssyn\env`，在真实环境里就是你的 python 环境）。必须先 `rmdir` 逐个摘掉链接，再删树。

**④ 这套合成用例证不到什么**

- 证不到真实 python 环境里 `import duckdb` 能否跑通（空壳 `python.exe` 只是占位）；
- 证不到 62 GB 库上的耗时与峰值内存（与本节的路径/扫描判据无关）；
- 证不到"检查之后路径不变"（TOCTOU 竞态，见 §2.4.0(1) 的声明）。

##### 2.4.10 有限合成申请：S0b 最小执行（**待批；本轮未执行**）

> **申请状态：未批准、未执行、未建任何目录。** 本节只交申请文本与命令。
> 审核已关闭「合法平根误拦删除」「盘根保留 + S0b 正例设计」两项退修；
> **纯合成执行与升级均未获批准**，本节是就此提出的**有限申请**，不是执行记录。

**A0. 本轮登记的唯一改动（DRIFT-N19-r3 批准）**

| 项 | 内容 |
|---|---|
| 改动 | **仅** PS1 提取副本的写入编码：`$utf8` → `$utf8Bom`（`UTF8Encoding($true)`，带 BOM） |
| 其余 | **全部不变**：扫描器提取、数组直喂、五计数比较、变异绑定、排他新建、无清理授权 |
| 理由 | 2026-11-16 首次执行（stamp `20261116T143939`）闸未运行：PS 5.1 把无 BOM 的 `.ps1` 按 ANSI(936) 读 ⇒ 中文乱码 ⇒ 解析失败 |
| 新增校验 | ①落盘后实测首 3 字节须为 `EF BB BF`，否则 `PARSE-PRECHECK` 中止；②用 **powershell 5.1 自身 Parser** 对磁盘文件做**静态解析**，失败即中止（未执行任何语句） |
| 现场 | 旧 `20261116T143939` **保留不清理**；本轮用**新**根与新证据目录 |

**A. 申请做什么（只此一件）**

在**一个此前不存在的新绝对根**下建 **1 个目录 + 3 个 txt**，然后用 §2.4.0 **原文提取**的扫描器跑一次、
再用 §2.4.0 **原文提取的消费闸**判定一次，以证明「合法平根不再被误拦」。

**B. 明确不做什么**（与 2.4.9b 的全套建树严格区分）

| 不做 | 说明 |
|---|---|
| ❌ 不建 `env\` / `env_with_link\` / `data\` 等固定根 | 2.4.9b 的建树脚本含 `New-Item -Force` 固定根，**不能拿来当本申请执行** |
| ❌ 不建任何 junction（`mklink /J`） | 边界禁止 |
| ❌ 不构造 ACL | 边界禁止 |
| ❌ 不 `robocopy`、不复制真实解释器环境 | 边界禁止 |
| ❌ 不 pip、不开库、不碰 `data\` 与任何生产文件 | 边界禁止 |
| ❌ 不做任何删除动作（含清理） | 见 G：失败/成功均保留现场，清理需独立申请 |

**C0. 提取逻辑已做只读实测**（纯内存，未建任何目录、未写任何文件到磁盘业务路径）

本轮为确认"照抄能否跑"，在**内存里**复刻了 D 步骤 2/4 的提取逻辑并做语法自检：

| 检查 | 读数 |
|---|---|
| 定界符构造 | `openTok` = `@'`（len 2）、`closeTok` = `'@`（len 2）✅ |
| 扫描器边界定位 | 围栏行 `$PY_SCAN = @'` 在 **L429**，闭合 `'@` 在 **L487**（1-based）；正文 = **L430–L486** |
| 扫描器正文行数 | **57** 行 |
| 扫描器语法 | `py_compile.compile(..., doraise=True)` ⇒ **PASS**（rc=0） |
| 消费闸边界定位 | 起于 **L493**（`if ($scanRc -eq 8)`），止于 **L512**（`PY-SRC-LINKS  = 0（…`） |
| 消费闸语法 | `[scriptblock]::Create(...)` ⇒ **PASS** |

> 说明：这两条只证「提取得到的东西**语法可用**」，**不证**扫描器在平根上的读数正确——
> 那正是本申请要跑的（E 表），**尚未执行**。

**C. 前置（已做只读核实，不构成执行）**

| 项 | 读数 | 来源 |
|---|---|---|
| 目标根不存在 | `Test-Path -LiteralPath 'D:\qssyn_s0b_20261116T0950'` ⇒ **False** | 本轮只读探测 |
| D: 可用空间 | **174,104 MB** | `(Get-PSDrive D).Free` |
| 目标为盘根绝对形式 | 匹配 `^[A-Za-z]:\\` | 形式闸 |
| 无 junction 依赖 | 本申请**不建也不检** junction | — |

> ⚠ 若执行时该根**已存在**（他线或残留），**必须中止**，不得改用 `-Force` 覆盖写。
> 判别：`if (Test-Path -LiteralPath $root) { throw "目标根已存在，非本申请的"不存在的新绝对根"，中止。" }`

**D. 逐步命令（5 步；每步都给出失败时的行为）**

> 解释器**不写占位符**：步骤 0 现场用 `Get-Command python -CommandType Application` 取得真实路径，
> 并核"路径是文件 / 文件名叫 python.exe / 能跑出 `sys.version`"，同时打印 `PY-SHA` 供与 §1.2 对认。
> 本项 S0b 只要求"一个能跑的真解释器"；§1.2 的**身份绑定**属于升级正式流程，不在本申请内。

```powershell
# 步骤 0 · 变量（单一来源，后续只用这些）
$stamp    = Get-Date -Format 'yyyyMMddTHHmmss'
$root     = 'D:\qssyn_s0b_' + $stamp            # 盘根绝对形式
$outDir   = Join-Path (Resolve-Path '.\docs\evidence').Path ('duckdb-s0b-' + $stamp)
$utf8     = New-Object System.Text.UTF8Encoding($false)   # .py / .txt：无 BOM
$utf8Bom  = New-Object System.Text.UTF8Encoding($true)    # **.ps1 必须带 BOM**（PS 5.1 否则按 ANSI/GBK 读）
$notice   = (Resolve-Path '.\docs\handoff\customer-notice-5-duckdb-1.5.6-upgrade-v6-20261115.md').Path
# 解释器：**现场取得 + 当场核验**，不写占位符、不猜路径
$pyCmd = Get-Command python -CommandType Application -ErrorAction SilentlyContinue |
         Select-Object -First 1
if (-not $pyCmd) { throw "PATH 上找不到 python（Application 类型），中止。" }
$py = [System.IO.Path]::GetFullPath($pyCmd.Source)
if (-not (Test-Path -LiteralPath $py -PathType Leaf)) { throw "解释器路径不是文件：$py" }
if ([System.IO.Path]::GetFileName($py) -ne 'python.exe') { throw "取到的不叫 python.exe：$py" }
$pyVerOut = & $py -c "import sys;print(sys.version.split()[0])"
if ($LASTEXITCODE -ne 0 -or -not $pyVerOut) { throw "解释器无法运行：$py" }
"PY-EXE  = $py"
"PY-VER  = $pyVerOut"
"PY-SHA  = " + (Get-FileHash -LiteralPath $py -Algorithm SHA256).Hash

# 根与输出目录：**两者都排他新建**（先判不存在，再不带 -Force 建；-Force 会静默复用已存在目录）
if (Test-Path -LiteralPath $root) { throw "目标根已存在，中止（本申请要求不存在的新根）：$root" }
if ($root -notmatch '^[A-Za-z]:\\') { throw "非盘根绝对形式：$root" }
if (Test-Path -LiteralPath $outDir) { throw "输出目录已存在，中止（排他新建）：$outDir" }
New-Item -ItemType Directory -Path $outDir -ErrorAction Stop | Out-Null   # 无 -Force：已存在即抛错
New-Item -ItemType Directory -Path $root  -ErrorAction Stop | Out-Null   # 同上；与步骤 1 合并

# 步骤 1 · 写 3 个 txt（根已在步骤 0 排他新建，此处不重复建；无子目录 ⇒ 合法平根）
foreach ($n in 'a.txt','b.txt','c.txt') {
    [System.IO.File]::WriteAllText((Join-Path $root $n), 'x', $utf8)
}
# 前置断言：确实只有 3 个文件项、0 个子目录
$entries = @(Get-ChildItem -LiteralPath $root -Force)
$subDirs = @($entries | Where-Object { $_.PSIsContainer })
if ($entries.Count -ne 3) { throw "根下条目数 $($entries.Count) != 3，中止。" }
if ($subDirs.Count -ne 0)  { throw "根下有 $($subDirs.Count) 个子目录，不是平根，中止。" }

# 步骤 2 · 从 §2.4.0 **原文提取**扫描器（不重写、不复制粘贴）
$L = Get-Content -LiteralPath $notice -Encoding UTF8
$s0 = -1; $s1 = -1
# 定界符用**显式字符串拼接**，不用 [char] 算术（[char]+[char] 在 PS 里会退化成整数加法）
$openTok  = '@' + "'"        # 行内容为： @'
$closeTok = "'" + '@'        # 行内容为： '@
for ($k = 0; $k -lt $L.Count; $k++) {
    if ($s0 -lt 0 -and $L[$k].Trim() -eq ('$PY_SCAN = ' + $openTok)) { $s0 = $k; continue }
    if ($s0 -ge 0 -and $L[$k].Trim() -eq $closeTok) { $s1 = $k; break }
}
if ($s0 -lt 0 -or $s1 -le $s0) { throw "未能在通知里定位 `$PY_SCAN` 边界（s0=$s0 s1=$s1）" }
$scanPy = Join-Path $outDir '_pyid_scan.py'
[System.IO.File]::WriteAllText($scanPy, (($L[($s0+1)..($s1-1)]) -join "`r`n"), $utf8)

# 步骤 3 · 跑扫描器（同一份原文实现）
# ⚠ **不要把输出转成一个字符串**：消费闸用的是 `Select-String '^SCAN-LINKS'` 这类**行首**匹配，
#   而 `Out-String` 会把逐行数组压成"一个含换行的字符串"，Select-String 对单字符串只按
#   整体一条记录处理 —— 实测结果：只有恰好位于首行的 `SCAN-LINKS` 能命中，
#   `SCAN-DIRS` / `SCAN-ROOT-ENTRIES` / `SCAN-WALK-ERRORS` / `SCAN-LSTAT-ERRORS` **四个全部失配**，
#   闸会先撞上"扫描输出缺字段"而 throw，**根本走不到数值比较**。
#   故：**原样保留逐行数组**喂给闸；只有写日志时才拼成文本。
$scanOut = @(& $py $scanPy $root)          # 数组：一行一个元素（与生产同源形态）
$scanRc  = $LASTEXITCODE
# 落日志：这里才转文本
[System.IO.File]::WriteAllText((Join-Path $outDir 'scan.stdout.txt'), (($scanOut -join "`r`n") + "`r`n"), $utf8)
[System.IO.File]::WriteAllText((Join-Path $outDir 'scan.rc.txt'),     "$scanRc`r`n", $utf8)
if ($scanRc -ne 0) { throw "扫描器 rc=$scanRc（预期 0），本项失败，不再进消费闸。" }
# 自证：数组形态下五个行首锚点必须都能取到值（与实验一致）
foreach ($k in 'SCAN-LINKS','SCAN-DIRS','SCAN-ROOT-ENTRIES','SCAN-WALK-ERRORS','SCAN-LSTAT-ERRORS') {
    if (-not (($scanOut | Select-String ('^' + $k)).Line)) { throw "扫描输出缺 $k（数组形态下仍取不到），拒绝继续。" }
}

# 步骤 4 · 从 §2.4.0 **原文提取消费闸**，dot-source 后喂同一 $scanOut/$scanRc
$g0 = -1; $g1 = -1
for ($k = 0; $k -lt $L.Count; $k++) {
    if ($g0 -lt 0 -and $L[$k].Trim().StartsWith('if ($scanRc -eq 8)')) { $g0 = $k }
    if ($g0 -ge 0 -and $L[$k].Contains('PY-SRC-LINKS  = 0（')) { $g1 = $k; break }
}
if ($g0 -lt 0 -or $g1 -le $g0) { throw "未能在通知里定位消费闸边界（g0=$g0 g1=$g1）" }
$gatePath = Join-Path $outDir '_gate_from_notice.ps1'
# ⚠ **本副本必须带 BOM**：PS 5.1 读取**无 BOM** 的 .ps1 时按 ANSI（本机 936/GBK）解码，
#    含中文的闸会整段乱码、解析失败（2026-11-16 首次执行即栽在此处）。
#    其余逻辑不变，**仅这一处写入编码**改为 $utf8Bom。
[System.IO.File]::WriteAllText($gatePath, (($L[$g0..$g1]) -join "`r`n"), $utf8Bom)

# 4a · BOM 实测（不看文件头就当"带 BOM"是不可接受的）
$gb = [System.IO.File]::ReadAllBytes($gatePath)
if ($gb.Length -lt 3 -or $gb[0] -ne 0xEF -or $gb[1] -ne 0xBB -or $gb[2] -ne 0xBF) {
    throw "PARSE-PRECHECK：PS1 提取副本无 EF BB BF（实得 $('{0:X2} {1:X2} {2:X2}' -f $gb[0],$gb[1],$gb[2])），拒绝继续。"
}
"GATE-BOM = EF BB BF OK"

# 4b · 目标宿主静态解析（用 powershell 5.1 自己的 Parser 读盘解析，未执行任何语句）
$parseOut = & powershell -NoProfile -Command @"
`$e = `$null
[void][System.Management.Automation.Language.Parser]::ParseFile('$($gatePath -replace "'","''")', [ref]`$null, [ref]`$e)
if (`$e -and `$e.Count -gt 0) { `$e | ForEach-Object { 'PARSE-ERR ' + `$_.Message }; exit 2 } else { 'PARSE-OK'; exit 0 }
"@
$parseRc = $LASTEXITCODE
$parseOut
if ($parseRc -ne 0) { throw "PARSE-PRECHECK：目标宿主静态解析失败（rc=$parseRc），拒绝继续。$parseOut" }

# 闸的输入就是上面那两个变量 —— 与生产完全同源
& { . $gatePath }                 # 不 throw = 消费闸接受
$GATE_ACCEPTED = $true
[System.IO.File]::WriteAllText((Join-Path $outDir 'gate.accepted.txt'), "ACCEPTED`r`n", $utf8)
```

**E. 预期读数（**必须逐项相等**，不是"看着差不多"）**

| 项 | 预期值 | 由谁产出 |
|---|---|---|
| `scan.rc.txt` | **0** | 扫描器进程 |
| `SCAN-DIRS` | **1** | 扫描器 stdout |
| `SCAN-ROOT-ENTRIES` | **3** | 扫描器 stdout |
| `SCAN-LINKS` | **0** | 扫描器 stdout |
| `SCAN-WALK-ERRORS` | **0** | 扫描器 stdout |
| `SCAN-LSTAT-ERRORS` | **0** | 扫描器 stdout |
| 消费闸 | **不抛异常**，并打印 `PY-SRC-LINKS  = 0（dirs=1, root_entries=3, walk_err=0, lstat_err=0，结论仅限检查时刻）` | 消费闸 |

> **⚠ 退出码 0 本身不等于误拦已关闭。** 这是本申请最要紧的一条：
> 上一版被删掉的那条错规则（`DIRS==1 且 ROOT-ENTRIES>0 ⇒ 拒`）**不在扫描器里，在消费闸里**。
> 所以「扫描器 rc=0」对它**毫无鉴别力**——旧配方下扫描器同样 rc=0，误拦发生在**下游判定**。
> **关闭判据只能是**：消费闸读到的 `dirs=1` 与 `root_entries=3` 被它**接受**（不 throw），
> 且上面五个计数**逐项相等**。两个条件同时成立，才算误拦实测关闭。

**F. 鉴别力自检（证明消费闸真的会拒绝；纯内存变异，不建任何东西）**

```powershell
# 每个变异都必须让消费闸**因预期原因**throw。
# ⚠ 上一版的自检是**空转的**：它只改局部变量 $so / $m.rc，而闸读的是 $scanOut / $scanRc ——
#   `& { . $gatePath }` 走的是**动态作用域**，子作用域读到的是父作用域的 $scanOut/$scanRc，
#   那两个变量**从头到尾没被改过**，于是六个"变异"实际都在对同一份正常输入跑，必然全不 throw。
#   本版改为：**逐例把变异值赋给闸真正读的那两个变量**，跑完立刻还原，并**核对错误消息**。
$baselineOut = $scanOut          # 原始数组，逐例还原用
$baselineRc  = $scanRc

$cases = @(
  @{ n='WALK=1';  rc=0; from='SCAN-WALK-ERRORS 0';  to='SCAN-WALK-ERRORS 1';  expect='无法列出' }
  @{ n='LSTAT=1'; rc=0; from='SCAN-LSTAT-ERRORS 0'; to='SCAN-LSTAT-ERRORS 1'; expect='lstat 有 1 个失败' }
  @{ n='LINKS=1'; rc=0; from='SCAN-LINKS 0';        to='SCAN-LINKS 1';        expect='重解析点' }
  @{ n='DIRS=0';  rc=0; from='SCAN-DIRS 1';         to='SCAN-DIRS 0';         expect='未产出任何目录' }
  @{ n='RC=8';    rc=8; from=$null;                 to=$null;                 expect='根无效' }
  @{ n='RC=7';    rc=7; from=$null;                 to=$null;                 expect='扫描失败' }
)

$pass = 0
foreach ($m in $cases) {
    # ① 构造输入（按行替换，保持数组形态 —— 与修 1 的理由相同）
    if ($m.from) {
        $mut = @($baselineOut | ForEach-Object { if ($_ -eq $m.from) { $m.to } else { $_ } })
        if ($mut.Count -ne $baselineOut.Count) { throw "变异 $($m.n) 行数变了，构造有误。" }
        if (-not (@($mut) -contains $m.to)) { throw "变异 $($m.n) 未真正替换成功（找不到 $($m.from)）。" }
    } else {
        $mut = $baselineOut
    }
    # ② **显式绑定到闸真正读的变量**（不是 $so / $m.rc）
    $scanOut = $mut
    $scanRc  = $m.rc
    # ③ 跑闸，收集错误
    $err = $null
    try { & { . $gatePath } } catch { $err = $_.Exception.Message }
    # ④ 还原，任何时候都不让变异泄漏到下一例或后续步骤
    $scanOut = $baselineOut
    $scanRc  = $baselineRc
    # ⑤ 核**预期原因**：没 throw ⇒ 无鉴别力；throw 但不是预期原因 ⇒ 不算命中
    if (-not $err) { throw "鉴别力不足：变异 $($m.n) 未被消费闸拒绝。" }
    if ($err -notlike ('*' + $m.expect + '*')) {
        throw "变异 $($m.n) 被拒了，但原因不是预期（期望含「$($m.expect)」，实得「$err」）——不算命中。"
    }
    "  GATE-CASE $($m.n) => 按预期被拒（$($m.expect)）"
    $pass++
}
# ⑥ 六例全过之后再跑一次基线，确认还原有效（闸必须接受）
$err0 = $null
try { & { . $gatePath } } catch { $err0 = $_.Exception.Message }
if ($err0) { throw "变异后基线不再通过（还原失效）：$err0" }
"GATE-DISCRIMINATION = $pass/6 全部按预期被拒 OK；基线仍通过（还原有效）"
```

> 说明：变异只改**内存里的 `$scanOut`／`$scanRc`**，不碰磁盘，因此不违反"不建树/不构造 ACL"。

**G. 输出路径（不含清理）**

| 产物 | 路径 |
|---|---|
| 扫描器副本（原文提取） | `<outDir>\_pyid_scan.py` |
| 消费闸副本（原文提取） | `<outDir>\_gate_from_notice.ps1` |
| 读数 | `<outDir>\scan.stdout.txt` / `scan.rc.txt` / `gate.accepted.txt` |
| 落证 | `<outDir>\s0b-verdict.txt` |

`<outDir>` = `docs\evidence\duckdb-s0b-<stamp>\`。

**不提供清理步骤（本申请不含清理授权）**

失败或成功后**一律保留现场**：`$root` 的 3 个 txt、`<outDir>` 的全部读数与提取件都留在原处，
作为「跑了什么、看到什么」的物证。**本申请不申请、也不执行任何删除动作**。

> ⚠ 特别说明：**「没建 junction」不等于获得递归删除权**。
> 我上一版写过「本申请未建 junction，故可安全删树」——这是**从"没有链接"推出"可以删"**，
> 推理不成立：①树里现在没有链接，不代表删除那一刻没有（同一 TOCTOU 面）；
> ②即使真没有链接，`Remove-Item -Recurse` 的正确性也依赖"路径确实是我建的那个"，
> 而这一条要靠**排他新建**来保证（步骤 0 已改），不能靠"没建 junction"来保证；
> ③删除是不可逆动作，**不该由一个测试申请顺手携带**。
> 清理若确需进行，应作为**独立申请**另报（含目标路径、判据、回退说明），不在本申请范围。

**H. 失败与退出（每步失败都停，不继续）**

| 失败点 | 触发条件 | 行为 |
|---|---|---|
| 根已存在 | `Test-Path $root` 真 | `throw`，**不覆盖** |
| 非盘根形式 | 不匹配 `^[A-Za-z]:\\` | `throw` |
| 条目数不符 | `$entries.Count ≠ 3` | `throw` |
| 有子目录 | `$subDirs.Count ≠ 0` | `throw`（不是平根） |
| 提取失败 | 定位不到 `$PY_SCAN` / 消费闸边界 | `throw`（**不手写替代**） |
| 扫描器 rc≠0 | 见 F 表（7 遍历错 / 8 根无效） | `throw`，**不进消费闸** |
| 消费闸 throw | 任一门命中 | 本项失败；若因 `DIRS==1 且 ROOT>0` ⇒ **误拦仍在** |
| 解释器取不到 | PATH 无 python / 非文件 / 名字不符 / 跑不出 version | `throw` |
| 输出目录已存在 | `Test-Path $outDir` 真 | `throw`（排他新建，**不复用**） |
| 变异原因不符 | 被拒但错误消息不含预期关键词 | 判"不算命中"，本项失败 |
| 变异还原失效 | 六例跑完后基线不再通过 | 本项失败 |
| 行首锚点自证失败 | 数组形态下五个锚点仍取不到值 | `throw`（说明修 1 未生效） |

**I. 这套最小执行证不到什么**

- 证不到 junction 相关判据（本申请**不建** junction，`SCAN-LINKS=0` 只是"没有链接"，不是"能检出链接"）；
- 证不到 `scandir` 被拒的处理（不构造 ACL，`SCAN-WALK-ERRORS=0` 同理）；
- 证不到 `Test-ChainNoReparse` 的盘根保留（本申请不走链检查；那由 U32 单独覆盖）；
- 证不到 62 GB 耗时与峰值内存；
- 证不到"检查之后路径不变"（TOCTOU，见 §2.4.0(1)）。

##### 2.4.8 预期读数对照表（**执行时应看到什么；对不上就是有问题**）

> 这张表是本节的**验收预期**。实际输出与下表不符时，先查「实际」列对应的原因，**不要**按"看起来还行"往下走。

| # | 场景 / 触发方式 | 预期输出（关键行） | 预期 exit | 预期后续行为 |
|---|---|---|---|---|
| 1 | 正常、同版本链、七面全一致 | `RESTORE-EQUIV: OK`<br>`scope: schemas=[...] tables=N` | **0** | 签发 `UPGRADE-GATE-OK-$stamp.txt`，允许进步骤 3 |
| 2 | **存在空 schema**（有 schema 无表） | 基线打印 `[<schema>] {}   <EMPTY-SCHEMA>`；该 schema **出现在** `schemas=` 列表里 | 0 | 空 schema 已纳入判据，不因"没表"被跳过 |
| 3 | **空 schema 被删除或新增** | `RESTORE-EQUIV: MISMATCH`<br>`x SCHEMA SET: ['main','newx'] vs ['main']` | **1** | 写 `UPGRADE-BLOCKED`；**禁止**步骤 3 |
| 4 | **`SET memory_limit` 失败** | `SETUP-FAILED`<br>`SET memory_limit='4GB' failed: …`<br>`Refusing to continue on an implicit default (exit=6)` | **6** | 同上；**不**以默认内存继续 |
| 5 | **`SET temp_directory` 失败** | `SETUP-FAILED` + `SET temp_directory failed: …` | **6** | 同上；**不**以默认临时目录继续 |
| 6 | `SET preserve_insertion_order` 失败（**可选**项） | `[note] optional SET preserve_insertion_order=false declined: …` | 0 | **继续**（该项不影响摘要：聚合与顺序无关） |
| 7 | 内容/行数/列/索引/约束不一致 | `RESTORE-EQUIV: MISMATCH` + `x ROWS ...` / `x CONTENT ...` 等 | **1** | 写 `UPGRADE-BLOCKED` + **`throw` 中止**；步骤 3 硬闸再拦一次 |
| 8 | 基线 `hash_probe` 与当前不同 | `HASH-SEMANTICS-DIFFER` + 两侧探针值 | **4** | 同上（拒比，不给任何数据摘要结论） |
| 9 | 版本或 `source_id` 不同 | `CROSS-VERSION-INCOMPARABLE` + 两侧版本/`source_id`；摘要只作 `ref.` 打印 | **5** | 同上（**不输出 OK**） |
| 10 | 基线文件不存在 | `BASELINE-MISSING: <path>` | **3** | 同上 |
| 11 | 步骤 3 被执行但通行证缺失 | `未找到通行证 … —— 2.4.4 未通过或未执行，禁止升级。` | 脚本中止 | `git pull` **不执行** |
| 12 | 步骤 3 被执行但存在阻断标记 | `恢复验证已判失败，见 … —— 禁止升级。` | 脚本中止 | `git pull` **不执行** |

**怎么自己验证"阻断真的生效"**（建议在正式升级前，用合成库演练一次，不碰生产库）：

```powershell
# 演练：故意用一个内容不同的基线，确认 ①exit≠0 ②抛错中止 ③留下 UPGRADE-BLOCKED ④步骤3 不执行
# 期望：控制台出现 "RESTORE-EQUIV: MISMATCH" -> "RESTORE-EQUIV 未通过：exit=1 ==> ..." -> throw 红字
#       且 .\data\UPGRADE-BLOCKED-$stamp.txt 生成、UPGRADE-GATE-OK 不存在
# 随后执行步骤 3 的硬闸段，应被 throw 拦住（git pull 不执行）
```

> **如实说明**：上表 11/12 项的"脚本中止"来自 PowerShell `throw`；若您是**逐段粘贴**到同一个控制台窗口，
> `throw` 会打印红字错误但**控制台不会关闭**——所以真正防你误继续的，是 ③的阻断标记文件 +
> 步骤 3 开头的硬闸自检。**两道都要在**，缺一都可能被人手跳过。

##### 2.4.9 合成用例清单（**我们侧补测用；客户不承担首测**）

> 下面这些是**我们**在合成库上必须逐条跑通的用例。**不要求客户在自己的真实库上首次试跑**——
> 客户现场只需按下表「客户侧预期」核对输出；**用例行为是否正确，由我们补测证明后才算数**。
> **当前状态：全部未执行**（本轮边界：不跑新补测）。

| ID | 目标 | 构造方式（合成库 / `%TEMP%`） | 预期输出 | 预期 exit |
|---|---|---|---|---|
| **U1** | 空 schema 被登记 | 建 schema `emp`（无表），采基线 | 基线打印 `[emp] {}   <EMPTY-SCHEMA>`；`schemas=` 含 `emp` | 0 |
| **U2** | 空 schema **新增**被检出 | 基线后 `CREATE SCHEMA emp2`，再 verify | `x SCHEMA SET: ['main','emp2'] vs ['main']` | **1** |
| **U3** | 空 schema **删除**被检出 | 基线含空 schema `emp`，`DROP SCHEMA emp` 后 verify | `x SCHEMA SET`（缺 `emp`） | **1** |
| **U4** | schema **变空**被检出 | 基线含 `s1`（1 表 1 行），verify 前 `DROP TABLE s1.t` | `x TABLE SET differs`（`s1: [] vs ['t']`） | **1** |
| **U5** | `memory_limit` 失败即停 | `--memory-limit '0B'`（或非法值） | `SETUP-FAILED` + `SET memory_limit=... failed` + `Refusing to continue` | **6** |
| **U6** | `temp_directory` 失败即停 | `--tempdir` 指向不可创建路径（如 `Z:\nope\nope`） | `SETUP-FAILED` + `SET temp_directory failed` | **6** |
| **U7** | 可选项 SET 失败**不**阻断 | 桩：令 `preserve_insertion_order` 抛错（或断言其失败仅 note） | `[note] optional SET ... declined`，随后正常出 `OK` | 0 |
| **U8** | exit=1 真阻断 | 同 U2 | `UPGRADE-BLOCKED-<stamp>.txt` 生成；`UPGRADE-GATE-OK` **不存在**；步骤 3 硬闸 `throw` | 1 → 拦 |
| **U9** | exit=4 真阻断 | 伪造基线 `hash_probe.h1="999"` | `HASH-SEMANTICS-DIFFER`；同上阻断 | **4** |
| **U10** | exit=5 真阻断 | 基线 1.4.5，verify 用 1.5.6 | `CROSS-VERSION-INCOMPARABLE`，**无 OK**；同上阻断 | **5** |
| **U11** | 步骤 3 无通行证被拦 | 不跑 2.4.4，直接跑步骤 3 硬闸段 | `未找到通行证 ... 禁止升级` | 脚本中止 |
| **U12** | 步骤 3 有阻断标记被拦 | 手工造 `UPGRADE-BLOCKED-<stamp>.txt` 后跑步骤 3 | `恢复验证已判失败 ... 禁止升级` | 脚本中止 |
| **U13** | 留存目标==源被拒 | `$pyKeep` 设为 `$srcPrefix` | `留存目标与源环境同一路径，拒绝执行` | 中止 |
| **U14** | 留存目标在源子树被拒 | `$pyKeep` 设为 `$srcPrefix\..\pykeep` 之下的路径 | `留存目标位于源环境子树内 ... 已拒绝` | 中止 |
| **U15** | 复制后不可用被拒 | 复制后用坏 python（或删掉 `duckdb` 包）跑 `$pyVerify` | `复制的解释器跑不起来` / `duckdb 版本 !=` | 中止 |
| **U16** | **`source_id` 不等被拒** | 留存环境人为换一个 duckdb 补丁版（source_id 变） | `source_id: ... != ...` ⇒ 中止 | 中止 |
| **U17** | **留存不独立被拒（仍在原 site-packages）** | 复制后让 `duckdb` 从原 prefix 加载（如删掉留存内 duckdb 包，留 `sys.path` 回退） | `duckdb 模块不在留存目录内 ... 不独立` | 中止 |
| **U18** | **中文路径 pin 无损** | `data` 目录置于含中文的路径（如 `D:\测试\data`） | pypin 读回与写入**逐字一致**；`PY-PREFIX` 显示中文未变 `?` | 0 |
| **U19** | **留存目标是 junction 被拒** | 预先把 `data\pykeep-<stamp>` 做成指向源的 junction | `留存目标已存在且是重解析点 ... 拒绝` | 中止 |
| **U20** | **父目录是 junction 指向源子树被拒** | 把 `data` 目录做成指向 `$srcPrefix\sub` 的 junction | `留存目标（逐级解析后）位于源环境子树内 ... 已拒绝` | 中止 |
| **U21** | **祖先 junction 被拒（本轮核心）** | `alias` 指向源环境，复制目标设为 `alias\child`（`child` 为普通目录） | 逐级解析后目标落在源子树内 ⇒ **拒绝** | 中止 |
| **U22** | **多级 Target 被拒** | `l1 -> l2 -> 源环境`，目标设为 `l1\child` | 逐级+多跳解析到源，⇒ **拒绝**；成环则报链过长 | 中止 |
| **U23** | **源内链接拒绝复制** | 源环境内建一个 junction（如 `Lib\sp_link` 指向外部） | `源环境内部存在 N 个重解析点 ... 拒绝复制` | 中止 |
| **U24** | **PS 直读不依赖 stdout** | 把控制台代码页设为 936(GBK) 后跑全链，路径含中文 | 仍能正确读出 `$pyObj.prefix`（因走 `ReadAllText` 读文件，不经 stdout）；`PFX-HEX` 对拍一致 | 0 |
| **U25** | **扫描失败不得当零链接** | 源目录内造一个无权限子目录（使 `lstat` 报错） | `SCAN-ERRORS > 0` ⇒ `扫描期存在 N 项访问错误，结论不可靠，拒绝继续` | 中止 |
| **U26** | **留存三值比较真生效** | 复制完成后手工改 `pykeep-id-<stamp>.json` 一个字节（或改字段值） | `留存 FILE-HEX != 其 META-HEX` ⇒ 拒绝（**保留旧 META 的整链首先被 FILE 拦**）；**对齐 META 后的隔离测试**才到 `留存 prefix 字段对拍不一致` | 中止 |
| **U27** | **驱动器相对路径被拒** | 路径给 `D:qssyn\data`（`D:` 后无斜杠） | `路径是驱动器相对形式…请改为全限定路径` | 中止升级步骤 |
| **U28** | **根无效不得当零链接** | 扫描 `D:\qssyn\nope`（不存在） | `SCAN-ROOT-INVALID …`；PS 侧 `根无效（不存在或不可读），不能当作零链接` | **扫描脚本 exit 8**，升级步骤中止 |
| **U29** | **scandir 被拒不得当零链接** | 源树内造一个 ACL 拒绝列出的子目录（`icacls` 移除当前用户读权限） | `SCAN-WALK-ERRORS ≥1`；PS 侧 `遍历期有 N 个目录无法列出…拒绝继续` | **扫描脚本 exit 7**，升级步骤中止 |
| **U30** | **junction 不被 followlinks 挡住** | 源树内建 junction（`mklink /J`） | `SCAN-LINKS ≥1` 且 `SCAN-HIT` 指名该 junction；**不得**钻进链接目标后爆量 | 0（脚本正常完成，链接被发现） |
| **U31** | **「根只有普通文件」不得被误拦** | `D:\qssyn\flat`（3 个 txt、无子目录） | `SCAN-DIRS 1` / `SCAN-ROOT-ENTRIES 3` / 两个错误计数 0 ⇒ **通过** | **0**（本轮误拦回归用例） |
| **U32** | **盘根不被裁成驱动器相对** | `Test-ChainNoReparse D:\` 与其下路径 | 返回值为 `D:\...` 形式，**不得**出现 `D:` 或 `D:foo`；出口断言不触发 | 见下※ |

> **判据纪律**：U1–U4 是"判据能抓到目标变更"（鉴别力），U5–U6 是"必需项失败必须停"，
> **U21–U26 是「全链路径」与「编码链」**:U21 祖先 junction、U22 多级 Target、U23 源内链接、
> U24 PS 直读不依赖 stdout、U25 扫描失败不得当零链接、U26 留存三值比较真生效。
> **⚠ U16／U17 不能代替其他路径的前置**：它们分别只证 `source_id` 真参与比较、`duckdb.__file__` 真做归属判定，
> **不涉及**祖先 junction、多级 Target、源内链接、编码链；任何一条用例通过都**不能**推出其余路径成立。
> **U16–U20 是「身份比较真实生效」与「留存独立」**:U16 验 source_id 真参与比较,U17 验「能 import ≠ 独立」,
> U18 验无损 pin,U19–U20 验 junction 情形不被词法比较蒙混,
> U7 是"可选项失败**不**得停"（防止把 fail-closed 做成过度阻断），U8–U12 是"真阻断"，
> U13–U15 是"留存配方不会吞自己、且可用性靠机器判据"。
> **未执行前，本表只是待验清单，不构成任何已实现承诺。**

### 步骤 3 · 升级代码

> **前置硬闸（不可跳过）**：只有 2.4.4 **签发**了 `UPGRADE-GATE-OK-$stamp.txt` 才允许执行本步。
> 若存在 `UPGRADE-BLOCKED-$stamp.txt`，或通行证缺失 ⇒ **本步立即中止**，不得凭"验证看起来没问题"继续。
> 分段执行/重开窗口导致变量丢失时，先用 `Get-ChildItem .\data\UPGRADE-*.txt` 找回原 `$stamp`，**不得改 stamp**。

```powershell
$gateOk  = Join-Path $dataDir "UPGRADE-GATE-OK-$stamp.txt"
$blocked = Join-Path $dataDir "UPGRADE-BLOCKED-$stamp.txt"
if (Test-Path $blocked)         { throw "恢复验证已判失败，见 $blocked —— 禁止升级。" }
if (-not (Test-Path $gateOk))   { throw "未找到通行证 $gateOk —— 2.4.4 未通过或未执行，禁止升级。" }
"GATE-OK 确认：$gateOk"

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

| 文件 | 由哪一步产生 | 出问题时的作用 |
|---|---|---|
| `data\upgrade-nolock-evidence-<stamp>.txt` | 步骤 1 无写者举证（四节） | 证明备份时无人持库 |
| `data\quantstudio.db.bak.<stamp>` + `data\quantstudio.db.bak.<stamp>.wal`（如有 WAL） | 步骤 2.2 备份 | **唯一原件，冻结不动** |
| `COPY-FIDELITY-OK` 那几行（含六条判据 + BEFORE/AFTER 指纹） | 步骤 2.3 | 证明**拷贝忠实**（不等于副本完整） |
| `data\restorecheck_tool.py` | 步骤 2.4.1 | 比对工具（**须与通知内版本逐字一致**） |
| `data\upgrade-baseline-fp-<stamp>.json` | 步骤 2.4.2 生产端 | 恢复比对的**唯一基准**（含 `BASELINE-SHA256`） |
| `data\upgrade-restorecheck-<stamp>.json` + `.txt`（正例） | 步骤 2.4.4 消费端 | 证明隔离副本与基线逐表一致（含 `CONSUMED-BASELINE` 回显） |
| `data\upgrade-restorecheck-<stamp>-neg.txt`（负例） | 步骤 2.4.5 | **证明这套比对会报警**（鉴别力证据；无 WAL 时附合成 fixture 输出） |
| 步骤 5 自检完整输出 | 步骤 5 | 版本门结论 |

##### 2.4.12 判据实测状态总表（2026-11-17 终审补正，逐组声明）

> **为什么要有这一节**：本通知多处给出「判定逻辑」「预期读数」。为避免把**设计**读成**已实测**，
> 下面把各组判据的「设计 / 实测状态 / 客户侧含义」一次列清。**凡标「未实测」者，首次实跑即为首测。**

| 组 | 判据（本通知位置） | 设计 | 实测状态 | 客户侧含义 |
|---|---|---|---|---|
| **H2** | 升级后校验链主干：基线采集、schema 比对、`exit 1/4/5` 真阻断、步骤 3 通行证（§2.4.4 / §2.4.5） | 判定逻辑与退出码已写明 | **未实测**（U1–U12 执行数 = 0） | 按本通知执行；首次实跑即首测，异常请连同 `pypin-*.json` 回传 |
| **H3** | 留存链合法性：目标＝源被拒、目标在源子树被拒、复制后可用性判据（§2.4.0(1)(2) / §(8)） | 覆盖两种递归自吞；可用性走**机器比对**（实跑 `import duckdb` + `pragma_version()`） | **未实测**（U13–U17 执行数 = 0；另见 §步骤2 L611 声明） | 以 (4)(5) 实际输出为准；**不请客户承担首测** |
| **H4** | PS 直读不依赖子进程 stdout（中文路径 + 代码页） | 直读文件（UTF-8），关键字段走 ASCII(hex) 对拍，**不经 stdout** | **未实测**（U24；936 代码页下未验） | 已按设计规避该通道；未在 936 代码页实测 |
| **H6** | 包身份三值同源：`python` / `duckdb` / `source_id`（§2.4.0 `$PY_COLLECT`、§(8) 8b） | 三值同源采集 + 8b 相等三项 | **采集与比较链已实测**（U18 7/7、U26 8/8，**跨轮合并**）；**跨环境复制后**的同源性未测（U16/U17） | 8b 判据在已测面有效；复制场景以实跑为准 |
| **H5** | 62 GB 真实库的**耗时**与**峰值内存**（§2.4.6 / §四 / §六） | 内存常量级（不随表规模线性增长） | **62 GB 真实库未跑**；合成库实测：200 万行 × ~210 B（59.8 MB），`memory_limit='128MB'`，新写法 0.06 s 成功 | **不作耗时与峰值承诺**（见 §六） |
| **L1** | junction / 重解析点拒绝（§2.4.0(1) 逐级拒绝、§(5) 扫描） | **设计上拒绝**：从根到末级逐级检查，任一级是 reparse 即拒 | **未在真实 junction 上实测**（U19–U22 / U30 执行数 = 0） | 若您环境含 junction（部分 OneDrive / ProgramFiles 布局）**会被直接拒绝**——这是设计意图，不是故障 |
| — | 「正式版本」定义（§一 / §二） | 解释器 / DuckDB 包身份 / 放行政策三者一致 | 定义已闭合；**不设 PyPI GA 联网取证门** | 无需联网查 release 状态 |

> **三条不得反过来读**：①「未实测」**不等于**「已知有问题」——它是「我们没在您这类环境上跑过」；
> ②「已实测」的范围仅限本表写明的那一对（如 U18/U26 只覆盖中文路径 + 比较链，**不覆盖**客户库规模）；
> ③U18/U26 的 8/8 与 7/7 是**两次运行合并覆盖、两次驱动版本不同**，**不是**同版一次全绿。

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
> **自 2026-11-18 起，回退到 1.4.5 属已过期上游支持，仅作短期过渡**：**版本门仍放行、功能可用**，但不再获得上游修复；问题定位后应重新前移至 1.5.6。

## 六、性能提示（**仅覆盖实测表、实测数据与实测路径**）

> **⚠ 不作承诺（2026-11-17 终审补正）**：本通知**不承诺** 62 GB 真实库的**耗时**与**峰值内存**。
> 本节及 §2.4.6 的全部性能数据均来自**合成库**（200 万行 / 59.8 MB，`memory_limit='128MB'`）与**设计推理**（内存常量级、不随表规模线性增长），
> **不构成**对客户库规模下运行时间或内存占用的任何保证。首次实跑耗时以您环境实测为准；过慢请按 §2.4.7 裁剪口径降级。


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

| # | 本轮（第十七轮·r4 候选完整 S0b） | 结果 |
|---|---|---|
| 1 | 执行件与批准值一致性 | ✅ `DAFAB11E86C4671461B17893B923052CAC0AD64FB436337DE9D9CDAE8E8A24C0` **逐位一致** |
| 2 | 保持 PS 5.1 / 解释器，不临场紧凑化 | ✅ `5.1.26100.9711` / `Python311`（3.11.9），脚本未改一字符 |
| 3 | 新证据根先于 HOST | ✅ `STAGE BOOTSTRAP rc=0` 在 `HOST` 之前 |
| 4 | 原生命令先完整取输出并保存 rc | ✅ `PY-RUN-RC = 0`，无管道截断 |
| 5 | 新合成根仅三 txt | ✅ 实测 `a/b/c.txt` 各 1 B，0 子目录，0 重解析点 |
| 6 | 扫描五值 + rc0 | ✅ `0/1/3/0/0`，`scanRc=0` |
| 7 | PS1 BOM | ✅ `GATE-BOM = EF BB BF` |
| 8 | 目标 Parser 静态解析 | ✅ `GATE-PARSE = OK` |
| 9 | 基线消费闸接受 | ✅ 未抛异常，输出五值自述行 |
| 10 | 六负控按预期拒绝 | ✅ **6/6**（WALK/LSTAT/LINKS/DIRS/RC=8/RC=7） |
| 11 | 还原基线 | ✅ `STAGE RESTORE rc=0` |
| 12 | 首失败也落 verdict/stages/runner | ✅ 本轮无失败；机制已在 r3 验证过（r3 未落证缺陷已修） |
| 13 | 退出码 | **0** |
| 14 | 边界（不清理/不 junction/不 ACL/不复制/不 pip/不升级/不下发/不提交） | ✅ 全部遵守 |

**三次执行对照**

| 轮次 | stamp | 结果 | 原因 |
|---|---|---|---|
| r2 | `20261116T143939` | 五值全对，闸**解析**失败 | PS1 副本**无 BOM** ⇒ PS 5.1 按 GBK 读 ⇒ 中文乱码 |
| r3 | `20261116T154753` | **HOST 假失败** | `Select-Object -First 1` 提前关管道 ⇒ `$LASTEXITCODE = -1` |
| **r4** | **`20261116T195917`** | **PASS（13/13 阶段）** | 三处 runner 修订 + PS1 带 BOM |

**本轮对账（实测）**

| 项 | 读数 |
|---|---|
| 阶段 rc | **12 项为 `0`；NEGCONTROLS 一项的字面值是 `6/6`（不是 `0`）** —— 不得概称「每行均 rc=0」 |
| `scanRc` / 五值 | **0** / `LINKS 0`·`DIRS 1`·`ROOT_ENTRIES 3`·`WERR 0`·`LERR 0` |
| `GATE-BOM` / `GATE-PARSE` | **`EF BB BF`** / **OK** |
| 消费闸 | **接受**（`dirs=1, root_entries=3`） |
| 六负控 | **6/6 按预期原因被拒**；还原后基线仍通过 |
| 提取件逐字节核 | 扫描器 **2165 B 一致**；闸 **2209 B 含 BOM 一致** |
| 根目录 | 3 文件 / 0 子目录 / 0 重解析点，`st_file_attributes=0x10` |
| runner 退出码 | **0** |
| 证据文件 | **10 个**（含 `runner-identity.txt` / `hashes.tsv`） |
| 现场是否清理 | **未清理**（143939 / 154753 / 195917 + 候选目录全在位） |
| U1–U32 执行 | 仅 S0b 相关；**其余仍未执行** |
| **62 GB 耗时与峰值** | **仍未实测** |

**口径来源（内部随附）**：
- 放行集合与判定源：`quantstudio/pipeline/duckdb_version_policy.py`
- 旧门举证：`git show fdca384^:quantstudio/pipeline/duckdb_version_gate.py`
- 解释器实测：daemon PID 39200 `ExecutablePath=...\Python311\python.exe`
- 备份脚本顺序实证：仓库根 `check_wal.ps1` L117-128（备份）先于 L130-137（CHECKPOINT）
- 主库规模：`data/quantstudio.db` **62.06 GB**（2026-11-15 00:50 只读 stat）；WAL 当前已收敛（无文件）
- 性能量化：`docs/evidence/a14-executemany-scale-20261029.md`（含 §四 局限）
- `#23645` 暴露面核验：`docs/evidence/pt01-f2-truncate-20261029.md`
