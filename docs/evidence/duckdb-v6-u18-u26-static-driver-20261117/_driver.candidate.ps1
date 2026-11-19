# =====================================================================
#  U18 / U26 完整静态驱动（**候选件 · 本轮不执行**）
#  抽取源：docs\handoff\customer-notice-5-duckdb-1.5.6-upgrade-v6-20261115.md
#  U18 = 真实 metadata 在「中文＋空格目录」路径上往返（路径面）
#  ENC = 独立合成编码控制（内容面，明确含中文；不冒充环境 identity）
#  U26 = 留存比较链路（8a/8b/8c）；隔离模式仅用于试支路，**不代表生产可绕过校验**
#  禁触：生产 data 目录 / 62GB 库 / pykeep / site-packages / ACL / junction / 真实环境复制
# =====================================================================
param(
    [string]$Py = "",
    [switch]$KeepFailedScene,
    [string]$Scope = 'ALL',
    [string]$FixedPin = ''
)
if (-not (@('ALL','ENC+U26','PROBE','EQATTR','NOTICE') -contains $Scope)) { throw ('PARAM: 未知 Scope -> ' + $Scope) }
$runU18 = ($Scope -eq 'ALL')
$runNeg = ($Scope -eq 'ALL')
$runProbe = ($Scope -eq 'PROBE')
$runEnc    = @('ALL','ENC+U26') -contains $Scope
$runU26    = @('ALL','ENC+U26','EQATTR') -contains $Scope
$runEqOnly = ($Scope -eq 'EQATTR')
$runNoticeOnly = ($Scope -eq 'NOTICE')
$runFix    = @('ALL','ENC+U26','EQATTR') -contains $Scope
$ErrorActionPreference = 'Stop'
$script:written = $false
$script:stages  = New-Object System.Collections.ArrayList
$script:cases   = New-Object System.Collections.ArrayList
$script:hashRows = New-Object System.Collections.ArrayList
$script:pyCalls = 0
$script:neg     = New-Object System.Collections.ArrayList

# ---------- 内容 pin（硬门） ----------
$PIN_F1      = '1DD4DC464640BCDBDB2AAB3F6E85992FB6FDFBCE31D3B276E8DC2F60B0DB234F'   # Assert-AbsoluteDrivePath
$PIN_F2      = '8B725BA145C6D0EB9EB0796922F604FA9AF86C71272E34C9267FFA84D61C486B'   # Test-ChainNoReparse
$PIN_COLLECT = '223231A658589E11C1449D465449C7FF62DA07120F35697AA622D710A56DD945'   # PY_COLLECT
$PIN_RT      = 'C0E57F7C8A47C3FF38D0FB7640F16CE3DD5B365BB4AD8C30EE7D5B828D080A8F'   # PY_RT
$PIN_PARSERS = '9545FF90F59F8D7C3E8449051FD5BC14C9B4A2561EA9FC8448E8DC4BB62C82EB'   # 8a
$PIN_EQATTR  = '18AF80E87BB2B67130F7532EA1EE8ECEBF3EA6DDB529CD8A77894F2F09CE6CBE'   # 8b+8c
$PIN_SYNTH   = 'D7A906EAD538DEBEDB0FDAB8E54B5F13C841AF61D4F53D4AA7C9392E8E90739F'   # _synth.template.py（驱动同目录，非通知抽取）
$PIN_DIGEST  = '158746396792143BEE1A5A2DCEA36C0489A49DADED8BEFEAB9A2C8E110392EFB'   # _digestcmp.ps1（摘要边界唯一实现，负控与驱动共用）
$KNOWN_NOTICE_HIST = '96773A27D1D08BFAE64149A2D987871B0382D74BD7B0A2735B913C95DC1DDE8F'   # 史前版：新增 §2.4.12 + §六不作承诺后（2026-11-17 r15–r21）；598741EF 已移出三槽链（台账见 §7.43–§7.67）
$KNOWN_NOTICE_PREV = '771D20A488CD61748F0D77FC46319AFB64F57D68104CEE2A46BDB6CE022249D2'   # 上一版：L1726 后补「回退 1.4.5 属已过期上游支持」（2026-11-18 r22）
$KNOWN_NOTICE = '5C60A4DDA2F0E213DA367716782E0D28E0302C3DDF89E1D422051697E85143B3'   # 当前：按 ZCode 终审 r24 补粗体（「版本门仍放行、功能可用」），文字未改（2026-11-18 r24）
$S0B_PY_SHA  = '5F7B89A612C9B8AF1D6456CDFCD1DBE5CA630849E79AEBCED9BEE9A6694952EC'
$SIX_FIELDS  = 'duckdb,duckdb_file,exe,prefix,python,source_id'
$FIXED_PIN_SHA = '1363117B1FCE18D86954E20F49A3F2B42C54EA671331C09E7C60A3900523B160'   # r10 已核 pin 字节哈希（= 其 FILE-HEX = 其 META-HEX）
$PIN_PROBE   = 'CAF23AC78414C1BF8036EDF8BDF7099C57A67CBE803D68CFB2E891BB41D80526'   # _argvprobe.py（临时 echo-argv probe）

# 预登记全部判决断言（分母以此为准；未跑即 NOT-REACHED）
$ALL_PLAN = @(
    @{Id='U18-PATH-HAS-CJK-SPACE'}, @{Id='U18-COLLECT-RC'},
    @{Id='U18-FILE-EQ-META'}, @{Id='U18-PFX-EQ'}, @{Id='U18-FIELDS-EXACT'},
    @{Id='U18-PREFIX-ROUNDTRIP'}, @{Id='U18-PS-RECOMPUTE-EQ'},
    @{Id='ENC-ASCII-CONTENT-REJECT'}, @{Id='ENC-UTF8-OK-BYTES'},
    @{Id='ENC-ENSUREASCII-FILE-DIFF'}, @{Id='ENC-VALUE-PRESERVED'},
    @{Id='ENC-ASCII-CONTENT-ON-CJK-PATH-OK'},
    @{Id='U26-POS-TRIPLE'}, @{Id='U26-NEG-FILE'}, @{Id='U26-NEG-PFX-ISO'},
    @{Id='U26-NEG-FIELDS-ISO'}, @{Id='U26-EQ-TRIO-NEG'}, @{Id='U26-EQ-TRIO-POS'},
    @{Id='U26-ATTR-POS'}, @{Id='U26-ATTR-NEG'}
)

$script:plan = if ($Scope -eq 'PROBE' -or $Scope -eq 'NOTICE') { @() } elseif ($Scope -eq 'EQATTR') { @($ALL_PLAN | Where-Object { $_.Id -like 'U26-EQ-TRIO-*' -or $_.Id -like 'U26-ATTR-*' }) } else { @($ALL_PLAN | Where-Object { $Scope -eq 'ALL' -or ($_.Id -like 'ENC-*') -or ($_.Id -like 'U26-*') }) }
$stamp      = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmss') + 'Z'   # r10：排他根用 UTC
$stampLocal = Get-Date -Format 'yyyyMMddTHHmmss'
$repo  = (Get-Item $PSScriptRoot).Parent.Parent.Parent.FullName
$notice = Join-Path $repo 'docs\handoff\customer-notice-5-duckdb-1.5.6-upgrade-v6-20261115.md'
$evBase = Join-Path $repo ('docs\evidence\duckdb-v6-u18-u26-run-' + $stamp)
$evDir  = $evBase

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$utf8Bom   = New-Object System.Text.UTF8Encoding($true)

$hostVer=''; $hostExe=''; $pyExe=''; $pyVer=''; $pySha=''; $pyIdent='UNKNOWN'
$noticeSha=''; $noticeWarn=''; $prefixAscii='UNKNOWN'

function Write-Text([string]$path, [string]$text, [bool]$bom) {
    $enc = if ($bom) { $utf8Bom } else { $utf8NoBom }
    [System.IO.File]::WriteAllText($path, $text, $enc)
}
function Write-Bytes([string]$path, [byte[]]$b) { [System.IO.File]::WriteAllBytes($path, $b) }
function Get-BytesSha([byte[]]$b) {
    $h = [System.Security.Cryptography.SHA256]::Create()
    try { return ([System.BitConverter]::ToString($h.ComputeHash($b)) -replace '-','') } finally { $h.Dispose() }
}
function Add-Stage([string]$name, [int]$rc, [string]$note) {
    $script:stages.Add([pscustomobject]@{ Name=$name; Rc=$rc; Note=$note }) | Out-Null
    if ($rc -ne 0) { throw ('STAGE-FAIL ' + $name + ' rc=' + $rc + ' ' + $note) }
}
function Add-Case([string]$id, [string]$grp, [string]$arg, [string]$expect, [string]$got) {
    $pass = ($expect -ceq $got)
    $script:cases.Add([pscustomobject]@{ ID=$id; Item=$grp; Arg=$arg; Expect=$expect; Got=$got; Cmp='EXACT'; Pass=$pass }) | Out-Null
    return $pass
}
function Add-CaseDigest([string]$id, [string]$grp, [string]$arg, [string]$expect, [string]$got) {
    # 摘要值边界：先验 64 位 hex，再统一小写比较；原显示值原样入表
    $pass = Same-Digest $expect $got
    $script:cases.Add([pscustomobject]@{ ID=$id; Item=$grp; Arg=$arg; Expect=$expect; Got=$got; Cmp='DIGEST-NORM'; Pass=$pass }) | Out-Null
    return $pass
}
function Get-AllCases {
    $done = @{}; foreach ($k in $script:cases) { $done[$k.ID] = $true }
    $out = New-Object System.Collections.ArrayList
    foreach ($k in $script:cases) { $out.Add($k) | Out-Null }
    foreach ($pl in $script:plan) {
        if (-not $done.ContainsKey($pl.Id)) {
            $out.Add([pscustomobject]@{ ID=$pl.Id; Item='(未达)'; Arg='-'; Expect='(预登记)'; Got='NOT-REACHED'; Pass=$false }) | Out-Null
        }
    }
    return ,$out
}
function Save-Evidence([string]$verdict, [string]$failInfo) {
    if (-not (Test-Path -LiteralPath $evDir)) { return $false }
    $all = Get-AllCases
    $st = New-Object System.Collections.ArrayList
    $st.Add("name`trc`tnote") | Out-Null
    foreach ($x in $script:stages) { $st.Add(([string]$x.Name + "`t" + [string]$x.Rc + "`t" + [string]$x.Note)) | Out-Null }
    Write-Text (Join-Path $evDir 'stages.tsv') (($st -join "`n") + "`n") $false
    $ct = New-Object System.Collections.ArrayList
    $ct.Add("id`tgroup`targ`texpect`tgot`tcmp`tpass") | Out-Null
    foreach ($x in $all) { $ct.Add(([string]$x.ID + "`t" + [string]$x.Item + "`t" + [string]$x.Arg + "`t" + [string]$x.Expect + "`t" + [string]$x.Got + "`t" + [string]$x.Cmp + "`t" + $(if($x.Pass){'Y'}else{'N'}))) | Out-Null }
    Write-Text (Join-Path $evDir 'cases.tsv') (($ct -join "`n") + "`n") $false
    $ht = New-Object System.Collections.ArrayList
    $ht.Add("file`tbytes`tsha256") | Out-Null
    foreach ($x in $script:hashRows) { $ht.Add(([string]$x.File + "`t" + [string]$x.Bytes + "`t" + [string]$x.Sha256)) | Out-Null }
    Write-Text (Join-Path $evDir 'hashes.tsv') (($ht -join "`n") + "`n") $false
    $nt2 = New-Object System.Collections.ArrayList
    $nt2.Add("id`texpect`tgot`tpass") | Out-Null
    foreach ($x in $script:neg) { $nt2.Add(([string]$x.ID + "`t" + [string]$x.Expect + "`t" + [string]$x.Got + "`t" + $(if($x.Pass){'Y'}else{'N'}))) | Out-Null }
    Write-Text (Join-Path $evDir 'negcontrols.tsv') (($nt2 -join "`n") + "`n") $false
    $passN = @($all | Where-Object { $_.Pass }).Count
    $L = New-Object System.Collections.ArrayList
    $L.Add('# U18 / ENC / U26 完整驱动 · 运行回执') | Out-Null
    $L.Add('') | Out-Null
    $L.Add('stamp            = ' + [string]$stamp) | Out-Null
    $L.Add('evDir            = ' + [string]$evDir) | Out-Null
    $L.Add('notice_sha256    = ' + [string]$noticeSha) | Out-Null
    $L.Add('notice_sha_prev  = ' + [string]$KNOWN_NOTICE_PREV) | Out-Null
    $L.Add('notice_sha_note  = ' + $(if($noticeWarn){[string]$noticeWarn}else{'MATCHES-KNOWN'})) | Out-Null
    $L.Add('host_ps          = ' + [string]$hostVer) | Out-Null
    $L.Add('host_exe         = ' + [string]$hostExe) | Out-Null
    $L.Add('py_exe           = ' + [string]$pyExe) | Out-Null
    $L.Add('py_ver           = ' + [string]$pyVer) | Out-Null
    $L.Add('py_sha256        = ' + [string]$pySha) | Out-Null
    $L.Add('py_identity      = ' + [string]$pyIdent) | Out-Null
    $L.Add('real_prefix_ascii= ' + [string]$prefixAscii + '   # Y=真实 sys.prefix 纯 ASCII ⇒ A 组只证路径面，不证内容编码') | Out-Null
    $L.Add('pin_F1/F2        = ' + $PIN_F1 + ' / ' + $PIN_F2) | Out-Null
    $L.Add('pin_COLLECT      = ' + $PIN_COLLECT) | Out-Null
    $L.Add('pin_RT           = ' + $PIN_RT) | Out-Null
    $L.Add('pin_PARSERS      = ' + $PIN_PARSERS) | Out-Null
    $L.Add('pin_EQATTR       = ' + $PIN_EQATTR) | Out-Null
    $L.Add('scope            = ' + [string]$Scope) | Out-Null
    $L.Add('groups           = ' + $(if($Scope -eq 'PROBE' -or $Scope -eq 'NOTICE'){'0'}elseif($Scope -eq 'ALL'){'3'}elseif($Scope -eq 'EQATTR'){'1'}else{'2'}) + '   # PROBE=0（非业务组）；ENC+U26=2；EQATTR=1；ALL=3') | Out-Null
    $L.Add('business_groups  = 2   # 业务两组：ENC + U26；EQATTR 属 U26 子范围（不另计组）') | Out-Null
    $L.Add('plan_by_group    = ENC=' + @($ALL_PLAN | Where-Object { $_.Id -like 'ENC-*' }).Count + ' U26=' + @($ALL_PLAN | Where-Object { $_.Id -like 'U26-*' }).Count + ' U18=' + @($ALL_PLAN | Where-Object { $_.Id -like 'U18-*' }).Count + '   # 全量登记') | Out-Null
    $L.Add('plan_assertions  = ' + $script:plan.Count) | Out-Null
    $L.Add('reached          = ' + @($script:cases).Count) | Out-Null
    $L.Add('not_reached      = ' + ($script:plan.Count - @($script:cases).Count)) | Out-Null
    $L.Add('py_calls         = ' + $script:pyCalls) | Out-Null
    $L.Add('cases_pass       = ' + $passN + '/' + $all.Count) | Out-Null
    $L.Add('negcontrols      = ' + @($script:neg | Where-Object { $_.Pass }).Count + '/' + @($script:neg).Count + '   # 纯内存负控，不计入业务分母') | Out-Null
    $L.Add('neg_in_denominator= NO') | Out-Null
    $L.Add('stamp_utc        = ' + [string]$stamp) | Out-Null
    $L.Add('stamp_local      = ' + [string]$stampLocal) | Out-Null
    $L.Add('digest_pin       = ' + [string]$PIN_DIGEST) | Out-Null
    $L.Add('isolation_note   = 隔离模式仅试支路；生产顺序为先 FILE 后 PFX，不存在绕过路径') | Out-Null
    $L.Add('verdict          = ' + $verdict) | Out-Null
    if ($failInfo) { $L.Add('fail_msg         = ' + [string]$failInfo) | Out-Null }
    $L.Add('') | Out-Null
    $L.Add('## 逐条（plan 全量）') | Out-Null
    foreach ($k in $all) {
        $L.Add(('{0,-34} {1,-12} expect={2} got={3}' -f $k.ID, $(if($k.Pass){'PASS'}elseif([string]$k.Got -eq 'NOT-REACHED'){'NOT-REACHED'}else{'FAIL'}), [string]$k.Expect, [string]$k.Got)) | Out-Null
    }
    Write-Text (Join-Path $evDir 'verdict.txt') (($L -join "`n") + "`n") $false
    return $true
}
function Stage-Fails([string]$grp) {
    $bad = @($script:cases | Where-Object { $_.Item -eq $grp -and (-not $_.Pass) })
    if ($bad.Count -gt 0) {
        $m = ($bad | ForEach-Object { $_.ID + ': 期望[' + $_.Expect + '] 实际[' + $_.Got + ']' }) -join ' | '
        throw ('CASE-FAIL ' + $grp + ' n=' + $bad.Count + ' :: ' + $m)
    }
}
function Invoke-PySlots([string]$tag, [string]$script, [string[]]$slots) {
    # 槽位协议：每个槽位都显式序列化成一对引号；空槽位写 "" 占位，**绝不跳过**。
    #   collect / rt : @(path)
    #   write        : @(mode, '', dst)
    #   mut          : @(mode, src, dst)
    $o = Join-Path $evDir ('py-' + $tag + '.stdout.txt')
    $e = Join-Path $evDir ('py-' + $tag + '.stderr.txt')
    $r = Join-Path $evDir ('py-' + $tag + '.rc.txt')
    $line = '"' + $script + '"'
    foreach ($a in $slots) {
        $v = if ($null -eq $a) { '' } else { [string]$a }
        $line = $line + ' "' + $v + '"'
    }
    [System.IO.File]::WriteAllText((Join-Path $evDir ('py-' + $tag + '.argv.txt')), $line, $utf8NoBom)
    $p = Start-Process -FilePath $pyExe -ArgumentList $line -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $o -RedirectStandardError $e
    $rc = $p.ExitCode
    [System.IO.File]::WriteAllText($r, ([string]$rc), $utf8NoBom)
    $script:pyCalls = $script:pyCalls + 1
    return [pscustomobject]@{ Rc=$rc; StdOut=[System.IO.File]::ReadAllText($o); StdErr=[System.IO.File]::ReadAllText($e) }
}
function Pick ([string]$text, [string]$prefix) {
    foreach ($ln in ($text -split "`r?`n")) { if ($ln.StartsWith($prefix)) { return $ln.Substring($prefix.Length).Trim() } }
    return ''
}

try {
    # ---------------- STAGE 1 BOOTSTRAP（排他创建 + 祖先非 reparse） ----------------
    if (-not (Test-Path -LiteralPath (Split-Path -Parent $evBase))) { throw 'BOOTSTRAP: evidence 目录不存在' }
    function Assert-NoReparseAncestors([string]$path) {
        $cur = [System.IO.Path]::GetFullPath($path)
        while ($cur -and $cur.Length -gt 3) {
            $cur = [System.IO.Path]::GetDirectoryName($cur)
            if (-not $cur) { break }
            if (Test-Path -LiteralPath $cur) {
                $it = Get-Item -LiteralPath $cur -Force
                if ($it.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { throw ('BOOTSTRAP: 祖先含重解析点 -> ' + $cur) }
            }
        }
    }
    Assert-NoReparseAncestors $evBase
    $made = $false
    for ($n = 0; $n -lt 10; $n++) {
        $cand = if ($n -eq 0) { $evBase } else { $evBase + '-' + ($n + 1) }
        if (Test-Path -LiteralPath $cand) { continue }
        try { New-Item -ItemType Directory -Path $cand -ErrorAction Stop | Out-Null }
        catch { if (Test-Path -LiteralPath $cand) { continue } else { throw } }
        $evDir = $cand; $made = $true; break
    }
    if (-not $made) { throw 'BOOTSTRAP: 无法排他创建输出根' }

    # 中文 + 空格 目录（U18 的路径面变量；**不伪改 prefix**）
    $cnDir = Join-Path $evDir '中文 目录'
    New-Item -ItemType Directory -Path $cnDir -ErrorAction Stop | Out-Null
    $cnData = Join-Path $cnDir 'data'
    New-Item -ItemType Directory -Path $cnData -ErrorAction Stop | Out-Null
    Add-Stage 'BOOTSTRAP' 0 ('evDir=' + $evDir + ' cnData=' + $cnData)

    # ---------------- STAGE 2 NOTICE ----------------
    if (-not (Test-Path -LiteralPath $notice)) { throw ('NOTICE: 不存在 ' + $notice) }
    $raw = [System.IO.File]::ReadAllBytes($notice)
    $noticeSha = Get-BytesSha $raw
    if ($noticeSha -ne $KNOWN_NOTICE) { $noticeWarn = 'NOTICE-SHA-DRIFT(仅告警;内容级由 pin 把关)' }
    $idx = New-Object System.Collections.ArrayList
    $s = 0
    for ($i = 0; $i -lt $raw.Length; $i++) { if ($raw[$i] -eq 10) { $idx.Add([pscustomobject]@{ S=$s; N=($i-$s+1) }) | Out-Null; $s = $i+1 } }
    if ($s -lt $raw.Length) { $idx.Add([pscustomobject]@{ S=$s; N=($raw.Length-$s) }) | Out-Null }
    Add-Stage 'NOTICE' 0 ('bytes=' + $raw.Length + ' lines=' + $idx.Count + ' ' + $noticeWarn)

    # ---------------- STAGE 3 HOST-ID ----------------
    $hostVer = $PSVersionTable.PSVersion
    $hostExe = (Get-Process -Id $PID).Path
    if (-not ($hostVer.Major -eq 5 -and $hostVer.Minor -eq 1)) { throw ('HOST-ID: 需 PS 5.1，实际 ' + $hostVer.ToString()) }
    $pyExe = $Py
    if (-not $pyExe) { $gc = Get-Command python -ErrorAction SilentlyContinue; if ($gc) { $pyExe = $gc.Source } }
    if (-not $pyExe) { throw 'HOST-ID: 未找到 python' }
    $vOut = & $pyExe -c "import sys;print(sys.version.split()[0])"
    $script:pyCalls = $script:pyCalls + 1
    if ($LASTEXITCODE -ne 0) { throw 'HOST-ID: 解释器无法运行' }
    $pyVer = (($vOut | Out-String).Trim())
    $pySha = (Get-FileHash -LiteralPath $pyExe -Algorithm SHA256).Hash
    $pyIdent = if ($pySha -eq $S0B_PY_SHA) { 'SAME-AS-S0B' } else { 'DIFFERS-FROM-S0B' }
    $prefixOut = & $pyExe -c "import sys;print(sys.prefix)"
    $script:pyCalls = $script:pyCalls + 1
    if ($LASTEXITCODE -ne 0) { throw 'HOST-ID: 取 sys.prefix 失败' }
    $realPrefix = (($prefixOut | Out-String).Trim())
    $prefixAscii = if ($realPrefix -match '^[\x00-\x7F]+$') { 'Y' } else { 'N' }
    Add-Stage 'HOST-ID' 0 ('ps=' + $hostVer.ToString() + ' py=' + $pyVer + ' ' + $pyIdent + ' prefix_ascii=' + $prefixAscii + ' real_prefix=' + $realPrefix)

    # ---------------- STAGE 4 EXTRACT（边界 token；四新 pin + 两沿用 pin） ----------------
    $enc = [System.Text.Encoding]::UTF8
    function Line-Text([int]$k) { $L = $idx[$k]; return $enc.GetString($raw, $L.S, $L.N).TrimEnd([char]10) }
    function Slice-Bytes([int]$a, [int]$b) {
        $st = $idx[$a].S; $last = $idx[$b]; $n = ($last.S + $last.N) - $st
        $out = New-Object 'System.Byte[]' $n
        [System.Array]::Copy($raw, $st, $out, 0, $n)
        return ,$out
    }
    function Find-Range([string]$startPat, [string]$endPat) {
        for ($i = 0; $i -lt $idx.Count; $i++) {
            if ((Line-Text $i) -match $startPat) {
                for ($j = $i + 1; $j -lt $idx.Count; $j++) { if ((Line-Text $j) -match $endPat) { return @($i,$j) } }
            }
        }
        throw ('EXTRACT: 未找到 ' + $startPat + ' .. ' + $endPat)
    }
    $rF1 = Find-Range '^function Assert-AbsoluteDrivePath\(' '^\}$'
    $rF2 = Find-Range '^function Test-ChainNoReparse\(' '^\}$'
    $rC  = Find-Range '^\$PY_COLLECT = @' ('^' + "'" + '@$')
    $rR  = Find-Range '^\$PY_RT = @' ('^' + "'" + '@$')
    $rP  = Find-Range ('^\$CHK_FILE_HEX = \(\(\$chkRtOut') '^"PY-KEEP-HASHDIGEST'
    $rE  = Find-Range '^# 8b 相等三项' '^# ---- \(9\) 落证'

    $bF1 = Slice-Bytes $rF1[0] $rF1[1]
    $bF2 = Slice-Bytes $rF2[0] $rF2[1]
    $bC  = Slice-Bytes ($rC[0]+1) ($rC[1]-1)
    $bR  = Slice-Bytes ($rR[0]+1) ($rR[1]-1)
    $bP  = Slice-Bytes $rP[0] ($rP[1]-1)
    $bE  = Slice-Bytes $rE[0] ($rE[1]-1)
    $pairs = @(
        @{N='F1';B=$bF1;P=$PIN_F1}, @{N='F2';B=$bF2;P=$PIN_F2},
        @{N='COLLECT';B=$bC;P=$PIN_COLLECT}, @{N='RT';B=$bR;P=$PIN_RT},
        @{N='PARSERS';B=$bP;P=$PIN_PARSERS}, @{N='EQATTR';B=$bE;P=$PIN_EQATTR}
    )
    foreach ($x in $pairs) {
        $got = Get-BytesSha $x.B
        if ($got -ne $x.P) { throw ('EXTRACT-PIN-' + $x.N + ' 漂移 got=' + $got) }
    }
    Add-Stage 'EXTRACT' 0 ('6 段 pin 全中：F1/F2/COLLECT/RT/PARSERS/EQATTR')

    # ---------------- STAGE 5 EXTRACT-WRITE ----------------
    $pFuncs  = Join-Path $evDir '_pathfuncs.ps1'
    $pColl   = Join-Path $evDir '_pyid_collect.py'
    $pRt     = Join-Path $evDir '_pyid_check.py'
    $pParse  = Join-Path $evDir '_chkparse.ps1'
    $pEq     = Join-Path $evDir '_eqattr.ps1'
    $pSynth  = Join-Path $evDir '_synth.py'
    Write-Bytes $pFuncs ([byte[]](0xEF,0xBB,0xBF) + $bF1 + $bF2)
    Write-Bytes $pColl  $bC
    Write-Bytes $pRt    $bR
    Write-Bytes $pParse ([byte[]](0xEF,0xBB,0xBF) + $bP)
    Write-Bytes $pEq    ([byte[]](0xEF,0xBB,0xBF) + $bE)
    $srcSynth = Join-Path $PSScriptRoot '_synth.template.py'
    if (-not (Test-Path -LiteralPath $srcSynth)) { throw ('EXTRACT-WRITE: 缺 _synth.template.py -> ' + $srcSynth) }
    $bSynth = [System.IO.File]::ReadAllBytes($srcSynth)
    $gotSynth = Get-BytesSha $bSynth
    if ($gotSynth -ne $PIN_SYNTH) { throw ('EXTRACT-PIN-SYNTH 漂移 got=' + $gotSynth) }
    Write-Bytes $pSynth $bSynth
    Add-Stage 'EXTRACT-WRITE' 0 ('ps1 x3(bom) py x3 (synth ' + $bSynth.Length + 'B pin ok)')

    # ---------------- STAGE 6 PARSE ----------------
    $pk = $null; $pe = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($pFuncs, [ref]$pk, [ref]$pe)
    if (@($pe).Count -gt 0) { throw ('PARSE: _pathfuncs.ps1 解析失败 n=' + @($pe).Count + ' :: ' + ((@($pe) | ForEach-Object { 'L' + $_.Extent.StartLineNumber + ' ' + $_.Message }) -join ' | ')) }
    $pk = $null; $pe = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($pParse, [ref]$pk, [ref]$pe)
    if (@($pe).Count -gt 0) { throw ('PARSE: _chkparse.ps1 解析失败 n=' + @($pe).Count + ' :: ' + ((@($pe) | ForEach-Object { 'L' + $_.Extent.StartLineNumber + ' ' + $_.Message }) -join ' | ')) }
    $pk = $null; $pe = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($pEq, [ref]$pk, [ref]$pe)
    if (@($pe).Count -gt 0) { throw ('PARSE: _eqattr.ps1 解析失败 n=' + @($pe).Count + ' :: ' + ((@($pe) | ForEach-Object { 'L' + $_.Extent.StartLineNumber + ' ' + $_.Message }) -join ' | ')) }
    Add-Stage 'PARSE' 0 '3/3 解析通过（各自独立 err）'
    $srcDigest = Join-Path $PSScriptRoot '_digestcmp.ps1'
    if (-not (Test-Path -LiteralPath $srcDigest)) { throw ('DIGEST-PIN: 缺 _digestcmp.ps1 -> ' + $srcDigest) }
    $dgBytes = [System.IO.File]::ReadAllBytes($srcDigest)
    $dgSha = Get-BytesSha $dgBytes
    if ($dgSha -ne $PIN_DIGEST) { throw ('DIGEST-PIN: _digestcmp.ps1 漂移 got=' + $dgSha) }
    Write-Bytes (Join-Path $evDir '_digestcmp.ps1') $dgBytes
    . $srcDigest
    . $pFuncs

    # ---------------- STAGE 7 NEGCONTROLS（纯内存，不计入业务 20 分母） ----------------
    if ($runNeg) {
    $ncBase    = '1363117b1fce18d86954e20f49a3f2b42c54ea671331c09e7c60a3900523b160'
    $ncUpper   = $ncBase.ToUpperInvariant()
    $ncChanged = $ncBase.Substring(0,63) + $(if ($ncBase.Substring(63,1) -eq '0') { '1' } else { '0' })
    $ncLen63   = $ncBase.Substring(0,63)
    $ncBadChr  = 'g' + $ncBase.Substring(1)
    function Add-Neg([string]$id, [string]$expect, [string]$got) {
        $pass = ($expect -ceq $got)
        $script:neg.Add([pscustomobject]@{ ID=$id; Expect=$expect; Got=$got; Pass=$pass }) | Out-Null
    }
    Add-Neg 'NC1-DIGEST-CASE-EQ'    'True'  ([string](Same-Digest $ncUpper $ncBase))
    Add-Neg 'NC2-DIGEST-ONECHAR-NE' 'False' ([string](Same-Digest $ncChanged $ncBase))
    $ncT1 = try { [string](Same-Digest $ncLen63  $ncBase) } catch { 'THROW' }
    $ncT2 = try { [string](Same-Digest $ncBadChr $ncBase) } catch { 'THROW' }
    Add-Neg 'NC3-DIGEST-BADHEX-REJECT' 'THROW,THROW' ($ncT1 + ',' + $ncT2)
    $ncBad = @($script:neg | Where-Object { -not $_.Pass })
    Add-Stage 'NEGCONTROLS' ([int]$ncBad.Count) ($script:neg.Count.ToString() + ' 条负控，失败 ' + $ncBad.Count + '（不计入业务分母）')
    }

    # ---------------- STAGE 7' ARGV PROBE（临时；echo-argv，用同一份 Invoke-PySlots） ----------------
    if ($runProbe) {
        $probeSrc = Join-Path $PSScriptRoot '_argvprobe.py'
        if (-not (Test-Path -LiteralPath $probeSrc)) { throw ('PROBE: 缺 _argvprobe.py -> ' + $probeSrc) }
        $pbBytes = [System.IO.File]::ReadAllBytes($probeSrc)
        $pbSha = Get-BytesSha $pbBytes
        if ($pbSha -ne $PIN_PROBE) { throw ('PROBE: _argvprobe.py pin 漂移 got=' + $pbSha) }
        Write-Bytes (Join-Path $evDir '_argvprobe.py') $pbBytes
        $probeDst = Join-Path $cnData 'probe-中文 空格.json'
        $pw = Invoke-PySlots 'probe-write' $probeSrc @('write-utf8', '', $probeDst)
        $ps1 = Invoke-PySlots 'probe-single' $probeSrc @($probeDst)
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        $hashOf = { param($s) Get-BytesSha ($utf8.GetBytes([string]$s)) }
        $wArgc = [int](Pick $pw.StdOut 'ARGC ')
        $wLen2 = [int](Pick $pw.StdOut 'A2 LEN ')
        $wHash2 = Pick $pw.StdOut 'A2 HASH '
        $wHash3 = Pick $pw.StdOut 'A3 HASH '
        $sArgc = [int](Pick $ps1.StdOut 'ARGC ')
        $sHash1 = Pick $ps1.StdOut 'A1 HASH '
        $rowsp = New-Object System.Collections.ArrayList
        $rowsp.Add("id`texpect`tgot`tpass") | Out-Null
        $probeFail = 0
        function DigestToken([string]$expect,[string]$got) {
            if (-not (Test-DigestHex $expect)) { return 'EXPECT-NOT-64HEX' }
            if (-not (Test-DigestHex $got))    { return 'GOT-NOT-64HEX' }
            if (Same-Digest $expect $got) { return 'OK' } else { return 'MISMATCH' }
        }
        function PR([string]$id,[string]$exp,[string]$got) {
            $okp = ($exp -ceq $got)
            if (-not $okp) { $script:probeFail = $script:probeFail + 1 }
            $script:rowsp.Add([string]$id + "`t" + $exp + "`t" + $got + "`t" + $(if($okp){'Y'}else{'N'})) | Out-Null
        }
        $script:rowsp = $rowsp; $script:probeFail = 0
        # 非摘要字段：精确比较（ARGC、LEN 都是整数串）
        PR 'PROBE-WRITE-ARGC'   '3' ([string]$wArgc)
        PR 'PROBE-SINGLE-ARGC'  '1' ([string]$sArgc)
        # 复合值先拆分：长度严格比 0；摘要单独验 64hex 后归一比较，整串不进摘要函数
        PR 'PROBE-EMPTY-SRC'    'LEN=0,HEX=OK' ('LEN=' + [string]$wLen2 + ',HEX=' + (DigestToken (& $hashOf '') $wHash2))
        PR 'PROBE-DST-EXACT'    'HEX=OK' ('HEX=' + (DigestToken (& $hashOf $probeDst) $wHash3))
        PR 'PROBE-SINGLE-EXACT' 'HEX=OK' ('HEX=' + (DigestToken (& $hashOf $probeDst) $sHash1))
        Write-Text (Join-Path $evDir 'probe.tsv') (($rowsp -join "`n") + "`n") $false
        Add-Stage 'ARGV-PROBE' ([int]$script:probeFail) ($rowsp.Count - 1).ToString() + ' 项，失败 ' + $script:probeFail
    }

    # ---------------- STAGE 8 U18（真实 metadata × 中文+空格路径） ----------------
    if ($runU18) {
    $grp = 'U18'
    $pinReal = Join-Path $cnData ('pypin-' + $stamp + '.json')
    [void](Add-Case 'U18-PATH-HAS-CJK-SPACE' $grp ($cnData) ('CJK=' + $(if($cnData -match '[\u4e00-\u9fff]'){'Y'}else{'N'}) + ',SPACE=' + $(if($cnData -match ' ') {'Y'}else{'N'})) 'CJK=Y,SPACE=Y')

    $r1 = Invoke-PySlots 'collect-real' $pColl @($pinReal)
    $srcPin = $pinReal   # ALL: 先定源再读
    [void](Add-Case 'U18-COLLECT-RC' $grp 'collect-real rc' '0' ([string]$r1.Rc))
    $META = Pick $r1.StdOut 'META-HEX '
    $PFX0 = Pick $r1.StdOut 'PFX-HEX  '
    $r2 = Invoke-PySlots 'rt-real' $pRt @($pinReal)
    $FILE = Pick $r2.StdOut 'FILE-HEX '
    $PFX1 = Pick $r2.StdOut 'PFX-HEX  '
    $FLD  = Pick $r2.StdOut 'FIELDS   '
    [void](Add-CaseDigest 'U18-FILE-EQ-META' $grp 'FILE vs META（摘要边界）' $META $FILE)
    [void](Add-CaseDigest 'U18-PFX-EQ' $grp 'PFX1 vs PFX0（摘要边界）' $PFX0 $PFX1)
    [void](Add-Case 'U18-FIELDS-EXACT' $grp 'FIELDS' $SIX_FIELDS $FLD)

    $objReal = ([System.IO.File]::ReadAllText($srcPin, $utf8NoBom)) | ConvertFrom-Json
    if (-not $objReal) { throw 'OBJREAL: 未生成（U18 分支）' }
    [void](Add-Case 'U18-PREFIX-ROUNDTRIP' $grp 'PS 读回 prefix' $realPrefix ([string]$objReal.prefix))
    $filBytes = [System.IO.File]::ReadAllBytes($srcPin)
    [void](Add-CaseDigest 'U18-PS-RECOMPUTE-EQ' $grp 'PS 独立算 sha256（摘要边界）' $META (Get-BytesSha $filBytes))
    Stage-Fails $grp
    Add-Stage 'U18' 0 (@($script:cases | Where-Object { $_.Item -eq $grp }).Count.ToString() + ' 条')
    $srcPin = $pinReal
    } elseif ($runFix) {
    # ---------------- STAGE 8' INPUT-FIXED（按完整 hash 复制已核输入；只读） ----------------
    if (-not $FixedPin) { throw 'INPUT-FIXED: 未给 -FixedPin' }
    if (-not (Test-Path -LiteralPath $FixedPin)) { throw ('INPUT-FIXED: 源不存在 -> ' + $FixedPin) }
    if ($pyIdent -ne 'SAME-AS-S0B') { throw ('INPUT-FIXED: 解释器身份已变（' + $pyIdent + '），先停核') }
    $fixBytes = [System.IO.File]::ReadAllBytes($FixedPin)
    $fixSha = Get-BytesSha $fixBytes
    if ($fixSha -ne $FIXED_PIN_SHA) { throw ('INPUT-FIXED: 源输入身份不符 got=' + $fixSha) }
    $pinFixed = Join-Path $cnData ('pypin-fixed-' + $stamp + '.json')
    Write-Bytes $pinFixed $fixBytes
    $cpSha = Get-BytesSha ([System.IO.File]::ReadAllBytes($pinFixed))
    if ($cpSha -ne $fixSha) { throw ('INPUT-FIXED: 复制后 hash 不符 got=' + $cpSha) }
    $r2 = Invoke-PySlots 'rt-fixed' $pRt @($pinFixed)
    if ($r2.Rc -ne 0) { throw ('INPUT-FIXED: rt 失败 rc=' + $r2.Rc) }
    $META = $fixSha
    $PFX0 = Pick $r2.StdOut 'PFX-HEX  '
    $FILE = Pick $r2.StdOut 'FILE-HEX '
    if (-not $PFX0 -or -not $FILE) { throw 'INPUT-FIXED: rt 输出不完整' }
    if (-not (Same-Digest $META $FILE)) { throw ('INPUT-FIXED: 固定输入 FILE≠META got=' + $FILE) }
    Add-Stage 'INPUT-FIXED' 0 ('src=' + $FixedPin + ' sha=' + $fixSha + ' copy=ok META=取自源字节(声明推导)')
    $srcPin = $pinFixed
    $objReal = ([System.IO.File]::ReadAllText($srcPin, $utf8NoBom)) | ConvertFrom-Json
    if (-not $objReal) { throw 'OBJREAL: 未生成（FixedPin 分支）' }
    $missingF = @($SIX_FIELDS.Split(',') | Where-Object { -not $objReal.PSObject.Properties[$_] })
    if ($missingF.Count -gt 0) { throw ('OBJREAL: 缺字段 -> ' + ($missingF -join ',')) }
    Add-Stage 'OBJREAL-INIT' 0 ('fields=' + $SIX_FIELDS + ' python=' + [string]$objReal.python + ' prefix_len=' + ([string]$objReal.prefix).Length)
    }
    $mutByte = $null; $mutPfx = $null; $mutFld = $null

    # ---------------- STAGE 8 ENC（独立合成编码控制；不冒充 env identity） ----------------
    if ($runEnc) {
    $grp = 'ENC'
    $synUtf8 = Join-Path $cnData 'synth-cn-utf8.json'
    $synAsc  = Join-Path $cnData 'synth-cn-ensureascii.json'
    $synBad  = Join-Path $cnData 'synth-cn-asciiwrite.json'
    $synPlain= Join-Path $cnData 'synth-plain-ascii.json'

    $b1 = Invoke-PySlots 'enc-ascii-cn' $pSynth @('write-ascii-cn', '', $synBad)
    if ($b1.Rc -eq 0) { throw ('ENC-SETUP: write-ascii-cn 竟然成功，场景无效 rc=0') }
    [void](Add-Case 'ENC-ASCII-CONTENT-REJECT' $grp 'ascii 编码写含中文内容 ⇒ 应抛' 'RC-NZ=Y,UE=Y' ('RC-NZ=' + $(if($b1.Rc -ne 0){'Y'}else{'N'}) + ',UE=' + $(if($b1.StdErr -like '*UnicodeEncodeError*'){'Y'}else{'N'})))
    $b2 = Invoke-PySlots 'enc-utf8' $pSynth @('write-utf8', '', $synUtf8)
    if ($b2.Rc -ne 0) { throw ('ENC-SETUP: write-utf8 失败 rc=' + $b2.Rc + ' :: ' + $b2.StdErr) }
    $utf8Hex = Pick $b2.StdOut 'HEX '
    if (-not $utf8Hex) { throw 'ENC-SETUP: write-utf8 未输出 HEX' }
    $r3 = Invoke-PySlots 'rt-synth-utf8' $pRt @($synUtf8)
    if ($r3.Rc -ne 0) { throw ('ENC-SETUP: rt-synth-utf8 失败 rc=' + $r3.Rc) }
    [void](Add-CaseDigest 'ENC-UTF8-OK-BYTES' $grp 'FILE-HEX vs 内存HEX（摘要边界）' $utf8Hex (Pick $r3.StdOut 'FILE-HEX '))
    $b3 = Invoke-PySlots 'enc-ensureascii' $pSynth @('write-ensureascii', '', $synAsc)
    if ($b3.Rc -ne 0) { throw ('ENC-SETUP: write-ensureascii 失败 rc=' + $b3.Rc) }
    $eaHex = Pick $b3.StdOut 'HEX '
    $r4 = Invoke-PySlots 'rt-synth-ea' $pRt @($synAsc)
    if ($r4.Rc -ne 0) { throw ('ENC-SETUP: rt-synth-ea 失败 rc=' + $r4.Rc) }
    $eaFile = Pick $r4.StdOut 'FILE-HEX '
    $eaPfx  = Pick $r4.StdOut 'PFX-HEX  '
    [void](Add-Case 'ENC-ENSUREASCII-FILE-DIFF' $grp 'ea FILE vs utf8 FILE（摘要边界判差）' ('DIFF=' + $(if (Same-Digest $eaFile $utf8Hex) {'N'} else {'Y'})) 'DIFF=Y')
    [void](Add-CaseDigest 'ENC-VALUE-PRESERVED' $grp 'ea PFX vs utf8 PFX（摘要边界）' (Pick $r3.StdOut 'PFX-HEX  ') $eaPfx)
    $b4 = Invoke-PySlots 'enc-ascii-plain' $pSynth @('write-ascii-plain', '', $synPlain)
    [void](Add-Case 'ENC-ASCII-CONTENT-ON-CJK-PATH-OK' $grp '纯ASCII内容+ascii+中文空格路径 ⇒ 应成功' 'RC-ZERO=Y,OK=Y' ('RC-ZERO=' + $(if($b4.Rc -eq 0){'Y'}else{'N'}) + ',OK=' + $(if($b4.StdOut -like '*WROTE-ASCII-PLAIN-OK*'){'Y'}else{'N'})))
    Stage-Fails $grp
    Add-Stage 'ENC' 0 (@($script:cases | Where-Object { $_.Item -eq $grp }).Count.ToString() + ' 条')
    }

    # ---------------- STAGE 9 U26（比较链路；隔离模式仅试支路） ----------------
    if ($runU26) {
    $grp = 'U26'
    if (-not $runEqOnly) {
    $mutByte = Join-Path $cnData ('pypin-' + $stamp + '.mut-byte.json')
    $mutPfx  = Join-Path $cnData ('pypin-' + $stamp + '.mut-pfx.json')
    $mutFld  = Join-Path $cnData ('pypin-' + $stamp + '.mut-fields.json')
    $mb = Invoke-PySlots 'synth-mut-byte'   $pSynth @('mut-byte',   $srcPin, $mutByte)
    if ($mb.Rc -ne 0) { throw ('U26-SETUP: mut-byte 失败 rc=' + $mb.Rc + ' :: ' + $mb.StdErr) }
    $mp = Invoke-PySlots 'synth-mut-pfx'    $pSynth @('mut-pfx',    $srcPin, $mutPfx)
    if ($mp.Rc -ne 0) { throw ('U26-SETUP: mut-pfx 失败 rc=' + $mp.Rc) }
    $mf = Invoke-PySlots 'synth-mut-fields' $pSynth @('mut-fields', $srcPin, $mutFld)
    if ($mf.Rc -ne 0) { throw ('U26-SETUP: mut-fields 失败 rc=' + $mf.Rc) }
    }

    function Try-Compare([scriptblock]$sb) {
        $o = [pscustomobject]@{ Threw=$false; Msg='' }
        try { & $sb } catch { $o.Threw=$true; $o.Msg=$_.Exception.Message }
        return $o
    }
    function Classify([string]$msg) {
        if ($msg -like '*留存 FILE-HEX != 其 META-HEX*') { return 'FILE' }
        if ($msg -like '*留存 prefix 字段对拍不一致*') { return 'PFX' }
        if ($msg -like '*留存身份字段不齐*') { return 'FIELDS' }
        if ($msg -like '*留存环境身份与原身份不符*') { return 'TRIO' }
        if ($msg -like '*解释器 exe 不在留存目录内*') { return 'ATTR-EXE' }
        if ($msg -like '*sys.prefix 不等于留存目录*') { return 'ATTR-PFX' }
        return 'OTHER'
    }

    if (-not $runEqOnly) {
    # 正例：三值全过
    $CHK_META_HEX = $META; $CHK_PFX_HEX0 = $PFX0; $chkRtOut = ($r2.StdOut -split "`r?`n")
    $t = Try-Compare { . $pParse }
    [void](Add-Case 'U26-POS-TRIPLE' $grp '8a 正例：原 pin 三值全过' 'threw=False' ('threw=' + $t.Threw))

    # 整链：META 保原值 + 篡改件
    $rMb = Invoke-PySlots 'rt-mut-byte' $pRt @($mutByte)
    $CHK_META_HEX = $META; $CHK_PFX_HEX0 = $PFX0; $chkRtOut = ($rMb.StdOut -split "`r?`n")
    $t = Try-Compare { . $pParse }
    [void](Add-Case 'U26-NEG-FILE' $grp '整链：META 保原值 + mut-byte' 'class=FILE' ('class=' + (Classify $t.Msg)))

    # 隔离试支路：META 对齐篡改后 FILE，使 PFX 成为首个失败点
    $rMp = Invoke-PySlots 'rt-mut-pfx' $pRt @($mutPfx)
    $CHK_META_HEX = (Pick $rMp.StdOut 'FILE-HEX '); $CHK_PFX_HEX0 = $PFX0; $chkRtOut = ($rMp.StdOut -split "`r?`n")
    $t = Try-Compare { . $pParse }
    [void](Add-Case 'U26-NEG-PFX-ISO' $grp '隔离：META 对齐 + mut-pfx' 'class=PFX' ('class=' + (Classify $t.Msg)))

    $rMf = Invoke-PySlots 'rt-mut-fields' $pRt @($mutFld)
    $CHK_META_HEX = (Pick $rMf.StdOut 'FILE-HEX '); $CHK_PFX_HEX0 = (Pick $rMf.StdOut 'PFX-HEX  '); $chkRtOut = ($rMf.StdOut -split "`r?`n")
    $t = Try-Compare { . $pParse }
    [void](Add-Case 'U26-NEG-FIELDS-ISO' $grp '隔离：META 对齐 + mut-fields' 'class=FIELDS' ('class=' + (Classify $t.Msg)))
    }   # 前四条业务用例结束

    # 8b 相等三项
    $pyObj = $objReal
    $chkObj = $objReal | Select-Object *
    $chkObj.python = '3.11.8'
    $keepRoot = $realPrefix
    $t = Try-Compare { . $pEq }
    [void](Add-Case 'U26-EQ-TRIO-NEG' $grp '8b：python 改 3.11.8' 'class=TRIO' ('class=' + (Classify $t.Msg)))
    $chkObj = $objReal | Select-Object *
    $t = Try-Compare { . $pEq }
    [void](Add-Case 'U26-EQ-TRIO-POS' $grp '8b：三值与原相同' 'threw=False' ('threw=' + $t.Threw))

    # 8c 归属三项：正例 keepRoot=真实 prefix；反例 keepRoot=假目录
    $keepRoot = $realPrefix
    $t = Try-Compare { . $pEq }
    [void](Add-Case 'U26-ATTR-POS' $grp '8c：keepRoot=真实 sys.prefix' 'threw=False' ('threw=' + $t.Threw))
    $keepRoot = Join-Path $evDir '假留存根'
    $t = Try-Compare { . $pEq }
    [void](Add-Case 'U26-ATTR-NEG' $grp '8c：keepRoot=假留存根' 'class=ATTR-EXE' ('class=' + (Classify $t.Msg)))
    Stage-Fails $grp
    Add-Stage 'U26' 0 (@($script:cases | Where-Object { $_.Item -eq $grp }).Count.ToString() + ' 条')
    }

    # ---------------- STAGE 10 HASHES ----------------
    $skipHash = 0
    foreach ($f in @($pFuncs,$pColl,$pRt,$pParse,$pEq,$pSynth,$pinReal,$synUtf8,$synAsc,$synPlain,$mutByte,$mutPfx,$mutFld)) {
        if ([string]::IsNullOrEmpty([string]$f)) { $skipHash++; continue }
        if (Test-Path -LiteralPath $f) {
            $bb = [System.IO.File]::ReadAllBytes($f)
            $script:hashRows.Add([pscustomobject]@{ File=(Split-Path $f -Leaf); Bytes=$bb.Length; Sha256=(Get-BytesSha $bb) }) | Out-Null
        }
    }
    Add-Stage 'HASHES' 0 ($script:hashRows.Count.ToString() + ' files, skip_empty=' + $skipHash)

    # ---------------- STAGE 11 VERDICT ----------------
    $passCount = @($script:cases | Where-Object { $_.Pass }).Count
    $reached = @($script:cases).Count
    $verdict = if (($passCount -eq $reached) -and ($reached -eq $script:plan.Count)) { 'PASS' } else { 'FAIL' }
    if (-not (Save-Evidence $verdict '')) { throw 'VERDICT: 证据未能落盘' }
    $script:written = $true
    if ($verdict -ne 'PASS') { exit 1 }
    exit 0
}
catch {
    $msg = $_.Exception.Message
    if (Test-Path -LiteralPath $evDir) {
        $cls = if ($msg -like 'EXTRACT*') {'EXTRACT'} elseif ($msg -like 'STAGE-FAIL*') {'STAGE'} elseif ($msg -like 'CASE-FAIL*') {'CASE'} elseif ($msg -like 'HOST*') {'HOST'} elseif ($msg -like 'BOOTSTRAP*') {'BOOTSTRAP'} elseif ($msg -like 'VERDICT*') {'VERDICT'} elseif ($msg -like 'DIGEST-FORMAT*') {'DIGEST'} else {'OTHER'}
        if (Save-Evidence 'FAIL' ($cls + ' :: ' + $msg)) { $script:written = $true }
    }
    [Console]::Error.WriteLine($msg)
    if (-not $script:written) { exit 2 }
    exit 1
}
