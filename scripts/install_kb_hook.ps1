<#
.SYNOPSIS
  安装通用 KB 体检 pre-push 钩子到指定项目（KMAP-M1 · 接入模板包）。

.DESCRIPTION
  在 <Repo>/.githooks/ 写 pre-push（UTF-8 BOM），并设置 core.hooksPath=.githooks。
  钩子行为：从 stdin 解析实际推送范围 → git diff --name-only 取推送内改动 → 调 kb_hook.py 分族体检。
  **WARN 起步**：默认永不阻断推送（观察一轮后由项目裁定改 --strict）。
  依赖：<Repo>/scripts/kb_hook.py 与 <Repo>/knowledge/kb-info.yaml 已就位。

.PARAMETER Repo
  目标项目根（缺省当前目录）。

.PARAMETER Strict
  装成阻断版（kb_hook --strict：有条目不合规即拒推）。缺省 WARN 版。

.EXAMPLE
  powershell -File scripts/install_kb_hook.ps1 -Repo C:\path\to\project
#>
[CmdletBinding()]
param(
  [string]$Repo = (Get-Location).Path,
  [switch]$Strict
)
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path $Repo).Path
$hooks = Join-Path $Repo '.githooks'
if (-not (Test-Path $hooks)) { New-Item -ItemType Directory -Force -Path $hooks | Out-Null }
$hook = Join-Path $hooks 'pre-push'
$strictArg = if ($Strict) { ' --strict' } else { '' }
$body = @'
# KB 体检 pre-push（KMAP 通用钩子 · 由 install_kb_hook.ps1 生成）
# WARN 起步：默认不阻断；--strict 版才以非零退出拒推。
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$lines = @($input)
$rangeArgs = @()
foreach ($ln in $lines) {
  $parts = ($ln -split '\s+')
  if ($parts.Count -ge 4) {
    $localSha = $parts[1]; $remoteSha = $parts[3]
    if ($remoteSha -match '^0+$') {
      $rangeArgs = @('--diff-filter=ACM', (git -C $root hash-object -t tree /dev/null 2>$null))
    }
    $rangeArgs = @('--diff-filter=ACM', ($remoteSha + '..' + $localSha))
  }
}
Push-Location $root
try {
  if ($rangeArgs.Count -eq 0) { exit 0 }
  $files = @(git diff --name-only @rangeArgs 2>$null | Where-Object { $_ -match '^knowledge/.+\.md$' })
  if ($files.Count -eq 0) { exit 0 }
  $py = (Get-Command python -ErrorAction SilentlyContinue).Source
  if (-not $py) { $py = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe' }
  & $py scripts/kb_hook.py --files @files --repo-root $root __STRICT__
  exit $LASTEXITCODE
} finally { Pop-Location }
'@
$body = $body.Replace('__STRICT__', $strictArg.Trim())
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
[System.IO.File]::WriteAllText($hook, $body, $utf8Bom)
git -C $Repo config core.hooksPath .githooks
Write-Host ("[install_kb_hook] 已装：{0}" -f $hook)
Write-Host ("[install_kb_hook] core.hooksPath = {0}（{1} 版）" -f (git -C $Repo config core.hooksPath), $(if ($Strict) { 'STRICT' } else { 'WARN' }))
Write-Host '[install_kb_hook] 校验：git push 时将自动跑 knowledge 条目分族体检'
