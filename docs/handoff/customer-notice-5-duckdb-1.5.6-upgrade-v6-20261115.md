# 客户通知稿（**v6 修订终稿**）：QuantStudio 组件 duckdb 版本升级至 1.5.6（通知编号 5）

> **状态**：**v6 修订候选稿 · 待总调度终审 · 未下发 · 非已批准下发版**。
> 本版为**第六轮**修缺陷（2026-11-15 终审：空 schema 漏报 / SET 失败不得续跑 / 非 0 须真阻断 / 解释器留存前置）：
> **①空 schema 纳入登记**——旧稿 `if blk:` 会把**没有表的 schema 整块丢掉** ⇒ schema 增删**漏报**。
> 本版**覆盖范围内每个 schema 一律登记**（空 schema 记为 `{}` 并打印 `<EMPTY-SCHEMA>`），
> 使 `schema_set` / `table_set` 两面都能抓到空 schema 的出现与消失。
> **②必需 `SET` 失败即停止**——`memory_limit` / `temp_directory` 失败时**不再打印 warn 后按默认值继续**，
> 而是 `SETUP-FAILED` + **exit=6** 中止（读取未受限时，任何后续摘要都没有意义）。
> 仅 `preserve_insertion_order` 属**可选项**（不影响聚合摘要），失败只记 note。
> **③非 0 真正阻断后续**——消费端 `$LASTEXITCODE≠0` 时**撤销历史通行证、写 `UPGRADE-BLOCKED`、`throw` 中止**；
> **步骤 3 开头加硬闸自检**，通行证缺失或存在阻断标记即拒绝执行 `git pull`（双重拦截，防人工跳过）。
> **④原版本解释器留存进入实际前置**——新增 **2.4.0**：升级前固化 `pypin`（python/路径/duckdb 版本）并**三选一留存**
> （A 复制 `sys.prefix` / B `pip download` 留存 wheel / C 记录重建法），**三者皆失败即禁止升级**；
> 升级后须先证明「验证用的就是原版本解释器」，否则同版本链不成立。
> 新增 **2.4.8 预期读数对照表**：空 schema、`SET` 失败、exit=1/3/4/5/6、步骤 3 被拦，逐项写明**预期输出与退出码**。
> 保留前几轮：同版本版本门（跨版本 exit=5 不判成功）、撤回 192 位/2^96 强度承诺、工程摘要定位、
> 七面独立核验、全文件 SHA 不替代逻辑验证、WAL 配对命名、有界流式聚合、错配 WAL 负例。
> **T041 未连带核销**，仍为 `BLOCKED`。

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

##### 2.4.0 前置：**留存「原版本解释器」**（这一步做不到，就不该开始升级）

**为什么必须前置**：版本门要求「基线」与「验证」**同版本同 source_id**（见下方版本门）。
而步骤 4 会把 `duckdb` 就地升到 1.5.6 —— **同一个 `$py` 升级后必然不再是 1.4.5**。
所以**必须在升级前**把原版本环境留存/可重建的证据做出来，否则升级后**无法走同版本链**，
恢复验证只能落进 `CROSS-VERSION-INCOMPARABLE`（exit=5，不判成功）。

> **三选一，必须完成其一并留证**（A 优先）：
> | 方案 | 做法 | 适用 |
> |---|---|---|
> | **A 整体复制环境** | 复制 `sys.prefix` 整个目录到 `.\data\pykeep-$stamp` | 离线可用，最稳 |
> | **B 留存 wheel** | `pip download duckdb==<原版本> -d .\data\duckdb-wheel-$stamp --no-deps` | 环境大、有网/有内网源 |
> | **C 记录重建法** | 记下原版本号与重建命令，升级后新建 venv 装回同版本 | 前两者都不可行时的兜底 |
>
> **三者都失败 ⇒ 停止，不要升级**（没有原环境就没有同版本链，恢复验证给不出成功结论）。

```powershell
# —— 2.4.0 统一定义本节的公共变量（2.4.1 起**不再重定义**，避免 stamp 漂移）——
$dataDir = (Resolve-Path '.\data').Path
$py      = '<解释器绝对路径>'        # 必须是 §1.2 已确认的那个 python
$stamp   = Get-Date -Format 'yyyyMMdd_HHmmss'

# (1) 固化解释器身份（python 版本 + 可执行路径 + duckdb 版本）
$pyPin  = Join-Path $dataDir "pypin-$stamp.json"
$pyInfo = & $py -c "import sys,json,duckdb;print(json.dumps({'python':sys.version.split()[0],'exe':sys.executable,'prefix':sys.prefix,'duckdb':duckdb.__version__},ensure_ascii=False))"
if ($LASTEXITCODE -ne 0) { throw "解释器不可用，禁止开始升级：$py" }
$pyInfo | Set-Content -Path $pyPin -Encoding UTF8
$pyObj = $pyInfo | ConvertFrom-Json
"PY-PINNED     = $pyPin"
"PY-EXE        = $($pyObj.exe)"
"PY-PREFIX     = $($pyObj.prefix)"
"PY-DUCKDB     = $($pyObj.duckdb)"

# (2) 留存原环境：A 优先 → B → C（三者都失败就停）
$pyKeep = Join-Path $dataDir "pykeep-$stamp"
$wheelDir = Join-Path $dataDir "duckdb-wheel-$stamp"
$keepMode = $null
try {
    robocopy $pyObj.prefix $pyKeep /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -lt 8) { $keepMode = "A-copy-env" }
} catch { }
if (-not $keepMode) {
    New-Item -ItemType Directory -Path $wheelDir -Force | Out-Null
    & $py -m pip download "duckdb==$($pyObj.duckdb)" -d $wheelDir --no-deps
    if ($LASTEXITCODE -eq 0) { $keepMode = "B-wheel" }
}
if (-not $keepMode) {
    throw "无法留存原版本解释器（A 复制环境 / B 留存 wheel 均失败）。请改用 C：记下 duckdb=$($pyObj.duckdb)，升级后新建 venv 装回该版本，并把命令留证后再继续。在此之前禁止升级。"
}
"PY-KEEP-MODE  = $keepMode"
"PY-KEEP-PATH  = $(if ($keepMode -eq 'A-copy-env') { $pyKeep } else { $wheelDir })"
```

**升级后、做 2.4.4 同版本链验证之前，必须先证明「我用的是原版本解释器」**：

```powershell
# 用留存环境（A）跑验证；若走 B/C，则先重建同版本 venv 再指向它
$pyVerify = Join-Path $pyKeep 'python.exe'          # A 方案；venv 场景可能是 Scripts\python.exe
if (-not (Test-Path $pyVerify)) { $pyVerify = Join-Path $pyKeep 'Scripts\python.exe' }
if (-not (Test-Path $pyVerify)) { throw "留存环境里找不到 python.exe，无法走同版本链（A 方案不可用，请按 B/C 重建）" }
$nowInfo = & $pyVerify -c "import sys,json,duckdb;print(json.dumps({'python':sys.version.split()[0],'duckdb':duckdb.__version__},ensure_ascii=False))"
"PY-VERIFY     = $pyVerify"
$nowInfo
# 硬要求：这里的 duckdb 版本必须 == $pyObj.duckdb；否则不是同版本链，验证结论无效
```

> **⚠ 未实测声明（如实）**：本节 A/B/C 的具体命令**未在客户现场验证过**（我们只做了语法与流程层面核对）。
> `sys.prefix` 复制、venv 搬迁后 `python.exe` 的可用性，**取决于您的环境布局**——
> 请以 `PY-VERIFY` 段打印出的 duckdb 版本是否等于 `PY-DUCKDB` 为准；不等就走 B/C 重建。

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
$rcOut = Join-Path $dataDir "upgrade-restorecheck-$stamp.json"
$rcLog = Join-Path $dataDir "upgrade-restorecheck-$stamp.txt"
& $py $tool --mode verify --db (Join-Path $iso 'r.db') --baseline $baseline --out $rcOut |
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
& $py $tool --mode verify --db (Join-Path $isoNeg 'r.db') --baseline $baseline --out (Join-Path $dataDir "upgrade-restorecheck-$stamp-neg.json") |
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
& $py $tool --mode baseline --db $fx --out $fxBase
Copy-Item $fx (Join-Path $fxDir 'neg.db') -Force
Copy-Item "$fx.wal" (Join-Path $fxDir 'neg.db.wal.displaced') -Force
& $py $tool --mode verify --db (Join-Path $fxDir 'neg.db') --baseline $fxBase --out (Join-Path $fxDir 'fx-neg.json')
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

##### 2.4.7 可裁剪（时间不够时的降级口径）

```powershell
& $py $tool --mode baseline --db (Join-Path $dataDir 'quantstudio.db') --out $baseline --schema main
```

> 裁剪会**缩小验证覆盖**，属「有损验收」：裁剪后通过的结论**只覆盖被比对到的表**，
> 不得表述为「整个备份已完整验证」。时间允许时**不要裁剪**。

> **务必注意**：**只有在您自己的真实主库上跑出正例 `RESTORE-EQUIV: OK` 且负例 `MISMATCH`，才算「备份可恢复」被验证过。**
> 我们在开发环境用合成库跑通了**同一条命令链**（正例 OK / WAL 错配负例 MISMATCH / 结构负例 MISMATCH），
> 那只能证明**命令与判据可用**，**不能代替**您真实窗口的结果——**没跑过就等于没验证**。

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

| # | 终审意见（第六轮） | 本版落实 |
|---|---|---|
| 1 | **snapshot 登记覆盖范围内的空 schema，避免增删漏报** | 旧代码 `blk = schema_block(...); if blk: out["schemas"][s] = blk` —— **空 schema 被整块丢弃**，于是"某 schema 变空""空 schema 新增/消失"在 `schema_set`、`table_set` 两面**都不可见**，正是该判据要抓的变更被漏掉。<br>改为：**覆盖范围内每个 schema 一律登记**，空 schema 记 `{}`；`table_set` 对该 schema 给**显式空列表**；基线打印时标注 `<EMPTY-SCHEMA>` 让操作者看得见。<br>**预期**（见 2.4.8 第 2/3 行）：存在空 schema ⇒ 基线出现 `[<schema>] {}   <EMPTY-SCHEMA>` 且该名在 `schemas=` 内；空 schema 增删 ⇒ `x SCHEMA SET: [...] vs [...]`，**exit=1** |
| 2 | **必需 memory/temp 设置失败须停止，不能 warn 后默认继续** | 旧代码 `except Exception: print(" [warn] SET failed")` 后照常往下跑。<br>改为：`memory_limit`、`temp_directory` 各自包 try，失败 **`raise SetupError`** ⇒ `main()` 捕获打印 `SETUP-FAILED` + 失败项 + `Refusing to continue on an implicit default (exit=6)` ⇒ **exit=6**。<br>`preserve_insertion_order` 明确为**可选项**（聚合与顺序无关，不影响摘要），失败只 `[note] ... declined` 不阻断。<br>另加 `try/finally` 确保异常路径也 `close()` 连接 |
| 3 | **verify 非 0 的 PowerShell 链须真正阻断后续步骤，不能只打印"停止升级"** | 旧代码 `if ($LASTEXITCODE -ne 0) { "…停止升级" }` 只是**打印一行字符串**，后续语句照跑。<br>改为**双重阻断**：①先把 `$LASTEXITCODE` 落到 `$rc`；②`$rc≠0` ⇒ **撤销历史 `UPGRADE-GATE-OK`**、按退出码给出 1/3/4/5/6 的**具体原因**、写 `UPGRADE-BLOCKED-$stamp.txt`、**`throw` 中止**；③**步骤 3 开头硬闸**：存在 `UPGRADE-BLOCKED` 或缺少 `UPGRADE-GATE-OK` 即 `throw`，`git pull` 不执行；④仅 `exit=0` 才签发通行证。<br>并说明：逐段粘贴时 `throw` 不会关闭控制台，**真正拦人的是阻断标记 + 步骤 3 自检**，两道都要在 |
| 4 | **原版本解释器保留/同版重建证据放入实际前置** | 新增 **2.4.0 前置**（在 2.4.1 之前执行）：<br>· 固化 `pypin-$stamp.json`（python 版本 / `sys.executable` / `sys.prefix` / `duckdb` 版本）；<br>· **三选一留存**：A `robocopy sys.prefix → data\pykeep-$stamp`；B `pip download duckdb==<原版> -d data\duckdb-wheel-$stamp --no-deps`；C 记录重建法。<br>· **三者皆失败 ⇒ `throw` 禁止升级**（没有原环境就没有同版本链，恢复验证只能落 exit=5）。<br>· 升级后验证前，须用留存环境跑 `PY-VERIFY` 段并**核 duckdb 版本 == `PY-DUCKDB`**。<br>· 公共变量 `$stamp/$dataDir/$py` 统一在 2.4.0 定义，**2.4.1 起不再重定义**（防 stamp 漂移——上一版 `{stamp}` 缺陷的同类风险） |
| 5 | **给空 schema、SET 失败、exit 1/4/5 阻断后续的预期** | 新增 **2.4.8 预期读数对照表**（12 行）：正常 OK/0、空 schema 存在、空 schema 增删/1、`SET memory_limit` 失败/6、`SET temp_directory` 失败/6、可选 SET 失败/0（继续）、内容不一致/1、探针不符/4、跨版本/5、基线缺失/3、步骤 3 无通行证被拦、步骤 3 有阻断标记被拦；每行含**预期关键输出 + 预期 exit + 预期后续行为**，并给出"自己演练阻断是否生效"的合成库步骤 |
| 6 | （历轮成果保留）同版本门 / 撤强度承诺 / 七面覆盖 / 全文件 SHA 定位 / WAL 配对命名 / 有界读取 | 均保留 |

**本轮实测与未实测（如实）**

| 项 | 状态 |
|---|---|
| 工具草稿语法（抽出源码 `py_compile`） | ✅ **通过**（12687 B，纯 ASCII） |
| 空 schema 登记 / `SetupError`→exit 6 / `throw` 阻断 / 2.4.0 前置 | ⚠ **仅静态修改，未跑行为补测**（本轮边界：不跑新补测） |
| 2.4.0 的 A/B/C 命令 | ⚠ **未在任何环境验证**（依赖客户侧 python 布局，已写入未实测声明） |
| **62 GB 耗时与峰值内存** | **仍未实测**，继续明列 |

**口径来源（内部随附）**：
- 放行集合与判定源：`quantstudio/pipeline/duckdb_version_policy.py`
- 旧门举证：`git show fdca384^:quantstudio/pipeline/duckdb_version_gate.py`
- 解释器实测：daemon PID 39200 `ExecutablePath=...\Python311\python.exe`
- 备份脚本顺序实证：仓库根 `check_wal.ps1` L117-128（备份）先于 L130-137（CHECKPOINT）
- 主库规模：`data/quantstudio.db` **62.06 GB**（2026-11-15 00:50 只读 stat）；WAL 当前已收敛（无文件）
- 性能量化：`docs/evidence/a14-executemany-scale-20261029.md`（含 §四 局限）
- `#23645` 暴露面核验：`docs/evidence/pt01-f2-truncate-20261029.md`
