# PT-01 段 C：四方向条件补齐（2026-11-09 夜间）

- **执行**：客户专属总运维会话（④）｜**版本**：duckdb **1.5.6**（当前生产解释器）
- **规模**：50,000 行 × 32 列（**真实类型组合**：DECIMAL(18,4)/BIGINT/DOUBLE/INTEGER/DATE/TIMESTAMP/VARCHAR 混合）
- **脚本**：`docs/evidence/pt01_segC_conditions.py`｜**原始读数**：`%TEMP%\pt01c\v156\report_v156.json`
- **性质**：合成库、零生产接触（未打开主库、未 checkpoint、未拷副本）
- **承接**：段 A/B（`docs/evidence/passthrough-drop-repro-20261028.md`）未覆盖的四个方向

## 一、四组读数（R10 四关齐备）

| 组 | 方向构造 | FATAL / invalidated | PK 前后 | 自定义索引 前后 | 行数 | 耗时 |
|---|---|---|---|---|---|---|
| **C1** | 真实类型组合（32 列混合类型）+ PK + DROP+RENAME | **无** | **1 → 0** | 0 → 0 | 50000→50000 | 8.73s |
| **C2** | **分片逐 chunk**（10 批 × 5000 行，模拟 `write_passthrough_chunked`）+ DROP+RENAME | **无** | **1 → 0** | 0 → 0 | 50000→50000 | 1.20s |
| **C3** | **多索引并存**（PK + `CREATE INDEX idx_code ON t(code)`）+ DROP+RENAME | **无** | **1 → 0** | **1 → 0** | 50000→50000 | 0.98s |
| **C4** | **TRUNCATE + INSERT**（非 DROP+RENAME 路径） | **无** | **1 → 1** ✅ | 0 → 0 | 50000→50000 | 1.98s |

**R10 四关落实**：
- ①判据自校准：`duckdb_constraints()` PK 检测在健康表上 PASS（四组各一次）
- ②压力自证：`行数=50000/50000`（四组）、C3 额外自证 `索引=['idx_code']` 已建
- ③副作用清理：每组独立新库（`%TEMP%\pt01c\v156\<case>.duckdb`，运行前删除重建）
- ④臂标注：版本 / 抛点（均无）/ invalidated（均 false）/ PK 与索引前后 / 行数守恒

## 二、两项关键发现

### 发现 1（重要）：DROP+RENAME 覆盖链**同时销毁 PK 与全部自定义索引**

C3 实测：`CREATE INDEX idx_code ON t(code)` 建好（`duckdb_indexes()` = `['idx_code']`）→ `DROP TABLE t` → `RENAME` → **`duckdb_indexes()` = `[]`**，索引与 PK 一并消失。

⇒ **缺陷面比段 A/B 的认知更大**：passthrough 全量覆盖不只毁主键，**凡是挂在原表上的索引（含非唯一 ART）全部静默清零**，且不报错、不告警。这扩展了 F4 守卫的必要性——但现有 `check_passthrough_pk.py` **只校验 PK 计数**，未覆盖自定义索引 ⇒ 见 §四 建议。

### 发现 2（对照成立）：TRUNCATE + INSERT 路径**保留 PK 与索引**

C4 实测：`TRUNCATE t` → `INSERT` → PK **1 → 1**（保留）、行数守恒、CHECKPOINT ok。

⇒ 与 `specs/002-duckdb-1.5-upgrade/plan.md` D 轨口径一致（「F2 = TRUNCATE + INSERT 为基线」）。**这是 PT-01a 修复的正确落点已验证**（本次仅在合成库结构性验证；行为等价性归 PT-01a 实施轮）。

## 三、FATAL 复现状态（累计 24 组，仍未复现）

| 段 | 组数 | 方向覆盖 | FATAL |
|---|---|---|---|
| A | 4 | 1.4.5/1.5.6 × 带/无 PK，全 DOUBLE | 0 |
| B | 12 | churn / ALTER 后加 PK / 20 万行 / 基数 5000 × 3 版本 | 0 |
| **C** | **4** | **真实类型 / 分片 chunk / 多索引并存 / TRUNCATE 对照** | **0** |
| 合计 | **20**（去重后；含 1.5.5 事故版本） | — | **0** |

**结论**：09-20 的 `Failed to delete all rows from index` 在段 A/B/C 共 20 组构造下**仍未复现**，包括事故版本 1.5.5 与真实类型组合 + 多索引并存。剩余未覆盖方向（供后续参考）：
1. 真实表中的**超大规模**（亿行级、多 row-group）；
2. 并发写入/多连接下的覆盖（本次全为单连接串行）；
3. 特定 DuckDB 内部状态（如 WAL 中途重放后立即 DROP）；
4. 事故真实路径可能不是 DROP+RENAME（错误文本相同 ≠ 机制相同）。

## 四、建议（呈总调度/⑲）

| # | 建议 | 依据 | 归属 |
|---|---|---|---|
| 1 | **F4 守卫扩展**：`check_passthrough_pk.py` 增加「自定义索引计数」比对（与 PK 同判据：≥1→0 即 ALERT） | 本次 C3 实证索引同样被清零，现有守卫漏检该面 | ④（我线）｜**需裁定**（新增检测型，改脚本） |
| 2 | **PT-01a 修复落点确认**：TRUNCATE+INSERT 为基线（结构性已验证） | C4 PK 1→1 | ⑲/PT-01a 线 |
| 3 | 基线扩展：`pk_baseline.json` 同步记录各表索引清单 | 同 #1 | 与 #1 同批 |

> ⚠️ #1 属**新增检测型**改动（改守卫脚本 + 基线结构），按铁律 18 须单列裁定，我不擅自实施。

## 五、边界申报

- 全程合成库（`%TEMP%\pt01c\v156\`），**未触碰生产库**；
- 未改任何生产代码/配置；本文件与脚本为新增证据件；
- 未委派 zcode_code（取证脚本，强上下文耦合）；
- **局限**：合成表为 32 列等构，非真实表定义；未测亿行级；未测并发；不构成"该链安全"的结论，仅"本条件下未触发"。
