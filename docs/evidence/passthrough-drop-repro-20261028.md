# PT-01 段 A：passthrough DROP+RENAME 全量覆盖链定谳（合成库）

- **取证日期**：2026-10-28 23:18–23:19（Asia/Shanghai）
- **任务来源**：`QuantStudio` 想法评估链 `.specify/assessments/duckdb-2.0-upgrade/`，decision v9.1 §8
- **执行方**：客户运维会话（本文件作者）
- **性质**：只做定谳、不改代码；**全合成库，零生产接触**（未打开、未 checkpoint、未拷贝主库）

---

## 一、任务与判据

判定 `建 _pt_tmp → INSERT → DROP 原表 → RENAME` 这条 passthrough 全量覆盖链，在
duckdb **1.4.5** 与 **1.5.6** 上、对**带 PK** 与**不带 PK**的表，是否抛
`Failed to delete all rows from index` / `database has been invalidated`。

**R10 四关（缺一结论作废）**：

| 关 | 要求 | 本实验落实 |
|---|---|---|
| ① 判据自校准 | 健康库上同一判据必须 PASS | `PK 判据`：建表后 PK=期望值、不存在表 PK=0 ⇒ 四组均 **PASS** |
| ② 压力自证 | 写入行数 / WAL > 0 | 四组均 `行数=50000/50000` ⇒ **PASS** |
| ③ 副作用清理 | 每组独立新库 | 每组 `pk{0,1}.duckdb` 运行前删除重建；v2 运行前整体清空 `%TEMP%\pt01` |
| ④ 臂标注 | 版本 / 抛点 / invalidated | 见下表逐组标注 |

---

## 二、四组读数

表结构：**32 列**（`code VARCHAR`、`time TIMESTAMP`、29×`DOUBLE`、`freq VARCHAR`），
**50000 行**；`code` 取自 50 个值（高重复，模拟股票代码列）；PK = `(code, time, freq)`。

| # | 版本 | 表形态 | 建 _pt_tmp | INSERT | **DROP t** | **RENAME** | 是否 FATAL | invalidated | 抛点 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **1.4.5** | 带 PK | PK=0（CTAS 无 PK） | 50000 行 / 0.06s | **成功 0.0s** | **成功 0.0s** | ❌ 无 | ❌ 否 | — |
| 2 | **1.4.5** | 无 PK | PK=0 | 50000 行 / 0.05s | **成功 0.0s** | **成功 0.0s** | ❌ 无 | ❌ 否 | — |
| 3 | **1.5.6** | 带 PK | PK=0 | 50000 行 / 0.05s | **成功 0.0s** | **成功 0.0s** | ❌ 无 | ❌ 否 | — |
| 4 | **1.5.6** | 无 PK | PK=0 | 50000 行 / 0.05s | **成功 0.0s** | **成功 0.0s** | ❌ 无 | ❌ 否 | — |

**行数守恒**：四组均 `50000 → 50000`（RENAME 后表完整）。
**全链耗时**：1.4.5 带 PK **1.0s** / 无 PK 0.3s；1.5.6 带 PK **0.32s** / 无 PK 0.28s。

> **附带发现（版本行为差异，非本任务判据）**：v1 脚本用 `executemany` 逐行插入时，
> 1.5.6 + 带 PK 组运行 **>30 分钟未完**（1.4.5 同脚本数秒完成）；改用单条
> `INSERT … SELECT … FROM range()` 后，1.5.6 组 **0.17s** 完成。
> ⇒ 该差异位于 **Python `executemany` 路径**，**不在 DROP+RENAME 链**；
> 但作为 1.5.x 性能回归线索登记（未深究，不作结论）。

---

## 三、附加观测（任务要求的两行）

**观测 1：RENAME 之后表上还有没有 PK？**

> **没有。四组一致：带 PK 组 `PK 1 → 0`，RENAME 后 `duckdb_constraints()` 查不到任何 PRIMARY KEY。**
> 原因确定：`_pt_tmp` 由 `CREATE TABLE … AS SELECT … LIMIT 0` 建出（CTAS 不继承约束），
> `ALTER TABLE … RENAME TO t` 只改名不搬约束 ⇒ **原表的 PK 随 `DROP TABLE t` 一起消失**。
> ⇒ **passthrough 全量覆盖链每执行一次，就静默清空目标表的主键，且不报错、不告警。**

**观测 2：DROP 抛错时的行数是否 2048 对齐？**

> **本次四组均未抛错，故无 2048 对齐读数。**
> （2048 是 DuckDB 向量长度；09-20 事故文本 `Only deleted 0 out of 2048 rows` 的 2048 与此吻合，
> 但本次未能在合成库上重现该抛错，**不能**对该对齐关系下结论。）

---

## 四、结论归属

### 归属：**A 成立；B 不成立；但 09-20 的 FATAL 本身在本实验条件下未复现**

| 解释 | 判定 | 依据 |
|---|---|---|
| **A：09-21 重建后这些表丢了 PK（历史 schema ≠ 现状）** | ✅ **成立，且机制已定位** | 本实验证明覆盖链会**静默清空 PK**。若 passthrough 任务曾对某表执行过一次全量覆盖，该表 PK 即永久消失且无告警 ⇒ 这**正好解释**「69 个 passthrough 任务指向的表与 25 张带 PK 的 canonical 表交集为空」 |
| **B：炸的是分片通道 `write_passthrough_chunked`，被指向 canonical 大表（如 `stock_minutes`）** | ❌ **不成立（反证）** | 若该通道曾对 `stock_minutes` 走过 DROP+RENAME，其 PK 应已被清空；但 2026-10-28 实测 `stock_minutes` **仍有 PK(code,time,freq)**（E7 体检中 2.12 亿行 PK 点查全部命中）⇒ 说明该表**未被这条链覆盖过** |
| **C：09-20 的 FATAL 另有触发条件（本次未复现）** | ⚠️ **保留待查** | 本次四组（32 列 × 5 万行 × 带/无 PK × 两版本）**全部通过**，未抛 `Failed to delete all rows from index`、未 invalidated。说明仅"多列 + 带 PK + DROP/RENAME"**不足**以触发；缺的条件可能包括：①先经 UPDATE/DELETE churn 使索引产生删除标记；②PK 为后加（`ALTER TABLE ADD PRIMARY KEY`）而非建表声明；③行数/row-group 规模跨多个 2048 向量；④真实表的索引列基数分布。**建议作为 PT-01 段 B 的输入** |

### 对「解释 A」的补强事实（与 E7 联合可交叉验证）

- 2026-10-28 E7 体检：主库 **41 PK 全部索引完好**（含 `stock_minutes` 2.12 亿行）。
- 本实验：覆盖链**一次即清空 PK**。
- ⇒ 若 69 张 passthrough 表当前**无 PK**，与"它们被覆盖过"自洽；而 41 张仍有 PK 的表，
  说明它们**未走**这条覆盖链。二者不矛盾，共同指向 A。

---

## 五、段 B：条件补齐（2026-10-28 23:33–23:35）

段 A 未复现 FATAL，故补跑四个候选缺条件，并**补测事故版本 1.5.5 本身**（段 A 只测了 1.4.5/1.5.6）。

### B-1 四场景 × 三版本 = 12 组

| 场景 | 行数 | PK 方式 | churn | code 基数 | 1.4.5 | **1.5.5（事故版本）** | 1.5.6 |
|---|---|---|---|---|---|---|---|
| S1_churn_50k | 50,000 | 建表声明 | 35% | 5,000 | 通过 / PK 1→0 | 通过 / PK 1→0 | 通过 / PK 1→0 |
| S2_alter_pk_50k | 50,000 | **ALTER 后加** | 0 | 5,000 | 通过 / PK 1→0 | 通过 / PK 1→0 | 通过 / PK 1→0 |
| S3_big_churn_200k | **200,000** | 建表声明 | 35% | 5,000 | 通过 / PK 1→0 | 通过 / PK 1→0 | 通过 / PK 1→0 |
| S4_big_alter_churn | **200,000** | ALTER 后加 | 35% | 5,000 | 通过 / PK 1→0 | 通过 / PK 1→0 | 通过 / PK 1→0 |

- **12 组全部通过**：无 `Failed to delete all rows from index`、无 `database has been invalidated`；
- **12 组 PK 全部 1→0**（PK 丢失在三个版本上均复现，与段 A 一致）；
- R10 四关在每组齐备（自校准 PASS / 行数自证 / 每组新库 / 臂标注）。
- 200,000 行 ≈ **97.6 个 2048 向量**（跨多个向量条件已满足）。

### B-2 段 B 结论

**「09-20 的 FATAL」在四个候选条件补齐后仍未复现，且已排除"版本差异"解释**（连事故版本 1.5.5 本身也未复现）。

⇒ 触发条件**不在**以下候选内：churn 前置 / ALTER 后加 PK / 跨多向量规模 / 索引列基数。
⇒ 仍需解释的剩余可能（供 PT-01 段 C 参考，本次不定谳）：
① 真实表的 **32 列类型组合**（本次全为 DOUBLE，真实表含 VARCHAR/TIMESTAMP 混合）；
② 该链在生产中被 **并发/分片** 方式调用（`write_passthrough_chunked` 逐 chunk，非单条 INSERT）；
③ 事故时表上存在 **多索引并存**（非仅 PK）；
④ 事故的真实触发路径可能不是 DROP+RENAME（错误文本相同不等于机制相同）。

### B-3 附带发现（**单独立项，不得混入 DROP 链结论**）

**executemany 性能回归线索（1.5.x）**

| 项 | 读数 |
|---|---|
| 场景 | 同一脚本、同一数据（32 列 × 50,000 行、带 PK）、仅换灌数方式 |
| 1.4.5 + `executemany` | 数秒内完成 |
| **1.5.6 + `executemany`** | **>30 分钟未完成**（人工终止） |
| 1.5.6 + 单条 `INSERT … SELECT … FROM range()` | **0.17s** 完成 |

⇒ 差异位于 **Python `executemany` 调用路径**，**不在 DROP+RENAME 链**（后者三版本均 0.0s）。
⇒ 登记为独立线索，**不作结论**，待需要时以专门实验确认（可能影响 1.5.x 上的批量写入性能）。

---

## 六、F4 巡检守卫（零改动，已生效）

**目的**：passthrough 覆盖会静默清空 PK，故以「覆盖后 PK 计数」作为止损观测点。

| 项 | 内容 |
|---|---|
| 脚本 | `scripts/check_passthrough_pk.py`（严格只读；daemon 运行时输出 SKIP 退出码 3，非失败） |
| 模式 | `--snapshot` 写基线 / `--check` 比对 / 无参打印清单；`--db`、`--baseline` 可指定 |
| 告警判据 | **`n_base >= 1 且 n_now == 0` ⇒ ALERT（退出码 2）**；`0→≥1` 为 WARN；表消失为 WARN |
| 主库基线 | 已生成 `docs/evidence/pk_baseline.json`：**101 表 / 41 表有 PK**（2026-10-28 23:34:36） |
| 自检 | 主库 `--check` ⇒ `OK: PK 状态与基线一致`，exit 0 |
| **端到端负向对照** | scratch 库（t_a/t_b 有 PK、t_c 无）→ snapshot(2/3) → **执行真实 passthrough 覆盖** → check ⇒ **`★PK 丢失: t_a（1 → 0）`，exit 2** ✅ 判据有效 |

**接入建议**：作为每日哨兵第 5 步（紧随三判据 grep 之后）；daemon 运行时可对**副本**执行，
或在停服窗内对主库执行。**不做任何源码改动**（新增脚本属新增检测型能力）。

---

## 七、对首跳实施顺序的建议

**任务第七条的触发条件未命中**：`1.5.6 + 带 PK` 组**未出现 FATAL / invalidated**
⇒ **首跳（1.4.5 → 1.5.6）无需为 PT-01 让位**，可按原计划（纯 EOL 驱动）推进。

**但 PT-01 必须独立、优先修复**，理由与版本无关：

1. **它是数据完整性缺陷，且比 FATAL 更隐蔽**：每轮 passthrough 全量覆盖**静默销毁目标表 PK**，
   不报错、不告警；丢 PK 后 `ON CONFLICT` 冲突探测失效 ⇒ 后续 upsert 可能插入重复行；
2. **它与本次升级无关**（1.4.5 与 1.5.6 表现一致）⇒ **不能指望升级解决**；
3. **建议的修复方向**（供 PT-01 方案阶段参考，本次不定谳实现）：
   - 覆盖链改为 `TRUNCATE/DELETE + INSERT`（保留表对象与约束），或
   - `_pt_tmp` 建表时**显式带上与原表一致的 PK 约束**（先 `SHOW`/`duckdb_constraints()` 取约束再重建），或
   - 覆盖完成后**校验 PK 存在性**并在缺失时自动重建 + 告警（防线）；
4. **立即生效的守卫（零改动，纯观测）**：把「passthrough 覆盖后 PK 计数」纳入巡检，
   凡覆盖后 PK 从 ≥1 变 0 即告警——本实验已证明该变化可被 `duckdb_constraints()` 精确检出。

---

## 八、证据文件清单

| 文件 | 说明 |
|---|---|
| `docs/evidence/pt01_segA_v2.py` | 段 A 执行脚本（SQL 批量灌数版，含 R10 四关与逐步骤计时） |
| `docs/evidence/pt01_segB_conditions.py` | 段 B 执行脚本（四场景条件补齐，含 ALTER PK / churn / 20万行 / 基数5000） |
| `scripts/check_passthrough_pk.py` | **F4 巡检守卫**（只读 PK 存在性检查；--snapshot/--check） |
| `docs/evidence/pk_baseline.json` | 主库 PK 基线（101 表 / 41 有 PK，2026-10-28 23:34:36） |
| `%TEMP%\pt01\v155\` | **1.5.5（事故版本）**段 A 读数与合成库 |
| `%TEMP%\pt01b\v155|v145|v156\` | 段 B 三版本读数与合成库 |
| `%TEMP%\venv_ddb155\` | duckdb **1.5.5** 隔离 venv（事故版本复验用） |
| `%TEMP%\pt01\v145\report_v145.json` | 1.4.5 两组结构化读数（含各步耗时、PK 前后、行数前后） |
| `%TEMP%\pt01\v156\report_v156.json` | 1.5.6 两组结构化读数 |
| `%TEMP%\pt01\v145\pk{0,1}.duckdb` | 1.4.5 两组合成库（可复检 `duckdb_constraints()`） |
| `%TEMP%\pt01\v156\pk{0,1}.duckdb` | 1.5.6 两组合成库 |
| `%TEMP%\venv_ddb156\` | duckdb 1.5.6 隔离 venv（Python 3.11.9），独立于生产环境 |

**复现命令**：

```powershell
# 1.4.5
python docs\evidence\pt01_segA_v2.py v145
# 1.5.6
& "$env:TEMP\venv_ddb156\Scripts\python.exe" docs\evidence\pt01_segA_v2.py v156
```

---

## 九、边界申报

- **未碰生产库**：未打开 `data\quantstudio.db`、未执行任何 checkpoint、未拷贝主库；
  全部读写发生在 `%TEMP%\pt01\` 合成库。
- **未改任何源码与配置**：本任务只新增本报告与执行脚本两个文件。
- **未用 release notes 代替读数**：本文件所有结论均来自本机实测。
- **未委派编码**：脚本由本会话直接编写（调试型取证脚本，强上下文耦合）。
- **已知局限**：①合成表为 32 列等构，非真实表 schema；②未复现 FATAL ⇒
  「该链安全」**不成立**，只能表述为「本条件下未触发」；③未测 2048 对齐关系。
