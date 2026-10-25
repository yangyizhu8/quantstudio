# check_daemon_alive.ps1 —— daemon 外部心跳巡检（W-2 族 ① · 纯增量）
#
# ══════════════════════════════════════════════════════════════════════════════
# 方案：docs/design-daemon-heartbeat-watchdog-20261026.md（2026-10-26 ②审通过 · 用户三裁点照准）
# 派单：W-2 族 ①「外部心跳巡检」—— 纯增量，**不改 daemon 任何代码**
# 定性：18 号铁律「新增检测型」（开始报告真相，非行为漂移）
# ══════════════════════════════════════════════════════════════════════════════
#
# 【硬约束 · 只读】
#   本脚本**绝不** create_writer / init_schema / 写任何 DuckDB 或 QuestDB。
#   原因：daemon 持有 DuckDB 单写锁，巡检若碰 writer 会抢锁（历史事故类型）。
#   全部动作 = 读进程表 / 读 JSON 文件 / 读文件时间戳 / 读 Windows 事件日志。
#
# 【三条判据 · 必须一起看】（任一单独看都会误判）
#   H1 进程存活   ：按命令行匹配 quantstudio[/\]pipeline[/\]daemon\.py 或 -m quantstudio.pipeline.daemon
#                   ★ 不能用 "pipeline.daemon" 子串 —— 实际 cmdline 是完整路径形态
#                     (data/daemon_status.json: "D:\\...\\quantstudio\\pipeline\\daemon.py")，
#                     历史上两次误判均因匹配式过窄。
#   H2 状态一致   ：daemon_status.json 的 pid 必须「活着 且 命令行匹配」。
#                   ★ 宿主重启后 status 残留旧 pid —— 只看文件存在 / 只看 mtime 会假阳性判活。
#   H3 心跳新鲜   ：status create_time 龄 与 日志 mtime 龄 双龄，> StaleFactor × 轮询间隔 判疑似假死。
#
# 【判级与退出码】
#   0 = OK（静默，不产生推送噪声）
#   2 = WARN  疑似假死（进程在但心跳超龄）
#   3 = FAIL  静默死亡（进程死 / status 与进程不一致）
#   4 = 巡检自身异常（探测失败 —— 不得静默报 OK）
#
# 【宿主重启归因分离】（本方案与普通 watchdog 的核心差别，④ 排查的直接产物）
#   判 FAIL 时**同查该时段 System 日志有无 id=1074/6006/41**：
#     有 ⇒ 标 [宿主重启所致]，不触发任何「重启 daemon」动作（重启了也会被下次关机杀掉）
#     无 ⇒ 标 [daemon 自身异常退出 · 需 ②stderr 取证]，这才是需人介入的信号
#   ⇒ 告警自带归因提示，避免收告警的人重做排查。
#
# 【自愈范围】默认**只告警不拉起**（用户 2026-10-26 裁定）。
#   理由：①宿主重启场景拉起无意义 ②自动启停 daemon 是 19 号铁律禁区
#         ③须先解决 status 陈旧互斥（③项）
#
# 用法：
#   pwsh -File scripts/check_daemon_alive.ps1                      # 单次巡检
#   pwsh -File scripts/check_daemon_alive.ps1 -AsJson              # 机器可读
#   pwsh -File scripts/check_daemon_alive.ps1 -StaleFactor 3 -Quiet
# 自测（合成输入，不依赖真实 daemon）：
#   pwsh -File scripts/check_daemon_alive.ps1 -SimulateStatusPid 999999
# ══════════════════════════════════════════════════════════════════════════════

[CmdletBinding()]
param(
    [string]$RepoRoot,
    [int]$IntervalMinutes = 10,
    [int]$StaleFactor = 3,
    [string]$StatusFile,
    [string]$DaemonLog,
    [string]$AlertLog,
    [string]$Webhook,
    [int]$SimulateStatusPid = 0,
    [switch]$AsJson,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# ── 路径解析 ──────────────────────────────────────────────────────────────────
if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}
if (-not $StatusFile) { $StatusFile = Join-Path $RepoRoot 'data\daemon_status.json' }
if (-not $DaemonLog)  { $DaemonLog  = Join-Path $RepoRoot 'data\logs\daemon.log' }
if (-not $AlertLog)   { $AlertLog   = Join-Path $RepoRoot 'data\logs\daemon_heartbeat_alerts.log' }
if (-not $Webhook)    { $Webhook    = $env:QS_HEARTBEAT_WEBHOOK }

$now = Get-Date
$result = [ordered]@{
    checked_at   = $now.ToString('yyyy-MM-dd HH:mm:ss')
    verdict      = 'OK'
    reason       = ''
    attribution  = ''
    h1_alive     = $false
    h1_pids      = @()
    h2_status_pid = $null
    h2_consistent = $null
    h3_status_age_s = $null
    h3_log_age_s    = $null
    h3_stale_threshold_s = $StaleFactor * $IntervalMinutes * 60
    daemon_started_at = $null
    note         = @()
}

# ── 匹配式（H1 关键）──────────────────────────────────────────────────────────
# 同时覆盖两种启动形态：
#   ① 路径形态：python ...\quantstudio\pipeline\daemon.py --mode forever ...
#   ② 模块形态：python -m quantstudio.pipeline.daemon --mode forever ...
$reDaemonPath = 'quantstudio[\\/]+pipeline[\\/]+daemon\.py'
$reDaemonMod  = 'quantstudio\.pipeline\.daemon'

function Get-DaemonProcs {
    $procs = @()
    try {
        $cim = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop
    } catch {
        $result['note'] += "CIM 查询进程表失败: $($_.Exception.Message)"
        return @()
    }
    foreach ($p in $cim) {
        $cl = $p.CommandLine
        if (-not $cl) { continue }
        if ($cl -match $reDaemonPath -or $cl -match $reDaemonMod) {
            $procs += [pscustomobject]@{
                pid     = [int]$p.ProcessId
                cmdline = $cl
            }
        }
    }
    return $procs
}

# ── H1：进程存活 ──────────────────────────────────────────────────────────────
$daemonProcs = Get-DaemonProcs
$result['h1_pids']  = @($daemonProcs | ForEach-Object { $_.pid })
$result['h1_alive'] = ($daemonProcs.Count -gt 0)

# ── H2：status.json 与进程一致性 ──────────────────────────────────────────────
$statusPid = $null
if ($SimulateStatusPid -gt 0) {
    $statusPid = $SimulateStatusPid
    $result['note'] += "合成输入：status pid 强制为 $SimulateStatusPid"
} elseif (Test-Path $StatusFile) {
    try {
        $st = Get-Content -LiteralPath $StatusFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $statusPid = [int]$st.pid
        $result['daemon_started_at'] = $st.started_at
        # H3 输入之一：status create_time（epoch **秒**，实测 1792932026.44 ≈ 2026-10-25T20:40:27）
        if ($st.create_time) {
            try {
                $ct = [System.DateTimeOffset]::FromUnixTimeSeconds([int64][double]$st.create_time).LocalDateTime
                $result['h3_status_age_s'] = [int]($now - $ct).TotalSeconds
            } catch {
                $result['note'] += "status create_time 解析失败: $($_.Exception.Message)"
            }
        }
    } catch {
        $result['note'] += "status.json 解析失败: $($_.Exception.Message)"
    }
} else {
    $result['note'] += "status.json 不存在（$StatusFile）"
}
$result['h2_status_pid'] = $statusPid

if ($null -ne $statusPid) {
    $result['h2_consistent'] = ($result['h1_pids'] -contains $statusPid)
}

# ── H3：日志心跳新鲜度 ────────────────────────────────────────────────────────
if (Test-Path $DaemonLog) {
    try {
        $logMtime = (Get-Item -LiteralPath $DaemonLog).LastWriteTime
        $result['h3_log_age_s'] = [int]($now - $logMtime).TotalSeconds
    } catch {
        $result['note'] += "日志 mtime 读取失败: $($_.Exception.Message)"
    }
} else {
    $result['note'] += "daemon.log 不存在（$DaemonLog）"
}

# ── 判级 ──────────────────────────────────────────────────────────────────────
$threshold = $result['h3_stale_threshold_s']

if (-not $result['h1_alive']) {
    $result['verdict'] = 'FAIL'
    $result['reason']  = 'H1: 未发现 daemon 进程（静默死亡）'
}
elseif ($null -ne $statusPid -and -not $result['h2_consistent']) {
    $result['verdict'] = 'FAIL'
    $result['reason']  = "H2: status pid=$statusPid 不在存活进程集合 [$($result['h1_pids'] -join ',')] 中（status 陈旧）"
}
else {
    $ages = @()
    if ($null -ne $result['h3_status_age_s']) { $ages += [int]$result['h3_status_age_s'] }
    if ($null -ne $result['h3_log_age_s'])    { $ages += [int]$result['h3_log_age_s'] }
    if ($ages.Count -gt 0) {
        $maxAge = ($ages | Measure-Object -Maximum).Maximum
        if ($maxAge -gt $threshold) {
            $result['verdict'] = 'WARN'
            $result['reason']  = "H3: 心跳超龄 ${maxAge}s > 阈值 ${threshold}s（疑似假死/挂住，进程仍在）"
        } else {
            $result['verdict'] = 'OK'
            $result['reason']  = "三判据全通过（进程 $($result['h1_pids'] -join ',')，心跳 ${maxAge}s ≤ ${threshold}s）"
        }
    } else {
        # 进程在但拿不到任何心跳输入 ⇒ 不得静默报 OK
        $result['verdict'] = 'WARN'
        $result['reason']  = 'H3: 无法取得任何心跳输入（status create_time 与日志 mtime 均不可用）'
    }
}

# ── 宿主重启归因（仅 FAIL 时查询，减少事件日志开销）────────────────────────────
if ($result['verdict'] -eq 'FAIL') {
    $lookbackMin = [Math]::Max($IntervalMinutes * 2, 30)
    $since = $now.AddMinutes(-1 * $lookbackMin)
    try {
        $evts = Get-WinEvent -FilterHashtable @{
            LogName = 'System'; Id = 1074, 6006, 41; StartTime = $since
        } -ErrorAction SilentlyContinue
        if ($evts) {
            $first = $evts | Sort-Object TimeCreated | Select-Object -First 1
            $result['attribution'] = '宿主重启所致'
            $result['note'] += ("回溯 ${lookbackMin}min 内检出系统关机/重启事件：" +
                                "{0:MM-dd HH:mm:ss} id={1} {2}" -f $first.TimeCreated, $first.Id, $first.ProviderName)
        } else {
            $result['attribution'] = 'daemon 自身异常退出 · 需 ②stderr 取证'
            $result['note'] += "回溯 ${lookbackMin}min 内无系统关机/重启事件 ⇒ 非宿主重启"
        }
    } catch {
        $result['attribution'] = '未定（事件日志查询失败）'
        $result['note'] += "事件日志查询失败: $($_.Exception.Message)"
    }
}

# ── 输出 ──────────────────────────────────────────────────────────────────────
$exitCode = switch ($result['verdict']) { 'OK' { 0 } 'WARN' { 2 } 'FAIL' { 3 } default { 4 } }

if ($AsJson) {
    $result | ConvertTo-Json -Depth 4
} elseif (-not $Quiet -or $result['verdict'] -ne 'OK') {
    Write-Output ("[daemon-heartbeat] {0}  {1}" -f $result['verdict'], $result['reason'])
    if ($result['attribution']) { Write-Output ("[daemon-heartbeat] 归因: {0}" -f $result['attribution']) }
    if (-not $Quiet) {
        Write-Output ("  H1 存活={0} pids=[{1}]" -f $result['h1_alive'], ($result['h1_pids'] -join ','))
        Write-Output ("  H2 status_pid={0} 一致={1}" -f $result['h2_status_pid'], $result['h2_consistent'])
        Write-Output ("  H3 status龄={0}s 日志龄={1}s 阈值={2}s" -f $result['h3_status_age_s'], $result['h3_log_age_s'], $threshold)
        Write-Output ("  daemon.started_at={0}" -f $result['daemon_started_at'])
        foreach ($n in $result['note']) { Write-Output ("  note: {0}" -f $n) }
    }
}

# ── 告警落盘 + 可选 webhook（仅非 OK）────────────────────────────────────────
# ★ 模拟态（-SimulateStatusPid）**不落盘、不发 webhook** —— 合成输入不得污染真实告警历史
#   （2026-10-26 首测教训：模拟 FAIL 曾写入真实 daemon_heartbeat_alerts.log）。
$isSimulated = ($SimulateStatusPid -gt 0)
if ($result['verdict'] -ne 'OK' -and -not $isSimulated) {
    $line = "{0} [{1}] {2} | 归因={3} | pids=[{4}] status_pid={5}" -f `
        $result['checked_at'], $result['verdict'], $result['reason'],
        $result['attribution'], ($result['h1_pids'] -join ','), $result['h2_status_pid']
    try {
        $dir = Split-Path -Parent $AlertLog
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Add-Content -LiteralPath $AlertLog -Value $line -Encoding UTF8
    } catch {
        Write-Warning "告警日志写入失败: $($_.Exception.Message)"
    }

    if ($Webhook) {
        try {
            $payload = @{
                msg_type = 'text'
                content  = @{ text = "【QuantStudio daemon 心跳】`n$($result['verdict']) — $($result['reason'])`n归因：$($result['attribution'])`n时间：$($result['checked_at'])" }
            } | ConvertTo-Json -Depth 4 -Compress
            Invoke-RestMethod -Uri $Webhook -Method Post -ContentType 'application/json; charset=utf-8' `
                -Body ([System.Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 15 | Out-Null
        } catch {
            Write-Warning "webhook 告警发送失败（不影响判级）: $($_.Exception.Message)"
        }
    }
}

exit $exitCode
