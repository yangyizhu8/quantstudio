# =====================================================================
#  U27 / U32 / U28 小驱动（**静态候选件 · 本版未运行**）
#  抽取源：docs\handoff\customer-notice-5-duckdb-1.5.6-upgrade-v6-20261115.md
#  只抽三段（两个路径函数 + scanner 正文）；**不抄整份通知执行**
#  禁触：生产库 / junction / ACL / 环境复制 / 供血 / flag
#  运行一次即可，位置：docs\evidence\duckdb-v6-u27-u28-u32-run-<stamp>\
# =====================================================================
param(
    [string]$Py = "",
    [switch]$Keep
)
$ErrorActionPreference = 'Stop'
$script:written = $false
$script:stages  = New-Object System.Collections.ArrayList
$script:cases   = New-Object System.Collections.ArrayList

# ---------- 内容 pin（静态阶段已实测，硬门：不符即 FAIL，不降级） ----------
$PIN_F1   = '1DD4DC464640BCDBDB2AAB3F6E85992FB6FDFBCE31D3B276E8DC2F60B0DB234F'
$PIN_F2   = '8B725BA145C6D0EB9EB0796922F604FA9AF86C71272E34C9267FFA84D61C486B'
$PIN_SCAN = '53AC95D927359DB4AB876836E9C11B1DDA06EA4B89566480E368A858D9B34A16'
# 文档级 hash 只记录 + 告警（允许因无关章节编辑而变），不参与判定
$KNOWN_NOTICE = '288AB994FE9AB6FFB6F31862B49A2AC29727E047FBEC486E96EA6139E420A681'

$stamp = Get-Date -Format 'yyyyMMddTHHmmss'
$repo  = (Get-Item $PSScriptRoot).Parent.Parent.Parent.FullName
$notice = Join-Path $repo 'docs\handoff\customer-notice-5-duckdb-1.5.6-upgrade-v6-20261115.md'
$evDir  = Join-Path $repo ('docs\evidence\duckdb-v6-u27-u28-u32-run-' + $stamp)

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$utf8Bom   = New-Object System.Text.UTF8Encoding($true)

function Write-Text([string]$path, [string]$text, [bool]$bom) {
    $enc = if ($bom) { $utf8Bom } else { $utf8NoBom }
    [System.IO.File]::WriteAllText($path, $text, $enc)
}
function Get-BytesSha([byte[]]$b) {
    $h = [System.Security.Cryptography.SHA256]::Create()
    try { return ([System.BitConverter]::ToString($h.ComputeHash($b)) -replace '-','') } finally { $h.Dispose() }
}
function Add-Stage([string]$name, [int]$rc, [string]$note) {
    $script:stages.Add([pscustomobject]@{ Name = $name; Rc = $rc; Note = $note }) | Out-Null
    if ($rc -ne 0) { throw ("STAGE-FAIL " + $name + " rc=" + $rc + " " + $note) }
}
function Add-Case([string]$id, [string]$item, [string]$arg, [string]$expect, [string]$got) {
    $pass = ($expect -ceq $got)
    $script:cases.Add([pscustomobject]@{ ID=$id; Item=$item; Arg=$arg; Expect=$expect; Got=$got; Pass=$pass }) | Out-Null
    return $pass
}
function Stage-Fails([string]$stageName) {
    $bad = @($script:cases | Where-Object { $_.Item -eq $stageName -and (-not $_.Pass) })
    if ($bad.Count -gt 0) {
        $msg = ($bad | ForEach-Object { $_.ID + ': 期望[' + $_.Expect + '] 实际[' + $_.Got + ']' }) -join ' | '
        throw ('CASE-FAIL ' + $stageName + ' n=' + $bad.Count + ' :: ' + $msg)
    }
}

try {
    # ---------------- STAGE 1 BOOTSTRAP（必须在一切之前：否则 finally 无目录可写） ----------------
    if (-not (Test-Path -LiteralPath (Split-Path -Parent $evDir))) { throw 'BOOTSTRAP: evidence 目录不存在' }
    New-Item -ItemType Directory -Path $evDir -Force | Out-Null
    Add-Stage 'BOOTSTRAP' 0 ('evDir=' + $evDir)

    # ---------------- STAGE 2 NOTICE ----------------
    if (-not (Test-Path -LiteralPath $notice)) { throw ('NOTICE: 通知不存在 ' + $notice) }
    $raw = [System.IO.File]::ReadAllBytes($notice)
    $noticeSha = Get-BytesSha $raw
    $noticeWarn = ''
    if ($noticeSha -ne $KNOWN_NOTICE) { $noticeWarn = 'NOTICE-SHA-DRIFT（文档级，仅告警；内容级由 EXTRACT pin 把关）' }
    # 行索引：保留每行含 \n 的原始字节区间，保证切片逐字节还原
    $idx = New-Object System.Collections.ArrayList
    $s = 0
    for ($i = 0; $i -lt $raw.Length; $i++) {
        if ($raw[$i] -eq 10) { $idx.Add([pscustomobject]@{ S = $s; N = ($i - $s + 1) }) | Out-Null; $s = $i + 1 }
    }
    if ($s -lt $raw.Length) { $idx.Add([pscustomobject]@{ S = $s; N = ($raw.Length - $s) }) | Out-Null }
    Add-Stage 'NOTICE' 0 ('bytes=' + $raw.Length + ' lines=' + $idx.Count + ' sha=' + $noticeSha + ' ' + $noticeWarn)

    # ---------------- STAGE 3 EXTRACT（边界 token 定位，**不用行号**） ----------------
    $enc = [System.Text.Encoding]::UTF8
    function Line-Text([int]$k) {
        $L = $idx[$k]
        return $enc.GetString($raw, $L.S, $L.N).TrimEnd([char]10)
    }
    function Slice-Bytes([int]$a, [int]$b) {
        $st = $idx[$a].S
        $last = $idx[$b]
        $n = ($last.S + $last.N) - $st
        $out = New-Object 'System.Byte[]' $n
        [System.Array]::Copy($raw, $st, $out, 0, $n)
        return ,$out
    }
    function Find-Range([string]$startPat, [string]$endExact) {
        for ($i = 0; $i -lt $idx.Count; $i++) {
            if ((Line-Text $i) -match $startPat) {
                for ($j = $i + 1; $j -lt $idx.Count; $j++) {
                    if ((Line-Text $j) -ceq $endExact) { return @($i, $j) }
                }
                throw ('EXTRACT: 起始命中但无结束行 ' + $endExact + ' @' + $startPat)
            }
        }
        throw ('EXTRACT: 未找到起始行 ' + $startPat)
    }
    $r1 = Find-Range '^function Assert-AbsoluteDrivePath\(' '}'
    $r2 = Find-Range '^function Test-ChainNoReparse\(' '}'
    $r3 = Find-Range ('^\$PY_SCAN = @' + "'") ("'" + '@')
    $bF1   = Slice-Bytes $r1[0] $r1[1]
    $bF2   = Slice-Bytes $r2[0] $r2[1]
    $bScan = Slice-Bytes ($r3[0] + 1) ($r3[1] - 1)     # 正文：两围栏之间
    $sF1 = Get-BytesSha $bF1; $sF2 = Get-BytesSha $bF2; $sScan = Get-BytesSha $bScan
    if ($sF1 -ne $PIN_F1)     { throw ('EXTRACT-PIN-F1 漂移 got=' + $sF1) }
    if ($sF2 -ne $PIN_F2)     { throw ('EXTRACT-PIN-F2 漂移 got=' + $sF2) }
    if ($sScan -ne $PIN_SCAN) { throw ('EXTRACT-PIN-SCAN 漂移 got=' + $sScan) }
    Add-Stage 'EXTRACT' 0 ('F1 L' + ($r1[0]+1) + '..L' + ($r1[1]+1) + ' ' + $bF1.Length + 'B; F2 L' + ($r2[0]+1) + '..L' + ($r2[1]+1) + ' ' + $bF2.Length + 'B; SCAN L' + ($r3[0]+2) + '..L' + $r3[1] + ' ' + $bScan.Length + 'B')

    # ---------------- STAGE 4 EXTRACT-WRITE ----------------
    $pFuncs = Join-Path $evDir '_pathfuncs.ps1'
    $bom = [byte[]](0xEF, 0xBB, 0xBF)
    [byte[]]$funcsWithBom = $bom + $bF1 + $bF2
    [System.IO.File]::WriteAllBytes($pFuncs, $funcsWithBom)     # ★ PS 5.1 读中文 .ps1 必须带 BOM（r2 实测）
    $pScan = Join-Path $evDir '_pyid_scan.py'
    [System.IO.File]::WriteAllBytes($pScan, $bScan)             # ★ 无 BOM，字节原样（LF 保留）
    Add-Stage 'EXTRACT-WRITE' 0 ('funcs=' + $funcsWithBom.Length + 'B(bom) scan=' + $bScan.Length + 'B')

    # ---------------- STAGE 5 PARSE（静态语法校验，非执行） ----------------
    $tokens = $null; $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($pFuncs, [ref]$tokens, [ref]$errors)
    Add-Stage 'PARSE' ([int]@($errors).Count) ('errors=' + @($errors).Count)
    . $pFuncs

    function Try-Call([scriptblock]$sb) {
        $o = [pscustomobject]@{ Threw = $false; Msg = ''; Val = $null }
        try { $o.Val = & $sb } catch { $o.Threw = $true; $o.Msg = $_.Exception.Message }
        return $o
    }

    # ---------------- STAGE 6 C1 · U27 拒驱动器相对路径 ----------------
    $own = 'C1-U27'
    $c = Try-Call { Assert-AbsoluteDrivePath 'D:\qssyn\data' }
    [void](Add-Case 'C1-ACCEPT-FULL' $own 'D:\qssyn\data' ('OK|' + 'D:\qssyn\data') (('{0}|{1}' -f $(if($c.Threw){'THREW'}else{'OK'}), $c.Val)))
    $c = Try-Call { Assert-AbsoluteDrivePath 'D:qssyn\data' }
    [void](Add-Case 'C1-REJ-DRIVREL' $own 'D:qssyn\data' 'THREW|驱动器相对' (('{0}|{1}' -f $(if($c.Threw){'THREW'}else{'OK'}), $(if($c.Msg -like '*驱动器相对形式*'){'驱动器相对'}else{$c.Msg}))))
    $c = Try-Call { Assert-AbsoluteDrivePath 'D:' }
    [void](Add-Case 'C1-REJ-DRIVREL-BARE' $own 'D:' 'THREW|驱动器相对' (('{0}|{1}' -f $(if($c.Threw){'THREW'}else{'OK'}), $(if($c.Msg -like '*驱动器相对形式*'){'驱动器相对'}else{$c.Msg}))))
    $c = Try-Call { Assert-AbsoluteDrivePath 'qssyn\data' }
    [void](Add-Case 'C1-REJ-NONABS' $own 'qssyn\data' 'THREW|不是绝对路径' (('{0}|{1}' -f $(if($c.Threw){'THREW'}else{'OK'}), $(if($c.Msg -like '*不是绝对路径*'){'不是绝对路径'}else{$c.Msg}))))
    $c = Try-Call { Assert-AbsoluteDrivePath '\\server\share\x' }
    [void](Add-Case 'C1-REJ-UNC' $own '\\server\share\x' 'THREW|UNC' (('{0}|{1}' -f $(if($c.Threw){'THREW'}else{'OK'}), $(if($c.Msg -like '*UNC 路径不在本判据内*'){'UNC'}else{$c.Msg}))))
    Stage-Fails $own
    Add-Stage 'C1-U27' 0 '5/5'

    # ---------------- STAGE 7 C2 · U32 保留盘根 ----------------
    $own = 'C2-U32'
    $c = Try-Call { Test-ChainNoReparse 'D:\' }
    [void](Add-Case 'C2-ROOT-KEEP' $own 'D:\' 'OK|D:\' (('{0}|{1}' -f $(if($c.Threw){'THREW'}else{'OK'}), $c.Val)))
    $c = Try-Call { Test-ChainNoReparse 'D:/' }
    [void](Add-Case 'C2-ROOT-FWDSLASH' $own 'D:/' 'OK|D:\' (('{0}|{1}' -f $(if($c.Threw){'THREW'}else{'OK'}), $c.Val)))
    # 反向控制：证明「旧写法会退化」是真的 ⇒ 上面的断言不是空洞断言
    $old = 'D:\'.TrimEnd('\')
    [void](Add-Case 'C2-OLD-FORM-CTRL' $own ('[D:\].TrimEnd(\)') 'D:' $old)
    Stage-Fails $own
    Add-Stage 'C2-U32' 0 '3/3'

    # ---------------- STAGE 8 C3 · U28 真实扫描不存在根 → exit 8 ----------------
    $own = 'C3-U28'
    $pyExe = $Py
    if (-not $pyExe) {
        $gc = Get-Command python -ErrorAction SilentlyContinue
        if ($gc) { $pyExe = $gc.Source }
    }
    if (-not $pyExe) { throw 'HOST: 未找到 python 解释器' }
    $pyVer = & $pyExe -c "import sys;print(sys.version.split()[0])"
    $pyVerRc = $LASTEXITCODE
    if ($pyVerRc -ne 0) { throw ('HOST: 解释器无法运行 rc=' + $pyVerRc) }
    $pySha = (Get-FileHash -LiteralPath $pyExe -Algorithm SHA256).Hash

    $bad = 'D:\qssyn_nope_' + $stamp
    $guard = 0
    while ((Test-Path -LiteralPath $bad) -and $guard -lt 5) { $bad = $bad + 'x'; $guard++ }
    if (Test-Path -LiteralPath $bad) { throw ('C3: 无法取得不存在的根 ' + $bad) }

    $outBad = & $pyExe $pScan $bad      # 不接管道，避免 Select-Object 提前关管道导致假 rc（r3 实测 -1）
    $rcBad = $LASTEXITCODE
    $txtBad = ($outBad | Out-String)
    [void](Add-Case 'C3-BADROOT-RC' $own ($bad + ' → rc') '8' ([string]$rcBad))
    [void](Add-Case 'C3-BADROOT-MSG' $own ($bad + ' → stdout') 'HAS-SCAN-ROOT-INVALID' $(if($txtBad -like '*SCAN-ROOT-INVALID*'){'HAS-SCAN-ROOT-INVALID'}else{'MISSING'}))
    [void](Add-Case 'C3-BADROOT-NO-COUNTS' $own '不得出现 SCAN-LINKS' 'ABSENT' $(if($txtBad -like '*SCAN-LINKS*'){'PRESENT'}else{'ABSENT'}))

    # 正向控制：同一扫描器对**有效根**必须 rc=0 且不报 ROOT-INVALID ⇒ 证明 exit 8 不是无差别拒绝
    $outOk = & $pyExe $pScan $evDir
    $rcOk = $LASTEXITCODE
    $txtOk = ($outOk | Out-String)
    [void](Add-Case 'C3-CTRL-RC' $own 'evDir → rc' '0' ([string]$rcOk))
    [void](Add-Case 'C3-CTRL-ROOTENTRIES' $own 'evDir → stdout' 'HAS-SCAN-ROOT-ENTRIES' $(if($txtOk -like '*SCAN-ROOT-ENTRIES*'){'HAS-SCAN-ROOT-ENTRIES'}else{'MISSING'}))
    [void](Add-Case 'C3-CTRL-NO-INVALID' $own '不得报 ROOT-INVALID' 'ABSENT' $(if($txtOk -like '*SCAN-ROOT-INVALID*'){'PRESENT'}else{'ABSENT'}))
    Stage-Fails $own
    Add-Stage 'C3-U28' 0 '6/6'

    # ---------------- STAGE 9 HASHES（证据自证：记录每个落盘件真实 hash） ----------------
    $hashRows = New-Object System.Collections.ArrayList
    foreach ($f in @($pFuncs, $pScan)) {
        $bb = [System.IO.File]::ReadAllBytes($f)
        $hashRows.Add(([pscustomobject]@{ File = (Split-Path $f -Leaf); Bytes = $bb.Length; Sha256 = (Get-BytesSha $bb) })) | Out-Null
    }
    Add-Stage 'HASHES' 0 ($hashRows.Count.ToString() + ' files')

    # ---------------- STAGE 10 VERDICT ----------------
    $passCount = @($script:cases | Where-Object { $_.Pass }).Count
    $total     = @($script:cases).Count
    $verdict   = if ($passCount -eq $total) { 'PASS' } else { 'FAIL' }
    $lines = New-Object System.Collections.ArrayList
    $lines.Add('# U27 / U32 / U28 小驱动 · 运行回执') | Out-Null
    $lines.Add('') | Out-Null
    $lines.Add('stamp            = ' + $stamp) | Out-Null
    $lines.Add('evDir            = ' + $evDir) | Out-Null
    $lines.Add('notice           = ' + $notice) | Out-Null
    $lines.Add('notice_sha256    = ' + $noticeSha) | Out-Null
    $lines.Add('notice_sha_note  = ' + $(if($noticeWarn){$noticeWarn}else{'MATCHES-KNOWN'})) | Out-Null
    $lines.Add('host_ps          = ' + $PSVersionTable.PSVersion.ToString()) | Out-Null
    $lines.Add('py_exe           = ' + $pyExe) | Out-Null
    $lines.Add('py_ver           = ' + ($pyVer -join ',')) | Out-Null
    $lines.Add('py_sha256        = ' + $pySha) | Out-Null
    $lines.Add('pin_F1           = ' + $PIN_F1) | Out-Null
    $lines.Add('pin_F2           = ' + $PIN_F2) | Out-Null
    $lines.Add('pin_SCAN         = ' + $PIN_SCAN) | Out-Null
    $lines.Add('cases_pass       = ' + $passCount + '/' + $total) | Out-Null
    $lines.Add('verdict          = ' + $verdict) | Out-Null
    $lines.Add('') | Out-Null
    $lines.Add('## 用例逐条（expect / got）') | Out-Null
    foreach ($k in $script:cases) {
        $lines.Add(('{0,-20} {1,-6} {2,-9} expect={3} got={4}' -f $k.ID, $k.Item, $(if($k.Pass){'PASS'}else{'FAIL'}), $k.Expect, $k.Got)) | Out-Null
    }
    Write-Text (Join-Path $evDir 'verdict.txt') (($lines -join "`n") + "`n") $false

    $st = New-Object System.Collections.ArrayList
    $st.Add("name`trc`tnote") | Out-Null
    foreach ($x in $script:stages) { $st.Add(($x.Name + "`t" + $x.Rc + "`t" + $x.Note)) | Out-Null }
    Write-Text (Join-Path $evDir 'stages.tsv') (($st -join "`n") + "`n") $false

    $ct = New-Object System.Collections.ArrayList
    $ct.Add("id`titem`targ`texpect`tgot`tpass") | Out-Null
    foreach ($x in $script:cases) { $ct.Add(($x.ID + "`t" + $x.Item + "`t" + $x.Arg + "`t" + $x.Expect + "`t" + $x.Got + "`t" + $(if($x.Pass){'Y'}else{'N'}))) | Out-Null }
    Write-Text (Join-Path $evDir 'cases.tsv') (($ct -join "`n") + "`n") $false

    $ht = New-Object System.Collections.ArrayList
    $ht.Add("file`tbytes`tsha256") | Out-Null
    foreach ($x in $hashRows) { $ht.Add(($x.File + "`t" + $x.Bytes + "`t" + $x.Sha256)) | Out-Null }
    Write-Text (Join-Path $evDir 'hashes.tsv') (($ht -join "`n") + "`n") $false

    $script:written = $true
    if ($verdict -ne 'PASS') { exit 1 }
    exit 0
}
catch {
    $msg = $_.Exception.Message
    if (Test-Path -LiteralPath $evDir) {
        $f = Join-Path $evDir 'verdict.txt'
        $body = "verdict          = FAIL`nfail_class       = " + $(if($msg -like 'EXTRACT*'){'EXTRACT'}elseif($msg -like 'STAGE-FAIL*'){'STAGE'}elseif($msg -like 'CASE-FAIL*'){'CASE'}elseif($msg -like 'HOST*'){'HOST'}elseif($msg -like 'BOOTSTRAP*'){'BOOTSTRAP'}else{'OTHER'}) + "`nfail_msg         = " + $msg + "`nstages_completed = " + $script:stages.Count + "`n"
        Write-Text $f $body $false
        $script:written = $true
    }
    [Console]::Error.WriteLine($msg)
    if (-not $script:written) { exit 2 }   # ★ 证据写不出去时必须显式退出（r3 实测曾有静默跳过）
    exit 1
}
