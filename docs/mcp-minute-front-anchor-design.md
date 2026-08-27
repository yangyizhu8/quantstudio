# MCP 分钟 front 锚点一致性闭环方案（minute-front-anchor-closure）

| 项 | 内容 |
|---|---|
| 文档版本 | v1.2（2026-08-17） |
| 状态 | **G0/G1 关闭 → G2a 受阻修订：v1.2（独立因子交叉验证）待 ZCode 复核**（新铁律六步流水线步骤 2） |
| 变更定性 | **框架行为/正确性变更**（AGENTS.md 铁律：六步流水线——方案 → 审计 → 实施 → 验收 → 用户确认 → 双仓库推送） |
| 适用范围 | QFQ 重锚编排（`qfq_resident_orchestrator` / `qfq_event_discovery` / `qfq_observation`）+ 数据巡检（`quality_audit`）+ 因子表治理 |
| 实测证据 | `docs/mcp-minute-caliber-audit-20260816.md`（v1.1）+ `docs/evidence/mcp-minute-anchor-g1-20260816.md` + G2a 受阻实测（2026-08-17） |
| 前置事实 | **MCP 为唯一权威源**（xtquant/tushare 等全部废弃，用户 2026-08-16 确认） |

---

## 1. 摘要（一页结论）

1. **问题**：ETF 除权后，分钟表历史 `*_front` 列不重锚 → 同一 code 序列内 front 基准不一致（锚点漂移）。实测 520810：08-12 除权（因子 1.0108→1.0158），08-09 回填的 06-08 bar front 仍锚定旧因子，偏差 **~0.5%**；563020/159307 同批次偏差 0.5-1.2%。引擎 `fq='pre'` 分钟信号（动量/止损）在除权日前后出现假跳变。
2. **根因链（已核实）**：
   - `config/profiles/mcp_only/qfq_aux_paths.json` **`released=false`** + `qfq_aux.db` **无编排表**（qfq_cycle_run / qfq_watermark_intent / qfq_reanchor_event / qfq_trigger_queue / qfq_observation_cursor 全部 MISSING）→ **QFQ 重锚编排从未激活**（daemon 走 legacy 水位路径，`daemon.py:2288-2298` 确认"released=false → 必须仍走 legacy"）；
   - 因子观测部分运行（`qfq_factor_observation` 979 万行）但 **`qfq_factor_revision_alert` = 0 行** → 因子修订检测/告警链路未触发；
   - 事件发现仅扫 `stock_dividend`（`qfq_event_discovery.scan_stock_dividend`）→ **ETF 除权（etf_dividend）不在发现范围**；
   - fresh 采集已支持 MCP（`qfq_fresh_fetcher_factory` 按 price_source 分派，MCP-only fail-fast）——基础设施就绪，编排未激活。
3. **方案**：三阶段——① 巡检先行（纯观测，零数据改动）；② 重锚闭环激活（编排激活 + ETF 除权发现 + 分钟重锚验证）；③ 因子表世代清理（数据治理）。
4. **验收红线**：520810/563020/159307 案例 V2 判别公式全部 MATCH；正常标的零误报；因子非单调比例显著下降；分钟 `fq='pre'` 信号修复前后差异仅限"锚点漂移被修正"。
5. **回退条件**：阶段 1 纯新增规则可删；阶段 2a 定点重锚以 code 级备份回退；阶段 2b 以 `released` 门单开关回退（由 TD-D2 轨道控制，见 §4.0）；阶段 3 保留因子备份表。

### 1.1 审核记录（ZCode 第一轮，2026-08-16）

**审核结论：原则通过（G0 附条件放行）**，事实核验全部属实，附 1 项必须解决的轨道冲突（R1）+ 5 项 G1 前修订（R2-R6）；D1-D4 裁决同意。v1.1 逐项落实：

| 项 | 审核要求 | v1.1 落实 |
|---|---|---|
| R1（必须） | 与 TD-D2/mcp-gen1 轨道 ⑤ 释放门冲突：明确 ①激活后落点 aux DB ②执行顺序 ③⑤ 释放唯一执行方 | §4.0 新增轨道协调节 |
| R2 | G2 拆分为 G2a（定点重锚，零 gate 改动）+ G2b（编排激活，受 R1 约束）；修复与激活解耦 | §4 阶段 2 拆分 + §7 WBS |
| R3 | 因子直算 fallback 前置条件：该 code 因子单调性复验通过（或阶段 3 完成后）才允许 | §4 阶段 2 B3 |
| R4 | D3 第一步改为排查 0 告警根因（979 万行观测 0 告警） | §4 阶段 2 B2 + §2.4 根因（实测：ETF 0 观测 + 追加式写入使同键值变化永不触发） |
| R5 | 执行环境与数据安全红线：并发执行方案 + 重锚前四 front 列备份 + raw/volume/amount 逐位零改动硬验收 | §5 新增执行环境与数据安全 |
| R6 | A1 阈值复议：FAIL 降至 0.5% 或论证 1%；WARN 0.3% 误报率实测扩至 500 只/全 ETF 池 | §4 阶段 1 A1 + §8 验收 |

**D1-D4 裁决**：D1 同意纳入但按 R2 拆分；D2 同意 fresh_staged 为主、因子直算仅 fallback 且受 R3；D3 同意先验证 alert 链路但按 R4 改为先排查根因（根因已实测定性 → 结论：实施 scan_etf_dividend）；D4 同意独立立项。

### 1.2 审核记录（ZCode 第二轮：G2a 受阻，2026-08-17）

**审核结论：方向同意选项 a（因子直算），但"殊途同归"论证存在循环**——V2 只证明"入库时用本地这份因子"，不证明因子与真实（MCP/交易所）一致；510020 类别②（front=raw×0.1006）证明污染行曾真实烧进 front。**v1.2 修订（选项 a 的批准条件）**：

| 项 | 审核要求 | v1.2 落实 |
|---|---|---|
| R7（硬前置） | **独立因子交叉验证**：优先 = prefetch 66 窗全市场导出工件（`data/mcp_landing/exp_etf_minutes_*`，18.1GB 已落盘）提取 124 只分钟 adj_factor 构建独立参照；备选 = 除权事件日定点小查询 | §4.1 新增独立验证设计；提取任务已启动（`output/g2a_independent_factors.csv`） |
| R8 | **分级放行**：本地因子（段合并+尖刺过滤）与独立参照一致的 code → 因子直算重锚；不一致 → 挂起（等阶段 3 或 export_cache 修复后 fresh_staged） | §4.1 分级规则 |
| R9 | **R3 重述**：放行条件 = 独立源一致性验证通过 + per-bar 合理性校验（front/close 有界、相邻 bar 连续性、raw 逐位不动）；单调性降为观测指标（A2 已覆盖），不再作为放行条件 | §4.1 |
| R10 | **注记 1 结论更新**：直算 UPDATE 的 SET 列表写死（仅四 front 列），按此验收 | §4.2 直算 UPDATE 契约 |
| R11 | export_cache 0 行 bug **独立立项**（六步流水线），先留最小复现证据；**G2b 依赖**：gen1 释放后 fresh_staged 仍需 MCP 拉取 → export_cache 修复是 G2b 前置，写入 §4.0 | §4.0 / §7 |
| R12 | prefetch exit 1 完整输出归档证据目录 | `docs/evidence/g2a-prefetch-failure-20260817.md` |

**原验收全套保留**：停机窗口、code 级四 front 列备份 + SHA-256、raw/volume/amount/update_time 逐位零改动硬验收、V2 复验、引擎归因档回归。**124 只 → 分级后的实际执行清单最终由用户批准。**

---

## 2. 问题定义与实测证据

### 2.1 锚点漂移（实测实锤，`docs/mcp-minute-caliber-audit-20260816.md` §3.5）

| 标的 | 事件 | 证据 |
|---|---|---|
| 520810 | 08-12 除权（因子 1.0108→1.0158）；06-08 bar 08-09 回填，front 锚定 1.0108 | `close_front/close`=0.996834 vs 最新因子预期 0.990352，偏差 ~0.5%（=1.0158/1.0108−1） |
| 563020 | 08-09 回填批次 | 偏差 ~1.0% |
| 159307 | 08-09 回填批次 | 偏差 ~1.2% |

### 2.2 因子序列非单调（实测实锤，§3.6）

- `qfq_aux.db.adj_factor`（股票）：**4826/5791（83.3%）** 非单调；
- `qfq_aux.db.fund_adj`（ETF）：**277/2227（12.4%）** 非单调；
- 典型：520810 07-01 白天因子 1.0076 → **1.0** 回落（世代混存痕迹）；
- `qfq_factor_observation` 979 万行观测，`qfq_factor_revision_alert` **0 行**——未被巡检捕获。

### 2.3 正确性基线（实测确认，§5 V2）

分钟 `close_front = raw × adj_i/adj_latest` **精确成立**（30 只股票 + 6 只 ETF，6 位小数匹配）——重锚公式可复用，无需新口径。

### 2.4 0 告警根因（R4 实测定性，2026-08-16）

`qfq_factor_observation` 979 万行 **全部 asset_type='STOCK'、revision_no 全部=1、ETF 0 行**（520810 0 行）。根因两层：

1. **观测从未覆盖 ETF**：`_observe_factors` 的 ETF 分支（fund_adj 观测）未产生任何行——观测链路对 ETF 完全无效，ETF 除权因子变化天然不可能触发 alert；
2. **"同键值变化"检测与因子表写入模式不匹配**：`record_observations` 按 `(asset_type, code, factor_time)` 键比对值变化（`qfq_observation.py:305-377`）；而因子表为**追加式写入**（每次注入产生新 time 键，键首次写入即终值，如 520810 08-12 00:00 键首次出现即 1.0158）→ 观测每次看到新键 → INSERT revision_no=1，**已有键的值变化场景不存在** → revision 永不 ≥2 → alert 恒 0。

**结论（R4）**：alert 链路现状无法承载 ETF 除权发现 → D3 定案：**实施 `scan_etf_dividend`（与 scan_stock_dividend 对称）**；观测链路本身（ETF 覆盖 + 值变化检测适配追加式写入）纳入 G2b 范围（编排激活时一并修复/重建基线）。

---

## 3. 现状链路（代码证据）

| 环节 | 现状 | 证据 |
|---|---|---|
| 入库 | MCP raw + adj_factor → daemon 提取因子 → aligner `_apply_qfq`（front = raw × adj_i/adj_latest）→ writers upsert | `daemon.py:925-942`、`aligner.py:415-435` |
| 重锚引擎 | 完整覆盖分钟表：方法 B ratio（R = target_scale/stored_scale）、fresh_staged（逐值 UPDATE 四 front 列）、方法 A golden、postcheck minute_* 四项 | `qfq_reanchor_engine.py`（方法 B:440+、fresh_staged:658+、postcheck:1309+） |
| fresh 源 | 工厂按 `price_source` 分派：mcp → `McpFreshFetcher`（MCP raw + adj_factor → front，替代 xtquant 三段式）；MCP-only fail-fast | `qfq_fresh_fetcher_factory.py:49-69`、`qfq_fresh_capture.py:738-769` |
| 事件发现 | 仅 `scan_stock_dividend`（STOCK 分红）；ETF 除权不在范围 | `qfq_event_discovery.py:130-267` |
| 因子观测 | `qfq_observation.ObservationStore` 版本化写入（revision_no≥2 同事务发 alert） | `qfq_observation.py:299-377` |
| 编排 | `qfq_resident_orchestrator.run_post_ingest`：recover→discover→claim→reanchor→gate→commit/hold；**受 `released` 门控制** | `qfq_resident_orchestrator.py:1273-1378`、`daemon.py:2288-2298` |
| 激活状态 | **`released=false`；qfq_aux.db 无编排表；revision alert 0 行** | `config/profiles/mcp_only/qfq_aux_paths.json`、`qfq_aux.db` 实测 |

---

## 4. 方案设计

### 4.0 轨道协调（R1，必须满足）

**与 TD-D2/mcp-gen1 轨道的 ⑤ 释放门边界**（提交 `f69462e`「TD-D2 因子库 legacy→mcp-gen1 统一路由收敛」）：

- **路由机制**：`qfq_aux_router.resolve_runtime_aux_path` 双条件齐备才指向 gen1 世代库：①主库 `qfq_active_cutover` 实查存在记录（当前 `b6_formal_20260807_v2` 已 active）②`qfq_aux_paths.json` 顶层 `released=true`（⑤ 释放门，当前 false）；任一不满足/不可判定 → fail-secure legacy（`qfq_aux.db`）；
- **① 激活后编排/观测/告警落点**：`released=true` 后路由解析到 **`qfq_aux_mcp_gen1.db`**（gen1 世代，与 MCP 唯一权威源一致）。该库当前 observation/alert 表为空、**编排表缺失**（与 legacy 库同构缺失）→ **G2b 前置条件：gen1 库初始化编排 schema（`qfq_reanchor_schema.init_*`）+ ETF 观测基线重建**（当前 979 万行观测在 legacy 库且仅 STOCK，不可直接复用）；
- **② 执行顺序**：TD-D2/C-6 **先**完成 ⑤ 释放（`released=true`）→ 本方案 G2b 在 released=true 前提下激活编排（gen1 schema 初始化 → 单 code 演练 → 除权窗口标的 → 全量）；**G2a（定点重锚）不依赖 released，独立先行**——即使 TD-D2 延后，数据正确性已恢复（R2 解耦的收益）；
- **③ ⑤ 释放唯一执行方**：**TD-D2/C-6 轨道**。本方案**明示不触碰 `released` 开关**；若审计判定需本方案推进释放，须先与 TD-D2 轨道书面交接（本方案不擅自翻转）。
- **④ export_cache 修复 = G2b 前置（v1.2，R11）**：实测（2026-08-17）`export_cache` 读取路径返回 0 行（缓存命中后 codes 过滤 124→0，`mcp_adapter.py` 框架缺陷）→ **MCP fresh 拉取当前不可用**。该 bug 阻塞所有 fresh_staged 场景（含 G2b 编排激活后的重锚路径）——**独立立项走六步流水线修复**（最小复现证据见 `docs/evidence/g2a-prefetch-failure-20260817.md`），修复完成前 fresh_staged 不可用，G2a 采用 v1.2 因子直算（§4.1）。

### 阶段 1：巡检先行（纯观测，零数据改动）

**A1 分钟 front 锚点漂移巡检**
- 规则：对除权窗口内标的（`stock_dividend`/`etf_dividend` ex_date ∈ [T-7, T+7]），校验 `close_front/close` vs `adj_i/adj_latest`（因子来自 qfq_aux.db）：
  - 偏差 > 0.3% → WARN；**> 0.5% → FAIL**（R6：FAIL 门槛从 1% 降至 0.5%，确保 520810（0.5% 级）可检出；563020（1.0%）明确 FAIL）；
- 落点：`quality_audit.py`（沿用现有规则模式，SQL 窗口函数实现，V4a 已验证可行）；
- 增量/全量：仅校验除权窗口标的（候选集合小，全量扫描可控）；
- **误报率实测（R6）**：WARN 0.3% 阈值在**全 ETF 池**（或 ≥500 只正常标的）实测误报率，误报率 > 0.5% 则上调 WARN 阈值。

**A2 因子序列非单调告警**
- 规则：`adj_factor`/`fund_adj` 按 code LAG 扫描，回落 > 1e-9 → 告警（接入 `qfq_factor_revision_alert` 或独立 `FactorMonotonicityAlert`）；
- 落点：巡检周期任务（daemon 巡检段或独立 CLI）。

**验收（阶段 1）**：520810/563020/159307 全部检出（520810 WARN/FAIL 按 R6 阈值定）；正常标的零误报（≥500 只/全 ETF 池实测）；全表扫描 < 5 分钟。
**回退**：纯新增巡检规则，删除即回退，无数据影响。

### 阶段 2a：定点重锚修复（R2 拆分，零 gate 改动，先行；v1.2 修订）

**背景（v1.2）**：MCP fresh 拉取不可用（export_cache bug，R11）→ fresh_staged 路径暂不可行；
"殊途同归"论证经审计指出循环（V2 只证"入库用本地因子"，不证因子真实）→ 放行条件改为
**独立源一致性验证**（R7-R9），与 fresh_staged 模型本身无关。

**v1.2 执行路径 = 因子直算重锚（分级放行）**：

1. **独立因子参照（R7，硬前置）**：从 prefetch 已落盘的 MCP 全市场导出工件
   （`data/mcp_landing/exp_etf_minutes_*`，18.1GB，66 窗覆盖 2025-01~2026-08）提取
   124 只候选的分钟级 `adj_factor`（`output/g2a_independent_factors.csv`）——**独立于本地
   qfq_aux.db**（云端直接产物）；备选路径：除权事件日定点小查询（MCP 短窗口 fetch）；
2. **一致性判定（R8 分级放行）**：对每 code，本地因子（段合并+尖刺过滤）与独立参照在
   关键时点（最新日、除权事件日、因子变化点）相对差 < 0.1% → **一致 → 放行因子直算**；
   不一致 → **挂起**（等阶段 3 因子治理或 export_cache 修复后 fresh_staged）；
3. **因子直算重锚（UPDATE 契约，R10 写死）**：
   ```sql
   UPDATE etf_minutes SET
     open_front = open  * adj_i / adj_latest,
     high_front = high  * adj_i / adj_latest,
     low_front  = low   * adj_i / adj_latest,
     close_front= close * adj_i / adj_latest
   WHERE code = ? AND freq = '1min'
   ```
   **仅四个 front 列**；adj_i = 该 bar 日因子（过滤后序列），adj_latest = 该 code 最新因子；
4. **per-bar 合理性校验（R9）**：重锚后 front/close ∈ [0.05, 20]、相邻 bar front 比率连续
   （单 bar 跳变 < 20% 且与 raw 同向）、raw 四列逐位不动（SQL 契约保证 + 备份 hash 对照）；
5. 执行环境按 §5（停机窗口 + code 级备份 + 零改动硬验收）；124 只 → **分级后的实际执行
   清单由用户批准**。

**验收（阶段 2a，v1.2）**：放行 code 重锚后 V2 判别全部 MATCH（偏差 < 0.05%）；raw/volume/
amount/preClose/data_source/update_time **逐位零改动**（R5 硬验收，UPDATE SET 契约 R10）；
挂起 code 清单如实记录（原因：独立参照缺失/不一致），**不静默跳过**。
**回退**：code 级 front 备份恢复。

### 阶段 2b：编排激活 + 水位 gate（R2 拆分，受 §4.0 R1 约束）

- **前置（R1）**：TD-D2/C-6 完成 ⑤ 释放（`released=true`）→ 路由解析到 gen1 库；
- gen1 库编排 schema 初始化（`qfq_reanchor_schema.init_duckdb_schema/init_sqlite_schema`）→ ETF 观测基线重建（修复 §2.4 观测覆盖）+ 值变化检测适配追加式写入；
- `EventDiscovery` 新增 `scan_etf_dividend`（与 scan_stock_dividend 对称，R4 定案）；
- 编排周期生效（recover→discover→claim→reanchor→gate→commit/hold），小范围演练 → 全量；
- 范围控制：先单 code 演练（520810）→ 小范围（除权窗口标的）→ 全量。

**验收（阶段 2b）**：520810 类新除权事件自动触发重锚（cycle 审计表有记录）；水位 gate 通过；ETF 观测基线建立（revision 检测生效）。
**回退**：`released` 置回 false（由 TD-D2 轨道协调，单开关）。

### 阶段 3：因子表世代清理（数据治理，独立立项，D4）

**C1 世代盘点**：`adj_factor`/`fund_adj` 中 xtquant-legacy 世代与 MCP 世代行分布（按 data_source/时间/值域）；
**C2 收敛**：以 MCP 因子为准重建唯一世代（备份 → 清理 legacy 行 → 单调性复验）；
**验收**：非单调比例 < 1%；520810 类因子回落消除；回填/重锚输入基准唯一。
**回退**：备份表恢复。

---

## 5. 执行环境与数据安全（R5，必须满足）

| 项 | 方案 |
|---|---|
| **主库并发** | 主库（`data/quantstudio.db`）当前被生产 daemon 独占锁定（实测 IO Error）→ 所有巡检（只读）与重锚（UPDATE）**在 daemon 停机窗口执行**（运维排程），或经协调写入路径（如 QFQMaintenance 短事务 + busy_timeout）；禁止在 daemon 运行期直接并发写主库 |
| **重锚前备份** | 每个受影响 code 重锚前，将四 front 列（`open_front/high_front/low_front/close_front`）导出快照（`output/reanchor_backup/<code>_<batch>.csv` 或备份表），含 SHA-256 清单 |
| **硬验收：零改动** | 重锚后校验 raw OHLC / volume / amount / preClose / data_source / update_time **逐位零改动**（复用 postcheck `minute_raw_match` 口径）；任何一行偏差 = 验收失败 |
| **因子直算前置（R9，v1.2 重述）** | 因子直算重锚放行条件 = **独立源一致性验证通过**（本地过滤后因子 vs MCP 导出参照，关键时点相对差 < 0.1%）**+ per-bar 合理性校验**（front/close 有界、相邻 bar 连续性、raw 逐位不动）。**单调性降为观测指标**（A2 已覆盖），不再作为放行条件——理由必须是外部验证，不是自洽（R7 破循环论证） |
| **回滚** | code 级备份恢复 + `released` 门回退（2b）+ 因子备份表（阶段 3） |

## 6. 待审计裁决点（D1-D4，已按 ZCode 裁决落实）

| # | 裁决点 | 裁决结果 | 落实 |
|---|---|---|---|
| D1 | 阶段 2 编排激活是否在本方案范围（全仓水位 gate 生效） | **同意纳入，但按 R2 拆分**：G2a 定点修复先行（零 gate），G2b 编排激活独立受 R1 约束 | §4 阶段 2a/2b |
| D2 | 分钟重锚模型：fresh_staged（McpFreshFetcher）vs 因子直算 | **v1.2 修订**：fresh_staged 因 export_cache bug 暂不可用（R11 立项修复）；G2a 采用**因子直算（分级放行）**，放行条件 = 独立源一致性验证（R7/R8/R9）；G2b 仍以 fresh_staged 为模型根基（export_cache 修复后） | §4 阶段 2a / §4.0-④ |
| D3 | ETF 除权发现：alert 链路 vs etf_dividend 扫描 | **先排查 0 告警根因（R4）**：实测根因 = ETF 0 观测 + 追加式写入使同键值变化永不触发 → **alert 链路现状不可承载，定案实施 `scan_etf_dividend`** | §2.4 / §4 阶段 2b |
| D4 | 阶段 3 因子清理并入本方案 vs 独立立项 | **同意独立立项**——数据治理与编排修复分离 | §4 阶段 3 |

---

## 7. 实施 WBS（门禁）

| 阶段 | 内容 | 门禁 |
|---|---|---|
| G0 | 本方案 ZCode 审计（v1.0）→ R1-R6 修订（v1.1）→ **v1.2 受阻修订（R7-R12）** | v1.2 复核通过（待回传） |
| G1 | 阶段 1 巡检（A1+A2）+ 测试 + 误报率实测（≥500 只/全 ETF 池） | ✅ **已关闭**（9 新 + 42 回归全绿；G1 证据 `docs/evidence/mcp-minute-anchor-g1-20260816.md`） |
| G2a | 阶段 2a 定点重锚（v1.2）：**独立因子提取（MCP 导出工件）→ 一致性分级 → 因子直算 UPDATE（仅四 front 列）→ 备份/零改动/合理性校验** | 放行 code V2 复验 MATCH + raw/volume/amount/update_time 逐位零改动 + per-bar 合理性（R9）；挂起清单如实记录；**执行清单用户批准** |
| G2b | 阶段 2b 编排激活（**前置：TD-D2/C-6 ⑤ 释放 + export_cache 修复（R11 立项）**；gen1 schema + ETF 观测基线 + scan_etf_dividend + 演练→全量） | 新除权自动触发重锚 + 水位 gate 通过 + cycle 审计有记录 |
| G3 | 阶段 3 因子清理（独立立项，D4） | 非单调 <1% + 备份可回退 |
| X | **export_cache 0 行 bug 独立立项**（R11，六步流水线；最小复现证据归档） | 复现 → 修复 → 验收 → 用户确认 |
| G4 | 文档同步（README/docs/CHANGELOG，六步流水线同步内容完整要求） | 一致性检查 |
| G5 | 双仓库推送 | 用户确认 |

---

## 8. 验收标准总则

1. 阶段 1：520810/563020/159307 检出（520810 按 R6 阈值 WARN 或 FAIL；563020/159307 FAIL）；随机 ≥500 只/全 ETF 池正常标的零误报（WARN 0.3% 实测）；全表扫描 < 5 分钟；
2. 阶段 2a（v1.2）：放行 code 重锚后 `close_front/close` 与最新因子预期偏差 < 0.05%（tick 级）；raw OHLC/volume/amount/preClose/data_source/update_time **逐位零改动**（R5 硬验收，UPDATE SET 契约 R10）；**独立源一致性验证通过**（R7/R8）；per-bar 合理性校验通过（R9）；挂起 code 清单如实记录；
3. 阶段 2b：新除权事件自动触发重锚（qfq_cycle_run/qfq_jump_audit 有执行记录）；四价格表水位正常推进（gate 通过）；ETF 观测基线建立（revision 检测生效）；
4. 阶段 3：非单调比例 < 1%；520810 07-01 因子回落消除；
5. 引擎回归：分钟 `fq='pre'` 信号回测修复前后差异**仅限锚点漂移被修正的标的/日期**（唯一允许差异，逐项归因），其余逐位一致；**基线须在干净基线产出**（复用 `scripts/etf_t0_regression.py` 方法学：零差异档 + 归因档双档，参照上一轨道 G2 教训——修正对基线、PYTHONHASHSEED=0）；
6. 不符合即回退（各阶段回退条件见 §4/§5）。

---

## 9. 证据索引

- 审计文档：`docs/mcp-minute-caliber-audit-20260816.md`（v1.1，2026-08-16）
- 实测脚本（已清理，方法学记录在审计文档 §5）
- 代码位置：见 §3 现状链路表
