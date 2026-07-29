# fresh_authoritative_rebase 模式设计（权威 fresh 全历史重基准）

> **状态**：设计阶段（待用户确认后进入实现）。不碰生产配置、不直接改代码。
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

真实 xtquant 的前复权数学模型经实测确认为**纯减法复权**（`front = raw - 累积分红 D`）：
同一根 K 线内 `open-open_front == close-close_front`（减法偏移差 add_spread 在近期数据
精确为 0）。但 precheck 的乘法比例假设不成立（减法复权下比例天然不恒定）。

加法豁免在**近期数据**成立（2024 年 619 行 add_dev 全为 0），但在**全历史**失效：
累积分红 D 越大（历史越早），xtquant 的减法偏移精度误差越大（2018 年 D≈2.75 时
add_dev 达 0.06，超 1 tick=0.01）。这是 xtquant 对历史数据前复权的**精度衰减特性**
（疑似 float32 或有限精度内部计算）。实测全历史 2076 行中 124 行 add_dev 超 tick。

> **明确区分**：基准漂移解释"为什么需要全历史重基准"；precheck 假设是"为什么当前代码
> 跑不通"。两者不能混为一谈。

> **精度衰减的确定性边界**：add_dev 与累积分红 D 正相关。D 的计算：`D(t) = close - close_front`
> （fresh 数据可直接算）。add_dev 上界应建模为 `add_dev ≤ k × D + c`（k 为精度衰减系数，
> c 为固定舍入），从真实样本拟合可证明的上界，而非反向调参。

### 1.3 为何不选 A（窗口化）/ B（放宽容差）
- **A 不成立**：引擎要求 fresh 分钟全量覆盖库内存量区间（窗口覆盖触发
  minute_coverage_incomplete）；且窗口内外双基准会产生边界伪跳空，不可接受。
- **B 不成立**：max_mul_dev=2.5% 本身覆盖不了；放宽到 2.5%+add_dev=0.06 会放过真实
  价格污染和列错配；未建立区分"xtquant 正常舍入"与"数据损坏"的确定性边界。

## 2. 设计目标：fresh_authoritative_rebase 模式

新增显式模型 `model="fresh_authoritative_rebase"`（独立命名，不复用 fresh_staged）。

### 2.1 核心契约（审核确认的 12 条）
1. fresh 必须来自锁定的 xtquant 数据源，保留 capture_id、内容 SHA、下载区间、下载轨迹。
2. daily 和 minute 必须**完整覆盖**库内目标证券整个存量区间：禁止窗口覆盖、缺行、增行、
   重复行、区间静默截断。
3. fresh raw OHLC 必须与库内 raw OHLC **逐 bar 对齐**；任何无法解释的 raw 差异整券 BLOCK。
4. 只允许覆盖四个 `*_front` 字段；raw、`*_back`、volume、amount、行数、主键、其它字段不变。
5. **移除**"每根 K 线必须满足纯乘法 OR 纯加法"的理想化假设；不得简单放宽其容差。
6. fresh 自身仍须检查：finite、正数、OHLC K线关系（low≤min(o,c)≤max(o,c)≤high）、
   交易日、session、cadence、代码、频率、键唯一性。
7. 写后正式表四个 front 字段必须与 staged fresh **逐 bar 精确一致**（或满足明确的序列化精度）。
8. close 复权因子链**分段检查**：结合已知除权事件/因子变化分段，无事件区间不得出现
   无法解释的因子突变。
9. daily 与 minute 的边界、覆盖范围、可比收盘值需**跨表校验**。
10. 任一 precheck/postcheck 失败**整券回滚**，不推进 anchor 和水位。
11. 同一 capture 重复执行必须**幂等**。
12. 不得通过 ratio BLOCK 后静默切换；常驻编排器须显式选择该模式并记录原因。

### 2.2 与 fresh_staged 的关系
- **复用**：逐值写入能力（`update_daily_front_from_staged` / `apply_fresh_minute_staged`
  的 UPDATE 四 front 列逻辑）、raw 逐 bar 对齐校验、完整覆盖校验。
- **替换**：precheck 的乘法/加法比例校验 → 改为 raw 对齐 + 基本校验；
  postcheck 的 scale_consistency 乘法校验 → 改为复权因子链分段检查。
- **新增**：fresh_authoritative_rebase 专属的"全历史完整覆盖 + 因子链分段 + 写后精确一致"。

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

**C. raw 逐 bar 对齐（核心安全网）**
- fresh 的 raw OHLC（open/high/low/close）与库内对应行**逐 bar 精确一致**（|Δ|≤eps）。
  - 任一 raw 差异 → BLOCK（"fresh raw 与库内不一致，无法解释"）。
  - 这是 rebase 模式的核心安全保证：只信任 raw 一致时的 front 重写。

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

**替换的校验**
- ~~scale_consistency（乘法比例）~~ → **复权因子链分段检查**（见 3.4）。
- ~~front_chain_return（乘法/加法收益）~~ → **分段内收益一致性**（见 3.4）。

### 3.4 复权因子链分段检查（⚠️ 待验证设计假设，非已确认契约）

> **状态**：本节为待验证设计假设。只读验证报告（`docs/qfq-rebase-precision-validation-20260729.md`）
> 已证明 `add_dev ≤ k×D` **不能**作为硬门禁（跨证券不一致 + 故障全漏检 + 整段加减盲区）。
> 本节原设计的"无除权区间因子恒定"假设已被证伪（81% 交易日因子漂移 >1e-4）。
> 在确定替代校验逻辑前，本节不作为实现依据。

**原假设（已证伪）**：前复权因子 `f(t) = close_front(t)/close(t)` 在无除权区间恒定。
实测 000012 全历史 81% 交易日因子漂移 >1e-4，假设不成立。

**候选 H1（待验证，不构成门禁）**：同一根 K 线四 OHLC 字段近似共享加法偏移。
部分证券（600875/510300）精确成立（add_dev=0），但 000012/002864 不成立（add_dev 达 0.66/2.76），
且跨证券无统一规律（add_dev 主要受日内振幅和证券特异性影响，非 D 单变量函数）。

**验证结论**：详见 `docs/qfq-rebase-precision-validation-20260729.md`。add_dev ≤ k×D 对
1~20 tick 污染全部漏检，且有"整段加减盲区"（open/close 同步偏移使 add_dev 不变）。
**不作为硬门禁**，仅作观察/审计/告警指标。

**C 方案安全门禁**只依赖确定性条件（见 2.1 节契约 1-12），核心是：
- fresh raw 与库内 raw 逐 bar 一致（第 3 条）
- 写后 front 与 staged fresh 逐 bar 精确一致（第 7 条）
- 全历史完整覆盖 + 守恒（第 2/4/5 条）

移除原 precheck 的理想化乘法/加法假设（第 5 条），由 raw 一致 + 写后一致承担安全责任。
具体替代校验逻辑待后续设计确定（见第 7 节未解决问题）。

### 3.5 幂等性
- 同一 capture_id 重复执行：检查该 capture 是否已 applied（qfq_fresh_capture.status='applied'
  且对应 event committed）→ 跳过重写，返回已 committed 结果。
- anchor 推进逻辑不变（已 committed 的 event 不重复推进）。

### 3.6 写入流程（复用 fresh_staged）
```
1. stage_fresh_authoritative: precheck（A-D）→ 建 staged 临时表
2. apply_fresh_minute_staged: 分钟 raw 对齐 + 覆盖 + UPDATE 四 front（复用）
3. update_daily_front_from_staged: daily UPDATE 四 front（复用）
4. run_postchecks_authoritative: 写后校验（含因子链分段）
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

### 4.3 逐项验证
- 全历史 daily/minute front 与 fresh oracle 一致
- raw、back、行数、非 front 列完全守恒
- 故障注入全部被挡：缺 bar、多 bar、重复 bar、raw 污染、front 单点污染、错误证券、错误频率
- 重复执行幂等
- 故障注入后事务完整回滚
- 代表性策略在"增量重基准库"与"同 fresh 快照干净全量重建库"间：
  信号、选股、订单、成交、持仓、现金、每日净值一致

## 5. 实现范围（分阶段）

### 阶段 R1：模型注册 + precheck（stage_fresh_authoritative）
- MODELS 增加 fresh_authoritative_rebase
- apply_reanchor_for_security 增加 rebase 分支（显式模型选择 + 防呆）
- stage_fresh_authoritative：基本校验 + 完整覆盖 + raw 对齐（删除比例校验）
- 单元测试：raw 对齐 / 覆盖 / K线关系 / 故障注入

### 阶段 R2：postcheck（因子链分段检查）
- run_postchecks 增加 rebase 分支：复权因子链分段 + 分段内收益一致 + 除权日跳变合理
- 替换 scale_consistency / front_chain 的乘法假设
- 单元测试：分段因子平滑 / 除权日跳变 / 无事件区间收益一致

### 阶段 R3：真实数据验收
- staging 副本上对 000012 等真实证券全历史重基准
- 干净副本全量重建对照（黄金基准）
- 策略信号/订单/净值一致性
- 故障注入全套

### 阶段 R4：文档 + 上线门控
- README / strategy_toolbox / prompt_engineering / runbook / fix report 更新
- 汇报代码改动、数据语义、测试证据、风险、回退
- 用户明确确认后才 stage/commit/push/PR
- 真实 staging 全 committed 且守恒通过前，保持 enabled=false

## 6. 不变项（铁律）
- ratio / fresh_staged 模式逐位不变（rebase 是独立新增，不改既有模型行为）。
- apply_reanchor_for_security 既有参数签名不变（model 新增值，其余不变）。
- 四价格表 raw / *_back / volume / amount / 行数 / 主键不受 rebase 影响。
- 失败路径（blocked/rolled_back/failed）绝不推进 anchor，绝不污染已提交数据。
- 不碰 daemon.py 既有逻辑（rebase 由编排器显式选择，编排器改动单独审核）。

## 7. 已知风险
- factor_drift_tol（1e-4）需真实数据校准：若 xtquant 在无除权区间的因子舍入偏差 >1e-4，
  需调整为可证明的上界（基于真实样本的因子漂移分布，非反向调参）。
- 复权因子链分段依赖 ex_dates 准确性：ex_dates 缺漏会导致无事件区间误判（把除权日当
  无事件区间，因子突变被误报）。需交叉验证 ex_dates 与因子 observation 变化点。
- 全历史 fresh 下载耗时：2076 行 daily + 数万行 minute 的 xtquant 下载，需评估性能。
