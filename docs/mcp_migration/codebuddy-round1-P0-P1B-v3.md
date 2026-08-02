# QuantStudio 第一轮任务（v3 刷新版）：P0 双库基线冻结 + P1B MCP 契约草案

> **版本**：v3（2026-08-01），基于当前真实进度刷新，取代 v2（codebuddy-round1-P0-P1A.md）
> **执行智能体**：CodeBuddy（本地 QuantStudio）
> **审核**：ZCode（监督，审核通过后更新进度报告）
> **本轮范围**：**P0 双库基线冻结 + P1B MCP 契约草案**
> **明确不做**：P1A（Source Registry，延后）、P2/P3（客户端管线，待MCP server上云后）、任何 stage/commit/push/PR

---

## 0. 当前真实进度（必读，任务以此为基准）

### 架构已演变为自建数据底座（与原方案 v1.1 不同）
```
系统A（已完成）：trading-battle-back\data → 本地QuestDB → sync_to_cloud同步管线 → 云端QuestDB
系统B（待开发）：云端QuestDB(124.223.159.234) → MCP server → Nginx:443/SSL → 客户
系统C（本轮所在）：QuantStudio（客户产品，DuckDB存储）→ 通过MCP拉取数据
```

### 已完成（本任务的前置）
- ✅ 云端QuestDB上云：89GB/109表，全量校验0差异，服务化运行
- ✅ 本地→云端同步管线：feat/sync-to-cloud 分支，双功能(增量+全量)，SQL白名单，0 alert运行
- ✅ 云端账号：mcp_reader只读（127.0.0.1），8812源IP锁110.53.17.130
- ✅ **C0合规已解除**：用户确认数据可合法转供客户（2026-08-01）
- ✅ 域名+SSL：quantstudio.online → 124.223.159.234，证书已下载

### G0 结论：直接 GO（无需探测）
原方案 P0B 要求"探测第三方上游可行性"。但本路线上游是**用户自建的云端QuestDB**，数据已实测完整（stock_daily 1422万行/stock_minutes 4.78亿行/财务三表齐全），C0合规已解除。**不存在第三方可行性问题，G0 = GO。**

---

## 1. 绝对禁止事项（违反任意一条立即停止并汇报）

1. **禁止 stage/commit/push/PR**。完成后按 §6 模板汇报，等待用户**修复后明确确认**。
2. **禁止修改任何生产代码行为**：本轮是"冻结现状+起草契约"，不改 daemon/Adapter/基类/价格authority守卫（daemon.py:426-446）。
3. **P0 对云端QuestDB的操作必须只读**（经本地HTTP 9000或云端8812），禁止任何写操作。
4. **禁止为通过测试放宽容差**；禁止顺便修复既有缺陷（只记录）。
5. **禁止实现MCP server本体**（那是workbuddy后续工作）；本轮只起草契约。
6. **禁止假设数据来源合规**（C0虽解除，但代码/文档不写"合规已验证"，合规由用户侧落实）。

---

## 2. 任务流程（阶段A→B→C，一口气做完，最后汇报）

### 阶段A：P0 双库基线冻结（只读）

#### A1. QuantStudio现有DuckDB库基线冻结
QuantStudio用DuckDB存储（config/data_config.json type=duckdb）。冻结现有库作为"迁移前权威基线"：
- 在 `output/mcp_migration/P0_baseline/` 生成：
  - `table_inventory.csv`：各canonical表行数、最早/最晚业务时间（按主键时间字段）
  - `watermark_inventory.csv`：水位表全部内容
  - `source_inventory.csv`：各表来源分布
  - `price_authority_evidence.md`：daemon.py:426-446 的xtquant锁定原文+行号（**只读记录，标注未改动**）
  - `strategy_golden/`：≥2个代表性策略黄金结果（净值序列SHA256指纹）
- 查询必须只读SELECT。若需探测脚本，写 `scripts/p0_baseline_inventory.py`（标注"未纳入提交"）。
- 连接失败或某表不存在，如实记录，不伪造。

#### A2. 云端QuestDB库基线冻结（已有实测数据，补充固化）
云端库（124.223.159.234）经同步管线持续更新。冻结当前状态作为"MCP server数据底座基线"：
- 在 `output/mcp_migration/P0_baseline/questdb_baseline.json` 记录：
  - 109张核心表的schema（列名+类型）、行数、时间列min/max、distinct code数
  - 核心表schema已在§0确认（stock_daily: ts_code/trade_date/open.../adj_factor/is_qfq 等）
- 连云端用HTTP 9000（只读）或PG 8812（mcp_reader只读账号）。
- 补充：分钟覆盖分布统计（方案§2.4/D2）：stock_minutes/etf_minutes的每证券最早日期分布、每日证券数分布。

#### A3. 生成 mcp_dataset_requirements.json（方案§4）
在 `config/mcp_dataset_requirements.json` 生成覆盖矩阵，每个数据域含：
canonical_table、minimum_requirement、priority、current_status、questdb_source_table（QuestDB源表映射）。

### 阶段B：P1B MCP契约草案（起草规格，不写server代码）

> **重要**：本轮只产出契约文档，不实现MCP server（workbuddy后续C4）。契约是server与client的共同规格，必须精确到可被双方独立实现。

#### B1. MCP工具集定义（方案§5.2）
在 `docs/mcp_migration/mcp_contract_v1_draft.md` 定义server要实现的工具。最小工具集：

| 工具 | 用途 | QuestDB后端映射 |
|---|---|---|
| describe_source | 声明server能力 | 静态能力文档 |
| describe_dataset | 单dataset的schema/覆盖/历史起点 | table_columns + min/max(trade_date) |
| health_check | 连通性 | SELECT 1 |
| list_universe | 证券列表 | DISTINCT ts_code FROM stock_basic |
| get_remote_watermark | 远端水位 | max(trade_date) |
| create_export_job | 大批量导出任务（分钟数据） | 触发查询+Parquet导出 |
| get_export_status | 任务状态 | job状态机 |
| list_export_parts | 分页工件列表 | 分片元数据 |
| fetch_page | 小批量分页拉取 | LIMIT offset,count |
| get_artifact | 取Parquet工件 | 文件下载+SHA256 |
| cancel_export_job | 取消任务 | job状态置cancelled |

#### B2. 数据集契约（每个QuestDB表→一个MCP dataset）
为每个核心表定义 dataset_id / canonical_table / supported_freqs / history_start / column_map（QuestDB列→QuantStudio canonical列）/ code_format / adjustment_state / upstream_providers。关键决策：
- **复权口径**：server返回 **raw + adj_factor**（不复权），复权在客户端本地算（方案§10.1）。adjustment_state: "raw"，附adj_factor列。
- **代码格式**：QuestDB用 `600519.SH`（带后缀），QuantStudio canonical格式以A2冻结的现有库格式为准，契约明确转换。
- **血缘**：upstream_providers 填 `questdb_cloud`（云端自建）。

#### B3. 大批量Manifest规范（方案§5.3）
定义分钟数据（4.78亿行）的分页/工件Manifest格式：job_id、part_no/part_count、SHA256、row_count、size_bytes、schema_fingerprint、lineage。Parquet工件格式，**禁止Pickle/可执行压缩包/远端脚本/无schema CSV**（方案§5.4）。

#### B4. 安全与治理（方案§18.2）
契约含：客户key鉴权、限流、endpoint主机白名单、TLS（Nginx:443）、最大响应/工件字节、SHA-256校验、禁止路径穿越、Landing锚定DATA_ROOT、全链request_id/job_id审计。

#### B5. 职责边界（方案§8铁律）
**server只负责查询返回原始数据+metadata+血缘，不做复权、不做PIT补算、不直接写客户库。** 复权/PIT/写库都在客户机器的QuantStudio L3管线（P2/P3）。这是防止server越权的关键边界。

### 阶段C：文档评估

评估这些文档是否需因本轮改动：README.md、docs/data-pipeline-contract.md、docs/data-quality-checks.md、docs/strategy_toolbox.md、docs/prompt_engineering.md。本轮是冻结+草案，理想情况对外文档无需改；若某文档明确描述数据源架构，则在汇报指出。

---

## 3. 验收标准（全部满足才算完成）

1. P0A（DuckDB库冻结）全部产物生成在 output/mcp_migration/P0_baseline/，无伪造数据。
2. questdb_baseline.json 覆盖109核心表的schema/行数/深度/样本（只读结果）。
3. mcp_dataset_requirements.json 生成，每个canonical_table标注QuestDB源表映射。
4. mcp_contract_v1_draft.md 含B1工具集、B2数据集契约、B3 Manifest、B4安全、B5职责边界。
5. 未修改任何生产代码行为（daemon/Adapter/基类/价格authority零改动；QuestDB只读）。
6. 未创建MCP server代码（仅契约草案）。
7. 未执行任何 stage/commit/push/PR。

---

## 4. 不要做的事

- 不实现MCP server本体（workbuddy后续C4）
- 不实现MCP Client/MCPSourceAdapter/Raw Landing（P2/P3，待server上云后）
- 不做P1A（Source Registry重构，本轮延后）
- 不改alignment_rules.json schema
- 不顺便修复既有缺陷（只记录）
- 不对QuestDB执行任何写操作
- 不声称合规已验证（C0由用户侧落实）

---

## 5. 完成后汇报模板

```markdown
## 阶段
P0（DuckDB + QuestDB 双库基线冻结）+ P1B（MCP server契约草案）

## 本地代码/文档变更
- 新增文档：questdb_baseline.json、mcp_dataset_requirements.json、mcp_contract_v1_draft.md
- 新增探测脚本：scripts/p0_baseline_inventory.py（未纳入提交）
- 生产代码改动：无

## P0产物清单（output/mcp_migration/P0_baseline/）
- 列出所有生成文件 + 关键摘要

## P1B契约草案要点
- 工具集：N个工具
- 数据集映射：M个QuestDB表→MCP dataset
- 复权口径：raw+adj_factor（理由）
- 职责边界：server只返回原始数据

## 测试证据
- 现有测试未破坏（命令+结果）

## 待用户确认的Git同步范围（未执行）
- 文档/配置清单

## 等待确认
本地工作完成，未stage/commit/push/PR，请确认。
```

---

## 附录：QuestDB核心schema（契约依据，已实测）

```
stock_daily(13): ts_code SYMBOL, trade_date TIMESTAMP, open/high/low/close DOUBLE,
  pre_close, change, pct_chg, vol, amount, adj_factor DOUBLE, is_qfq BOOLEAN
stock_minutes(11): ts_code SYMBOL, trade_time TIMESTAMP, freq SYMBOL,
  open/high/low/close/vol/amount/adj_factor DOUBLE, is_qfq BOOLEAN
etf_daily(13): ts_code, trade_date, pre_close, open/high/low/close, change, pct_chg,
  vol, amount, adj_factor, is_qfq
etf_minutes(11): 同stock_minutes
stock_basic(17): ts_code, symbol, name, area, industry, fullname, enname, cnspell,
  market, exchange, list_status, list_date, delist_date, is_hs, curr_type, act_name, act_ent_type
stock_balancesheet(158): ts_code, ann_date, f_ann_date, end_date, report_type, ...
stock_income(94), stock_cashflow(97), stock_fina_indicator(107): 财务三表+指标
index_daily(11): ts_code, trade_date, open/high/low/close, pre_close, change, pct_chg, vol, amount
index_weight(4): index_code, ts_code, trade_date, weight
trade_cal(4): exchange, cal_date, is_open, pretrade_date
stock_daily_basic(18): ts_code, trade_date, close, turnover_rate, pe, pb, total_mv, circ_mv...
sw_daily(15), sw_weight(4): 申万
```

云端连接（只读）：HTTP http://124.223.159.234:9000（需鉴权admin）或PG 124.223.159.234:8812（mcp_reader只读，密码从用户获取）。**优先用本地 http://127.0.0.1:9000 做schema探测（与云端同构，无需鉴权），行数/深度用云端实测数据（已在进度报告记录）。**
