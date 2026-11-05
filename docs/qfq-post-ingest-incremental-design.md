# 方案③：QFQ post-ingest 增量改造（消除 O(N) 往返）—— 设计 v1（②审呈报稿）

- 起草：2026-11-06（客户运维线，会话节点2）
- 判型：**纯性能型**（观测表逐行相同、alert/trigger 相同、下游复权结果相同；**新引入检测能力须单列**）
- 前置：①用户 10-31 定的五项②审前置（变更集语义+丢失面 / revision_no 链构造性证明+真实库对拍 / `QS_QFQ_OBS_INCREMENTAL=0` 回退 / `QfqPendingSla 50762` 排水 run / 不与 doom-loop 混案）②本方案的根因定量见 `docs/evidence/qfq-post-ingest-profiling-20261031.md` §八

---

## 一、背景与动机（根因已定量，非推测）

### 1.1 现象

- 客户「全部执行增量拉取卡 2 天」；`factor_value_pt` 单任务**硬啃 16 小时**；QFQ post-ingest 阶段挂住。

### 1.2 根因（插桩实测 · 2026-10-31 §八）

| 项 | 实测读数 | 出处 |
|---|---|---|
| 观测表规模 | `qfq_factor_observation` **43,900,131 行**（11-06 复测 44,434,134 行） | 插桩 §八 |
| `to_write` 首条 | `("STOCK","000001", 1686787200000=2023-06-15, 116.713)` ⇒ **不是今日增量，是历史因子点** | 插桩 §八 |
| 单次往返 `_rt` | `[1.32, 0.59, 0.126, 0.157, …] ms` ⇒ **0.13–1.32 ms** | 插桩 §八 |
| **N+1 写入** | 每条观测 **2–4 次 sqlite 往返** ⇒ N=4390 万 ⇒ **8790 万 ～ 1.76 亿次往返** | §八 H1 |
| **累计耗时** | 4390 万 × 0.13ms ≈ **95 分钟** ～ × 1.32ms ≈ **16 小时** | 算自上两行 |
| WAL | 1.56GB（**H2 已排除**：非放大主因，仅叠加项） | §八 H2 |

**⇒ 根因表述**：post-ingest 每轮把**全量历史观测（4390 万条）逐条**做「查 last 值 → 比对 → 写回」，往返次数 **O(N)**，单次虽快（0.13ms）但总量放大到数十亿次 ⇒ **16 小时级挂死**。

### 1.3 为什么不按「时间下界」增量（丢失面论证 · 关键）

直觉方案「只处理 `factor_time > 上次观测最大时间`」**不成立**：

- 上游因子**持续微调历史值**（既有知识条目《已知错误-因子版本漂移致rebase自锁》即此族）⇒ 修订可能发生在**任意历史 factor_time**（例：`000008@2018-01-02` 跳变 4.355×、`000010@2013-…`）；
- 时间下界会把这些历史修订**排除在外** ⇒ `revision_no≥2` 永不产生 ⇒ **漏检**；
- ⇒ **纯时间下界增量 = 用「漏检」换「提速」**，违反纯增益。

### 1.4 本方案的正解：变更集来自**观测比对结果本身**，不依赖上游

核实结论（本轮代码级取证）：

| 事实 | 出处 | 意义 |
|---|---|---|
| `record_observations` 返回 **`revisions: List[RevisionRow]`**（修订明细）与 **`factor_new: List[FactorNewRow]`**（新增 factor_time 明细） | `qfq_observation.py` `ObservationResult` | **变更集已现成**，无需上游 delta log |
| 观测表列含 `revision_no / first_seen_run_id / last_seen_run_id / first_seen_at / last_seen_at` | aux 库实测 schema | 比对状态**已持久化** |
| **⚠️ 更正我 11-05 的设想**：`elif |new-last.value| <= epsilon: UPDATE last_seen_run_id/at` ⇒ **未变行也刷新 `last_seen_run_id`** ⇒ **`last_seen_run_id == 本轮` 不能当变更集**（会把未变 code 也算进来，恒为全量） | `qfq_observation.py:16` | 我原设想的口径**不成立**，已改用 `revisions`/`factor_new` |
| 快照表 `adj_factor`/`fund_adj` 由 `INSERT OR REPLACE` 写入，**无 per-run 变更日志**；`qfq_jump_audit` **rows=0**（只记跳变异常）；`adj_factor_snapshot` 只有 **5780 行**（每 code 一行聚合值，无 factor_time 粒度） | 本轮 aux 实测 + `daemon.py:1874` / `qfq_maintenance.py:504` | **上游变更集来源确实不存在** ⇒ 正解只能落在「比对结果」侧（1.4 表首行） |
| post-ingest 消费侧**逐 code** 查 `SELECT DISTINCT factor_time FROM qfq_factor_observation WHERE asset_type=? AND code=?` ⇒ **第二处 O(N)**（8179 codes × 扫 4390 万行） | `qfq_resident_orchestrator.py:1746-1756`（调用点 `:858`、`:1694`） | 增量须**同时**治理消费侧 |

---

## 二、范围与边界

### 2.1 改动文件（3 处）

| 文件 | 改动 |
|---|---|
| `quantstudio/pipeline/qfq_observation.py` | ①「查 last 值」逐条往返 ⇒ **批量取**（按 `(asset_type, code)` 分组批取 / 分块 SELECT + 内存映射）；②**只写变化行**（new → INSERT rev1；revised → INSERT rev+1 + alert pending）；③**未变行批量刷新** `last_seen_run_id/last_seen_at`（分批 UPDATE，**不是逐条**） |
| `quantstudio/pipeline/qfq_resident_orchestrator.py` | ④逐 code 遍历（`_security_effective_dates` 调用点 `:858`/`:1694`）⇒ **只遍历本轮 changed code 集** |
| `quantstudio/pipeline/qfq_event_discovery.py` | ⑤把 `result.revisions`（**当前未被消费**，`factor_new` 已被 `:468-469` 消费）透传给 orchestrator |

### 2.2 明确不改动

- **不改判据**：epsilon 语义、revision_no 递增规则、alert 写入契约、`factor_new` trigger 语义、`code_contract` 预过滤（fail-safe 阻断 2）**全部原样**；
- **不改上游写入侧**（`daemon.py` / `qfq_maintenance.py` 的因子写入）⇒ 上游零改动、零风险；
- **不改 `_security_effective_dates` 的语义**（仍是「除权事件 ∪ 因子观察时间」），只改**遍历范围**；
- **不改 doom-loop 相关**（日线写冲突/水位冻结**不混本案**，见前置⑤）。

### 2.3 开关与回退

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `QS_QFQ_OBS_INCREMENTAL` | `1` | `0` ⇒ **逐位还原旧路径**（逐条往返 + 全量消费侧遍历）⇒ ②审前置③ |

---

## 三、任务拆解（功能点分解表 · 逐点门控）

| 点 | 功能 | 独立可验证判据 | 负向对照 |
|---|---|---|---|
| **G1** | 批量取 last 值（替代逐条查） | 同一批输入下，批量取与逐条取的结果集**逐行相等**（对拍脚本：随机 200 code × 全 factor_time） | 故意错配 `asset_type` ⇒ 不等 |
| **G2** | 只写变化行 | 新增/修订行的 `revision_no`/`factor_value`/`first_seen_run_id` 与旧路径**逐位相同** | 伪造 epsilon 边界（差值恰在阈值上）⇒ 旧路径记 unchanged、新路径不得记 revised |
| **G3** | 未变行批量刷新 | 未变行 `last_seen_run_id` 更新为当轮 run_id；`revision_no` 不变；行数不变 | 关闭刷新 ⇒ 该列停更（供对拍识别） |
| **G4** | alert outbox 等价 | `qfq_factor_revision_alert` 行集与旧路径**逐位相同**（含 `alert_id`） | 去掉 alert 写入 ⇒ 行集缺 ⇒ 红灯 |
| **G5** | 消费侧 changed-code 遍历 | 遍历 code 集 == 本轮 changed code 集 ∪ **除权事件新增 code 集**（见 §四 丢失面第二源） | 只取 changed code ⇒ 除权事件 code 漏算 ⇒ 红灯 |
| **G6** | 真实库对拍（②审前置②） | 同一天真实快照：旧路径 vs 新路径 ⇒ `qfq_factor_observation`（44M 行）**全表 hash 相等**、`alert` 表相等、`qfq_trigger_queue` 相等、水位相等 | 引入单点差异 ⇒ hash 不等 |
| **G7** | revision_no 链构造性证明（②审前置②） | 批量取 last 必须等于「该 (code,factor_time) 的 max(revision_no) 行」⇒ 链连续性证明：rev1…revN 无缺号 | 人工挖空某 rev ⇒ 链不连续 ⇒ 红灯 |
| **G8** | 回退开关 | `QS_QFQ_OBS_INCREMENTAL=0` ⇒ 全表 hash 与旧路径相等、耗时回到旧量级 | 开/关两态结果同一（仅耗时不同） |
| **G9** | 50762 排水 run（②审前置④） | `QfqPendingSla` 逾期量从 50762 下降且**不新增**（排水不制造新 pending） | — |
| **G10** | 收益实测 | 同库同快照：post-ingest 段耗时（旧 vs 新）**对照记录**；往返次数计数（`record_observations` 内计数）从 O(N) 降至 O(变化数+批数) | — |

**门控纪律**：一点绿才进下一点；任一红停在该点，修复后重过。

---

## 四、风险描述

| 风险 | 影响 | 规避 |
|---|---|---|
| **丢失面①：历史值修订漏检** | 漏检因子漂移 ⇒ rebase 自锁（既有已知错误族） | 变更集以 `revisions`（真值比对结果）为准，**不用时间下界**；G6 全表 hash 对拍 |
| **丢失面②：除权事件新增 code 未纳入**（**关键**） | `effective_dates` 少算除权日 ⇒ 复权价错 | changed code 集 = factor 变化集 **∪ 本轮新增除权事件的 code 集**（`stock_dividend` 增量取数口径**待补**，见 §六） |
| **丢失面③：未变行不再刷新 `last_seen_run_id`** | 若下游以该列判「本轮已观测」⇒ 误判 | 保留批量刷新（G3）；并核实下游依赖（§六待补） |
| 批量取与逐条取不等价 | 漏/错判修订 | G1 对拍 200×全量；G7 链连续性证明 |
| 未变行批量 UPDATE 放大 | 写放大 | 分批 UPDATE（按 code 分组，批大小可调）+ 只更新受影响 (code,factor_time) 集 |
| 内存占用（44M 行 last 值映射） | 内存压力 | 分块流式（按 code 分组、按 code 边界切块）⇒ 不全量驻留 |
| **性能判据虚高** | 「提速」来自漏检 | G6 hash 对拍为硬门；收益读数须与 hash 相等同时成立 |

---

## 五、验收要点

1. **功能**：G1–G10 全绿；
2. **等价性**：真实库对拍三表 hash 相等（G6）——**这是纯性能型的核心硬门**；
3. **场景**：
   - 无变化日（因子全未变）：往返次数 ≈ O(批次数)，观测表 hash 与旧路径相等；
   - 有修订日：`revision_no` 递增与旧路径一致、alert 行集一致；
   - 有除权事件日：changed code 集含除权 code（G5）；
4. **边界**：空快照、单 code、单 factor_time、epsilon 恰在阈值、batch 切块边界；
5. **回退**：`QS_QFQ_OBS_INCREMENTAL=0` 逐位还原（G8）。

---

## 六、质量判据

| # | 判据 |
|---|---|
| 1 | 观测表/alert/trigger/水位**四方 hash 或行集相等**（旧 vs 新）——任何不等即验收失败 |
| 2 | 往返次数从 O(N=4390万) 降至 O(变化数 + 批数)，且**计数可机检**（代码内计数器，非估计） |
| 3 | 丢失面三源逐条有 G5/G6 对应证据，无「未覆盖项以假设代替证明」 |
| 4 | 回退开关零残留（`=0` 时行为与旧路径逐位相同） |
| 5 | 上游零改动（`daemon.py`/`qfq_maintenance.py` 不在本方案 diff 内） |

---

## 七、待补项（如实标注，②审需一并裁）

| # | 待补 | 现状 | 需谁 |
|---|---|---|---|
| **T1** | **`stock_dividend` 新增除权事件的 code 集取数口径** | 本轮 grep 未命中其 INSERT 路径（可能走 DuckDB writer 批量写）；**丢失面②的实现依赖它** | 我侧继续查（明晨）／若在 writers 线则需转 |
| **T2** | 下游是否依赖 `last_seen_run_id` 判「本轮已观测」 | 未核 | 我侧（明晨） |
| **T3** | `QfqPendingSla` 50762 排水的**触发条件与排空顺序** | 计数点在 `quality_audit.py:754`；排水机制未查 | 需与 dev 协同（②审前置④） |
| **T4** | WAL 现状复测（H2 已排除为主因，但当前 WAL 大小未知） | 11-01 实测 WAL=0；11-03 后未测 | 我侧（明晨，顺带） |
| **T5** | 「不与 doom-loop 混案」的边界声明 | 本方案只动 `qfq_observation`/`orchestrator`/`event_discovery`，**不碰 writers/水位提交** ⇒ 边界成立，需调度确认 | 调度 |

---

## 附一：知识反哺检索留痕（N8 / R1）

- 知识反哺：[命中] 已知错误-因子版本漂移致rebase自锁（12-客户运维/已知错误/，2026-11-06）——本方案丢失面①的既有判据族（上游持续微调 ⇒ 禁止时间下界增量）
- 知识反哺：[命中] 已知错误-QFQ空分钟表致重锚永久跳过水位冻结（12-客户运维/已知错误/，2026-11-06）——QFQ 族的相邻已知问题，用于边界划清
- 知识反哺：[命中] 案例卷宗-QFQ空分钟水位冻结案（12-客户运维/案例卷宗/，2026-11-06）——同族案例档
- 知识反哺：[无命中] knowledge/{contracts,db,platform,playbooks,probes,ptrade-deploy}（QFQ/post-ingest/增量/O(N)往返 条目面），2026-11-06

## 附二：本方案与既有工作的关系

| 项 | 关系 |
|---|---|
| 插桩（`0e50c81`/`fa4a57e`） | 本方案的根因依据（§1.2 全部读数来自插桩 §八） |
| 方案② 全局段缓存（`07e6238`） | 治审计段；**不改善 post-ingest** ⇒ 两案互补、不重叠 |
| doom-loop（日线写冲突/水位冻结） | **不混本案**（T5 待调度确认） |
| QFQ 增量 B 线 | 本方案即 B 线的实现层（用户 10-31 定的五项②审前置逐条对应 G1–G10） |
