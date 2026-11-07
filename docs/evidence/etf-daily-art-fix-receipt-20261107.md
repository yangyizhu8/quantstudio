# 【P0 修复回执】etf_daily DuckDB ART 索引损坏 —— 重建完成（V1/V2/V3/V6 通过，V4/V5 待观察）

> 执行：节点⑳ ｜ 日期：2026-11-07 21:0x ｜ 依据：②审 2026-11-07 通过（条件=T1–T4 读数随实施窗呈报；V1/V2 失败即回退）
> 方案件：`docs/duckdb-art-index-corruption-fix-design-20261107.md`

---

## 一、T1–T4 实测（②审条件项，逐条呈报）

| # | 实测项 | 结果 | 结论 |
|---|---|---|---|
| **T1** | `REINDEX` 语句是否存在（duckdb **1.5.6**） | `REINDEX TABLE t` / `REINDEX` / `REINDEX DATABASE` **全部 `ParserException: syntax error at or near "REINDEX"`**；但 `duckdb_keywords()` 中存在 `('reindex',)` —— **关键字已登记但语句未实现** | **A 不可行** |
| **T2** | `etf_daily` 能否完整 SELECT | **能**：行数 **2,220,975**、31 列、全列 hash **20,569,599,082,417,213,915,958,292** | **B 可行** |
| **T3** | `COPY FROM DATABASE` 单表粒度 | **整库 ✔**（`COPY FROM DATABASE src TO dst` 成功，两表位移正确）；**单表 ✘**：`src.t1` 与 `(t1)` 限定两种写法均 `ParserException` | **C 仅支持整库**（故 B 优先） |
| **T4** | `invalidated` 后是否必须重启进程 | 不构造真实 fatal（危险）；按**保守策略=独立进程**执行（Obs-2 已实测新进程可只读开库） | 保守策略已采纳 |

**决策树落点（②审授权自行选定）**：T1 否 → T2 是 ⇒ **选定候选 B（单表导出→重建→导入）**。
依据：REINDEX 语句不存在（A 排除）；etf_daily 可完整读出（B 前置满足）；C/D 影响面远大于单表且 `COPY FROM DATABASE` 不支持单表粒度。

## 二、前置保障（回退点）

| 项 | 读数 |
|---|---|
| **全库备份** | `D:\qs_backup_art_20261107_203042\quantstudio.db`，**67,075,321,856 B（62.47 GB）** |
| 备份校验 | 大小一致 + **三处抽样 SHA256 逐位一致**（`46c503de11cf…`；全量 SHA256 因持柄阻塞已被终止，改用抽样+下述 dump 双保险） |
| **表级 dump** | `D:\qs_art_fix_20261107\etf_daily_dump.parquet`，**101.2 MB**，SHA256 `6e528daa3e53…`，**回读行数与 hash 均一致** |
| 基线清单 | `D:\qs_art_fix_20261107\baseline.txt`（行数/hash/DDL/约束/31 列定义） |
| daemon | 已停（实测无进程、无 status 文件），DuckDB read_write 独占打开成功 |

## 三、重建执行（21:03:35 → 21:03:45，**10 秒**）

```sql
DROP TABLE etf_daily;                      -- ✅
-- 按原 DDL 重建（含 PRIMARY KEY(code, "time") 与 2 个 NOT NULL）  -- ✅
COPY etf_daily FROM '...etf_daily_dump.parquet' (FORMAT parquet);  -- ✅
CHECKPOINT;                                -- ✅
```

**DDL 完全一致**（含损坏的那个 `PRIMARY KEY(code, "time")` 约束）。

## 四、验收读数（V1–V6）

| # | 判据 | 读数 | 结果 |
|---|---|---|---|
| **V1** | 行数一致 | `2,220,975` == 基线 `2,220,975` | ✅ |
| **V2** | 内容校验和逐位一致 | `20569599082417213915958292` == 基线（**逐位相同**） | ✅ |
| **V3** | 唯一性约束生效（不再 ART fatal） | 插入重复 PK ⇒ **`ConstraintException: Duplicate key … violates primary key constraint`**（**正常约束拒绝**，非 `Corrupted unique ART index`/`gated leaf`） | ✅ |
| **V3 强化** | 真实写入路径 | `INSERT` 新行 ✅ / `UPDATE` ✅ / `DELETE` ✅ / 事务回滚**零残留** ✅；最终行数与 hash 仍与基线一致 | ✅ |
| **V6** | 既有测试全绿 | W5 + QFQ 红线域四文件（`test_watermark_read_visibility` / `test_qfq_resident_orchestrator` / `test_qfq_orchestrator_cli` / `test_qfq_orchestrator_types`）⇒ **75 passed**，0 失败 | ✅ |
| **V4** | 一轮真实采集水位推进 | **待 daemon 重启后观察**（见 §六） | ⏳ |
| **V5** | 水位语义未受损 | 同上 | ⏳ |

## 五、修复前后对照（核心证据）

| 时点 | 行为 |
|---|---|
| **修复前**（11-07 06:03:11） | 写 etf_daily ⇒ `FATAL: Corrupted unique ART index "PRIMARY_etf_daily_0"` ⇒ `database has been invalidated` ⇒ 整轮失效 ⇒ 水位冻结 |
| **修复后**（11-07 21:03–21:04） | 写 etf_daily ⇒ `INSERT/UPDATE/DELETE` 全部正常；重复 PK ⇒ **标准 `ConstraintException`**（索引语义恢复）⇒ **不再出现 ART 错误与 invalidated** |

## 六、待办：V4/V5（需重启 daemon）

| 步 | 内容 |
|---|---|
| 1 | **重启 daemon**（`--mode forever`）——按铁律 19，启停属生命周期动作，**已呈报请协调**（不自行启动） |
| 2 | 观察一轮真实采集：`etf_daily` 写入是否无 fatal；`post-ingest` 是否通过 |
| 3 | **V4**：水位是否推进（基线 `etf_daily wm=09-04` ⇒ 目标推进至表内 MAX 附近、重拉窗口 ≤1 天） |
| 4 | **V5**：水位不领先表内 MAX（`_get_safe_watermark` 不变式） |
| 5 | 若 V4/V5 失败 ⇒ 按方案 §5 回退（备份 + dump 均在案） |

## 七、回退方式（若需）

```powershell
# 方式一：整库回退（最彻底）
Stop-Process <daemon-pid>          # 确保无进程
Copy-Item D:\qs_backup_art_20261107_203042\quantstudio.db data\quantstudio.db -Force
# 方式二：仅还原本表
python -c "import duckdb; c=duckdb.connect(r'data\quantstudio.db'); c.execute('DROP TABLE etf_daily'); ..."
# （或用 dump 重新走一遍本回执 §三 流程）
```

## 八、§丙 熔断护栏（未实施，按方案待后续）

丙-2（近 7 天最小锚）+ 丙-3（`QS_ALLOW_FULL_REBUILD` 逃生阀）**尚未实施**——属独立子件（方案 §6），不与 P0 捆绑。本次仅完成 P0。

## 九、R4 沉淀

- `本轮沉淀：N/A`（理由：索引损坏缺陷族待本修复观察 1–2 轮确认无复发后入册；现处「已修未验完」态，不宜提前沉淀为定论）
