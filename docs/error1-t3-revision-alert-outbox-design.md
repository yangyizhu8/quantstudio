# 错误一 T3 修复设计：adj_factor 注入点修订检测 → revision alert outbox — 六步第 1 步

- 状态：**方案（待审计）**｜日期：2026-09-23｜归属：客户运维会话（错误一案案主）
- 勘察输入：客户运维 2 的勘察结论（已裁定作为本方案输入）——落点 `mcp_adapter.py:2140-2206 _inject_adjfactor`、
  检测规则 `(code, 新值≠旧值@max_time) → outbox`、表与消费链已就绪（`qfq_reanchor_schema.py:537` / `consume_revision_alerts:417`）、
  验收硬指标 = 双空表 0→非 0、fail-soft 边界
- 本方案在勘察基础上**补齐两处精确化**（见 §1 注）并给出写入设计 + 幂等去重 + 验收判据
- **修订 v1.3（2026-09-23，未决项 5 取证后 · 实施形态定案）**：**情形 A（锚前移）已由既有 `factor_new` 通道覆盖**
  （实证：`qfq_trigger_queue` 总 111,604 行 = `stock_dividend` 109,284 + **`factor_new` 2,320**，
  `detection_source='tushare_adj_factor_new'`，样例 `factor_old→factor_new` 值确不同，effective_date 至 1790006400000）
  ⇒ **T3 职责收敛为「情形 B（同 `(code,time)` 值变化）」**，不重复造情形 A 的 outbox 告警。
  取证脚本：`agent_workspace/t3_factor_new_coverage_probe.py`（守卫式：daemon 状态探测 + 90s 超时放弃）
- **修订 v1.2（2026-09-23，方案过审后 / 实施前取证）**：**未决项 1 已定谳**（观察链在跑、抽样全为 `revision_no=1`、
  outbox=0；真因 = **覆盖先于观察 + 新 time 无基线** → 只有注入点覆盖前读旧值能捕获，实证支撑本设计）；
  **冷启动开关默认开**（审核裁定）；**新增未决项 5**（情形 A 是否已由既有 `factor_new` 通道覆盖 → 决定 T3 是否需为情形 A
  单独构造 outbox 告警，见 §3.6 实施前必读）；实施排期 **D+1**。取证脚本：`agent_workspace/t3_obs_probe.py`

---

## 1. 问题定义（现状取证，全部为实测）

| 项 | 实测 |
|---|---|
| 注入点 | `mcp_adapter._inject_adjfactor(df, freq, table, conn=None)`（2140-2206）：写 `qfq_aux.db` 的 `adj_factor`(股票)/`fund_adj`(ETF)，PK `(code,time)` |
| 写入方式 | **`INSERT OR REPLACE`**（2188-2190）→ **同 time 的旧值被静默覆盖**，无版本、无变化检测 |
| 调用点 | **5 处**（1395 / 1755 / 1809 / 2070 + 定义）→ **单点改动即可全覆盖** ✓ |
| 版本化写入器（已就绪） | `qfq_observation.ObservationStore.record_observations(observations, run_id, *, as_of_ms, epsilon_abs/rel, source_generation, conn)`：与最新 revision 比较 → 未变仅刷 `last_seen`；**变化则 INSERT 新 `revision_no` 行 + 同事务 `INSERT OR IGNORE` alert(pending)**（375-382） |
| outbox 表（已就绪） | `qfq_reanchor_schema.py:537` `qfq_factor_revision_alert(alert_id PK, asset_type, code, factor_time, revision_no, status, first_seen_run_id, created_at, acknowledged_at)` |
| 消费链（已就绪） | `qfq_event_discovery.consume_revision_alerts(conn, run_id, as_of_ms)`（417）← `qfq_resident_orchestrator:367` 调用；`qfq_observation.acknowledge_alert` 幂等置 acknowledged |
| **生产表状态（本次实测）** | `qfq_factor_observation` = **37,407,501 行**（基线已建）；**`qfq_factor_revision_alert` = 0 行**（outbox 空）；`adj_factor` = 25,219,309 行；`fund_adj` = 11,930,536 行 |

> **注（对勘察结论的两处精确化）**
> ① 「双空表 0→非0」在**生产**应精确表述为 **outbox 0 → 非 0**（observation 已有 3740 万行基线）；
> 副本/测试环境才是双表 0→非0（首次观察建基线 + 修订产 alert）。
> ② 缺口不只在「无检测」：注入点**从不调用版本化写入器**，故修订捕获**依赖 discovery 侧观察时序**；
> 注入点检测 + 写入器调用可使捕获**不依赖时序**（快照覆盖前即留痕）。

**缺口一句话**：`adj_factor`/`fund_adj` 快照的**同 time 值变化**在注入时被 `INSERT OR REPLACE` 吞掉，
outbox 生产恒为 0 → QFQ 重锚闭环收不到因子修订信号（双空表/空 outbox 即其表征）。

## 2. 改动范围（文件面）

| # | 落点 | 改动 |
|---|---|---|
| ① | `quantstudio/pipeline/sources/mcp_adapter.py::_inject_adjfactor` | **注入前**做「每 code @max(time) 键」的新旧值比对；变化键 → 调 `ObservationStore.record_observations(..., conn=<同一连接>)`（**同事务**）→ 再 `INSERT OR REPLACE` 快照 |
| ② | 同上（自管连接路径） | 自管路径显式 `BEGIN IMMEDIATE`，使「检测 + observation/alert + 快照覆盖」**原子一致**；复用连接路径并入调用方事务（`record_observations(conn=…)` 不自行 BEGIN/commit，见其契约） |
| ③ | 新增（可选，建议同批） | **冷启动保护开关**（见 §5 风险 1）：首轮「只记 observation 不告警」或按 code 限速；默认值待审计裁定 |
| ④ | 文档 | README + `docs/strategy_toolbox.md` / `docs/prompt_engineering.md` 涉及 QFQ 闭环/因子修订的表述同步（若涉） |

**不做**：不改快照表结构与语义（仍是 `(code,time)` PK + REPLACE 语义）；不改 `ObservationStore` 既有契约
（容差/幂等/事务语义原样复用）；不改消费链与 trigger 生成；不触碰 QFQ 复权口径与价格链。

## 3. 写入设计（核心）

### 3.1 检测规则（采用勘察口径，含两种情形精确定义）

勘察件 §2.2 已定规则：注入路径检测 `(code, 新值 ≠ 旧值 @ max_time)`。**「@ max_time」= 该 code 在目标表中的最大 time（锚点）**，分两种情形（本方案**写死**）：

| 情形 | 本批新值落点 | 比对对象 | alert 的 `factor_time` | 本项是否负责 |
|---|---|---|---|---|
| **A. 锚前移** | 落在**新的更大 time** 上 | 与**上一锚**（旧 `max(time)` 行）的值比较 | （无需本项构造） | **否——已由既有 `factor_new` 通道覆盖**（`_observe_factors` 的相邻 factor_time 值变化 → `_emit_factor_new_triggers` → DuckDB `qfq_trigger_queue`；实证 **2,320 条**，含 `factor_old/factor_new`） |
| **B. 同锚刷新** | 落在**已有 time** 上 | 与**同 time** 旧值比较 | 同该 time | **是——本项唯一职责**（discovery 因「覆盖先于观察」结构性看不到旧值 ⇒ 只有注入点覆盖前读旧值能捕获） |

- **比对口径 = 分钟表去重后**的 `(code, time)` 集合（去重在前，见 `_inject_adjfactor` 2169-2173）。
- **比较容差**：沿用 `ObservationStore` 的 `epsilon_abs=1e-9` + 相对分量，避免浮点噪声误报（与写入器同口径，防"检测说变、写入器说没变"分叉）。
- **无旧值**（新 code，或情形 A）→ **不算修订**（走基线语义，见 §3.6；情形 A 的留痕由 `factor_new` 通道承担）。

### 3.6 `revision_no` 口径（勘察标注的必答设计点）

**唯一定义源 = `ObservationStore.record_observations`**：注入点**不自行编号**，一律由写入器按
「该键最新 revision_no + 1」递增（`qfq_observation.py:367`）——保证与 `qfq_factor_observation` 口径**天然一致**
（否则消费侧对不上，勘察 §2.4 已标注）。

| 情形 | 写入器行为 | 是否告警 |
|---|---|---|
| observation 已有该键 revision（≥1）且值变化 | 新增 `revision_no = last+1` 行 + 同事务 alert(pending) | **告警** ✓ |
| observation **无**该键（从未观察） | 记为 **new**（`revision_no = 1`，建基线） | **不告警**（沿用既有「首次建立基线不触发」设计） |
| 值在容差内未变 | 仅刷 `last_seen_run_id/at` | 不告警 |

> **情形「无基线」的处置（显式定义）**：视为**基线建立**，不告警，但**必须计数并落日志**
> （`logger.info` 含 asset_type/code/factor_time/新值），供 §4-V8 的生产规模评估与漏检审视。
> 生产侧 observation 已有 3740 万行基线，预计绝大多数键落在「已观察」分支 → 变化即告警。

> **⚠️ 实施前必读（v1.3 定案，未决项 5 已取证）**：`record_observations` 是**按 `(asset_type, code, factor_time)` 逐键**
> 与自身历史比较的——因此：
> - **情形 B（同 time 值变化）＝ 本项唯一职责**：该键在 observation 已有 `revision≥1`（discovery 每周期全表观察 ⇒ **已存在的 time 键都有**），
>   交给 `record_observations` 即可产生 `revision+1` + alert ✓ —— **这是 T3 要实现的**；
> - **情形 A（锚前移，新更大 time）＝ 不由本项承担**：该 time 在 observation 无旧值 → `record_observations` 只会记 `new`（`revision_no=1`）；
>   而**既有 `factor_new` 通道已覆盖该语义**（实证 `qfq_trigger_queue` 中 `factor_new` **2,320 条**，带 `factor_old/factor_new`，
>   来源 `tushare_adj_factor_new`）⇒ **不重复造**（避免同一事件双通道留痕与双倍重锚）。

### 3.2 写入路径（同事务原子）
```
[自管连接] locked_connect(qfq_aux) → PRAGMA → BEGIN IMMEDIATE
[复用连接] 直接用调用方 conn（调用方事务）
   ↓
1) SELECT code,time,adj_factor FROM {target} WHERE (code,time) IN <本次检测集合>   ← 只查检测集合，不扫全表
2) changed = [(asset_type, code, max_time, new_value) ...]                        ← 容差外变化
3) if changed: ObservationStore(aux_db=aux).record_observations(
        changed, run_id=<见 3.3>, conn=conn)                                     ← 同事务：写 revision + alert(pending)
4) conn.executemany("INSERT OR REPLACE INTO {target} ...", rows)                  ← 快照照旧覆盖
   ↓
[自管连接] commit；[复用连接] 由调用方 commit
```
- **原子性**：检测、修订留痕、快照覆盖在**同一事务**；任一失败整体回滚（快照与留痕不分裂）。
- **并发**：沿用既有 3A 写锁（`locked_connect`）；复用连接路径**不重复取锁**（避免嵌套锁）。

### 3.3 run_id 来源（需实施时逐一核对 5 处调用点）
- 优先复用调用方作用域内已有的批次/run 标识（如 parquet 批次 id）；
- 缺省用**确定性 id**：`adjfactor-inject:{asset_type}:{target}`（`run_id` 仅落 `first_seen_run_id/last_seen_run_id`
  与 alert 溯源，**不参与 alert_id 生成**——alert_id 由 `alert_id_of(asset_type, code, factor_time, revision_no, source_generation)`
  决定，天然幂等可重放）。

### 3.4 幂等去重（三层）
| 层 | 机制 | 效果 |
|---|---|---|
| 同批同键 | `ObservationStore._preprocess`（228-236）：同键同值/容差内合并；**超容差冲突 → ValueError 整批拒绝** | 同一批内不会产生跨 revision |
| 跨批重复 | 与最新 revision 比较：**值未变 → 仅刷 `last_seen`**（357-364） | 重复注入同值**不产生** alert |
| alert 本身 | `alert_id` 确定性 + `INSERT OR IGNORE`（375-382） | 即使重复触发也**不重复入库** |
| 消费侧 | `consume_revision_alerts` 幂等转 trigger + `acknowledge_alert` 幂等置位 | 重复消费不产生重复 trigger |

### 3.5 fail-soft 边界（硬要求）
- 检测与留痕**整体** try/except 包裹：失败仅 `logger.warning`，**绝不影响快照写入**（主采集优先）；
- `record_observations` 的 `ValueError`（同批冲突/非法输入）**必须捕获**——不得因修订检测失败而让采集批次失败；
- 若「留痕」失败但快照已写：记 WARNING 含 `(asset_type, code, factor_time)` 与异常，便于事后补账（不静默）。

## 4. 验收标准（预钉）

| # | 判据（副本库/测试环境） |
|---|---|
| V1 | **outbox 0 → 非 0（触发场景 = 情形 B：同 `(code,time)` 值变化）**：空库 → 首注（基线：observation 有行、outbox=0）→ **同 time 不同值再注** → observation 新增 1 条 `revision_no=2` 行 **且** outbox 新增 1 条 `status='pending'`（生产口径：outbox 0→非0） |
| V1-note | **情形 A（锚前移）不纳入本项验收**（已由既有 `factor_new` 通道覆盖，其实证与回归归 `test_qfq_factor_new_date_trigger` / `test_qfq_event_discovery`；本项仅需保证**不干扰**该通道、不产生双份留痕） |
| V2 | **幂等**：同值重复注入 → 无新增 alert（仅 `last_seen` 刷新）；同批重放 → 无重复 alert（alert_id 去重）；`revision_no` 不跳号 |
| V3 | **容差**：容差内微变 → 不记修订（与写入器同口径） |
| V4 | **fail-soft**：注入留痕失败（模拟异常）→ 快照**仍写入成功**、批次不失败、WARNING 落日志 |
| V5 | **消费闭环**：`consume_revision_alerts` → DuckDB `qfq_trigger_queue` 生成对应 trigger + alert 置 `acknowledged`（幂等重放不重复） |
| V6 | **回归全绿**：`test_qfq_event_discovery` / `test_qfq_factor_new_date_trigger` / `test_qfq_reanchor_batch1` 等 QFQ 相关套件 + 快照锁套件 |
| V7 | **性能**：注入路径增量成本量化（每 code 一次索引查；以 minutes 5220 码/日为例实测增量耗时，阈值实测后钉） |
| V8 | **生产规模评估**：真实采集一轮后 outbox 条数与 revision 分布记录（防激增，见 §5 风险 1） |
| **V9** | **9-06 窗口重放对照表不回归**（勘察件 §四 + 方案 §五-1 硬指标）：重放 9-06 窗口，对照表逐项一致 |

## 5. 风险与缓解（含回退）

| # | 风险 | 缓解 | 回退 |
|---|---|---|---|
| 1 | **首次启用 outbox 激增**（observation 3740 万行基线 vs 快照现值可能已漂移 → 一轮产生海量 alert → 触发海量重锚） | **量级依据（勘察件 §2.4 / 方案 R6）**：仅 `(code, 新值≠旧值)` 触发，**月均每 code 0-2 次**，SQLite 行级 upsert，开销可忽略 → 激增概率低。**仍保留两项护栏**：① 首轮只读**规模验证**（记录 alert 条数与 revision 分布，不阻塞）；② **冷启动保护开关 = 默认开**（审核 2026-09-23 裁定：检测语义是「覆盖前读旧值」，存量快照已覆盖过、不重放历史，首次启用只对未来演进生效） | 关闭检测开关（配置控制） |
| 2 | 浮点噪声误报 | 沿用写入器容差（同口径）；V3 覆盖 | 调容差（配置） |
| 3 | 注入路径性能退化 | 只查「检测集合」（code×max_time，走 PK 索引），不扫全表；V7 量化 | 关闭检测（开关） |
| 4 | 事务/锁：同事务使写锁持有时长增加 | 检测集合小、索引命中；复用连接不重复取锁 | 改「先留痕后覆盖」两段式（放弃原子性，换取锁时长） |
| 5 | 与 discovery 侧观察路径**重复计数** | 写入器 revision 比较天然幂等（值未变不新增）；实施期核对 discovery 观察覆盖范围，确认无双重 revision | 调整检测集合口径 |

## 6. 未决项（实施前需确认，禁止未证实归因）

1. ~~**discovery 侧观察路径为何未产生 alert**~~ → **已定谳（2026-09-23 只读取证）**：
   - **观察链在跑**：抽样 `last_seen_at` = **今天 20:42:56（STOCK）/ 21:14:50（ETF）**（当前 daemon 周期内刚更新）；
   - **从未产生过修订**：抽样 code（600519 / 000001 / 300750 / 510300）的 observation **全部只有 `revision_no=1`**，无任何 ≥2；outbox = 0；
   - **真因 = 覆盖先于观察 + 新 time 无历史基线**：注入（`INSERT OR REPLACE`）发生在 discovery 观察**之前** →
     观察者第一次看到的就是**新值** → 记 `new`（无告警）；情形 A（锚前移，新更大 time）在 observation 里**本就无旧值** → 亦记 `new`。
   - ⇒ **只有「注入点、覆盖前读旧值」才能捕获**（discovery 侧结构性做不到）——**本方案设计正确性由此得到实证支撑**。
2. **5 处调用点的 run/batch 标识可得性**（§3.3）。
3. **冷启动保护形态** → **已裁：默认开**（审核 2026-09-23；理由：检测语义是「覆盖前读旧值」，存量快照已覆盖过、不重放历史，首次启用只对未来演进生效）。
4. ~~同域线索（云端 `etf_minutes` close 口径）~~ → **已核：不重叠**（勘察件 §2.5：数据面与语义面均不相交；
   仅间接关联——本轮实证「部分码历史 `adj_factor` 与 close 口径不一致（约 3%~5% 行）」，
   若 T3 告警恰好捕到这些码的因子刷新事件，可作该 tech-debt 的**旁证线索**，**不构成本项前置**）。
5. ~~**情形 A（锚前移）是否已由既有 `factor_new` 通道覆盖？**~~ → **已定谳（2026-09-23 守卫式只读取证）**：
   - `qfq_trigger_queue` 总 **111,604** 行 = `stock_dividend` 109,284 + **`factor_new` 2,320**；
   - `factor_new` 的 `detection_source = tushare_adj_factor_new`，样例带 `factor_old → factor_new`（值确不同），
     `effective_date` 至 **1790006400000**（≈2026-09-21）；
   - ⇒ **情形 A 已被覆盖**，**T3 收敛为只管情形 B**（不重复造，避免同一事件双通道留痕/双倍重锚）。
   - 取证脚本：`agent_workspace/t3_factor_new_coverage_probe.py`（守卫：daemon 状态探测 + 90s 超时放弃；
     本次实测查询 0.37 s 完成，daemon status 残留但 pid 已不存在 → 空闲窗）

## 7. 派单与期限

| 项 | 内容 |
|---|---|
| 归属 | 客户运维会话（错误一案案主） |
| 期限 | **方案已过审（审核即六步②，2026-09-23）**；**实施排 D+1**；三未决项（1 已定谳 / 2 run_id 可得性 / 5 情形 A 归属）随实施批收口 → 验收 → 用户确认 → 双推（触及 `quantstudio/` → trading 同步门**不豁免**） |
| 提交纪律 | `mcp_adapter.py` 为共享核心文件：① **每次 `edit` 后即时 `git diff` 自检**（防并行会话覆盖）；② **精确文件清单提交**（禁 `git add -A`）；③ **实施前建零副作用回退点**（`git stash create -u` + `git stash store`） |
| **回退纪律（CASE-009 教训）** | 回退**禁用整文件 `git checkout <sha> -- mcp_adapter.py`**——CASE-009 曾因此**连带回退同文件的 T4 改动**；一律用**精确 hunk 反向补丁**或**新建反向提交** |
| 关联 | 勘察输入：`docs/handoff/handoff-err1-t3-to-session1-20260923.md`（会话 2 移交）；台账 S1（已解除暂缓）；CASE-005/007/008/009 |
