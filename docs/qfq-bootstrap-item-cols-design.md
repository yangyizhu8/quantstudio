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

## 五、回退条件

- 改动为**单文件三行**，回退即删该三行（逐文件精确回退，不用批量 git）；
- V3 出现任何新增失败 → 立即回退并上报；
- 若 V2 显示两条契约测试**改前就已因别处不一致而红**，本修复不背该责，但须在验收文档中**分列归因**。

## 六、待确认（实施方案前需落定）

1. `qfq_reanchor_schema.py:766` 的迭代点是否消费本表（若是，追加三列会改变其行为，需评估）；
2. 是否存在**独立的 manifest/fingerprint** 记录本表列序（若有，须**同 commit** 更新——矩阵哈希追认同批纪律）；
3. 两条契约测试**改动前的失败原因**（先跑一次留基线，才能证明本修复是否为其成因）。
