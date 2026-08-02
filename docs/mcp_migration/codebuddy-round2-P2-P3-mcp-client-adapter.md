# P2/P3 客户端接入任务书（CodeBuddy 执行）— 完全采纳审核意见版

> **版本**：v1（2026-08-02）
> **执行智能体**：CodeBuddy（本地 QuantStudio）
> **审核**：ZCode（监督）
> **前置**：P1B契约(f166502c)、MCP server(C4已完成+压测达标)、C0合规解除
> **本轮范围**：P2-0契约收口 → P2-1~P2-4 → P3-1~P3-6（渐进式，每阶段验证通过才进下一阶段）

---

## 0. 审核批准的实施纲领（必读）

本任务书完全基于 P2/P3 方案复审结论。审核发现原方案4个致命遗漏，全部修订采纳：
1. daemon authority guard 硬编码会拒绝 MCP（必须区分 transport_source / upstream_authority）
2. mcp_only 仍隐式访问 xtquant/tushare（QFQ orchestrator + per_trade_date 路径）
3. mcp_only.json 不会自动生效（无 profile loader，用独立 config-dir 目录）
4. 12工具（非11，含 create_export_job）

### 【最高优先级·用户明确要求】QFQ复权重算完整功能对齐
**新MCP数据源入库管线必须实现与原管线完全相同的复权重算功能。** 当某ETF/股票当日除权除息时，在"数据拉取→标准化→入库→质量检查"全流程中，必须自动检测事件、重算复权价格、过质量门控、入库。

原管线QFQ是一个**12模块的事件驱动完整闭环**（不是简单的raw×adj_factor计算）：
- qfq_event_discovery：扫描stock_dividend表检测除权除息→生成trigger
- qfq_fresh_capture：多worker捕获除权日的复权因子
- qfq_reanchor_engine：按新因子重算复权价格
- qfq_resident_orchestrator：协调各环节+水位hold/推进
- 质量门控：front_exact_match/raw_unchanged/row_conservation/coverage_min
- 配置：price_source=xtquant、stock_factor_detector=tushare_adj_factor、etf_factor_detector=tushare_fund_adj

**P2/P3必须保证这套QFQ闭环在MCP数据源下完整复现**：
- MCP数据里的dividend/除权信息要能驱动trigger生成（替代扫stock_dividend）
- MCP返回的adj_factor要能被fresh_capture使用（替代tushare_adj_factor）
- 重锚计算用MCP的raw+adj_factor，且与xtquant口径一致
- 质量门控（front_exact_match等）必须通过
- 水位协调（hold_until_consistent）不变

**这是核心验收标准，不是附属项。** 做出一个"能取数但不会重算复权"的管线是失败的。

## 1. 冻结的契约要素（P2-0产物，不可自行变更）

### 1.1 工具清单（12个，冻结）
```
describe_server / describe_dataset / fetch_page / get_artifact / query_snapshot /
get_schema / list_datasets / get_coverage / validate_access / get_manifest /
ping_health / create_export_job
```
大表链路（stock_minutes/etf_minutes）：create_export_job → 轮询 get_manifest → get_artifact → SHA256 → Parquet

### 1.2 鉴权（冻结）
- Header：`X-MCP-Key`（值从环境变量 MCP_API_KEY 读）
- key 绝不写入日志/异常/测试快照；HTTP debug 日志对 Header 脱敏
- 401 不重试（key错误）；429 按 Retry-After 重试
- key 缺失时 Client 初始化直接失败（fail-fast）
- key 不写入 sources_config.json（走环境变量）

### 1.3 endpoint（冻结，IP先行）
- 开发期：`MCP_ENDPOINT=https://124.223.159.234/mcp`（环境变量）
- 备案后：改 MCP_ENDPOINT=https://quantstudio.online/mcp（只改环境变量，不改代码）
- endpoint 从配置/环境变量读，**绝不硬编码**

### 1.4 TLS模式（冻结）
- 正式：tls_verify=true（备案切域名后）
- 开发IP模式：MCP_TLS_VERIFY=false + MCP_ALLOW_INSECURE_IP_DEV=true，仅限白名单IP+开发profile，启动打印安全警告（不打印key）
- **不以 verify=False 作为正式上线验收通过条件**

### 1.5 复权口径（冻结，修正原方案简化）
- 云端返回 raw OHLC + adj_factor
- 客户端计算：front = raw × adj_i / adj_latest；back = raw × adj_i / adj_earliest
- 禁止信任云端 open_back/close_back 等预计算列
- 分钟 bar 按交易日匹配日频 factor

### 1.6 authority模型（冻结）
- transport_source = mcp（数据传输通道）
- upstream_authority = xtquant（上游权威口径）
- daemon 验证 MCP metadata/lineage 表明价格来自 xtquant 口径
- **禁止简单 source in ("xtquant","mcp") 而不核查 lineage**

---

## 2. P2-0：契约收口（门禁，未通过禁止开始Adapter）

### P2-0 工作项
1. ✅ 进度报告状态冲突已修正（ZCode已完成）
2. ✅ 12工具冻结（见§1.1）
3. ✅ 鉴权Header冻结（见§1.2）
4. **IP协议探针**：对 https://124.223.159.234/mcp 实测并固化：
   - initialize 请求/响应格式、MCP-Protocol-Version、session id Header
   - notifications/initialized、tools/list、tools/call
   - 流式(SSE) vs 普通JSON响应、406触发条件
   - 401/403/429/5xx 响应结构
   - **写入 docs/mcp_migration/mcp_protocol_probe.md**
5. **Raw Landing 决策**：分钟Parquet必须走受控landing（DATA_ROOT/mcp_landing/），保存Manifest/SHA256/schema/lineage；小表可内存DataFrame
6. **QFQ在mcp_only下的处理**：明确QFQ orchestrator的price_source/detector在mcp_only如何不访问xtquant/tushare（可能需禁用QFQ或改用MCP数据算factor）

### P2-0 门禁
P2-0全部完成后（尤其协议探针），才能开始P2-1。

---

## 3. P2-1：MCP Client

**文件**：quantstudio/pipeline/mcp/client.py + errors.py + models.py + __init__.py

**职责**：
- MCP initialize / session管理 / notifications/initialized
- tools/list / tools/call
- 12工具封装（见§1.1）
- API key注入（X-MCP-Key，环境变量，不回显不记日志）
- HTTP/协议错误分类（401/403/406/429/5xx）
- retry（fetch_page/get_manifest/get_artifact幂等可重试；create_export_job需idempotency key）
- timeout
- Manifest校验 + SHA256 + Parquet流式下载
- TLS配置（开发IP模式显式区分）
- Header脱敏

**分层**：MCPClient（单次调用）+ MCPSourceAdapter（调_retry_with_backoff包装）。Client不直接继承Adapter。

**重试注意**：
- fetch_page/get_manifest/get_artifact：幂等可重试
- create_export_job：响应丢失不得盲目重复创建，需idempotency key或按request_id查已有job

---

## 4. P2-2：MCPSourceAdapter

**文件**：quantstudio/pipeline/sources/mcp_adapter.py

**实现**：fetch_table / get_last_date / supports_freq / supports_task / close

**fetch_table路由**：
- 小表：fetch_page循环游标 → 合并DataFrame → 返回 raw_df + metadata
- 分钟大表：create_export_job → get_manifest → get_artifact → SHA256 → Parquet分片
- **注意fetch_table返回完整DataFrame，超大分钟数据可能全量进内存**：P3前必须明确（小日期窗口调用 / Adapter内部分片 / 或扩展daemon为batch iterator——后者属公共API变更需单独立项）

**复权策略**：返回raw OHLC + adj_factor列（不计算，交给aligner）
**code格式**：接收600063.SH → metadata声明code_format（aligner按source查alignment_rules映射转裸数字）

---

## 5. P2-3：注册 + 独立mcp_only配置（不改默认生产配置）

**修改**：
- sources/__init__.py registry 加 "mcp": MCPSourceAdapter
- source_capabilities.py 加 mcp 能力声明
- config_lint.py 识别 mcp 源
- alignment_rules.json 加 mcp.<table> 映射（ts_code→code, trade_date/trade_time→time, vol→volume, pre_close→preClose, pct_chg→pctChg 等）

**新增（独立配置目录，不改默认）**：
```
config/profiles/mcp_only/
  ├── data_config.json       (type=duckdb, staging路径，不碰正式库)
  ├── sources_config.json    (只有mcp源，forbidden xtquant/tushare)
  ├── collector_tasks.json   (mcp优先)
  └── alignment_rules.json   (含mcp映射)
```
启动：`python -m quantstudio.pipeline.daemon --mode once --config-dir config/profiles/mcp_only`

**暂不修改**：config/collector_tasks.json（默认生产配置），直到mcp_only全部验收完成。

---

## 6. P2-4：daemon authority与复权协同

**4项必须处理**（原方案只提adj_factor分支，不够）：
1. MCP authority/lineage校验：metadata含lineage表明upstream=xtquant
2. 替换四价格表硬编码source guard（daemon.py:426-446）：区分transport_source(mcp)/upstream_authority(xtquant)，验证MCP lineage
3. 标准化MCP adj_factor_df（normalize_mcp_adj_factor_df：SH/SZ后缀、UTC→Asia/Shanghai、分钟按交易日连接、重复factor、缺失/非数字/<=0处理）
4. 禁止QFQ/per_trade_date绕过mcp_only（mcp_only下QFQ不访问xtquant/tushare）

**修改必须保持**：fetch→align→validate→write→watermark 顺序不变。
**禁止改动**：validator阈值、writer幂等、PIT语义、DuckDB schema、xtquant/tushare默认profile行为。

---

## 7. P3：渐进式验证（每阶段通过才进下一阶段）

### P3-1：协议smoke test
401/认证/initialize/tools/list/ping_health/describe_server/describe_dataset/fetch_page/session关闭

### P3-2：stock_daily staging端到端（临时DuckDB，不碰正式库）
MCP stock_daily → 字段映射 → adj_factor标准化 → aligner → validator → writer → staging DuckDB
验收：raw一致/主键无重复/front/back与P0黄金一致/不信任云端预计算/data_source=mcp/lineage=xtquant/watermark成功推进/重跑幂等/失败不fallback/不创建xtquant client

### P3-3：etf_daily
ETF专属：三位价格精度/ETF factor口径/code后缀/front-back/preClose-pctChg/validator行为不变

### P3-4：分钟Parquet分片
小时间范围：export job/manifest/shard顺序/sha256/schema fingerprint/concat hash/中断恢复/不全量驻留内存/daily factor按交易日连minute bar/无未来factor

### P3-5：财务PIT
balance/income/cashflow/fin_indicator/stock_daily_valuation
对抗样本：end_date到但ann_date未到/同报告期多次公告/修订/f_ann_date/重复/公告边界

### P3-6：mcp_only真正隔离验收（runtime证明）
monkeypatch XtquantAdapter/TushareAdapter/import tushare 全部拦截 → 运行mcp_only daemon
通过：daemon正常构造/stock_daily成功/无xtquant-tushare import/_resolve_source_chain只返回["mcp"]/隐藏任务只用MCP或标unsupported/QFQ不访问旧源/缺key fail-fast/MCP不可用不fallback

### P3-7：QFQ复权重算完整功能验证【最高优先级，用户明确要求】
验证MCP管线的QFQ复权重算与原管线完全一致。选有除权除息的样本（如某ETF/股票的除权日前后）：
1. **事件检测**：MCP数据的dividend信息能驱动qfq_event_discovery生成trigger（与原管线扫stock_dividend一致）
2. **因子捕获**：MCP的adj_factor能被qfq_fresh_capture使用（替代tushare_adj_factor）
3. **重锚计算**：用MCP raw+adj_factor重算，front=raw×adj_i/adj_latest，与xtquant口径一致
4. **质量门控**：front_exact_match/raw_unchanged/row_conservation/coverage_min全部通过
5. **水位协调**：hold_until_consistent——除权日未通过QFQ门控不推进水位
6. **对照**：MCP管线重算的复权价 vs 原xtquant管线，除权日前后逐字段一致
验收失败=管线不合格，必须修到一致。

---

## 8. 暂不批准（审核明确）

- 不改默认collector_tasks.json为全MCP
- 不直接覆盖客户正式DuckDB（用staging）
- 未经单表验证不扩展全表
- 不以verify=False作为正式TLS验收
- 测试通过不自动stage/commit/push

---

## 9. 治理与GitHub同步

P2/P3属框架层变更（Adapter/daemon authority/复权路由/配置校验/能力矩阵/客户端接入），必须：
本地任务书→实现→回归+黄金对比→更新文档→ZCode审核→更新进度报告→用户确认→才能commit/push
**不因测试通过自动同步GitHub。**

---

## 10. 预计文件

**新增**：
- quantstudio/pipeline/mcp/{__init__,client,errors,models}.py
- quantstudio/pipeline/sources/mcp_adapter.py
- config/profiles/mcp_only/{data_config,sources_config,collector_tasks,alignment_rules}.json
- tests/test_mcp_{client,adapter,protocol_contract,authority_lineage,only_profile,stock_daily_e2e,artifact_integrity,financial_pit}.py

**修改（小改）**：
- quantstudio/pipeline/sources/__init__.py
- quantstudio/pipeline/source_capabilities.py
- quantstudio/pipeline/config_lint.py
- quantstudio/pipeline/daemon.py（authority guard + adj_factor协同）
- config/alignment_rules.json（加mcp映射）
- config/mcp_dataset_requirements.json（12工具）

**任务书本地路径**：docs/mcp_migration/codebuddy-round2-P2-P3-mcp-client-adapter.md（本文件）

---

## 11. 完成后汇报

每个P2-x/P3-x阶段完成后单独汇报，等审核通过再进下一阶段。不一口气做完全部。
最终汇报含：协议探针/Client/Adapter/注册配置/authority协同/各P3阶段验证结果/回归结果/黄金对比/文档更新/拟提交范围。
