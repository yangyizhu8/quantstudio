# register_daemon_heartbeat_task.ps1 —— 注册 daemon 外部心跳巡检计划任务（W-2 族 ①）
#
# 方案：docs/design-daemon-heartbeat-watchdog-20261026.md（2026-10-26 ②审通过）
# 任务名：QS_DaemonHeartbeat      间隔：10 分钟（用户裁定 (a)）
# 动作：调用 scripts/check_daemon_alive.ps1 -Quiet（健康态静默，仅非 OK 落盘告警）
#
# 纪律：
#   · 只**注册/更新计划任务**，不启停 daemon 本体（19 号铁律：只观察不干预）
#   · 任务动作是只读巡检脚本（绝不 create_writer / init_schema）
#   · 幂等：已存在即覆盖（-Force 语义），不重复创建
#
# ★ 实现要点（2026-10-26 首测教训）：**必须用 Task XML 注册，不能用 New-ScheduledTaskTrigger 的
#   -RepetitionDuration ([TimeSpan]::MaxValue)** —— 后者生成 `P99999999DT23H59M59S`，
#   超出 Task Scheduler XML 允许域，报 0x80041318「value incorrectly formatted or out of range」。
#   正解：`<Repetition>` 内**只给 `<Interval>`、不给 `<Duration>`** ⇒ 语义即「无限期重复」。
#
# ★ 文件编码必须 UTF-8 **带 BOM**（PS 5.1 按 ANSI/GBK 读无 BOM 的 .ps1，中文字符串会乱码并
#   引发解析器报错 —— W-10 编码坑族在 .ps1 上的复现，2026-10-26 首测实证）。
#
# 用法：
#   powershell -File scripts/register_daemon_heartbeat_task.ps1              # 注册/更新
#   powershell -File scripts/register_daemon_heartbeat_task.ps1 -DryRun      # 只打印将注册的 XML
#   powershell -File scripts/register_daemon_heartbeat_task.ps1 -Unregister  # 卸载（完全回退）

[CmdletBinding()]
param(
    [string]$RepoRoot,
    [string]$TaskName = 'QS_DaemonHeartbeat',
    [int]$IntervalMinutes = 10,
    [string]$Webhook,
    [switch]$Unregister,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path }
$probe = Join-Path $RepoRoot 'scripts\check_daemon_alive.ps1'

if (-not (Test-Path $probe)) {
    Write-Error "缺巡检脚本: $probe"
    exit 1
}

# ── 卸载 ──────────────────────────────────────────────────────────────────────
if ($Unregister) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) { Write-Output "[register] 任务 $TaskName 不存在，无需卸载"; exit 0 }
    if ($DryRun) { Write-Output "[dry-run] 将执行: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"; exit 0 }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "[register] 已卸载任务 $TaskName（完全回退：巡检脚本本身可保留为手工工具）"
    exit 0
}

# ── 解释器选择：优先 pwsh（PS7），回退 powershell.exe（PS5.1）────────────────
$exe = $null
$pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
if ($pwshCmd) { $exe = $pwshCmd.Source }
else {
    $ps51 = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (Test-Path $ps51) { $exe = $ps51 }
}
if (-not $exe) { Write-Error "未找到 pwsh / powershell.exe，无法注册"; exit 1 }

# ── 参数行 ────────────────────────────────────────────────────────────────────
$quotedProbe = '"' + $probe + '"'
$argList = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $quotedProbe,
             '-Quiet', '-IntervalMinutes', "$IntervalMinutes")
if ($Webhook) { $argList += @('-Webhook', ('"' + $Webhook + '"')) }
$argStr = $argList -join ' '

function Esc([string]$s) {
    return $s.Replace('&', '&amp;').Replace('<', '&lt;').Replace('>', '&gt;').Replace('"', '&quot;')
}

# ── Task XML（★ 关键：Repetition 只给 Interval，不给 Duration ⇒ 无限期）──────
$startBoundary = (Get-Date).AddMinutes(1).ToString('yyyy-MM-ddTHH:mm:ss')
$userId = "$env:USERDOMAIN\$env:USERNAME"
$desc = "QuantStudio daemon 外部心跳巡检（W-2 ①）：每 ${IntervalMinutes} 分钟只读探测 进程存活 / status 一致 / 心跳新鲜；FAIL 时按 System 事件日志 id=1074/6006/41 区分『宿主重启所致』与『daemon 自身异常退出』。只告警不拉起（用户 2026-10-26 裁定）。"

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$(Esc $desc)</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>PT${IntervalMinutes}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>$startBoundary</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$(Esc $userId)</UserId>
      <LogonType>S4U</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$(Esc $exe)</Command>
      <Arguments>$(Esc $argStr)</Arguments>
      <WorkingDirectory>$(Esc $RepoRoot)</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

if ($DryRun) {
    Write-Output "[dry-run] TaskName : $TaskName"
    Write-Output "[dry-run] Interval : ${IntervalMinutes} min（Repetition 无 Duration ⇒ 无限期）"
    Write-Output "[dry-run] Principal: $userId (S4U, LeastPrivilege)"
    Write-Output "[dry-run] ---- Task XML ----"
    $xml -split "`n" | ForEach-Object { Write-Output ("  " + $_.TrimEnd()) }
    Write-Output "[dry-run] 未做任何变更。"
    exit 0
}

# ── 注册/更新（-Force 覆盖同名）──────────────────────────────────────────────
Register-ScheduledTask -TaskName $TaskName -Xml $xml -Force -ErrorAction Stop | Out-Null

$t = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Output "[register] 已注册/更新 任务 $TaskName"
Write-Output "  状态        : $($t.State)"
Write-Output "  间隔        : ${IntervalMinutes} 分钟（无限期重复）"
Write-Output "  下次运行    : $($info.NextRunTime)"
Write-Output "  解释器      : $exe"
Write-Output "  巡检脚本    : $probe"
Write-Output "  参数        : $argStr"
Write-Output ""
Write-Output "手工验证："
Write-Output "  Start-ScheduledTask -TaskName $TaskName"
Write-Output "  Get-ScheduledTaskInfo -TaskName $TaskName    # LastTaskResult：0=OK / 2=WARN / 3=FAIL"
Write-Output "  Get-Content data\logs\daemon_heartbeat_alerts.log -Tail 5"
Write-Output "完全回退："
Write-Output "  powershell -File scripts\register_daemon_heartbeat_task.ps1 -Unregister"
