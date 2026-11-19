$CHK_FILE_HEX = (($chkRtOut | Select-String '^FILE-HEX').Line -split '\s+')[1]
$CHK_PFX_HEX1 = (($chkRtOut | Select-String '^PFX-HEX').Line -split '\s+')[1]
$CHK_FIELDS   = (($chkRtOut | Select-String '^FIELDS').Line -split '\s+')[1]
if (-not $CHK_FILE_HEX -or -not $CHK_PFX_HEX1 -or -not $CHK_FIELDS) { throw "留存对拍输出不完整，拒绝继续：$chkRtOut" }
if ($CHK_FILE_HEX -ne $CHK_META_HEX) { throw "留存 FILE-HEX != 其 META-HEX（写入编码/截断损坏），拒绝继续。" }
if ($CHK_PFX_HEX1 -ne $CHK_PFX_HEX0) { throw "留存 prefix 字段对拍不一致，拒绝继续。" }
if ($CHK_FIELDS -ne "duckdb,duckdb_file,exe,prefix,python,source_id") { throw "留存身份字段不齐：$CHK_FIELDS" }
