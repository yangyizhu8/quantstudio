# A4 执行方案：变更检测闭环（MCP 工具 + 客户端接入）

> 版本：v1.2（2026-08-10，reasonix 编排）
> 目标：让所有下游（QuantStudio + 客户）增量拉取前能检测"云端哪些数据窗口被
> 更新过"，自动重拉被修正的窗口——杜绝"已拉数据被更新却永远用旧值"。
> 架构（v1.2 修正，基于云端取证）：cloud_updated_log 表在**云端 QuestDB**
> （本机 sync_to_cloud 推送时通过公网 8812 写入）；**云端 v1.0.0 MCP server**
> （C:\quant_data_server\mcp_server，12 个数据工具）与 QuestDB **同机**，
> 读云端 QuestDB 走 **127.0.0.1:8812 回环**（psycopg2，与现有 12 工具同通道，
> 零网络配置）；客户端（QuantStudio）只走 MCP 协议（443 /mcp）。
> ⚠️ v1.2 重要修正：任务 A 目标 = **云端 v1.0.0**（数据服务）。本机
> QuantDinger/mcp_server（0.3.0）是独立的 QuantDinger 策略网关代理
> （27 工具，Bearer token 代理 Agent Gateway），**与 QuantStudio 无关，
> 搁置不发布、不覆盖云端**。v1.0 的 HTTP 9000 / QUANTDINGER_QDB_HTTP_URL /
> PyPI / Railway 路径全部作废（9000 锁 localhost 是安全设计，保持现状）。

---

## 任务 A（workbuddy，云端）：云端 v1.0.0 新增 `query_updated_since` 工具

**代码位置**：`C:\quant_data_server\mcp_server\`（线上 v1.0.0，SERVER_VERSION="1.0.0"，
psycopg2 → 127.0.0.1:8812）

**前置（Trae 交付，见任务 A0）**：cloud_updated_log 契约文档（DDL + 字段语义 +
查询 SQL 样例）+ 云端真实 log 状态验证。

**实现要求**（必须遵循 v1.0.0 铁律，与现有 12 工具一致）：
1. 新工具 `query_updated_since(since: str, table: str | None = None, limit: int = 1000)`：
   - async def，返回 dict（JSON 序列化）；失败一律 `{"error": "<message>"}`；
   - 入参校验：since 必填 ISO 8601（拒绝原始拼接，规范化后进 SQL）；
   - 逻辑：psycopg2 查云端 QuestDB `cloud_updated_log`，返回
     `last_update_time > since` 的记录（可选 table 过滤，limit 封顶，DESC 排序）；
   - 返回：`{"updates": [{"table_name", "trade_date", "last_update_time",
     "update_source", "rows_pushed"}, ...], "count": int}`；
   - `trade_date` 输出 `YYYY-MM-DD`（TIMESTAMP 截前 10 字符）；
   - 空结果/表不存在 → `{"updates": [], "count": 0}`（不报错）；
   - **只读**：仅 SELECT；
2. **鉴权/安全**：走既有 AuthMiddleware（X-MCP-Key → tier/scope）+ 限流 +
   只读铁律——与 12 工具完全一致；
3. **注册**：tools 列表 12 → 13；`describe_server` 的 tools 数组同步追加
   `"query_updated_since"`（客户端可能按顺序枚举）；
4. **版本**：SERVER_VERSION "1.0.0" → **"1.1.0"**；
5. **测试**：since 过滤/table 过滤/空结果/表不存在/limit 封顶/鉴权 406/越权
   access denied；12 工具冒烟无回归（每工具真实调用一轮）；
6. 交付：代码 diff + 测试证据 + 版本变更 + 重启后 describe_server 显示 13 工具。

## 任务 A0（Trae，本机）：cloud_updated_log 契约交付

1. 产出契约文档（供 workbuddy 写工具）：cloud_updated_log 的 DDL
   （table_name/trade_date/last_update_time/update_source/rows_pushed，
   designated ts = trade_date，DEDUP KEY）+ 字段语义 + 等效查询 SQL 样例
   （含 `WHERE last_update_time > :since` 写法）+ update_source 枚举
   （incremental/repair/full）；
2. 验证云端真实 cloud_updated_log 状态：本机 psycopg2 只读连接云端 8812，
   确认表存在 + 数据样例（推送写入链路在真实运行）；
3. 交付：契约文档 + 云端真实 log 验证记录。

## 任务 B（workbuddy，云端）：发布 v1.1.0

```
① 备份：cp -r C:\quant_data_server\mcp_server\ mcp_server_bak_v1.0.0_<ts>\
② 替换源码：v1.1.0 写入 C:\quant_data_server\mcp_server\（覆盖）
③ 重启：Restart-Service mcp-server（nssm 自愈 + 开机自启不变）
④ 验证：ping_health + 12 工具冒烟 + query_updated_since 真实返回
        + describe_server 显示 13 工具 + 版本 1.1.0
```
无 PyPI、无 Railway、无环境变量注入（8812 走既有 .env 配置）。


## 任务 C（ZCode）：QuantStudio 客户端接入

**代码位置**：`quantstudio/pipeline/sources/mcp_adapter.py`（或 client 层）+ `quantstudio/pipeline/daemon.py` 增量分支

**实现要求（修正 v1.1：2026-08-10）**：
1. **接入范围 = 所有增量拉取入口**（统一走 `UpdateDetector`）：
   - daemon 每日定时增量（06:00，周一至六）——主场景；
   - GUI 手动"增量拉取"——手动拉过数据后同样存在"已拉窗口被云端修正"风险；
   - CLI `--mode once` 增量任务——同上；
   - **全量拉取（full_range）不接入**（全量本就覆盖一切）；
2. **接口抽象先行**（不阻塞等工具发布）：
   ```python
   class UpdateDetector(Protocol):
       def query_updated_since(self, last_sync: str) -> List[Tuple[str, str, str]]:
           """返回 [(table, trade_date, update_source), ...]"""
   ```
   - 后端 1（当前）：`MockUpdateDetector`（返回空，行为零变化，可先行集成）；
   - 后端 2（工具发布后）：`MCPUpdateDetector`（调 MCP client 新方法 `query_updated_since`）；
3. **last_sync 基准持久化**（关键，防跨入口/重启失效）：
   - 每次**任意增量入口**拉取成功后，持久化"本次完成时间戳"（或本次覆盖的最大窗口）；
   - 基准存本地状态（`source_watermark` 旁或独立 kv 表），供所有入口下次查询共用；
   - 无基准（首次/升级后）→ 跳过检测（等同 mock 行为），不阻塞拉取；
4. **集成逻辑**（增量开始前）：
   - `detector.query_updated_since(last_sync)`；
   - 对返回的每个 `(table, trade_date)`：**`update_source ∈ {repair, full}` 必须局部重拉**（DEDUP UPSERT 幂等覆盖）；`incremental` 可选择（记录审计）；
   - 局部重拉后再走正常水位增量；
   - 无更新 → 零额外开销（一次轻查询）；
5. **测试**：
   - mock detector 返回 repair/full 更新 → 对应 (table, date) 被重拉、数据一致；
   - 返回空 → 行为与现在完全一致（回归保护）；
   - last_sync 持久化：拉取成功后基准更新；重启后基准仍有效（跨进程）；
   - 三个入口（daemon 定时 / GUI 手动 / CLI）共用同一检测路径（单一实现，入口层调同一函数）；
   - 既有 mcp/daemon 测试无回归；
6. staging 验证（MCP 工具发布后与 Trae 联调）：真实增量采集一轮——云端推送历史窗口更新 → 客户端检测 → 局部重拉 → 本地与云端一致。
## 时序与依赖

```
任务 A（Trae）──────┐
                    ├──→ 任务 B 发布（用户授权）──→ MCP 工具上线
任务 C 接口抽象+集成（ZCode，mock 后端先行）──┘         │
                                                        ↓
                              任务 C 换 MCP 后端 + 联调（staging 真实增量一轮）
```

- 任务 A 与任务 C 可完全并行；
- 任务 B 是 A 与 C 联调之间的唯一依赖；
- C-4 bootstrap 运行不受任何影响（C 只动增量分支，不碰 bootstrap/export_cache 路径）。

## 红线

- ❌ MCP server 只加工具，不改既有 12 工具行为；
- ❌ cloud_updated_log 只读（工具与服务端均无写路径）；
- ❌ QuantStudio 不直连云端 QuestDB（安全模型：一切走 MCP 协议）；
- ❌ 不碰 C-4 / bootstrap / export_cache / 正式库；
- ✅ 无更新时零开销（一次轻查询）；
- ✅ 发布动作需用户明确授权（PyPI token / Railway 部署）。

## 验收标准

1. 任务 A：`query_updated_since` 工具测试全绿 + 真实云端调用正确；
2. 任务 B：0.3.0 发布成功，云端 server 可调新工具（check_health + 手动调用）；
3. 任务 C：mock 集成测试全绿；工具上线后换后端 + staging 联调一轮通过（检测→重拉→一致）；
4. 无回归：QuantStudio 既有测试 + mcp_server 既有测试全绿。
