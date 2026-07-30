# fresh_authoritative_rebase 模式设计（权威 fresh 全历史重基准）

> **状态**：R1-R4 + 阶段6A 全部实现完成并通过验收（2026-07-30）。**生产配置仍关闭**：
> `qfq_orchestrator.enabled=false`。功能进入"待生产启用"状态，启用需用户按 runbook 部署门控确认。
> **日期**：2026-07-29
> **性质**：回测框架数据语义与正确性变更（非配置调整、非纯性能优化）。
> **前置**：常驻 QFQ 编排器已落地（PR #6/#7/#8），但真实全历史重锚被 precheck BLOCK。

## 1. 问题陈述

### 1.1 业务根因：前复权基准漂移
前复权（QFQ）的基准日随除权事件漂移。每次除权后，全历史 `*_front` 必须按新基准统一重写，
否则同一证券历史 front 序列存在多基准，在边界产生伪跳空，影响收益率、均线、波动率、
技术指标、选股信号和回测净值。

因此"每日增量自动重算除权除息证券"的正确语义 = **全历史权威重基准**：用 fresh xtquant
最新基准，对该证券全历史 daily+minute 的 `*_front` 整体重算。

### 1.2 直接代码阻断：理想化乘法/加法假设
当前 `stage_fresh_daily` precheck 与 postcheck `scale_consistency` 校验 fresh 数据自身：
`open_front/open ≈ close_front/close`（纯乘法）或加法偏移 ≤1 tick（纯加法）。

真实 xtquant 的前复权数据**既不严格满足纯乘法，也不严格满足纯加法**。
候选假设 H1（未完成跨证券/跨事件验证，不构成门禁）：在部分样本中同一根 K 线四 OHLC 字段
近似共享加法偏移（近期数据 add_dev≈0），但跨证券/全历史一致性不成立（600875 add_dev 恒 0，
000012/002864 add_dev 达 0.66/2.76；详见验证报告 §2）。

`add_dev ≤ k×D`（D=close-close_front，代数恒等式）作为硬门禁已被证伪：对 1~20 tick 污染
全漏检 + 同步偏移结构性盲区 + 缺乏损坏区分能力（详见验证报告 §4）。**不作为门禁，仅作观察指标。**

> **明确区分**：基准漂移（业务根因）解释"为什么需要全历史重基准"；precheck 理想化假设
> （直接阻断）解释"为什么当前代码跑不通"。两者不能混为一谈。

### 1.3 为何不选 A（窗口化）/ B（放宽容差）
- **A 不成立**：引擎要求 fresh 分钟全量覆盖库内存量区间（窗口覆盖触发
  minute_coverage_incomplete）；且窗口内外双基准会产生边界伪跳空，不可接受。
- **B 不成立**：max_mul_dev=2.5% 本身覆盖不了；放宽到 2.5%+add_dev=0.06 会放过真实
  价格污染和列错配；未建立区分"xtquant 正常舍入"与"数据损坏"的确定性边界。

## 2. 设计目标：fresh_authoritative_rebase 模式

新增显式模型 `model="fresh_authoritative_rebase"`（独立命名，不复用 fresh_staged）。

### 2.1 核心契约（审核确认的 11 条）
1. fresh 必须来自锁定的 xtquant 数据源，保留 capture_id、内容 SHA、下载区间、下载轨迹。
2. daily 和 minute 必须**完整覆盖**库内目标证券整个存量区间：禁止窗口覆盖、缺行、增行、
   重复行、区间静默截断。
3. fresh raw OHLC 必须与库内 raw OHLC **逐 bar 对齐**；任何无法解释的 raw 差异整券 BLOCK。
4. 只允许覆盖四个 `*_front` 字段；raw、`*_back`、volume、amount、行数、主键、其它字段不变。
5. **移除**"每根 K 线必须满足纯乘法 OR 纯加法"的理想化假设；不得简单放宽其容差。
6. fresh 自身仍须检查：finite、正数、OHLC K线关系（low≤min(o,c)≤max(o,c)≤high）、
   交易日、session、cadence、代码、频率、键唯一性。
7. 写后正式表四个 front 字段必须与 staged fresh **逐 bar 精确一致**（或满足明确的序列化精度）。
8. daily 与 minute 的边界、覆盖范围、可比收盘值需**跨表校验**。
9. 任一 precheck/postcheck 失败**整券回滚**，不推进 anchor 和水位。
10. 同一 capture 重复执行必须**幂等**（capture 不可变性 + 冲突检测 + 崩溃恢复，见 §3.5）。
11. 不得通过 ratio BLOCK 后静默切换；常驻编排器须显式选择该模式并记录原因。

> ~~原第 8 条"close 复权因子链分段检查"~~ 已否决（验证报告证明无适用经验公式），
> 移入 §8 未来研究。

### 2.2 与 fresh_staged 的关系
- **复用**：逐值写入能力（`update_daily_front_from_staged` / `apply_fresh_minute_staged`
  的 UPDATE 四 front 列逻辑）、raw 逐 bar 对齐校验、完整覆盖校验。
- **替换**：precheck 的乘法/加法比例校验 → **移除**（不替换为 k×D 或因子链，由 raw 对齐 +
  写后一致承担）；postcheck 的 scale_consistency / front_chain 乘法校验 → **移除**。
- **新增**：fresh_authoritative_rebase 专属的"全历史完整覆盖 + 写后精确一致"。

> **信任边界（核心）**：fresh_authoritative_rebase 将经过来源认证和内容冻结的 xtquant front
> 作为**权威输入（oracle）**。引擎不通过经验复权公式重新证明源端 front 的经济语义，而是验证：
> 源标识、数据完整性、目标 raw 对齐、全量覆盖、字段守恒、原子写入、写后精确匹配。
> **框架不独立证明 oracle 自身的复权语义正确性，也不检测 fresh capture 阶段形成的同步 front 污染。**
> 若需检测此类错误，必须增加独立 oracle（独立公司行为链/独立复权因子源），属于未来研究
> （见 §8），不列入当前实现范围。

## 3. 详细设计

### 3.1 模型注册
```python
MODELS: Tuple[str, ...] = ("ratio", "fresh_staged", "fresh_authoritative_rebase")
```
`apply_reanchor_for_security(model="fresh_authoritative_rebase")` 必须显式传入：
`fresh_daily` + `fresh_minutes` + `model_reason` + 审计三元组（capture_id/metadata_sha/source）。
缺任一 → ValueError（防呆，杜绝静默切换）。

### 3.2 precheck（替换 stage_fresh_daily 的比例校验）

新增 `stage_fresh_authoritative(conn, asset_type, code, fresh_daily, fresh_minutes, tol, calendar)`：

**A. 基本校验（保留，与 fresh_staged 共用）**
- 必需列齐全；code 全等于目标；(code,time) 无重复；time 合法 epoch-ms。
- close / close_front finite 且 >0；open/high/low 及 front 非 NULL 时 finite>0。
- **OHLC K线关系**：`low_front ≤ min(open_front,close_front) ≤ max(...) ≤ high_front`（逐行）。
- 交易日/session/cadence：每个自然日 calendar.is_trading_day；分钟 session-aware。

**B. 完整覆盖校验（强化）**
- fresh_daily 的 (code,time) 集合 == 库内 daily 该 code 的 (code,time) 集合。
  - fresh 多行/少行/重复 → BLOCK（"全历史覆盖，禁止窗口/缺行/增行"）。
- fresh_minutes 同理（按 freq 分组，staged==target==matched）。

**C. raw 逐 bar 对齐（对齐及传输完整性保证）**
- fresh 的 raw OHLC（open/high/low/close）与库内对应行**逐 bar 精确一致**（|Δ|≤eps）。
  - 任一 raw 差异 → BLOCK（"fresh raw 与库内不一致，无法对齐"）。
  - **保障范围**：证明 fresh 与库内 raw 来源对齐、未被传输错位/串码/截断。
  - **不保障**：无法检测 fresh front 自身的同步偏移污染（属源端语义故障，见 §2.2 信任边界）。

**D. 移除的比例校验（删除）**
- ~~`open_front/open ≈ close_front/close`（乘法）~~ —— 删除。
- ~~加法偏移 ≤1 tick~~ —— 删除。
- 不以任何形式保留理想化乘法/加法假设。

### 3.3 postcheck（替换 scale_consistency / front_chain）

新增 `run_postchecks_authoritative(...)`，或基于现有 run_postchecks 增加模型分支：

**保留的校验（模型无关，rebase 仍执行）**
- daily_staged_match：写后 daily 四 front 与 staged 精确一致（mismatch=0，全覆盖）。
- kline_relation：写后 `low_front≤min(o,c)≤max(o,c)≤high_front`。
- row_conservation：修正前后行数完全一致。
- cross_table_overlap：daily close_front vs minute 当日末 bar close_front（容差 tol_cross）。
- minute_staged_match / minute_raw_match / minute_coverage / minute_tick_error（fresh_staged 四项）。

**移除的校验（不替换为因子链/k×D，见 §3.4 说明）**
- ~~scale_consistency（乘法比例）~~ —— 移除（理想化假设不适用真实 xtquant）。
- ~~front_chain_return（乘法/加法收益）~~ —— 移除（同上）。
- 这两项的防护意图由"raw 逐 bar 对齐 + 写后 front==staged 精确一致"承担（对齐/写入完整性），
  不覆盖源端语义故障（见 §2.2 信任边界）。

### 3.4 已否决的经验语义校验研究（不列入实现）

简短历史结论（详见验证报告 `docs/qfq-rebase-precision-validation-20260729.md`）：
- **因子恒定假设已证伪**：无除权区间复权因子并非恒定（81% 交易日漂移 >1e-4）。
- **add_dev ≤ k×D 已证伪**：对 1~20 tick 污染全漏检 + 同步偏移盲区 + 无损坏区分能力。
- **不列入当前实现**。当前实现只承担确定性完整性保证（raw 对齐 + 写后一致 + 守恒）。
- **独立语义 oracle 属未来研究**（§8），当前不寻找经验替代逻辑。

### 3.5 幂等性与 capture 不可变性（审核要求补充）

> 当前框架现实（必须在实现中处理）：
> - `capture_id = sha1(asset_type|code|run_id)` —— 运行轮次寻址，非内容寻址。
> - `INSERT OR REPLACE INTO qfq_fresh_capture` —— 同 capture_id 不同内容会覆盖，而非 BLOCK。
> - 引擎先 committed event，`cap.mark_applied()` 在引擎返回后另行执行，两者非同一原子事务。
> 若 event committed 后、capture 标记 applied 前崩溃，会出现 event=committed 但 capture=captured。

**幂等契约（rebase 模式必须实现）**：
1. **capture 已存在且 event 已 committed**：修复 capture 状态（标记 applied），**不重复写价**（以 committed event 为成功事实）。
2. **capture 已存在但 event 未 committed（含崩溃恢复）**：现有 qfq_fresh_capture 不持久保存 fresh payload，
   无法直接复用。采用**方案二**：允许重新采集相同请求区间，但必须与已登记的 source、code、asset_type、
   区间、daily SHA、minute SHA、metadata SHA **完全一致**；一致后继续 apply，任何差异 → BLOCK
   `capture_id_content_conflict`，**禁止覆盖原 capture 元数据**（INSERT OR REPLACE 改为先查冲突）。
3. **capture applied 但无 committed event**：异常恢复状态，**不能静默跳过**（可能写入未完成）。需人工或下轮恢复处置。
4. **event/capture 都未完成**：重新采集并校验内容（同方案二规则）后继续。
5. **内容 SHA 共同校验**：内容 SHA、请求区间、source、code、asset_type 必须共同校验一致才算同一 capture。
6. **冲突检测**：capture_id 已存在但重新采集的 metadata SHA 不同 → BLOCK `capture_id_content_conflict`。
7. **不得仅凭 capture.status='applied' 判定数据库已正确**：必须 capture applied AND event committed 同时成立才算成功。

> 这是 C 方案"权威输入可审计"的关键组成部分。

### 3.6 写入流程（复用 fresh_staged）
```
1. stage_fresh_authoritative: precheck（A-D）→ 建 staged 临时表
2. apply_fresh_minute_staged: 分钟 raw 对齐 + 覆盖 + UPDATE 四 front（复用）
3. update_daily_front_from_staged: daily UPDATE 四 front（复用）
4. run_postchecks_authoritative: 写后校验（确定性完整性校验：写后 front==staged、守恒、跨表）
5. 全部通过 → committed event + anchor 推进（同一事务）
6. 任一失败 → ROLLBACK + blocked/rolled_back event（独立短事务）
```

## 4. 验收标准（审核确认）

### 4.1 黄金基准（非"重算前后一致"）
增量重基准后的数据库结果 == 使用同一份 fresh capture 从**干净副本全量重建 front** 的结果。
（本次修复本就需改变旧基准，不要求重算前后一致。）

### 4.2 真实数据验收
- staging 暴露问题：000012、000025、000060、600000
- 既有 fixture：600875、600039、002864
- 至少 2 只 ETF（验证 0.001 tick 路由）
- 场景：多次分红、送转、分红送转混合、长期无除权、停牌跨事件

### 4.3 逐项验证（按信任域分类）
- 全历史 daily/minute front 与 fresh oracle 一致（写后 front==staged）
- raw、back、行数、非 front 列完全守恒
- **结构/覆盖故障**被挡：缺 bar、多 bar、重复 bar、错误时间（coverage precheck）
- **对齐故障**被挡：错误证券、错误日期、raw 错位（raw match precheck）
- **事务写入故障**被挡：UPDATE 后 front 被改（staged-match postcheck）
- **源端语义故障**（fresh front 同步偏移）：确定性条件不检测，需独立 oracle（未来研究，§8）
- 重复执行幂等
- 故障注入后事务完整回滚
- 代表性策略在"增量重基准库"与"同 fresh 快照干净全量重建库"间：
  信号、选股、订单、成交、持仓、现金、每日净值一致

## 5. 实现范围（分阶段）

### 阶段 R1：模型注册 + precheck（stage_fresh_authoritative）✅ 已完成
- MODELS 增加 fresh_authoritative_rebase
- apply_reanchor_for_security 增加 rebase 分支（显式模型选择 + 防呆）
- stage_fresh_authoritative：基本校验 + 完整覆盖 + raw 对齐（删除比例校验）
- 单元测试：raw 对齐 / 覆盖 / K线关系 / 故障注入

### 阶段 R2：postcheck（移除乘法假设，确定性校验）✅ 已完成
- run_postchecks 增加 rebase 分支：移除 scale_consistency（乘法）和 front_chain（乘法/加法收益）
- 保留 daily_staged_match（写后 front==staged 精确一致）、kline_relation、row_conservation、
  cross_table_overlap、minute_* 四项
- 不引入因子链分段检查或 k×D（已证伪，见 §3.4 + 验证报告）
- 单元测试：写后一致 / 守恒 / 跨表边界 / 故障注入（结构/对齐/写入三类，源端语义类不要求检测）

### 阶段 R3：真实数据验收 ✅ 已完成
- `scripts/qfq_rebase_r3_staging.py`：staging 副本（9 证券含 4 代表）对真实证券全历史重基准
- **结论**：2.1 单证券直接 apply（全 ex_dates 一次性）→ 4/4 committed，front 调整比率与
  xtquant 前复权逐日一致（机器精度 ~1e-16），raw/back/行数守恒；2.2 编排器 reconcile-once
  → `committed=6/7 单元`（ETF 无 ex_date 不入队），正式库 SHA 不变。**committed>0 成立**
  （对比 fresh_staged 演练 committed=0 被乘法校验 BLOCK），证实 rebase 功能可用。
- 证据：`output/qfq_rebase_r3_20260730/`（direct_apply_results.json / reconcile_summary.json /
  r3_conclusion.txt）

### 阶段 R4：文档 + 上线门控 ✅ 已完成
- README / strategy_toolbox / prompt_engineering / runbook / fix report 更新
- 汇报代码改动、数据语义、测试证据、风险、回退
- 用户明确确认后才 stage/commit/push/PR
- 真实 staging 全 committed 且守恒通过前，保持 enabled=false

### 阶段 6A：trigger 粒度修复（增量轮次全量 ex_dates）✅ 已完成
- **问题**：`_claim_and_merge` 按 (asset_type, code) 合并，但 `unit["effective_dates"]` 仅含
  本轮领取的 pending trigger 子集。增量轮次下，同券部分 trigger 已 committed、仅新增 pending
  被领取 → 传给引擎的 `ex_dates_ms` 不全 → 局部重基（旧 ex_date 被忽略）。
- **修复（方案 A）**：`qfq_resident_orchestrator._reanchor_security` 在 rebase 模式下改从
  `stock_dividend + factor_observation` 取该证券**全量** ex_dates（`_security_effective_dates`），
  而非仅 trigger 子集。仅影响 rebase 模式（ratio/fresh_staged 不走此路径），签名不变。
- **测试**：3 新增用例（增量子集→全量 / 多 trigger 合并 e2e / rebase BLOCK→水位 held），
  全量 qfq 回归 214 passed（原 211 + 3）。

## 6. 不变项（铁律）
- ratio / fresh_staged 模式逐位不变（rebase 是独立新增，不改既有模型行为）。
- apply_reanchor_for_security 既有参数签名不变（model 新增值，其余不变）。
- 四价格表 raw / *_back / volume / amount / 行数 / 主键不受 rebase 影响。
- 失败路径（blocked/rolled_back/failed）绝不推进 anchor，绝不污染已提交数据。
- 不碰 daemon.py 既有逻辑（rebase 由编排器显式选择，编排器改动单独审核）。

## 7. 已知风险
- **源端语义故障不可检测**（信任边界核心风险）：fresh front 同步偏移污染无法被确定性条件
  检测。C 方案以"xtquant front 为权威 oracle"为前提接受此风险；若不接受，需独立 oracle（§8）。
- **raw 逐 bar 一致的全市场覆盖率未验证**：预检仅抽样 000012/510300（daily，0 差异），
  全市场（5202 股票 + 1605 ETF）+ minute raw 的差异率待扩大预检（**生产启用前必做**，见 runbook 门控）。
  若部分证券 raw 不一致，需 BLOCK 或先 raw 迁移。
- **trigger 粒度（增量重基）已修复**：阶段 6A 保证 rebase 永远传全量 ex_dates，增量轮次不再丢
  历史除权日。但**部分码失败不 degraded**（runbook 风险2）仍保留现状：某证券部分码失败可能用旧
  快照，是否升级为"任意单码失败即 degraded"另立后续正确性变更审核。
- 全历史 fresh 下载耗时：2076+ 行 daily + 数万行 minute 的 xtquant 全量下载，需评估性能。
- **R3 验收结论**：真实 staging 上 rebase 端到端 committed>0（对比 fresh_staged committed=0），
  front 调整比率与 xtquant 前复权逐日一致（~1e-16）；功能可用，仅待生产启用门控。
- ~~factor_drift_tol / 因子链分段检查~~ —— 已证伪，移入 §8 未来研究，不列入实现。

## 8. 未来研究：独立语义 oracle（不在当前实现范围）

C 方案以"xtquant front 为权威 oracle"为前提，不检测源端同步 front 污染。若未来需要检测
此类错误，必须引入**独立 oracle**（不共享同一错误链路的验证来源），候选方向（均待研究，
不构成当前承诺）：
- 独立公司行为事件链（cash_div/stk_div/ex_date）驱动的复权因子重算，与 xtquant front 抽样核验。
- 独立前复权因子源（如 Tushare adj_factor / fund_adj）的交叉验证。
- 基于公司行为参数的复权结果区间校验。

> 这些方向在 add_dev ≤ k×D 被证伪后提出，尚未验证可行性，不列入 R1-R4 实现阶段。
