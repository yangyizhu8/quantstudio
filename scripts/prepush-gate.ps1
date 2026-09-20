<#
.SYNOPSIS
  推送前闸门（C7 机器化）—— 本地领先清单含「非本件提交」即拒推。

.DESCRIPTION
  背景（C7 实例 #2 / #3）
    实例 #2（2026-09-18）：本线推送 80b7744/73cd8b4 时，他线 c8c1b75（闸门未齐）随推泄漏；
    实例 #3（2026-09-20）：本线推送 f2d588f 时，他线 4a863af（标注「六步①，待审」）随推泄漏。
    两次根因完全相同 —— **检查动作做了、判据用错**：C7 ①（打印清单）每次都执行了，
    但 ③ 的判据只覆盖「本笔闸门是否齐备」，未覆盖「清单中其他笔是否已过闸门」。
    本门把该纪律从「人看清单」变为「机器拦人」。

  放行通道（唯一例外，刻意不自动化）
    经用户裁定批准的**捆绑推送**（先例：生态件带 98af33d）须同时满足：
     ① 裁定引用：环境变量 QS_PUSH_BUNDLE_RULING=<裁定标识>（如日历节号）
     ② 显式列明：-AllowAlso <sha,sha>
    清单中任何非本件提交若未被 -AllowAlso **逐一覆盖** ⇒ 仍拒推（**不留后门**）。
    hook 不自动读取裁定 ⇒ **例外必须是有意识的动作**（由人显式导出变量）。
    放行事实**强制留痕**：docs/handoff/push-bundle-rulings.log。

  已知可绕过面（诚实披露，不声称绝对安全）
    `git push --no-verify` 可跳过 hook —— 技术上无法阻止。
    对策：① C7 人工三查照旧执行（本门是**叠加**，非替代）；② 留痕缺失可被事后审计发现；
          ③ 纪律要求「任何 --no-verify 推送须在回报中声明」。

.PARAMETER Commit
  本件提交的 sha（或前缀）。清单中必须存在该提交，否则直接拒绝（防误传）。
.PARAMETER AllowAlso
  允许一并推送的其他提交 sha（逗号分隔）。仅在提供裁定引用时生效，且须**逐一覆盖**。
.PARAMETER ReportOnly
  只报告不阻断（用于核验流程，不用于实际推送）。

.EXAMPLE
  # 正常单笔推送（由 .githooks/pre-push 自动调用）
  pwsh -File scripts/prepush-gate.ps1 -Commit <sha>

.EXAMPLE
  # 经用户裁定批准的捆绑推送
  $env:QS_PUSH_BUNDLE_RULING = '日历§一二三'
  pwsh -File scripts/prepush-gate.ps1 -Commit <本件sha> -AllowAlso <他件sha1>,<他件sha2>
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Commit,
  [string]$AllowAlso = '',
  [string]$Remote = 'origin',
  [string]$Branch = 'main',
  [switch]$ReportOnly
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$LogPath = Join-Path $Root 'docs\handoff\push-bundle-rulings.log'

function Write-Denied([string]$msg) {
  Write-Host ''
  Write-Host ('[prepush-gate] 拒绝推送：' + $msg) -ForegroundColor Red
  Write-Host '[prepush-gate] 纪律（C7）：非本件提交须先声明、由归属会话确认闸门；'
  Write-Host '              确需捆绑须带用户裁定引用（QS_PUSH_BUNDLE_RULING）+ 显式列明（-AllowAlso）。'
  if ($ReportOnly) {
    Write-Host '[prepush-gate] （ReportOnly 模式：仅报告，不阻断）'
    exit 0
  }
  exit 9
}

Push-Location $Root
$raw = @()
# 关键实现纪律（2026-09-20 自测暴露）：git 会把「远程无该分支」「引用不存在」等**预期情形**
# 写到 stderr，而 `$ErrorActionPreference='Stop'` 会让 PowerShell 将其视为致命错误**中止脚本**，
# 导致退出码变成 1（而非本门设计的 0/9）——全部场景因此失效。
# ⇒ 本段在**受控范围内**降级 EAP，仅在最后恢复；不改变脚本其余部分的严格性。
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
  # 先确保追踪引用与服务端一致：C7 反复强调「以实测为真值」——本地追踪引用可能过期，
  # 过期会让「本地领先清单」失真（多算或少算），从而使本门形同虚设。
  git fetch $Remote $Branch 2>&1 | Out-Null
  git rev-parse --verify "$Remote/$Branch" 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) {
    # 远程分支不存在 ⇒ 首次推送（建仓/初始推送）：无「本地领先清单」参照可比。
    # 此时放行，但**显式提示**——不伪装成「清单为空」，避免掩盖真实情况。
    Write-Host ''
    Write-Host ("[prepush-gate] 远程 {0}/{1} 尚不存在（首次推送）⇒ PASS（无参照可比）" -f $Remote, $Branch)
    Write-Host '[prepush-gate] 提示：若您认为该分支应当存在，请先 git fetch 后重试。'
    $ErrorActionPreference = $prevEAP
    Pop-Location
    exit 0
  }
  $raw = @(git log --format="%H`t%s" "$Remote/$Branch..HEAD" 2>&1)
} finally {
  $ErrorActionPreference = $prevEAP
  Pop-Location
}
# 过滤 git 的提示/告警行（非提交行）：提交行形如 "<40位sha>\t<subject>"
$raw = @($raw | Where-Object { $_ -match '^[0-9a-f]{40}\t' })
$entries = @()
foreach ($line in $raw) {
  if (-not $line -or -not $line.Trim()) { continue }
  $p = $line -split "`t", 2
  $entries += [pscustomobject]@{
    Sha     = $p[0]
    Subject = $(if ($p.Count -gt 1) { $p[1] } else { '' })
  }
}

if ($entries.Count -eq 0) {
  Write-Host ("[prepush-gate] 清单为空（无本地领先 {0}/{1} 的提交）⇒ PASS" -f $Remote, $Branch)
  exit 0
}

# —— 定位本件 ——
$mine = @($entries | Where-Object { $_.Sha -like "$Commit*" })
Write-Host ("[prepush-gate] 待推清单（{0}/{1}..HEAD，共 {2} 笔）：" -f $Remote, $Branch, $entries.Count)
foreach ($e in $entries) {
  $isMine = ($mine.Count -gt 0 -and $e.Sha -eq $mine[0].Sha)
  $mark = if ($isMine) { '  [本件]' } else { '  [其他]' }
  Write-Host ("{0} {1}  {2}" -f $mark, $e.Sha.Substring(0, 8), $e.Subject)
}

if ($mine.Count -eq 0) {
  Write-Denied ("清单中找不到本件提交（-Commit {0}）—— 防误传，直接拒绝" -f $Commit)
}
$mineSha = $mine[0].Sha
$others = @($entries | Where-Object { $_.Sha -ne $mineSha })

if ($others.Count -eq 0) {
  Write-Host '[prepush-gate] 仅本件 ⇒ PASS'
  exit 0
}

# —— 有非本件提交：检查放行通道 ——
if (-not $env:QS_PUSH_BUNDLE_RULING) {
  Write-Host ''
  Write-Host ("[prepush-gate] 检出 {0} 笔非本件提交，且未提供裁定引用：" -f $others.Count)
  foreach ($o in $others) {
    Write-Host ("    {0}  {1}" -f $o.Sha.Substring(0, 8), $o.Subject)
  }
  Write-Denied '存在非本件提交且无 QS_PUSH_BUNDLE_RULING 裁定引用'
}

$allowed = @()
if ($AllowAlso) {
  $allowed = @($AllowAlso -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}
if ($allowed.Count -eq 0) {
  Write-Denied ("有裁定引用（{0}）但未用 -AllowAlso 列明允许捆绑的提交" -f $env:QS_PUSH_BUNDLE_RULING)
}

$uncovered = @($others | Where-Object {
    $o = $_
    @($allowed | Where-Object { $o.Sha -like "$_*" }).Count -eq 0
  })
if ($uncovered.Count -gt 0) {
  Write-Host '[prepush-gate] 以下非本件提交未被 -AllowAlso 覆盖：'
  foreach ($u in $uncovered) {
    Write-Host ("    {0}  {1}" -f $u.Sha.Substring(0, 8), $u.Subject)
  }
  Write-Denied '裁定引用未能覆盖全部非本件提交（一一对应，不留后门）'
}

# —— 放行 + 强制留痕 ——
$stamp = (Get-Date).ToString('yyyy-MM-ddTHH:mmK')
$bundled = (($others | ForEach-Object { $_.Sha.Substring(0, 8) }) -join ',')
$logLine = ("{0} | ruling={1} | commit={2} | bundled={3}" -f $stamp,
  $env:QS_PUSH_BUNDLE_RULING, $mineSha.Substring(0, 8), $bundled)
$logDir = Split-Path -Parent $LogPath
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
Add-Content -LiteralPath $LogPath -Value $logLine -Encoding UTF8

Write-Host ''
Write-Host '[prepush-gate] 放行（裁定捆绑）'
Write-Host ("    裁定引用 : {0}" -f $env:QS_PUSH_BUNDLE_RULING)
Write-Host ("    本件     : {0}" -f $mineSha.Substring(0, 8))
Write-Host ("    捆绑     : {0}" -f $bundled)
Write-Host ("    留痕     : {0}" -f $LogPath)
exit 0
