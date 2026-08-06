# B-3R QFQ Schema 只读勘察报告

> **日期**：2026-08-05
> **性质**：只读勘察（首版未修改任何代码/配置/数据库）
> **目的**：原为 B-3a（2.1 新库契约 + 普通 init 安全闸）提供精确结构输入；
> 后续追加 §12（B-3a/B-3a.2/B-3a.3 完整实施结果）与 §13（B-3b 实施）
> **状态**：B-3R 收口；B-3a、B-3b.3、B-4 均已通过独立复审；B-5 获准进入本地实施（2026-08-06）。
> **关联设计**：`docs/mcp_migration/mcp-cutover-design-v2.md` v2.4

---

## 0. 关键先导发现（B-3a 前置）

1. **`SCHEMA_VERSION` 已升到 `"reanchor-2.1"`（`qfq_reanchor_schema.py:52`），但 DDL_DUCKDB/DUCKDB_COLS 未落地任何 B-3 列**——当前是"半成品常量声明"。B-3a 要真正落实成列 + 列清单 + 契约。
2. **`TriggerStatus` 枚举缺 `SUPERSEDED`**（`qfq_orchestrator_types.py:57-64`）—— `TRIGGER_STATUS` 常量已含（`qfq_reanchor_schema.py:79-83`）但枚举未对齐，违反"逐字对齐"契约。B-3a 必补。
3. **状态识别不能只看 SCHEMA_VERSION 常量**：代码常量已是 2.1 但物理 schema 未落地。B-3a 状态探测器必须从物理结构（表/列/类型/约束/PK）判定，不能因常量为 2.1 就判数据库为完整 2.1。

---

## 1. DDL_DUCKDB 各表完整 DDL（B-3 涉及表）

文件：`quantstudio/pipeline/qfq_reanchor_schema.py`，DDL_DUCKDB 字典 `:185-421`（11 张表，均 `CREATE TABLE IF NOT EXISTS`）。

### qfq_trigger_queue（`:341-366`，B-3 主战场）
```
trigger_id      VARCHAR   NOT NULL
asset_type      VARCHAR   NOT NULL
code            VARCHAR   NOT NULL
trigger_type    VARCHAR   NOT NULL
detection_source VARCHAR  NOT NULL
source_key      VARCHAR
effective_date   -IGINT
payload_hash     VARCHAR
factor_old       DOU-LE
factor_new       DOU-LE
factor_revision  -IGINT
status           VARCHAR   NOT NULL
attempt_count    INTEGER   DEFAULT 0
next_retry_at    TIMESTAMP
claimed_by       VARCHAR
claimed_at       TIMESTAMP
last_event_id    VARCHAR
last_error       VARCHAR
dead_letter_at   TIMESTAMP
created_at       TIMESTAMP NOT NULL
updated_at       TIMESTAMP NOT NULL
completed_at     TIMESTAMP
PRIMARY KEY (trigger_id)
```
**当前无 `trigger_id_version`、无 `source_generation`/`price_source` 列**——B-3 要加。

### qfq_anchor_state（`:187-207`）
PK `(asset_type, code, price_source)`（已有 price_source，无 source_generation）。17 列。

### qfq_reanchor_event（`:209-248`）
PK `(event_id)`。有 price_source 列，无 source_generation。**注意：此表不在 DUCKDB_COLS**（见 §2）。

### qfq_pending_backfill（`:250-273`）
PK `(asset_type, code, table_name, freq, range_start, range_end)`——6 列复合，无世代。20 列。

### qfq_bootstrap_run（`:275-291`）
PK `(bootstrap_run_id)`。有 `schema_version VARCHAR`（per-run 版本标识，可空）、`config_hash`、`baseline_version`——均可空。**无 price_source/source_generation/cutover_id**。

### qfq_bootstrap_item（`:293-306`）
PK `(bootstrap_run_id, asset_type, code)`。10 列。

### qfq_cycle_run（`:319-339`）
PK `(cycle_id)`。有 `schema_hash`（内容 hash，非版本号，当前未回读比较）。**无 price_source/source_generation/cutover_id/owner 字段**。

### qfq_watermark_intent（`:368-380`）
PK `(cycle_id, source, table_name, freq)`——4 列复合，有 source 无 source_generation/cutover_id。

### qfq_fresh_capture（`:382-405`）
PK `(capture_id)`。有 source 列，无 source_generation/cutover_id。21 列。

### qfq_observation_cursor（`:408-420`）
PK `(detector_name, asset_type)`——2 列复合，无世代。9 列。

### trade_calendar（`:308-317`）
B-3 不涉及。PK `(cal_date)`。

---

## 2. DUCKDB_COLS 清单（`:495-561`）

**只覆盖 10 张表，DDL_DUCKDB 有 11 张——`qfq_reanchor_event` 不在 DUCKDB_COLS**。这意味着 `_migrate_duckdb_columns` 不会给 `qfq_reanchor_event` 补列（即便 DDL 有新列定义），但其契约仍由 SCHEMA_CONTRACT_DUCKDB 全覆盖校验——**不一致源头**。B-3 若给 reanchor_event 加列，必须同时加入 DUCKDB_COLS。

各表列清单与 DDL 一致（顺序相同），具体见勘察原始报告。B-3 改任何表的列时，必须同步改 DDL_DUCKDB + DUCKDB_COLS + SCHEMA_CONTRACT（后者自动）+ TriggerRecord.COLS（trigger_queue 专属）。

---

## 3. SCHEMA_CONTRACT / _parse_ddl_contract / _verify_duckdb_contract

### SCHEMA_CONTRACT_DUCKDB 生成（`:669-672`）
**完全自动从 DDL_DUCKDB 文本解析**（模块加载即求值）：`{t: _parse_ddl_contract(d) for t, d in DDL_DUCKDB.items()}`。改 DDL → 契约自动跟变。

### _parse_ddl_contract（`:634-665`）
正则解析 DDL 文本 → `{"columns": {name: TYPE}, "not_null": [...], "pk": [...]}`。列类型大写规范化去空格。PK 按顺序入 `pk`。

### _verify_duckdb_contract（`:675-720`）
对每张表回读校验，任一不符抛 RuntimeError（fail-fast）：
1. 列存在（缺列 → RuntimeError）
2. 列类型一致（PRAGMA table_info 第 3 列 `.upper()`）
3. NOT NULL（spec 中 not_null 列必须 actual notnull=True）
4. PK 顺序（优先 `duckdb_constraints()` 取有序列名 list；降级 PRAGMA pk 标志集合语义）

**VERIFY 不校验 DEFAULT/CHECK/FOREIGN KEY**。**VERIFY 是"事后告警"非"门禁回滚"**——DuckDB autocommit 下校验失败时改动已落盘。

---

## 4. _migrate_duckdb_columns 完整实现（`:586-609`）

```python
def _migrate_duckdb_columns(conn, best_effort: bool = False) -> None:
    for table, ddl in DDL_DUCKDB.items():
        actual = {r[0] for r in conn.execute(f"DESCRI-E {table}").fetchall()}
        for col in DUCKDB_COLS.get(table, []):
            if col not in actual:
                col_type = _infer_col_type(ddl, col)
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                except Exception as e:
                    if best_effort: logger.warning(...)
                    else: raise RuntimeError(...)
```

关键行为：
- **遍历源**：DDL_DUCKDB 拿表名，列清单走 DUCKDB_COLS（`qfq_reanchor_event` 不在 → 不补列）
- **条件**：`col not in actual`（只在缺列时 ALTER，幂等）
- **只 ADD COLUMN（nullable 无 default）**，不回填、不设 NOT NULL
- **不显式 commit**（DuckDB autocommit 立即生效）
- **best_effort=False（默认）**：fail-fast；=True：warning 跳过
- **可部分成功**：每列独立 try/except，fail-fast 下失败前的成功 ALTER 已生效
- **_infer_col_type（`:755-760`）**：从 DDL 文本正则匹配类型，白名单 `-IGINT/INTEGER/DOU-LE/VARCHAR/-OOLEAN/TIMESTAMP/REAL/TEXT`，匹配不到降级 VARCHAR

**B-3 关键风险**：NOT NULL 列（trigger_id_version）若走此路径，DuckDB `ADD COLUMN NOT NULL` 无 default 对存量行 NULL 会报错 → fail-fast 抛 RuntimeError。**这正是 B-3a 安全闸必须先落地的技术根因**：普通 init 遇旧 2.0 必须 fail-fast，不得自动补列。

**唯一调用点**：`init_duckdb_schema:577`（全项目无其它调用者）。无非 B-3 的特殊兼容用途——它是 schema 演进通用补列工具。

---

## 5. init_duckdb_schema 完整实现（`:568-578`）

```python
def init_duckdb_schema(conn) -> None:
    for ddl in DDL_DUCKDB.values():
        conn.execute(ddl)            # CREATE TABLE IF NOT EXISTS（幂等）
    _migrate_duckdb_columns(conn)    # 默认 best_effort=False（fail-fast）
```

不 commit、不开事务（DuckDB autocommit）。事务边界由调用方控制。

### init_all_from_paths（`:763-802`）
- 解析路径（main_db/aux_db，默认 `db_path()` = 正式库）
- 同路径保护（main/aux 不能 resolve 到同一文件）
- DuckDB：`init_duckdb_schema` + `_verify_duckdb_contract`（autocommit，无显式 commit）
- SQLite：`init_sqlite_schema` + **显式 commit** + `_verify_sqlite_contract`

---

## 6. init 调用链（可能命中正式库的入口）

| 入口 | 文件:行号 | 命中正式库？ |
|---|---|---|
| **daemon 每轮运行** | `daemon.py:254` → `orch.init_schema` → `init_duckdb_schema` | **是（唯一生产运行时 ALTER 入口）** |
| `__main__` 无参 | `qfq_reanchor_schema.py:807` `init_all_from_paths()` | 是（需人工运行） |
| CLI 变更命令 | `qfq_orchestrator_cli.py:395,431,474` | 是（需 `--allow-production`） |
| 测试 fixture | 多处（test_qfq_reanchor_batch1 等） | 否（临时/内存库） |
| scripts staging | `scripts/qfq_*.py` | 否（staging 副本） |

**daemon 每轮运行是 B-3a 安全闸必须先落地的根本原因**：B-3 改 schema 后 daemon 重启会自动跑 `_migrate_duckdb_columns`。

---

## 7. DB 路径解析

- **单一真相源**：`quantstudio/_paths.py:24` `_ROOT = Path(__file__).resolve().parent.parent`（锚定项目根，不依赖 cwd）
- `get_data_root()`（`:28-46`）：环境变量 `QUANTSTUDIO_DATA_ROOT` > `data_config.json` path 字段 > `_ROOT/data`
- `db_path(name)`（`:53-55`）：`DATA_ROOT / name`，默认 `data/quantstudio.db`
- CLI `_resolve_db`（`qfq_orchestrator_cli.py:143-149`）：强制 `--db`，`Path(args.db).resolve()`
- CLI `_production_db_path`（`:138-140`）：`Path(db_path()).resolve()`
- `_guard_mutating`（`:152-163`）：db==prod 且变更命令无 `--allow-production` 即拒绝
- **符号链接/junction**：`_paths.py` 本身不解析，但 CLI `.resolve()` 会跟随 junction。项目内未见此配置。
- `aux_db_path(main_db)`（`:164-178`）：同目录 `qfq_aux.db`

**B-3b migration runner 正式库拒绝必须用 `os.path.samefile` 或等价文件身份判断**（不能只字符串比较），处理绝对/相对/`..`/大小写/符号链接/junction/别名。**`--allow-production` 不得成为 migration runner 的绕过开关**（硬门禁）。

---

## 8. SCHEMA_VERSION 比较点

- **`bootstrap_completed`（`qfq_resident_orchestrator.py:686-700`）**：取最新 `status='completed'` 的 `qfq_bootstrap_run.schema_version` 与代码常量比较。NULL 容错（`if schema_v is not None`）。SCHEMA_VERSION 已升 2.1 → 历史 2.0 bootstrap 判不匹配 → fail-closed。**这是预期行为**（B-3a 不应放宽；重建路径留 B-6）。
- **`bootstrap` 落盘（`:836-846`）**：每次把当前 SCHEMA_VERSION 落库到 `qfq_bootstrap_run.schema_version`。
- **无库级版本元数据表**：当前唯一版本持久化是 `qfq_bootstrap_run.schema_version`（per-run 级）。`qfq_cycle_run.schema_hash` 是内容 hash 非 version，未回读比较。

---

## 9. TRIGGER_STATUS 消费者清单（B-3a SUPERSEDED 逐字一致性核查）

### 常量与枚举
- `TRIGGER_STATUS`（`qfq_reanchor_schema.py:79-83`）：**已含 superseded**
- `TriggerStatus` 枚举（`qfq_orchestrator_types.py:57-64`）：**⚠️ 缺 SUPERSEDED**——B-3a 必补

### 内联硬编码状态集合（未引用常量，B-3 重点核查）
| 位置 | 内联集合 | B-3 风险 |
|---|---|---|
| `qfq_resident_orchestrator.py:603` | gate `row[0] != "committed"` | **高**：若 claimed trigger 被标 superseded 会误 fail（B-3a 不退役 trigger，暂无风险；B-6 世代过滤时处理） |
| `:770` | `status='committed'`（_classify_bootstrap_security） | 中 |
| `:313` | claim `status='pending'`（只领 pending） | 低（superseded 是终态，不被 claim，正确） |
| `:483` | backfill resolved `status IN ('pending','retryable_failed','blocked')` | 中（P0-5 过度 resolve，B-5 收紧） |
| `:720` | bootstrap_item 终态 `('pending','in_progress','blocked','failed','dead_letter')` | 低 |
| `quality_audit.py:474,481,487` | dead_letter/SLA/stale 统计 | 低（不含 superseded，正确） |
| `qfq_orchestrator_cli.py:236` | `GROUP -Y status`（全状态分组） | 低（会显示 superseded，OK） |
| `:531-532` | reopen 仅限 dead_letter | 低（superseded 不能 reopen，正确） |

### claim/retry/dead_letter/cleanup 路径
- claim 只领 pending（`:313`）→ superseded 不被 claim（正确）
- retry/dead_letter（`:486-512`）：blocked/rolled_back/failed → bump_attempt → dead_letter/retryable_failed
- **trigger 级 supersede 逻辑当前不存在**——B-6 待实现退役能力
- watermark_intent 的 superseded 是既有终态（语义不同：候选水位作废）

**B-3a 要求**：枚举、常量、通用状态分类逐字一致，不提前改变当前 trigger 处理行为。

---

## 10. B-3a 允许范围、禁止范围、测试矩阵

### B-3a 允许范围
- 新空库最终 2.1 DDL（新增列 + 新建表 + 4 张 swap 表新库场景用最终 v2 PK + trigger_id_version INTEGER NOT NULL）
- SCHEMA_VERSION=2.1、DUCKDB_COLS、SCHEMA_CONTRACT、TRIGGER_STATUS.superseded + TriggerStatus.SUPERSEDED 枚举
- 普通 init_duckdb_schema 状态机（见下表）
- `_migrate_duckdb_columns` 处置：阻止普通 init 对版本化旧 schema 调用它

### 普通 init 状态机（B-3a 核心）

| 数据库状态 | 普通 init 行为 |
|---|---|
| 路径不存在/真正空库 | 创建完整 2.1 schema |
| 已是完整 2.1 | 只验证，幂等返回，不写入 |
| 完整旧 2.0 | **在任何 DDL/DML 前 fail-fast**，提示用显式 migration runner |
| 部分迁移/版本未知/结构不一致 | fail-closed，不猜测/自动修复 |
| 正式库且旧 schema | 同样只 fail-fast，不自动补列 |

**禁止普通 init 对版本化 schema 调用隐式 _migrate_duckdb_columns**。

### B-3a 禁止范围
- 不得实施 B-3b migration runner
- 不得写正式库
- 不得让 daemon 自动进入 mcp-gen1 生产处理
- 不得 stage/commit/push
- 不实施 trigger 退役逻辑（B-6）

### B-3a 测试矩阵
- 不存在的临时路径 → 创建完整 2.1
- 完整 2.1 再次 init → 结构和数据不变
- 人工构造完整 2.0 临时库 → 普通 init 抛 migration-required 异常
- 2.0 fail-fast 前后临时库 schema/行数/内容 hash 不变
- 部分 2.1 列/缺表/错误 PK/错误版本号 → 全部 fail-closed
- trigger_id_version NOT NULL；v1/legacy=1；MCP v2=2；禁 NULL/猜测
- superseded 在所有状态集合和校验器语义一致
- 所有既有新空库测试继续通过
- **正式库前后**：canonical path/size/mtime/SHA-256 四项不变

### B-3a 内部强制顺序
1. 先实现数据库状态识别（EMPTY_OR_NEW/COMPLETE_2_0/COMPLETE_2_1/PARTIAL_OR_MIXED/UNKNOWN）+ fail-fast 安全闸
2. 测试安全闸确实在任何写操作前触发（给 _migrate_duckdb_columns/DDL execute/写连接加 spy/mock 证明异常在首次写入前）
3. 再接入完整 2.1 新库 DDL/COLS/CONTRACT
4. 最后验证新空库生成完整 2.1
5. **不能先激活新 DDL 再补旧库拦截**

### B-3a 新对话固定执行顺序
1. 重新核验工作区和正式库基线（git status + B-3R 报告 SHA-256 + 正式库 canonical path/size/mtime/SHA-256 + 无进程访问）
2. 先写状态探测器（只读，不调任何建表/补列/init）
3. 先接安全闸（空路径允许/完整 2.1 允许验证/完整 2.0 写前 fail-fast/partial fail-closed/正式旧库 fail-fast/_migrate 未被调用——用 spy/mock 证明）
4. 再接入完整 2.1 新库契约
5. 完成回归与正式库不变证明（四项重算一致）
6. 停止审核，不进 B-3b，不 stage/commit/push

---

## 11. 冻结风险（B-3a 输入）

1. **禁止只按 SCHEMA_VERSION 判断数据库状态**：物理 schema 才是最终事实。
2. **_migrate_duckdb_columns 不参与版本化 2.0→2.1 升级**（只 nullable ADD，无回填/NOT NULL/PK swap/原子升级/中断恢复/版本一致性验证）。B-3a 必须先阻止普通 init 对版本化旧 schema 调用它。
3. **--allow-production 不得成为 migration runner 绕过开关**（B-3b 硬门禁）。
4. **SUPERSEDED 检查全部消费者**（枚举/常量/终态集合/可 claim/retry/dead-letter/pending 计数/gate/CLI/cleanup/metrics/schema contract/claimed unit 最终状态判定）。B-3a 不退役 trigger 但保证逐字一致。
5. **bootstrap 2.0→2.1 不匹配是预期 fail-closed**：B-3a 不放宽，重建路径留 B-6。

---

## 附录：B-3 涉及表的 4 张 v2 swap（B-3b 范围，此处仅记录）

| 表 | 当前 PK | v2 PK（B-3b） |
|---|---|---|
| qfq_pending_backfill | (asset_type,code,table_name,freq,range_start,range_end) | + price_source, source_generation |
| qfq_observation_cursor | (detector_name, asset_type) | + price_source, source_generation |
| qfq_anchor_state | (asset_type, code, price_source) | + source_generation |
| qfq_watermark_intent | (cycle_id, source, table_name, freq) | + source_generation, cutover_id |

**B-3a 新库场景**：这 4 张表在新空库直接用最终 v2 PK（不走 swap，swap 是 B-3b 存量迁移）。

---

## 12. B-3a 完整实施结果（2026-08-05）

> **状态**：B-3a 完整实现完成，待集中机械复核。本节记录落地结果，不写"审核通过"。

### 12.1 唯一真相源：`quantstudio/pipeline/qfq_schema_contracts.py`（新建，无 DB 副作用）

冻结两个**独立**版本化物理指纹（不派生自当前 DDL/SCHEMA_CONTRACT_DUCKDB/SCHEMA_VERSION）：

- **`LEGACY_QFQ_2_0_FINGERPRINT`**（11 表）：来自本报告 §1 实测真实 2.0 物理结构。4 张 swap 表的旧 PK 单独写死。
- **`TARGET_QFQ_2_1_FINGERPRINT`**（15 表 = 11 升级 + 4 新表）：来自 mcp-cutover-design-v2.md 最终 DDL。
- **`LEGACY/TARGET_SOURCE_WATERMARK_FINGERPRINT`**（6/8 列）+ 共享 `SOURCE_WATERMARK_2_0/2_1_DDL`（writers.py 与 QFQ schema 共同引用，单一真相源）。
- **`LEGACY/TARGET_MAIN_DB_*_FINGERPRINT`**（聚合 QFQ 表 + source_watermark）。

指纹维度（逐字校验）：精确表集合（管理范围限定）、列集合（reject_extra 拒绝多余列）、列顺序（strict_order 可选）、类型、**物理 NOT NULL**（显式 + inline PK + 复合 PK 列均 true，与 DuckDB PRAGMA table_info.notnull 一致）、**DEFAULT 规范化**（canonicalize_default）、PK 列与顺序、**外键**（duckdb_constraints）。

工具：`parse_physical_contract(ddl)`、`verify_fingerprint(conn, fp, *, reject_extra, strict_order)`、`project_legacy_contract_shape(fp)`（供 SCHEMA_CONTRACT_DUCKDB 确定性投影）、`pre_cutover_generation(table, source)`（静态 pre-cutover 哨兵映射）。

### 12.2 状态机五态（`qfq_schema_status.py` 重写）

| 状态 | 判定 | 普通 init 行为 |
|---|---|---|
| EMPTY_OR_NEW | 无任何 QFQ 重锚专属表（qfq_*，含 B-3 新表）/shadow/migration 残留 | 代码侧 DDL==target 时创建完整 2.1 + 回读校验 |
| COMPLETE_2_1 | 物理结构与 TARGET_MAIN_DB_2_1 逐字一致 | **严格只读 no-op**（仅 verify_fingerprint，0 写操作） |
| COMPLETE_2_0 | 物理结构与 LEGACY_MAIN_DB_2_0 逐字一致 | 写前 fail-fast（QfqSchemaMigrationRequired） |
| PARTIAL_OR_MIXED | 其它（缺表/缺列/多列/类型/NOT NULL/default/PK 错/shadow 残留/混合） | 写前 fail-fast |
| UNKNOWN | introspection 异常（阻断 6 可达） | 写前 fail-fast |

关键：**不**用 SCHEMA_VERSION 常量判版本；**不**把当前 SCHEMA_CONTRACT_DUCKDB 当 2.1 定义。**真实正式库只读探测 = COMPLETE_2_0**（阻断 1 修复，opt-in 测试 `QS_PRODUCTION_READONLY=1` 证明）。

### 12.3 init 五态行为（`qfq_reanchor_schema.init_duckdb_schema` 重写）

- `assert_code_ddl_matches_target_2_1()`：第一条 DDL 前代码侧预检（DDL==target，否则空库也 fail-fast）。
- COMPLETE_2_1 → `verify_fingerprint` 只读返回（**0 CREATE/ALTER/DROP/DML/migrate**）。
- EMPTY_OR_NEW → 建表 + source_watermark 共享 DDL + 回读校验。
- 2.0/partial/unknown → `assert_init_allowed` 写前 fail-fast。
- **`_migrate_duckdb_columns` 从普通 init 彻底移除调用**；保留为受控内部工具（不承担 2.0→2.1 migration，B-3b 处理）。
- daemon/CLI/orchestrator/测试的幂等性改由**物理契约逐字校验**保证，不再靠 CREATE TABLE IF NOT EXISTS 重跑。

### 12.4 完整 2.1 schema（DDL_DUCKDB 15 表 + DUCKDB_COLS + SCHEMA_CONTRACT_DUCKDB）

- 既有表 B-3 新列（NOT NULL 无业务 DEFAULT）：trigger_queue +6（trigger_id_version/price_source/source_generation/cutover_id/retired_at/retire_reason）、cycle_run/bootstrap_run +3、fresh_capture/reanchor_event +2、4 张 swap 表 +generation 列并扩 PK。
- **source_watermark 8 列**（source_generation/cutover_id NOT NULL 无 DEFAULT，PK 不变）。
- 4 张新表：discovery_baseline / source_cutover / active_cutover（含 FK→source_cutover）/ cycle_lease。
- 4 张 swap 表新空库用最终 2.1 PK（pending_backfill 8 列 / observation_cursor 4 列 / anchor_state 4 列 / watermark_intent 6 列）。
- `SCHEMA_CONTRACT_DUCKDB = project_legacy_contract_shape(TARGET_QFQ_2_1_FINGERPRINT)`（来源固定，不再运行时解析当前 DDL）。
- 机械测试保证 `parse(DDL_DUCKDB) == TARGET`、`DUCKDB_COLS == DDL 列顺序`、`SCHEMA_CONTRACT == target 投影`。

### 12.5 静态 pre-cutover 写入兼容桥（B-5 替换为动态，B-6 激活 mcp-gen1）

所有受影响生产 INSERT 提供确定值（B-3a.2 修正 P0-3）：`price_source = cfg.price_source`（**MCP 配置下仍是 mcp，绝不改写为 xtquant**；xtquant 配置下仍是 xtquant）、`source_generation=cfg.source_generation`（pre-cutover 哨兵 xtquant-legacy）、`cutover_id=cfg.cutover_id`（哨兵 legacy-xtquant-pre-cutover）、`trigger_id_version=1`。**source 保留真实值不改写**（source=mcp 仍是 mcp；哨兵只表 active cutover 未激活）。改动文件：writers.py（source_watermark 3 路径 + backfill + `_init_tables` 安全闸 + 内部 price_source/source_generation 参数）、qfq_event_discovery.py（3 trigger + cursor + TriggerRecord 构造，全用 self.cfg.*）、qfq_resident_orchestrator.py（cycle/backfill/intent/bootstrap/_advance_watermark + apply_reanchor_for_security 传 price_source，全用 self.cfg.*）、qfq_reanchor_engine.py（event/anchor，price_source 由调用方传入）、qfq_fresh_capture.py、qfq_orchestrator_types.py（TriggerRecord.COLS/CycleRun/WatermarkIntent/FreshCaptureRecord）。

### 12.6 测试与回归

- 新增/重写 `tests/test_qfq_schema_status.py`（32 测试，无 mock、冻结指纹 fixture、全状态矩阵、init 五态 spy 证明 0 写、过渡门禁、契约一致性）。
- opt-in `tests/test_qfq_production_schema_readonly.py`（marker/env gate，默认跳过，read_only=True，证明正式库=COMPLETE_2_0 + fail-fast）。
- 既有 INSERT 测试适配 2.1 NOT NULL（~6 文件）。
- **回归**：全 QFQ 测试集 `704 passed / 2 failed`，2 个失败均为预存（`test_supersede_bootstrap_runs_clears_old_blocked`=DuckDB `.rowcount==-1` 留 B-5；`test_capture_writes_download_trace`=download_trace NULL 属 B-2/fresh_capture 预存）。ConfigLint default（0 错误）+ mcp_only（0 错误）通过。

### 12.7 正式库不变证明

测试前后 `data/quantstudio.db` 与 `data/qfq_aux.db` 的 canonical path / size / mtime / SHA-256 四项**逐字节一致**（quantstudio.db SHA-256 `53e85fed...03d5c677` / 14,996,746,240 -；aux `59667901...9e2158b9`）。只读探测用 read_only 连接。

### 12.8 B-3a.2 集中补修（2026-08-05，首次集中审核未通过后的闭环补修）

> B-3a 首次集中机械审核未通过（4 P0 + 4 P1）。本节记录 B-3a.2 补修结果，待再次机械复核。

**P0-1 共享表写前门禁**：`detect_schema_status` 在判 EMPTY_OR_NEW 前，先校验 source_watermark / trade_calendar 是否不存在或已精确匹配 target；旧 6 列 / 错误 / partial 共享表 → PARTIAL_OR_MIXED（不再误判空库）。
**P0-2 DuckDBWriter `_init_tables` 安全闸**：第一条 DDL 前调 `_assert_source_watermark_init_safe`——旧 6 列 / partial / 错误 → 抛 `_WriterSchemaMigrationRequired` 写前 fail-fast（不再到运行时 advance_watermark 才 -inderException）。
**P0-3 MCP 真实 price_source**：所有 pre-cutover 生产写入改用 `cfg.price_source`（mcp 仍是 mcp，绝不改写 xtquant）；cursor / 3 trigger INSERT / TriggerRecord 构造 / cycle_run / bootstrap_run / pending_backfill / apply_reanchor_for_security 调用 / writer backfill 内部参数（默认 xtquant，MCP 显式传 mcp）。source_generation/cutover_id 用 cfg 值（pre-cutover 哨兵）。
**P0-4 full fingerprint UNIQUE/CHECK**：新增 `_table_unique_constraints` / `_table_check_constraints`；`verify_fingerprint` 校验 UNIQUE/CHECK；`assert_code_ddl_matches_target_2_1` 也比较 UNIQUE/CHECK/FK；introspection helper（`_table_columns`/`_table_pk`/`_table_foreign_keys`）**不再吞异常**——查询错误向上传播，由 `detect_schema_status` 统一转 UNKNOWN；`verify_fingerprint` 先用 `_table_exists` 明确判存在。
**P1-1 审计列冲突更新**：三条 source_watermark upsert 的 `ON CONFLICT DO UPDATE` 增 `source_generation=EXCLUDED.source_generation, cutover_id=EXCLUDED.cutover_id`。
**P1-2 遗留脚本**：`scripts/rebuild_industry_tables.py`（source_watermark 8列显式 + 非 QFQ 哨兵）、`scripts/qfq_batch2_canary.py`（bootstrap +3 列）、`scripts/qfq_batch2_multiround.py`（bootstrap +3 列、trigger +4 列含 trigger_id_version=1）。
**P1-3 trade_calendar 列顺序**：LEGACY/TARGET 指纹 + DDL_DUCKDB + writers.py DDL/`_table_columns` 全部统一为正式库实际 introspection 顺序（cal_date, is_open, source, updated_at, exchange, pretrade_date）；状态识别用严格列顺序（`strict_order=True` 默认）。

### 12.9 B-3a.3 最终补修（2026-08-05，第二次集中审核后的闭环）

> B-3a.2 再次集中机械审核未通过（2 P0）。B-3a.3 完成最终补修，并于 2026-08-05 通过第三次集中机械复核。

**P0-1 DuckDBWriter 完整 QFQ 五态写前门禁**：`_init_tables` 第一条 DDL 前不再只查 source_watermark 子契约，改为复用 `detect_schema_status` 完整五态预检（`_assert_qfq_schema_init_safe`）。仅 EMPTY_OR_NEW/COMPLETE_2_1 放行；COMPLETE_2_0/PARTIAL_OR_MIXED/UNKNOWN 全部抛 `_WriterSchemaMigrationRequired` 写前 fail-fast。机械验证：仅一张 legacy qfq_trigger_queue（无 source_watermark）→ fail-fast + 表集合不变；完整 legacy 2.0 / 2.0+2.1 混合 / shadow 残留 / introspection 异常 → 全 fail-fast；空库 / 完整 2.1 → 放行。

**P0-2 统一静态 pre-cutover identity**：新增 `qfq_schema_contracts.pre_cutover_qfq_identity(price_source)` → `{price_source: 真实值, source_generation: "xtquant-legacy", cutover_id: "legacy-xtquant-pre-cutover"}`。**所有 B-3a QFQ 写入必须调用此函数**，不得直接读 `cfg.source_generation`/`cfg.cutover_id`。覆盖：cursor / 3 类 trigger INSERT / 2 TriggerRecord 构造（event_discovery 用 `_ident` 局部；resident_orchestrator 用 `self._ident` 实例属性于 `__init__` 预算）；reanchor_engine event/anchor、watermark_intent、fresh_capture、source_watermark backfill 已硬编码 legacy 哨兵（正确）。**关键规则**：即使配置显式传 `mcp-gen1`/`cut_not_active`，B-3a 落库也只持久化 legacy 哨兵（B-5 动态、B-6 激活）。机械验证：危险配置（price_source=mcp, source_generation=mcp-gen1, cutover_id=cut_not_active）下 cursor/cycle/trigger 等落库 generation=xtquant-legacy（非 mcp-gen1）；静态扫描禁止 B-3a 生产写入直接用 cfg.source_generation/cutover_id。

### 12.10 B-3a 第三次集中机械复核通过（2026-08-05）

> B-3b 原"拟实施范围"（shadow swap / 原子 RENAME / 回填 / 幂等 / 故障注入）已在 §13 落地为真实实现。

- Writer 完整五态门禁机械复现通过：partial、完整 legacy 2.0、shadow/混合、UNKNOWN 均在第一条 DDL 前 fail-fast，表集合不变；EMPTY_OR_NEW / COMPLETE_2_1 放行。
- 危险配置 `price_source=mcp, source_generation=mcp-gen1, cutover_id=cut_not_active` 下，cycle/intent/cursor/bootstrap/event/anchor 均保留真实 `price_source=mcp`，但 generation/cutover 强制写 `xtquant-legacy` / `legacy-xtquant-pre-cutover`，未提前激活 MCP generation。
- 回归证据：`tests/test_qfq_schema_status.py` 53 passed；正式库 opt-in 只读审计 2 passed；扩展回归 732 passed、2 个既有失败（`.rowcount==-1` / `download_trace NULL`）；ConfigLint default 0 errors/1 existing warning、mcp_only 0/0。
- `data/quantstudio.db` 与 `data/qfq_aux.db` 的 canonical path、size、mtime、SHA-256 前后不变；未 stage/commit/push。
- **结论：B-3a 审核通过，允许进入 B-3b 本地 migration runner 实现；仍禁止正式库迁移和 Git 同步。**

---

## 13. B-3b 完整实施结果（2026-08-05）

> **状态**：B-3b.3 已于 2026-08-05 经 CodeBuddy 独立复审通过（P0=0、P1=0）。

### 13.1 新建 `quantstudio/pipeline/qfq_schema_migration.py`

显式 2.0→2.1 migration runner。核心 API：

- `migrate_reanchor_2_0_to_2_1(db_path, *, allowed_root, apply=False, failure_injection=None) -> MigrationReport`
- `MigrationReport`：db_path/source_status/target_status/dry_run/applied/already_current/tables_rebuilt/tables_created/row_counts_before/row_counts_after/hashes_before/hashes_after/validation_results/started_at/finished_at
- CLI：`python -m quantstudio.pipeline.qfq_schema_migration --db <path> --allowed-root <path> [--apply]`（默认 dry-run；`--allow-production` 无效）

### 13.2 安全边界（强制）

- **正式生产库绝对硬拒绝**：`_assert_not_production` 用 `os.path.samefile` 处理绝对/相对/`..`/大小写/symlink/junction/别名；在打开 read-write 连接前触发；**不接受 `--allow-production` 绕过**。
- **allowed-root**：目标 db 必须位于 `--allowed-root` 子路径下；等于根也拒绝。
- **状态门禁**：只接受 COMPLETE_2_0（apply/preflight）或 COMPLETE_2_1（幂等 already_current，0 写）；EMPTY_OR_NEW/PARTIAL_OR_MIXED/UNKNOWN 全 fail-closed；预检任何 `__b3b_v2`/`__b3b_legacy`/B-3 已知 shadow 残留 → fail-closed。

### 13.3 重建策略

- **重建 9 张 QFQ 表 + source_watermark**（共 10 张）：仅 ALTER ADD COLUMN 得不到 target 冻结的精确列顺序，故用 shadow 表 `<table>__b3b_v2` 按 target DDL 建全 → 映射复制 legacy 行（含历史回填）→ 校验 → 事务内统一 swap。
- **新建 4 张 B-3 表**（迁移完成时为空，不自动激活 cutover）。
- **保留不重建**：qfq_bootstrap_item、trade_calendar（须验证与 target 一致，否则 fail-closed）。

### 13.4 历史回填映射（§五）

- trigger_queue：trigger_id_version=1、price_source=xtquant、source_generation=xtquant-legacy、cutover_id=legacy-xtquant-pre-cutover、retired_at/retire_reason=NULL（不重算历史 trigger ID）。
- cycle_run/bootstrap_run：price_source=xtquant、source_generation=xtquant-legacy、cutover_id=legacy-xtquant-pre-cutover。
- pending_backfill：price_source=xtquant、source_generation=xtquant-legacy（新 8 列 PK）。
- observation_cursor：price_source=xtquant、source_generation=xtquant-legacy（新 4 列 PK）。
- reanchor_event：source_generation/cutover_id 回填，保留原 price_source。
- anchor_state：source_generation 回填，保留原 price_source（新 4 列 PK）。
- watermark_intent：source_generation/cutover_id 回填，保留原 source（新 6 列 PK）。
- fresh_capture：source_generation/cutover_id 回填，保留原 source。
- source_watermark：按 **table_name** 分类回填（QFQ 价格表→xtquant-legacy/legacy-xtquant-pre-cutover；非 QFQ→not-qfq-managed/not-applicable）。

### 13.5 单一原子事务

-EGIN → 全部 shadow 建 + 复制 + 行数/PK/NOT NULL 校验 + 新建 4 表 + 统一 swap（`<table>`→`<table>__b3b_legacy`，`<table>__b3b_v2`→`<table>`）+ DROP 临时 legacy + target fingerprint 回读 → COMMIT。任一步 ROLL-ACK → 重开库恢复 COMPLETE_2_0。

### 13.6 内容 hash 规范（CONTENT_HASH_VERSION = "b3b-sha256-v1"）

**B-3b.1 修订**（原"NULL 固定编码 `\N`"已废弃——与真实字符串 `\N` 碰撞）：固定列序（**迁移前用 LEGACY 指纹列，迁移后用 TARGET 指纹列**，不猜测）→ 按 PK 排序 → NULL 用独立 token `\x00NULL`（NUL 字节 + NULL，与真实字符串 `\N`/`<len>:<value>` 形态不同，无碰撞）→ 非 NULL 用**长度前缀** `<len>:<encoded>`（消除 `\x1f`/`\x1e`/`:` 分隔符碰撞）→ bool 先于 int → float 规范化（NaN/inf/-0.0）→ UTF-8 → SHA-256。snapshot **fail-closed**（查询失败抛 `QfqMigrationError`，不静默写空串）；`content_hash_version` 进入 `MigrationReport` 与 CLI JSON。

### 13.7 中断恢复（13 故障点）

`FAILURE_POINTS`：before_begin/after_first_shadow_create/after_partial_shadow_create/during_first_copy/after_all_copy/before_validation/after_validation/after_first_rename/after_partial_rename/after_new_tables_create/before_final_fingerprint/before_commit/after_commit_before_report。前 12 失败后重开库=COMPLETE_2_0；第 13（COMMIT 后报告前）重跑=COMPLETE_2_1 already_current。子进程崩溃测试验证 DuckDB 未提交事务自动回滚。

### 13.8 测试（三轮证据准确分列）

| 轮次 | 专项测试 | 扩展回归 |
|---|---|---|
| **B-3b 首版**（v6.6） | 37 passed | 775 passed / 2 failed / 1199 deselected |
| **B-3b.1**（v6.6.1） | 57 passed / 3 skipped | 795 passed / 2 failed / 3 skipped / 1199 deselected |
| **B-3b.2**（v6.6.2） | 70 passed / 1 skipped | 808 passed / 2 failed / 1 skipped / 1199 deselected |
| **B-3b.3（CodeBuddy 独立复审通过）** | **82 passed / 1 skipped** | **822 passed / 0 failed / 1 skipped / 1199 deselected** |

B-3b.3 coverage: final report path reserved with O_EXCL before DB write access; two concurrent callers yield exactly one winner; no publish temp file and no os.replace; report path identity checked before read-write open and before COMMIT; frozen states PENDING/DRY_RUN_COMPLETE/ROLLED_BACK/MIGRATION_COMMITTED/ALREADY_CURRENT/FAILED_PRECHECK; committed report failure raises MigrationCommittedReportError and is recoverable through a new already-current audit; hard crash leaves PENDING and restores legacy schema/data; all prior migration, hash, PK, NOT NULL, alias, and production-guard tests remain covered.

### 13.9 回归与安全证据

- B-3b.3 CodeBuddy 独立复审确认：**822 passed / 0 failed / 1 skipped / 1199 deselected**，P0=0、P1=0。
- ConfigLint default 0 errors/1 existing warning、mcp_only 0/0。
- opt-in 正式库只读审计 2 passed（正式库=COMPLETE_2_0）。
- `data/quantstudio.db` 与 `data/qfq_aux.db` 的 path/size/mtime/SHA-256 前后逐字节不变。
- 未实施正式库迁移、未数据回填生产库、未 active cutover、未动态 mcp-gen1、未 B-5/B-6 世代过滤、未 stage/commit/push。

### 13.10 B-3b.1 / B-3b.2 / B-3b.3 补修（2026-08-05/06）

**B-3b.1（第一次复核 3 P0 + 2 P1 补修，v6.6.1）**：P0-1 内容 hash 失效（10 表迁移前 hash 全空）→ 重写 `_content_hash`（显式 fingerprint/fail-closed/编码规范化 NULL `\x00NULL` + 长度前缀）；P0-2 CLI/report 契约（`--report` + 完整字段 + content_hash_version）；P0-3 测试过报（逐表回填/UNKNOWN/os._exit/hardlink-symlink/CLI/黄金hash）；P1-1 design migration 章节 superseded；P1-2 b3r 元数据。57 passed / 3 skipped。

**B-3b.2（第二次复核 2 P0 + 2 P1 补修，v6.6.2）**：P0-1 `--report` 可覆盖 db/正式库（高危数据破坏 + 正式库门禁旁路）→ `_assert_report_path_safe`（allowed-root containment/拒 report==db/拒 report==正式库主+aux/exclusive create/微秒+UUID）+ 迁移前预检；P0-2 `_row_count` fail-closed + validation NOT NULL 完整字段 + already-current 只读审计报告 + hard-crash 业务内容等价 + 同卷 hardlink + directory junction 文件别名；P1-1 b3r §13.6/13.8 修正；P1-2 design §9 anchor_state legacy 保留策略统一。70 passed / 1 skipped。

**B-3b.3（Codex convergence implementation；CodeBuddy 独立复审已通过）**： replaced the temp-file publish/replace design with final-path `O_CREAT|O_EXCL` reservation before any DB write access. The same owned descriptor stores PENDING and terminal states. No `os.replace` or post-migration path creation remains. Parent-directory validation and production/aux/DB identity checks occur before migration; file identity is rechecked before read-write open and COMMIT. Post-COMMIT report failures use `MigrationCommittedReportError` and recover through a fresh already-current audit. Evidence: **82 passed / 1 skipped** focused tests and **822 passed / 0 failed / 1 skipped / 1199 deselected** extended regression. CodeBuddy 已独立复跑专项、扩展回归、正式库四项证据与攻击矩阵，并判定 P0=0、P1=0。


**CodeBuddy P1 regression closure（独立复审已确认）**： `supersede_bootstrap_runs` no longer uses DuckDB `UPDATE.rowcount` (`-1`); it calculates the affected run count with the exact UPDATE predicate before updating. `write_fresh_capture` now persists the complete frozen `FRESH_CAPTURE_COLS` contract, including `download_trace`, min/max timestamps, and generation fields, while using `FreshCaptureRecord` defaults for optional fields omitted by older internal record-like callers. Focused related suites: **56 passed**. Final extended regression: **822 passed / 0 failed / 1 skipped / 1199 deselected**.

### 13.11 B-4/B-5 下一步

B-3b 通过后：B-4（生产执行前检查点 + staging 副本演练：迁移/中断/续跑/回退/MCP bootstrap/首轮 discover）、B-5（全链路 SQL 世代过滤 + RETURNING 替换 `.rowcount`，含修复 `test_supersede_bootstrap_runs_clears_old_blocked`）。

## 14. B-4 全量 staging 副本演练（权威日期 2026-08-05；本地完成，CodeBuddy 独立复审通过）

> 主机产生的结构化证据时间为 2026-08-06 04:40–04:45 +08:00，属于已记录的主机时钟跨日异常；本项目按当前会话权威日期 2026-08-05 登记。

### 14.1 新增演练器与边界

新增 `scripts/qfq_b4_staging_drill.py` 与 `tests/test_qfq_b4_staging_drill.py`。演练器不进入 daemon/生产调用链，默认只执行零数据库写、零 run-dir 写的 preflight；显式 `--execute` 才创建 `output/mcp_migration/<run-id>/` staging 副本。复制期间同时持有 `.daemon.lock` 与 `.collector_run.lock`，并在 preflight/持锁窗口内拒绝非空 WAL/journal sidecar；正式主库/aux 前后做 canonical path、size、mtime_ns、SHA-256 对比。

B-4 明确排除：B-5 动态 generation/cutover SQL 过滤、全局 RETURNING 改造、discovery-baseline CAS；B-6 trigger 退役、active pointer、`mcp-gen1` 激活。演练报告固定 `production_ready=false`、`git_sync_authorized=false`。

### 14.2 全量演练证据

- 命令：`python scripts/qfq_b4_staging_drill.py --run-id b4_20260805_final --execute`
- 结果：exit code 0；目录 `output/mcp_migration/b4_20260805_final/`，约 43.657 Gi-（单次 run 近似值）。
- baseline 分支：COMPLETE_2_0。
- normal 分支：dry-run=DRY_RUN_COMPLETE；`before_commit` 注入=ROLLED_BACK 且逻辑 hashes 与初始一致；正常 apply=MIGRATION_COMMITTED；二次 apply=ALREADY_CURRENT；最终 COMPLETE_2_1。
- recovery 分支：`after_commit_before_report` 产生 committed error，DB=COMPLETE_2_1；新 report 路径执行得到 ALREADY_CURRENT，验证用户接受的 TDB42 恢复契约。
- 离线 MCP bootstrap：不连接网络；bootstrap 前后 trigger 数不变；因子首轮 observation/revision 新增均为 0。
- 当前 pre-B-5 `stock_dividend` discovery 为全表 hash 扫描：首轮新增 2181，立即重放新增 0。该数字是 B-5 discovery-baseline/CAS 前的真实基线，不能提前宣称首轮净新增为 0。
- `qfq_active_cutover` 行数 0；10 张含 generation 表中 `source_generation='mcp-gen1'` 行数全 0；MCP cursor 落 `price_source=mcp, source_generation=xtquant-legacy`，保持静态 pre-cutover 桥。

### 14.3 正式库零改动

- main：size `14996746240`；mtime_ns `1785861379886337200`；SHA-256 `53e85feda38bc71a2595317092a5d03270b40558a9ea97c375bdf75703d5c677`。
- aux：size `2641793024`；mtime_ns `1785830385636695100`；SHA-256 `5966790153c4966a8dfbe61b59f50880c98e4dd49705053fffacdcda9e2158b9`。
- 前后逐项一致；正式库仍 COMPLETE_2_0，未执行正式迁移。

### 14.4 当前门禁

B-4 已于 2026-08-06 经 CodeBuddy 独立复审通过（P0=0/P1=0），权威进度报告已登记 B-4 PASS，允许进入 B-5 本地实施。Git staged index 继续为空；未 commit/push/PR；正式库迁移与 GitHub 同步仍需分别取得明确确认。

### 14.5 COMMIT 后真实 hard-crash 边界补修

最终回归曾在并行 DuckDB pytest 干扰下出现 Windows `0xC0000005`。未放宽测试。将 `after_commit_before_report` 从“正常 close 后”移动到 durable COMMIT 后、正常 close/report 前；串行验证直接 `os._exit` 30/30、原始 pytest 20/20、migration+B-4 87/1、扩展 827/1。失败诊断历史保留在权威报告 v6.7.1，最终闭环见 v6.7.2。
