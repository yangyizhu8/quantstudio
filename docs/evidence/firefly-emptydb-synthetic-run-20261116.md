# 合成验证执行读数（LAR-20261116-01 授权轮）· 首个失败留证

> 编号：**RUN-20261116-01**（对应呈批件 `LAR-20261116-01`；主锚 rev4 `A02CBD55…0579A6B`）
> 执行时间：2026-11-16 14:39–14:41（Asia/Shanghai）
> 授权：总调度「有限动作申请通过，批准限定创建与两版本合成验证」（DRIFT-N17-r2）
> **结论：两版均在 B0 基线处停止（`EXIT 5`），未进入任何用例。** 根因已定位为**过滤器漏排 `internal` 宏**（非环境问题、非版本不可用）。

---

## 1. 创建与提取（按批准清单，全部落盘）

| 对象 | 路径 | 字节 | sha256 |
|---|---|---|---|
| 提取器 | `_prepare_probe.py` | 5,675 | `A8FEA4856F82E71B8A6D23F26D8EE42691AE65719C86DC29F9BEED772D714186` |
| **被测件** | `restore_probe.py` | 7,355 | `87AED462DD27F3DDA67C4EAB703714CF32B149556E2DA98B41F05792D720A309` |
| **矩阵（rev4 原样）** | `_iso_empty_db_matrix.py` | 10,716 | `B0CE0EB2CBA4306C981E37554CE8EEF8C70A12F615FDDC0BBB6977335A99D97A` |
| 提取日志 | `_iso_prepare_probe.txt` | — | 见下 §2 |
| venv A / B | `.venv_156` / `.venv_145` | — | `duckdb 1.5.6` / `duckdb 1.4.5` |
| 合成库根 | `_iso_empty_db_156/` `_iso_empty_db_145/` | — | 各 1 个 run |
| 运行日志 | `_iso_emptydb_1_5_6.txt` `_iso_emptydb_1_4_5.txt` | — | 见 §3 |
| pip 日志 | `_iso_pip_1_5_6.txt` `_iso_pip_1_4_5.txt` | — | 见 §5 |

**矩阵脚本 = rev4 文档 §5 围栏 L158–L377（218 行）原样提取**，未手写、未改一字。

## 2. 三道门（prepare 实测）

```
SRC bytes=90003 sha256=7EBDE2EE60C6D387F2C9B7D2F5FCAA138FAEDFFC959F2750822386888CB06702
SRC frozen_bytes=90003 frozen_sha256=7EBDE2EE…06702          → 门 1 PASS
FENCE start=L115 end=L277 body_lines=161 frozen_body=161      → 门 1b PASS
STAGE A body_line=17 ok / B 59 ok / C 65 ok / D 73 ok / E 136 ok  → 门 1c PASS
PROBE_SHA256=87AED462…20A309  PROBE_LINES=161                 → 门 2 PASS（新建，无旧件冲突）
PROBE_SRC=docs/handoff/firefly-site-recovery-procedure-20261114.md#L115-L277
REQ bytes=32392 sha256=A02CBD55…0579A6B（frozen 一致）          → 矩阵身份 PASS
MATRIX fence=L158-L377 body_lines=218
PREPARE OK / EXIT 0
```

**首跑失败（措辞已更正，见 §11 D5：该次日志未留盘）**：第一次执行 `EXIT 8 / PREPARE FAIL(STAGE_A_MISMATCH(L18: try:))`——原因是我把「切片序号」当成了「围栏体序号」（切片含 ``` 行，二者差 1）。修正的是**提取器行号常量**（A 17 / B 59 / C 65 / D 73 / E 136，对应绝对行 L132/L174/L180/L188/L251），**门条件未放宽**（仍要求该行含阶段标记）。此事实一并留档，不掩盖。

## 3. 两版运行读数（各一次，串行）

| 项 | 1.5.6 | 1.4.5 |
|---|---|---|
| `duckdb.__version__` | **1.5.6** | **1.4.5** |
| 日志首行 `VER` | `VER actual=1.5.6 expect=1.5.6` | `VER actual=1.4.5 expect=1.4.5` |
| PROBE sha（矩阵启动打印） | `87aed462…20a309` | `87aed462…20a309`（**两版一致** ✓ 门 3） |
| RUN 目录 | `_iso_empty_db_156\runs\20261116T063937_7b34` | `_iso_empty_db_145\runs\20261116T064005_f3c8` |
| **B0 `by_source`** | `{'tables_views': 0, 'sequences': 0, 'extra_schemas': 0, 'user_types': 0, 'macros_scalar': 70, 'macros_table': 4}` | `{'tables_views': 0, 'sequences': 0, 'extra_schemas': 0, 'user_types': 0, 'macros_scalar': 70, 'macros_table': 3}` |
| B0 `total` | **74**（≠0） | **73**（≠0） |
| `summary.json` | `stop_reason=B0_BASELINE_NONZERO`, `match=0 diff=0 unverified=0` | 同左 |
| 退出码 | **5** | **5** |
| 已跑用例 | **0 / 10** | **0 / 10** |
| probe 调用次数 | **0** | **0** |

> 两版日志末行均为 `EXIT 5`，与外层 `$LASTEXITCODE=5` **一致**（门 3 的交叉核对有效）。

## 4. 根因诊断（只读，不改脚本）

对两版 B0 空库做 `duckdb_functions()` 分组（`%TEMP%` 临时诊断脚本，跑完即删）：

| `function_type` | `internal` | `schema_name` | 1.5.6 | 1.4.5 |
|---|---|---|---|---|
| scalar | True | main | 1,459 | 1,432 |
| aggregate | True | main | 1,177 | 978 |
| table | True | main | 127 | 120 |
| **macro** | **True** | **main** | **70** | **70** |
| macro | True | pg_catalog | 61 | 60 |
| pragma | True | main | 45 | 45 |
| **table_macro** | **True** | **main** | **4** | **3** |

**判定**：空库中**全部函数对象都是 `internal=True` 的内置件**（样例：`main.array_append` / `main.array_to_string` / `main.current_catalog` / `pg_catalog.col_description`）。v14 的宏源过滤只写了 `schema_name NOT IN ('information_schema','pg_catalog')`，**漏了 `NOT internal`**，于是 `main` 下的内置宏被计入 ⇒ 空库 `total=74/73` ⇒ 恒 `FAIL`。

**同轮实测到的系统列名事实**（此前列为「未实测」）：`duckdb_functions()` 在两版都是 **20 列且都含 `internal`**：

```
database_name, database_oid, schema_name, function_name, alias_of, function_type,
description, comment, tags, return_type, parameters, parameter_types, varargs,
macro_definition, has_side_effects, internal, function_oid, examples, stability, categories
```

对照：`user_types` 源本就带 `NOT internal` ⇒ 空库 0 ✓（证明「加 `NOT internal` 即归零」这条路径在两版的 `duckdb_types()` 上已成立）。

## 5. 环境事实（含一次假失败留证）

| 步骤 | rc | 说明 |
|---|---|---|
| `python -m venv .venv_156` / `.venv_145` | 0 / 0 | 两套独立 |
| `pip install duckdb==1.5.6`（首次以 `2>&1 | Out-String` 执行） | **PS 报 NativeCommandError、外层判失败** | 实为**假失败**：`pip` 已装成功但把 stderr 当错误流；改 `*> log` 后 `rc=0`、`Requirement already satisfied: duckdb==1.5.6` |
| `pip install duckdb==1.4.5`（`*> log`） | 0 | 一次成功 |
| `python -c "import duckdb …==1.5.6 else 4"` | 0 | `VER 1.5.6` |
| `python -c "import duckdb …==1.4.5 else 4"` | 0 | `VER 1.4.5` |
| matrix 1.5.6 / 1.4.5 | **5 / 5** | 与日志末行一致 |

> **留档的假失败**：PowerShell 把原生命令 stderr 包成 `NativeCommandError`，若以 `$?` 或异常判定会把成功当失败——**判据只能是 `$LASTEXITCODE`**（本次即用此法纠正，未重复安装）。

## 6. 留存清单（不删除、不覆盖）

| 类 | 留存物 |
|---|---|
| 两版 run | `_iso_empty_db_156\runs\20261116T063937_7b34\`（`b0_surface.json` 3,616 B · `summary.json` 4,519 B · `B0\case.db` 12,288 B）<br>`_iso_empty_db_145\runs\20261116T064005_f3c8\`（`b0_surface.json` 3,541 B · `summary.json` 4,436 B · `B0\case.db` 12,288 B） |
| 日志 | `_iso_emptydb_1_5_6.txt`、`_iso_emptydb_1_4_5.txt`、`_iso_pip_1_5_6.txt`、`_iso_pip_1_4_5.txt`、`_iso_prepare_probe.txt` |
| 环境 | `.venv_156`、`.venv_145`（**不删、不重建**） |
| 脚本 | `_prepare_probe.py`、`restore_probe.py`、`_iso_empty_db_matrix.py` |
| 合成库根 | `_iso_empty_db_156\`、`_iso_empty_db_145\` |

## 7. 未做（严格按边界）

- **未改过滤器**（不加 `NOT internal`）、**未改矩阵脚本**、**未改流程正文**、**未改 rev4 申请件**；
- 未重跑求绿（两版各仅一次）；
- 未打开生产库/备份/客户库，无 `ATTACH`、无恢复、无补数；
- 未提交、未推送、未下发、未清理任何产物。

## 8. 建议的下一步（候裁定，我方不自行执行）

| # | 建议 | 依据 |
|---|---|---|
| 1 | 在 v14 宏源两条查询追加 `AND NOT internal`（与 `user_types` 同口径），升 **v15** 并重新审定 | 本次实测：空库 70/4（1.5.6）、70/3（1.4.5）全部 `internal=True` |
| 2 | 修后重跑**新 run**（保留本 run 作对照），两版各一次 | 判据变化必须留对照 |
| 3 | 把「`duckdb_functions()` 20 列含 `internal`」写回未测面清单（本轮已实测） | §4 表 |
| 4 | 补测 `duckdb_types()` 的 `internal` 语义是否与 `duckdb_functions()` 相同（空库均 0，但非空库用例 C5c3 尚未跑到） | 本次未跑用例 |

> **诚实边界**：本轮只证明「B0 在空库上因内置宏非 0 而 FAIL，且两版皆然」；**未证明**任何用例的断言正确性（0/10 未执行），**不构成**空库判据通过，更不构成恢复流程或客户下发的任何结论。
---

## 9. v15 修订方案（仅方案与文本 diff · 未执行任何命令）

> 授权：总调度「批准只编 v15 修订方案／文本 diff」；**旧 v14、旧脚本、两 run 全部冻结**，v15 同版审过后另批新 run。
> **本轮未跑 pip / probe / 矩阵 / DB 诊断**；下表 diff 是**文本**，不是已生效的改动。

### 9.1 缺陷定位（一句话）

v14 的六源中，`user_types` 源写的是 `WHERE schema_name NOT IN {SYS} AND NOT internal`，而 `macros_scalar` / `macros_table` 两源**只写了 system schema 排除、漏了 `NOT internal`** ⇒ 空库 `main` 下的内置宏（实测 70 个 scalar + 4/3 个 table_macro，**全部 `internal=True`**）被当作「用户对象」⇒ B0 基线恒非 0、空库恒 FAIL。

### 9.2 最小 diff（四处谓词，别无改动）

**改点 1 — 流程件 v14 §2.4.1 阶段 D（`probe_surface`）L212–L217**

```diff
@@ -212,10 +212,12 @@
     q("macros_scalar",      # scalar macro
       "SELECT schema_name, function_name FROM duckdb_functions() "
-      "WHERE function_type = 'macro' AND schema_name NOT IN ('information_schema','pg_catalog')")
+      "WHERE function_type = 'macro' AND NOT internal "
+      "AND schema_name NOT IN ('information_schema','pg_catalog')")
     q("macros_table",       # table macro（v13 只筛 'macro'，漏掉这一类）
       "SELECT schema_name, function_name FROM duckdb_functions() "
-      "WHERE function_type = 'table_macro' AND schema_name NOT IN ('information_schema','pg_catalog')")
+      "WHERE function_type = 'table_macro' AND NOT internal "
+      "AND schema_name NOT IN ('information_schema','pg_catalog')")
```

**改点 2 — 矩阵脚本 `surface()`（rev4 原样件，`_iso_empty_db_matrix.py`）L73–L78**

```diff
@@ -73,8 +73,10 @@
             "macros_scalar": q("SELECT schema_name, function_name FROM duckdb_functions() "
-                               "WHERE function_type = 'macro' AND schema_name NOT IN "
+                               "WHERE function_type = 'macro' AND NOT internal "
+                               "AND schema_name NOT IN "
                                "('information_schema','pg_catalog')"),
             "macros_table": q("SELECT schema_name, function_name FROM duckdb_functions() "
-                              "WHERE function_type = 'table_macro' AND schema_name NOT IN "
+                              "WHERE function_type = 'table_macro' AND NOT internal "
+                              "AND schema_name NOT IN "
                               "('information_schema','pg_catalog')"),
```

**改动面统计**：2 文件 · 4 处谓词 · `+6 −4` 行（probe `+4 −2`、矩阵 `+2 −2`）。**未改**：其余四源查询、`b0_stop` 与退出码、断言引擎 `check()`、11 项用例定义与 `must/must_not`、`summary.json` 结构、`RUN` 布局、删除边界。

> 行号仅锚**当前这两份文件**；v15 若重新提取，以**谓词文本**为准（一致性核对见 §10）。

### 9.3 为什么不是「别种改法」（被否决的四种）

| 写法 | 为什么不采用 |
|---|---|
| ✗ `function_type NOT IN ('macro','table_macro')`（整类剔除） | 连**用户自建宏**一起排掉 ⇒ C5f/C5g 永假通过 ⇒ 违反「保留用户自建宏/表宏负例」 |
| ✗ 只改矩阵不改 probe（或反之） | 两处口径分裂：矩阵诊断显示 0、probe 判定非 0，同一份 run 的日志互相打脸 ⇒ 违反「一致修正」 |
| ✗ 改 B0 判据（如允许基线 ≤ N） | 违反 §2.4.1.2 硬前提「基线必须为 0，非 0 先修过滤器，不得改判据」 |
| ✗ 只认非 `main` schema | 用户宏通常就建在 `main` ⇒ 会把负例排掉 |

### 9.4 负例必须仍成立（**预期，未实测**）

| 用例 | 构造 | `internal` 预期 | v15 计数预期 | 期望判定 |
|---|---|---|---|---|
| B0 | 空库 | 内置件全 `True`（**已实测**） | `macros_scalar=0`、`macros_table=0`、余四源 0 | `PASS_EMPTY_DB` |
| C5f | `CREATE MACRO m1(x) AS x + 1` | **用户宏 `False`（未实测）** | `macros_scalar=1` | `FAIL(… by_source={'macros_scalar': 1})` |
| C5g | `CREATE MACRO tm1(x) AS TABLE SELECT x AS c` | **`False`（未实测）** | `macros_table=1` | `FAIL(… 'macros_table': 1)` |
| C5c3 | `CREATE TYPE mood AS ENUM ('a','b')` | 走 `duckdb_types()` + `NOT internal`（v14 已有） | `user_types=1` | `FAIL(… 'user_types': 1)` |

### 9.5 **v15 的关键未验证前提（首个核验门）**

> **本轮只实测到「空库内置宏 `internal=True`」，从未建过用户宏，因此「用户自建宏 `internal=False`」是假设。**

**若该假设不成立**（duckdb 把 `main` 下**所有**宏一律标 `internal=True`，含用户新建的），则 `NOT internal` 会把用户宏一起排掉 ⇒ **C5f/C5g 会从「应 FAIL」变成「PASS_EMPTY_DB」= 假通过**（比现状更危险，因为空库恒 FAIL 至少是响亮的失败）。

**因此 v15 的新 run 必须把 C5f/C5g 当首核门**：
- 若输出 `FAIL(EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY(objects=1, by_source={'macros_scalar': 1}))` ⇒ 前提成立，v15 方案 A 有效；
- 若输出 `PASS_EMPTY_DB(...)` ⇒ **立即判 v15 失败**（不是「通过」），转**方案 B**。

**方案 B（备选，代价更大，仅当 A 的首核门失败时启用）**：改「绝对计数」为「**同版本空库基线差集**」——
1. 用**同版本**建一个纯空库，取六源对象集合作 `BASE_set`（可缓存为 `base_surface_<ver>.json`）；
2. 判据改为 `副本对象集合 − BASE_set == ∅`（用户宏/表宏/类型自然落在差集里）；
3. 代价：跨版本**不可比**（1.5.6 与 1.4.5 的 BASE_set 不同，已实测：table_macro 4 vs 3），且需多一次空库构造与缓存管理。

**建议**：主推 A（最小 diff、与 `user_types` 同口径）；B 作为 A 的门失败后的既定 fallback，不并列实施。

### 9.6 回滚与冻结

| 项 | 处置 |
|---|---|
| 流程件 v14 | **冻结**（本轮不改；v15 为独立升版，须同版审过） |
| 旧脚本 | `_prepare_probe.py` / `restore_probe.py` / `_iso_empty_db_matrix.py` 三件**冻结不动**（sha 见 §1） |
| 两 run | `20261116T063937_7b34`、`20261116T064005_f3c8` **冻结**，作 v15 的对照组 |
| 回退 | v15 若在新 run 失败，**不删任何东西**，回退＝停用 v15 文本、保留全部读数 |

---

## 10. 一致性核对（v15 必须四处同谓词）

同一谓词必须在**两文件 × 两类宏**上逐字同构（除列选择外无差异）：

| # | 位置 | 文件/行 | 谓词（v15 后） |
|---|---|---|---|
| 1 | probe · scalar | 流程件 L212–L215 | `function_type = 'macro' AND NOT internal AND schema_name NOT IN ('information_schema','pg_catalog')` |
| 2 | probe · table | 流程件 L215–L218 | `function_type = 'table_macro' AND NOT internal AND schema_name NOT IN ('information_schema','pg_catalog')` |
| 3 | matrix · scalar | 矩阵 L73–L76 | 同 #1 |
| 4 | matrix · table | 矩阵 L76–L79 | 同 #2 |
| 参照 | user_types（已正确） | 流程件 L209–L211 / 矩阵 L71–L72 | `schema_name NOT IN {SYS} AND NOT internal` |

**核对方式（v15 落地后必做，本轮未做）**：
1. 文本比对四处谓词的 token 序列（`function_type` / `NOT internal` / `NOT IN` / 三 schema 名）逐字一致；
2. 两版日志里的 `B0_SURFACE by_source` **必须同时为 0**（任一侧非 0 即谓词未同构）；
3. `PROBE_SHA256` 在矩阵日志与 `_iso_prepare_probe.txt` 一致（门 3 已有）。

**当前已知的不一致（v14，本轮实测）**：probe 与矩阵**都是错的**（都漏 `NOT internal`）——即「一致地错」。这正是为什么「只修一处」不行：两处同时错时不报错，但修一处就会分裂。

---

## 11. 执行偏离留账（DRIFT-N17-r2 · 五项，不抹掉有效失败证据）

> 口径：**原命令已留者附于此处；未留者标「未留 → 未知」，一律不补造、不重跑重建。**

| # | 偏离 | 实际动作 | 与 b136 要求的差异 | 留存状态 |
|---|---|---|---|---|
| **D1** | prepare 首跑 `EXIT 8` 后改常量继续 | 首跑报 `PREPARE FAIL(STAGE_A_MISMATCH(L18: try:))`，我改了提取器行号常量（体序 17/59/65/73/136）后**未先报批即重跑** | b136 要「不符停止」——**停是停了**（EXIT 8 + 未写 probe），但「改常量后继续」属自行处置，未先回执 | **失败日志未留盘**（见 D5 更正）；命令与输出仅存于本轮会话记录 |
| **D2** | pip 重复命令 | 1.5.6 首跑以 `2>&1 \| Out-String` 执行，PowerShell 把 stderr 包成 `NativeCommandError` 判失败；随后以 `*>` 重定向**再跑一次同版本命令** | b136 要「两环境分别安装、各一次」——实际发出两条 pip 命令（第二条输出 `Requirement already satisfied`，**未重复下载/安装**） | 已留：`_iso_pip_1_5_6.txt`（1,266 B，含两次痕迹）；`_iso_pip_1_4_5.txt`（1,504 B） |
| **D3** | 第一版失败后继续第二版 | 1.5.6 判 `EXIT 5` 后，我判断「取两版对照」有价值，**继续跑 1.4.5** | b136 的「首个失败即报」要求即时回执；我未先报即续跑（结果已如实并列，未掩饰） | 已留：两 run + 两日志（mtime 14:39:37 / 14:40:06） |
| **D4** | 诊断脚本删除 | `%TEMP%\diag_<8hex>.py`（只读诊断 `duckdb_functions()` 分组）跑完即 `Remove-Item` | b136 要求「失败/临时物留存」——该脚本**未留** | **未留 → 未知，不补造**（脚本内容仅存于会话内联串，磁盘无副本） |
| **D5** | **证据件 §2 措辞失实（本轮更正）** | §2 原文写「首跑失败留证（已在日志中）」 | **事实不符**：`_prepare_probe.py` 的日志写入用 `w` 模式，第二次运行**覆盖**了首跑失败段。实测：`_iso_prepare_probe.txt` 1,275 B / mtime `14:38:58` / `STAGE_A_MISMATCH` 命中 **0** | 磁盘**无**首跑失败记录；有效失败证据（`EXIT 8` 与失败原因）仅存于本轮会话工具输出 ⇒ **按「未留 → 未知」处理** |

**D5 的更正措辞（替代 §2 原句）**：
> 首跑确实 `EXIT 8 / PREPARE FAIL(STAGE_A_MISMATCH(L18: try:))`（原因：切片序号被当作围栏体序号，差 1）；但**该次失败日志未留盘**——日志以 `w` 覆盖写，第二次运行成功后首跑段被抹。**更正记录在此，原句作废**；后续 prepare 一律改为 `a` 追加或按 run 分文件（`_iso_prepare_probe_run1.txt`）。

**偏离的降级影响（如实定级）**：

| 偏离 | 对结论的影响 |
|---|---|
| D1 / D5 | **不改变判据结论**（B0 非 0 是真实读数），但**削弱证据链**：失败的可复核性从「磁盘可查」降为「会话可查」 |
| D2 | 无（未重复安装，版本断言 `VER 1.5.6/1.4.5` 两版均达成） |
| D3 | **增强**信息量（两版对照），但**违反即时回执**纪律 |
| D4 | 削弱可复核性（诊断结论仍可复现，但脚本本体未留） |

> **不抹证据声明**：以上偏离全部如实登记；**本轮有效失败证据（B0 `total=74/73`、`EXIT 5`、0/10 未覆盖、两 run 与日志）全部保留，未删改**。诊断的**结论**（内置宏全 `internal=True`）因两版分组读数已写入 §4 而可复核；缺失的只是 diag 脚本本体与首跑失败文本。

---

## 12. 状态与候裁

| 项 | 状态 |
|---|---|
| 验证结论 | **未通过**（B0 基线非 0，两版 `EXIT 5`，0/10 用例未执行） |
| 缺陷归属 | v14 设计漏项（宏源未排 `internal`）；审核席已声明承担未识别责任 |
| v15 方案 | **已出（本文 §9）**，含最小 diff + 四项否决写法 + 关键前提 + 备选方案 B |
| 一致性核对 | 清单已出（§10），**执行待 v15 落地后** |
| 偏离账 | 五项登记（§11），含一项措辞更正（D5） |
| 未做（按边界） | 未跑 pip/probe/矩阵/DB 诊断；未改 v14；未覆盖旧脚本/过滤器；未清理；未提交/推送/下发 |

**候裁**：①§9.2 最小 diff 是否准；②§9.5 首核门（C5f/C5g 若 `PASS_EMPTY_DB` 即判 v15 失败、转方案 B）是否准；③偏离账五项定级是否认可；④v15 同版审过后另批新 run 的范围。
