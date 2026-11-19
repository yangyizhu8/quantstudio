# r4 候选 runner 静态核验记录

生成时间：2026-11-16（Asia/Shanghai）
性质：**静态候选**，本轮**未运行**（不跑 HOST / Python / 扫描 / 负控，不建 D 盘根，不清旧现场）

## 一、身份

| 项 | 值 |
|---|---|---|
| 基线（r3 失败原件） | `docs/evidence/duckdb-s0b-20261116T154753/_runner.ps1` |
| 基线 bytes / sha256 | 13080 / `8DB0F2AC535B0B0F0F50B2903D0E7C004DDE6A5DEE0C4121F1805DA00B134C59` |
| 候选 | `docs/evidence/duckdb-s0b-runner-r4-candidate-20261116/_runner.candidate.ps1` |
| 候选 bytes / sha256 | 15374 / `DAFAB11E86C4671461B17893B923052CAC0AD64FB436337DE9D9CDAE8E8A24C0` |
| 候选 BOM | 有（EF BB BF） |
| diff 规模 | +51 / -12（共 105 行） |

## 二、三处修订（仅此三处，其余逐行未动）

### 修订 1 · 原生命令完整取输出 → 立即存 rc → 再对变量取首行

```powershell
# 基线（错）：原生命令内联进管道，rc 已被改坏
$pyVer = ((& $py -c "..." | Select-Object -First 1)).Trim()
if ($LASTEXITCODE -ne 0 -or -not $pyVer) { ... }

# 候选（改）：先完整取输出，紧接着存 rc，再从变量取首行
$pyVerOut = @(& $py -c "import sys;print(sys.version.split()[0])")
$pyRunRc  = $LASTEXITCODE
if ($pyRunRc -ne 0) { Mark 'HOST' "FAIL(py run rc=$pyRunRc)"; throw ... }
$pyVer = if ($pyVerOut.Count -ge 1) { ([string]$pyVerOut[0]).Trim() } else { '' }
if (-not $pyVer) { Mark 'HOST' 'FAIL(py ver empty)'; throw ... }
```

要点：`$LASTEXITCODE` **紧接原生命令**读取，中间不夹任何 cmdlet；首行从**已落定变量**取。
同时把「无输出」与「rc≠0」拆成两条独立判据（基线里两者 OR 在一起，归因不可分）。

### 修订 2 · 证据 bootstrap 先于 HOST 且排他

- `STAGE BOOTSTRAP` 移到 `STAGE HOST` **之前**；
- `if (Test-Path $outDir) { throw }` + `New-Item` **不带 -Force**（排他）；
- 建后**立即**落 `runner-identity.txt`（`runner_path` / `runner_sha256` / `runner_bytes` / `boot_stamp`）并复制 `_runner.ps1`；
- `SETUP-DIRS` 不再管 `$outDir`（已由 BOOTSTRAP 排他建好，避免二次建与「已存在」自撞）；
- bootstrap 失败 ⇒ `Mark 'BOOTSTRAP' FAIL` + **显式报告** + `exit 2`（不产生无证据读数）。

### 修订 3 · 首失败也统一落 verdict / stages / runner 身份

- 落证门从 `if (Test-Path $outDir)` 改为 `$proofDir = $script:evDir`；
- 无 `$proofDir` 时走 **`EVIDENCE-WRITE-IMPOSSIBLE` 显式报告分支**（打印 fail_class / fail_msg / stages），不再静默跳过；
- 落证失败 ⇒ `VERDICT-WRITE-FAILED（显式报告）`；
- 结尾 `if (-not $script:written) { exit 2 }`：**没落证不许以 0/1 退出**。

## 三、明确未改（逐字节比对 IDENTICAL）

- **GATE-BOM 段**：`IDENTICAL`
- **GATE-PARSE 段**：`IDENTICAL`
- **五值精确比较段**：`IDENTICAL`
- **六负控段**：`IDENTICAL`
- **基线恢复段**：`IDENTICAL`
- **扫描器提取段**：`IDENTICAL`
- **扫描运行段**：`IDENTICAL`
- **闸提取段（含 $utf8Bom）**：`IDENTICAL`

## 四、静态解析（PS 5.1 宿主自身 Parser，读盘、不执行）

```
宿主      = 5.1.26100.9711 / CLR 4.0.30319.42000
ParseFile = PARSE-OK（0 错误）
AST 节点  = 2601
exit 语句 = 4 处：L44 exit 2（bootstrap 失败）/ L284 exit 2（未落证）/ L285 exit 1 / exit 0
花括号    = { 104 / } 104（配平）
圆括号    = ( 220 / ) 220（配平）
```

## 五、判据自检（含明确比较符，避免「应 0 判成 >=1」之类反向）

| 判据 | 读数 | 期望 | 结论 |
|---|---|---|---|
| 基线含管道内联原生命令（缺陷样本） | 1 | == 1 | OK |
| 候选含管道内联原生命令 | 0 | == 0 | OK |
| 候选 `-First 1)).Trim()` 残留 | 0 | == 0 | OK |
| 候选 `$pyRunRc = $LASTEXITCODE` | 1 | == 1 | OK |
| 候选 首行取自变量 | 1 | == 1 | OK |
| 候选 BOOTSTRAP 块 | 1 | == 1 | OK |
| 候选 runner-identity 落盘 | 1 | == 1 | OK |
| 候选 finally 用 `$proofDir` | 3 | >= 2 | OK |
| 候选 落证不可能显式分支 | 1 | == 1 | OK |
| 候选 未落证 exit 2 | 1 | == 1 | OK |
| 候选 finally 内 `$outDir` 作门 | 0 | == 0 | OK |

**自检总计：全部通过**

## 六、本轮未做（边界）

- 未运行候选（HOST / Python / 扫描 / 六负控 全部未执行）；
- 未新建任何 `D:\qssyn_s0b_*` 根；
- 未清理旧现场（`20261116T143939` / `20261116T154753` 原样保留）；
- 未升级、未生产验证、未下发、未提交。

## 七、待批

准确源核过后，请批准**一次新 stamp 完整 S0b**（修 runner 与验 BOM 在同一次完整链内完成）。
**注意：r3 的一次已用尽**，不因上次停在 HOST 而视为未用。
