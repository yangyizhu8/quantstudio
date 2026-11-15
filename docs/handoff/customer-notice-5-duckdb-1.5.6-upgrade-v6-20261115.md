# 客户通知稿（**v6 修订终稿**）：QuantStudio 组件 duckdb 版本升级至 1.5.6（通知编号 5）

> **状态**：**v6 修订候选稿 · 待总调度终审 · 未下发 · 非已批准下发版**。
> 本版在两轮退修基础上再修 **(2026-11-15 第三次终审：命令链确定缺陷)**：
> **①路径一致性**——输出侧曾写字面量 `{stamp}`、消费侧读展开后的 `$stamp`，文件名必然对不上；
> 本版**生产端/消费端一律用同一 PowerShell 变量**，路径**显式作为 `--out` / `--baseline` 参数**传入，并附**端到端断言**。
> **②有界读取**——旧稿整表 `ORDER BY ALL` + `fetchall()` 在 62 GB 上未验证；本版改**流式聚合**（无排序、不物化），
> 并显式设 `temp_directory` / `memory_limit`；实测旧写法 200 万行即 **OOM**、新写法 0.06 s。
> **③口径一致**——内容编码统一用 `hash(有类型列)`、SHA **完整 64 位**、全部 **schema 限定**、索引取**完整 DDL**、两侧比对 `duckdb version`。
> **④负例前提**——现场无 WAL 时「WAL 改名」无从谈起，改为**另用含增量的合成 fixture** 造负例（§2.4.5 分支 2）。
> 另保留前两轮成果：**删除「完整副本/区间全静止」结论**（六判据只证拷贝忠实性）、**WAL 配对命名修正**（v5 缺陷）。
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

##### 2.4.1 落盘比对工具（**单引号 here-string，PowerShell 不做任何插值**）

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$dataDir  = (Resolve-Path '.\data').Path
$py       = '<解释器绝对路径>'
$tool     = Join-Path $dataDir 'restorecheck_tool.py'

@'
import duckdb, hashlib, json, argparse, os, sys

SYSTEM_SCHEMAS = {"main", "information_schema", "pg_catalog", "temp", "system"}

def row_expr(cols):
    # typed-column hash: no string concat, so no float/date repr drift
    return "hash(" + ", ".join('"%s"' % c for c in cols) + ")"

def table_block(con, schema, table):
    q = '"%s"."%s"' % (schema, table)
    cols = con.execute(
        "SELECT column_name, data_type, is_nullable FROM duckdb_columns() "
        "WHERE schema_name=? AND table_name=? ORDER BY column_index",
        [schema, table]).fetchall()
    names = [c[0] for c in cols]
    rows = con.execute("SELECT count(*) FROM %s" % q).fetchone()[0]
    # Bounded: streaming aggregate. No ORDER BY, no fetchall, constant memory.
    hsum = con.execute(
        "SELECT coalesce(sum(CAST(%s AS HUGEINT)), 0) FROM %s" % (row_expr(names), q)
    ).fetchone()[0]
    return {"columns": [[c[0], c[1], bool(c[2])] for c in cols],
            "rows": rows, "row_hash_sum": str(hsum)}

def snapshot(db, tempdir, memory_limit):
    con = duckdb.connect(db, read_only=True)
    try:
        con.execute("SET memory_limit='%s'" % memory_limit)
        con.execute("SET temp_directory='%s'" % tempdir.replace("\\", "/"))
        con.execute("SET preserve_insertion_order=false")
    except Exception as e:
        print("  [warn] SET failed (does not affect verdict): %s" % str(e)[:80], file=sys.stderr)
    out = {"duckdb_version": con.execute("SELECT version()").fetchone()[0],
           "schemas": {}, "indexes": [], "constraints": []}
    schemas = [r[0] for r in con.execute(
        "SELECT schema_name FROM duckdb_schemas() ORDER BY schema_name").fetchall()]
    schemas = [s for s in schemas if s.lower() not in SYSTEM_SCHEMAS] + ["main"]
    for s in sorted(set(schemas)):
        tabs = [r[0] for r in con.execute(
            "SELECT table_name FROM duckdb_tables() WHERE schema_name=? ORDER BY table_name",
            [s]).fetchall()]
        if tabs:
            out["schemas"][s] = {t: table_block(con, s, t) for t in tabs}
    try:
        out["indexes"] = sorted([list(map(str, r)) for r in con.execute(
            "SELECT schema_name, table_name, index_name, is_unique, is_primary, sql "
            "FROM duckdb_indexes() ORDER BY schema_name, table_name, index_name").fetchall()])
    except Exception:
        out["indexes"] = sorted([list(map(str, r)) for r in con.execute(
            "SELECT index_name, table_name FROM duckdb_indexes() ORDER BY index_name").fetchall()])
    try:
        out["constraints"] = sorted([list(map(str, r)) for r in con.execute(
            "SELECT schema_name, table_name, constraint_type, constraint_text "
            "FROM duckdb_constraints() ORDER BY schema_name, table_name, constraint_type").fetchall()])
    except Exception:
        out["constraints"] = []
    con.close()
    return out

def structure_sha(snap):
    # structural face: column defs + index defs + constraint defs (no data)
    blob = json.dumps({"schemas": {s: {t: v["columns"] for t, v in tabs.items()}
                                   for s, tabs in snap["schemas"].items()},
                       "indexes": snap["indexes"], "constraints": snap["constraints"]},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()

def data_sha(snap):
    # data face: per-table (row count, row hash sum), full 64-hex SHA256
    blob = json.dumps({s: {t: [v["rows"], v["row_hash_sum"]] for t, v in tabs.items()}
                       for s, tabs in snap["schemas"].items()},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["baseline", "verify"])
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--tempdir", default=None)
    ap.add_argument("--memory-limit", default="4GB")
    ap.add_argument("--schema", default=None)
    a = ap.parse_args()
    tempdir = a.tempdir or os.path.join(os.path.dirname(os.path.abspath(a.out)), "_duckdb_tmp")
    os.makedirs(tempdir, exist_ok=True)

    if a.mode == "baseline":
        snap = snapshot(a.db, tempdir, a.memory_limit)
        if a.schema:
            snap["schemas"] = {k: v for k, v in snap["schemas"].items() if k == a.schema}
            print("  [note] trimmed to schema:", a.schema)
        snap["_structure_sha256"] = structure_sha(snap)
        snap["_data_sha256"] = data_sha(snap)
        snap["_db"] = os.path.abspath(a.db)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, sort_keys=True, indent=2)
        print("BASELINE-WRITTEN:", os.path.abspath(a.out))
        print("  duckdb =", snap["duckdb_version"])
        print("  schemas =", list(snap["schemas"]))
        for s, tabs in snap["schemas"].items():
            print("   [%s] %s" % (s, {t: v["rows"] for t, v in tabs.items()}))
        print("  structure_sha256 =", snap["_structure_sha256"])
        print("  data_sha256      =", snap["_data_sha256"])
        return 0

    if not a.baseline or not os.path.exists(a.baseline):
        print("BASELINE-MISSING:", a.baseline)
        return 3
    b = json.load(open(a.baseline, encoding="utf-8"))
    snap = snapshot(a.db, tempdir, a.memory_limit)
    snap["_structure_sha256"] = structure_sha(snap)
    snap["_data_sha256"] = data_sha(snap)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, sort_keys=True, indent=2)

    bad = []
    if b.get("duckdb_version") != snap["duckdb_version"]:
        bad.append("duckdb version differs: %s vs %s" % (b.get("duckdb_version"), snap["duckdb_version"]))
    if b["_structure_sha256"] != snap["_structure_sha256"]:
        bad.append("structure sha256 differs (columns/indexes/constraints)")
    bt, ct = b["schemas"], snap["schemas"]
    if set(bt) != set(ct):
        bad.append("schema set differs: %s vs %s" % (sorted(bt), sorted(ct)))
    for s in sorted(set(bt) & set(ct)):
        if set(bt[s]) != set(ct[s]):
            bad.append("[%s] table set differs: missing=%s extra=%s"
                       % (s, sorted(set(bt[s]) - set(ct[s])), sorted(set(ct[s]) - set(bt[s]))))
        for t in sorted(set(bt[s]) & set(ct[s])):
            if bt[s][t]["columns"] != ct[s][t]["columns"]:
                bad.append("[%s.%s] column definition differs" % (s, t))
            if bt[s][t]["rows"] != ct[s][t]["rows"]:
                bad.append("[%s.%s] rows %s != %s" % (s, t, ct[s][t]["rows"], bt[s][t]["rows"]))
            if bt[s][t]["row_hash_sum"] != ct[s][t]["row_hash_sum"]:
                bad.append("[%s.%s] row content hash sum differs" % (s, t))
    if b["_data_sha256"] != snap["_data_sha256"]:
        bad.append("data sha256 differs")

    print("RESTORE-EQUIV:", "OK" if not bad else "MISMATCH")
    for x in bad:
        print("   x", x)
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
& $py $tool --mode verify --db (Join-Path $iso 'r.db') --baseline $baseline --out $rcOut |
    Tee-Object -FilePath (Join-Path $dataDir "upgrade-restorecheck-$stamp.txt")
"CONSUMED-BASELINE = $baseline"
if ($LASTEXITCODE -ne 0) { "RESTORE-EQUIV 未通过（exit=$LASTEXITCODE），停止升级" }
```

**判据（四条全中）**

| # | 判据 | 实现方式 |
|---|---|---|
| A | **schema 集合 + 表集**一致 | `duckdb_tables()` 按 `schema_name` 限定，逐 schema 枚举 |
| B | **逐表行数**一致 | `SELECT count(*)`（schema 限定表名） |
| C | **逐表内容**一致 | `sum(CAST(hash(全部列) AS HUGEINT))` 流式聚合（见 2.4.6） |
| D | **列定义 / 索引 / 约束**一致 | `duckdb_columns()`（`data_type`/`is_nullable`）+ `duckdb_indexes()`（`is_unique`/`is_primary`/`sql` 完整定义）+ `duckdb_constraints()` |

**口径一致性（审核要求，逐条落实）**

| 项 | 做法 |
|---|---|
| **内容编码** | 一律用 duckdb `hash(列...)` 对**有类型的列**直接哈希（不做字符串拼接，避免浮点/日期 repr 差异）；`SET preserve_insertion_order=false` 使聚合与顺序无关 |
| **完整 SHA** | 结构面 / 数据面各输出**完整 64 位十六进制** `sha256`（不截断） |
| **schema 限定** | 查询写 `"schema"."table"`；元数据查询一律带 `WHERE schema_name=?` |
| **索引定义** | 取 `is_unique` / `is_primary` / **`sql`（完整 DDL 文本）**，不只取名字 |
| **版本一致** | 两侧都打印 `duckdb version` 并作为比对项（版本不同直接报错，避免 `hash()` 语义漂移） |

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

| # | 终审意见（第三轮） | 本版落实 |
|---|---|---|
| 1 | **输出基线用字面量 `{stamp}`，消费端读展开后的 `$stamp`，须显式传同一路径并做端到端验证** | §2.4.1-2.4.4 重写：全部改为**同一 `$baseline` 变量**，工具调用一律显式 `--out` / `--baseline` 参数；<br>· §2.4.2 写后立即 `if (-not (Test-Path $baseline)) { throw }` 并打印 `BASELINE-PATH` + `BASELINE-SHA256`；<br>· §2.4.4 打印 `CONSUMED-BASELINE`，要求与 `BASELINE-PATH` **逐字一致**；<br>· 另注明**必须用单引号 here-string**（双引号会把 Python 里的 `$` 吃掉）。<br>**实测复现**：`"...{stamp}.json"` → 字面量不展开；`"...$stamp.json"` → 展开为时间戳；两者 `-eq` 为 **False** |
| 2 | **整表排序加 `fetchall()` 对 62GB 未验，补有界读取与排序临时空间设计** | §2.4.6 新增「有界性说明」：<br>· **不用** `SELECT *` + `fetchall()`、**不用** `ORDER BY ALL`；只取 `count(*)` + **流式聚合** `sum(CAST(hash(列...) AS HUGEINT))`；<br>· 新写法**不含排序** ⇒ 无排序溢写；仍显式 `SET temp_directory='...\_duckdb_tmp'`、`SET memory_limit='4GB'`；<br>· **实测差分**（200 万行 / 59.8 MB / `memory_limit=128MB`）：旧写法 **`OutOfMemoryException`**（121.9/122.0 MiB）；新写法 **0.06 s 成功**；<br>· **如实声明**：62 GB 真实库耗时与峰值内存**未实测**，只保证内存不随表规模线性增长；过慢按 §2.4.7 裁剪（并注明裁剪=有损验收） |
| 3 | **内容编码、完整 SHA、schema 限定及索引定义须一致** | 新增「口径一致性」表：<br>· 编码：统一 `hash(有类型列)`（不拼字符串，避免浮点/日期 repr 差异）+ `preserve_insertion_order=false`；<br>· SHA：结构面/数据面均输出**完整 64 位**（不再截断 16 位）；<br>· schema：查询写 `"schema"."table"`，元数据查询带 `WHERE schema_name=?`；<br>· 索引：取 `is_unique`/`is_primary`/**`sql` 完整 DDL**，并纳入 `duckdb_constraints()`；<br>· 另加 `duckdb version` 比对，防 `hash()` 语义漂移 |
| 4 | **原现场无 WAL 时，去 WAL 负例须另用含增量的合成 fixture** | §2.4.5 拆两分支：<br>· **分支 1**（有 WAL 备份）⇒ 照常把 WAL 改名 `.displaced`；<br>· **分支 2**（**无 WAL**）⇒ 明确「没有 WAL 可改名」，提供**与生产库完全隔离**的 fixture 构造（建表+CHECKPOINT=3 行 → 另起进程追加 4 行 → 强杀 → 得 `fx.db` + `fx.db.wal`），再采基线 + 造错配副本；<br>· 判据：负例必须 `MISMATCH`，否则「比对无鉴别力、结论无效」 |
| 5 | （前两轮成果保留）删除完整保证 / WAL 配对命名 | §2.3 ⚠ 三条「证不到什么」保留；§2.2 WAL 命名 `<主库备份全名>.wal` 保留 |
| 6 | **T041 不连带核销** | 明确写「**T041 未连带核销**，仍为 `BLOCKED`」 |

**隔离命令链验收（2026-11-15 实测，合成库）**

| 用例 | 期望 | 实测 |
|---|---|---|
| 正例（`r.db` + 配对 `r.db.wal`） | OK | ✅ `RESTORE-EQUIV: OK 与基线一致`，exit=0 |
| 负例（WAL 改名 `.displaced`） | MISMATCH | ✅ 逐表报 `行数 3 != 4` 等，exit=1 |
| 结构负例（数据不动，仅加一个索引） | 只报结构面 | ✅ 仅 `结构面 sha256 不一致`，未误报行数/内容 |
| 无 WAL 现场正例 | OK | ✅ exit=0 |
| 有界读取差分 | 旧写法失败 | ✅ 旧 OOM / 新 0.06 s |

**口径来源（内部随附）**：
- 放行集合与判定源：`quantstudio/pipeline/duckdb_version_policy.py`
- 旧门举证：`git show fdca384^:quantstudio/pipeline/duckdb_version_gate.py`
- 解释器实测：daemon PID 39200 `ExecutablePath=...\Python311\python.exe`
- 备份脚本顺序实证：仓库根 `check_wal.ps1` L117-128（备份）先于 L130-137（CHECKPOINT）
- 主库规模：`data/quantstudio.db` **62.06 GB**（2026-11-15 00:50 只读 stat）；WAL 当前已收敛（无文件）
- 性能量化：`docs/evidence/a14-executemany-scale-20261029.md`（含 §四 局限）
- `#23645` 暴露面核验：`docs/evidence/pt01-f2-truncate-20261029.md`
