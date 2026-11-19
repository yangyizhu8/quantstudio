# =====================================================================
#  SHA-256 摘要值边界比较 —— 唯一实现（驱动与静态门共用，避免"测复制品"）
#  边界规则：① 先验 64 位 hex；② 仅在此边界内统一小写后比较。
#  **只用于摘要值**；其他字段（路径/版本/字段串/布尔）仍严格区分大小写。
# =====================================================================
function Test-DigestHex([string]$v) {
    if ($null -eq $v) { return $false }
    return ($v -match '^[0-9A-Fa-f]{64}$')
}
function Same-Digest([string]$a, [string]$b) {
    if (-not (Test-DigestHex $a)) { throw ('DIGEST-FORMAT: 左值非 64 位 hex -> [' + [string]$a + ']') }
    if (-not (Test-DigestHex $b)) { throw ('DIGEST-FORMAT: 右值非 64 位 hex -> [' + [string]$b + ']') }
    return ($a.ToLowerInvariant() -eq $b.ToLowerInvariant())
}
