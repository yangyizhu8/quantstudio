# U27 / U32 / U28 小驱动 · 静态候选件与预期（**本版未运行**）

编制：2026-11-16（节点⑲，DRIFT-N19-r5）｜性质：**静态源交付**，等审核后再放一次运行

> **本轮边界（已遵守）**：仅静态源；**未运行**链检查或 scanner；未建 ACL / junction；未复制环境；未触生产库；未恢复供血；未清 flag；未客户下发；**未提交推送**。

## 〇、一页速览

| 项 | 值 |
|---|---|
| 驱动件 | `_driver.candidate.ps1` |
| 驱动 bytes / sha256 | **15333** / **`471B1D1E5F0FB39188BDEE1B8AD270383534854D2F1AAC22B89275F1A5808C2E`** |
| 静态语法校验 | `Parser::ParseFile` **errors = 0**（2918 tokens） |
| 抽取件 1 | `_extracted-pathfuncs.ps1` **2136 B**（BOM + 两函数）`ParseFile` errors=0 |
| 抽取件 2 | `_extracted-scan.py` **2110 B**（无 BOM，LF 原样）`py_compile` **OK** |
| 用例数 | **10**（C1×5 / C2×3 / C3×6 判决断，落在 10 个用例上） |
| 阶段数 | **10**（BOOTSTRAP / NOTICE / EXTRACT / EXTRACT-WRITE / PARSE / C1 / C2 / C3 / HASHES / VERDICT） |
| 运行方式 | 审核通过后**执行一次**；产物落 `docs\evidence\duckdb-v6-u27-u28-u32-run-<stamp>\` |

## 一、为什么只抽三段、不抄整通知

| | bytes | 说明 |
|---|---|---|
| 通知全文 | **117808** | 含 §1–§六、U1–U32 表、全部步骤 |
| 本驱动实际抽取 | **4243**（749 + 1384 + 2110） | 只有被判据直接覆盖的三段 |
| 占比 | **3.60%** | 抄整通知既无必要，也会把无关分支拖进运行面 |

**理由**：三项验证的被测对象只有三处——`Assert-AbsoluteDrivePath`、`Test-ChainNoReparse`、scanner 正文。
整份通知还含留存复制、版本门、基线采样等**不该在本轮出现**的步骤；抄进来只会扩大运行面。

## 二、抽取规则（**边界 token 定位，不用行号**）

行号会随通知编辑漂移，故一律按**边界 token** 定位，并对结果做**内容 hash 硬 pin**：

| 段 | 起始 token | 终止 token（整行精确） | 当前落点 |
|---|---|---|---|
| 函数 1 | `^function Assert-AbsoluteDrivePath\(` | `}` | L326..L336 |
| 函数 2 | `^function Test-ChainNoReparse\(` | `}` | L338..L361 |
| scanner | `^\$PY_SCAN = @'` | `'@` | 围栏 L419 / L477，正文 L420..L476 |

### 抽取结果（静态阶段实测）

```text
name	first_line	last_line	lines	bytes	sha256
Assert-AbsoluteDrivePath	326	336	11	749	1DD4DC464640BCDBDB2AAB3F6E85992FB6FDFBCE31D3B276E8DC2F60B0DB234F
Test-ChainNoReparse	338	361	24	1384	8B725BA145C6D0EB9EB0796922F604FA9AF86C71272E34C9267FFA84D61C486B
PY_SCAN-body	420	476	57	2110	53AC95D927359DB4AB876836E9C11B1DDA06EA4B89566480E368A858D9B34A16
PY_SCAN-withfence	419	477	59	2127	03EB540AFF5BB0E3BA524940B7F1A6CF6C0D8097F47AD924D91D9F205460F64F
_extracted-pathfuncs.ps1	-	-	35	2133	25BE8094AEB49B1968A1F40EE748607C7BF95283FAD27A813B0155DEE2693669
_file_pathfuncs(with_BOM)	-	-	-	2136	EDFFBDB3B3DB0BCE7A872426995A2BC23B1FFCE56D2363EE9B1F4D426CB85781
```

> **硬 pin**：驱动在 `EXTRACT` 阶段自算三段 sha256，与上表 pin 不符即 **FAIL 并停止**——
> 不「降级通过」、不「警告继续」。文档级 `notice_sha256` 只记录 + 告警（允许无关章节编辑使其变化）。

## 三、三段完整源（逐字，未改一字符）

### 3.1 `Assert-AbsoluteDrivePath`（749 B / 11 行）

```powershell
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
```

### 3.2 `Test-ChainNoReparse`（1384 B / 24 行）

```powershell
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
```

### 3.3 scanner 正文（2110 B / 57 行）

```python
import os, sys, stat
root = sys.argv[1]
REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

# 根有效断言：不能"扫了个不存在/读不了的地方"还报 0 链接
if not os.path.isdir(root):
    print("SCAN-ROOT-INVALID " + root)
    sys.exit(8)
try:
    root_entries = os.listdir(root)
except OSError as ex:
    print("SCAN-ROOT-INVALID " + root + " :: " + str(ex))
    sys.exit(8)
print("SCAN-ROOT-ENTRIES " + str(len(root_entries)))

# onerror 必须显式给：默认 onerror=None 时 scandir 被拒会被**静默忽略**，
# 内层循环根本看不到那个目录的内容，错误数于是仍是 0 —— 这正是上一版的漏。
walk_errors, lstat_errors, hits = [], [], []
dirs_visited = 0

def onerror(ex):
    walk_errors.append("%s :: %s" % (getattr(ex, "filename", "?"), ex))

for dp, dns, fns in os.walk(root, onerror=onerror, followlinks=False):
    dirs_visited += 1
    # Windows 上 junction 的 is_dir() 为 True、is_symlink() 为 False，
    # 只靠 followlinks=False **挡不住**它 —— 必须自行检测并剪枝，否则会钻进链接目标。
    for name in list(dns):
        q = os.path.join(dp, name)
        try:
            st = os.lstat(q)
        except OSError as ex:
            lstat_errors.append("%s :: %s" % (q, ex))
            dns.remove(name)
            continue
        if getattr(st, "st_file_attributes", 0) & REPARSE:
            hits.append(q)
            dns.remove(name)
    for name in fns:
        q = os.path.join(dp, name)
        try:
            st = os.lstat(q)
        except OSError as ex:
            lstat_errors.append("%s :: %s" % (q, ex))
            continue
        if getattr(st, "st_file_attributes", 0) & REPARSE:
            hits.append(q)

print("SCAN-LINKS " + str(len(hits)))
print("SCAN-DIRS " + str(dirs_visited))
print("SCAN-WALK-ERRORS " + str(len(walk_errors)))
print("SCAN-LSTAT-ERRORS " + str(len(lstat_errors)))
for h in hits[:10]:
    print("SCAN-HIT " + h)
for err in (walk_errors + lstat_errors)[:10]:
    print("SCAN-ERR " + err)
sys.exit(0 if (not walk_errors and not lstat_errors) else 7)
```

## 四、实际参数与预期结果

### C1 · U27（`Assert-AbsoluteDrivePath`）

| # | 用例 ID | 实际参数 | 判据 | 预期结果 |
|---|---|---|---|---|
| 1 | `C1-ACCEPT-FULL` | `D:\qssyn\data` | 应**不抛**且原样返回 | `OK\|D:\qssyn\data` |
| 2 | `C1-REJ-DRIVREL` | `D:qssyn\data`（`D:` 后无斜杠） | 应抛且含「驱动器相对形式」 | `THREW\|驱动器相对` |
| 3 | `C1-REJ-DRIVREL-BARE` | `D:`（裸盘符） | 应抛且含「驱动器相对形式」 | `THREW\|驱动器相对` |
| 4 | `C1-REJ-NONABS` | `qssyn\data` | 应抛且含「不是绝对路径」 | `THREW\|不是绝对路径` |
| 5 | `C1-REJ-UNC` | `\\server\share\x` | 应抛且含「UNC 路径不在本判据内」 | `THREW\|UNC` |

> **#1 是鉴别力对照**：若闸对一切输入都抛，则 #2–#5 全过也毫无意义。
> **U27 的判据要点**正是「`D:qssyn\data` 必须被拒」**且**「`D:\qssyn\data` 必须被放行」，两条同时成立才算有区分度。

### C2 · U32（`Test-ChainNoReparse` 的盘根保留）

| # | 用例 ID | 实际参数 | 判据 | 预期结果 |
|---|---|---|---|---|
| 6 | `C2-ROOT-KEEP` | `D:\` | 返回值必须**仍是 `D:\`**（不得退化成 `D:`） | `OK\|D:\` |
| 7 | `C2-ROOT-FWDSLASH` | `D:/` | 规范化后仍须是 `D:\` | `OK\|D:\` |
| 8 | `C2-OLD-FORM-CTRL` | `[D:\].TrimEnd(\\)` | 必须等于 `D:` | `D:` |

> **#8 是反向控制**：它证明「旧写法 `TrimEnd('\')` 会把 `D:\` 退化成驱动器相对的 `D:`」这件事**属实**，
> 从而 #6 不是空洞断言——被测代码里那句「这里不做任何裁剪」的注释是有据的，且确实防住了退化。

### C3 · U28（scanner 真实扫描**不存在的根** → exit 8）

| # | 用例 ID | 实际参数 | 判据 | 预期结果 |
|---|---|---|---|---|
| 9 | `C3-BADROOT-RC` | `D:\qssyn_nope_<stamp>`（运行前实测**不存在**） | 进程退出码 | `8` |
| 10 | `C3-BADROOT-MSG` | 同上 | stdout 含 `SCAN-ROOT-INVALID` | `HAS-SCAN-ROOT-INVALID` |
| 11 | `C3-BADROOT-NO-COUNTS` | 同上 | stdout **不得**出现 `SCAN-LINKS` | `ABSENT` |
| 12 | `C3-CTRL-RC` | `$evDir`（**有效根**） | 进程退出码 | `0` |
| 13 | `C3-CTRL-ROOTENTRIES` | 同上 | stdout 含 `SCAN-ROOT-ENTRIES` | `HAS-SCAN-ROOT-ENTRIES` |
| 14 | `C3-CTRL-NO-INVALID` | 同上 | 不得报 `SCAN-ROOT-INVALID` | `ABSENT` |

> **#11 是本项的灵魂**：U28 的原始缺陷是「扫了个不存在的地方，却报 0 链接」——
> 所以除了「必须 exit 8」，还要证「**没跑到计数行就退了**」，否则 exit 8 之外仍可能先打一排 0。
> **#12–#14 是正向控制**：同一扫描器对有效根必须 rc=0 且正常出计数，证明 exit 8 **不是无差别拒绝**。

## 五、判定规则（fail-closed）

| 规则 | 内容 |
|---|---|
| 内容 pin | 三段抽取 sha256 必须**逐位相等**，否则 `EXTRACT-PIN-*` 直接 FAIL |
| 阶段门 | 任一阶段 rc≠0 即抛，后续阶段不跑（`Add-Stage` 内置） |
| 用例门 | 单阶段内**全跑完再判**（保留全部差异行），该阶段有任意 FAIL 即抛 `CASE-FAIL` |
| 退出码 | PASS=**0**；FAIL=**1**；证据写不出去=**2**（显式，不静默） |
| 假失败防护 | `& $pyExe ...` 先整体赋给变量、再单独取 `$LASTEXITCODE`——**不接 `Select-Object`**（r3 实测该写法致 rc=-1 假失败） |
| 证据自证 | `HASHES` 阶段重读落盘件算 sha256，与 `cases.tsv` / `verdict.txt` 一起落盘 |
| 编码 | 驱动与被抽取 `.ps1` 一律 **UTF-8 带 BOM**（PS 5.1 按 ANSI 读无 BOM 文件，中文必乱码——r2 实测） |

## 六、静态校验回执（本轮已做，**非运行**）

| 对象 | 校验 | 结果 |
|---|---|---|
| `_driver.candidate.ps1` | `[Parser]::ParseFile` | **errors = 0**（2918 tokens） |
| `_extracted-pathfuncs.ps1` | `[Parser]::ParseFile` | **errors = 0** |
| `_extracted-scan.py` | `py_compile.compile(doraise=True)` | **OK** |
| 行首 here-string 终止符扫描 | 无（避免抽取出嵌套 here-string 这类历史坑） | 通过 |

> **静态校验 ≠ 运行**：`ParseFile` 只证明语法可解析；它**不能**发现运行期缺陷。
> 前几轮的事实是：r2 的编码缺陷、r3 的判定缺陷，**全都是在真跑时才暴露的**，静态看都「没问题」。
> 所以本轮只能交「可运行且预期明确」，**不能**交「已验证」。

## 七、一处必须说清的差异：EOL 与末尾换行

| 件 | bytes | LF | CRLF | 说明 |
|---|---|---|---|---|
| 通知 scanner 正文（本轮抽取） | **2110** | 57 | 0 | 原文 LF，逐字节原样 |
| S0b 运行期落盘 `_pyid_scan.py` | **2165** | 56 | 56 | 由行数组 + CRLF **重新拼接**，且**无末尾换行** |

⇒ 两者 **sha256 不同**（`53AC95D927359DB4AB876836E9C11B1DDA06EA4B89566480E368A858D9B34A16` vs `88772071…4A4C`），但**代码内容一致**：
差异仅在（a）行结束符约定、（b）末尾是否多一个换行——都对 Python **无任何语义影响**。

> **为什么要专门写**：上一轮我在同类地方栽过一次（`io.open` 的 universal newlines 把 CRLF 归一化成 LF，
> 我据此误报「扫描器抽取不一致」）。这里**先把差异量出来再判断**，并且本驱动一律写 **LF 原样**，
> 使运行期 hash 可与本轮静态 pin **直接比对**，不再引入第三套行尾。

## 八、若全 PASS，对 U 表的影响（**预告，非申报**）

| 项 | 现状 | 运行后（若全 PASS） | 依据 |
|---|---|---|---|
| **U27** | 未测 | **已核** | 被测对象就是 `Assert-AbsoluteDrivePath` 本体，5 例含鉴别力对照 |
| **U32** | 未测 | **已核** | 被测对象就是 `Test-ChainNoReparse` 的盘根保留，含反向控制 |
| **U28** | 部分已核（仅消费闸侧） | **已核（两侧闭合）** | 扫描器侧由 C3 补上；闸侧已由 S0b `NEGCASE RC=8` 覆盖 |

**这不会洗掉任何单列项**：`U25` / `U29` 的真实 ACL 分支**不在本驱动内**（仍部分已核）；
62 GB 成本、T041、11-17 原闸**完全不受本轮影响**。

## 九、待审点（请审核后指定是否放行运行）

1. **抽取 pin 是否认可**：749 / 1384 / 2110 B 三个 sha256 是否与你侧核对值一致。
2. **用例集是否够**：C1×5、C2×3、C3×6 判决断，共 10 个用例、14 条判决，有无需要加/减。
3. **C3 正向控制根**用 `$evDir` 是否可接受（备选：另建空目录 `run-<stamp>\ctrl`）。
4. **`U25` / `U29` 的扫描器侧**要不要一并做——那需要造 ACL 拒绝目录，**超出本轮边界**，需另批。

**在收到放行前，我不会运行它。**
