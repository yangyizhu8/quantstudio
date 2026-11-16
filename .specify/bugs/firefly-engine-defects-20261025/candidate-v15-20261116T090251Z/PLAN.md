# v15 隔离候选（静态制品）· DRIFT-N17-r3

> 候选目录：`candidate-v15-20261116T090251Z`（**排他新建**，UTC 为执行时刻实测值）
> 依据：`docs/evidence/firefly-emptydb-synthetic-run-20261116.md` §9（全文 sha256 `3E3AE03CC8C925DE15A29B56801A0E4E7060676420C7BFD5413C2F45C18F4FA9`）
> **状态：静态制品，未运行。** 未 `pip`、未跑 `prepare`、未跑 probe / 矩阵 / DB 诊断。
> 冻结：流程件 v14、rev4 申请件、旧三脚本、两失败 run 均**未改动**（sha 见 MANIFEST.json）。
> 方案 B 仅登记，**首核失败不得自动切换**，须另请裁定。

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
