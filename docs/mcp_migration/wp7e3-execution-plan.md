# WP7-E3 执行计划（项目经理 reasonix / 执行 ZCode）

> 版本：v1（2026-08-08，用户批准"串行执行"工作模式：ZCode 执行，reasonix 审核，
> 每轮用户转发 ZCode 反馈 → reasonix 给出可直接回复的执行安排与意见）
> 目标：完成水位线修复 + 分片化改造包（Raw Landing 复用 + 流式分片），
> 解锁 WP7-E3 水位推进（bootstrap completed → watermark release），全程严守项目铁律。

## 0. 工作模式与铁律（本计划最高约束）

1. **串行顺序**：水位线修复先行 → 分片化改造包后行（用户 2026-08-08 拍板）。
   两任务同改 `daemon.py` 相邻区域 + 调用链耦合（写库→水位推进）+ 验收归因依赖，
   并行会产生高频冲突与归因困难。
2. **框架层修复铁律**：本地修复完成后，**绝不擅自提交/推送 GitHub**；向用户汇报、
   经用户明确同意后才执行同步（含 README + docs 引用文档完整同步）。
3. **性能铁律**：分片化改造必须**语义等价**（修复前后黄金结果逐项一致：API 返回值、
   数据范围、日期语义、复权口径、watermark 行为、回测结果），禁止以性能优化为名
   混入行为变更；验收以修复前后对比为唯一依据。
4. **staging-only**：所有写操作限 staging 副本（`QUANTSTUDIO_DATA_ROOT` 重定向 +
   staging config 目录）；正式库保持安全冻结（schema 2.1, cutover active,
   watermark held, backup 就绪），任何正式库操作需用户单独授权。
5. **进度报告铁律**：每个阶段完成且 reasonix 审核通过后，立即更新
   `实时进度报告.md`（唯一权威档案，附证据：SHA/行数/测试数/耗时）。
6. **MCP 项目铁律**：同进度报告要求；技术债/风险必须记录在案，不得删除。

## 审核点设置（全流程仅 2 个，减少来回）

- **审核点 1**（阶段 1 完成后）：水位线修复代码 + 测试 + staging 验证证据 + stock
  灌数结果，一次性提交 reasonix 审核；审核通过 → 用户确认 → 阶段 2 开始。
- **审核点 2**（阶段 2 完成后）：分片化改造包代码 + 测试 + staging 全量验证 +
  等价性证据 + 客户硬需求验证，一次性提交 reasonix 审核；审核通过 → 用户确认 →
  阶段 3（GitHub 同步 + 工作包 C 授权）。

**ZCode 自主决策边界**（无需请示）：staging 副本内的一切数据操作/重跑；代码实现
细节（函数命名、分片粒度、缓存策略内部实现）；测试用例设计；只要不改变公共 API
契约、不碰正式库、不提交 GitHub、不改变水位/复权/日期语义。

**必须请示**：任何正式库操作；任何公共 API 签名/返回契约变更；任何水位/复权/
日期/门禁语义变更；GitHub 提交；新增依赖；方案性分歧（与任务书不一致时先停下
来汇报，不自行发挥）。

---

## 阶段 1：水位线修复（串行先行）+ stock 分钟线灌数（并行数据操作）

### 1.1 stock 分钟线灌数（staging，选项 1，已授权）

- 改 `config/profiles/mcp_only/collector_tasks.json`：`mcp_stock_minutes.start_date`
  → `"2026-07-01"`（1 个月，内存约束降级，2026-08-08 决策）；
- `QUANTSTUDIO_DATA_ROOT=<staging 目录>` + staging config 跑
  `daemon --mode once --task mcp_stock_minutes`（ETF 任务已成功勿重灌）；
- 验证：`stock_minutes` >0 行、范围 ≈ 2026-07-01 ~ 2026-08-06；
- 此操作与水位线修复代码改动无依赖，可与 1.2 并行推进。

### 1.2 水位线修复（代码）

**已知问题定性**（进度报告 v3.x/v6.7 历史，ZCode 需补充现状确认）：
- etf_daily QFQ 编排器 fail-closed 水位问题：协调周期**卡在 applying 永不
  finalize**（用户已声明）；
- trigger 缺数据源世代隔离（历史 xtquant 编排状态污染 MCP-only 环境）；
- started 周期崩溃恢复缺陷。

**要求**：
1. ZCode 先给出**现状确认**（staging 副本上复现/观察：协调周期当前状态、
   `qfq_cycle_run`/`qfq_watermark_intent` 表内实际数据、卡 applying 的 run 记录）；
2. 修复方案需覆盖：协调周期 finalize 语义（fail-closed 不变）、trigger 世代隔离、
   崩溃恢复（started → interrupted 语义，参考 B-4 已验收的
   `after_commit_before_report`/`started -> interrupted` 模式）；
3. 涉及文件预期：`daemon.py`（`_advance_or_defer_watermark` 及协调周期调用点）、
   `qfq_resident_orchestrator.py`（cycle 状态机）、相关 schema/迁移（如需）；
4. 新增测试：协调周期正常 finalize、崩溃恢复（模拟 started 残留）、世代隔离
   （xtquant 遗留 trigger 不污染 mcp-gen1 周期）；
5. **staging 验证**：修复后协调周期能正常 finalize（applying → finalized 全链路
   跑通）；watermark intent 语义与修复前设计一致（fail-closed 门禁不变）。

**交付物清单（一次性提交审核点 1）**：
- 现状确认证据（staging 查询记录）；
- 修复方案简述（改动点 + 理由，一页内）；
- 代码 diff（本地，不提交）；
- 新增/修改测试 + 测试结果（pytest 输出）；
- staging 验证证据（协调周期 finalize 成功、intent 语义正确）；
- stock 灌数结果（行数/范围/耗时）。

**验收标准（阶段 1）**：
- 协调周期不再卡 applying（staging 实测 full cycle 正常 finalize）；
- 既有测试无回归（13 canary + 相关 qfq/daemon 测试全过）；
- stock_minutes 灌数成功（>0 行，范围正确）；
- 正式库零触碰（前后 SHA 对比）。

---

## 阶段 2：分片化改造包（Raw Landing 复用 + 流式分片处理）

按 `docs/mcp_migration/wp7e3-workpackage-B-task.md` v1.2 执行，核心要求重申：

### 2.1 Raw Landing 复用
- `_export_batches` 固定日历网格切分（epoch 对齐 365/10 天网格，全证券共享批次边界）；
- export 缓存（`export_cache` 开关，**仅 bootstrap 链路开启**，daemon 日常路径
  默认 false 行为不变）；manifest 落盘 `mcp_landing/_export_cache_manifest.json`；
  缓存命中失败自动回退直连；
- 落盘级缓存（不内存驻留）。

### 2.2 流式分片处理（方案 A，客户交付硬需求）
- `fetch_table_streaming(table, start, end, freq, codes) -> (meta, shard_iter)`：
  逐 shard 读回 → 过滤 → 对齐/复权 → 写库 → 释放，**不 concat**；
- 覆盖：`stock_minutes`/`etf_minutes`/`stock_daily`/`etf_daily`/
  `stock_daily_valuation`（aligner 逐行公式 + validator 聚合计数 + watermark
  max 聚合，均无跨行全局依赖）；
- daemon `_run_with_source` 循环化：同一事务内逐片写入；**水位推进调用时机设计**
  （全部片写完后再推进一次，不每片推进——与阶段 1 修复后的语义一致）；
- 单批小表（est_rows < 安全阈值）走原路径，零影响；
- 公共 API 契约不变（`fetch_table` 现有调用方不受影响）。

### 2.3 测试与验证
- `tests/test_mcp_export_cache.py`（缓存命中/未命中/回退/网格一致性/等价性 hash）；
- `tests/test_mcp_streaming.py`（分片迭代、同事务写、与全量路径逐值一致、
  小表走原路径、内存峰值断言）；
- staging 全量 bootstrap（工作包 B 步骤 4）：冒烟 N=2 → 全量；
  `bootstrap_completed()` True、blocked=0、≤4 小时；
- **客户交付硬需求**（验收第 7 条）：首次全量回填内存峰值 ≤1GB（8GB 机器可跑）、
  小时级、断点续传；
- 等价性验收：缓存/流式路径 vs 直连路径同一证券同一窗口输出 hash 逐值一致
  （NaN 位置、index、值；不放宽容差）。

**交付物清单（一次性提交审核点 2）**：
- 代码 diff（本地，不提交）；
- 两个新测试文件 + 全量测试结果；
- 等价性验收证据（hash 对比记录）；
- staging 全量 bootstrap 审计（completed、blocked=0、耗时）；
- 内存峰值实测记录（回填场景 ≤1GB）；
- README 更新稿（首次全量回填说明升级：小时级 + 进度/断点提示）。

**验收标准（阶段 2）**：
- 等价性 hash 逐值一致（铁律硬指标）；
- 门禁通过 + 全量 ≤4 小时 + 内存 ≤1GB；
- daemon 日常路径行为不变（抽查 mcp_etf_daily 增量采集对比）；
- 既有测试无回归。

---

## 阶段 3：收尾（用户确认后）

1. 阶段 1 + 2 代码经用户确认后，按铁律同步 GitHub（代码 + README + docs 引用
   文档完整同步，一次提交或分两次均可，按用户意愿）；
2. 更新 `实时进度报告.md`（阶段 1/2 各自完成时即更新，含证据）；
3. 工作包 C 启动（正式库操作，**需用户单独授权**）：正式库先灌分钟表
   （mcp_stock_minutes/mcp_etf_minutes，扩窗口时机由 T-B1 完成后决定）→
   正式库 bootstrap-plan + bootstrap-run → watermark release（最不可逆，G2 确认
   + 用户单独授权）。

---

## 附：当前已知状态快照（2026-08-08）

- 工作包 A 已同步 GitHub（commit 301090b）；
- ETF 分钟线灌数成功（23,523,925 行，staging）；
- stock 分钟线 1 个月降级待执行（阶段 1.1）；
- 剖析结论：15GB/10158 parquet、日线 ~10 分钟、分钟线 20+ 分钟（瓶颈确认）；
- 正式库安全冻结；进度报告 v6.7.29。
