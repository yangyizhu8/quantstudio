# QuantStudio MCP 数据集服务契约 v1（草案 / DRAFT）

> 阶段：Round 1 — P1B（契约草案，非实现）
> 基线 commit：`da2dace`
> 关联：P0 基线见 `output/mcp_migration/P0_baseline/`；覆盖矩阵见 `config/mcp_dataset_requirements.json`
> 状态：**DRAFT** — 待 ZCode 审核。本轮仅起草，禁止实现 MCP server 本体（server 实现属 workbuddy 后续 C4）。

---

## 0. 设计原则（不可违背）

1. **server 只返回原始数据**：复权、PIT 校验、写库全部在 MCP 客户端（QuantStudio 侧）完成。
2. **价格权威不可动摇**：xtquant 为价格唯一权威源（见 `price_authority_evidence.md`）。MCP 返回的数据须可追溯到 xtquant 口径，复权必须以 `raw × adj_factor` 重算，禁止信任云端预计算前端复权列（如 `open_back/close_back`）。
3. **只读服务**：MCP server 对 QuestDB 全程只读，不执行任何 INSERT/UPDATE/DELETE。
4. **code 规范化**：QuestDB 为 `SYMBOL` 带后缀（`600063.SH`），QuantStudio DuckDB 为裸数字（`600063`）。转换在客户端。

---

## B1. MCP 工具集（11 个）

所有工具统一签名风格：`tool_name(arguments: dict, ctx: AuthContext) -> MCPResult`。
返回结构统一：`{ "datasets": [...], "manifest_ref": str|null, "next_cursor": str|null, "sha256": str }`。

| # | 工具 | 用途 | 关键入参 |
|---|------|------|----------|
| 1 | `describe_server` | 返回 server 能力、数据集清单、版本、限流档位 | — |
| 2 | `describe_dataset` | 返回某数据集 schema（列名/类型/designated timestamp/复权支持） | `dataset_id` |
| 3 | `fetch_page` | 按游标分页拉取数据集一页（默认 50k 行，分钟数据强制分片） | `dataset_id, cursor, page_size` |
| 4 | `get_artifact` | 拉取已生成的大批量 Parquet 工件（Manifest 引用） | `artifact_id` |
| 5 | `query_snapshot` | 按谓词（ts_code/trade_date 范围）拉取快照（小结果集） | `dataset_id, filters, limit` |
| 6 | `get_schema` | 返回单表 DDL（与 P0 基线冻结的 schema 比对用） | `table_name` |
| 7 | `list_datasets` | 列出当前 key 有权限的数据集 ID 列表 | — |
| 8 | `get_coverage` | 返回数据集覆盖矩阵（Q1–Q7 可用状态 + gap 标注） | — |
| 9 | `validate_access` | 校验当前 key 对某数据集/表的读权限，返回 scope | `dataset_id?` |
| 10 | `get_manifest` | 返回大批量作业 Manifest（分片清单 + SHA256 + 行数） | `job_id` |
| 11 | `ping_health` | 存活/延迟探测（不含数据） | — |

> 注：`fetch_page` 与 `get_artifact` 二选一为主路径；分钟数据（4.78 亿行）强制走 `get_artifact` + Manifest。

---

## B2. 数据集契约（QuestDB 表 → MCP dataset）

### B2.1 映射规则
- 每个 QuestDB 表对应一个 `dataset_id`，命名 `qdb.<table_name>`（如 `qdb.stock_daily`）。
- 客户端请求时声明 `adj_policy: raw | adjusted`。
  - `raw`：返回 `open/high/low/close` 原始值 + `adj_factor` 列。
  - `adjusted`：server **不**预计算，仅返回 raw+adj_factor，由客户端乘算（职责边界 B5）。

### B2.2 复权口径（强制）
```
adjusted_price = raw_price * adj_factor        # 后复权/前复权由 adj_factor 定义决定
is_qfq BOOLEAN  -- true 表示已是前复权基准序列，仍须以 adj_factor 重算保证与 xtquant 一致
```
- **禁止** server 返回 `open_back/close_back` 等前端预计算复权列作为权威。
- DuckDB 侧 `stock_daily` 已含 `open_back/close_back`；MCP 客户端接入云端时必须以 `raw×adj_factor` 覆盖，不得直接用云端预计算值。

### B2.3 核心数据集清单（节选）
| dataset_id | 源表 | 行数 | designated ts | 复权 |
|------------|------|------|---------------|------|
| qdb.stock_daily | stock_daily | 14,233,566 | trade_date | adj_factor |
| qdb.stock_minutes | stock_minutes | 478,535,169 | trade_time | adj_factor |
| qdb.etf_daily | etf_daily | 2,417,403 | trade_date | adj_factor |
| qdb.etf_minutes | etf_minutes | 119,877,256 | trade_time | adj_factor |
| qdb.stock_basic | stock_basic | 5,222 | list_date | — |
| qdb.index_daily | index_daily | 126,435 | trade_date | — |
| qdb.sw_classify | sw_classify | 511 | ingest_time | — |

---

## B3. 大批量 Manifest 规范（Parquet 工件）

### B3.1 触发条件
- 单数据集请求行数 > `page_size` 上限（默认 50,000）或命中分钟级大表（`stock_minutes`/`etf_minutes`）→ 生成 Parquet 工件 + Manifest，返回 `manifest_ref`。

### B3.2 Manifest 结构（JSON）
```json
{
  "job_id": "j_20260801_stock_minutes_abc",
  "dataset_id": "qdb.stock_minutes",
  "generated_at": "2026-08-01T02:00:00Z",
  "total_rows": 478535169,
  "shards": [
    {"shard_id": "s00", "row_start": 0, "row_end": 49999,
     "parquet_uri": "artifact://j_.../s00.parquet", "sha256": "...", "rows": 50000},
    ...
  ],
  "concat_sha256": "对所有 shard sha256 有序拼接再 sha256",
  "code_format": "symbol_with_suffix",
  "adj_factor_included": true
}
```

### B3.3 分页与游标
- `fetch_page` 用 `cursor`（opaque base64）定位；分钟数据每页强制 ≤ 50k 行。
- `get_artifact` 按 `shard_id` 拉取单个 Parquet；客户端顺序下载 + 校验 `sha256` + 末尾 `concat_sha256` 全量校验。

### B3.4 完整性校验
- 每个 shard Parquet 附 `sha256`；Manifest 附 `concat_sha256`。
- 客户端下载后逐 shard 验 sha256，全量拼接验 concat_sha256，失败拒绝入库。

---

## B4. 安全治理

| 控制项 | 要求 |
|--------|------|
| 鉴权 | MCP 请求须带 API key（Header `X-MCP-Key` 或 Bearer）；server 校验 scope 后返回数据集 |
| 限流 | 按 key 分级：free=100 req/min + 50MB/min；pro=1000 req/min + 2GB/min；超出 429 |
| TLS | 传输层强制 TLS1.2+；明文 HTTP 拒绝 |
| SHA256 | 所有工件（Parquet/Manifest）须带 sha256（见 B3.4） |
| 审计 | server 记录 key + dataset_id + 时间戳 + 行数（不记录数据内容） |
| 最小权限 | key scope 限定数据集白名单；`validate_access` 可自查 |
| 防注入 | `query_snapshot` 的 filters 仅允许白名单列 + 范围谓词，禁止任意 SQL |

---

## B5. 职责边界（server vs client）

| 职责 | server（MCP，只读） | client（QuantStudio，可写） |
|------|--------------------|-----------------------------|
| 数据返回 | 原始行（raw + adj_factor） | 消费并按需复权 |
| 复权计算 | ❌ 不预计算 | ✅ `raw × adj_factor` |
| PIT 校验 | ❌ | ✅ 以 trade_date/trade_time 去未来数据 |
| code 规范化 | 返回带后缀 SYMBOL | ✅ 转裸数字对齐 DuckDB |
| 写库 | ❌ 只读 | ✅ 写本地 DuckDB / 校验入仓 |
| 权限校验 | ✅ 校验 key scope | — |
| 完整性校验 | 提供 sha256 | ✅ 下载后验 sha256 |

> 红线圈定：server 不得修改 QuestDB、不得预计算复权、不得写客户端库。价格权威（xtquant）在客户端落地，MCP 仅作数据搬运通道。

---

## C. 待决事项（非本轮范围）
- C4：MCP server 本体实现（workbuddy 后续）。
- 全量覆盖堆积技术债（云端 sw_classify designated ts=ingest_time，每周 +1）不属 MCP 范围。
- C0 合规验证由用户侧落实；本轮不声称合规已验证。
