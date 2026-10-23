<#
.SYNOPSIS
  kb-kit 幂等安装器：把 KB 闭环（体检/草稿器/钩子/模板/CI）装进任意 git 项目。

.DESCRIPTION
  四种用法：
    首次接入：  install.ps1 -Target <项目根>                # 拷 scripts + 建 knowledge 骨架 + 装 pre-push
    加装 CI：   install.ps1 -Target <项目根> -InstallCI     # 另拷 .github/workflows/kb-schema.yml
    升级副本：  install.ps1 -Target <项目根> -Upgrade       # 只覆盖 scripts/（不动知识库与钩子配置）
    维护者同步：install.ps1 -Sync -CanonicalRepo <主仓根>   # 从主仓覆盖 kb-kit/scripts 与 templates
  幂等：二次执行默认全部跳过；knowledge/ 与既有 pre-push 永不覆盖。

.PARAMETER Target
  目标项目根（须为 git 仓；非 git 仅告警继续）。

.PARAMETER Upgrade
  覆盖已存在的 scripts/ 文件（升级路径）。

.PARAMETER NoHook
  不装 pre-push（自管钩子的项目用）。

.PARAMETER InstallCI
  另装 .github/workflows/kb-schema.yml（已存在则跳过，-Upgrade 时覆盖）。

.PARAMETER Sync
  维护者模式：从 -CanonicalRepo 同步本包（kb-kit/scripts、kb-kit/templates）。

.PARAMETER CanonicalRepo
  主仓根（-Sync 必填）。

.EXAMPLE
  powershell -File kb-kit/install.ps1 -Target C:\vibe-coding-study\vibe-coding-cn
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Target,
  [string]$KitDir = '',        # 缺省由脚本自身位置推断（双回退：$PSScriptRoot 在 -File+非 ASCII 路径下可能为空）
  [switch]$Upgrade,
  [switch]$NoHook,
  [switch]$InstallCI,
  [switch]$Sync,
  [string]$CanonicalRepo = ''
)
$ErrorActionPreference = 'Stop'

# KitDir 推断（2026-10-23 M4 实测：PS 5.1 以 -File 调用且路径含中文时 $PSScriptRoot 可能为空 ⇒ 参数绑定空串报错）
if (-not $KitDir) { $KitDir = $PSScriptRoot }
if (-not $KitDir) { try { $KitDir = Split-Path -Parent $MyInvocation.MyCommand.Path } catch { } }
if (-not $KitDir -or -not (Test-Path (Join-Path $KitDir 'KIT_VERSION'))) {
  throw ('无法定位 kb-kit 目录（KIT_VERSION 不在 {0}）。请显式传 -KitDir <kb-kit 绝对路径>。' -f $KitDir)
}
$KitDir = (Resolve-Path $KitDir).Path
$ver = ((Get-Content (Join-Path $KitDir 'KIT_VERSION') -TotalCount 1) -as [string]).Trim()
Write-Host ("[kb-kit] v{0}  安装目标：{1}" -f $ver, $Target)

function Copy-Safe([string]$src, [string]$dst, [string]$label) {
  if ((-not $Upgrade) -and (Test-Path $dst)) { Write-Host ("  [skip] {0}（已存在；-Upgrade 可覆盖）" -f $label); return }
  $dir = Split-Path -Parent $dst
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  Copy-Item $src $dst -Force
  Write-Host ("  [ok]   {0}" -f $label)
}

# ---- 维护者模式：从主仓同步本包 ----
if ($Sync) {
  if (-not $CanonicalRepo) { throw '-Sync 需要 -CanonicalRepo <主仓根>' }
  $c = (Resolve-Path $CanonicalRepo).Path
  $pairs = @(
    @{ s = Join-Path $c 'scripts\kb_preflight.py'; d = Join-Path $KitDir 'scripts\kb_preflight.py' },
    @{ s = Join-Path $c 'scripts\kb_hook.py'; d = Join-Path $KitDir 'scripts\kb_hook.py' },
    @{ s = Join-Path $c 'scripts\kb_draft.py'; d = Join-Path $KitDir 'scripts\kb_draft.py' },
    @{ s = Join-Path $c 'scripts\install_kb_hook.ps1'; d = Join-Path $KitDir 'scripts\install_kb_hook.ps1' },
    @{ s = Join-Path $c 'docs\templates\kb-info-template.yaml'; d = Join-Path $KitDir 'templates\kb-info.yaml' },
    @{ s = Join-Path $c 'docs\templates\kb-family-readme-template.md'; d = Join-Path $KitDir 'templates\family-README.md' },
    @{ s = Join-Path $c 'docs\templates\kb-onboarding-checklist.md'; d = Join-Path $KitDir 'templates\onboarding-checklist.md' },
    @{ s = Join-Path $c '.github\workflows\kb-schema.yml'; d = Join-Path $KitDir 'templates\kb-schema.yml' }
  )
  foreach ($p in $pairs) {
    if (-not (Test-Path $p.s)) { Write-Host ("  [miss] 源缺失：{0}" -f $p.s); continue }
    Copy-Item $p.s $p.d -Force
    $h = (Get-FileHash $p.d -Algorithm SHA256).Hash.Substring(0, 12)
    Write-Host ("  [sync] {0}  sha256:{1}…" -f (Split-Path $p.d -Leaf), $h)
  }
  Write-Host '[kb-kit] 同步完成。请 bump KIT_VERSION 后提交。'
  exit 0
}

$Target = (Resolve-Path $Target).Path
if (-not (Test-Path (Join-Path $Target '.git'))) {
  Write-Host '[kb-kit][WARN] 目标不是 git 仓（无 .git）——pre-push 钩子将无效，仅拷脚本。'
}

# ---- 1) 脚本 ----
foreach ($f in @('kb_preflight.py', 'kb_hook.py', 'kb_draft.py', 'install_kb_hook.ps1')) {
  Copy-Safe (Join-Path $KitDir "scripts\$f") (Join-Path $Target "scripts\$f") "scripts/$f"
}

# ---- 2) knowledge 骨架（永不覆盖已有）----
$kbInfo = Join-Path $Target 'knowledge\kb-info.yaml'
if (Test-Path $kbInfo) {
  Write-Host '  [skip] knowledge/kb-info.yaml（已存在，不覆盖）'
} else {
  Copy-Safe (Join-Path $KitDir 'templates\kb-info.yaml') $kbInfo 'knowledge/kb-info.yaml（请填族名/枚举/前缀）'
}
foreach ($t in @(
    @{ s = 'family-README.md'; d = 'docs\kb-templates\family-README.md' },
    @{ s = 'onboarding-checklist.md'; d = 'docs\kb-templates\onboarding-checklist.md' },
    @{ s = 'kb-schema.yml'; d = 'docs\kb-templates\kb-schema.yml' })) {
  Copy-Safe (Join-Path $KitDir ("templates\" + $t.s)) (Join-Path $Target $t.d) $t.d
}

# ---- 3) pre-push 钩子 ----
if ($NoHook) {
  Write-Host '  [skip] pre-push（-NoHook）'
} elseif (-not (Test-Path (Join-Path $Target '.git'))) {
  Write-Host '  [skip] pre-push（非 git 仓）'
} else {
  & (Join-Path $KitDir 'scripts\install_kb_hook.ps1') -Repo $Target
}

# ---- 4) 可选 CI ----
if ($InstallCI) {
  $ci = Join-Path $Target '.github\workflows\kb-schema.yml'
  Copy-Safe (Join-Path $KitDir 'templates\kb-schema.yml') $ci '.github/workflows/kb-schema.yml（WARN 起步）'
}

Write-Host ''
Write-Host ("[kb-kit] 完成（v{0}）。下一步：" -f $ver)
Write-Host '  1) 编辑 knowledge/kb-info.yaml：填 families（族名/目录/id 前缀/枚举）'
Write-Host '  2) 按 docs/kb-templates/family-README.md 建族 README，用 kb_draft.py --kb 沉 3-5 条种子条目'
Write-Host '  3) 跑基线：从种子条目改写 10-20 问 → grep 记命中率'
