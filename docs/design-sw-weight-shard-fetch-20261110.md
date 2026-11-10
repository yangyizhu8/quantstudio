# sw_weight 2,000,000 行截断·分片拉取方案（六步①方案件）

- **起草**：主仓 dev 会话（节点⑤）｜**日期**：2026-11-10 15:28
- **上游口径**：用户 2026-11-10 15:20 裁「sw_weight 方案件方向修正：⑪ 已证 sw_weight 为本地计算表（无源端、当前逐位完整）⇒ 主库 2M 截断候选＝MCP export 行数上限（与分钟 8,000 行/请求同族）——方案件按『分片拉取』方向查」
- **知识反哺**：`知识反哺：[无命中] knowledge/db + knowledge/contracts 检索「sw_weight / export 上限 / 分片」，2026-11-10`（无同族条目）

## 一、⚠️ 先指出一处与⑪ 表述的冲突（铁律「冲突先指出」）

**⑪ 表述**：sw_weight 为**本地计算表（无源端）**、当前逐位完整。

**我方实态三证**（本件据此立论，若与⑪ 结论冲突请裁定）：

| # | 证据 | 内容 |
|---|---|---|
| 证一 | `config/profiles/mcp_only/collector_tasks.json` | **`mcp_sw_weight_pt`**（`source=**mcp**` / `passthrough=true` / `enabled=true`）<br>`_note: 类别B passthrough（codex审计：原 industry_membership 错配，sw_weight L1指数权重快照改本名直通。注意：仅31个L1指数，2024-01起）` |
| 证二 | `data/logs/daemon.log`（今日 06:42-06:43） | `06:42:47 START task=mcp_sw_weight_pt source=**mcp** table=sw_weight/daily`<br>`06:42:47 PASSTHROUGH mcp_sw_weight_pt: sw_weight 2024-01-01→2026-11-10 (source=mcp)`<br>`06:43:18 sw_weight passthrough 全量覆盖 **2000000 行**`<br>`06:43:18 ✅ passthrough **raw=2000000 written=2000000** (CREATE OR REPLACE, 不推进水位)` |
| 证三 | 任务清单内检索 | **不存在**任何非 `mcp` 源、指向 `sw_weight` 的本地计算任务 |

⇒ **结论：sw_weight 是 MCP passthrough 直写表（有源端），不是本地计算表**；「2,000,000 行」= **MCP 服务端 row_limit 天花板**所致（见 §二），非"本地计算量到此为止"。

## 二、根因（一证锁定）

**`quantstudio/pipeline/sources/mcp_adapter.py:184`** 注释原文：

> `# 只能拉到 ~5% 数据。export_dataset 无 1 万限制（**只有 200 万 row_limit**，这些表`

⇒ **MCP `export_dataset` 服务端硬上限 = `row_limit=2,000,000`**；sw_weight 全量请求（2024-01-01→2026-11-10）返回 **恰好 2,000,000 行** ⇒ 服务端按**时间升序**截断到最老 2M 行 ⇒ 库内 `max(trade_date)=2025-03-31`、**2026 年 0 行**（缺口约 1.6 年）。

**与分钟表同族**：分钟表是「**8,000 行/码**」上限，sw_weight 是「**2,000,000 行/请求**」上限——**同为服务端行数上限**，故对策同为**分片**。

**佐证（我方实测）**：`sw_weight` rows=**2,000,000**（整数·非自然数据量）｜min=2024-01-02｜max=**2025-03-31**｜distinct_index=31｜2024 年 1,622,627 行 / 2025 年 377,373 行 / **2026 年 0 行** ⇒ 1,622,627 + 377,373 = **恰好 2,000,000** ⇒ **截断点精确落在 2M**。

## 三、修法（分片拉取·三处改动）

### 3.1 核心设计：参数化时间窗分片 + 累积写入

| 项 | 内容 |
|---|---|
| **分片维度** | **按 `time_start/time_end` 分片**（MCP 已支持 `WHERE` 过滤导出，见 `mcp_client.py:1027-1046`：`row_limit` 可传、`time_start/time_end` 可窄化） |
| **分片粒度** | 按**季度**（2024Q1…2026Q4 ≈ 12 片）。单季 31 指数 × ~60 交易日 × ~最大成分数 ⇒ 远小于 2M（2024 全年 1.62M ⇒ 单季约 0.4M） |
| **切片顺序** | **由新到旧**（先 2026Q4 保证当日可消费，再回补历史）——保证"当日可用"优先 |
| **写入语义** | ⚠ **passthrough 现为 `CREATE OR REPLACE`（整表替换）⇒ 分片会互相覆盖** ⇒ 必须改为 **分片累积**：首片可 `CREATE OR REPLACE`，后续片用 `INSERT OR REPLACE`（按 `index_code + ts_code + trade_date` 主键去重） |
| **失败原子性** | 分片间**非原子**（任一片失败不回滚已成功片）⇒ 需**片级幂等**（同片重跑覆盖同键）+ 片级成功日志 |

### 3.2 落点清单

| # | 落点（实施时再 grep 复核） | 改动 |
|---|---|---|
| P0 | `config/profiles/mcp_only/collector_tasks.json` 的 `mcp_sw_weight_pt` | 新增 `shard_by: "quarter"` + `shard_range: {start: "2024-01-01", end: null}`（null=至今）+ `write_mode: "accumulate"` |
| P1 | `daemon.py` passthrough 分支（`PASSTHROUGH` 执行处，实施时定位） | 识别 `shard_by` ⇒ **循环分片调用** `export_dataset(time_start=片起, time_end=片止, row_limit=None)`；首片 `CREATE OR REPLACE`、后续片 `INSERT OR REPLACE` |
| P2 | 同上 | **片级日志**：`[shard 3/12] 2024Q3 rows=NNN ok` + 全片汇总；任一片失败 ⇒ 记 ERROR 但**继续其余片**（可回补），末了汇总失败片清单 |
| P3 | 同上 | **截断自检**：每片返回行数 `>= 2,000,000` ⇒ **WARNING「本片撞服务端 row_limit，需再细分」**（防静默截断复发） |

### 3.3 不改的部分

- **不改** `sw_weight` 表结构/主键/列序（`index_code / ts_code / trade_date / weight`）
- **不改** 其它 passthrough 表的写语义（仅当任务带 `shard_by` 时走新分支 ⇒ 纯增益）
- **不改** 水位语义（passthrough 本就不推进水位）

## 四、判型（铁律 18）

| 项 | 判型 | 理由 |
|---|---|---|
| P0-P2 分片拉取 | **数据修正型** | 修复后 `sw_weight` 将**新增 2025-04~2026-11 约 1.6 年数据**（可观察结果变化）⇒ 须显式声明影响面 |
| **影响面声明** | 仅 `sw_weight` 表新增行（约 1.6 年 × 31 指数 × ~N 成分）；**不影响**其它任何表/契约/schema | — |
| 准确性 | **只升不降**：现有 2,000,000 行保持（INSERT OR REPLACE 覆盖同键），新增补全 2026 段 | — |
| 验收失败判据 | 若新增行中出现**与源端不符**或**破坏既有 2M 行** ⇒ 验收失败立即回退 | — |
| P3 截断自检 | **新增检测型**（单列） | 会让"静默截断"开始报警 |

## 五、验收标准

| # | 判据 |
|---|---|
| V1 | 修复后 `SELECT max(trade_date), COUNT(*) FROM sw_weight` ⇒ **max ≥ 当日**（或最近交易日）且 **rows > 2,000,000** |
| V2 | `2026 年` 行数 **> 0**（当前为 0） |
| V3 | 全年分片汇总日志齐全：`[shard N/12]` 全部出现且 `rows` 之和 = 库内总行数（无静默丢失） |
| V4 | **无片撞 2M 上限**（或撞了有 WARNING） |
| V5 | 既有 2024-01~2025-03 段**逐位不变**（`INSERT OR REPLACE` 同键覆盖 = 值相同） |

## 六、回退条件与手段

| 触发 | 手段 |
|---|---|
| 分片写入破坏既有 2M 行 | 从 `INSERT OR REPLACE` 回退为单次 `CREATE OR REPLACE`（任务配置删 `shard_by` 即恢复原行为） |
| 片级失败率过高 | 缩小分片（季度→月），env `QS_SW_WEIGHT_SHARD=month` |
| 运行时长过长 | 分片并发上限 env `QS_SW_WEIGHT_SHARD_CONCURRENCY`（默认 1=串行，保守） |

**共性**：全部新增行为由**任务配置 `shard_by`** 驱动 ⇒ 删配置即回原位 ⇒ **回退后不劣于现状**。

## 七、待②审确认

1. **⑪「本地计算表」表述 vs 我方「MCP passthrough 有源端」实态冲突**——请裁定以哪方为准（我方有三证）
2. 分片粒度 **季度** 是否合适（或月）
3. 切片顺序 **由新到旧**（当日优先）是否采纳
4. `INSERT OR REPLACE` 累积是否需要**先校验源端数据完整性**（防源端分片间不一致）

## 八、实施窗口

- 排在 **A4+M1 明晚同窗之后**（不同文件但同 daemon 停窗可合并；本件改 `collector_tasks.json` + passthrough 分支，A4/M1 改 A4 子段 + tick 调度 ⇒ 冲突面小）
- 实施前 **re-grep 行号** + 先跑**单季度分片试拉**验证上限与耗时
