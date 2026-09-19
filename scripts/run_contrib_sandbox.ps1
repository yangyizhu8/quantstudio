<#
.SYNOPSIS
  外部贡献策略的本地沙箱试跑（策略生态准入 · D3，2026-09-19）。

.DESCRIPTION
  在隔离环境（只读影子库 + 临时工作目录 + 断网）中对贡献策略跑一次短窗回测。
  已批规格三条，本脚本逐条落实：
    1) 共享影子副本 —— 建一次、按需刷新（不每审一拷；48.8GB 级库不可复制式消耗）；
    2) 磁盘余量守卫 —— 仅在需要**新建**影子库时校验 D 盘余量；
    3) 三层断网降级 —— 管理员防火墙规则 → 环境代理黑洞（免管理员）→ 告警不阻断。

  边界声明：本脚本**不判断安全性**，只回答「这段代码能不能跑完、跑多久、有无异常」。
  安全性由 D2 静态门 + 所有者人工审查承担。数据源恒为**只读影子库**，
  绝不接触生产库（可由日志审计）。

.PARAMETER StrategyPath
  待试跑的策略 .py 路径。
.PARAMETER Start / End
  回测窗口（短窗；默认半年内，避免贡献审查耗时过长）。
.PARAMETER TimeoutSec
  硬超时（秒）。超时即杀**整棵进程树**，防残留。
.PARAMETER RefreshShadow
  从 -RefreshSource 重新生成共享影子库（默认不刷新，直接复用现有副本）。
.PARAMETER RefreshSource
  刷新源（静态备份库路径，如 data/quantstudio_backup_20260912.db）。
.PARAMETER SkipNetworkBlock
  跳过断网处置（仅调试用；正式审查不应使用）。

.EXAMPLE
  pwsh -File scripts/run_contrib_sandbox.ps1 -StrategyPath .\quantstudio\backtest\strategies\双均线策略.py
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$StrategyPath,
  [string]$Start = '2026-01-02',
  [string]$End = '2026-06-30',
  [int]$TimeoutSec = 300,
  [string]$ShadowDb = '',
  [string]$RefreshSource = '',
  [switch]$RefreshShadow,
  [switch]$SkipNetworkBlock
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($ShadowDb)) {
  $ShadowDb = Join-Path $Root 'agent_workspace\backtest_readonly\quantstudio.db'
}
$ReviewDir = Join-Path $Root 'agent_workspace\contrib_review'
$RunnerPy = Join-Path $PSScriptRoot '_contrib_sandbox_runner.py'
$Python = 'python'

function Write-Step([string]$msg) { Write-Host ("[sandbox] " + $msg) }

# ---------------------------------------------------------------- 前置校验
if (-not (Test-Path -LiteralPath $StrategyPath)) {
  throw "策略文件不存在：$StrategyPath"
}
if (-not (Test-Path -LiteralPath $RunnerPy)) {
  throw "缺少执行器：$RunnerPy"
}
$StrategyPath = (Resolve-Path -LiteralPath $StrategyPath).Path

# ---------------------------------------------------------------- ② 磁盘余量守卫
if ($RefreshShadow -or -not (Test-Path -LiteralPath $ShadowDb)) {
  $drive = (Split-Path -Qualifier $ShadowDb).TrimEnd(':')
  $free = (Get-PSDrive -Name $drive).Free
  $freeGB = [math]::Round($free / 1GB, 1)
  $needGB = 48
  Write-Step "目标盘 $drive 余量 ${freeGB}GB（需要约 ${needGB}GB）"
  if ($freeGB -lt $needGB) {
    throw "磁盘余量不足：${freeGB}GB < ${needGB}GB —— 拒绝生成影子库（防 D 盘告急）"
  }
}

# ---------------------------------------------------------------- ① 共享影子库
if ($RefreshShadow) {
  if ([string]::IsNullOrWhiteSpace($RefreshSource)) {
    throw "指定 -RefreshShadow 时必须同时给出 -RefreshSource（静态备份库路径）"
  }
  if (-not (Test-Path -LiteralPath $RefreshSource)) {
    throw "刷新源不存在：$RefreshSource"
  }
  Write-Step "刷新共享影子库：$RefreshSource -> $ShadowDb"
  $dstDir = Split-Path -Parent $ShadowDb
  if (-not (Test-Path -LiteralPath $dstDir)) { New-Item -ItemType Directory -Force -Path $dstDir | Out-Null }
  Copy-Item -LiteralPath $RefreshSource -Destination $ShadowDb -Force
}
if (-not (Test-Path -LiteralPath $ShadowDb)) {
  throw "影子库不存在且未指定刷新源：$ShadowDb"
}
$shadowGB = [math]::Round((Get-Item -LiteralPath $ShadowDb).Length / 1GB, 1)
Write-Step "使用共享影子库：$ShadowDb (${shadowGB}GB) —— 只读，不接触生产库"

# ---------------------------------------------------------------- 工作目录
if (-not (Test-Path -LiteralPath $ReviewDir)) { New-Item -ItemType Directory -Force -Path $ReviewDir | Out-Null }
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$work = Join-Path $ReviewDir $stamp
New-Item -ItemType Directory -Force -Path $work | Out-Null
$outJson = Join-Path $work 'result.json'
$logFile = Join-Path $work 'run.log'

# ---------------------------------------------------------------- ③ 三层断网降级
$netMode = 'none'
if (-not $SkipNetworkBlock) {
  # L1 管理员防火墙规则（出站阻止本解释器）
  try {
    $pyExe = (Get-Command $Python -ErrorAction Stop).Source
    if ($pyExe) {
      $ruleName = 'QS-Contrib-Sandbox-Block'
      if (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue) {
        Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
      }
      New-NetFirewallRule -DisplayName $ruleName -Direction Outbound `
        -Program $pyExe -Action Block -ErrorAction Stop | Out-Null
      $netMode = 'L1-firewall'
      Write-Step "断网 L1：已建出站阻断规则（$pyExe）"
    }
  } catch {
    # L2 代理黑洞（免管理员；绝大多数网络库遵守 *_proxy 环境变量）
    $env:HTTP_PROXY = 'http://127.0.0.1:9'
    $env:HTTPS_PROXY = 'http://127.0.0.1:9'
    $env:http_proxy = 'http://127.0.0.1:9'
    $env:https_proxy = 'http://127.0.0.1:9'
    $env:NO_PROXY = ''
    $env:no_proxy = ''
    $netMode = 'L2-proxy-blackhole'
    Write-Step "断网 L2：防火墙规则不可用（$($_.Exception.Message.Split([char]10)[0])），已设代理黑洞"
  }
} else {
  $netMode = 'skipped'
  Write-Step "断网：已按 -SkipNetworkBlock 跳过（仅调试用）"
}
if ($netMode -eq 'none') {
  # L3 兜底：连代理也设不上（极端情况）——告警但不阻断
  $env:HTTP_PROXY = 'http://127.0.0.1:9'
  $env:HTTPS_PROXY = 'http://127.0.0.1:9'
  $env:http_proxy = 'http://127.0.0.1:9'
  $env:https_proxy = 'http://127.0.0.1:9'
  $netMode = 'L3-warn-only'
  Write-Warning '断网处置未生效，仅记录告警（不阻断）——沙箱隔离强度下降'
}

# ---------------------------------------------------------------- 执行（硬超时 + 进程树清理）
#
# 实现纪律（2026-09-19 首轮自测事故固化）：**不得使用 ReadToEndAsync + Kill(bool) 组合**——
# 首版即因此卡死（整条命令 300s 超时、连超时分支的 Write-Step 都未打印），根因是
# 异步管道读取在进程被强杀后未收敛。改用「文件重定向（无管道）+ taskkill /T /F
# （比 Kill(bool) 更可靠的进程树终止）+ 二次收敛等待」。
Write-Step "试跑：$StrategyPath  窗口 $Start ~ $End  超时 ${TimeoutSec}s"
$outLog = Join-Path $work 'stdout.log'
$errLog = Join-Path $work 'stderr.log'
$pyArgs = @($RunnerPy, '--strategy', $StrategyPath, '--db', $ShadowDb,
            '--start', $Start, '--end', $End, '--out', $outJson)

$proc = Start-Process -FilePath $Python -ArgumentList $pyArgs `
  -WorkingDirectory $work -NoNewWindow -PassThru `
  -RedirectStandardOutput $outLog -RedirectStandardError $errLog

$timedOut = $false
if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
  $timedOut = $true
  Write-Step "超时 ${TimeoutSec}s —— 终止整棵进程树（taskkill /T /F）"
  & taskkill.exe /T /F /PID $proc.Id 2>&1 | Out-Null
  if (-not $proc.WaitForExit(10000)) {
    Write-Step '二次收敛未果，升级为直接 Kill()'
    try { $proc.Kill() } catch { }
    [void]$proc.WaitForExit(5000)
  }
}
try { $proc.WaitForExit(5000) } catch { }

$so = if (Test-Path -LiteralPath $outLog) { Get-Content -LiteralPath $outLog -Raw -Encoding UTF8 } else { '' }
$se = if (Test-Path -LiteralPath $errLog) { Get-Content -LiteralPath $errLog -Raw -Encoding UTF8 } else { '' }
($so + "`n--- stderr ---`n" + $se) | Set-Content -LiteralPath $logFile -Encoding UTF8

# ---------------------------------------------------------------- 清理断网规则
if ($netMode -eq 'L1-firewall') {
  try { Remove-NetFirewallRule -DisplayName 'QS-Contrib-Sandbox-Block' -ErrorAction Stop; Write-Step '断网 L1 规则已移除' }
  catch { Write-Warning "防火墙规则移除失败，请手工删除 QS-Contrib-Sandbox-Block：$($_.Exception.Message)" }
}

# ---------------------------------------------------------------- 结论
$exitCode = if ($timedOut) { 3 } else { $proc.ExitCode }
Write-Step "退出码=$exitCode  超时=$timedOut  断网模式=$netMode  日志=$logFile"
if (Test-Path -LiteralPath $outJson) {
  Write-Host (Get-Content -LiteralPath $outJson -Raw -Encoding UTF8)
} else {
  Write-Warning '未产出 result.json（可能导入期即失败），请查看日志'
}
exit $exitCode
