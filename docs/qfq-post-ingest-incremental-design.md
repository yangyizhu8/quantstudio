# 方案③：QFQ post-ingest 增量改造（消除 O(N) 往返）—— 设计 v2（②审呈报稿）

- v1：`a15ec41`（2026-11-06 00:52）｜**v2：2026-11-06 01:50（T1/T2/T4 闭合后升版）**
- 判型：**纯性能型**（观测表逐行相同、alert/trigger 相同、下游复权结果相同）
- 前置：用户 10-31 定的五项②审前置（变更集语义+丢失面 / revision_no 链构造性证明+真实库对拍 / `QS_QFQ_OBS_INCREMENTAL=0` 回退 / `QfqPendingSla 50762` 排水 run / 不与 doom-loop 混案）

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

**⇒ 根因表述**：post-ingest 每轮把**全量历史观测（4390 万条）逐条**「查 last 值 → 比对 → 写回」，往返次数 **O(N)**；单次虽快（0.13ms）但总量放大到数十亿次 ⇒ **16 小时级挂死**。

### 1.3 为什么「时间下界增量」不成立（丢失面论证）

上游因子**持续微调历史值**（既有知识条目《已知错误-因子版本漂移致rebase自锁》即此族；例：`000008@2018-01-02` 跳变 4.355×）⇒ 修订可能发生在**任意历史 factor_time** ⇒ 时间下界会把它们排除 ⇒ `revision_no≥2` 永不产生 ⇒ **漏检** ⇒ 用「漏检」换「提速」违反纯增益。

### 1.4 【v2 增补】变更集三源**全部闭合**，且**不依赖上游**

| 源 | 口径 | 取数方式（实测） | 完备性 |
|---|---|---|---|
| **① 因子值修订** | `ObservationResult.revisions`（`RevisionRow` 明细） | **已产出但当前无人消费** ⇒ 直接透传 | 与真值比对的结果 ⇒ 天然完备 |
| **② 新增 factor_time** | `ObservationResult.factor_new` | 已被 `qfq_event_discovery.py:468-469` 消费 ⇒ 复用 | 同上 |
| **③ 除权事件新增 code** | `stock_dividend` 中**本轮新增**的行 | **读侧增量**：`WHERE update_time > <增量水位>`（`update_time` 实测格式 `'YYYY-MM-DD HH:MM:SS'`、非空率 **100%**、全 `data_source='mcp'`、`div_proc='实施'` 全量；近 7 天仅 **8 行** ⇒ 增量集极小） | `update_time` 是**行级写入时间戳** ⇒ 读侧可判「本轮新增」，**无需上游 delta log** |

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
| `quantstudio/pipeline/qfq_dividend_payload.py` / 既有除权扫描通道 | ⑥【v2 新增】按 `update_time > 增量水位` 取本轮新增除权事件的 code 集，并入 changed code 集 |

### 2.2 【v2 新增】增量水位的存储

`qfq_observation_cursor`（**DuckDB 主库**，非 aux）现有列：`detector_name / asset_type / price_source / source_generation / cursor_as_of / last_run_id / status / updated_at`。

- **问题**：`cursor_as_of` 是「本轮上界」，**不是「上次已消费位置」** ⇒ 直接用它做 `update_time >` 会**重复消费**。
- **方案**：新增列 `incremental_consumed_as_of`（VARCHAR `'YYYY-MM-DD HH:MM:SS'`），每轮消费后写回；首次为空 ⇒ 首轮走全量（bootstrap 基线，与既有 bootstrap 语义一致）。
- **schema 变更影响**：`qfq_observation_cursor` 是否受指纹校验约束**待核**（若受约束 ⇒ 加列＝契约变更 ⇒ 按铁律 24 六件套走）；**若不受约束**，则按普通 DDL 处理。**此项列为 T6**。

### 2.3 明确不改动

- **不改判据**：epsilon 语义、`revision_no` 递增规则、alert 写入契约、`factor_new` trigger 语义、`code_contract` 预过滤（fail-safe 阻断 2）全部原样；
- **不改上游写入侧**（`daemon.py` / `qfq_maintenance.py` / 各 adapter）⇒ 上游零改动、零风险；
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
| **G6** | 真实库对拍（②审前置②） | 同一真实快照：旧路径 vs 新路径 ⇒ `qfq_factor_observation`（44M 行）**全表 hash 相等** + `alert` 表相等 + `qfq_trigger_queue` 相等 + 水位相等 | 引入单点差异 ⇒ hash 不等 |
| **G7** | revision_no 链构造性证明（②审前置②） | 批量取 last == 该 `(code,factor_time)` 的 `max(revision_no)` 行 ⇒ 链 rev1…revN 无缺号 | 人工挖空某 rev ⇒ 链不连续 ⇒ 红灯 |
| **G8** | 回退开关 | `QS_QFQ_OBS_INCREMENTAL=0` ⇒ 全表 hash 与旧路径相等、耗时回到旧量级 | 开/关两态结果同一（仅耗时不同） |
| **G9** | 50762 排水 run（②审前置④） | `QfqPendingSla` 逾期量从 50762 下降且**不新增** | — |
| **G10** | 收益实测 | 同库同快照：post-ingest 段耗时（新 vs 旧）对照；**往返次数计数器**从 O(N) 降至 O(变化数+批数)（计数器入库，非估计） | — |
| **G11** | 【v2 新增】除权增量集不重不漏 | `update_time > 增量水位` 取出的 code 集 == 旧路径「本轮新增除权」口径；**重复消费检测**：同一水位连跑两次，第二次增量集须为空 | 水位未写回 ⇒ 第二次仍非空 ⇒ 红灯 |
| **G12** | 【v2 新增】增量水位持久化 | `incremental_consumed_as_of` 每轮写回；首轮（空水位）走全量且写回 | 水位写回失败 ⇒ 次轮重复消费 ⇒ 红灯 |

**门控纪律**：一点绿才进下一点；任一红停在该点，修复后重过。

---

## 四、风险描述

| 风险 | 影响 | 规避 |
|---|---|---|
| **丢失面①：历史值修订漏检** | 漏检因子漂移 ⇒ rebase 自锁（既有已知错误族） | 变更集以 `revisions`（真值比对结果）为准，**不用时间下界**；G6 全表 hash 对拍 |
| **丢失面②：除权事件新增 code 未纳入** | `effective_dates` 少算除权日 ⇒ 复权价错 | G5 + G11 双门；口径 = `update_time > 增量水位`（实测格式/非空率已验） |
| **丢失面③：未变行不再刷新 `last_seen_run_id`** | 若下游以该列判「本轮已观测」⇒ 误判 | **【v2 已闭合】仓内检索：`last_seen_run_id`/`first_seen_run_id` 除写入侧外**零消费方** ⇒ 无下游依赖；G3 保留为「审计可追溯」的可选动作 |
| **重复消费**（v2 新识别） | 除权增量集被重复计入 ⇒ 重复触发 | G11 重复消费检测 + G12 水位写回；首轮空水位走全量 |
| 游标表加列是否触发契约变更 | 若受指纹约束 ⇒ 须走铁律 24 六件套 | **T6 待核**；未核前不实施 G12 |
| 批量取与逐条取不等价 | 漏/错判修订 | G1 对拍 200×全量；G7 链连续性证明 |
| 未变行批量 UPDATE 放大 | 写放大 | 分批 UPDATE + 只更新受影响 `(code,factor_time)` 集 |
| 内存占用（44M 行 last 值映射） | 内存压力 | 按 code 分组流式切块 ⇒ 不全量驻留 |
| **性能判据虚高** | 「提速」来自漏检 | G6 hash 对拍为硬门；收益读数须与 hash 相等同时成立 |

---

## 五、验收要点

1. **功能**：G1–G12 全绿；
2. **等价性**：真实库对拍三表 hash + 水位相等（G6）——**纯性能型的核心硬门**；
3. **场景**：
   - **无变化日**（因子全未变）：往返次数 ≈ O(批次数)；观测表 hash 与旧路径相等；除权增量集 ≈ 空；
   - **有修订日**：`revision_no` 递增与旧路径一致、alert 行集一致；
   - **有除权事件日**：changed code 集含该 code（G5）；
   - **重复消费**：同水位连跑两次，第二次增量集为空（G11）；
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

### 6.1 【v2 新增】收益预估（**待 G10 实测证实**）

| 面 | 现状 | 改造后（估算） | 降幅 |
|---|---|---|---|
| 因子侧往返 | 4390 万 × 2–4 次 | 变化数 × 2–4 次 + 批次数（按 code 分组 ≈ 8179 批） | **无变化日 ↓99.98%** |
| 消费侧遍历 | 8179 code 全遍历（每 code 扫其 factor_time） | changed code 集（无变化日 ≈ 0） | **↓≈100%** |
| post-ingest 段总耗时 | 95 分钟～16 小时 | **分钟级**（须 G10 证） | 待证 |
| WAL 膨胀 | H2 曾测 1.56GB | **H1 的镜像**（往返少 ⇒ 事务少 ⇒ WAL 涨得少）⇒ 治理 O(N) 往返后自然收敛 | 随 H1 |

**WAL 现状实测（v2 · T4 闭合）**：`qfq_aux.db-wal` = **0 MB**；`journal_mode=wal`；`PRAGMA wal_checkpoint(PASSIVE)` = **0.0 ms**；`freelist_count=1`；主库 `quantstudio.db-wal` 不存在。⇒ **H2 当前不成立**，收益测算**不带 WAL 惩罚**。

---

## 七、待补项（②审需一并裁）

| # | 待补 | 状态（v2） | 需谁 |
|---|---|---|---|
| ~~T1~~ | 除权事件增量口径 | **✅ 闭合**：`stock_dividend.update_time`（`'YYYY-MM-DD HH:MM:SS'`，非空率 100%，mcp 源，近 7 天 8 行）⇒ 读侧 `WHERE update_time > 增量水位`；增量水位需新增 `incremental_consumed_as_of`（见 §2.2） | 已闭合 |
| ~~T2~~ | 下游是否依赖 `last_seen_run_id` | **✅ 闭合**：仓内检索**零消费方**（除写入侧）⇒ 无下游依赖 | 已闭合 |
| ~~T4~~ | WAL 现状复测 | **✅ 闭合**：WAL=0MB、checkpoint 0.0ms、freelist=1 ⇒ H2 当前不成立 | 已闭合 |
| **T3** | `QfqPendingSla` 50762 排水触发条件与顺序 | 未查（计数点 `quality_audit.py:754`） | **与 dev 协同** |
| **T5** | 「不与 doom-loop 混案」边界确认 | 本方案只动 `qfq_observation`/`orchestrator`/`event_discovery`/除权取数，**不碰 writers/水位提交** ⇒ 边界成立 | 调度确认 |
| **T6** | 【v2 新增】`qfq_observation_cursor` 加列是否触发**schema 指纹契约变更**（铁律 24） | 未核 | 我侧（实施前必核） |

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
| QFQ 增量 B 线 | 本方案即 B 线实现层（用户 10-31 五项②审前置逐条对应 G1–G12） |
