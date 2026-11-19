function Assert-AbsoluteDrivePath([string]$p) {
    if ([string]::IsNullOrWhiteSpace($p)) { throw "路径为空，拒绝继续。" }
    if ($p -match '^[A-Za-z]:(?![\\/])') {
        throw "路径是驱动器相对形式（$p）：其含义取决于「该盘的当前目录」，不是固定位置。请改为全限定路径（如 D:\...）。"
    }
    if (-not [System.IO.Path]::IsPathRooted($p)) { throw "路径不是绝对路径（$p），拒绝继续。" }
    $full = [System.IO.Path]::GetFullPath($p)
    if ($full.StartsWith('\\')) { throw "UNC 路径不在本判据内（$full），拒绝继续。" }
    if ($full -notmatch '^[A-Za-z]:\\') { throw "规范化后仍非盘根绝对形式（$full），拒绝继续。" }
    return $full
}
function Test-ChainNoReparse([string]$p, [switch]$AllowMissingLeaf) {
    $full = Assert-AbsoluteDrivePath $p
    # 盘根必须**原样保留**（形如 D:\）。上一版写成 `$root.TrimEnd('\')`，
    # 对 `D:\` 会得到 `D:` —— 那不是绝对路径，是**驱动器相对**形式（含义取决于该盘当前目录），
    # 与本函数自己的形式闸自相矛盾。这里不做任何裁剪。
    $root = [System.IO.Path]::GetPathRoot($full)
    $segs = @($full.Substring($root.Length).Split('\') | Where-Object { $_ -ne '' })
    $cur = $root
    for ($i = 0; $i -lt $segs.Count; $i++) {
        $cur = Join-Path $cur $segs[$i]
        $isLeaf = ($i -eq $segs.Count - 1)
        if (-not (Test-Path -LiteralPath $cur)) {
            if ($isLeaf -and $AllowMissingLeaf) { return $cur }
            return $null
        }
        $it = Get-Item -LiteralPath $cur -Force
        if ($it.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "路径链上存在重解析点（junction/symlink）：$cur —— 拒绝继续（本配方要求全链无链接）。"
        }
    }
    # 出口再断言一次：结果必须仍是**盘根绝对形式**（防任何规范化把 D:\ 退化成 D:）。
    if ($cur -notmatch '^[A-Za-z]:\\') { throw "路径链检查结果为非盘根绝对形式（$cur），拒绝继续。" }
    return $cur
}
