# 工作包 B 任务书：Raw Landing artifact 复用 + 分钟表灌数 + staging 全量 bootstrap 验证

> 版本：v1.1（2026-08-08，reasonix 起草，用户拍板"分钟表近 3 个月起步"→
> v1.1 更新：stock 分钟线 1 个月降级决策 + 选项 2 分块写入立项）
> 执行方：ZCode（本地实施，不提交 GitHub，等用户确认）
> 关联：WP7-E3 性能阻塞（500+ 小时 → 目标小时级）；工作包 A 已同步（commit 301090b）

## 0. 背景与目标

WP7-E3 水位推进需要 mcp-gen1 世代 completed bootstrap（2181+ 证券）。当前每证券
14-16 分钟（日线+分钟线各一次全表 export + 本地过滤），全量 500+ 小时不可行。
**根因**：`export_dataset` 不支持 codes 过滤（全表导出），2181 个证券重复下载
完全相同的数据。**目标**：export 次数从 2181×2×N 降至 2×N（一次预导出、全证券本地切片），
全量 bootstrap 控制在小时级（≤4 小时）。

**已确认的事实基线（2026-08-08 实测）**：
- 主库（staging + 正式）分钟表 0 行（`stock_minutes`/`etf_minutes`），日线已有
  2018 起数据（stock 957 万行 / etf 208 万行）；
- 云端分钟数据范围：2025-01-01 至 2026-08-06（用户提供）；
- 重锚引擎 `_check_minute_cov_raw`（qfq_reanchor_engine.py:951）partial 降级硬条件
  `target_count > 0` → 分钟表空时 bootstrap 分钟线必然 `minute_coverage_mismatch`
  BLOCK（staging 库实测已有 5 个该 BLOCK 事件）；
- 引擎只 UPDATE 存量分钟行 front 四列、不 INSERT 新行（qfq_reanchor_engine.py:1052-1054）；
- 分钟 fetch 窗口 = 主库分钟表 MIN/MAX（`_security_range`，qfq_resident_orchestrator.py:386-396）；
- daemon MCP 采集已是全量导出模型（daemon.py:530-532 注释），且
  `config/profiles/mcp_only/collector_tasks.json` 已存在 `mcp_stock_minutes` /
  `mcp_etf_minutes` 任务（enabled=true，start_date=2025-01-01）；
- daemon CLI 支持 `--mode once --task <name> --pull-mode full_range|incremental`
  （daemon.py:2308-2311）；
- MCPAdapter 落盘机制：`_landing_path(job_id, shard)` → `DATA_ROOT/mcp_landing/<job_id>/`
  （mcp_adapter.py:319, 715, 831），job_id 每次 export 新建，无跨调用复用。

---

## 步骤 1：单证券成本剖析（≤30 分钟，staging 副本，只读+单次 fetch）

**目的**：用数据确认瓶颈分布（日线 vs 分钟线、网络 vs 本地 vs 写库），校准步骤 3 的
参数；同时验证"500+ 小时"外推的准确性（不同证券窗口长度不同）。

**操作**：
1. 在 staging 副本（`data/quantstudio.20260807T041035.db` 的新拷贝）上，用
   `McpFreshFetcher` 对 000001 分别执行日线、分钟线 `fetch_none_front`，各计时；
2. 记录每侧：`_export_batches` 批次数（打日志即可见）、分片总数、Raw Landing 落盘
   字节数、fetch_table 耗时 vs apply 前耗时；
3. 若分钟线耗时占比 ≥ 80%（预期），确认步骤 3 以分钟线网格为主设计；
4. 产出 `output/mcp_migration/wp7e3_cost_probe_<ts>.json`：
   `{daily_batches, minute_batches, daily_sec, minute_sec, daily_bytes, minute_bytes, codes_per_batch}`。

**验证**：剖析记录落盘，可复现；不写主库（fetch 只落 Raw Landing + qfq_aux.db
因子注入，均发生在 staging 副本对应路径）。

---

## 步骤 2：分钟表灌数（近 3 个月）——daemon 配置修改点

**决策**：用户拍板"近 3 个月起步"（2026-08-08）。bootstrap 分钟线窗口 = 主库分钟表
MIN/MAX，灌多少重锚多少。

**修改点（仅 `config/profiles/mcp_only/collector_tasks.json`，staging 环境）**：

| 任务 | 字段 | 现值 | 改为 | 理由 |
|---|---|---|---|---|
| `mcp_stock_minutes` | `start_date` | `"2025-01-01"` | `"2026-07-01"` | **v1.1 决策：1 个月降级**（2026-08-08 用户确认）。3 个月 stock 分钟线 4400 万行 pandas copy 4GB 内存溢出 → 缩到 1 个月（~25M 行，与 ETF 3 个月成功案例 23.5M 行同量级）；窗口不一致（stock 1 个月 vs ETF 3 个月）不影响引擎（按证券独立校验，fresh ⊇ target 即可） |
| `mcp_etf_minutes` | `start_date` | `"2025-01-01"` | `"2026-05-01"` | 近 3 个月起点（月初对齐）；**已执行成功（23,523,925 行写入 staging）**，勿重灌 |

**其余字段不动**：`enabled=true` 已满足；`mode` 默认 incremental（首次水位线为空时
daemon.py:553 自动回退用 `start_date`，即全量拉 2026-07-01 → 今天）；`freq="1min"`；
`codes=["ALL"]`；`max_workers=4`；`rate_limit.calls_per_min=1`（MCP server 限速，
勿改，改小会触发 429）。

**执行**（staging 副本 + staging 的 config 目录）：
```
QUANTSTUDIO_DATA_ROOT=<output/staging/wp7e3_b_<ts>/> python -m quantstudio.pipeline.daemon \
  --mode once --task mcp_stock_minutes --config-dir <staging-config-dir> [--pull-mode full_range]
```
（daemon DB 路径由 `QUANTSTUDIO_DATA_ROOT` 重定向，`_paths.py:30` 原生支持；配套
staging 配置目录含 `qfq_aux_paths.json` 的 aux 副本，因为 fetch_table 会触发
`_sync_factor_snapshot` 写 aux。ETF 任务已成功，不需要再跑。）

**量级预估（v1.1 更新）**：stock 1 个月 ≈ 5000 证券 × 21 交易日 × 240 分钟 ≈ 25M 行；
10 天/批 → ~3 批全市场 export（每批 ~1200 万行，~1.5-2GB parquet）→ 落盘 ~5GB；
预计 15-30 分钟（含 aligner 复权计算 + 写库）。磁盘 296GB free，充足。

**执行状态（2026-08-08 更新）**：
- ✅ ETF 分钟线灌数（近 3 个月，start_date=2026-05-01）：**成功**，23,523,925 行写入
  staging，时间范围正确；
- ❌ Stock 分钟线灌数（近 3 个月）：**内存溢出**——4400 万行 pandas `DataFrame.copy()`
  尝试分配 4GB 连续内存失败（非网络/磁盘问题，是 pandas 大 DataFrame copy 的内存
  管理不友好；stock 数据量 ≈ ETF 的 2x）；
- ✅ 日线表：完整（etf_daily 2,084,886 / stock_daily 9,576,322，staging 副本）；
- 剖析结论（进程监控）：近 3 个月窗口全表导出 15GB / 10158 parquet；日线 ~10 分钟
  完成下载；分钟线 20+ 分钟未完成（超时 kill）→ 确认 minutes 主导 + export_dataset
  全表导出瓶颈 + Raw Landing 复用必要性（15GB × 2181 ≈ 32TB 重复下载 vs 复用后
  一次性 15GB）。

**验证**（staging 只读查询）：
```sql
SELECT COUNT(*), MIN(time), MAX(time), COUNT(DISTINCT code)
FROM stock_minutes;   -- 期望（选项 1 执行后）：>0 行，范围 ≈ 2026-07-01 ~ 2026-08-06
SELECT COUNT(*) FROM etf_minutes;  -- 已确认 23,523,925 行
```
另查 `source_watermark` 分钟表水位已推进。**正式库不动**。

---

## 步骤 3：Raw Landing artifact 复用实现（核心代码改动）

**文件**：`quantstudio/pipeline/sources/mcp_adapter.py`（其余文件不动，除非必要）。

### 修改点 3.1：`_export_batches` 固定日历网格切分

现状：从每个证券自己的 start 起切 365/10 天窗口（mcp_adapter.py:641-653）→ 不同证券
批次边界错位 → 缓存键 `(table, bs, be)` 命中率 ≈ 0。

改为：窗口边界对齐到固定网格（**epoch 对齐的 365 天网格**，`(days_since_epoch // 365) * 365`
作为每批起点；分钟线同理 10 天网格）。所有证券共享同一组批次边界 → 缓存命中率 100%。
正确性：服务端按 [time_start, time_end) 过滤，客户端再按 ds/de 二次裁剪（fetch_table
内已有日期裁剪逻辑，mcp_adapter.py:744-756）→ 网格扩出的边界数据被裁掉，语义等价。

### 修改点 3.2：export 缓存（bootstrap 专用开关）

- 新增配置开关：`mcp_cfg["export_cache"]`（默认 false）。**仅 bootstrap 链路开启**
  （`McpFreshFetcher._ensure()` 构造 adapter 时注入；daemon 日常采集路径不注入 →
  行为不变，杜绝"daemon 拿到过期数据"风险）。
- 缓存键：`(table, bs, be)`（网格化后的批次边界）。
- 命中路径：读 manifest + Raw Landing parquet 分片 → 本地过滤（跳过
  `export_dataset` 网络调用）。
- 未命中路径：现有 `_fetch_export` 逻辑（export_dataset → 落盘）→ 写 manifest。
- manifest 文件：`mcp_landing/_export_cache_manifest.json`：
  ```json
  {"stock_daily": {"2026-01-01|2026-12-31": {"job_id": "...", "shards": [...], "bytes": 123, "ts": "..."}}}
  ```
- 失效策略：manifest 记录 `ts`，同 key 存在即命中（bootstrap 单次运行生命周期内
  数据不变；跨天重跑 bootstrap 如需刷新，删 manifest 或加 `--no-export-cache`）。
- 并发：bootstrap-run 当前单进程串行（DuckDB 单写者），无需文件锁；若后续并行化，
  需加 `fcntl`/`msvcrt` 锁（本工作包不引入并行）。
- 落盘级缓存：命中时逐分片 `pd.read_parquet` → 过滤出目标证券 → 释放（不驻留内存），
  内存峰值与现状一致（分钟表内存溢出问题不复发）。

### 修改点 3.3：测试

新增 `tests/test_mcp_export_cache.py`：
- 缓存命中：同一 (table, bs, be) 二次 fetch → 第二次不调 export_dataset（mock client）；
- 未命中 → 落盘 + manifest 写入；
- manifest 损坏/缺失 → 回退到未命中路径（不抛错）；
- 网格化批次：不同 start 的证券 → 批次边界一致；
- 语义等价：缓存路径 vs 直连路径对同一证券同一窗口输出 `fetch_none_front` 结果
  hash 一致（NaN 位置、index、值逐项）。

**语义等价性论证**（铁律要求）：export 结果与 codes 无关（全表导出），同一
(table, 网格窗口) 的 artifact 内容相同；缓存路径只是"跳过重复网络请求直接读本地
parquet"，后续 codes 过滤 / 日期裁剪 / qfq 还原 / 复权计算代码路径完全不变 →
每个证券拿到的数据与直连路径逐字节一致。

---

## 步骤 4：staging 全量 bootstrap 验证

前置：步骤 2 分钟表已灌（>0 行）、步骤 3 代码已合入本地。

1. 复制 staging 副本（步骤 2 之后的新快照）→ `output/staging/wp7e3_b_<ts>/`；
2. `python -m quantstudio.pipeline.qfq_orchestrator_cli bootstrap-plan --db <staging> \
   --admissible --config-dir <staging-config>`（消费准入名单，excluded 不阻塞门禁）；
3. `bootstrap-run --run-id <id> --db <staging> --max-batches N`（先 N=2 小批量冒烟：
   确认分钟线不再 BLOCK、日线正常、事件 committed）；
4. 冒烟通过 → 全量跑完（目标 ≤ 4 小时）；
5. `bootstrap-audit --run-id <id>`：status=completed、blocked=0、failed=0；
6. 抽查：`qfq_reanchor_event` committed 事件中
   `minute_front_coverage='partial'|'full'` 分布、日线 front 值对账
   （对比 xtquant 世代同证券同日前复权价，允许锚差异 ~1.6% 内，README 已知限制）。

---

## 步骤 5：验收标准（性能铁律：黄金结果逐项一致）

1. **语义等价**：步骤 3.3 的等价性测试全过（缓存路径 vs 直连路径逐值一致，
   不允许放宽容差）；
2. **门禁**：`bootstrap_completed()` 返回 True（存在 completed run + 版本校验 +
   证券级状态机全清）；
3. **性能**：全量 bootstrap（2181+ 证券）≤ 4 小时（目标 2-3 小时）；单证券
   fetch（缓存命中）≤ 10 秒；
4. **磁盘**：预导出落盘（3 个月分钟 ~15GB + 日线 ~5GB）≤ 30GB；
5. **无回归**：既有测试全过（13 canary + qfq 相关 pytest 套件）；
6. **日常路径不受影响**：daemon 采集（export_cache=false）行为与修复前一致
   （可抽查一次 mcp_etf_daily 增量采集结果对比）。

---

## 禁止事项

- ❌ 不碰正式库（所有操作在 staging 副本；正式库保持安全冻结）；
- ❌ 不改 `fetch_table` / `fetch_none_front` 公共签名、返回契约、日期语义；
- ❌ 不绕过 `bootstrap_completed()` 门禁、不改 blocked 语义；
- ❌ 不改 daemon 日常采集路径（export_cache 默认 false 是硬边界）；
- ❌ 不在本工作包引入并行/多进程（DuckDB 单写者，另行立项）；
- ❌ 不提交/推送 GitHub（等用户确认）；
- ❌ 不以"性能优化"名义混入正确性修复/API 重构（如确需，单独拆分审核）。

---

## 风险与回退

| 风险 | 影响 | 缓解/回退 |
|---|---|---|
| 网格化后 export 窗口略扩 | 下载量 +~5% | 正确性不变（客户端二次裁剪）；接受 |
| MCP server 分钟导出限速（calls_per_min=1） | 预导出 ~7 批可能 30-60 分钟 | 接受（仍是小时级）；必要时与 MCP 运维协调放宽 |
| aligner 写库慢（7200 万行 INSERT） | 灌数超 1 小时 | 接受；写入分批，监控 batch_audit |
| 分钟表灌数后 bootstrap 仍 BLOCK | 窗口/覆盖不匹配 | 查 `minute_coverage_mismatch` 明细；确认 start_date 与主库范围一致 |
| 缓存 manifest 损坏 | 回退直连 | 命中失败自动回退未命中路径（3.3 测试覆盖） |
| 磁盘占满（预导出 20GB+） | 采集失败 | 跑完清理 `mcp_landing`（bootstrap 完成后可删） |

---

## 技术债（v1.1 立项，2026-08-08）

**T-B1：stock 分钟线大窗口分块写入**（选项 2）
- 现象：`mcp_stock_minutes` 近 3 个月窗口（4400 万行）pandas `DataFrame.copy()`
  4GB 连续内存分配失败 → 当前以"1 个月窗口"临时规避；
- 根治：把采集写入拆成按 code / 按分片的小块处理（内存峰值从 ~7GB 降到 ~1GB）；
- 落地时机：**与步骤 3（Raw Landing 复用）一起实施**——预导出的分片机制（逐
  shard 读回 + 过滤）本身就是分块骨架，灌数分块复用同一批 parquet 分片，避免两套
  分块逻辑；
- 完成后：staging/正式库扩窗口重灌（重锚是增量的，扩窗口随时可补；近 3 个月 →
  云端全量 19 个月）。

**注意**："近 3 个月 → 1 个月"是**内存约束导致的临时降级**（非业务决策）；staging
上 1 个月先跑通链路（工作包 B 验收），窗口扩充由 T-B1 完成后执行。

---

## v1.2 更新（2026-08-08）：方案 A 决策——T-B1 升级为"分片化改造包"（客户交付硬需求）

**背景**：stock 分钟线 3 个月窗口内存溢出（4400 万行 pandas copy 4GB 失败）暴露
**架构性全量驻留**问题——客户首次全量回填云端 19 个月分钟线（≈4.6 亿行）需 40GB+
连续内存，任何单机扛不住。这不是个例，是必须解决的交付前提。
（日常增量采集无此问题：每天 ~120 万行 ≈ 100MB。）

**用户决策（2026-08-08）**：选 **方案 A（流式分片处理）**，且要求**覆盖所有数据拉取**
（不仅分钟数据）。

### 方案 A 设计要点

1. **流式路径**：`fetch_table` 新增流式入口
   `fetch_table_streaming(table, start, end, freq, codes) -> (meta, shard_iter)`——
   逐 shard 读回 parquet → 过滤 → 对齐/复权 → 写库 → 释放，**不 concat**；
2. **效率保证**：网络下载（真正瓶颈）不变；所有分片写入在**同一事务**内（DuckDB
   批量写与一次性等价）；单批小表（est_rows < 安全阈值）走原路径零影响；
3. **内存收益**：4.6 亿行（40GB+）→ 单分片 ~200 万行 ≈ **200-300MB**；
4. **公共 API 契约不变**：`fetch_table` 现有调用方不受影响，流式是增量路径；
5. **覆盖范围**（已核对立即可分片）：`stock_minutes`/`etf_minutes`/
   `stock_daily`/`etf_daily`/`stock_daily_valuation`（aligner 复权是逐行公式、
   validator 是聚合计数、watermark 是 max(time) 聚合，均无跨行全局依赖）；
   单批快照/财务表与 passthrough 表（量小）不走流式；
6. **daemon 改造**：`_run_with_source` 的"拉取→对齐→校验→写入"循环化（同一事务），
   reject_rate 用分片累计计数、watermark 取分片 max 聚合；
7. **与 Raw Landing 复用共用分片骨架**：步骤 3 的预导出分片机制即流式的数据源，
   一套改造同时解决性能（重复下载 32TB→15GB）与内存（40GB→300MB）。

### 客户交付硬需求（升级为验收标准第 7 条）

- 首次全量回填（日线+分钟线+估值）内存峰值 ≤ 1GB（8GB 客户机器可跑）；
- 首次全量回填小时级完成（分钟线下载 1-2 小时 + 分片写库）；
- 支持断点续传（增量模式水位线天然支持；建议 GUI 采集界面提示进度）。
- README「首次全量拉取耗时较长请耐心等待」表述需升级（分钟线全量回填是 1-2 小时
  级别 + 进度/断点提示）。

### 实施调整

- T-B1 吸收进步骤 3（不再是独立技术债）：步骤 3 更名为
  **"分片化改造包：Raw Landing 复用 + 流式分片处理"**；
- 1 个月窗口降级保留（staging 先跑通链路），T-B1 完成后扩窗口；
- 新增测试：`tests/test_mcp_streaming.py`（分片迭代、同事务写、与全量路径结果
  逐值一致、小表走原路径）。

---

## 交付物

1. `output/mcp_migration/wp7e3_cost_probe_<ts>.json`（步骤 1 剖析数据）；
2. `config/profiles/mcp_only/collector_tasks.json` 修改 diff（start_date × 2）；
3. `mcp_adapter.py` 网格化 + export 缓存 diff（export_cache 开关）；
4. `tests/test_mcp_export_cache.py`（新增测试全过）；
5. staging 全量 bootstrap 审计结果（status=completed、blocked=0、耗时）；
6. 等价性验收证据（缓存 vs 直连 hash 对比记录）；
7. 完成报告（含性能前后对比：500+ 小时 → 实际耗时）。

## 完成后（工作包 C 前置已解锁）

- 用户确认 + 铁律流程：代码同步 GitHub（含 README/docs 同步）；
- 工作包 C：正式库 bootstrap-plan + bootstrap-run（分钟线窗口 = 正式库分钟表范围，
  需先在正式库跑 mcp_stock_minutes/mcp_etf_minutes 灌数）；
- 更新实时进度报告（v6.7.28+）。
