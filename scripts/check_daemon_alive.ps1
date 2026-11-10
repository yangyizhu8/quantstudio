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
#   H3 心跳新鲜   ：**只认 `daemon.log` mtime 龄**，> StaleFactor × 轮询间隔 判疑似假死。
#                   ★★ 2026-10-26 首夜实证修正：**`status.create_time` 不是心跳，是进程启动时刻**
#                   （其龄单调增长：daemon 跑 5h ⇒ 龄 5h）。旧实现把二者并入 max(双龄) ⇒
#                   **启动满 30 分钟即必然 WARN 且永久 WARN**（实证：03:58 起每 10min 一条，
#                   至 08:48 累计 30 条假 WARN，而同期日志 mtime 龄仅 11s）。
#                   现：create_time 仅作展示（已运行时长）；仅当日志不可用才降级为弱信号并注明。
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
    [switch]$Quiet,
    # ── 复活能力段参数（2026-11-10 新增；方案 v1.1 ②审通过）──
    [ValidateSet('observe', 'active', 'off')]
    [string]$ReviveMode = 'observe',
    [int]$ReviveThreshold = 3,
    [int]$ReviveCooldownMinutes = 30,
    [switch]$NoRevive,
    # 仅测试用：强制 verdict（与 -SimulateStatusPid 同族的合成输入通道）。
    # 用途：负向对照需在"生产有 daemon 在跑"的环境下构造 FAIL 分支，否则 H1 恒判活。
    # 安全约束：本参数**不**使复活段进入 simulated 跳过分支；但会**强制 ReviveMode=observe**，
    #           因此绝不可能触发真实拉起（active 被降级并留痕）。
    [ValidateSet('', 'OK', 'WARN', 'FAIL')]
    [string]$SimulateVerdict = ''
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
# ★ 2026-10-26 修（首次真跑误报实证）：必须用 @() 强制数组。
#   缘由：`return $procs` 会把**单元素数组 unroll 成单个 [pscustomobject]**，而
#   `[pscustomobject].Count` 返回 **$null**（PSObject 不像普通标量那样合成 Count 属性），
#   ⇒ `$null -gt 0` = $False ⇒ **对活着的 daemon 误报「未发现进程」**。
#   现象：h1_alive=False 但 h1_pids=[2232]（自相矛盾）、h2_consistent=True（不打自招）。
$daemonProcs = @(Get-DaemonProcs)
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
    # ★ 2026-10-26 修（首夜实证 30 条假 WARN）：
    #   H3 只能用**日志 mtime 龄**作心跳信号。
    #   `status.create_time` 是**进程启动时刻**，其龄随时间单调增长（daemon 跑 5h ⇒ 龄 5h），
    #   与「心跳是否新鲜」完全无关。旧实现把它并入 max(双龄) ⇒ **启动满 30 分钟即必然 WARN
    #   且永久 WARN**（实证：03:58 起每 10min 一条，至 08:48 累计 30 条，而同期日志 mtime 龄仅 11s）。
    #   create_time 仅作展示（已运行时长）；只有当日志不可用时才降级为弱信号并显式标注。
    $ages = @()
    if ($null -ne $result['h3_log_age_s']) { $ages += [int]$result['h3_log_age_s'] }
    if ($ages.Count -eq 0 -and $null -ne $result['h3_status_age_s']) {
        $ages += [int]$result['h3_status_age_s']
        $result['note'] += "日志不可用 ⇒ H3 退化为 create_time 弱信号（该值是启动时刻龄，非心跳）"
    }
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
        $result['reason']  = 'H3: 无法取得任何心跳输入（日志 mtime 不可用，且 create_time 缺失）'
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

# ══════════════════════════════════════════════════════════════════════════════
# 【复活能力段 · 2026-11-10 新增】方案 docs/daemon-guard-revival-design-20261110.md v1.1（②审通过）
#   判型：新增能力型（铁律 18）——**既有 H1/H2/H3 判据、匹配式、StaleFactor、退出码 0/2/3/4 一字未改**；
#         本段只在其后追加「是否拉起」的决策与执行。
#   闸序：F1 计数（排除 normal-exit）→ F2 阈值 → F3 占用声明 → F4 冷却 → F11 旁证四探 → 拉起 → F6 自证
#   红线：唯一拉起路径 = schtasks /Run /TN QS_DaemonForever（禁 Start-Process）；
#         不写库、不改 daemon 代码；observe 模式只记不拉（默认）。
# ══════════════════════════════════════════════════════════════════════════════
# ── 合成 verdict 注入（仅测试用；2026-11-10）────────────────────────────────
# 不改既有判级逻辑，只在其后覆盖。安全约束：强制 observe，绝不可能真实拉起。
if ($SimulateVerdict) {
    $result['verdict'] = $SimulateVerdict
    $result['reason']  = "[合成输入] verdict 强制为 $SimulateVerdict（原: $($result['reason'])）"
    $result['note']   += "合成 verdict 注入（-SimulateVerdict）"
    if ($ReviveMode -eq 'active') {
        $ReviveMode = 'observe'
        $result['note'] += "安全约束：-SimulateVerdict 下 ReviveMode 由 active 降级为 observe（禁止合成输入触发真实拉起）"
    }
}

# ★ 模拟态标志统一在这里定义（2026-11-10）——复活段在输出段之前就要用到它，
#   原先定义在告警落盘段（更后），会导致复活决策读到 $null ⇒ 模拟态误入真实决策链。
$isSimulated = ($SimulateStatusPid -gt 0)

$reviveStatePath = Join-Path $RepoRoot 'data\daemon_revival_state.json'
$reviveLogPath   = Join-Path $RepoRoot 'data\logs\daemon_revival.log'
$occupancyPath   = Join-Path $RepoRoot 'data\daemon_occupancy.json'
$exitcodePath    = Join-Path $RepoRoot 'data\logs\daemon_exitcode.log'
$reviveTaskName  = 'QS_DaemonForever'

$revive = [ordered]@{
    mode            = $ReviveMode
    evaluated       = $false
    consecutive     = 0
    gate            = ''
    action          = 'NONE'
    detail          = ''
    revived_pid     = $null
}

function Write-ReviveLog([hashtable]$f) {
    try {
        $dir = Split-Path -Parent $reviveLogPath
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $line = "{0} verdict={1} consecutive={2} gate={3} action={4} target_pid={5} | {6}" -f `
            $now.ToString('yyyy-MM-dd HH:mm:ss'), $result['verdict'], $f['consecutive'],
            $f['gate'], $f['action'], $(if ($null -ne $f['target_pid']) { $f['target_pid'] } else { '-' }), $f['detail']
        Add-Content -LiteralPath $reviveLogPath -Value $line -Encoding UTF8
    } catch {
        Write-Warning "revival 日志写入失败: $($_.Exception.Message)"
    }
}

function Save-ReviveState([hashtable]$s) {
    try {
        $dir = Split-Path -Parent $reviveStatePath
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $json = ($s | ConvertTo-Json -Depth 6)
        # 无 BOM + 原子写（先 .tmp 再 Move）
        $tmp = "$reviveStatePath.tmp"
        [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $tmp -Destination $reviveStatePath -Force
    } catch {
        Write-Warning "revival 状态写入失败: $($_.Exception.Message)"
    }
}

$reviveEnabled = (-not $NoRevive) -and ($ReviveMode -ne 'off')
if (-not $reviveEnabled) {
    $revive['gate']   = 'disabled'
    $revive['detail'] = if ($NoRevive) { '-NoRevive 显式禁用' } else { '-ReviveMode off' }
} elseif ($isSimulated) {
    # 合成输入绝不触发真实拉起/状态变更（与告警落盘同纪律）
    $revive['gate']   = 'simulated'
    $revive['detail'] = '模拟态（-SimulateStatusPid）不参与复活决策'
} else {
    $revive['evaluated'] = $true

    # ── 读状态 ──
    $st = [ordered]@{ consecutive = 0; last_check_at = $null; last_revive_at = $null;
                      last_action = $null; observed_since = $now.ToString('yyyy-MM-dd HH:mm:ss') }
    if (Test-Path $reviveStatePath) {
        try {
            $old = Get-Content -LiteralPath $reviveStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($null -ne $old.consecutive)       { $st['consecutive']    = [int]$old.consecutive }
            if ($old.last_revive_at)              { $st['last_revive_at'] = $old.last_revive_at }
            if ($old.observed_since)              { $st['observed_since'] = $old.observed_since }
        } catch {
            $revive['detail'] += "状态文件解析失败(按0计): $($_.Exception.Message); "
        }
    }

    # ── F1：连续 FAIL 计数（★ 排除 normal-exit —— 这是与"直接对 FAIL 计数"的关键差异）──
    $lastExit = $null
    if (Test-Path $exitcodePath) {
        try {
            $tail = @(Get-Content -LiteralPath $exitcodePath -Tail 3 -Encoding UTF8)
            for ($i = $tail.Count - 1; $i -ge 0; $i--) {
                if ($tail[$i] -match '^\d{4}-\d{2}-\d{2}.*EXIT') { $lastExit = $tail[$i]; break }
            }
        } catch {
            $revive['detail'] += "exitcode.log 读取失败: $($_.Exception.Message); "
        }
    }
    $isNormalExit = ($null -ne $lastExit) -and ($lastExit -match '\(normal-exit\)')

    if ($result['verdict'] -eq 'FAIL' -and -not $isNormalExit) {
        $st['consecutive'] = [int]$st['consecutive'] + 1
    } elseif ($result['verdict'] -eq 'FAIL' -and $isNormalExit) {
        # 正常退出后的"进程不在"不是故障 ⇒ 计数归零，且不进任何闸
        $st['consecutive'] = 0
        $revive['gate']    = 'skip-normal-exit'
        $revive['detail'] += "最近一次退出为 normal-exit（$($lastExit.Trim())）⇒ 非真死，不计数不拉起"
    } else {
        $st['consecutive'] = 0
    }
    $revive['consecutive'] = [int]$st['consecutive']

    if ($revive['gate'] -eq 'skip-normal-exit') {
        # 已判定跳过
    }
    # ── F2：阈值闸 ──
    elseif ($st['consecutive'] -lt $ReviveThreshold) {
        $revive['gate']   = "skip-threshold($($st['consecutive'])/$ReviveThreshold)"
        $revive['detail'] += "连续 FAIL 未达阈值"
    }
    else {
        # ── F3：占用声明闸 ──
        $occupied = $false
        $occDetail = 'occupancy.json 不存在或未占用'
        if (Test-Path $occupancyPath) {
            try {
                $occ = Get-Content -LiteralPath $occupancyPath -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($occ.occupied -eq $true) {
                    $inWindow = $true
                    if ($occ.window -and @($occ.window).Count -ge 2) {
                        try {
                            $ws = [System.DateTimeOffset]::Parse([string]@($occ.window)[0]).LocalDateTime
                            $we = [System.DateTimeOffset]::Parse([string]@($occ.window)[1]).LocalDateTime
                            $inWindow = ($now -ge $ws) -and ($now -le $we)
                        } catch {
                            $inWindow = $true   # 窗口解析失败 ⇒ fail-closed 视为占用
                        }
                    }
                    if ($inWindow) {
                        $occupied = $true
                        $occDetail = "占用窗内（owner=$($occ.owner)；$((@($occ.window))[0]) ~ $((@($occ.window))[1])）"
                    }
                }
            } catch {
                $occDetail = "occupancy.json 解析失败（按未占用处理并告警）: $($_.Exception.Message)"
            }
        }
        if ($occupied) {
            $revive['gate']   = 'skip-occupancy'
            $revive['detail'] += $occDetail
        }
        # ── F4：冷却闸 ──
        elseif ($st['last_revive_at']) {
            $cooling = $false
            try {
                $lr = [datetime]::Parse($st['last_revive_at'])
                $mins = ($now - $lr).TotalMinutes
                if ($mins -lt $ReviveCooldownMinutes) { $cooling = $true; $occDetail = "冷却中（上次拉起 $($st['last_revive_at'])，已 $([int]$mins)min < $ReviveCooldownMinutes min）" }
            } catch { }
            if ($cooling) {
                $revive['gate']   = 'skip-cooldown'
                $revive['detail'] += $occDetail
            }
        }

        if ($revive['gate'] -eq '') {
            # ── F11：旁证四探（R3b「自停未声明」专项；任一命中即不拉）──
            $probes = @()
            # 探① WAL 活跃：存在且近 5 分钟 mtime 有变
            $walPath = Join-Path $RepoRoot 'data\quantstudio.db.wal'
            if (Test-Path $walPath) {
                try {
                    $w1 = Get-Item -LiteralPath $walPath
                    $ageSec = ($now - $w1.LastWriteTime).TotalSeconds
                    if ($ageSec -le 300) { $probes += "WAL活跃(mtime ${ageSec}s前, $([math]::Round($w1.Length/1MB,2))MB)" }
                } catch { }
            }
            # 探② 独占试探：能独占打开 ⇒ 无他人持库
            $dbPath = Join-Path $RepoRoot 'data\quantstudio.db'
            $locked = $false
            if (Test-Path $dbPath) {
                try {
                    $fs = [System.IO.File]::Open($dbPath, 'Open', 'ReadWrite', 'None')
                    $fs.Close(); $fs.Dispose()
                } catch {
                    $locked = $true
                }
                if ($locked) { $probes += "主库被独占(不可 ReadWrite/None 打开)" }
            }
            # 探③ 自停痕迹：daemon.log 近 30 分钟有停止请求/清理 status，而 exitcode 无 normal-exit
            if (Test-Path $DaemonLog) {
                try {
                    $logAge = ($now - (Get-Item -LiteralPath $DaemonLog).LastWriteTime).TotalMinutes
                    if ($logAge -le 30) {
                        $tailLog = @(Get-Content -LiteralPath $DaemonLog -Tail 40 -Encoding UTF8)
                        $hasStopTrace = @($tailLog | Where-Object { $_ -match '收到停止请求|已清理本进程 status 文件' }).Count -gt 0
                        if ($hasStopTrace) { $probes += "daemon.log 近30min有自停痕迹但 exitcode 无 normal-exit" }
                    }
                } catch { }
            }
            # 探④ 锁文件时间：.daemon.lock 近 30 分钟活跃
            $lockPath = Join-Path $RepoRoot 'data\.daemon.lock'
            if (Test-Path $lockPath) {
                try {
                    $lockAge = ($now - (Get-Item -LiteralPath $lockPath).LastWriteTime).TotalMinutes
                    if ($lockAge -le 30) { $probes += ".daemon.lock 近30min活跃($([int]$lockAge)min)" }
                } catch { }
            }

            if ($probes.Count -gt 0) {
                $revive['gate']   = 'skip-suspected_activity'
                $revive['detail'] += ("旁证命中 " + ($probes -join ' / '))
            }
            else {
                # ── 全闸通过：执行（observe 只记不拉）──
                $revive['gate'] = 'all-pass'
                if ($ReviveMode -ne 'active') {
                    $revive['action'] = 'REVIVE_ARMED'
                    $revive['detail'] += "observe 模式：本应拉起，仅记录（切 active 需 5 组负向对照全过 + 观察 ≥24h）"
                } else {
                    # F5 唯一路径拉起
                    try {
                        $null = & schtasks /Run /TN $reviveTaskName 2>&1
                        $runRc = $LASTEXITCODE
                        $revive['action'] = 'REVIVE_CALLED'
                        $revive['detail'] += "schtasks /Run 返回码=$runRc"
                        $st['last_revive_at'] = $now.ToString('yyyy-MM-dd HH:mm:ss')

                        # F6 拉起后自证（≤60s，轮询 5s）
                        $deadline = $now.AddSeconds(60)
                        $ok = $false
                        while ((Get-Date) -lt $deadline) {
                            Start-Sleep -Seconds 5
                            $p2 = @(Get-DaemonProcs)
                            if ($p2.Count -gt 0) {
                                $revive['revived_pid'] = $p2[0].pid
                                $ok = $true
                                break
                            }
                        }
                        if ($ok) {
                            $revive['action'] = 'REVIVED'
                            $revive['detail'] += "自证成功 pid=$($revive['revived_pid'])"
                            $st['consecutive'] = 0
                            $result['note'] += "[REVIVED] 已自动拉起 daemon pid=$($revive['revived_pid'])（同时记入告警流）"
                        } else {
                            $revive['action'] = 'REVIVE_FAILED'
                            $revive['detail'] += "60s 内未见到进程，保留计数待下轮"
                        }
                    } catch {
                        $revive['action'] = 'REVIVE_DENIED'
                        $revive['detail'] += "拉起异常: $($_.Exception.Message)"
                    }
                }
            }
        }
    }

    # ── F10 留痕：**每次评估都落一行**（含所有 SKIP 分支）
    #   2026-11-10 负向对照暴露：原实现把本调用嵌在「四探全过」分支内 ⇒ 5 类 SKIP 决策无痕（违反 F10 留痕完整）
    Write-ReviveLog @{ consecutive = $st['consecutive']; gate = $revive['gate'];
                       action = $revive['action']; target_pid = $revive['revived_pid'];
                       detail = $revive['detail'] }

    # ── 状态落盘 ──
    $st['last_check_at'] = $now.ToString('yyyy-MM-dd HH:mm:ss')
    $st['last_action']   = $revive['action']
    Save-ReviveState $st
}

$result['revive'] = $revive

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
#   注：$isSimulated 已在上方复活段之前统一赋值（2026-11-10 调整），此处不重复定义。
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
