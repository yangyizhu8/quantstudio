# start_daemon_guarded.ps1 —— daemon 强化形态启动 wrapper（W-2 ② 补项）
#
# 方案依据：docs/design-daemon-w2-23-merged-20261026.md §1.1（2026-10-26 ②审四项全准）
# 三件套取证能力 + 取证件自愈（§1.1 四项）：
#   1. stderr 落盘       —— RedirectStandardError（Python 异常 traceback）
#   2. faulthandler      —— PYTHONFAULTHANDLER=1（native 崩溃栈）
#   3. ExitCode 双路     —— WaitForExit 正常路径 + 兜底 UNKNOWN(lost)（§1.1-3）
#   4. wrapper 自身心跳   —— 每 60s 一行 GUARD（§1.1-2）：wrapper 若静默亦成可检测事件
#   5. 重启后对账         —— 启动时读 exitcode.log：有 START 无 EXIT ⇒ 补记 UNKNOWN(lost)（§1.1-4）
#   ★ 独立启动（§1.1-1）：本脚本**必须由 Start-Process 脱离调用者 job 启动**，不得作为
#     调用方（shell/job）的子进程 —— 第五死实证：wrapper 与 daemon 同时消失致 ExitCode 件失效。
#
# ExitCode 释义：
#   0 = 正常退出；1..255 = Python 异常退出；大负数 = native
#   -1073741819=0xC0000005 access violation / -1073741510=0xC000013A Ctrl+C / -1073741515=0xC0000135 DLL 缺失
#
# 用法（脱离调用者，由启动者执行）：
#   Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','<本脚本绝对路径>' -WindowStyle Hidden
# 或手工前台运行（调试用）：powershell -File scripts\start_daemon_guarded.ps1

param([switch]$Force)   # -Force = 应急手动拉起：无视 backtest_stop.flag（2026-10-28）

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# ★ 2026-10-28 用户裁定（尊重手动恢复语义）：采集因回测被停（data/backtest_stop.flag
#   在）时，自动拉起通道（QS_DaemonForever 每日 06:05 等）必须跳过——恢复时点由用户
#   决定。手动启动（GUI 开启 / CLI 启动命令）经 daemon_lifecycle.run_forever 清 flag，
#   次日自动通道自然恢复供血。应急直启请加 -Force。
$RepoRoot0 = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$stopFlag0 = Join-Path $RepoRoot0 ([IO.Path]::Combine('data','backtest_stop.flag'))
if (-not $Force -and (Test-Path $stopFlag0)) {
$skipLog0 = Join-Path $RepoRoot0 ([IO.Path]::Combine('data','logs','daemon_autostart_skips.log'))
    $line0 = "{0} skip auto-start: backtest_stop.flag present (manual-restore semantics, ruled 2026-10-28; GUI start or -Force to override)" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    try { Add-Content -Path $skipLog0 -Value $line0 -Encoding UTF8 } catch { }
    exit 0
}
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $RepoRoot

$exe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
if (-not (Test-Path $exe)) { $exe = (Get-Command python).Source }

# ★ 2026-10-26 紧急修复（第六死教训）：`-RedirectStandardError` **每次启动会截断目标文件**，
#   第六死 320 行 stderr 证据已被第七次启动覆盖为 25 行 ⇒ **取证件自身丢失证据**。
#   修复：**按运行分文件**（`daemon_stderr_<stamp>.log`），并写指针文件 `daemon_stderr.current.txt`
#   指向最新一份；历史证据永不被覆盖。stdout 同处理。
$stamp     = (Get-Date).ToString('yyyyMMdd_HHmmss')
$stderrLog = Join-Path $RepoRoot ("data\logs\daemon_stderr_$stamp.log")
$stdoutLog = Join-Path $RepoRoot ("data\logs\daemon_stdout_$stamp.log")
$stderrPtr = Join-Path $RepoRoot 'data\logs\daemon_stderr.current.txt'
$exitLog   = Join-Path $RepoRoot 'data\logs\daemon_exitcode.log'
$guardLog  = Join-Path $RepoRoot 'data\logs\daemon_guard_heartbeat.log'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function AppendLog([string]$path, [string]$line) {
    [System.IO.File]::AppendAllText($path, $line + [Environment]::NewLine, $utf8NoBom)
}

# ══ §1.1-4 重启后对账：上一轮「有 START 无 EXIT」⇒ 补记 UNKNOWN(lost) ══════════
if (Test-Path $exitLog) {
    $lines = @(Get-Content $exitLog -Encoding UTF8 -ErrorAction SilentlyContinue | Where-Object { $_.Trim() })
    if ($lines.Count -gt 0) {
        $last = $lines[-1]
        if (($last -match 'START pid=(\d+)') -and ($last -notmatch 'EXIT')) {
            $lostPid = $Matches[1]
            # ── D6（2026-10-28 预写·今晚窗验证启用）：对账前先验存活 ──
            # 缺陷（10-28 06:05 实测）：Daily 触发的新 wrapper 启动时对账，读到上一行
            # 「03:19:21 START pid=43336 且无 EXIT」，把**仍在运行的 43336** 误标 UNKNOWN(lost)。
            # 修法：pid 仍存活 ⇒ 说明只是它的 wrapper 尚未写 EXIT，**跳过补记**。
            $stillAlive = $null
            try { $stillAlive = Get-Process -Id ([int]$lostPid) -ErrorAction SilentlyContinue } catch { }
            if ($stillAlive) {
                AppendLog $guardLog ("{0} RECONCILE skip-pid={1}（该 pid 仍存活，跳过 lost 补记）" -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $lostPid)
                Write-Output "§1.1-4 对账：pid=$lostPid 仍存活，跳过 lost 补记（D6 存活校验）"
            } else {
            AppendLog $exitLog ("{0} EXIT  pid={1} ExitCode=UNKNOWN(lost) -- wrapper-also-terminated（§1.1-4 对账补记：有 START 无 EXIT，wrapper 与 daemon 同时消失）" -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $lostPid)
            AppendLog $guardLog ("{0} RECONCILE lost-pid={1}（上轮有 START 无 EXIT，已补记 UNKNOWN）" -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $lostPid)
            Write-Warning "§1.1-4 对账：检测到上轮 pid=$lostPid 有 START 无 EXIT，已补记 UNKNOWN(lost)"
            }
        }
    }
}

$env:PYTHONFAULTHANDLER = '1'

$startedAt = Get-Date
$p = Start-Process -FilePath $exe `
    -ArgumentList @('-m','quantstudio.pipeline.daemon','--mode','forever','--config-dir','config\profiles\mcp_only') `
    -WorkingDirectory $RepoRoot -WindowStyle Hidden `
    -RedirectStandardError $stderrLog -RedirectStandardOutput $stdoutLog -PassThru

$wrapperPid = $PID
# 指针文件：指向本次运行的 stderr（巡检/人工据此定位最新证据，不必猜 stamp）
try { [System.IO.File]::WriteAllText($stderrPtr, $stderrLog + [Environment]::NewLine, $utf8NoBom) } catch { }
AppendLog $exitLog ("{0} START pid={1} wrapper_pid={2} faulthandler=1 stderr={3}" -f $startedAt.ToString('yyyy-MM-dd HH:mm:ss'), $p.Id, $wrapperPid, $stderrLog)
AppendLog $guardLog ("{0} GUARD start wrapper_pid={1} daemon_pid={2} alive=True" -f $startedAt.ToString('yyyy-MM-dd HH:mm:ss'), $wrapperPid, $p.Id)
Write-Output "START pid=$($p.Id) wrapper_pid=$wrapperPid stderr=$stderrLog"

# ══ §1.1-2 wrapper 自身心跳（每 60s）+ 守候 daemon ══════════════════════════════
$lastBeat = Get-Date
while (-not $p.HasExited) {
    Start-Sleep -Seconds 10
    if (((Get-Date) - $lastBeat).TotalSeconds -ge 60) {
        $alive = -not $p.HasExited
        AppendLog $guardLog ("{0} GUARD wrapper_pid={1} daemon_pid={2} alive={3} uptime={4:n0}min" -f `
            (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $wrapperPid, $p.Id, $alive, ((Get-Date) - $startedAt).TotalMinutes)
        $lastBeat = Get-Date
    }
}

# ══ §1.1-3 双路 ExitCode：正常路径 ═════════════════════════════════════════════
# ── D5（2026-10-28 预写·今晚窗验证启用）：ExitCode 读取加固 ──
# 缺陷（第八死 10-28 01:53 实测）：`$p.ExitCode` 稳定失败 ⇒ 只能记 UNKNOWN(cannot-read)，
# **无异常类型 ⇒ 诊断价值为零**（无法区分"句柄失效/权限/其他"）。
# 修法：①Refresh() 后再读（.NET 进程句柄需刷新）②最多重试 3 次（100ms 间隔）
#       ③失败时**记录异常类型名**（如 InvalidOperationException）写进 ExitCode 字段。
$ec = $null
$ecErr = ''
for ($attempt = 1; $attempt -le 3; $attempt++) {
    if ($attempt -gt 1) { Start-Sleep -Milliseconds 100 }
    try {
        $p.Refresh()
        $ec = $p.ExitCode
        break
    } catch {
        $ecErr = $_.Exception.GetType().Name
        $ec = $null
    }
}
$hex = if (($null -ne $ec) -and ($ec -lt 0)) { '0x{0:X8}' -f ($ec -band 0xFFFFFFFF) } else { '' }
$interp = if ($null -eq $ec) { "UNKNOWN(cannot-read:$ecErr)" } else {
    switch ($ec) {
        0            { 'normal-exit' }
        # ── D7（2026-10-28 预写·今晚窗验证启用）：附写 daemon.log 尾部的停止 reason ──
        # 动机（10-28 复核）：exitcode/guard 通道不携带 reason ⇒ 观测面把 backtest 优雅停止
        # 误判为"死亡"（我据此误报第八/九死）。EXIT 时顺带从 daemon.log 尾部取最后一条
        # 「收到停止请求（reason=xxx）」，让 EXIT 行自带操作性/异常性判据。
        -1073741819  { 'NATIVE access-violation (0xC0000005)' }
        -1073741510  { 'Ctrl+C / console-close (0xC000013A)' }
        -1073741515  { 'DLL-not-found (0xC0000135)' }
        default      { if ($ec -gt 0 -and $ec -lt 256) { "python-exit-code $ec" } else { 'external-terminate or native' } }
    }
}
$reasonTag = ''
try {
    $dlog = Join-Path $RepoRoot 'data\logs\daemon.log'
    if (Test-Path $dlog) {
        $hit = Get-Content $dlog -Encoding UTF8 -Tail 400 -ErrorAction SilentlyContinue |
               Where-Object { $_ -match '收到停止请求' } | Select-Object -Last 1
        if ($hit) {
            if ($hit -match 'reason=([\w\-]+)') { $reasonTag = " reason=$($Matches[1])" }
            else { $reasonTag = ' reason=(unspecified:旧版无reason参数)' }
        } else { $reasonTag = ' reason=(none:无停止请求行⇒疑似真异常)' }
    }
} catch { $reasonTag = ' reason=(probe-error)' }
$endedAt = Get-Date
AppendLog $exitLog ("{0} EXIT  pid={1} ExitCode={2} {3} ({4}){5}  uptime={6:n1}min" -f `
    $endedAt.ToString('yyyy-MM-dd HH:mm:ss'), $p.Id, $ec, $hex, $interp, $reasonTag, (($endedAt - $startedAt).TotalMinutes))
AppendLog $guardLog ("{0} GUARD exit wrapper_pid={1} daemon_pid={2} ExitCode={3} ({4})" -f `
    $endedAt.ToString('yyyy-MM-dd HH:mm:ss'), $wrapperPid, $p.Id, $ec, $interp)
Write-Output "EXIT pid=$($p.Id) ExitCode=$ec ($interp)"
