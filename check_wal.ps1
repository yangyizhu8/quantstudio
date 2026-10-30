<#
.SYNOPSIS
  check_wal.ps1 — QuantStudio 数据库 WAL 巡检、探测与收敛（Windows，幂等）

.DESCRIPTION
  与 check_wal.sh（macOS）语义对齐：
    ① 探测优先：不论 WAL 大小，先尝试只读打开探测；打不开即判定"需收敛"，
       256MB 仅作性能阈值（不再作为唯一门槛）；
    ② duckdb 版本门：放行集合 = 1.4.5 ≤ x < 1.5 或 x == 1.5.6（判定源唯一：
       quantstudio/pipeline/duckdb_version_policy.py，本脚本只调用不自持规则）；
    ③ 日期列自动探测：stock_daily 实为 time 列（非 trade_date）；
    ④ 失败即中断：CHECKPOINT 非零退出不打印"完成"，给出明确指引；
    ⑤ 早退提示（早退 ≠ 已收敛，彻底收敛用 -Force）。

.PARAMETER Force
  跳过"无需处理"早退，强制执行收敛。
.PARAMETER RecoverStaleWal
  库打不开且 CHECKPOINT 失败时：备份后移走陈旧 WAL（.orphan.<时间戳>），再重验。
.PARAMETER BackupDb
  同时备份主库（大库复制较慢；默认只备份 WAL）。
.PARAMETER Py
  解释器（默认 python；可指定项目解释器路径）。
.PARAMETER SkipProcessCheck
  跳过 GUI/daemon 进程检查（仅测试/无人值守显式使用）。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File check_wal.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File check_wal.ps1 -Force -Py "C:\...\python.exe"

.NOTES
  铁律：写者进程运行中禁止 CHECKPOINT；禁止手工删除 .wal。
  实现注记：Python 片段一律写入临时 .py 文件后执行——PowerShell 5.1 向原生命令
  传参时会剥掉内嵌双引号（`-c "a(\"b\")"` 会损坏），临时文件方式彻底规避。
#>
[CmdletBinding()]
param(
  [switch]$Force,
  [switch]$RecoverStaleWal,
  [switch]$BackupDb,
  [string]$Py = 'python',
  [switch]$SkipProcessCheck
)

# 原生命令（python）的非零退出/回溯输出不当作终止错误——失败由 $LASTEXITCODE 显式判定。
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Db = Join-Path $Root 'data\quantstudio.db'
$Wal = "$Db.wal"
$Threshold = 256MB
$env:QS_WAL_DB = $Db

function Fail([string]$msg) { Write-Host "!! $msg" -ForegroundColor Red; exit 1 }

function Invoke-PyFile([string]$code) {
  # 写临时 py 文件执行；退出码经脚本作用域变量传出（函数输出留给 python 的 stdout/stderr）
  $f = Join-Path $env:TEMP ("qs_wal_" + [guid]::NewGuid().ToString('N') + ".py")
  Set-Content -Path $f -Value $code -Encoding UTF8
  & $Py $f
  $script:LAST_PY_RC = $LASTEXITCODE
  Remove-Item $f -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path $Db)) { Fail "未找到 $Db，请把本脚本放在项目根目录后运行" }

# 1. 版本门（判定源唯一：quantstudio/pipeline/duckdb_version_policy.py，本脚本不自持规则）
$ver = & $Py -c "import duckdb; print(duckdb.__version__)" 2>$null
if ($LASTEXITCODE -ne 0 -or -not $ver) { Fail "无法导入 duckdb（用 -Py 指定项目解释器）" }
Write-Host "duckdb = $ver"
$gateSrc = Join-Path $Root 'quantstudio\pipeline\duckdb_version_policy.py'
if (-not (Test-Path $gateSrc)) { Fail "找不到判定源 $gateSrc（请把本脚本与仓库一起放置）" }
& $Py $gateSrc --check $ver | Out-Null
$gateRc = $LASTEXITCODE
if ($gateRc -ne 0) {
  Fail "duckdb $ver 不在项目放行集合（1.4.5 ≤ x < 1.5 或 x == 1.5.6）。修复：pip install `"duckdb==1.5.6`""
}

# 2. 写者进程检查（GUI/daemon）
if (-not $SkipProcessCheck) {
  $procs = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and ($_.CommandLine -match 'quantstudio|main_gui|daemon\.py') })
  if ($procs.Count -gt 0) {
    foreach ($p in $procs) {
      $cl = [string]$p.CommandLine
      if ($cl.Length -gt 120) { $cl = $cl.Substring(0, 120) }
      Write-Host ("  持有候选: pid={0} {1}" -f $p.ProcessId, $cl)
    }
    Fail "有进程持有/可能持有数据库（GUI/daemon 正在使用）——请先关闭 GUI 并停止采集进程，再重跑本脚本"
  }
}
Write-Host "无进程持有 ✓"

# 3. WAL 体积
$size = if (Test-Path $Wal) { (Get-Item $Wal).Length } else { 0 }
Write-Host ("WAL = {0} 字节（{1:N0} MB），性能阈值 256MB" -f $size, ($size / 1MB))

# 4. 只读探测
$probeCode = @'
import os, duckdb
c = duckdb.connect(os.environ["QS_WAL_DB"], read_only=True)
c.execute("SELECT 1").fetchone()
c.close()
'@
Invoke-PyFile $probeCode 2>&1 | Out-Null
$probeOk = ($script:LAST_PY_RC -eq 0)
if ($probeOk) { Write-Host "只读探测通过 ✓" } else { Write-Host "只读探测失败（WAL 残留不匹配或库状态异常，判定需收敛）" }

if ($probeOk -and ($size -le $Threshold) -and (-not $Force)) {
  Write-Host "库可正常打开且 WAL 低于阈值，无需处理 ✓"
  if (Test-Path $Wal) {
    Write-Host ("提示：WAL 仍残留（{0} 字节），只读访问无法自愈；如需彻底收敛请加 -Force 重跑" -f $size)
  }
  exit 0
}

# 5. 备份（WAL 总备份；主库按 -BackupDb 显式备份）
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
if ($BackupDb) {
  Copy-Item $Db "$Db.bak.$ts" -Force
  Write-Host "已备份主库: $Db.bak.$ts（大库复制较慢）"
}
if (Test-Path $Wal) {
  Copy-Item $Wal "$Wal.bak.$ts" -Force
  Write-Host "已备份 WAL: $Wal.bak.$ts"
} else {
  Write-Host "无 WAL（跳过 WAL 备份）"
}

# 6. CHECKPOINT 收敛
$ckCode = @'
import os, duckdb, time
t = time.time()
c = duckdb.connect(os.environ["QS_WAL_DB"])
c.execute("CHECKPOINT")
c.close()
print("CHECKPOINT 完成 %.1fs" % (time.time() - t))
'@
Invoke-PyFile $ckCode
if ($script:LAST_PY_RC -ne 0) {
  if ($RecoverStaleWal -and (Test-Path $Wal)) {
    Write-Host "!! CHECKPOINT 失败（WAL 与主库不匹配/损坏）。按 -RecoverStaleWal 处理：备份已存在，移走陈旧 WAL 后重验" -ForegroundColor Yellow
    Move-Item $Wal "$Wal.orphan.$ts" -Force
    Write-Host "陈旧 WAL 已移至 $Wal.orphan.$ts（如怀疑 WAL 内有未合并写入，请保留该文件并联系技术支持）"
  }
  else {
    Fail "CHECKPOINT 失败：疑似 WAL 陈旧/损坏（详见上方报错）。处置：确认无进程占用后，加 -RecoverStaleWal 重跑（备份后移走陈旧 WAL）；或联系技术支持"
  }
}

# 7. 验证
$newSize = if (Test-Path $Wal) { (Get-Item $Wal).Length } else { 0 }
Write-Host "WAL 收敛后 = $newSize 字节"
$verifyCode = @'
import os, time, duckdb
t = time.time()
c = duckdb.connect(os.environ["QS_WAL_DB"], read_only=True)
try:
    print("trade_calendar rows:", c.execute("SELECT COUNT(*) FROM trade_calendar").fetchone()[0])
except Exception as e:
    print("（trade_calendar 不可用，跳过）", type(e).__name__)
try:
    cols = [r[1] for r in c.execute("PRAGMA table_info('stock_daily')").fetchall()]
    date_col = next((x for x in ("trade_date", "time", "date") if x in cols), None)
    if date_col:
        q = 'SELECT MAX("%s") FROM stock_daily' % date_col
        print("stock_daily last %s:" % date_col, c.execute(q).fetchone()[0])
    else:
        print("（stock_daily 无日期列，跳过）")
except Exception as e:
    print("（stock_daily 抽查失败，跳过）", type(e).__name__)
c.close()
print("只读打开+查询 %.2fs" % (time.time() - t))
'@
Invoke-PyFile $verifyCode
if ($script:LAST_PY_RC -ne 0) { Fail "验证阶段失败（详见上方报错）" }

Write-Host "== 完成（WAL 已收敛，库可正常打开）=="
