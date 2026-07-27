# 框架修复数据库迁移 Runbook（2026-07-27，F3/F4，v2 审核修订）

本文档是**可同步**的正式迁移手册：行业正式表安全重建（staging + 原子交换 +
完整 PRIMARY KEY）与指数成分快照 meta 打点（v2 语义）。所有步骤幂等、可重入。

## 1. 涉及表

| 表 | 作用 | 主键 |
|---|---|---|
| `industry_classification` | 行业分类定义（SW/SW2021/L1） | (classification_system, classification_version, industry_level, industry_code, effective_from) |
| `industry_membership` | 行业成员历史（PIT 区间，**raw 保留重叠**） | (classification_system, classification_version, industry_level, industry_code, code, effective_from) |
| `index_constituents_snapshot_meta` | 成分快照完整性批次契约 | (index_code, time) |

DDL 权威定义：`quantstudio/pipeline/writers.py::DDL_DUCKDB`（`DuckDBWriter`
初始化自动 CREATE IF NOT EXISTS）。expected_count 契约：
`config/index_constituents_expectations.json`（指数公开元数据，非策略白名单）。

## 2. 行业表安全重建（staging + 门控 + 原子交换）

背景：源端 `index_member`（SW2021）存在新旧分类区间正持续时间重叠
（实测 239 对 / 169 只证券）。**官方契约只有 in_date/out_date，没有冲突
裁决规则**——transform 严格 1:1 保留原始区间
（`quantstudio/pipeline/industry_membership_standardizer.py`，仅剔除
from > to 脏数据），歧义日期由 Provider 层 fail-closed
（`ReferenceDataCapabilityError`），能力降级为
APPROXIMATION_REQUIRES_CONFIRMATION，不得宣称正式 PIT READY。

```bash
# 唯一正式迁移入口（可同步）：staging 拉取+transform+validate+门控 → 单事务原子交换
python scripts/rebuild_industry_tables.py --db data/quantstudio.db     --start 2018-01-01 --end $(date +%F)
```

安全契约（2026-07-27 P0 修订）：

- staging 表按 `DDL_DUCKDB` 权威 schema **显式创建并包含完整 PRIMARY KEY**；
  **禁止** `CREATE TABLE ... AS SELECT`（CTAS 不保留约束，rename 后正式表
  会丢失 PK——本轮已实测修复）；
- 门控：分类 31 行 SW2021 L1、成员表 orphan=0、bad_ranges=0（重叠区间属
  原始事实，仅记录不阻断）；
- 仅在门控通过后用**单个短事务**原子交换（DROP official + RENAME staging
  → official + 推进水位）；交换事务中途任何失败 → ROLLBACK，正式表数据、
  PK、水位全部不变（`tests/test_industry_migration_tool.py` 三个中途注入点
  + 5 类前置失败注入锁定）；
- 交换后验收：`duckdb_constraints()` 必须显示两张表完整 PRIMARY KEY，且
  重复主键插入抛 `ConstraintException`。

## 3. 成分快照 meta 打点（v2 语义）

```python
from quantstudio.pipeline.writers import DuckDBWriter
from quantstudio.pipeline.index_constituents_meta import refresh_snapshot_meta
writer = DuckDBWriter({"type": "duckdb", "path": "data/quantstudio.db"})
with writer._conn_lock:
    conn = writer._conn()
    refresh_snapshot_meta(conn)   # 幂等 upsert，只基于当前已写入批次
    conn.close()
```

规则（v2，2026-07-27 审核修订）：

- `n_constituents = COUNT(DISTINCT code)`（不是 COUNT(*)）；
- 重大质量违规（重复代码/负权重/空或非法代码）→ `status='invalid'`，
  **永远不得**被当作完整 PIT 快照；
- 固定成分指数（`expectations` 登记）：无违规且 n_distinct ≥ expected_count
  → `complete`，否则 `partial`；
- 可变成分指数必须在 `variable_indices` **显式登记**（当前仅 399101），
  无违规且 n_distinct > 0 → `complete`；
- 未登记指数 → `unknown`（fail-closed，Provider 不服务）；
- 配置文件缺失/非法 → `ExpectationsConfigError`（fail-closed，不打点）。
完整性在打点确定，**不依赖未来快照**；历史 PIT 查询只消费 complete 快照，
未来写入不改变历史查询结果（有测试锁定）。日常增量由 daemon 在
`index_constituents` 任务写入后自动打点（`_stamp_and_write` 挂钩）。

## 4. 指数日线（含申万行业指数）

正式宇宙：`TushareAdapter.get_index_daily_universe()` = 普通指数
（`get_index_codes`）+ SW2021 L1（`get_sw_index_codes`，probe 缓存）。
宇宙完整性门控（`_validate_sw_universe`）：数量=31、格式 `^\d{6}\.SI$`、
唯一、非空——任一违例整个任务失败且水位不变。daemon 的
full/incremental/resident 三种模式统一经 per-stock 路径消费该宇宙
（`801xxx` → `sw_daily` 接口，万股/万元→手/千元→aligner 统一股/元）。

## 5. 验收清单

- [ ] `pytest -q` 全量通过；
- [ ] 行业：`duckdb_constraints()` 两张表完整 PRIMARY KEY；
      `query_industry_membership_quality()`：orphan=0、bad_ranges=0
      （正重叠对数为源端原始事实，记录不阻断）；歧义日期
      `get_industry` 抛 `ReferenceDataCapabilityError`；
- [ ] `index_constituents_snapshot_meta`：invalid/unknown 快照不被
      `get_index_stocks` 返回；
- [ ] `inspect_capabilities.py`：6 项 READY + industry_membership_pit
      DEGRADED（APPROXIMATION_REQUIRES_CONFIRMATION）；
- [ ] 默认 legacy 回测黄金结果与修复前逐项一致。

## 6. 备份与回退（真实方案，**DROP 两表不是回退**）

- 迁移前备份（任一即可）：
  1. 文件级：确认无进程持有写连接后 `cp data/quantstudio.db
     data/backups/quantstudio-<date>.db`（DuckDB 单写者，务必先停 daemon/GUI）；
  2. SQL 级：`EXPORT DATABASE 'data/backups/pre-industry-<date>' (FORMAT PARQUET);`
- 回退恢复：
  1. 文件级：停写后用备份文件整体替换 `data/quantstudio.db`；
  2. SQL 级：新建库后 `IMPORT DATABASE 'data/backups/pre-industry-<date>';`
  3. 若只需撤销行业表：从备份库 `ATTACH` 后把两张表数据插回，
     或重新运行 `scripts/rebuild_industry_tables.py`（幂等）。
- 旧 `sw_industry` 从未改动（legacy 审计快照），不受影响。
