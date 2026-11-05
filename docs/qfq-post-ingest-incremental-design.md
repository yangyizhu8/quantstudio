# 方案③：QFQ post-ingest 增量改造（消除 O(N) 往返）—— 设计 v3（②审条件②答复 · T6 闭合）

- v1：`a15ec41`（00:52）｜v2：`75cbda5`（01:55，T1/T2/T4 闭合）｜**v3：2026-11-06 02:45（T6 闭合 · G12 实现路径改写）**
- ②审状态：**通过**（2026-11-06 02:20，附两条件：①三源口径采信 ②T6=实施硬前置）
- 判型：**纯性能型**（观测表逐行相同、alert/trigger 相同、下游复权结果相同）

> **v3 相对 v2 的实质变化（范围收窄，非扩张）**：G12 的实现路径由「**DuckDB `qfq_observation_cursor` 加列**（触发铁律 24 契约变更六件套）」改为「**aux SQLite 新建独立游标表**（零契约变更）」。依据见 §2.2 的三组实测证据。

---

## 一、背景与动机（根因已定量 · 非推测）

### 1.1 现象

客户「全部执行增量拉取卡 2 天」；`factor_value_pt` 单任务**硬啃 16 小时**；QFQ post-ingest 阶段挂住。

### 1.2 根因（插桩实测 · `docs/evidence/qfq-post-ingest-profiling-20261031.md` §八）

| 项 | 实测读数 |
|---|---|
| 观测表规模 | `qfq_factor_observation` **43,900,131 行**（11-06 复测 44,434,134 行） |
| `to_write` 首条 | `("STOCK","000001", 1686787200000=2023-06-15, 116.713)` ⇒ **不是今日增量，是历史因子点** |
| 单次往返 `_rt` | `[1.32, 0.59, 0.126, 0.157, …] ms` ⇒ **0.13–1.32 ms** |
| **N+1 写入** | 每条观测 **2–4 次 sqlite 往返** ⇒ N=4390 万 ⇒ **8790 万 ～ 1.76 亿次往返** |
| **累计耗时** | 4390 万 × 0.13ms ≈ **95 分钟** ～ × 1.32ms ≈ **16 小时** |
| 消费侧第二处 O(N) | `_security_effective_dates` **逐 code** 查 `SELECT DISTINCT factor_time FROM qfq_factor_observation WHERE asset_type=? AND code=?`（8179 codes × 扫 4390 万行）｜`qfq_resident_orchestrator.py:1746-1756`，调用点 `:858`/`:1694` |

**⇒ 根因表述**：post-ingest 每轮把**全量历史观测（4390 万条）逐条**「查 last 值 → 比对 → 写回」，往返次数 **O(N)**；单次虽快但总量放大到数十亿次 ⇒ **16 小时级挂死**。

### 1.3 为什么「时间下界增量」不成立（丢失面论证）

上游因子**持续微调历史值**（既有知识条目《已知错误-因子版本漂移致rebase自锁》即此族；例：`000008@2018-01-02` 跳变 4.355×）⇒ 修订可能发生在**任意历史 factor_time** ⇒ 时间下界会把它们排除 ⇒ `revision_no≥2` 永不产生 ⇒ **漏检** ⇒ 用「漏检」换「提速」违反纯增益。

### 1.4 变更集三源（**全部闭合** · 不依赖上游）

| 源 | 口径 | 取数方式（实测） | 完备性 |
|---|---|---|---|
| **① 因子值修订** | `ObservationResult.revisions`（`RevisionRow` 明细） | **已产出但当前无人消费** ⇒ 直接透传 | 与真值比对的结果 ⇒ 天然完备 |
| **② 新增 factor_time** | `ObservationResult.factor_new` | 已被 `qfq_event_discovery.py:468-469` 消费 ⇒ 复用 | 同上 |
| **③ 除权事件新增 code** | `stock_dividend` 本轮新增行的 code 集 | **读侧增量**：`WHERE update_time > <增量水位>`（`update_time` 实测格式 `'YYYY-MM-DD HH:MM:SS'`、非空率 **100%**、全 `data_source='mcp'`、`div_proc='实施'`；近 7 天仅 **8 行** ⇒ 增量集极小） | `update_time` 是**行级写入时间戳** ⇒ 读侧可判「本轮新增」 |

**⚠️ 我 11-05 设想已被实施核实推翻**：`qfq_observation.py:16`「未变也 UPDATE `last_seen_run_id`」⇒ **`last_seen_run_id == 本轮` 不能当变更集**（未变 code 也计入 ⇒ 恒为全量）⇒ 改用 `revisions`/`factor_new`。

**上游确实没有变更集**（实测排除）：`adj_factor`/`fund_adj` 为 `INSERT OR REPLACE` 无 per-run 日志；`qfq_jump_audit` **rows=0**；`adj_factor_snapshot` 仅 **5780 行**聚合（无 factor_time 粒度）。

---

## 二、范围与边界

### 2.1 改动文件（4 处）

| 文件 | 改动 |
|---|---|
| `quantstudio/pipeline/qfq_observation.py` | ①「查 last 值」逐条往返 ⇒ **批量取**（按 `(asset_type, code)` 分组/分块 SELECT + 内存映射）；②**只写变化行**（new → INSERT rev1；revised → INSERT rev+1 + alert pending）；③未变行**批量**刷新 `last_seen_run_id/last_seen_at`（分批 UPDATE，非逐条） |
| `quantstudio/pipeline/qfq_resident_orchestrator.py` | ④逐 code 遍历（`:858`/`:1694`）⇒ **只遍历本轮 changed code 集** |
| `quantstudio/pipeline/qfq_event_discovery.py` | ⑤透传 `result.revisions`（**当前未被消费**） |
| `quantstudio/pipeline/qfq_reanchor_schema.py`（aux `init_sqlite_schema`） | ⑥【v3 改】新增 aux 游标表 `qfq_obs_incremental_cursor`（见 §2.2） |

### 2.2 【v3 改写】增量水位的存储 —— **零契约变更路径**

**原 v2 方案**：给 DuckDB 主库的 `qfq_observation_cursor` 加列 `incremental_consumed_as_of`。
**问题（②审条件② · T6 核出）**：`qfq_observation_cursor` 在契约文件 `qfq_schema_contracts.py` 出现 **7 处**且列入 `_V2_SWAP_TABLES`、进入 `TARGET_QFQ_2_1_FINGERPRINT` ⇒ **受管表** ⇒ 加列＝**契约变更** ⇒ 触发铁律 24 六件套（契约期望列集 + 版本常量 + `detect_schema_status` 双版本放行 + `writers.py` 安全闸放行集 + 迁移 runner + 测试矩阵）⇒ 工作量与风险显著放大。

**v3 方案（更优）**：增量水位落 **aux SQLite 独立新表**，**不动任何受管表**。

| 证据 | 实测 |
|---|---|
| aux 观测表族是否受契约约束 | `qfq_factor_observation` / `qfq_deep_audit_cursor` / `qfq_factor_revision_alert` 在契约文件出现 **0 处** ⇒ **不校验** |
| aux 建表自由度 | `init_sqlite_schema`（`qfq_reanchor_schema.py:797`）幂等建 **14 张表**（`CREATE TABLE IF NOT EXISTS`）⇒ 加表有成熟先例 |
| aux 表演进方式 | 幂等 `CREATE TABLE IF NOT EXISTS` ⇒ **部署时自动创建，无迁移 runner、无双版本放行、无安全闸放行集** |

**新表设计**（aux SQLite，与既有 aux 表同模式）：

```sql
CREATE TABLE IF NOT EXISTS qfq_obs_incremental_cursor (
    detector_name TEXT PRIMARY KEY,      -- 'stock_dividend' / 'stock_adj_factor' / 'etf_fund_adj'
    consumed_as_of TEXT,                 -- 'YYYY-MM-DD HH:MM:SS'（同 stock_dividend.update_time 格式）
    last_run_id   TEXT,
    updated_at    TEXT
);
```

**命名避让**：aux 侧已存在同名 `qfq_observation_cursor`（14 表之一）⇒ 新表**必须异名**（`qfq_obs_incremental_cursor`）以免与 DuckDB 侧同名表混淆/误写。

**游标语义澄清（保留）**：DuckDB 侧 `qfq_observation_cursor.cursor_as_of` 是「**本轮上界**」，**不是「已消费位置」** ⇒ 即便改用 aux 表，仍**不得**用 `cursor_as_of` 做 `update_time >`（会重复消费）⇒ 由 G11/G12 承担。

### 2.3 明确不改动

- **不改判据**：epsilon 语义、`revision_no` 递增规则、alert 写入契约、`factor_new` trigger 语义、`code_contract` 预过滤（fail-safe 阻断 2）全部原样；
- **不改上游写入侧**（`daemon.py` / `qfq_maintenance.py` / 各 adapter）⇒ 上游零改动、零风险；
- **不改任何受管表的列集**（v3 核心收益）⇒ **不触发铁律 24**；
- **不改 `_security_effective_dates` 语义**（仍是「除权事件 ∪ 因子观察时间」），只改**遍历范围**；
- **不改 doom-loop 相关**（日线写冲突/水位冻结不混本案，见 T5）。

### 2.4 开关与回退

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `QS_QFQ_OBS_INCREMENTAL` | `1` | `0` ⇒ **逐位还原旧路径**（逐条往返 + 全量消费侧遍历 + 全量除权集）⇒ ②审前置③ |

---

## 三、任务拆解（功能点分解表 · 逐点门控）

| 点 | 功能 | 独立可验证判据 | 负向对照 |
|---|---|---|---|
| **G1** | 批量取 last 值（替代逐条查） | 同批输入下，批量取与逐条取结果集**逐行相等**（对拍：随机 200 code × 全 factor_time） | 故意错配 `asset_type` ⇒ 不等 |
| **G2** | 只写变化行 | 新增/修订行的 `revision_no`/`factor_value`/`first_seen_run_id` 与旧路径**逐位相同** | 伪造 epsilon 边界（差值恰在阈值）⇒ 旧路径记 unchanged、新路径不得记 revised |
| **G3** | 未变行批量刷新 | 未变行 `last_seen_run_id` 更新为当轮 run_id；`revision_no` 不变；行数不变 | 关闭刷新 ⇒ 该列停更（对拍识别） |
| **G4** | alert outbox 等价 | `qfq_factor_revision_alert` 行集与旧路径**逐位相同**（含 `alert_id`） | 去掉 alert 写入 ⇒ 行集缺 ⇒ 红灯 |
| **G5** | 消费侧 changed-code 遍历 | 遍历 code 集 == ①`revisions` code ∪ ②`factor_new` code ∪ **③本轮新增除权事件 code** | 只取 ①②（漏除权）⇒ 红灯 |
| **G5b** | 【v3 增补·实施期识别】**G5 的遍历范围不得小于「本轮到期单元」集合** | 实施核出：`_claim_and_merge` 的 unit 来自「①本轮新 trigger ②`promote_scheduled_due` 排期到期 ③`recover_pending_due` 待重试」三类来源（`run_post_ingest` 的 recover→discover→claim 序，见 `qfq_resident_orchestrator.py:1844-1860`）；而 `_security_effective_dates` 的 O(N) 计算发生在**unit 生成阶段**（`:858`）。⇒ 若把遍历范围直接缩到「changed code」，会**漏掉 ②③ 两类到期单元** ⇒ 正确范围 = **changed code 集 ∪ 本轮到期/待重试 code 集** | 构造「因子未变但有到期单元」的 code ⇒ 缩范围后该 unit 消失 ⇒ 红灯 |
| **G6** | 真实库对拍（②审前置②） | 同一真实快照：旧路径 vs 新路径 ⇒ `qfq_factor_observation`（44M 行）**全表 hash 相等** + `alert` 表相等 + `qfq_trigger_queue` 相等 + 水位相等 | 引入单点差异 ⇒ hash 不等 |
| **G7** | revision_no 链构造性证明（②审前置②） | 批量取 last == 该 `(code,factor_time)` 的 `max(revision_no)` 行 ⇒ 链 rev1…revN 无缺号 | 人工挖空某 rev ⇒ 链不连续 ⇒ 红灯 |
| **G8** | 回退开关 | `QS_QFQ_OBS_INCREMENTAL=0` ⇒ 全表 hash 与旧路径相等、耗时回到旧量级 | 开/关两态结果同一（仅耗时不同） |
| **G9** | 50762 排水 run（②审前置④） | `QfqPendingSla` 逾期量从 50762 下降且**不新增** | — |
| **G10** | 收益实测 | 同库同快照：post-ingest 段耗时（新 vs 旧）对照；**往返次数计数器**从 O(N) 降至 O(变化数+批数)（计数器入库，非估计） | — |
| **G11** | 除权增量集不重不漏 | `update_time > consumed_as_of` 取出的 code 集 == 旧路径「本轮新增除权」口径；**重复消费检测**：同水位连跑两次，第二次增量集须为空 | 水位未写回 ⇒ 第二次仍非空 ⇒ 红灯 |
| **G12** | 【v3 改】aux 增量游标表 | `qfq_obs_incremental_cursor` 建表幂等（重复调用不报错、列集不变）；每轮写回 `consumed_as_of`；**受管表列集零变化**（`verify_fingerprint` 对 DuckDB 主库仍 True） | 水位写回失败 ⇒ 次轮重复消费 ⇒ 红灯；误改受管表 ⇒ 指纹 False ⇒ 红灯 |

**门控纪律**：一点绿才进下一点；任一红停在该点，修复后重过。

---

## 四、风险描述

| 风险 | 影响 | 规避 |
|---|---|---|
| **丢失面①：历史值修订漏检** | 漏检因子漂移 ⇒ rebase 自锁（既有已知错误族） | 变更集以 `revisions`（真值比对结果）为准，**不用时间下界**；G6 全表 hash 对拍 |
| **丢失面②：除权事件新增 code 未纳入** | `effective_dates` 少算除权日 ⇒ 复权价错 | G5 + G11 双门；口径 = `update_time > consumed_as_of`（实测格式/非空率已验） |
| **丢失面③：未变行不再刷新 `last_seen_run_id`** | 若下游以该列判「本轮已观测」⇒ 误判 | **已闭合**：仓内检索该列**除写入侧外零消费方** ⇒ 无下游依赖；G3 保留为「审计可追溯」的可选动作 |
| **重复消费** | 除权增量集被重复计入 ⇒ 重复触发 | G11 重复消费检测 + G12 水位写回；首轮空水位走全量 |
| **同名表混淆**（v3 新识别） | aux 与 DuckDB 均有 `qfq_observation_cursor` ⇒ 新表若同名会误写/误判 | 新表**异名** `qfq_obs_incremental_cursor`；G12 断言受管表列集零变化 |
| aux 表与主库事务不同源 | 水位写入与观测写入不在同一事务 ⇒ 崩溃时水位可能领先 | 水位写回置于**观测写入同一事务尾**（aux SQLite 同一 conn）；崩溃后以「水位 ≤ 已观测 max」为不变式，G12 断言 |
| 批量取与逐条取不等价 | 漏/错判修订 | G1 对拍 200×全量；G7 链连续性证明 |
| 未变行批量 UPDATE 放大 | 写放大 | 分批 UPDATE + 只更新受影响 `(code,factor_time)` 集 |
| 内存占用（44M 行 last 值映射） | 内存压力 | 按 code 分组流式切块 ⇒ 不全量驻留 |
| **性能判据虚高** | 「提速」来自漏检 | G6 hash 对拍为硬门；收益读数须与 hash 相等同时成立 |

---

## 五、验收要点

1. **功能**：G1–G12 全绿；
2. **等价性**：真实库对拍三表 hash + 水位相等（G6）——**纯性能型的核心硬门**；
3. **场景**：
   - **无变化日**：往返次数 ≈ O(批次数)；观测表 hash 与旧路径相等；除权增量集 ≈ 空；
   - **有修订日**：`revision_no` 递增与旧路径一致、alert 行集一致；
   - **有除权事件日**：changed code 集含该 code（G5）；
   - **重复消费**：同水位连跑两次，第二次增量集为空（G11）；
   - **契约面**：DuckDB 受管表 `verify_fingerprint` 仍 True、列集零变化（G12）；
4. **边界**：空快照、单 code、单 factor_time、epsilon 恰在阈值、批切块边界、首轮空水位；
5. **回退**：`QS_QFQ_OBS_INCREMENTAL=0` 逐位还原（G8）。

---

## 六、质量判据

| # | 判据 |
|---|---|
| 1 | 观测表 / alert / trigger / 水位 **四方 hash 或行集相等**（旧 vs 新）——任何不等即验收失败 |
| 2 | 往返次数从 O(N=4390万) 降至 O(变化数 + 批数)，且**计数可机检**（代码内计数器，非估计） |
| 3 | 丢失面三源逐条有 G5/G6/G11 对应证据，无「未覆盖项以假设代替证明」 |
| 4 | 回退开关零残留（`=0` 时行为与旧路径逐位相同） |
| 5 | 上游零改动（`daemon.py`/`qfq_maintenance.py`/各 adapter 不在本方案 diff 内） |
| 6 | **受管表零变更**（v3 新增）：`verify_fingerprint` 对 DuckDB 主库仍 True；契约文件与迁移 runner **零改动** ⇒ 不触发铁律 24 |

### 6.1 收益预估（**待 G10 实测证**）

| 面 | 现状 | 改造后（估算） | 降幅 |
|---|---|---|---|
| 因子侧往返 | 4390 万 × 2–4 次 | 变化数 × 2–4 次 + 批次数（按 code 分组 ≈ 8179 批） | **无变化日 ↓99.98%** |
| 消费侧遍历 | 8179 code 全遍历 | changed code 集（无变化日 ≈ 0） | **↓≈100%** |
| post-ingest 段总耗时 | 95 分钟～16 小时 | **分钟级**（须 G10 证） | 待证 |
| WAL 膨胀 | H2 曾测 1.56GB | **H1 的镜像**（往返少 ⇒ 事务少 ⇒ WAL 涨得少）⇒ 治理 O(N) 往返后自然收敛 | 随 H1 |

**WAL 现状实测（v2 · T4 闭合）**：`qfq_aux.db-wal` = **0 MB**；`journal_mode=wal`；`PRAGMA wal_checkpoint(PASSIVE)` = **0.0 ms**；`freelist_count=1`；主库 `quantstudio.db-wal` 不存在。⇒ **H2 当前不成立**，收益测算**不带 WAL 惩罚**。

---

## 七、待补项（②审需一并裁）

| # | 待补 | 状态 | 需谁 |
|---|---|---|---|
| ~~T1~~ | 除权事件增量口径 | **✅ 闭合**：`stock_dividend.update_time`（`'YYYY-MM-DD HH:MM:SS'`、非空率 100%、mcp 源、近 7 天 8 行）⇒ 读侧增量 | 已闭合 |
| ~~T2~~ | 下游是否依赖 `last_seen_run_id` | **✅ 闭合**：仓内检索**零消费方** | 已闭合 |
| ~~T4~~ | WAL 现状复测 | **✅ 闭合**：WAL=0MB、checkpoint 0.0ms、freelist=1 ⇒ H2 当前不成立 | 已闭合 |
| ~~T6~~ | 游标加列是否触发铁律 24 | **✅ 闭合（v3 改路径规避）**：aux 观测表族在契约文件 **0 命中**；`init_sqlite_schema` 幂等建 14 表有先例 ⇒ **改用 aux 独立新表 ⇒ 零契约变更、不触发铁律 24**；DuckDB 受管表列集**零变化** | 已闭合 |
| **T3** | `QfqPendingSla` 50762 排水触发条件与顺序 | 未查（计数点 `quality_audit.py:754`） | **与 dev 协同** |
| **T5** | 「不与 doom-loop 混案」边界确认 | 本方案只动 `qfq_observation`/`orchestrator`/`event_discovery`/aux 建表，**不碰 writers/水位提交、不动受管表** ⇒ 边界成立 | 调度确认 |
| **T7** | 【v3 新增】aux 与 DuckDB **同名表** `qfq_observation_cursor` 的既知混淆风险 | 已用异名新表规避（G12 断言受管表列集零变化） | 已闭合（随 v3 落档） |

---

## 附一：知识反哺检索留痕（N8 / R1）

- 知识反哺：[命中] 已知错误-因子版本漂移致rebase自锁（12-客户运维/已知错误/，2026-11-06）——丢失面①的既有判据族（上游持续微调 ⇒ 禁止时间下界增量）
- 知识反哺：[命中] 已知错误-QFQ空分钟表致重锚永久跳过水位冻结（12-客户运维/已知错误/，2026-11-06）——QFQ 族相邻已知问题，用于边界划清
- 知识反哺：[命中] 案例卷宗-QFQ空分钟水位冻结案（12-客户运维/案例卷宗/，2026-11-06）——同族案例档
- 知识反哺：[无命中] knowledge/{contracts,db,platform,playbooks,probes,ptrade-deploy}（QFQ/post-ingest/增量/O(N)往返 条目面），2026-11-06

## 附二：与既有工作的关系

| 项 | 关系 |
|---|---|
| 插桩（`0e50c81`/`fa4a57e`） | 本方案根因依据（§1.2 全部读数来自插桩 §八） |
| 方案② 全局段缓存（`07e6238`） | 治审计段；**不改善 post-ingest** ⇒ 两案互补不重叠 |
| doom-loop（日线写冲突/水位冻结） | **不混本案**（T5 待确认） |
| 铁律 24（schema 指纹） | **v3 不触发**（不动受管表列集）⇒ 无需六件套 |
| QFQ 增量 B 线 | 本方案即 B 线实现层（用户 10-31 五项②审前置逐条对应 G1–G12） |
