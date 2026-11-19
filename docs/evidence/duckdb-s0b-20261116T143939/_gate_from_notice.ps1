if ($scanRc -eq 8) { throw "源目录根无效（不存在或不可读），不能当作零链接，拒绝继续。" }
$SCAN_LINKS = (($scanOut | Select-String '^SCAN-LINKS').Line -split '\s+')[1]
$SCAN_DIRS  = (($scanOut | Select-String '^SCAN-DIRS').Line -split '\s+')[1]
$SCAN_WERR  = (($scanOut | Select-String '^SCAN-WALK-ERRORS').Line -split '\s+')[1]
$SCAN_LERR  = (($scanOut | Select-String '^SCAN-LSTAT-ERRORS').Line -split '\s+')[1]
$SCAN_ROOTE = (($scanOut | Select-String '^SCAN-ROOT-ENTRIES').Line -split '\s+')[1]
if (-not $SCAN_LINKS -or -not $SCAN_DIRS -or -not $SCAN_WERR -or -not $SCAN_LERR -or -not $SCAN_ROOTE) {
    throw "扫描输出缺字段（LINKS/DIRS/WALK-ERRORS/LSTAT-ERRORS/ROOT-ENTRIES），拒绝继续：$scanOut"
}
if ($scanRc -ne 0) { throw "源目录扫描失败（exit=$scanRc，遍历错误 $SCAN_WERR 项 / lstat 错误 $SCAN_LERR 项）——不能当作零链接，拒绝继续。" }
if ([int]$SCAN_WERR -ne 0) { throw "遍历期有 $SCAN_WERR 个目录无法列出（scandir 被拒），未进入其内部，结论不可靠，拒绝继续。" }
if ([int]$SCAN_LERR -ne 0) { throw "逐项 lstat 有 $SCAN_LERR 个失败，结论不可靠，拒绝继续。" }
# 只保留"walk 至少产出过根目录"这一条**结构性**不变量（walk 对有效根必然产出 1 次）。
# ⚠ 上一版还有一条 `DIRS==1 且 ROOT-ENTRIES>0 ⇒ 拒` 的兜底，**是错的，已删除**：
#   合法情形「根下只有普通文件、没有子目录」时，walk 恰好只产出根 1 次、而根下确有 N 个文件项 ——
#   这条规则会把完全正常的目录判成"扫描异常"，属于**凭空造的兜底**，与真实错误通道无关。
# 判"扫描是否可信"只认两条真实门：SCAN-WALK-ERRORS（onerror 收集）与 SCAN-LSTAT-ERRORS（逐项 lstat）。
if ([int]$SCAN_DIRS -lt 1) { throw "walk 未产出任何目录（根断言异常），拒绝继续。" }
if ([int]$SCAN_LINKS -ne 0) { throw "源环境内部存在 $SCAN_LINKS 个重解析点，无法保证留存完整且独立，拒绝复制。" }
"PY-SRC-LINKS  = 0（dirs=$SCAN_DIRS, root_entries=$SCAN_ROOTE, walk_err=0, lstat_err=0，结论仅限检查时刻）"