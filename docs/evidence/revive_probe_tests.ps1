# F11 旁证四探逐探单测（探②③④；探① 已由主测试组⑤覆盖）
# 每组只让一个探命中，验证四探各自独立有效
$ErrorActionPreference = 'Stop'
$script = (Resolve-Path "scripts\check_daemon_alive.ps1").Path
$rootT  = Join-Path $env:TEMP "revive_probe_test"
if (Test-Path $rootT) { Remove-Item $rootT -Recurse -Force }
$now = Get-Date

function New-Case([string]$name) {
    $d = Join-Path $rootT $name
    New-Item -ItemType Directory -Path (Join-Path $d 'data\logs') -Force | Out-Null
    $st = @{ pid = 999999; started_at = $now.AddHours(-1).ToString('yyyy-MM-dd HH:mm:ss') } | ConvertTo-Json
    [System.IO.File]::WriteAllText((Join-Path $d 'data\daemon_status.json'), $st, (New-Object System.Text.UTF8Encoding($false)))
    $state = [ordered]@{ consecutive = 2; last_check_at = $null; last_revive_at = $null; last_action = 'NONE'; observed_since = $now.AddHours(-2).ToString('yyyy-MM-dd HH:mm:ss') }
    [System.IO.File]::WriteAllText((Join-Path $d 'data\daemon_revival_state.json'), ($state | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
    [System.IO.File]::WriteAllText((Join-Path $d 'data\logs\daemon_exitcode.log'), '2026-11-10 03:00:00 EXIT  pid=333 ExitCode=1  (python-exit-code 1)  uptime=10min', (New-Object System.Text.UTF8Encoding($false)))
    return $d
}
function Run-Case($d) {
    $a = @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$script,
           '-Quiet','-RepoRoot',$d,'-SimulateVerdict','FAIL','-ReviveMode','observe')
    $null = & powershell @a 2>&1
    return $LASTEXITCODE
}
function Read-Gate($d) {
    $lg = Join-Path $d 'data\logs\daemon_revival.log'
    if (-not (Test-Path $lg)) { return @{ gate = '(no-log)'; action = '(none)'; detail = '' } }
    $last = @(Get-Content $lg -Encoding UTF8)[-1]
    $g = if ($last -match 'gate=([^\s]+)') { $Matches[1] } else { '?' }
    $act = if ($last -match 'action=([^\s]+)') { $Matches[1] } else { '?' }
    $det = if ($last -match '\| (.+)$') { $Matches[1] } else { '' }
    return @{ gate = $g; action = $act; detail = $det }
}

$out = @()

# ── 探② 主库被独占：本会话持句柄，使子进程无法 ReadWrite/None 打开 ──
$d = New-Case 'p2_db_locked'
$dbFile = Join-Path $d 'data\quantstudio.db'
[System.IO.File]::WriteAllBytes($dbFile, [byte[]]::new(8192))
$hold = [System.IO.File]::Open($dbFile, 'Open', 'ReadWrite', 'None')
try {
    $null = Run-Case $d
} finally {
    $hold.Close(); $hold.Dispose()
}
$r = Read-Gate $d
$out += [pscustomobject]@{ 探='探②主库被独占'; 期望='主库被独占'; 实得=$r.detail; gate=$r.gate; hit=($r.detail -like '*主库被独占*') }

# ── 探③ 自停痕迹：daemon.log 新鲜且含停止请求 ──
$d = New-Case 'p3_stop_trace'
$logTxt = @(
  '10:00:00 INFO quantstudio.pipeline.writers: [DuckDBWriter] tables initialized',
  '10:00:01 INFO quantstudio.pipeline.daemon_lifecycle: [DaemonLifecycle] 收到停止请求（reason=test），准备优雅退出'
) -join "`r`n"
[System.IO.File]::WriteAllText((Join-Path $d 'data\logs\daemon.log'), $logTxt, (New-Object System.Text.UTF8Encoding($false)))
$null = Run-Case $d
$r = Read-Gate $d
$out += [pscustomobject]@{ 探='探③自停痕迹'; 期望='daemon.log 近30min有自停痕迹'; 实得=$r.detail; gate=$r.gate; hit=($r.detail -like '*自停痕迹*') }

# ── 探④ 锁文件活跃：data\.daemon.lock 新鲜 ──
$d = New-Case 'p4_lock_file'
$lockFile = Join-Path $d 'data\.daemon.lock'
[System.IO.File]::WriteAllText($lockFile, 'pid=12345', (New-Object System.Text.UTF8Encoding($false)))
$null = Run-Case $d
$r = Read-Gate $d
$out += [pscustomobject]@{ 探='探④锁文件活跃'; 期望='.daemon.lock 近30min活跃'; 实得=$r.detail; gate=$r.gate; hit=($r.detail -like '*daemon.lock*') }

Write-Output "=== F11 四探逐探单测（探②③④；探① 见主测试组⑤）==="
$allHit = $true
foreach ($x in $out) {
    $ok = $x.hit -and ($x.gate -eq 'skip-suspected_activity')
    if (-not $ok) { $allHit = $false }
    Write-Output ("  {0,-16} gate={1,-24} 命中={2,-6} 期望={3}" -f $x.探, $x.gate, $x.hit, $x.期望)
    Write-Output ("      detail: {0}" -f $x.实得)
}
Write-Output ""
if ($allHit) { Write-Output "  ===== 探②③④ 各自独立命中: 3/3 PASS（探① 见组⑤）→ 四探全覆盖 =====" } else { Write-Output "  ===== 存在未命中项，需修 =====" }
