# 流程件 v15 合并说明（DRIFT-N17-r5 · 仅文档）

> 授权：DRIFT-N17-r5「合并既有流程件 v15，只纳已审四谓词、正确 probe 路径、双版本结果及适用界；交完整 diff/hash，明确客户操作所依赖的剩余项」。
> 边界：**仅文档**。未 `prepare`、未 `pip`、未跑新 run、未 ATTACH 真实或备份库、未客户下发、未清理、未提交推送。

## 0. 本轮两处程序性偏离（先报）

| # | 偏离 | 处置 |
|---|---|---|
| **D-r5-1** | 上一轮在 145 实际执行后删除了自建占位文件 `validation-20261116T120602Z/145/_NOT_EXECUTED.txt`，**属「不清理」范围之外** | 文件为本轮状态标记（非证据、非产物）；两版结果已通过 ⇒ **不恢复、不重开、不为形式完整改造真实证据** |
| **D-r5-2** | 首版合并把 §2.4.1 两处谓词写成**单行**，与已通过验证的候选 probe（**拆两行**）不是同一字节；且该脚本**先写回再自检**，自检失败时文件已被改 | 用已持有的原串**逆向重建 v14**，并以**整文件 sha `7EBDE2EE60C6D387…` + 字节 90,003 + probe 体 sha `87AED462DD27F3DD…` 三重立验**通过后，改以两行形态重做合并；**未丢失任何 v14 内容** |

## 1. 编辑清单（五处，原地升版）

| # | 位置 | 改动 | 目的 |
|---|---|---|---|
| E1 | 标题行 | `**v14**` → `**v15**` | 版本标识 |
| E2 | v14 修订段之后 | 新增 **v15 修订段**（谓词依据 / 改动面 / 双版本实测 / 适用界） | 纳入已审结论 |
| E3a | §2.4.1 · `macros_scalar` | 该行**拆两行**并加 `AND NOT internal` | 排内置 scalar 宏 |
| E3b | §2.4.1 · `macros_table` | 该行**拆两行**并加 `AND NOT internal` | 排内置 table 宏 |
| E4 | §2.4.1.2「执行状态」 | 「**未运行**合成验证」→「**已实测落定**」+ 读数 + 自检义务 | **消除文档自相矛盾** |

**未改动**：v2–v14 全部修订段（逐段保留）、§2.4.1 其余四源、§2.4.1.1 用例表、§2.4.2–§2.4.7、阶段 3、附录 A、客户可见用法文字。
**旧 v14 保持**：①文首 v14 修订段原文未删；②另存冻结副本 `docs/evidence/firefly-site-recovery-procedure-v14-frozen-20261116.md`（90,003 B · `7EBDE2EE60C6D387F2C9B7D2F5FCAA138FAEDFFC959F2750822386888CB06702`）——因为该文件**从未提交**（`HEAD` 中不存在），这是唯一实物回退点。

## 2. 完整 diff（v14 → v15）

```diff
--- a/docs/handoff/firefly-site-recovery-procedure-20261114.md
+++ b/docs/handoff/firefly-site-recovery-procedure-20261114.md
@@ -1,4 +1,4 @@
-# firefly 案 · 现场处置流程（客户侧执行）· **v14**
+# firefly 案 · 现场处置流程（客户侧执行）· **v15**
 
 > **适用**：客户收到《修复完成通知 N-20261027-01》后，**自愿**为排查 #1（大表写入卡死）现场卡顿而执行「回收死元组 / 全量重拉」。
 > **执行方＝客户**；本方只出稿并核读数，**不代客户执行任何一步**，也不代客户判断成败。
@@ -15,6 +15,7 @@
 > **v12 修订（2026-11-16，依据总调度 R12 判据「局部收，终审未过 · 仍有三个确定问题」）**：①**BOM 解析键统一**——表头去 BOM 后必须把**同一份 `fieldnames` 交给 `csv.DictReader`**（v11 只在表头去掉 BOM、却把原行喂给 DictReader ⇒ 带 BOM 时数据行取不到 `table`，误报 `BAD_ROW`）；②**空库范围补全**——`information_schema.tables` 须查**整库（排除系统 schema）**；**仅 main 基表为空不得报 `PASS_EMPTY_DB`**，一律降级为新态 **`PARTIAL_EMPTY_MAIN_ONLY`（整库未证）**，并新增 `#EMPTY_DB_MAIN_ONLY` 显式声明写法；③**PARTIAL 不得绕过证④与用户闸**——删除「用户/总调度任一书面」入口：§3.0 默认**不进入阶段 3**；确需部分未证重建，**另立 §3.6 用户例外申请**（业主本人书面 + 七项要素），**不预置为已生效配方**，批准前停在阶段 2。
 > **v13 修订（2026-11-16，依据总调度 R13 判据「局部收，静态终审未过」）**：①**「整库为空」的对象范围补齐为五源**——`information_schema.tables` **不枚举 sequence**，故仅查表/视图会在「只剩一个 sequence」时误报空库；v13 起要求**表与视图 · 序列 · 非默认 schema · 用户类型 · 宏** 五源合计为 0，且**任一源不可探测即 `FAIL(EMPTY_DB_SCOPE_UNVERIFIED)`（拒绝该未覆盖状态）**；②**`MAIN_ONLY` 分支先核后报**——必须先确认 `main` 基表**确实为 0** 才可打印 `main_base=0`，否则 `FAIL(EMPTY_DB_MAIN_ONLY_CLAIM_BUT_MAIN_HAS_TABLES)`；③**表格与代码统一**——`scope=whole_db` 只要有任何对象即 **`FAIL`（不降级）**；`PARTIAL_EMPTY_MAIN_ONLY` 只在**声明本身就是 `main_only`** 时出现。
 > **v14 修订（2026-11-16，依据总调度 R14 判据「收 sequence、MAIN_ONLY 及 whole_db 修正，可进入合成验证命令审定，未授权运行」）**：①**宏源拆为两类**——`function_type='macro'`（scalar macro）**不完整**，须与 **`table_macro`** 分别成源（`macros_scalar` / `macros_table`），二者任一非空即「非空库」；②**`PASS_EMPTY_DB` 输出补齐 `by_source`**（表里要求、代码此前未输出）；③明确**索引/约束依附于表，不单列为独立来源**（不靠凑来源数）；④新增 **§2.4.1.2 空库判据的基线前提**——两版默认 catalog 与 internal 对象须**先由合成空库确认基线为 0**，否则会出现「空库恒 FAIL」；**查询失败不得当 0**（fail-closed 不变）。
+> **v15 修订（2026-11-16，依据总调度 R23/R24 判据「收两版合成验证、准文档合并」）**：①**两个宏源补 `AND NOT internal`** —— v14 的 `macros_scalar` / `macros_table` 只排系统 schema、**未排内置件**，而两版空库的内置宏**全部 `internal=True`**（新库 `main` 下：1.5.6 为 scalar macro **70** / table_macro **4**，1.4.5 为 **70** / **3**），故 v14 在**真空库上恒判「非空」**。v15 两源改为 `function_type='<类型>' AND NOT internal AND schema_name NOT IN ('information_schema','pg_catalog')`，与 `user_types` 源**同口径**。**明确未采用**「整类剔除宏」——那会把用户自建宏一并排掉，属**假空库**。②**实际改动面**：本文正文 **2 处**（§2.4.1 代码块内）；用于合成验证的矩阵脚本 `surface()` 内**同 2 处**同步（该脚本不在本文正文，随证据件交付）。③**两版合成验证结果**（2026-11-16，两版各一次、串行、独立环境）：`duckdb 1.5.6` 与 `1.4.5` 均 **11/11 MATCH · DIFF 0 · UNVERIFIED 0 · 退出码 0**；两版「空库基线」六源合计均 **0**；**用户宏负例 `C5f`（scalar macro）与 `C5g`（table macro）两版均判 `FAIL`**（`by_source` 各记 1）——即 v15 **既排掉内置件、又没排掉用户自建件**。证据见 `docs/evidence/firefly-emptydb-synthetic-run-20261116.md` §9–§12 与合并说明件 `docs/evidence/firefly-v15-merge-20261116.md`。④**适用界（必读）**：本节判据**只在 `duckdb_functions()` 提供 `internal` 列的版本上成立**（两版均为 20 列、实测含该列）；**「用户自建宏 `internal=False`」是两版实测结论，不是跨版本保证** —— 换用其他版本后，**必须先用 C5f / C5g 两个负例自检**：若这两例退化成「报空库」，说明该版本把用户宏也标成 internal ⇒ **本判据在该版本失效**，不得据此放行（本文**不提供**自动降级方案）。⑤本轮**未在客户现场执行任何命令**，结论只基于合成库。
 > **版本与命令适配**：本文所有 DuckDB 命令已在 **duckdb 1.5.6（当前目标版本）** 与 **1.4.5（回退位）** 上**隔离实测通过**（合成库，不触生产库）——证据见 `docs/evidence/firefly-site-procedure-isolation-validation-20261114.md`。**若您的 DuckDB 版本与上述不同，请先在副本上重测本文命令再执行**。
 > ⛔ 标记的步骤为**不可逆操作**（会改写或丢弃数据）。**未完成阶段 2 备份并通过「隔离恢复验证」前，禁止进入阶段 3。**
 > 每阶段含「做什么 / 怎么验证 / 失败怎么办」。任何阶段失败：**保留现场**（命令输出、日志、截图），回传本方后再继续，不要「试一下看」。
@@ -211,10 +212,12 @@
       f"WHERE schema_name NOT IN {SYS_SCHEMAS} AND NOT internal")
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
     return surf
 
 surf = probe_surface(con)
@@ -343,7 +346,7 @@
 
 **若基线非 0 怎么办**：记录被计入的条目（`by_source` + 明细行）→ 改进过滤器（增加 `internal`/系统 schema/默认 catalog 排除）→ 重跑基线 → **直到为 0**；**任何情况下不得**改成「允许 N 个以内的残留」这种宽松判据。
 
-> **执行状态**：本节为**审定前提**；本轮**未运行**任何合成验证。具体命令与用例见 `docs/evidence/firefly-synthetic-validation-request-20261116.md`（待审定）。
+> **执行状态（v15 更新）**：本节前提**已于 2026-11-16 由合成验证实测落定**——两版（1.5.6 / 1.4.5）独立环境、各自新建空库，六源过滤后计数**均为 0**；据此确认「漏排 `internal` 会导致空库恒 FAIL」的推断成立，并在 v15 补入 `AND NOT internal`（详见文首 v15 修订③与合并说明件）。`duckdb_functions()` 在两版均为 **20 列**且含 `internal`；内置宏全部 `internal=True`。**客户现场仍须各自核实版本**（非 1.5.6 / 1.4.5 时先按 §2.4.1.1 与 C5f/C5g 负例在副本上自检）。用例与命令见 `docs/evidence/firefly-synthetic-validation-request-20261116.md`（rev4）与上条证据件。
 
 #### 2.4.2 备选：参照复制品（阶段 1.4 未做时）
 
```

独立文件：`docs/evidence/firefly-v14-to-v15-diff-20261116.patch`（8143 B · `398344850C1DC60DA60491ED20C6898A2E082FD22F0DF3F605A8C508AB4E2F9D`）。

## 3. hash / 规模

| 项 | 值 |
|---|---|
| v14（原） | 90,003 B · `7EBDE2EE60C6D387F2C9B7D2F5FCAA138FAEDFFC959F2750822386888CB06702` |
| v15（现） | 92,633 B · `19ACA564BF85FB680342678F82C56C6EB8791D35239FC8456709FD86EDEE0E60` |
| 变更行（+/-） | **11** |
| 编码 | UTF-8 · LF · 末尾换行保留 |
| 行数 | 965 → 968 |

## 4. 一致性硬核对

| 核对 | 结果 |
|---|---|
| `NOT internal` 次数 | **8**（2 处宏源 + 1 处 `user_types` 原有） |
| 两宏源谓词 | `macro` × 1、`table_macro` × 1 |
| v2–v15 修订段 | **14** 段（v14 及更早逐段未删） |
| **v15 文档提取的 probe 体** | `BA22F6084394ECEA6F35FEBE417C5A906670598A8CEE14A91B16E539F5D5B3CA` |
| **候选 v15 probe（两版 11/11 实测通过件）** | `BA22F6084394ECEA6F35FEBE417C5A906670598A8CEE14A91B16E539F5D5B3CA` |
| **逐字节相同？** | **是** ← 文档里的代码＝刚跑过的代码 |

## 5. 正确 probe 路径（已审 R1 路由）

- 验证工程件：`candidate-v15-20261116T090251Z/restore_probe_v15.py`，矩阵 `PROBE` 指向它（rev2 修正）。
- **客户侧**：按 §2.4.1 代码块自行另存，文件名自定（`用法:` 注释仅为示例）。该路由属验证工程，不进客户可见正文。

## 6. 客户操作所依赖的剩余项

| # | 剩余项 | 状态 | 客户依赖 | 阻断/解除 |
|---|---|---|---|---|
| **D-1** | 《修复完成通知 **N-20261027-01**》 | **未下发** | **前置** | 客户依通知启动；下发须用户确认 |
| **D-2** | 本文 **v15 终审 / 落仓 / 下发** | 未终审、未提交、未下发 | **是** | 终审与落仓另批 |
| **D-3** | 客户侧 **DuckDB 版本自查** | 客户动作 | **是** | ≠1.5.6/1.4.5 须先在副本重测 |
| **D-4** | 客户回传读数 | 流程内 | **是** | `RESTORE_PROBE` 输出 + 逐库 sha256 + WAL 指纹（含 `by_source`） |
| **D-5** | 跨版本适用界（`internal` 语义） | **已写入 v15 段④** | **自检义务** | 换版本先跑 C5f/C5g；退化成「报空库」⇒ 判据失效不得放行 |
| **D-6** | 未测面：`DOUBLE/REAL` 精确恢复不可证、内存峰值、并发写 | 长期未测 | **结论上限** | 只能称「所选文本摘要一致」 |
| **D-7** | 无键表排除须客户书面接受 | 条件触发 | 条件依赖 | 出现无法回放的无键表时 |
| **D-8** | §3.6 用户例外申请 | **零批准先例** | 条件依赖 | 例外不使证④齐备 |
| **D-9** | 方案 B（同版本空库基线差集） | **仅登记未实施** | 否（内部备选） | 首核失败时才可能启用，**不得自动切换** |

## 7. 本轮未做

未 `prepare`、未 `pip`、未跑新 run、未 ATTACH 真实或备份库、未客户下发、未清理、未提交/推送。
