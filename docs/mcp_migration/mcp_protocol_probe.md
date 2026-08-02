# MCP 协议探针报告（P2-0 门禁产物）

- 目标 endpoint：`https://124.223.159.234/mcp`（IP 先行，备案后切域名）
- 探针日期：2026-08-02
- 探针工具：`scripts/_mcp_protocol_probe.py`（临时，跑完即删）
- **密钥纪律**：`MCP_API_KEY` 仅从环境变量读取，脚本全程不回显/不记日志/不落盘。HTTP 调试对 `X-MCP-Key`/`Authorization` 头脱敏为 `***REDACTED***`。

---

## 0. 状态总览

| 项 | 状态 | 说明 |
|----|------|------|
| 连通性 / TLS | ✅ 实测固化 | 直连 124.223.159.234:443，HTTPS 通 |
| 无 key → 401 结构 | ✅ 实测固化 | 见 §1 |
| initialize 握手 + session id | ✅ 实测固化 | SSE + `mcp-session-id` 头，见 §2 |
| tools/list（12 工具清单） | ✅ 实测固化 | ⚠️ 与任务书 P1B 假设不符，见 §4 |
| tools/call 语义（ping/describe/fetch_page/export） | ✅ 实测固化 | 见 §5 |
| SSE vs JSON 判据 | ✅ 实测固化 | 全程 SSE，见 §5.1 |
| 406 触发条件 | ✅ 实测固化（无 406） | 非法 protocolVersion → 回退服务端版本，见 §5.2 |
| 错误码结构（含 tools/call 失败） | ✅ 实测固化 | 401/-32001 + result.isError，见 §5.5 |
| Raw Landing 决策 | ✅ 已定 | 见 §6 |
| QFQ mcp_only 处理决策 | ✅ 已定（升级为完整闭环复现） | 见 §7（§0/P3-7 最高优先级） |

> ⚠️ **P1B 契约偏差**：实测 12 工具与任务书 §1 冻结的 P1B 假设工具名（fetch_table/fetch_range/get_adj_factor/get_data_sha256/filter_universe/get_last_update）**全部不存在**。取数主路径是 `create_export_job→get_manifest→get_artifact`（Parquet 导出作业），小表走 `query_snapshot`。P2-1 须按实测重组 client 封装。

---

## 1. 连通性与鉴权（无 key 实测）

### 1.1 网络层
- `socket.gethostbyname("124.223.159.234")` 直连（IP，无 DNS）。
- `netstat` 确认 ESTABLISHED 到 `124.223.159.234:443`（HTTPS 默认端口）。
- 浏览器/脚本访问 HTTPS 正常握手；开发期 TLS 校验关闭（`verify=False`）时仅为本地告警文本（`127.0.0.1` 误报来自 urllib3），实际 TCP 连接目标为真实 IP（netstat 佐证），**非代理转发**。

### 1.2 无 key → 401（实测响应原文）
```
POST https://124.223.159.234/mcp
Content-Type: application/json
Accept: application/json, text/event-stream
(无 X-MCP-Key)

HTTP/1.1 401 Unauthorized
Content-Type: application/json
Server: nginx
Body:
{
  "jsonrpc": "2.0",
  "id": null,
  "error": {
    "code": -32001,
    "message": "unauthorized: missing or invalid X-MCP-Key"
  }
}
```
**固化结论**：
- 鉴权头字段 = `X-MCP-Key`（非 `Authorization`）。
- 缺失/无效 key 一律 `401`，错误码 `-32001`，消息前缀 `unauthorized:`。
- `id` 为 `null`（服务端拒绝阶段不绑定请求 id）。
- 响应为纯 JSON（`Content-Type: application/json`），非 SSE。

---

## 2. initialize 握手（实测 2026-08-02）

请求：
```http
POST /mcp
Content-Type: application/json
Accept: application/json, text/event-stream
X-MCP-Key: ***REDACTED***
```
```json
{"jsonrpc":"2.0","id":1,"method":"initialize",
 "params":{"protocolVersion":"2024-11-05","capabilities":{},
           "clientInfo":{"name":"qs-probe","version":"0.1"}}}
```
响应（HTTP 200，`Content-Type: text/event-stream`）：
```
event: message
data: {"jsonrpc":"2.0","id":1,"result":{
  "capabilities":{"experimental":{},"prompts":{"listChanged":false},
                  "resources":{"listChanged":false,"subscribe":false},
                  "tools":{"listChanged":false}},
  "protocolVersion":"2024-11-05",
  "serverInfo":{"name":"QuantStudio-MCP","version":""}}}
```
**固化**：
- 响应头 `mcp-session-id`（小写连字符）返回会话 ID（如 `edd262857710450aac6310299bcaf8f4`）。
- 走 **SSE**（`text/event-stream`），body 为 `event: message\r\ndata: {json}\r\n\r\n`（CRLF）。
- 客户端须把 `mcp-session-id` 回传后续所有请求（含 `notifications/initialized` 与 `tools/call`）。

## 3. notifications/initialized（实测）
携带 `Mcp-Session-Id` 头发送：
```json
{"jsonrpc":"2.0","method":"notifications/initialized"}
```
响应 HTTP **202**，`Content-Type: application/json`，body 空。

## 4. tools/list — 12 工具契约（实测，⚠️ 与任务书 P1B 假设不符）

> **重大偏差（须用户知悉）**：任务书 P1B 契约假设的 `fetch_table / fetch_range / get_adj_factor / get_data_sha256 / filter_universe / get_last_update` **在实测中不存在**。
> 实际 12 工具为「导出作业模式 + 小表快照 + 元数据」三族，取数主路径是 **create_export_job → get_manifest → get_artifact**（非 fetch_table）。
> P2-1 的 client 封装须按**实测清单**重组，不能照搬 P1B 工具名。

```
TOOLS_COUNT = 12
 1. ping_health          健康检查：QuestDB 可达性 + 延迟（返回 status/questdb/latency_ms）
 2. list_datasets        列出本 key 可访问的 dataset_id（qdb.<table>）
 3. describe_server      服务端能力/版本/层级/范围/限流（transport=streamable-http, tier=pro, read_only=true, adj_policy=raw+adj_factor, dataset_count=109）
 4. describe_dataset     数据集 schema：列名/类型/designated-ts/adj_factor 描述
 5. get_schema           原始列 schema（name/type/designated）
 6. fetch_page           取一页（<=50k 行）。大分钟表建议用 export_job
 7. query_snapshot       单点快照：白名单列的小结果集（直接返回行 JSON，含 adj_factor/is_qfq）★小表内存路径
 8. get_coverage         覆盖矩阵：row_count/ts_min/ts_max/distinct_code
 9. validate_access      校验 key 并报 scope/allowed datasets
10. create_export_job    触发 Parquet 导出作业（cursor 分页 shard，<=50k）★大表主路径
11. get_manifest         返回作业 Manifest JSON（shard 列表 + sha256）
12. get_artifact         返回一个 Parquet shard（base64）+ sha256 + size
```

## 5. tools/call 语义 / SSE-JSON 判据 / 406 / 错误码（实测）

### 5.1 传输判据
- 所有 `tools/call` 响应均为 **SSE**（`text/event-stream`），body 形如 `event: message\r\ndata: {jsonrpc...}`。
- 成功：`result.content[0].type="text"`，`result.content[0].text` 为 JSON 字符串（须二次 `json.loads`）。
- `result.isError` 标记工具级错误（见 5.4）。

### 5.2 协议版本协商
- 客户端发 `protocolVersion:"2024-11-05"` → 服务端回 `2024-11-05` 并建会话。
- 客户端发**非法版本** `1999-99-99` → 服务端回 `protocolVersion:"2025-11-25"`（返回其支持版本，**不返回 406**）。
  ⇒ **客户端应固定用 `2024-11-05`** 协商；服务端支持更高版本时以其回传为准，不做 406 拒绝。

### 5.3 导出作业主路径（大表 Parquet 取数，已端到端验证）
```
create_export_job({dataset_id:"qdb.stock_daily", page_size:1000})
  → result.content[0].text = {
      "manifest_ref":"j_1785611773_stock_daily",
      "total_rows":2000000, "shard_count":40,
      "schema_fingerprint":"3c35ba101f37df66",
      "concat_sha256":"cace3e36419b4a9a86cb637a635543ff5a1d73a5af4afe7627e58fd7909922a2"}

get_manifest({job_id:"j_1785611773_stock_daily"})   # 注意：参数名 job_id = manifest_ref 值
  → {job_id, dataset_id, table, generated_at, total_rows, shard_count,
     shards:[{shard_id:"part_00000", row_start:0, row_end:50000,
              parquet_uri:"artifact://j_.../part_00000",
              sha256:"c215...", rows:50000, size_bytes:1872194}, ...]}

get_artifact({job_id:"j_...", artifact_id:"j_.../part_00000"})  # artifact_id = job_id/shard_id
  → {artifact_id, sha256, size_bytes, content_base64:"UEFS..."}  # "UEARS"=PAR1 Parquet 魔数
```
- **对账**：`get_artifact` 返回的 `sha256` 应与 `get_manifest` 该 shard 的 `sha256` 一致（§1.5 第6项完整性校验）。
- `concat_sha256` 是全量拼接 sha256，可作整体一致性基线。

### 5.4 小表内存路径（query_snapshot，已验证含 adj_factor）
```
query_snapshot({dataset_id:"qdb.stock_daily", columns:["ts_code","trade_date","close"], limit:3})
  → rows:[{ts_code:"301677.SZ", trade_date:"2026-07-31T00:00:00",
           open:99.0, high:110.0, low:85.28, close:88.02, pre_close:82.4,
           change:5.62, pct_chg:6.8204, vol:157222.22, amount:1527267.98,
           adj_factor:1.0, is_qfq:true}, ...]
```
- **`adj_factor` 列直接在每日行内返回**，`is_qfq:true` ⇒ 证实**复权在客户端**（`raw × adj_factor`），与任务书 §1.5 一致。
- `trade_date` 为 ISO `YYYY-MM-DDTHH:MM:SS` 字符串。

### 5.5 错误码结构（实测）
- **鉴权失败**：HTTP 401，`{"jsonrpc":"2.0","id":null,"error":{"code":-32001,"message":"unauthorized: missing or invalid X-MCP-Key"}}`（见 §1.2）。
- **工具级错误（不抛连接异常，走 result）**：
  - 不存在工具：`result.isError=true`，`result.content[0].text="Unknown tool: no_such_tool"`。
  - 参数校验失败（pydantic）：`result.isError=true`，`text="Error executing tool <tool>: N validation error ... <field> ..."`（如 `fetch_page` cursor=None → `cursor Input should be a valid string`；`get_manifest` 缺 `job_id` → `job_id Field required`）。
  - `get_artifact` artifact_id 格式错：`{"error":"artifact_id must be job_id/shard_id"}`（走 result 的 error 字段，非 content）。
- **客户端须检查两类错误**：`response.error`（JSON-RPC 级）与 `result.isError`/result 内 `error`（工具级），均按失败处理。

### 5.6 关键参数约束（固化，避免 P2-1 踩坑）
- `fetch_page.cursor` 必须为**字符串**，首页传 `""`（传 `null` → pydantic 校验失败）。
- `get_manifest` 参数名 `job_id`，值 = `create_export_job` 返回的 `manifest_ref`。
- `get_artifact.artifact_id` 格式 = `"{job_id}/{shard_id}"`（如 `j_xxx/part_00000`），不是裸 `shard_id`。
- `page_size` 上限 50000（导出作业 shard 固定 5万行/片，共 40 片覆盖 200万行 `stock_daily`）。

---

## 6. Raw Landing 决策（P2-0 第 2 项 — 已定）

方案在任务书基础上明确落地：

### 6.1 分钟线 Parquet → 受控 landing
- **落地根**：`DATA_ROOT/mcp_landing/`（由 `quantstudio._paths.get_data_root()` 解析，`QUANTSTUDIO_DATA_ROOT` 可重定向）。
- **目录分层**：`mcp_landing/{dataset_id}/{trade_date}/{shard}.parquet`
  - 例：`mcp_landing/qdb.stock_minute/2026-08-02/600000.SH.parquet`
- **落地即落元**：每个 shard 同目录写 `.sha256` 侧车文件（hex digest），供客户端 `get_data_sha256` 对账（§1.5 第6项）。
- **受控**：landing 不进 DuckDB，仅作"云端→本地"中转；经 aligner/validator/writer 校验后再写 staging DuckDB。landing 文件属可重放、可清理（保留 N 天由 retention 策略控制，不在本阶段实现）。

### 6.2 小表（universe / adj_factor / coverage / metadata）→ 内存
- **adj_factor 数据来源修正**：任务书假设的 `get_adj_factor` 工具**实测不存在**；实测 `adj_factor` 直接随每日行情行返回（`query_snapshot` / 导出 Parquet 行均含 `adj_factor` 列 + `is_qfq` 标记）。
  ⇒ QFQ 所需因子 = 从落地 Parquet/快照逐行取 `adj_factor`，**无需单独因子工具**。
- 元数据（`describe_dataset / get_coverage / list_datasets / get_schema`）→ 内存缓存，随 `get_coverage`/`describe_server` 变更失效。
- 内存表**不落盘**到正式 DuckDB；权威持久化数据以 raw Parquet（landing→staging）为准。

### 6.3 复权在客户端（铁律，源自任务书 §1.5，且经 §5.4 实测印证）
- raw 行已含 `adj_factor`（`is_qfq=true`），客户端按行计算：
  - **front（盘中/最新）**：`price_front = raw_price × adj_factor_i / adj_factor_latest`
  - **back（历史回测）**：`price_back = raw_price × adj_factor_i / adj_factor_earliest`
- 禁止信任云端任何预计算前/后复权列；`adj_factor` 仅作原始因子，客户端算（见 §7.2-B 对 QFQ fresh_capture 的双轨取数约定）。

---

## 7. QFQ 在 mcp_only 下的处理决策（P2-0 第 3 项 — 已定）

### 7.1 代码级硬依赖核查（已读源码确认）
- `qfq_orchestrator_types.py:367` 与 `qfq_fresh_capture.py:375`：
  `config.price_source` **强制校验必须为 `"xtquant"`**，否则抛 `ValueError`。
  QFQ orchestrator 的"前复权基准修正/reanchor"依赖 xtquant 实时前复权价。
- `qfq_orchestrator_types.py:362-365`：
  `stock_factor_detector="tushare_adj_factor"`、`etf_factor_detector="tushare_fund_adj"`，
  因子检测硬依赖 tushare。
- daemon `per_trade_date` 路径（`_execute_task_per_trade_date`）：
  **tushare 专用**（函数内 `import tushare as ts`，docstring 明确"tushare 日线专用"），
  **不在 mcp 采集路径上**。

### 7.2 升级决策（最高优先级 — 完整复现 QFQ 事件驱动闭环，非"禁用"）

> **方向修正**：任务书 §0 / P3-7 明确要求，MCP 入库管线必须**完整复现**原 QFQ 12 模块事件驱动闭环
> （event_discovery → fresh_capture → reanchor_engine → resident_orchestrator → 质量门控），
> 而不是简单地 `raw×adj_factor`。原 P2-0 第3项"禁用 QFQ"的初版设计**已作废**，
> 改为"用 MCP 数据驱动完整 QFQ 闭环"。做出"能取数但不会重算复权"的管线 = 失败。

闭环对"取数源"的抽象**已经存在且可注入**（已读源码确认），适配是最小侵入：

**(A) 事件发现层（零代码改造，数据注入即可驱动）**
- `qfq_event_discovery.scan_stock_dividend` 读 DuckDB 主库 `stock_dividend`
  （`div_proc='实施' AND ex_date 非空`）→ STOCK 分红 trigger。
- `observe_stock_adj_factor` / `observe_etf_fund_adj` 读 `qfq_aux.db(adj_factor / fund_adj)`
  快照 → 版本化 observation（`qfq_factor_observation`）→ 因子变化 trigger。
  `detection_source` 字段（`tushare_adj_factor_new` / `tushare_fund_adj_new`）仅作溯源标签，不影响逻辑。
- **MCP 适配**：P2-2 采集链路把 dividend 元数据写入 DuckDB `stock_dividend`、把 `get_adj_factor`
  返回的因子写入 `qfq_aux.db(adj_factor / fund_adj)` 快照（替代 tushare 写入）→ discovery 自然驱动
  trigger，代码不动。P3-7 lineage 验证时把 `detection_source` 改标 `mcp_adj_factor_new` 等以区分来源。

**(B) 因子捕获层（最小改造 — 新增 McpFreshFetcher）**
- `FreshFetcher` 抽象基类已存在（`qfq_fresh_capture.py:129`），已有 `XtquantFreshFetcher`（:150）
  与 `FakeFreshFetcher`（:274）两实现，且 `capture()` / `_reanchor_security()` 的 `fetcher` 是**注入参数**。
- 新增 `McpFreshFetcher(FreshFetcher)`：实现 `fetch_none_front(asset_type, xt_code, period,
  start_yyyymmdd, end_yyyymmdd) -> (none_df, front_df)`：
  - `none_df` ← MCP `fetch_table(qdb.stock_daily / qdb.stock_minute, dividend_type='none')` 的 raw OHLC；
  - `front_df` ← MCP `fetch_table(..., dividend_type='front')` 的前复权 OHLC。
  - 接口签名**严格复用** `XtquantFreshFetcher.fetch_none_front`，使 `FreshCapture.capture`
    与 `qfq_reanchor_engine` 无需改动。
- 不再用 `get_adj_factor` 在客户端算 front（那会变成 §6.3 简化版，违反"完整闭环"要求）；
  front 一律由 MCP `dividend_type='front'` 原始提供，与 xtquant 双轨语义一致。

**(C) 重锚 + 编排层（配置层放宽，非逻辑改写）**
- `qfq_resident_orchestrator._reanchor_security` 当前硬编码 `fresh_source="xtquant"`（:437）；
  改为按 QFQ config 注入 `fresh_source`（mcp_only 时 `"mcp"`），`fetcher` 注入 `McpFreshFetcher`。
- `qfq_orchestrator_types.py:367` 与 `qfq_fresh_capture.py:375` 强制 `price_source=="xtquant"` 的
  校验，放开为允许 `{xtquant, mcp}`（白名单），mcp_only 时 `price_source="mcp"`。
- 引擎 `apply_reanchor_for_security` 的 `price_source`/`fresh_source` 已是字符串参数
  （`qfq_reanchor_engine.py:1818 _advance_anchor_on_conn`、`price_source` 默认 `"xtquant"` 可传其他）
  → 重锚计算（front_exact_match / tick 容差 / 水位 hold_until_consistent / 崩溃幂等）**原样复用**，
  不重写公式。

**(D) 质量门控 / 水位协调（原样复用）**
- `qfq_reanchor_engine` 的 `model="fresh_authoritative_rebase"` 全套 precheck/postcheck、
  `front_exact_match`、`tick` 容差、`hold_until_consistent`、`RECOVER_APPLIED_NO_EVENT` 阻断、
  崩溃幂等（event_id 预写 + `_already_committed` 跳过）→ 全部原样跑，仅 `fresh_source`/`price_source`
  标签变 `mcp`。

### 7.3 关键不变量（P3-7 验收基线）
1. MCP 闭环产出的 `open_front/high_front/low_front/close_front` 必须与"同输入下 xtquant 闭环"在
   tick 容差内一致（P3-7 用冻结快照对拍，非浮点全同）。
2. 事件发现 trigger 集（STOCK 分红 + 因子变化）必须与 xtquant/tushare 源同口径（仅 `detection_source`
   标签不同）。
3. `qfq_aux.db` 在 mcp_only 下**仍被使用**（存 MCP 写入的 adj_factor/fund_adj 快照），不复用旧
   tushare 历史快照；首次 mcp_only 启动需 bootstrap 重建因子观察基线（discovery.bootstrap）。
4. `per_trade_date`（tushare 专用）仍不进 mcp 路径（与 §7.1 一致，P2-4 加 source 分支守卫）。
5. authority：`mcp`=传输通道；`xtquant`=上游权威（仅 P3 lineage 对拍时参照）。mcp_only 日常运行
   不依赖 xtquant 在线。

### 7.4 线1：is_qfq 还原 raw（adapter 侧还原，已落地 2026-08-03）

> 任务书：`docs/mcp_migration/is_qfq_restore-raw-task.md`。验收报告：`docs/evidence/mcp_qfq_restore_verify_2026-08-03.md`。

**问题**：QuestDB 云端存的是 **qfq（前复权价）** 而非 raw。若 MCP 管线直接把 qfq 当 raw 喂进 aligner
的 `front = raw × adj_i / adj_latest`，会**二次复权（双重复权）**，300750 4-22 正确 front=226.312、
错误 front=222.017。

**方案**：adapter 取数后立即在本地还原 raw：
```
raw_i = qfq_i × adj_latest_global / adj_factor_i
adj_latest_global = qfq_aux.db(adj_factor/fund_adj) 完整历史的 ORDER BY time DESC LIMIT 1  # 300750=1.9495
```
还原后管线永远吃 raw + adj_factor，走 aligner 标准路径（front=raw×adj_i/adj_latest），与 tushare 同源。

**关键不变量 / 护栏（codex P0-① + 正确性护栏）**：
- `adj_latest_global` **绝不**取本次 export 分片末行：历史窗口误用偏差可达 8.67 元（2024-06-03 正确 202.5001 vs 误用分片末行 193.8267，见任务书附录 C.3）。
- 缺因子 → **fail-fast**（禁止把 qfq 当 raw 写库污染）。
- 因子锚过期（`adj_factor_i > adj_latest_global`，本地快照落后于云端）→ **fail-fast**（需先同步 qfq_aux.db）。
- 仅还原价格列（open/high/low/close/pre_close）；vol/amount/pct_chg 原样保留。
- 有 `is_qfq` 列时只还原 `is_qfq=True` 行；`is_qfq=False` 本就是 raw，原样保留。

**已知限制（设计内，须记录）**：
- **front 锚不同（~1.6%）**：MCP 还原走 qfq_aux.db **云端因子系列**（latest=1.9495），tushare 系列 latest=1.9816，差 ~1.6%。两系列均合法，但 MCP 路径产出的 `*_front` 与 tushare 路径 front **不会 tick 一致**；跨源比较 front 须注意锚差异（验收#2 已记录差异预期）。
- **ETF fund_adj 缺口**：qfq_aux.db `fund_adj` 当前为空，ETF 还原首次需触发全历史冷启动灌库（一次性重操作）；灌库前 ETF 还原 fail-fast。代码路径已具备（`_inject_adjfactor` 正确路由 `etf*`→`fund_adj`）。
- 不影响策略 API：`get_history` 等注入 API 仍默认 `fq='pre'`，本修复是管线内部（adapter 侧），策略层无感。

---

## 8. P2-0 通过判据
- [x] 连通性 + TLS + 401 结构实测固化
- [x] 带 key 完整握手（initialize/session/tools/list/tools/call/SSE-JSON/406/错误码）实测固化（§2~§5）
- [x] 12 工具清单固化（⚠️ 与 P1B 偏差已记录，P2-1 按实测重组）
- [x] Raw Landing 决策明确
- [x] QFQ mcp_only 处理决策明确（已升级为**完整复现 QFQ 事件驱动闭环**，非禁用）
- [x] QFQ 完整闭环适配设计（§7.2 A/B/C/D：McpFreshFetcher 注入 + source 配置放开 + 数据驱动 discovery + 门控原样复用）

## 9. 待办（进入 P2-1 后落地）
- P2-1：实现 `quantstudio/pipeline/mcp/client.py`（12 工具封装 / 握手 / session / retry / SHA256 / Parquet / TLS / Header 脱敏）。
- P2-2：MCPSourceAdapter.fetch_table 落地 raw+adj_factor，并写入 DuckDB `stock_dividend` + `qfq_aux.db(adj_factor/fund_adj)` 快照以驱动 QFQ discovery。
- P2-3：config/profiles/mcp_only/ 独立配置（不改默认生产配置）。
- P2-4：显式 source 分支守卫（禁止 mcp 任务路由到 per_trade_date / qfq_orchestrator）。
- P3-1~P3-6：staging DuckDB + 对账 + lineage。
- **P3-7（最高优先级）**：新增 `McpFreshFetcher(FreshFetcher)`，`fresh_source/price_source` 配置放开到 `mcp`，用冻结快照对拍验证 MCP 闭环复权结果与 xtquant 闭环在 tick 容差内一致。
