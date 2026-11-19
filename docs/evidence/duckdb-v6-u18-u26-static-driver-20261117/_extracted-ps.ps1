$CHK_FILE_HEX = (($chkRtOut | Select-String '^FILE-HEX').Line -split '\s+')[1]
$CHK_PFX_HEX1 = (($chkRtOut | Select-String '^PFX-HEX').Line -split '\s+')[1]
$CHK_FIELDS   = (($chkRtOut | Select-String '^FIELDS').Line -split '\s+')[1]
if (-not $CHK_FILE_HEX -or -not $CHK_PFX_HEX1 -or -not $CHK_FIELDS) { throw "留存对拍输出不完整，拒绝继续：$chkRtOut" }
if ($CHK_FILE_HEX -ne $CHK_META_HEX) { throw "留存 FILE-HEX != 其 META-HEX（写入编码/截断损坏），拒绝继续。" }
if ($CHK_PFX_HEX1 -ne $CHK_PFX_HEX0) { throw "留存 prefix 字段对拍不一致，拒绝继续。" }
if ($CHK_FIELDS -ne "duckdb,duckdb_file,exe,prefix,python,source_id") { throw "留存身份字段不齐：$CHK_FIELDS" }

# 8b 相等三项：python / duckdb / source_id
$mismatch = @()
if ($chkObj.python -ne $pyObj.python) { $mismatch += "python: $($chkObj.python) != $($pyObj.python)" }
if ($chkObj.duckdb -ne $pyObj.duckdb) { $mismatch += "duckdb: $($chkObj.duckdb) != $($pyObj.duckdb)" }
if ("$($chkObj.source_id)" -ne "$($pyObj.source_id)") { $mismatch += "source_id: $($chkObj.source_id) != $($pyObj.source_id)" }
if ($mismatch.Count -gt 0) { throw ("留存环境身份与原身份不符（不是同版本链，禁止升级）：" + ($mismatch -join '; ')) }

# 8c 归属三项：exe / duckdb 模块 / prefix 必须"全链无链接"且落在留存目录内
$exeChk = Test-ChainNoReparse $chkObj.exe
$dfChk  = Test-ChainNoReparse $chkObj.duckdb_file
$pfxChk = Test-ChainNoReparse $chkObj.prefix
if (-not $exeChk -or -not $dfChk -or -not $pfxChk) { throw "留存环境的 exe/prefix/duckdb 模块路径不可解析，拒绝继续。" }
if (-not $exeChk.StartsWith($keepRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "解释器 exe 不在留存目录内（$exeChk），留存不独立，禁止升级。"
}
if (-not $dfChk.StartsWith($keepRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "duckdb 模块不在留存目录内（$dfChk）——仍在用原 site-packages，升级会把它一起改掉，留存不独立，禁止升级。"
}
if ($pfxChk -ne $keepRoot) { throw "sys.prefix 不等于留存目录（$pfxChk != $keepRoot），留存不独立，禁止升级。" }

