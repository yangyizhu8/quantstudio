$ErrorActionPreference = 'Stop'
$stamp  = Get-Date -Format 'yyyyMMddTHHmmss'
$root   = 'D:\qssyn_s0b_' + $stamp
$base   = (Resolve-Path '.\docs\evidence').Path
$outDir = Join-Path $base ('duckdb-s0b-' + $stamp)
$utf8    = New-Object System.Text.UTF8Encoding($false)
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
$notice  = (Resolve-Path '.\docs\handoff\customer-notice-5-duckdb-1.5.6-upgrade-v6-20261115.md').Path

$script:stages = New-Object System.Collections.ArrayList
$script:verdict = 'FAIL'
$script:failClass = ''
$script:failMsg = ''
$script:written = $false
function Mark([string]$n,[string]$rc) { $script:stages.Add(("{0}`t{1}" -f $n,$rc)) | Out-Null; "STAGE $n rc=$rc" }

"=== S0b(r3) 开始 stamp=$stamp ==="
"ROOT   = $root"
"OUTDIR = $outDir"

try {
    # ---- STAGE HOST：明确并锁定宿主与解释器（不临场更换）----
    $hostVer = $PSVersionTable.PSVersion.ToString()
    $hostExe = (Get-Process -Id $PID).Path
    "HOST-PS   = $hostVer"
    "HOST-EXE  = $hostExe"
    if (-not $hostVer.StartsWith('5.1')) { Mark 'HOST' "FAIL(ver=$hostVer 非 5.1)"; throw "宿主不是 PowerShell 5.1（实得 $hostVer），本轮不更换宿主，中止。" }
    $pyCmd = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $pyCmd) { Mark 'HOST' 'FAIL(no python)'; throw "PATH 上找不到 python，中止。" }
    $py = [System.IO.Path]::GetFullPath($pyCmd.Source)
    if (-not (Test-Path -LiteralPath $py -PathType Leaf)) { Mark 'HOST' 'FAIL(py not file)'; throw "解释器不是文件：$py" }
    if ([System.IO.Path]::GetFileName($py) -ne 'python.exe') { Mark 'HOST' 'FAIL(py name)'; throw "不叫 python.exe：$py" }
    $pyVer = ((& $py -c "import sys;print(sys.version.split()[0])" | Select-Object -First 1)).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $pyVer) { Mark 'HOST' 'FAIL(py run)'; throw "解释器无法运行：$py" }
    $pySha     = (Get-FileHash -LiteralPath $py     -Algorithm SHA256).Hash
    $noticeSha = (Get-FileHash -LiteralPath $notice -Algorithm SHA256).Hash
    "PY-EXE    = $py"
    "PY-VER    = $pyVer"
    "PY-SHA    = $pySha"
    "NOTICE-SHA= $noticeSha"
    Mark 'HOST' '0'

    # ---- STAGE SETUP-DIRS：排他新建根与输出目录 ----
    if (Test-Path -LiteralPath $root)   { Mark 'SETUP-DIRS' 'FAIL(root exists)'; throw "目标根已存在：$root" }
    if ($root -notmatch '^[A-Za-z]:\\') { Mark 'SETUP-DIRS' 'FAIL(form)';        throw "非盘根绝对形式：$root" }
    if (Test-Path -LiteralPath $outDir) { Mark 'SETUP-DIRS' 'FAIL(out exists)';  throw "输出目录已存在：$outDir" }
    New-Item -ItemType Directory -Path $outDir -ErrorAction Stop | Out-Null
    New-Item -ItemType Directory -Path $root   -ErrorAction Stop | Out-Null
    Mark 'SETUP-DIRS' '0'

    # ---- STAGE SETUP-TXT ----
    foreach ($n in 'a.txt','b.txt','c.txt') { [System.IO.File]::WriteAllText((Join-Path $root $n), 'x', $utf8) }
    $entries = @(Get-ChildItem -LiteralPath $root -Force)
    $subDirs = @($entries | Where-Object { $_.PSIsContainer })
    if ($entries.Count -ne 3) { Mark 'SETUP-TXT' "FAIL(entries=$($entries.Count))"; throw "条目数 $($entries.Count) != 3" }
    if ($subDirs.Count -ne 0) { Mark 'SETUP-TXT' "FAIL(subdirs=$($subDirs.Count))"; throw "有 $($subDirs.Count) 个子目录" }
    Mark 'SETUP-TXT' '0'

    # ---- STAGE EXTRACT-SCAN：原文提取扫描器 ----
    $L = Get-Content -LiteralPath $notice -Encoding UTF8
    $openTok  = '@' + "'"
    $closeTok = "'" + '@'
    $s0 = -1; $s1 = -1
    for ($k = 0; $k -lt $L.Count; $k++) {
        if ($s0 -lt 0 -and $L[$k].Trim() -eq ('$PY_SCAN = ' + $openTok)) { $s0 = $k; continue }
        if ($s0 -ge 0 -and $L[$k].Trim() -eq $closeTok) { $s1 = $k; break }
    }
    if ($s0 -lt 0 -or $s1 -le $s0) { Mark 'EXTRACT-SCAN' "FAIL(s0=$s0,s1=$s1)"; throw "未定位 scanner 边界" }
    $scanPy = Join-Path $outDir '_pyid_scan.py'
    [System.IO.File]::WriteAllText($scanPy, (($L[($s0+1)..($s1-1)]) -join "`r`n"), $utf8)
    "SCANNER = L$($s0+2)..L$($s1) ($($s1-$s0-1) 行)"
    Mark 'EXTRACT-SCAN' '0'

    # ---- STAGE SCAN-RUN：数组直喂 ----
    $scanOut = @(& $py $scanPy $root)
    $scanRc  = $LASTEXITCODE
    [System.IO.File]::WriteAllText((Join-Path $outDir 'scan.stdout.txt'), (($scanOut -join "`r`n") + "`r`n"), $utf8)
    [System.IO.File]::WriteAllText((Join-Path $outDir 'scan.rc.txt'), "$scanRc`r`n", $utf8)
    $scanOut | ForEach-Object { "  | $_" }
    Mark 'SCAN-RUN' "$scanRc"
    if ($scanRc -ne 0) { throw "扫描器 rc=$scanRc（预期 0）" }
    foreach ($kk in 'SCAN-LINKS','SCAN-DIRS','SCAN-ROOT-ENTRIES','SCAN-WALK-ERRORS','SCAN-LSTAT-ERRORS') {
        if (-not (($scanOut | Select-String ('^' + $kk + '\s')).Line)) { throw "扫描输出缺 $kk" }
    }
    function Get-Count([string]$nm) {
        $ln = ($scanOut | Select-String ('^' + $nm + '\s')).Line
        if (-not $ln) { throw "缺字段 $nm" }
        return ($ln -split '\s+')[1]
    }
    $vLinks = Get-Count 'SCAN-LINKS'
    $vDirs  = Get-Count 'SCAN-DIRS'
    $vRoof  = Get-Count 'SCAN-ROOT-ENTRIES'
    $vWerr  = Get-Count 'SCAN-WALK-ERRORS'
    $vLerr  = Get-Count 'SCAN-LSTAT-ERRORS'
    "COUNTS = LINKS=$vLinks DIRS=$vDirs ROOT_ENTRIES=$vRoof WERR=$vWerr LERR=$vLerr"

    # ---- STAGE EXTRACT-GATE：**仅此处改为带 BOM 写入** ----
    $g0 = -1; $g1 = -1
    for ($k = 0; $k -lt $L.Count; $k++) {
        if ($g0 -lt 0 -and $L[$k].Trim().StartsWith('if ($scanRc -eq 8)')) { $g0 = $k }
        if ($g0 -ge 0 -and $L[$k].Contains('PY-SRC-LINKS  = 0（')) { $g1 = $k; break }
    }
    if ($g0 -lt 0 -or $g1 -le $g0) { Mark 'EXTRACT-GATE' "FAIL(g0=$g0,g1=$g1)"; throw "未定位消费闸边界" }
    $gatePath = Join-Path $outDir '_gate_from_notice.ps1'
    [System.IO.File]::WriteAllText($gatePath, (($L[$g0..$g1]) -join "`r`n"), $utf8Bom)
    "GATE = L$($g0+1)..L$($g1+1)"
    Mark 'EXTRACT-GATE' '0'

    # ---- STAGE GATE-BOM：实测首 3 字节 ----
    $gb = [System.IO.File]::ReadAllBytes($gatePath)
    $bomOk = ($gb.Length -ge 3 -and $gb[0] -eq 0xEF -and $gb[1] -eq 0xBB -and $gb[2] -eq 0xBF)
    $bomHex = if ($gb.Length -ge 3) { '{0:X2} {1:X2} {2:X2}' -f $gb[0],$gb[1],$gb[2] } else { '<短>' }
    "GATE-BOM = $bomHex"
    if (-not $bomOk) { Mark 'GATE-BOM' "FAIL($bomHex)"; throw "PARSE-PRECHECK：无 EF BB BF（实得 $bomHex）" }
    Mark 'GATE-BOM' '0'

    # ---- STAGE GATE-PARSE：**当前宿主自身**的 Parser 读盘解析（同一 PS 5.1，不经命令行传中文路径）----
    $perr = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($gatePath, [ref]$null, [ref]$perr)
    if ($perr -and $perr.Count -gt 0) {
        $perr | ForEach-Object { "PARSE-ERR " + $_.Message }
        Mark 'GATE-PARSE' "FAIL($($perr.Count) err)"
        throw ("PARSE-PRECHECK：目标宿主静态解析失败（" + ($perr[0].Message) + "）")
    }
    "GATE-PARSE = OK（宿主 $hostVer 自身 Parser，读盘）"
    Mark 'GATE-PARSE' '0'

    # ---- STAGE GATE-BASELINE ----
    $gateOut = $null; $gateErr = $null
    try { $gateOut = (& { . $gatePath }) } catch { $gateErr = $_.Exception.Message }
    "GATE-BASELINE-OUT = $gateOut"
    if ($gateErr) { Mark 'GATE-BASELINE' 'FAIL(rejected)'; throw "LOGIC：消费闸拒绝了基线（误拦仍在）：$gateErr" }
    Mark 'GATE-BASELINE' '0'

    # ---- 五值精确比较 ----
    $fail = @()
    if ($vDirs  -ne '1') { $fail += "DIRS=$vDirs!=1" }
    if ($vRoof  -ne '3') { $fail += "ROOT_ENTRIES=$vRoof!=3" }
    if ($vLinks -ne '0') { $fail += "LINKS=$vLinks!=0" }
    if ($vWerr  -ne '0') { $fail += "WERR=$vWerr!=0" }
    if ($vLerr  -ne '0') { $fail += "LERR=$vLerr!=0" }
    if ($scanRc -ne 0)   { $fail += "scanRc=$scanRc!=0" }
    if ($gateOut -notmatch 'dirs=1') { $fail += "闸输出无 dirs=1" }
    if ($gateOut -notmatch 'root_entries=3') { $fail += "闸输出无 root_entries=3" }
    if ($fail.Count -gt 0) { Mark 'FIVE-COUNTS' "FAIL($($fail -join ';'))"; throw ("LOGIC：五值/闸判据不符：" + ($fail -join '; ')) }
    Mark 'FIVE-COUNTS' '0'

    # ---- STAGE NEGCONTROLS：显式绑定闸真正读的变量 ----
    $baselineOut = $scanOut
    $baselineRc  = $scanRc
    $cases = @(
      @{ n='WALK=1';  rc=0; from='SCAN-WALK-ERRORS 0';  to='SCAN-WALK-ERRORS 1';  expect='无法列出' }
      @{ n='LSTAT=1'; rc=0; from='SCAN-LSTAT-ERRORS 0'; to='SCAN-LSTAT-ERRORS 1'; expect='lstat 有 1 个失败' }
      @{ n='LINKS=1'; rc=0; from='SCAN-LINKS 0';        to='SCAN-LINKS 1';        expect='重解析点' }
      @{ n='DIRS=0';  rc=0; from='SCAN-DIRS 1';         to='SCAN-DIRS 0';         expect='未产出任何目录' }
      @{ n='RC=8';    rc=8; from=$null;                 to=$null;                 expect='根无效' }
      @{ n='RC=7';    rc=7; from=$null;                 to=$null;                 expect='扫描失败' }
    )
    $negLog = @(); $pass = 0
    foreach ($m in $cases) {
        if ($m.from) {
            $mut = @($baselineOut | ForEach-Object { if ($_ -eq $m.from) { $m.to } else { $_ } })
            if ($mut.Count -ne $baselineOut.Count) { Mark 'NEGCONTROLS' "FAIL($($m.n):行数变)"; throw "变异 $($m.n) 行数变了" }
            if (-not ($mut -contains $m.to)) { Mark 'NEGCONTROLS' "FAIL($($m.n):未替换)"; throw "变异 $($m.n) 未真正替换" }
        } else { $mut = $baselineOut }
        $scanOut = $mut; $scanRc = $m.rc
        $err = $null; $o = $null
        try { $o = (& { . $gatePath }) } catch { $err = $_.Exception.Message }
        $scanOut = $baselineOut; $scanRc = $baselineRc
        if (-not $err) { Mark 'NEGCONTROLS' "FAIL($($m.n):未拒)"; throw "鉴别力不足：$($m.n) 未被拒" }
        if ($err -notlike ('*' + $m.expect + '*')) { Mark 'NEGCONTROLS' "FAIL($($m.n):原因不符)"; throw "变异 $($m.n) 原因不符：期望含「$($m.expect)」，实得「$err」" }
        "NEGCASE $($m.n) => 按预期被拒 ($($m.expect))"
        $negLog += ("{0}`tOK`t{1}" -f $m.n, $err)
        $pass++
    }
    Mark 'NEGCONTROLS' "$pass/6"
    if ($pass -ne 6) { throw "负控通过数 $pass != 6" }

    # ---- STAGE RESTORE ----
    $err2 = $null
    try { $null = (& { . $gatePath }) } catch { $err2 = $_.Exception.Message }
    if ($err2) { Mark 'RESTORE' 'FAIL'; throw "还原失效：基线不再通过：$err2" }
    "RESTORE = 基线还原后仍通过"
    Mark 'RESTORE' '0'

    $script:verdict = 'PASS'
}
catch {
    $script:failMsg = $_.Exception.Message
    if (-not $script:failClass) {
        if ($script:failMsg -like '*PARSE*') { $script:failClass = 'PARSE' }
        elseif ($script:failMsg -like 'LOGIC*') { $script:failClass = 'LOGIC-REJECT' }
        else { $script:failClass = 'SETUP-OR-OTHER' }
    }
    "FAIL-CLASS = $($script:failClass)"
    "FAIL-MSG   = $($script:failMsg)"
}
finally {
    # ---- 任何失败也落 verdict ----
    try {
        if (Test-Path -LiteralPath $outDir) {
            $v = @(
              "stamp=$stamp"
              "root=$root"
              "outDir=$outDir"
              "notice=$notice"
              "notice_sha256=$noticeSha"
              "host_ps=$hostVer"
              "host_exe=$hostExe"
              "py_exe=$py"
              "py_ver=$pyVer"
              "py_sha256=$pySha"
              "scanRc=$scanRc"
              "SCAN-LINKS=$vLinks"
              "SCAN-DIRS=$vDirs"
              "SCAN-ROOT-ENTRIES=$vRoof"
              "SCAN-WALK-ERRORS=$vWerr"
              "SCAN-LSTAT-ERRORS=$vLerr"
              "gate_bom=$bomHex"
              "gate_parse=OK-OR-FAIL-SEE-STAGES"
              "gate_out=$gateOut"
              "negcontrols=$pass/6"
              "verdict=$($script:verdict)"
              "fail_class=$($script:failClass)"
              "fail_msg=$($script:failMsg)"
            ) -join "`r`n"
            [System.IO.File]::WriteAllText((Join-Path $outDir 's0b-verdict.txt'), $v + "`r`n", $utf8)
            [System.IO.File]::WriteAllText((Join-Path $outDir 's0b-stages.tsv'), ((($script:stages) -join "`r`n") + "`r`n"), $utf8)
            if ($negLog) { [System.IO.File]::WriteAllText((Join-Path $outDir 'negcontrols.tsv'), (($negLog -join "`r`n") + "`r`n"), $utf8) }
            if ($PSCommandPath -and (Test-Path -LiteralPath $PSCommandPath)) {
                Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $outDir '_runner.ps1') -Force
            }
            $hs = @()
            Get-ChildItem -LiteralPath $outDir -File | Sort-Object Name | ForEach-Object {
                $hs += ("{0}`t{1}`t{2}" -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash, $_.Length, $_.Name)
            }
            [System.IO.File]::WriteAllText((Join-Path $outDir 'hashes.tsv'), (($hs -join "`r`n") + "`r`n"), $utf8)
            "=== EVIDENCE HASHES ==="
            $hs | ForEach-Object { "  $_" }
            $script:written = $true
        }
    } catch { "VERDICT-WRITE-FAILED: $($_.Exception.Message)" }
    "=== S0b(r3) 结束：verdict=$($script:verdict) failClass=$($script:failClass) ==="
    "STAMP-FINAL=$stamp"
}
if ($script:verdict -ne 'PASS') { exit 1 } else { exit 0 }