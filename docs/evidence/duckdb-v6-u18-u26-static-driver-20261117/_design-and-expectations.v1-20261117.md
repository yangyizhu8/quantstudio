# U18 / U26 最小候选设计（静态件 · 本轮不执行）

编制：2026-11-17（节点⑲，DRIFT-N19-r7）｜性质：设计与 pin 交付，等令再跑

> **本轮边界**：不执行新验证；不 ACL／junction／环境复制／62 GB／生产升级／清 flag／客户下发／提交推送。
> **不重跑已收的 15 条**（r6 那批已 PASS，不为展示参数再跑）。
> 索引**保持 4 已核／2 部分／26 未测**——本轮不因「设计了候选」而改任何状态。

## 〇、一句结论

两项都能**在不复制解释器环境、不碰生产 data 目录**的前提下验证：
U18 用**合成的中文路径目录**复现写入链；U26 用**合成 keep-pin 文件**驱动留存比较逻辑。
**共 2 组 / 14 条判决断言**（其中 4 条是对照/反例，已含在 14 内）。

## 一、为什么能不复制环境（先讲清，否则像取巧）

| 项 | 通知原做法 | 本设计替代 | 是否等价 |
|---|---|---|---|
| U18 中文路径 | `$dataDir` 落在中文路径 | 合成 `…\run-<stamp>\中文目录\data`，**同一条 Python 写入链、同一 `open(...,encoding='utf-8')`** | **等价**（被测的是写入/回读链，不是 data 目录本身） |
| U26 三值比较 | 复制 `pykeep-*` 后用**复制的解释器**重新采集 | 用**当前解释器**产出的 pin 作源，再**人为篡改**成留存 pin，喂给**同一段比较代码** | **部分等价**（见下） |

> **必须诚实标注的部分等价**：U26 原用例的目标是「复制完成后手工篡改 ⇒ 应拒绝」。
> 本设计能覆盖**篡改检测与比较逻辑**（8a 三值、8b 相等三项、8c 归属三项），
> 但**不覆盖**「复制产物本身是否可用」（那需要 robocopy + 复制出的解释器可运行，属禁止的环境复制）。
> 故本轮只关闭 U26 的**比较链路**，不关闭它的留存独立性前提——后者仍归 U13–U17。

## 二、抽取（边界 token 定位，**不用行号**）与 pin

| 段 | 起始 token | 终止 token | 落点 |
|---|---|---|---|
| `PY_COLLECT` | `^\$PY_COLLECT = @'` | 整行 `'@` | L366..L374（取两围栏之间） |
| `PY_RT` | `^\$PY_RT = @'` | 整行 `'@` | L396..L401（取两围栏之间） |
| `CHK_PARSERS`（8a） | `^\$CHK_FILE_HEX = \(\(\$chkRtOut` | 以 `PY-KEEP-HASHDIGEST` 开头 | L546..L552 |
| `EQ_ATTR`（8b+8c） | `^# 8b 相等三项` | `^# ---- \(9\) 落证` | L558..L577 |

```text
name	first_line	last_line	lines	bytes	sha256
PY_COLLECT	366	374	9	545	223231A658589E11C1449D465449C7FF62DA07120F35697AA622D710A56DD945
PY_RT	396	401	6	271	C0E57F7C8A47C3FF38D0FB7640F16CE3DD5B365BB4AD8C30EE7D5B828D080A8F
CHK_PARSERS(8a)	546	552	7	723	9545FF90F59F8D7C3E8449051FD5BC14C9B4A2561EA9FC8448E8DC4BB62C82EB
EQ_ATTR(8b+8c)	558	577	20	1498	18AF80E87BB2B67130F7532EA1EE8ECEBF3EA6DDB529CD8A77894F2F09CE6CBE
```

> 另需 `Test-ChainNoReparse`（8c 依赖）——沿用 r5 已 pin 的抽取：L338..L361、1384 B、
> `8B725BA145C6D0EB9EB0796922F604FA9AF86C71272E34C9267FFA84D61C486B`（同一通知，本次未变）。

## 三、静态读码发现的一处**实现顺序与用例描述不符**（必须先说）

通知 §2.4.0(8a) 的四条判定**有先后顺序**，实抽取内容如下：

```powershell
$CHK_FILE_HEX = (($chkRtOut | Select-String '^FILE-HEX').Line -split '\s+')[1]
$CHK_PFX_HEX1 = (($chkRtOut | Select-String '^PFX-HEX').Line -split '\s+')[1]
$CHK_FIELDS   = (($chkRtOut | Select-String '^FIELDS').Line -split '\s+')[1]
if (-not $CHK_FILE_HEX -or -not $CHK_PFX_HEX1 -or -not $CHK_FIELDS) { throw "留存对拍输出不完整，拒绝继续：$chkRtOut" }
if ($CHK_FILE_HEX -ne $CHK_META_HEX) { throw "留存 FILE-HEX != 其 META-HEX（写入编码/截断损坏），拒绝继续。" }
if ($CHK_PFX_HEX1 -ne $CHK_PFX_HEX0) { throw "留存 prefix 字段对拍不一致，拒绝继续。" }
if ($CHK_FIELDS -ne "duckdb,duckdb_file,exe,prefix,python,source_id") { throw "留存身份字段不齐：$CHK_FIELDS" }
```

| 用例表 U26 原文 | 按原样执行会发生什么 |
|---|---|
| 「改 prefix 值 ⇒ 留存 prefix 字段对拍不一致」 | **不会出现该消息**。改 prefix 值同时改了文件字节 ⇒ 第 5 行 `CHK_FILE_HEX -ne CHK_META_HEX` **先抛**「留存 FILE-HEX != 其 META-HEX」，prefix 支路根本到不了 |
| 「删一个字段 ⇒ 留存身份字段不齐」 | 同上，**先被 FILE 检查拦下** |

⇒ 这**不是实现缺陷**（fail-closed 先拦更严重的那条是对的），但**用例描述与代码顺序不匹配**。
**本设计的处置**：负例分两种模式跑——

| 模式 | 做法 | 证明什么 |
|---|---|---|
| **整链模式** | `$CHK_META_HEX` 保持原始值 | 真实场景：篡改**一定被拦**（拦在哪一条，记录在案） |
| **隔离模式** | 把 `$CHK_META_HEX` 对齐到**篡改后的** `FILE-HEX`，使目标支路成为首个失败点 | 该支路**自身有鉴别力**，不是靠上一条兜住 |

> 只有两种模式都跑，三值比较真生效才算证到：整链证明**会被拦**，隔离证明**每条支路都真的在判**。

## 四、精确合成输入（全部落在本次新目录内）

设 `RUN = docs\evidence\duckdb-v6-u18-u26-run-<stamp>`（**排他创建**），`CN = RUN\中文目录\data`。

| 文件 | 怎么造 | 用途 |
|---|---|---|
| `CN\pypin-<stamp>.json` | `PY_COLLECT` 原样执行（`open(...,'w',encoding='utf-8',newline='\n')`） | U18 正例：中文路径写入+回读 |
| `CN\pypin-<stamp>.mut-byte.json` | 复制上件，把 `python` 的值 `3.11.9` 改为 `3.11.8`（**改 1 字节**且 JSON 仍合法） | U26 反例：FILE 变、JSON 仍可解 |
| `CN\pypin-<stamp>.mut-pfx.json` | 复制原件，仅在 `prefix` 值末尾追加 `X`（JSON 合法） | U26 反例：PFX 支路（隔离模式） |
| `CN\pypin-<stamp>.mut-fields.json` | 复制原件，删掉 `duckdb_file` 键 | U26 反例：FIELDS 支路（隔离模式） |
| `CN\pypin-<stamp>.ascii.json` | 用 `encoding='ascii'` 写同一内容 | U18 反例：应抛 `UnicodeEncodeError` |
| `CN\pypin-<stamp>.ensureascii.json` | 用 `ensure_ascii=True` 写同内容 | U18 鉴别力：FILE 变、PFX 值不变 |

**合成输入的性质**：`python` 版本串与 `prefix` 值都**不是伪造的**——它们来自真实环境；
被篡改的只是**文件字节**，这正是 U26 要测的东西（篡改检测），不是伪造观测值。

## 五、命令（**Hidden**，按 r7 要求）

```powershell
powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass `
  -File <repo>\docs\evidence\duckdb-v6-u18-u26-static-driver-20261117\_driver.candidate.ps1
```

> `-WindowStyle Hidden` 已按 r7 落进命令；驱动内部再调 python 亦用 `Start-Process -WindowStyle Hidden`。
> **不提供重跑 15 条的开关**——r6 那批已收，本驱动不含其用例。

## 六、写域（先声明，越界即违规）

| 允许写 | 说明 |
|---|---|
| `RUN\`（排他创建，同秒加 `-2/-3…`） | 全部产物根 |
| `RUN\中文目录\data\*.json`（6 件，见 §四） | 合成输入与篡改件 |
| `RUN\_pyid_collect.py` / `_pyid_check.py` / `_pathfuncs.ps1` / `_chkparse.ps1` / `_eqattr.ps1` | 抽取件（`.ps1` 带 BOM；`.py` 无 BOM） |
| `RUN\stages.tsv` / `cases.tsv` / `verdict.txt` / `hashes.tsv` | 回执 |

| **禁止写** | 理由 |
|---|---|
| `QuantStudio\data\**`（生产 data 目录） | 不在本轮授权内；U18 用合成中文目录等价覆盖 |
| `data\quantstudio.db` 及 62 GB 库、任何 `.wal` | 生产数据 |
| 任何 `pykeep-*`、`site-packages` | 属环境复制，禁止 |
| 任何 ACL／junction | 边界明令禁止 |

## 七、预期结果（14 条判决断言 / 2 组）

### 组 A · U18（中文路径 pin 无损）— 6 条

| # | ID | 输入 | 判据 | 预期 |
|---|---|---|---|---|
| 1 | `U18-FILE-EQ-META` | `pypin-<stamp>.json` | `FILE-HEX == META-HEX` | 相等 |
| 2 | `U18-PFX-EQ` | 同上 | `PFX_HEX1 == PFX_HEX0` | 相等 |
| 3 | `U18-FIELDS-EXACT` | 同上 | `FIELDS` 等于六字段串（有序） | 精确相等 |
| 4 | `U18-PREFIX-CN-EXACT` | 同上 | PS `ReadAllText(UTF8)` + `ConvertFrom-Json` 得到的 `prefix` **逐字符**等于 python 的 `sys.prefix`，且含 CJK、**不含问号** | 逐字符相等且含中文 |
| 5 | `U18-ASCII-WRITE-REJECT` | `encoding='ascii'` 写中文路径 | 必须抛 `UnicodeEncodeError` | 抛错（反例） |
| 6 | `U18-FILE-VS-PFX-SPLIT` | `ensure_ascii=True` 写同内容 | `FILE-HEX` **变**、`PFX` 值 **不变** | 一改一不变（鉴别力分离） |

### 组 B · U26（留存三值比较真生效）— 8 条

| # | ID | 模式 | 输入 | 判据 | 预期 |
|---|---|---|---|---|---|
| 7 | `U26-POS-TRIPLE` | 正例 | `pypin-<stamp>.json` | 8a 解析块**不抛** | 通过 |
| 8 | `U26-NEG-FILE` | 整链 | `mut-byte.json`，META 用原值 | 抛「留存 FILE-HEX != 其 META-HEX」 | 拦下 |
| 9 | `U26-NEG-PFX-ISO` | 隔离 | `mut-pfx.json`，META 对齐篡改后值 | 抛「留存 prefix 字段对拍不一致」 | 拦下 |
| 10 | `U26-NEG-FIELDS-ISO` | 隔离 | `mut-fields.json`，META 对齐 | 抛「留存身份字段不齐」 | 拦下 |
| 11 | `U26-EQ-TRIO-NEG` | 8b | `$chkObj.python` 改为 `3.11.8` | 抛且消息含 `python:` | 拦下 |
| 12 | `U26-EQ-TRIO-POS` | 8b | 三值与原相同 | **不抛** | 通过 |
| 13 | `U26-ATTR-POS` | 8c | `$keepRoot` = 真实 `sys.prefix` | 三项归属全过（exe/duckdb_file 在其下、prefix 相等） | 通过 |
| 14 | `U26-ATTR-NEG` | 8c | `$keepRoot` = `RUN\假留存根` | 抛「解释器 exe 不在留存目录内」 | 拦下 |

**分母口径（不与调用数混淆）**：**分组数 = 2**，**判决断言数 = 14**，
预计 **Python 进程调用约 8 次**（collect×1、rt×4、ascii×1、ensure_ascii 写+rt×2）——**调用数不等于断言数**。

## 八、判定规则（沿用 r6 已审骨架）

| 规则 | 内容 |
|---|---|
| 内容 pin | 四段 sha256 逐位相等，否则 `EXTRACT-PIN-*` FAIL（见 §二表） |
| 输出根 | 排他创建（`New-Item` 无 `-Force`）＋同秒 `-2/-3…`；祖先无 reparse |
| 身份 | 断言 PS 5.1；记录 python exe/版本/sha256 并与 S0b `5F7B89A6…52EC` 比对 |
| 子进程 | `Start-Process -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput/-RedirectStandardError`，原样留 stdout/stderr/rc |
| 失败 | 共用 `Save-Evidence`；`plan` 预登记 14 条，未跑标 `NOT-REACHED` |
| 退出码 | PASS=0／FAIL=1／证据写不出=2 |
| 首失败 | **保现场、不改源码、不换 stamp 求绿** |

## 九、本轮**未做**（边界）

未执行本设计 ✓｜未复制环境 ✓｜未 ACL／junction ✓｜未触 62 GB／生产库 ✓｜未清 flag ✓｜未恢复供血 ✓｜未下发 ✓｜未提交推送 ✓｜未重跑 r6 的 15 条 ✓

**索引保持 4 已核 / 2 部分 / 26 未测**；`T041` 仍 `BLOCKED`；11-17 原闸保持，**临期不自动放宽或延期**。

## 十、待审点

1. §三那处「实现顺序 vs 用例描述」的处置（整链＋隔离双模式）是否认可；
2. U26 **只覆盖比较链路**、不覆盖留存独立性的边界划分是否接受；
3. 合成中文目录名用 `中文目录` 是否够——**建议再加一层含空格的目录**（如 `中文 目录`）以覆盖空格+中文组合，请指示；
4. 是否同意本设计的 14 条就是下一轮的**全部**执行范围（不顺手扩到其它 24 项）。
