# 5 组负向对照 + 组6 observe 演练（F11/F1/F2/F3/F4 逐闸验证）
# 全部在 %TEMP%\revive_test\<case> 隔离 RepoRoot 内，不碰生产 data/
$ErrorActionPreference = 'Stop'
$script = (Resolve-Path "scripts\check_daemon_alive.ps1").Path
$rootT  = Join-Path $env:TEMP "revive_test"
if (Test-Path $rootT) { Remove-Item $rootT -Recurse -Force }
$now = Get-Date

function New-Case([string]$name) {
    $d = Join-Path $rootT $name
    New-Item -ItemType Directory -Path (Join-Path $d 'data\logs') -Force | Out-Null
    # status.json：pid 指向不存在的进程（配合 -SimulateVerdict FAIL 使用）
    $st = @{ pid = 999999; started_at = $now.AddHours(-1).ToString('yyyy-MM-dd HH:mm:ss'); create_time = [int][double]([DateTimeOffset]$now.AddHours(-1)).ToUnixTimeSeconds() } | ConvertTo-Json
    [System.IO.File]::WriteAllText((Join-Path $d 'data\daemon_status.json'), $st, (New-Object System.Text.UTF8Encoding($false)))
    return $d
}
function Set-State($d, [int]$consecutive, [string]$lastRevive) {
    $o = [ordered]@{ consecutive = $consecutive; last_check_at = $null; last_revive_at = $lastRevive; last_action = 'NONE'; observed_since = $now.AddHours(-2).ToString('yyyy-MM-dd HH:mm:ss') }
    [System.IO.File]::WriteAllText((Join-Path $d 'data\daemon_revival_state.json'), ($o | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
}
function Run-Case($d, [string]$mode = 'observe') {
    $a = @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$script,
           '-Quiet','-RepoRoot',$d,'-SimulateVerdict','FAIL','-ReviveMode',$mode)
    $null = & powershell @a 2>&1
    return $LASTEXITCODE
}
function Read-Gate($d) {
    $lg = Join-Path $d 'data\logs\daemon_revival.log'
    if (-not (Test-Path $lg)) { return @{ gate = '(no-log)'; action = '(none)'; line = '' } }
    $last = @(Get-Content $lg -Encoding UTF8)[-1]
    $g = if ($last -match 'gate=([^\s]+)') { $Matches[1] } else { '?' }
    $act = if ($last -match 'action=([^\s]+)') { $Matches[1] } else { '?' }
    return @{ gate = $g; action = $act; line = $last }
}

$results = @()

# ── 组① 正常退出（exitcode.log 末条 normal-exit）⇒ 应 skip-normal-exit ──
$d = New-Case 'case1_normal_exit'
Set-State $d 5 $null   # 预置高计数，验证 normal-exit 会归零且不拉
$exitTxt = @(
  '2026-11-10 01:00:00 EXIT  pid=111 ExitCode=  (UNKNOWN(cannot-read:)) reason=backtest  uptime=100.0min',
  '2026-11-10 02:00:00 EXIT  pid=222 ExitCode=0  (normal-exit) reason=user  uptime=50.0min'
) -join "`r`n"
[System.IO.File]::WriteAllText((Join-Path $d 'data\logs\daemon_exitcode.log'), $exitTxt, (New-Object System.Text.UTF8Encoding($false)))
$rc = Run-Case $d
$r = Read-Gate $d; $results += [pscustomobject]@{ 组='①正常退出'; 期望='skip-normal-exit'; 实得=$r.gate; action=$r.action; rc=$rc }

# ── 组② 计数不足（预置 0 ⇒ +1 = 1 < 3）⇒ 应 skip-threshold ──
$d = New-Case 'case2_below_threshold'
Set-State $d 0 $null
[System.IO.File]::WriteAllText((Join-Path $d 'data\logs\daemon_exitcode.log'), '2026-11-10 03:00:00 EXIT  pid=333 ExitCode=1  (python-exit-code 1)  uptime=10min', (New-Object System.Text.UTF8Encoding($false)))
$rc = Run-Case $d
$r = Read-Gate $d; $results += [pscustomobject]@{ 组='②计数不足'; 期望='skip-threshold(1/3)'; 实得=$r.gate; action=$r.action; rc=$rc }

# ── 组③ 占用窗（预置 2 ⇒ 达 3；occupancy 覆盖 now）⇒ 应 skip-occupancy ──
$d = New-Case 'case3_occupancy'
Set-State $d 2 $null
[System.IO.File]::WriteAllText((Join-Path $d 'data\logs\daemon_exitcode.log'), '2026-11-10 03:00:00 EXIT  pid=333 ExitCode=1  (python-exit-code 1)  uptime=10min', (New-Object System.Text.UTF8Encoding($false)))
$occ = [ordered]@{
  occupied = $true
  window = @($now.AddHours(-1).ToString('o'), $now.AddHours(2).ToString('o'))
  operation = '回填窗（测试构造）'; owner = '测试线'; purpose = '负向对照③'
} | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $d 'data\daemon_occupancy.json'), $occ, (New-Object System.Text.UTF8Encoding($false)))
$rc = Run-Case $d
$r = Read-Gate $d; $results += [pscustomobject]@{ 组='③占用窗'; 期望='skip-occupancy'; 实得=$r.gate; action=$r.action; rc=$rc }

# ── 组④ 冷却期（预置 2；last_revive_at = 5 分钟前；无 occupancy）⇒ 应 skip-cooldown ──
$d = New-Case 'case4_cooldown'
Set-State $d 2 ($now.AddMinutes(-5).ToString('yyyy-MM-dd HH:mm:ss'))
[System.IO.File]::WriteAllText((Join-Path $d 'data\logs\daemon_exitcode.log'), '2026-11-10 03:00:00 EXIT  pid=333 ExitCode=1  (python-exit-code 1)  uptime=10min', (New-Object System.Text.UTF8Encoding($false)))
$rc = Run-Case $d
$r = Read-Gate $d; $results += [pscustomobject]@{ 组='④冷却期'; 期望='skip-cooldown'; 实得=$r.gate; action=$r.action; rc=$rc }

# ── 组⑤ 自停未声明（预置 2；无 occupancy；无冷却；造活跃 WAL ⇒ 探①命中）⇒ 应 skip-suspected_activity ──
$d = New-Case 'case5_silent_selfstop'
Set-State $d 2 $null
[System.IO.File]::WriteAllText((Join-Path $d 'data\logs\daemon_exitcode.log'), '2026-11-10 03:00:00 EXIT  pid=333 ExitCode=1  (python-exit-code 1)  uptime=10min', (New-Object System.Text.UTF8Encoding($false)))
$wal = Join-Path $d 'data\quantstudio.db.wal'
[System.IO.File]::WriteAllBytes($wal, [byte[]]::new(4096))   # mtime = now ⇒ 探① 命中
$rc = Run-Case $d
$r = Read-Gate $d; $results += [pscustomobject]@{ 组='⑤自停未声明(探①)'; 期望='skip-suspected_activity'; 实得=$r.gate; action=$r.action; rc=$rc }

# ── 组⑥ 全闸通过 + observe ⇒ 应 REVIVE_ARMED（不实际拉起）──
$d = New-Case 'case6_all_pass_observe'
Set-State $d 2 $null
[System.IO.File]::WriteAllText((Join-Path $d 'data\logs\daemon_exitcode.log'), '2026-11-10 03:00:00 EXIT  pid=333 ExitCode=1  (python-exit-code 1)  uptime=10min', (New-Object System.Text.UTF8Encoding($false)))
$rc = Run-Case $d 'observe'
$r = Read-Gate $d; $results += [pscustomobject]@{ 组='⑥全过+observe'; 期望='all-pass/REVIVE_ARMED'; 实得="$($r.gate)/$($r.action)"; action=$r.action; rc=$rc }

# ── 汇总 ──
Write-Output "=== 6 组对照结果 ==="
$allPass = $true
foreach ($x in $results) {
    $ok = $false
    if ($x.组 -like '⑥*') { $ok = ($x.实得 -eq 'all-pass/REVIVE_ARMED') }
    elseif ($x.组 -like '②*') { $ok = ($x.实得 -like 'skip-threshold*') }
    else { $ok = ($x.实得 -like ($x.期望 + '*')) }
    if (-not $ok) { $allPass = $false }
    Write-Output ("  {0,-22} 期望={1,-26} 实得={2,-34} action={3,-14} {4}" -f $x.组, $x.期望, $x.实得, $x.action, $(if($ok){'PASS'}else{'★FAIL'}))
}
Write-Output ""
if ($allPass) { Write-Output "  ===== 负向对照判定: 6/6 PASS =====" } else { Write-Output "  ===== 负向对照判定: 存在 FAIL，需修 =====" }
Write-Output ""
Write-Output "=== 拉起调用计数（关键：负向组必须为 0）==="
$reviveLogs = @(Get-ChildItem $rootT -Recurse -Filter 'daemon_revival.log' -ErrorAction SilentlyContinue)
Write-Output "  产生 revival 日志的组数 = $($reviveLogs.Count) / 6"
$reviveLogs | ForEach-Object { Write-Output ("    " + $_.Directory.Parent.Name + ": " + (@(Get-Content $_.FullName -Encoding UTF8)[-1])) }
