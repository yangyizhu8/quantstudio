# 方案③ 规格件：G5 落点修正 + G6 生产副本库需求 + G9 50762 排水口径

- 出具：客户运维会话1，2026-11-09 23:30（Asia/Shanghai）
- 关联方案：`docs/qfq-post-ingest-incremental-design.md`（v3.1）
- 状态：**G5 落点修正**＝实施期读码结论（目标不变、实现落点改变）；**G6** 待总调度排程后呈用户批窗（L2）；**G9** 待用户转节点1协同

---

## 一、G5 落点修正（v3.2 · 实施期读码）

### 1.1 v3 方案的原假设（**已证伪**）

v3 写「G5 = 消费侧**只遍历 changed code 集**」，前提是「当前逐 code 遍历全部 8179 个 code」。

### 1.2 实读结论（行号可核）

| 事实 | 证据行号 | 含义 |
|---|---|---|
| `_claim_and_merge` 的 unit 来自 **pending 队列** | `qfq_resident_orchestrator.py:564-574`：`SELECT ... FROM qfq_trigger_queue WHERE status='pending' AND effective_date IS NOT NULL AND effective_date <= ?` | **遍历范围本就有限**（= 本轮到期单元数），**不是 O(N) 源头** |
| unit 的 `effective_dates` 取自 trigger 队列的 `effective_date` 列 | `:589-593` | 与 `_security_effective_dates` **无关** |
| `_security_effective_dates` 的调用点在 **`_reanchor_security` 内**（per-unit） | `:858`（函数起 `:761`） | 每处理一个 unit 才调一次 |
| 该函数的 factor_time 取数是**逐 code 单查** | `:1752-1754`：`SELECT DISTINCT factor_time FROM qfq_factor_observation WHERE asset_type=? AND code=?` | **这才是 O(N) 往返源**（8179 次往返 × 每 code 全量 factor_time 行；4390 万行总量） |

### 1.3 修正后的 G5（**目标不变、落点改变、范围不缩**）

| 项 | v3（原） | **v3.2（修正）** |
|---|---|---|
| 改点 | `_claim_and_merge` 的遍历范围 | **`_security_effective_dates` 的 factor_time 取数** |
| 手法 | 范围缩到 changed code | **块预取 + 缓存**：首次请求某 code 时，一次 SQL 取**一批 code**（如 256 个，`WHERE asset_type=? AND code IN (...)`），按 code 建内存映射；同批内后续 code 零往返 |
| 往返复杂度 | O(code 数) → O(changed 数) | O(code 数) → **O(code 数 / 批大小)**（256 倍降） |
| 语义 | — | **零语义变更**：返回的 factor_time 集合逐字相同 |
| 内存 | — | 批内 256 code × 约 5.4k 行 ≈ 138 万行（约 100MB 级）⇒ **块大小可调** |

### 1.4 连带裁定（**G11 / G5b 退役**）

| 项 | 原定位 | 修正后 |
|---|---|---|
| **G11**（除权增量集 + G12 游标） | 为「changed code 集」服务 | **降级为可选项**：除权侧 `SELECT DISTINCT ex_date FROM stock_dividend WHERE code=?` 面向 **54,711 行小表**，非瓶颈 ⇒ **本批不做**；G12 游标表已落地（`07b45ee`）保留备用 |
| **G5b**（到期单元不得漏） | 防「缩范围漏处理」 | **本批不做范围缩放 ⇒ 无该风险** ⇒ G5b 转为「**若将来引入范围缩放则必须同批带**」的备注条款 |

### 1.5 修正的归因（诚实说明）

- **性质**：我 v3 方案对 G5 落点的判断有误（把「pending 队列驱动的有限遍历」误当「全量 code 遍历」），**实施期读码才发现**（与 11-06 G5b 同型：方案盲维须先呈报再动手）。
- **未造成损失**：G5 **尚未实施**（无错误代码入库）；G1–G4/G12 落地部分不受影响（其落点在 `qfq_observation.py` 写侧，与 G5 消费侧无关）。
- **本轮不自行实施 G5**：修正涉及「范围是否缩放」这一**已获批裁定的核心语义**，虽我判定为「落点细化、目标不变」，仍先呈报（纪律：与既有裁定相关的一律先报）。

---

## 二、G6 生产副本库需求规格（待总调度排程 → 呈用户批窗 L2）

### 2.1 需求一句话

需要一份**生产主库的只读副本**，用于「方案③ G6：真实库 44M 行全表 hash 四方相等对拍」（**严禁在生产库上做全表对拍**）。

### 2.2 库清单

| # | 库 | 路径 | 体积 | 必需性 | 用途 |
|---|---|---|---|---|---|
| 1 | 主库 `quantstudio.db` | `data/quantstudio.db` | 约 60GB 级（实测 `page_count` 3,239,049 × 4KB ≈ 12.4GB 元数据，另含大表体量，以实测为准） | **必需** | 四方对拍的主库侧（`qfq_trigger_queue` / 水位 / 表内容 hash） |
| 2 | aux 库 `qfq_aux.db` | `data/qfq_aux.db` | **11,268 MB**（11-01 实测） | **必需** | 观测表全表 hash（44,434,134 行）+ alert 表 + 游标表 |
| 3 | `batch_audit.db` / `quarantine.db` | `data/` 下 | 小 | 可选 | 审计侧留痕比对 |
| 4 | `config/` + `quantstudio/`（代码） | 仓库内 | — | **必需（同一 commit）** | 副本须用**同一版代码**跑新旧路径 |

### 2.3 副本要求（硬约束）

| 项 | 要求 | 理由 |
|---|---|---|
| 读法 | **只读挂载**（对拍脚本一律 `read_only=True` / `mode=ro`） | 防误写生产 |
| 一致性 | 快照时刻须**记录**（精确到秒）⇒ 旧路径与新路径跑在**同一快照**上 | 否则 hash 不可比 |
| 隔离 | 副本置于**独立数据根**（`QUANTSTUDIO_DATA_ROOT=<副本根>`）⇒ 避免任何写落到生产路径 | 铁律 23 迁移/对拍窗口纪律 |
| 写锁 | 对拍期间**生产 daemon 停止取锁**（或至少不与副本共享同一 DuckDB 文件句柄） | DuckDB 单写者 |
| 时间预算 | 副本拷贝（含 11GB aux）**约 20–40 分钟**（按实测带宽）；对拍跑两遍（旧路径全量 + 新路径全量）**约 2×35 分钟** ⇒ **合计窗口 2–3 小时** | 见 2.4 |
| 人力 | 拷贝 1 人 + 对拍 1 人（或同一人串行） | — |

### 2.4 窗口草案（三个候选，请排程择一）

| 方案 | 时间 | 优点 | 代价 |
|---|---|---|---|
| **甲** | **工作日 02:00–05:00**（daemon 夜间收工后） | 不影响日间采集；11-02 轮次 06:00 起跑 | 需确认 daemon 收工时点（11-09 实测：daemon 22:30 起仍在跑 ⇒ **夜间不保证空闲**，需实测锁释放时刻） |
| **乙** | **周末维护窗**（如 11-08 周日 09:00–12:00） | 无生产压力 | 客户「不爱热闹」⇒ 周末采集需求低（但周日 skip ⇒ 无冲突） |
| **丙** | **11-02 周一轮次跑完后**（约 15:00 起） | 白天可监督 | 需该日无其它线作业 |

### 2.5 对拍判据（G6 硬门，四方相等）

| 对照物 | 判定 |
|---|---|
| `qfq_factor_observation`（44M 行） | 全表 **内容 hash 逐位相等**（非行数相等） |
| `qfq_factor_revision_alert` | 行集逐位相等（含 `alert_id`/`status`） |
| `qfq_trigger_queue` | 行集逐位相等 |
| 水位（`source_watermark` 等） | 逐位相等 |
| 收益读数 | 旧路径 wall vs 新路径 wall（**附往返计数器**：旧 = 逐条 N 次；新 = O(块数)） |

### 2.6 请调度/用户裁的项

1. 副本由谁产出（我方拷贝 or 运维提供快照）？
2. 选甲/乙/丙哪个窗口？（我建议 **乙**：周日无轮次、无生产压力、客户侧影响最小）
3. 副本落哪个盘（需 ≥ 80GB 空闲）？
4. 是否允许在副本上**跑旧路径**（会写副本 aux）——建议允许（副本即沙箱）。

---

## 三、G9 `QfqPendingSla` 50762 排水口径（待用户转节点1 协同）

### 3.1 事实基线（我侧实测）

| 项 | 读数 | 出处 |
|---|---|---|
| 逾期量 | **50,762** | 2026-10-31 审计读数（`quality_audit.py:754` `QfqPendingSla`） |
| 死信量 | 14,302（`QfqDeadLetter`，`:748`） | 同上 |
| pending 来源 | `qfq_trigger_queue`（`status='pending'`） | `qfq_resident_orchestrator.py:564-574` |
| 到期转 pending | `promote_scheduled_due`（`:504-519`，`scheduled` → `pending`） | — |
| 待重试转 pending | `recover_pending_due`（`:521-537`，`retryable_failed` → `pending`） | — |
| 消费与确认 | `acknowledge_alert`（`qfq_observation.py:472-508`，`pending` → `acknowledged`） | — |
| 与方案③的关系 | 方案③ **不改变 alert 产生逻辑**（G4 已证 alert 行集逐位相同）⇒ **pending 增量源头不变**；且 post-ingest 变快 ⇒ 消费侧更快 ⇒ **可能加速排水** | G4 门 |

### 3.2 排水口径（请节点1 协同确认）

| # | 口径项 | 我方建议 | 需协同方 |
|---|---|---|---|
| 1 | 排空顺序 | **先「到期」（scheduled_due → pending）后「待重试」（retryable_failed → pending）**，与既有 `promote`/`recover` 顺序一致 | 节点1 |
| 2 | 排水判据 | `QfqPendingSla` 计数**下降且不新增**（新增量 = 本轮新 trigger 产生量，须扣掉）⇒ 单看绝对值会误判 | 节点1 |
| 3 | 死信隔离 | `QfqDeadLetter` 14,302 **不并入本轮**（死信是永久失败集，排水不解决它）⇒ 单独排期 | 节点1 |
| 4 | 与增量方案的耦合 | **解耦**：方案③ 开启后应「pending 不增 + 绝对值缓降」；若不降 ⇒ 消费侧未动 ⇒ 转节点1 查 `acknowledge` 路径 | 节点1 |
| 5 | 隔离侧先验（我侧可即刻做） | 在**合成库**验证「增量开启后 pending 不增」（不等生产） | 我侧自办 |

### 3.3 需节点1 回答的三个问题

1. `acknowledge_alert` 的**实际调用方与频次**（谁在消费后确认）？
2. 50762 中有多少是**真逾期**、多少是**误标**（`effective_date` 口径问题）？
3. 排水是否有**并发安全**要求（多个 daemon 同时消费）？

---

## 四、本件对应的门与状态

| 门 | 状态 | 备注 |
|---|---|---|
| R2 契约面销项 | ✅ **已闭环** | 136 passed / 0 failed（此前 2 例 PermissionError ＝ 生产库被持锁） |
| R5 / G7 链构造性证明 | 见同轮回执 | 隔离测试 |
| G5 | **落点已修正，待实施**（v3.2 §一） | 修正已先呈报 |
| G11 / G5b | **退役**（v3.2 §1.4） | G12 表保留备用 |
| G6 | **规格件已出**（§二），待排程 → 用户批窗 L2 | 建议窗口乙 |
| G9 | **口径件已出**（§三），待用户转节点1 | 三问已列 |

### 3.4 澄清件（2026-11-10 · 应节点1「实测判据无 effective_date 路径」）

**（一）撤回我方的错误表述**
§3.3 原写「50762 中有多少是**误标**（`effective_date` 口径问题）」——**该推测不成立，撤回**。
实读判据（`quality_audit.py:743-761`）：

```sql
-- QfqDeadLetter（:744-749）
SELECT COUNT(*) FROM qfq_trigger_queue
 WHERE status='dead_letter' AND {trig_scope}
-- 超 dl_max ⇒ error

-- QfqPendingSla（:750-755）
SELECT COUNT(*) FROM qfq_trigger_queue
 WHERE status IN ('pending','retryable_failed')
   AND COALESCE(updated_at, created_at) < NOW() - INTERVAL {sla_h} HOUR
   AND {trig_scope}
-- 超 sla_h 小时 ⇒ error "pending/retryable_failed older than {sla_h}h SLA"

-- QfqStaleInProgress（:756-761）
SELECT COUNT(*) FROM qfq_trigger_queue
 WHERE status='in_progress'
   AND (claimed_at IS NULL OR claimed_at < NOW() - INTERVAL {stale_h} HOUR)
```

⇒ **判据只依赖 `status` 与「停留时长」，完全不含 `effective_date`** ⇒ 节点1 实测结论正确，我方推测作废。

**（二）50,762@10-31 → 47,614@11-09（−3,148）的正确解释方向**

⚠️ **口径陷阱（本轮最重要的澄清）**：`QfqPendingSla` 的分母是「**停留已超 sla_h 小时**」的行。
而 `recover_pending_due`（`qfq_resident_orchestrator.py:521-537`）在把 `retryable_failed` 转回 `pending` 时**会刷新 `updated_at`**（`:534` `SET status='pending', updated_at=?`）。
⇒ **每轮 recover 都会把逾期行「重新计时」⇒ 它们掉出 SLA 窗口 ⇒ 计数下降，但积压行数并未减少。**
⇒ **「计数下降」≠「积压缓解」**，是本判据的固有失真面（不是 bug，是 SLA 判据的语义边界）。

**因此 −3,148 的可能构成（均为假设，未验证）**：
| # | 假设 | 判别特征 | 验证手段 |
|---|---|---|---|
| A | recover/promote 刷新 `updated_at` ⇒ 行掉出 SLA 窗口（**虚降**） | 行数不减、`MAX(updated_at)` 近期被刷新 | 需分解 SQL（顺延静默窗） |
| B | 真排水（消费成功，行状态离开 pending/retryable_failed） | `COUNT(*) WHERE status IN (...)` 绝对值下降 | 同上 |
| C | `retryable_failed` → `dead_letter` 迁移 | dead_letter 计数上升、pending 分母减少 | 同上 |
| D | 历史补数堆积被消费（10-31 后确有补数轮） | 特定 trigger_type 分桶下降 | 同上 |

**（三）我方口径修正（供对账基更新）**
- 原五点基线的第 2 点「**计数下降且不新增**」应修正为：**「同时记录 `status IN ('pending','retryable_failed')` 的绝对行数（不受 SLA 刷新影响）」与「SLA 窗口内计数」两组数**——只用 SLA 计数会因 recover 刷新而虚降。
- 建议判据：**排水成功 = ①绝对行数下降 ②dead_letter 不上升 ③SLA 计数与绝对行数同向变化**；三者背离即判「口径失真，需分解」。

**（四）分解 SQL 状态**
按调度指示「G9 分解 SQL 顺延静默窗维持」⇒ **本轮未执行任何分解 SQL**，仅出澄清。静默窗开启后按 §（二）四假设逐条判别。

### 1.6 G5 定谳（2026-11-10 节点2 裁定 · 归档）

**裁定**：**保持默认关**（`QS_QFQ_OBS_INCREMENTAL` 默认 0），**不单独立项为性能叙事**，
降级为「**已落地工具改进**」归档。

**实测依据**（G6 副本窗，2000 个真实 STOCK 码，只读）：

| 指标 | old | new | 比 |
|---|---|---|---|
| 往返次数 | 2,000 | 8 | **250.0×** |
| wall time | 39.965 s | 38.177 s | **1.05×** |
| 返回集合 | — | — | **差异 0 个 code（逐位相同）** |

**机理**：两路径**扫描行数相同**（约 1,073 万行），瓶颈是 IO 扫描量；本地 NVMe 下建连开销被大查询摊薄 ⇒ wall 仅快 5%。

**叙事修正（重要）**：「95 min – 16 h」量级属于 **`record_observations` 的逐条写往返（G1–G4 已解）**，
**不属于 G5**。G5 的收益是**往返数**，其 wall 价值只在**高延迟 IO / 远程 SQLite / 建连昂贵**的部署形态下显著。

**G5b 备注条款（保留）**：本改造**不缩遍历范围**（unit 仍来自 pending 队列）⇒ 无「漏到期单元」风险；
**若将来引入范围缩放，必须同批带 G5b**（changed ∪ 到期 ∪ 待重试）。

**差分布**：G5 维度（`factor_time` 键集完整性）**全 0 差** ⇒ 已转 ⑳（**该维度容差可定严格 0**）；
**写入维（`factor_value` float）容差尚未定值**（需两遍写入场景）。
