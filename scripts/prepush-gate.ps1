<#
.SYNOPSIS
  推送前闸门（C7 机器化）—— 按「实际推送范围」做全量显式声明覆盖判定。

.DESCRIPTION
  方案 a（2026-10-20 总调度裁定）两处修复：
    ① 窗口 = pre-push stdin 的实际推送范围（<remote_sha>..<local_sha>），
       不再使用 origin/main..HEAD；sha 形态 refspec（git push <sha>:main）同样适用；
    ② 归属判定 = 推送范围内**全部提交须被 -Commit 锚或 -AllowAlso 声明覆盖**，
       任一未声明即 exit 9（全量显式声明制；-Commit 锚只覆盖其自身）。

  放行通道：
    - 声明齐全：QS_PUSH_BUNDLE_ALLOWALSO / -AllowAlso 逐一覆盖范围内全部非锚提交；
    - 裁定引用（可选，留痕用）：QS_PUSH_BUNDLE_RULING；
    - 放行事实强制留痕：docs/handoff/push-bundle-rulings.log。

  已知可绕过面（诚实披露，不声称绝对安全）：
    `git push --no-verify` 可跳过 hook —— 技术上无法阻止。
    对策：① C7 人工三查照旧执行（本门是**叠加**，非替代）；② 留痕缺失可被事后审计发现；
          ③ 纪律要求「任何 --no-verify 推送须在回报中声明」。

.PARAMETER Commit
  推送范围的锚提交（hook 传入实际推送的 local_sha；须在推送范围内，否则拒绝——防误传）。
.PARAMETER AllowAlso
  允许一并推送的范围内其他提交 sha（逗号分隔，支持前缀）。须**逐一覆盖**全部非锚提交。
.PARAMETER LocalSha
  实际推送的本地 tip（hook 从 stdin 解析；缺省用 -Commit 解析值）。
.PARAMETER RemoteSha
  实际推送的远端 base（hook 从 stdin 解析；全零=新建分支）。
.PARAMETER ReportOnly
  只报告不阻断（仅用于核验流程，不用于实际推送）。

.EXAMPLE
  # 正常推送（由 .githooks/pre-push 自动调用，含实际推送范围）
  powershell -File scripts/prepush-gate.ps1 -Commit <sha> -LocalSha <sha> -RemoteSha <sha>

.EXAMPLE
  # 全量声明（推送范围内全部提交逐一列明）
  $env:QS_PUSH_BUNDLE_ALLOWALSO = '<sha1>,<sha2>'
  powershell -File scripts/prepush-gate.ps1 -Commit <锚> -LocalSha <sha> -RemoteSha <sha> -AllowAlso '<sha1>,<sha2>'
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Commit,
  [string]$AllowAlso = '',
  [string]$Remote = 'origin',
  [string]$Branch = 'main',
  [string]$LocalSha = '',
  [string]$RemoteSha = '',
  [switch]$ReportOnly
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$LogPath = Join-Path $Root 'docs\handoff\push-bundle-rulings.log'
$ZERO = ('0' * 40)

function Resolve-Sha([string]$shaLike) {
  # 解析为完整 commit sha；失败返回 $null（不抛）。EAP 受控降级：git 预期情形会写 stderr。
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $out = @(git rev-parse --verify --quiet "$shaLike^{commit}" 2>$null)
    if ($LASTEXITCODE -eq 0 -and $out.Count -gt 0 -and $out[0]) {
      return (($out[0]) -split "`n")[0].Trim()
    }
  }
  catch { }
  finally {
    $ErrorActionPreference = $prev
  }
  return $null
}

function Test-PartialPushLeak([object[]]$PendingEntries) {
  # 【CORP-01 附录（2026-10-07 裁定照准）】拒绝路径部分成功检测：
  # 多 push URL 场景下，git 按子推送串行执行，本门拒绝时**第一个 URL 可能已完成传输**
  # （10-07 实证：无裁定首推被拒时 quantstudio-plus 已上云）。本检测在拒绝时主动
  # ls-remote 各 push URL，若待推清单中任一提交已可追溯于远端 tip ⇒ 大声告警+留痕，
  # 供人工立即处置（补裁定/回滚远端），消除「拒了以为没推」盲点。
  try {
    $pushUrls = @(git remote get-url --all --push $Remote 2>$null)
    foreach ($u in $pushUrls) {
      $tipLine = git ls-remote $u "refs/heads/$Branch" 2>$null | Select-Object -First 1
      if (-not $tipLine) { continue }
      $tip = ($tipLine -split "`t")[0]
      if (-not $tip -or $tip -notmatch '^[0-9a-f]{40}$') { continue }
      $leaked = @()
      foreach ($e in $PendingEntries) {
        git merge-base --is-ancestor $e.Sha $tip 2>$null
        if ($LASTEXITCODE -eq 0) { $leaked += $e }
      }
      if ($leaked.Count -gt 0) {
        Write-Host ''
        Write-Host ("[prepush-gate] ⚠️ 部分成功泄漏：拒绝之后 {0} 已含待推提交 {1} 笔（{2}…）" `
                    -f $u, $leaked.Count, ($leaked[0].Sha.Substring(0, 8))) -ForegroundColor Yellow
        Write-Host '[prepush-gate] 处置：立即人工核对（补裁定推送 or 远端回滚），并在回报中声明。'
        $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        $shas = ($leaked | ForEach-Object { $_.Sha.Substring(0, 8) }) -join ','
        Add-Content -Path $LogPath -Value ("[{0}] PARTIAL-PUSH-LEAK remote={1} branch={2} leaked={3}" `
          -f $stamp, $u, $Branch, $shas) -Encoding utf8
      }
    }
  } catch {
    # 检测失败不改变本门主判定（拒绝仍拒绝），只提示检测不可用
    Write-Host '[prepush-gate] （部分成功检测异常跳过：' $_.Exception.Message '）'
  }
}

function Write-Denied([string]$msg) {
  Write-Host ''
  Write-Host ('[prepush-gate] 拒绝推送：' + $msg) -ForegroundColor Red
  Write-Host '[prepush-gate] 纪律（C7）：推送范围内全部提交须被 -Commit 锚或 -AllowAlso 声明覆盖；'
  Write-Host '              确需捆绑须显式列明（QS_PUSH_BUNDLE_ALLOWALSO / -AllowAlso），任一未声明即拒推。'
  Test-PartialPushLeak $script:DeniedEntries
  if ($ReportOnly) {
    Write-Host '[prepush-gate] （ReportOnly 模式：仅报告，不阻断）'
    exit 0
  }
  exit 9
}

# —— 解析实际推送范围（修复①）——
Push-Location $Root
$rangeEnd = $null
$base = $null
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
  if ($LocalSha) { $rangeEnd = Resolve-Sha $LocalSha }
  if (-not $rangeEnd) { $rangeEnd = Resolve-Sha $Commit }
  if ($RemoteSha -and ($RemoteSha -ne $ZERO)) { $base = Resolve-Sha $RemoteSha }
  if (-not $base) {
    $tracking = @(git rev-parse --verify --quiet "$Remote/$Branch" 2>$null)
    if ($LASTEXITCODE -eq 0 -and $tracking.Count -gt 0 -and $tracking[0]) {
      $base = (($tracking[0]) -split "`n")[0].Trim()
    }
  }
} finally {
  $ErrorActionPreference = $prevEAP
}

if (-not $rangeEnd) {
  Write-Host '[prepush-gate] 拒绝推送：无法解析推送 tip（-LocalSha/-Commit 均解析失败）——防误传' -ForegroundColor Red
  exit 9
}

if (-not $base) {
  # 无参照 base（新建分支 / 首次推送）：无「推送范围」可比 ⇒ 放行但显式提示，不伪装成"清单为空"。
  Write-Host ''
  Write-Host ("[prepush-gate] 无远端参照（{0}/{1} 不存在或首推）⇒ PASS（无范围可比）" -f $Remote, $Branch)
  Write-Host '[prepush-gate] 提示：若认为该分支应存在，请先 git fetch 后重试。'
  Pop-Location
  exit 0
}

$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
  $spec = "$base..$rangeEnd"
  $raw = @(git log --format="%H`t%s" $spec 2>&1)
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
# 供拒绝路径部分成功检测消费（CORP-01 附录）
$script:DeniedEntries = $entries

if ($entries.Count -eq 0) {
  Write-Host ("[prepush-gate] 推送范围为空（{0}..{1}）⇒ PASS" -f $base.Substring(0, 8), $rangeEnd.Substring(0, 8))
  exit 0
}

# —— 定位锚（本件）——
$mine = @($entries | Where-Object { $_.Sha -like "$Commit*" })
Write-Host ("[prepush-gate] 推送范围（{0}..{1}，共 {2} 笔）：" -f $base.Substring(0, 8), $rangeEnd.Substring(0, 8), $entries.Count)
foreach ($e in $entries) {
  $isMine = ($mine.Count -gt 0 -and $e.Sha -eq $mine[0].Sha)
  $mark = if ($isMine) { '  [锚]' } else { '  [声明]' }
  Write-Host ("{0} {1}  {2}" -f $mark, $e.Sha.Substring(0, 8), $e.Subject)
}

if ($mine.Count -eq 0) {
  Write-Denied ("推送范围内找不到锚提交（-Commit {0}）—— 防误传，直接拒绝" -f $Commit)
}
$mineSha = $mine[0].Sha

# —— 全量显式声明制（修复②）：范围内全部提交须被锚或 AllowAlso 覆盖 ——
$allowed = @()
if ($AllowAlso) {
  $allowed = @($AllowAlso -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

$others = @($entries | Where-Object { $_.Sha -ne $mineSha })
$uncovered = @($others | Where-Object {
    $o = $_
    @($allowed | Where-Object { $o.Sha -like "$_*" }).Count -eq 0
  })

if ($uncovered.Count -gt 0) {
  Write-Host ''
  Write-Host ("[prepush-gate] 推送范围内 {0} 笔提交未被声明（锚覆盖自身；其余须逐一列出）：" -f $uncovered.Count)
  foreach ($u in $uncovered) {
    Write-Host ("    {0}  {1}" -f $u.Sha.Substring(0, 8), $u.Subject)
  }
  Write-Host '[prepush-gate] 声明方式：环境变量 QS_PUSH_BUNDLE_ALLOWALSO=<sha1,sha2>（hook 透传）或 -AllowAlso。'
  Write-Denied '推送范围内存在未声明的提交（全量显式声明制，任一未声明即拒推）'
}

# —— P2-b（2026-10-22 ②审照准·裁定⑤ WARN 起步）／M1-2 抽壳：KB 条目分族体检（只 WARN 不阻断）——
# 体检逻辑已抽壳至 scripts/kb_hook.py（通用件）：读 knowledge/kb-info.yaml 的 families 段，
# 按 entry_glob 把推送范围内改动的条目件路由到所属族，用该族 schema 校验。
# 观察一轮后由总调度裁定是否升 BLOCK（kb_hook --strict）；检测自身异常 ⇒ 仅提示不可用，绝不阻断推送。
try {
  $kbFiles = @(git diff --name-only "$base..$rangeEnd" 2>$null | Where-Object {
      $_ -match '^knowledge/.+\.md$' })
  if ($kbFiles.Count -gt 0) {
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    $pyPath = if ($pyCmd) { $pyCmd.Source } else { 'C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe' }
    Push-Location $Root
    try { $kbOut = (& $pyPath scripts/kb_hook.py --files @kbFiles --repo-root $Root --json 2>$null | Out-String) } finally { Pop-Location }
    $kbJson = $null
    try { $kbJson = $kbOut | ConvertFrom-Json } catch { }
    if ($kbJson -and $kbJson.bad -and @($kbJson.bad).Count -gt 0) {
      Write-Host ''
      Write-Host ('[prepush-gate][KB-WARN] {0}/{1} 条 knowledge 条目 schema 不合规（WARN 起步·不阻断，观察一轮后裁定升级）：' -f @($kbJson.bad).Count, $kbJson.checked) -ForegroundColor Yellow
      foreach ($b in @($kbJson.bad)) {
        Write-Host ('    {0} : {1}' -f $b.file, ($b.errors -join '; ')) -ForegroundColor Yellow
      }
      Write-Host '[prepush-gate][KB-WARN] 处置指引：修复条目后再推（QD-0010 类先修不走豁免·裁定④），本门不代豁免。' -ForegroundColor Yellow
    } elseif ($kbJson) {
      Write-Host ("[prepush-gate][KB] {0} 条 knowledge 条目 schema 全部合规" -f $kbJson.checked)
    } else {
      Write-Host '[prepush-gate][KB-WARN] KB 体检执行不可用（python/解析异常）——跳过，不阻断' -ForegroundColor Yellow
    }
  }
} catch {
  Write-Host "[prepush-gate][KB-WARN] KB 体检异常跳过（$($_.Exception.Message)）——不阻断" -ForegroundColor Yellow
}

# —— 声明齐全：放行 ——
Write-Host ''
if ($others.Count -eq 0) {
  Write-Host '[prepush-gate] 仅锚提交 ⇒ PASS'
  exit 0
}

# 有声明捆绑：留痕（裁定引用可选，仅作元数据）
$stamp = (Get-Date).ToString('yyyy-MM-ddTHH:mmK')
$bundled = (($others | ForEach-Object { $_.Sha.Substring(0, 8) }) -join ',')
$ruling = if ($env:QS_PUSH_BUNDLE_RULING) { $env:QS_PUSH_BUNDLE_RULING } else { '(none)' }
$logLine = ("{0} | ruling={1} | commit={2} | declared={3} | range={4}..{5}" -f $stamp,
  $ruling, $mineSha.Substring(0, 8), $bundled, $base.Substring(0, 8), $rangeEnd.Substring(0, 8))
$logDir = Split-Path -Parent $LogPath
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
Add-Content -LiteralPath $LogPath -Value $logLine -Encoding UTF8

Write-Host '[prepush-gate] 放行（全量声明覆盖）'
Write-Host ("    锚提交   : {0}" -f $mineSha.Substring(0, 8))
Write-Host ("    声明捆绑 : {0}" -f $bundled)
Write-Host ("    裁定引用 : {0}" -f $ruling)
Write-Host ("    留痕     : {0}" -f $LogPath)
exit 0
