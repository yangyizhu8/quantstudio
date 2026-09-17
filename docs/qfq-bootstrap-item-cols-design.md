# qfq_bootstrap_item｜DUCKDB_COLS 缺 3 列修复方案（小六步①）

- 日期：2026-09-17｜归属：dev（QFQ 线）｜状态：**待审**
- 触发：总调度追单「qfq_bootstrap_item 3 列（DUCKDB_COLS 追加 approved*）」

## 〇、先更正我上一轮的错误定性（重要）

我上一轮报的是「**列已用、未声明** ⇒ 纯新库上 `UPDATE ... SET approved=TRUE` 必失败」。
**取证后该结论不成立，我撤回**：

- `qfq_reanchor_schema.py:311-327` 的 DDL **已经完整声明**三列（第 11/12/13 位）：
  `approved BOOLEAN` / `approved_reason VARCHAR` / `approved_at TIMESTAMP`；
- 故纯新库由该 DDL 建表后，UPDATE 语句是**可用**的。

**真实缺陷**（比原判轻，但仍必须修）：`DUCKDB_COLS["qfq_bootstrap_item"]` 这一**有序真相源**
只有 10 列，**漏掉尾部 3 列** ⇒ 真相源与 DDL 不一致（drift）。

## 一、问题定义（证据）

| 位置 | 内容 | 列数 |
|---|---|---|
| DDL `qfq_reanchor_schema.py:312-327` | bootstrap_run_id, asset_type, code, status, attempt_count, block_reason, last_error, started_at, finished_at, updated_at, **approved, approved_reason, approved_at** | **13** |
| `DUCKDB_COLS["qfq_bootstrap_item"]` `qfq_reanchor_schema.py:657-660` | bootstrap_run_id, asset_type, code, status, attempt_count, block_reason, last_error, started_at, finished_at, updated_at | **10** |

SQL 侧三列已被实际使用（`qfq_resident_orchestrator.py`）：
- L1190 `UPDATE qfq_bootstrap_item SET approved=TRUE, approved_reason=?, approved_at=?, updated_at=?`
- L1190 附近 `AND (approved IS NULL OR approved = FALSE)`
- L1224 附近 `AND status IN ('failed','dead_letter') AND approved = TRUE`

⇒ 三列的**列名**由 SQL 全量反查确证（非猜测）：`approved` / `approved_reason` / `approved_at`。

## 二、影响面

`DUCKDB_COLS` 是「列顺序单一真相源」，其消费点至少三处：
1. **契约测试（严格列序）**——`test_qfq_schema_status.py::TestContractConsistency::test_duckdb_cols_matches_ddl_order`、
   `test_qfq_reanchor_batch1.py::TestSchemaDDL::test_duckdb_column_order_matches_manifest`
   ⇒ **这两条正在「改动前既有失败集」里**——本修复**很可能正是它们的成因**；
2. `qfq_fresh_capture.py:55` `FRESH_CAPTURE_COLS = DUCKDB_COLS["qfq_fresh_capture"]`（非本表，但证明该模式被真实消费）；
3. `qfq_reanchor_schema.py:766` `for col in DUCKDB_COLS.get(table, [])`（按表迭代——**需确认是否覆盖本表**）。

## 三、改动范围（最小）

**单文件单行块**：`quantstudio/pipeline/qfq_reanchor_schema.py` 的 `DUCKDB_COLS["qfq_bootstrap_item"]`
追加三列，**顺序与 DDL 逐一对应**（紧随 `updated_at` 之后）：

```python
    "qfq_bootstrap_item": [
        "bootstrap_run_id", "asset_type", "code", "status", "attempt_count",
        "block_reason", "last_error", "started_at", "finished_at", "updated_at",
        "approved", "approved_reason", "approved_at",
    ],
```

**不改**：DDL 本体（已正确）、任何 SQL、写入语义、水位、停止钩子。

## 四、验收标准

| # | 项 | 通过条件 |
|---|---|---|
| V1 | 真相源一致 | `DUCKDB_COLS["qfq_bootstrap_item"]` 与 DDL 列集**逐位相等**（脚本断言，非肉眼） |
| V2 | 契约测试 | 上述 2 条 schema 契约测试**由红转绿**；若仍红 ⇒ 存在第二处不一致，须继续定位（**不得以"本来就是红的"结案**） |
| V3 | 既有失败集对表 | 全量套件结果与**改动前既有失败集逐条比对**：新增失败必须为 0；"本来就红"≠"被我改红" |
| V4 | 消费点回归 | `qfq_fresh_capture` / `qfq_reanchor_schema:766` 路径相关测试全绿 |
| **V5** | **迁移双态（②升格）** | `reanchor:766` 是**自动迁移循环**（DESCRIBE→缺列→`ALTER TABLE ADD COLUMN`）。须分别验证：**已建库（含三列）= no-op**（不得产生任何 ALTER）；**缺列库 = 补列成功**且补后列集 = 13。仅验"COLS=DDL"**不充分** |

## 五、回退条件

- 改动为**单文件三行**，回退即删该三行（逐文件精确回退，不用批量 git）；
- V3 出现任何新增失败 → 立即回退并上报；
- 若 V2 显示两条契约测试**改前就已因别处不一致而红**，本修复不背该责，但须在验收文档中**分列归因**。

## 六、三项落定结果（原待确认，已全部取证）

### 落定 1｜`reanchor:766` **确实消费本表——且是自动迁移循环**（本件性质升格）

```python
for table, ddl in DDL_DUCKDB.items():
    actual = {r[0] for r in conn.execute(f"DESCRIBE {table}").fetchall()}
    for col in DUCKDB_COLS.get(table, []):
        if col not in actual:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {_infer_col_type(ddl, col)}")
```

⇒ 本改动**不是簿记订正，而是激活一条既有迁移路径**：缺三列的库将被自动 ALTER 补列；
已含三列的库为 no-op。**按铁律属 schema 迁移生效**，验收须含 V5 双态。

### 落定 2｜指纹**不含错**——单文件改动成立，无需同批更新

- `SCHEMA_CONTRACT_DUCKDB = project_legacy_contract_shape(TARGET_QFQ_2_1_FINGERPRINT)`（:1223）⇒ manifest **由指纹派生**；
- 指纹两处条目**均为 13 列**（含 `approved_at`）：`:675` 条目 columns 677–689（末三行 687/688/689）；`:963` 同构条目 columns 965–977（末三行 975/976/977）；
- **比对矩阵**：

| 比对 | 现状 | 本改动后 |
|---|---|---|
| DDL(13) vs `DUCKDB_COLS`(10) | 红 | **绿** |
| manifest/指纹(13) vs `DUCKDB_COLS`(10) | 红 | **绿** |

⇒ 两条红**同源于唯一落后项 `DUCKDB_COLS`**，预期**双绿**，不存在"第二处不一致"；
⇒ 指纹无错 ⇒ **不触发矩阵哈希追认同批纪律**，改动面 = **单文件三行**。

### 落定 3｜改前基线已留证

```
tests/test_qfq_schema_status.py::TestContractConsistency::test_duckdb_cols_matches_ddl_order
tests/test_qfq_reanchor_batch1.py::TestSchemaDDL::test_duckdb_column_order_matches_manifest
→ 2 failed in 1.45s（改动前基线：两条均红）
```

V2 判据因此可判：本改动须使**两条同时转绿**；若仅一条转绿，则与落定 2 的推断矛盾，
须回指纹/manifest 链路继续定位（**不得以"本来就红"结案**）。
