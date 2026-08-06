# MCP Cutover + QFQ 世代隔离框架修复设计 v2.4

> **版本**：v2.4（修订版，回应 v2.3 复核的两阶段 CAS 新事件边界遗漏 + 2 文字修正）
> **日期**：2026-08-05
> **性质**：QuantStudio 本地回测框架层正确性变更
> **状态**：阶段 B 本地实施中；B-3b.3 已独立复审通过，B-4 全量 staging 演练已本地完成，CodeBuddy 独立复审通过。正式库迁移与 Git 同步仍须分别明确确认。
> **canonical path**：本文档固定路径为 `docs/mcp_migration/mcp-cutover-design-v2.md`，**不随小版本改名**
> **v2.3 复核结论**：架构层通过，5 项机械核验通过；只剩两阶段 CAS 引入的新事件边界遗漏（P0）+ 2 文字修正
> **v2.4 修订**：v2.3 复核 P0（新 logical key 无法进 baseline + 占位/trigger 未同事务）+ `_norm_div_val` 函数名 + deferred 不持久化
> **本轮交付范围**：只修订本文档，不改代码/配置/数据库，不 stage/commit/push

## v2.4 变更摘要（相对 v2.3）

| 变更 | v2.3 | v2.4 |
|---|---|---|
| baseline applied hash nullable | `applied_payload_hash NOT NULL` | **允许 NULL**（语义：该 logical key 尚未成功应用过任何 payload；bootstrap 历史基线写当前 hash，新事件 NULL） |
| 新 logical key 进 baseline | UPDATE 占位返回空被误判"已应用"→ 新事件漏掉 | **UPDATE 占位失败 → INSERT 新 baseline 行**（applied=NULL, pending=新 trigger）`ON CONFLICT DO NOTHING RETURNING` |
| 占位与 trigger 创建 | 两步无显式事务（崩溃死锁：有 pending 无 trigger） | **同一显式事务** BEGIN/COMMIT/ROLLBACK；占位+trigger 整体提交或回滚 |
| 一致性审计 | 无 | 新增 3 条：pending 非空但 trigger 不存在 / 指向错 cutover / payload 不一致 |
| `_norm_div_val` 函数名 | 示例内部仍调 `_norm_div_val`（NameError） | 统一改 `norm_div_val` |
| deferred_latest_payload_hash | 提到"记为 deferred"但无列定义 | **不持久化 deferred**；B 完成后下轮从 stock_dividend 重新发现 C |

## v2.3 变更摘要（相对 v2.2，保留）

| 变更 | v2.2 | v2.3 |
|---|---|---|
| 水位总事务 | COMMIT 后再 _finish_cycle（自相矛盾） | **cycle finalize 纳入 watermark 总事务**（全部 intent + 水位 + finalize 同一 BEGIN/COMMIT/ROLLBACK）；held 路径同理 |
| CAS 断言 | `.rowcount == 1`（DuckDB 返回 -1，无效） | **全部改 `UPDATE/DELETE/INSERT ... RETURNING`**，校验返回行主键/ID（实测 DuckDB 1.5.5 `.rowcount==-1`） |
| baseline discover CAS | 文字说串行但 SQL 未原子占位 | **两阶段 CAS**：discover 用 `pending IS NULL ... RETURNING` 原子占位；commit 用 `pending_trigger_id+payload_hash` WHERE CAS；增 `pending_trigger_id` 列 |
| lease 回收 DELETE | `WHERE cycle_id=?`（owner 检查与 DELETE 有 TOCTOU） | DELETE 加 `owner/expires_at` 快照条件 + RETURNING；lease 获取与 cycle 创建同一事务 |
| trigger 合并事务 | 未明确多 trigger 合并处理 | 明确：同证券合并的全部 trigger+baseline+backfill 整体提交；禁部分 committed 无恢复证据 |
| generation 默认值 | DEFAULT 'xtquant-legacy'（易让漏传参数静默进错世代） | 新 schema **仅 NOT NULL 无业务默认**，迁移时显式回填，新 INSERT 必须显式传 generation |

## v2.2 变更摘要（相对 v2.1，保留）

| 变更 | v2.1 | v2.2 |
|---|---|---|
| 事务边界 | "同一连接同一事务"（未明确谁开事务） | **显式 BEGIN/COMMIT/ROLLBACK**；DuckDB 默认 autocommit，同连接≠同事务 |
| trigger 组合提交 | 未明确 baseline+backfill 同事务 | trigger committed + baseline applied + backfill resolve 必须**同一显式事务** |
| cycle lease 续租 | heartbeat 更新但 expires_at 不续期 | heartbeat 更新必须 **CAS 续租** expires_at；回收须 lease 超时+heartbeat 超时+owner stale 三条件 |
| baseline B→C 并发 | committed 时 applied=pending（B commit 会误登 C） | **trigger-bound CAS**：applied 取本 trigger 自身 payload_hash；同 logical key 单 pending 串行 |
| active cutover 切换 | DELETE+INSERT（未校验 expected old） | **expected-old CAS**：校验旧 active=预期值，同事务 old→superseded、新→active、指针切换 |
| 循环 import | `_norm_div_val` 从 event_discovery 导入 | `norm_div_val`+`dividend_payload_hash` 同移中立模块，单向依赖 |
| source_watermark 非 QFQ 表 | 全回填 xtquant-legacy（污染非价格源审计） | 非 QFQ 表用哨兵 `not-qfq-managed`/`not-applicable` |

## v2.1 变更摘要（相对 v2，保留）

| 变更 | v2 | v2.1 |
|---|---|---|
| active cutover 唯一约束 | `UNIQUE(price_source,status) WHERE status='active'`（DuckDB 不可执行） | `qfq_active_cutover` 独立表，`price_source` 为 PK |
| daemon 水位提交 | 谓"自开连接自动提交不破坏原子性"（**错误**） | 必须用事务版 `_advance_watermark_on_conn` 同连接同事务；补崩溃测试 |
| discovery baseline PK | `(source_generation, event_logical_key)` | `(cutover_id, event_logical_key)`；committed 后推进 applied hash |
| SQLite 辅助库 | 只放 observation/outbox（与 `_observe_factors` 同连接读 adj_factor 矛盾） | 完整独立库（MCP adj_factor/fund_adj/observation/outbox）；旧库全冻结只读 |
| source_generation | 允许 NULL | 全表 NOT NULL，统一 `xtquant-legacy`/`mcp-gen1`，cutover_id 哨兵 `legacy-xtquant-pre-cutover` |
| 生产断言 | `ASSERT`（DuckDB 非语法） | 事务内应用层断言（fetchone 比对 + raise） |
| cycle 单活 | "CAS + FileLock"（抽象） | 独立 `qfq_cycle_lease` 表 + cmdline 指纹列 |
| source_watermark | 未加 generation | 加 source_generation/cutover_id 审计列（PK 不变） |
| 决策点 | 称 5 个实为 6 个 | 修正为 6 个，并采纳审核建议答案 |

---

## 0. 设计原则与铁律

1. **三 P0 不可拆分**：CLI 数据源路由 + trigger 世代隔离 + MCP cutover baseline 必须先共同完成并 staging 验证，才能退役历史 trigger。已验证代码级证据：`trigger_id` 不含 `price_source`，discover 用 `INSERT OR IGNORE` 冲突跳过（`qfq_event_discovery.py:193-202`）；先退役旧 trigger 而未世代隔离，MCP 无法为同一分红事件生成新 trigger。
2. **生产库操作必须是最后一个受控阶段**。所有框架代码、测试、文档、staging 演练在动生产库前完成。
3. **禁止承诺"清障后水位立即恢复"**。水位只能由 MCP bootstrap 完成 + 同源 gate 通过后产生 `source=mcp` intent 的正常提交路径建立（`_commit_or_hold_watermarks`）。禁止直接 INSERT/UPDATE `source_watermark`。
4. **遵守框架层铁律#1**：本地实现 + 测试 + 文档 → 汇报 → 用户明确确认 → 才 stage/commit/push。
5. **不放宽阈值、不删 quarantine、不填造价格/日期**。

---

## 1. v1 审核阻断项核实结论

### 1.1 已核实属实的阻断项（5 P0 + 6 P1）

| 阻断项 | 核实结论 | 关键证据 |
|---|---|---|
| **P0-1** 阶段顺序不安全 | 属实。v1 把生产 cutover 放在 P1 恢复前 | v1 阶段 3 在阶段 4 前 |
| **P0-2** 通用迁移器不能做 NOT NULL DEFAULT | 属实。`_migrate_duckdb_columns`(`qfq_reanchor_schema.py:585-608`) 只 `ADD COLUMN <type>`，`_infer_col_type` 丢弃约束；DuckDB 不支持 `ADD COLUMN ... NOT NULL DEFAULT` 一步到位 | 代码逐行确认 |
| **P0-3** MCP bootstrap 后会重生全历史分红 trigger | **部分属实（比 v1 更精确）**。当前幂等阻止每轮重生，但：(a) trigger queue 有 **52723 个孤儿 trigger** 指向已不存在的 (code,ex_date)；(b) MCP cutover 后 `stock_dividend` 已重灌为 2181 行窄窗口；(c) trigger_id 纳入世代(v2)后 MCP 会为这 2181 行生成新 pending trigger（非 5.5 万但仍全表洪水）；(d) payload 口径漂移是隐性洪水阀门 | `scan_stock_dividend` 全表 SELECT 无 cursor 过滤(`:146-158`)；`observation_cursor` 纯记账从不被读做过滤(全仓仅 1 读取点作 summary) |
| **P0-4** SQLite observation 无世代隔离 | 属实且高危。977 万 observation 全 `revision_no=1` 单源基线；MCP 一旦写入会全部判 `revision_no=2` 伪修订；`alert_id=sha1(asset_type\|code\|factor_time\|revision_no)` 不含世代 | `qfq_factor_observation` PK 无世代(`:429-441`)；`record_observations` 比较逻辑无源归属(`:300-373`) |
| **P0-5** 其他状态表无世代 | 全部属实。**额外发现**：`_apply_trigger_outcome` 的 resolved SQL(`:480-484`) 不仅跨世代，连同世代内丢了 `range/freq/trigger_id`，一次 committed 抹平同一 (asset_type,code,table) 下所有区间/频率欠账——比审核描述更严重 | 逐表实读 DDL + 生产库 `PRAGMA table_info` |
| **P0-6** 退役范围写死 27834 漏 scheduled | 属实。生产库有 `scheduled=2`；应按 `price_source='xtquant' AND source_generation='legacy' AND status IN (非终态)` 语义查询 | `SELECT status,COUNT(*) FROM qfq_trigger_queue` |
| **P0-7** cycle lease 不足 | 属实。无 owner/heartbeat/lease 字段；daemon 崩溃 cycle 永久 started；`supersede_stale_intents` 不清 started 周期 intent | `qfq_cycle_run` DDL 无 owner 字段(`:318-338`)；全仓无 cycle 级 lease 检测 |
| **P1-1** trigger_id 需显式算法版本 | 属实。两套算法不能隐藏在模糊条件分支 | `trigger_id_of`(`:96-103`) 单一函数 |
| **P1-2** 不应复制两套 fetcher 分支 | 属实。daemon(`:227-233`) 与 CLI(`:194-195`) 已漂移 | 应提取共享工厂 |
| **P1-3** CLI watermark_advancer 事务契约 | 属实。当前 `None` 让 orchestrator 用同一连接 upsert，事务一致；贸然加回调可能拆事务 | 需先定义事务契约 |
| **P1-4** detector 硬编码 tushare 名 | 属实。`tushare_adj_factor_new` 等(`:283`)；MCP 配置已声明 `mcp_factor_detector` | detection_source vs price_source 概念需厘清 |
| **P1-5** cutover 状态机不足 | 属实。单 `status` 字段无合法集合/迁移规则 | 需 planned→prepared→validated→active 状态机 |
| **P1-6** DuckDB/SQLite 无法单事务原子 | 属实。状态横跨两库，需可恢复多阶段协议 | — |

### 1.1b v2 二次审核新增 P0 核实（v2.1 回应）

| v2 新增阻断项 | 核实结论 | 关键证据 |
|---|---|---|
| **v2-P0-1** partial unique 不可执行 | **属实**。DuckDB 对 `UNIQUE(...) WHERE` 直接 ParserException | 实测 `CREATE TABLE t(a VARCHAR,b VARCHAR,UNIQUE(a) WHERE b='active')` → `Parser Error: syntax error at or near "WHERE"` |
| **v2-P0-2** daemon 水位原子性判断错误（**v2 实质错误**） | **属实，v2 文档结论相反**。`_commit_or_hold_watermarks:645` 调 `self.watermark_advancer`（daemon 传 `writer.advance_watermark`，自开短连接自动提交），`:646-649` intent UPDATE 在原 conn。崩溃窗口真实存在：水位已提交、intent 仍 pending → 下一轮 supersede intent 但水位已提前推进 | `writers.py:674-687`（自开连接）；`qfq_resident_orchestrator.py:658-670`（回调分支）；**事务版 `_advance_watermark_on_conn` 已存在（`writers.py:699-712`），是现成正确工具** |
| **v2-P0-3** discovery baseline 状态推进契约不完整 | **属实**。(a) PK 缺 cutover_id（generation 复用/重试 cutover 会覆盖前次基线）；(b) payload revision committed 后 baseline 仍是旧 hash → 每轮反复判 A≠B 反复尝试；(c) `<payload_fields...>` 未逐字写死 | baseline 表设计缺 cutover_id/applied hash；payload 字段见 `:175-187`（13 字段 + `_norm_div_val`） |
| **v2-P0-4** SQLite factor/observation 路由矛盾 | **属实**。`_observe_factors` 同一 aux 连接读 `adj_factor`/`fund_adj` + 写 observation；新库只放 observation 会报 `no such table: adj_factor` | `_observe_factors` 同连接事务模型 |

### v2.1 对 v2 七个 P1 的修订回应

| v2 P1 | v2.1 修订 |
|---|---|
| 1. ASSERT 非 DuckDB 语法 | §5.2/5.3 改为**事务内应用层断言**（`fetchone()` 比对 + `raise CutoverPreconditionFailed` + `rollback()`） |
| 2. cycle 单活 CAS 抽象 | §4.3 新增独立 `qfq_cycle_lease` 表 + `owner_cmdline_hash` 列 |
| 3. source_generation 应 NOT NULL | §3.2.1 全表 NOT NULL，统一 `xtquant-legacy`/`mcp-gen1`，cutover_id 哨兵 `legacy-xtquant-pre-cutover` |
| 4. source_watermark 缺 generation | §3.2.5 新增 source_generation/cutover_id 审计列（PK 不变） |
| 5. 证据表加世代 | §3.2.5 `qfq_fresh_capture`/`qfq_reanchor_event` 加 source_generation/cutover_id |
| 6. backfill resolved 参数来源 | §3.2.4 明确精确 key 由 trigger unit 携带 + `_enqueue_pending` 返回对应 backfill key |
| 7. 决策点数量 | §9 修正为 6 个，采纳审核建议答案 |

### 1.2 两处审核未点明的额外风险（核查发现）

1. **`_apply_trigger_outcome` resolved SQL 过度 resolve（同世代内也错）**：当前 `:480-484` 的 WHERE 只有 `(asset_type, code, table_name, status IN(...))`，丢了 `freq/range_start/range_end/trigger_id`。即便同一世代内，同一 (asset_type,code,table) 下多条不同区间/频率的欠账会被一次 committed 一次性抹平。世代隔离方案若只加 `source_generation` 列而不收紧 range/freq/trigger_id，仍留"同世代跨区间误 resolve"的洞。
2. **977 万 observation 的单源临界态**：当前全表 `revision_no=1`、0 revision、0 alert。`record_observations` 的 `ORDER BY revision_no DESC LIMIT 1` 比较逻辑会把整批 MCP 因子判为 `revision_no=2` 伪修订并产生海量 pending alert，污染 anchor_state 的 blocked_revision 判定链。**世代隔离必须在 MCP 首次写入 observation 前落地**。

### 1.3 生产库关键现状（修复前快照基准）

| 表 | 行数/状态 | 说明 |
|---|---|---|
| `stock_dividend` | 2181 行，全 `data_source='mcp'`，仅 2026 近 6 月 | MCP 已重灌窄窗口 |
| `qfq_trigger_queue` | 54904 行（committed 27068 / pending 27834 / scheduled 2），全 `created_at=2026-08-01` | 52723 指向已不存在历史 (code,ex_date) |
| `qfq_factor_observation`(SQLite) | **9,779,604 行全 revision_no=1** | 单源临界态，引入第二源前必须隔离 |
| `qfq_factor_revision_alert`(SQLite) | 0 行 | 干净 |
| `qfq_pending_backfill` | resolved 492 / retryable_failed 20 / dead_letter 2 | 22 条非 resolved 会被误 resolve |
| `qfq_watermark_intent` | 4 pending（全 source=xtquant）+ 8 superseded | 死锁 intent |
| `qfq_cycle_run` | 3 个 started 僵尸周期（cyc_5ea430f1023e / cyc_1c8c299911a6 / cyc_74404ebcc214） | 死锁周期 |
| `source_watermark` | 四价格表 0 条 MCP 水位 | 水位缺失根因 |
| `qfq_fresh_capture` | 8335 applied + 2 captured，全 source=xtquant | — |
| `qfq_reanchor_event` | 7888 committed + 447 blocked，全 price_source=xtquant | — |

---

## 2. 修订后的阶段顺序（A→I，回应 P0-1）

```
A. 设计冻结（本文档，补齐全部契约）—— 当前阶段
B. 本地实现（一次完成 P0+P1，不动生产库）
C. 本地测试与文档（专项/集成/崩溃恢复/黄金回归）
D. staging 副本演练（迁移/中断/续跑/回退/MCP bootstrap/首轮 discover）
E. 生产执行前检查点（向用户汇报，取得明确执行确认）
F. 生产 cutover（停写/备份/执行/验证，预条件不一致即中止）
G. MCP baseline 与正常水位建立（正常协调路径，禁手工推进）
H. 审核与进度报告（ZCode 审核通过即更新唯一权威进度报告）
I. GitHub 同步确认门（最后取得用户明确确认才 stage/commit/push）
```

**停机窗口最小化**（回应 P0-1 的停机担忧）：
- B/C 阶段（开发+单测）不停生产 daemon；
- D 阶段制作一致性 staging 快照时短暂停止；
- E→F 正式 cutover 前再次停止、备份、校验。

---

## 3. P0 框架修复详细设计

### 3.1 P0-1：CLI fetcher 共享工厂（回应 P0-1 + P1-2 + P1-3 + P1-4）

**不复制两套分支，提取共享工厂**（回应 P1-2）。

**新增**：`quantstudio/pipeline/qfq_fresh_fetcher_factory.py`
```python
def build_qfq_fresh_fetcher(cfg: QFQOrchestratorConfig,
                            sources_cfg: dict,
                            main_db: str) -> FreshFetcher:
    """按 cfg.price_source 构造 fresh fetcher（daemon 与 CLI 共用，防漂移）。
    MCP-only fail-fast 保证：不 import xtquant、不连接 QMT。"""
    if cfg.price_source == "mcp":
        mcp_cfg = (sources_cfg.get("sources", {}).get("mcp")
                   or sources_cfg.get("mcp", {}))
        return McpFreshFetcher(mcp_cfg=mcp_cfg)
    elif cfg.price_source == "xtquant":
        return XtquantFreshFetcher()
    else:
        raise QFQConfigError(f"不支持的 price_source: {cfg.price_source}")
```

**改动**：
- `qfq_orchestrator_cli.py:189-200` `_make_orchestrator`：调用共享工厂（替换硬编码 `XtquantFreshFetcher()`）。
- `qfq_orchestrator_cli.py` argparse：新增 `--sources-dir`（默认与 `--config-dir` 同目录），读 `sources_config.json`。
- `daemon.py:219-239` `_qfq_orchestrator`：同样调用共享工厂（替换内联分支）。

**detection_source 标准化**（回应 P1-4）：明确 `detection_source` = 语义检测器（如 `mcp_factor_detector`），与 `price_source`（数据源）分离。MCP 世代的因子 trigger 不再标 `tushare_adj_factor*`。MCP 世代的 detection_source 纳入 trigger_id_v2（见 §3.2.2）。

**事务契约**（回应 P1-3，**v2.1 重大修订：修正 v2 实质错误**）：

> **v2 错误**：v2 文档曾写"daemon 传 `self.writer.advance_watermark`（自开短连接自动提交）不破坏原子性"——**这个结论是错的**，恰恰相反：自开短连接自动提交正是破坏原子性的原因。

**核实**（v2.1）：`_commit_or_hold_watermarks:645` 调 `self.watermark_advancer`（daemon 传 `writer.advance_watermark`，`writers.py:674-687`，**自开新短连接自动提交**）；`:646-649` 的 intent UPDATE 在原 `conn`。两连接两事务 → 崩溃窗口真实存在：
1. `advance_watermark` 在独立连接提交 source_watermark；
2. 进程在 intent 改 committed 前崩溃；
3. intent 仍 pending，下一轮 supersede；
4. 但水位**已提前推进**，违反 hold_until_consistent + "水位和 intent 同一事务" + "崩溃后不得提前推进水位"。

**v2.1 强制修订**（**v2.2 补强 P0-1：同一连接 ≠ 同一事务，必须显式 BEGIN**）：

> **v2.2 复核 P0-1**：v2.1 只写"同一连接、同一事务"，但 DuckDB 默认 autocommit。即使使用同一 conn，没有显式 `BEGIN TRANSACTION`，下列操作仍可能分别提交：验证 active cutover / 更新 source_watermark / intent 改 committed / cycle 改 finalized。"使用同一连接"不等于"同一事务"，不提供原子保证。

daemon 和 CLI 都**必须显式事务化**完成：校验 cutover 仍 active → 推进 source_watermark → intent 改 committed → cycle finalize。**不**继续把公共 `advance_watermark()` 作为 QFQ 编排回调。

**显式事务边界契约（v2.3 写死：cycle finalize 纳入 watermark 总事务，全部 intent 整体提交/回滚）**：

> **v2.2 缺陷**：v2.2 伪代码注释说 "cycle finalize 在外层同事务"，但 COMMIT 已执行 → finalize 实际在另一 autocommit 事务。仍可能出现 source_watermark+intent 已提交、cycle 仍 started/finalized 前状态。

```python
# 水位提交总事务（passed 分支）：全部 intent + 水位 + cycle finalize 同一 BEGIN/COMMIT/ROLLBACK
conn.execute("BEGIN TRANSACTION")
try:
    assert_cutover_is_active(conn, cutover_id)      # 校验 active cutover 仍生效
    for intent in cycle_intents:                    # 四张价格表的所有 intent，整体处理
        advance_watermark_on_conn(conn, intent.source, intent.table, intent.freq,
                                  intent.candidate_watermark, batch_id)  # 推进水位（不自动提交）
        mark_intent_committed(conn, cycle_id, intent.source, intent.table, intent.freq)  # intent 改 committed
    finish_cycle_on_conn(conn, cycle_id, status="finalized", summary=...)  # cycle finalize（同事务）
    conn.execute("COMMIT")
except Exception:
    conn.execute("ROLLBACK")
    raise
```
**要求**：
- **不能每个 intent 单独提交**；四张价格表水位整体提交或整体回滚；
- **held 路径同理**：全部 intent 改 held + cycle finalized_held 放入一个总事务；
- `_finish_cycle()` **不得**在该总事务 COMMIT 后再次单独执行；
- **测试矩阵补充（v2.3）**：第 1/2/3/4 个 intent 提交后分别崩溃，均整体回滚；全部 intent committed 后、cycle finalize 前崩溃，也整体回滚。

**trigger committed 组合事务（v2.2：同样必须显式事务化）**——以下三件事必须同一 `BEGIN/COMMIT`；**v2.3 补充：同证券合并的全部 trigger 整体提交**：
```python
conn.execute("BEGIN TRANSACTION")
try:
    # 同证券合并后的多个 trigger：全部 trigger + 对应 baseline + backfill 整体提交（禁部分 committed 无恢复证据）
    for t in merged_unit.triggers:
        # 1. trigger committed（v2.3：用 RETURNING 校验，见修订点 2/3）
        mark_trigger_committed(conn, t.trigger_id, t.event_id)
        # 2. discovery baseline applied hash 推进（绑定本 trigger 自身 payload_hash + pending_trigger_id CAS，见 §3.2.3 v2.3）
        advance_baseline_applied_on_commit(conn, cutover_id, t.event_logical_key,
                                           t.trigger_id, t.payload_hash)
        # 3. pending_backfill 精确 resolve
        resolve_backfill_precise(conn, t.backfill_key, t.trigger_id, gen)
    conn.execute("COMMIT")
except Exception:
    conn.execute("ROLLBACK")
    raise
```
- `_commit_or_hold_watermarks` 改用**事务版** `_advance_watermark_on_conn(conn, ...)`（`writers.py:699-712`，已存在，不 commit，事务边界交调用方）；
- daemon 不再传 `watermark_advancer=self.writer.advance_watermark`（`_qfq_orchestrator` 删除该参数）；
- CLI 保持不传回调。
- **新增故障注入测试**：(a) source_watermark 写入后、intent committed 前崩溃；(b) trigger committed 后、baseline/backfill 更新前崩溃；(c) 4 个 intent 逐个提交后崩溃；(d) 全 intent committed 后 cycle finalize 前崩溃 → 均整体回滚、无半提交。
- **CAS 断言一律用 `RETURNING`，不用 `.rowcount`（v2.3 修订点 2，见下方）**。

**CAS 断言统一用 RETURNING（v2.3 修订点 2，全局规则）**：

> **实测**（DuckDB 1.5.5）：`cursor.rowcount` 对 UPDATE/DELETE/INSERT **返回 -1**，不能用于判断是否更新了一行。所有 CAS 必须改用 `... RETURNING <主键/ID>` 并校验返回行。

全文档所有 `受影响行数 == 1` / `rowcount` 表述，统一替换为 **`RETURNING` 返回一行且主键/ID 与预期一致**。各场景范例：

| CAS 场景 | SQL 形态 | 校验 |
|---|---|---|
| baseline committed | `UPDATE ... WHERE pending_trigger_id=? AND pending_payload_hash=? RETURNING cutover_id, event_logical_key` | 返回一行=正常推进；空=幂等重放或被新 trigger 阻止 |
| lease 续租 | `UPDATE qfq_cycle_lease SET expires_at=? WHERE cycle_id=? AND owner_pid=? AND owner_cmdline_hash=? RETURNING cycle_id` | 返回 cycle_id==本次 → 续租成功；空 → 已失 lease，中止 |
| lease 获取 | `INSERT ... ON CONFLICT DO NOTHING RETURNING cycle_id` | 返回本次 cycle_id → 获取成功；空 → 获取失败（不再靠 INSERT 后普通 SELECT，防读到其他 owner 的 lease） |
| active 指针删除 | `DELETE FROM qfq_active_cutover WHERE price_source=? AND cutover_id=? RETURNING cutover_id` | expected_old 非空时必须返回一行 |
| lease 回收 | `DELETE FROM qfq_cycle_lease WHERE ... AND owner_pid=? AND owner_cmdline_hash=? AND expires_at=? AND expires_at<NOW() RETURNING cycle_id` | 返回 cycle_id=被回收 lease；空=期间已续租，不误删 |

**测试必须显式覆盖 `cursor.rowcount == -1`**（防实现者误用）：单测断言 DuckDB UPDATE 后 `.rowcount` 为 -1，并验证 RETURNING 路径正确工作。

**验收**：MCP 配置下断言 import xtquant 次数=0、连接 QMT 次数=0、构造的是 McpFreshFetcher、capture/event/intent 的 source 与 fetcher 一致。

### 3.2 P0-2 + P0-3：trigger 世代隔离 + discovery baseline（核心，回应 P0-2/P0-3/P1-1）

#### 3.2.1 显式 schema 迁移（回应 P0-2，不用通用迁移器）

**新增独立迁移函数**：`quantstudio/pipeline/qfq_schema_migration.py`（B-3b 已实现）

> **v2.4 B-3b 实现注记（supersedes 下方早期分步伪代码）**：实际实现**不**用
> `_migrate_duckdb_columns`/`ADD COLUMN`/`ALTER COLUMN SET NOT NULL` 分步迁移——因仅 ADD COLUMN
> 会把新列追加到末尾，得不到 target 冻结的精确列顺序。改为**重建全部发生物理变化的 9 张 QFQ 表
> + source_watermark（共 10 张）**：用 shadow 表 `<table>__b3b_v2` 按 target DDL 建全 →
> 映射复制 legacy 行（含历史回填）→ 校验 → **单一原子事务**内统一 RENAME swap
> （`<table>`→`<table>__b3b_legacy`，`<table>__b3b_v2`→`<table>`）→ DROP 临时 legacy →
> target fingerprint 回读 → COMMIT。任一步 ROLLBACK 恢复 COMPLETE_2_0。新建 4 张 B-3 表
> （空，不激活 cutover）；保留 qfq_bootstrap_item/trade_calendar 不重建（验证与 target 一致）。
> 下方早期"分步 ADD COLUMN + ALTER NOT NULL + v2 swap + sqlite_router"伪代码 **已 superseded**，
> 保留仅作历史参照；以本节实现注记 + `qfq_schema_migration.py` 真实代码为准。

**真实 API（B-3b 实现）**：

```python
SCHEMA_VERSION_FROM = "reanchor-2.0"
SCHEMA_VERSION_TO = "reanchor-2.1"
CONTENT_HASH_VERSION = "b3b-sha256-v1"

def migrate_reanchor_2_0_to_2_1(
    db_path, *, allowed_root, apply=False, failure_injection=None, report_path=None
) -> MigrationReport:
    """显式 2.0→2.1 migration runner。

    安全边界：正式生产库绝对硬拒绝（os.path.samefile 处理绝对/相对/../大小写/
    symlink/junction/hardlink/别名，打开 read-write 前；--allow-production 不绕过）；
    allowed-root 强制（db 必须是 root 子路径，等于根也拒绝）；
    状态门禁（仅 COMPLETE_2_0 apply / COMPLETE_2_1 幂等；partial/empty/unknown fail-closed；
    shadow 残留拒绝）。
    """
```

**CLI**：`python -m quantstudio.pipeline.qfq_schema_migration --db <staging> --allowed-root <root> [--apply] [--report <path>]`
（默认 dry-run 0 写；`--allow-production` 无效；report JSON 含 hashes_before/after、
content_hash_version, report_path, report_status/report_error, and validation_results.

**B-3b.3 frozen report lifecycle**:
- Reserve the final report path with `O_CREAT|O_EXCL|O_RDWR` before any database read-write connection.
- Write a valid `PENDING` JSON record immediately and retain the same file descriptor for the full call.
- Do not create publish temp files and do not call `os.replace`; an existing final path is never overwritten.
- Re-check report-file identity before opening the database read-write and again immediately before COMMIT.
- Delete a failed reservation only when the path still identifies the inode created by the current call.
- Frozen states: `PENDING`, `DRY_RUN_COMPLETE`, `ROLLED_BACK`, `MIGRATION_COMMITTED`, `ALREADY_CURRENT`, `FAILED_PRECHECK`.
- A post-COMMIT report/audit failure raises `MigrationCommittedReportError`; recover through a new COMPLETE_2_1 already-current audit report.

**历史回填映射**（逐表，见 b3r §13.4）：trigger_id_version=1、price_source=xtquant
（trigger/cycle/bootstrap/backfill/cursor）或保留原值（event/anchor/intent/fresh_capture/watermark）、
source_generation=xtquant-legacy、cutover_id=legacy-xtquant-pre-cutover；source_watermark 按 table_name
分类（QFQ 价格表→legacy 哨兵；非 QFQ→not-qfq-managed/not-applicable）。不重算历史 trigger ID。

**中断恢复**：13 故障点（FAILURE_POINTS）；前 12 失败后重开库=COMPLETE_2_0（原子回滚）；
第 13（COMMIT 后报告前）重跑=COMPLETE_2_1 already_current。真实 `os._exit` 子进程崩溃测试
验证 DuckDB 未提交事务自动回滚。

**SCHEMA_VERSION 升级**：`reanchor-2.0` → `reanchor-2.1`（`qfq_reanchor_schema.py:52`）。升级后 `bootstrap_completed`（`:696`）的版本校验判定旧 xtquant bootstrap 不匹配 → 强制重做。同步更新 `DDL_DUCKDB` / `DUCKDB_COLS` / `SCHEMA_CONTRACT_DUCKDB` 三处（B-3a 已完成）。

#### 3.2.1b 世代命名规范（v2.1 回应 P1-3，**v2.3：source_generation 无业务默认，仅 NOT NULL**）

**全表 source_generation 必须 NOT NULL**，统一命名（禁止混用 `legacy`/`xtquant-legacy`/`NULL`）：

| 字段 | 历史值（xtquant 世代，迁移时显式回填） | MCP 新值（新 INSERT 显式传） | 说明 |
|---|---|---|---|
| `price_source` | `'xtquant'` | `'mcp'` | 价格数据源名 |
| `source_generation` | `'xtquant-legacy'` | `'mcp-gen1'` | 世代标识 |
| `cutover_id`（历史行哨兵） | `'legacy-xtquant-pre-cutover'` | 实际 cutover_id（如 `cut_<uuid>`） | 禁 NULL |

**v2.3 修订（v2.2 复核建议）**：新 schema **仅 NOT NULL，不设业务默认值**（不再 `DEFAULT 'xtquant-legacy'`）。理由：默认值写成 legacy 会让漏传 generation 参数的 MCP 新行**静默进入错误世代**。迁移时由 migration 函数显式 UPDATE 回填历史值；新生产 INSERT 必须显式提供 generation（缺失即报错，fail-fast）。

**理由**：审核指出 v2 允许 `source_generation` 为 NULL 会造成实现歧义，且 NULL 进入复合主键/审计逻辑不可控。v2.1 全部 NOT NULL；v2.3 进一步去掉业务默认，迫使调用方显式传值。

#### 3.2.2 trigger_id 显式 v2 算法（回应 P1-1）

**新增**（`qfq_orchestrator_types.py`）：
```python
TRIGGER_ID_VERSION = 2

def trigger_id_v1(asset_type, code, effective_date_ms, detection_source, payload_hash) -> str:
    """历史算法（仅供历史行 sha1 不重算/审计比对）。"""
    raw = f"{asset_type}|{code}|{int(effective_date_ms)}|{detection_source}|{payload_hash}"
    return hashlib.sha1(raw.encode()).hexdigest()

def trigger_id_v2(asset_type, code, effective_date_ms, detection_source,
                  payload_hash, price_source, source_generation) -> str:
    """MCP 生产路径唯一可用算法。纳入世代 → 同一分红事件跨世代生成不同 ID。"""
    raw = (f"v2|{asset_type}|{code}|{int(effective_date_ms)}|{detection_source}"
           f"|{payload_hash}|{price_source}|{source_generation}")
    return hashlib.sha1(raw.encode()).hexdigest()
```

- `TriggerRecord` dataclass 新增 `trigger_id_version: int = 1`。
- **MCP 生产路径（price_source=mcp）只调 v2**；历史 xtquant trigger 保持原 v1 ID（source_generation='legacy' 不参与重算）。
- 更新所有依赖 trigger ID 黄金值的测试、`scripts/qfq_batch2_multiround.py`、对外文档。

#### 3.2.3 generation-specific discovery baseline（回应 P0-3 核心，**v2.1 重大修订：PK 改含 cutover_id + committed 后推进 applied hash + payload 字段写死**）

**问题精确化**：trigger_id 纳入 v2 后，MCP 世代首轮 discover 会为当前 `stock_dividend` 的 2181 行**全部生成新 pending trigger**（非 5.5 万，但仍全表洪水）。需建"事件发现基线"让首轮只为基线后新增/payload 变化的事件生成 trigger。

**新建 discovery baseline ledger**（DuckDB，**v2.1：PK 改含 cutover_id**；**v2.3：增 `pending_trigger_id` 列**；**v2.4：`applied_payload_hash` 允许 NULL**，支持 bootstrap 后新事件）：
```sql
CREATE TABLE IF NOT EXISTS qfq_discovery_baseline (
    cutover_id          VARCHAR NOT NULL,
    price_source        VARCHAR NOT NULL,
    source_generation   VARCHAR NOT NULL,
    event_logical_key   VARCHAR NOT NULL,        -- 见下方定义
    applied_payload_hash VARCHAR,                -- v2.4：允许 NULL（语义=该 key 尚未成功应用过任何 payload；bootstrap 历史基线写当前 hash，新事件 NULL）
    pending_trigger_id  VARCHAR,                 -- 当前占位 pending trigger 的 ID（committed 后清空）
    pending_payload_hash VARCHAR,                -- 当前占位 pending trigger 的 hash
    last_trigger_id     VARCHAR,                 -- 上次 committed 的 trigger_id（审计）
    applied_at          TIMESTAMP,
    baselined_at        TIMESTAMP NOT NULL,
    updated_at          TIMESTAMP NOT NULL,
    PRIMARY KEY (cutover_id, event_logical_key)
)
-- 辅助索引，按世代查询
-- CREATE INDEX idx_baseline_gen ON qfq_discovery_baseline(source_generation, event_logical_key)
```

**`applied_payload_hash` nullable 语义（v2.4）**：
- `NULL` = 该 logical key 尚未成功应用过任何 payload（bootstrap 后新增/晚到历史事件）；
- 非 NULL = 已成功应用该 hash（committed 推进后的值）；
- bootstrap 历史基线行仍写当前 hash（视为"bootstrap 时刻已应用"）；
- bootstrap 后新事件首行：`applied_payload_hash=NULL, pending_trigger_id=新 trigger, pending_payload_hash=当前 hash`；committed 后 applied 推进。

**logical key 定义 + payload hash 字段（v2.2：逐字写死，提取共享函数防 bootstrap 与 discover 漂移 + 防循环 import）**：

payload hash 字段必须与 `scan_stock_dividend()`（`qfq_event_discovery.py:175-187`）**逐字一致**，提取为共享函数 `dividend_payload_hash(...)`，bootstrap 与 discover 共用。

> **v2.2 P1-1 修订（防循环 import）**：v2.1 设计 `qfq_dividend_payload.py` 从 `qfq_event_discovery` 导入 `_norm_div_val`，而 `qfq_event_discovery` 又要导入 `dividend_payload_hash()` → 循环依赖。v2.2 把 `_norm_div_val`（改名 `norm_div_val`）和 `dividend_payload_hash` **一起移到中立模块** `qfq_dividend_payload.py`，该模块**只依赖底层** `qfq_orchestrator_types.payload_hash_of`；`qfq_event_discovery.py` 单向依赖它，不能反向 import。

```python
# quantstudio/pipeline/qfq_dividend_payload.py（新建中立模块，只依赖底层，无循环）
from quantstudio.pipeline.qfq_orchestrator_types import payload_hash_of

def norm_div_val(v):
    """从 qfq_event_discovery 迁移过来（原 _norm_div_val），逐字一致。"""
    # ...（原实现逐字搬迁）

def dividend_payload_hash(code, ex_date, record_date, ann_date, end_date,
                          cash_div_before_tax, cash_div_after_tax, cash_div,
                          stk_div, stk_bo_rate, stk_co_rate, div_rat, div_proc):
    """与 scan_stock_dividend:175-187 逐字一致的 payload hash。"""
    return payload_hash_of([
        code, ex_date, record_date,
        int(ann_date) if ann_date is not None else None,
        int(end_date) if end_date is not None else None,
        norm_div_val(cash_div_before_tax),
        norm_div_val(cash_div_after_tax),
        norm_div_val(cash_div),
        norm_div_val(stk_div),
        norm_div_val(stk_bo_rate),
        norm_div_val(stk_co_rate),
        norm_div_val(div_rat),
        norm_div_val(div_proc),
    ])
```
`scan_stock_dividend` 和 `establish_discovery_baseline` 都调这个函数，绝不各自重列字段，避免 hash 口径漂移。

| 问题 | 答案（v2.1） |
|---|---|
| stock dividend 的 logical key | `"stock_dividend\|<code>\|<ex_date_ms>"`（与 trigger_id 业务键部分同源，不含 detection_source/payload_hash，使同除权日 payload 变化可被识别） |
| payload_hash 是否属于唯一键 | **不属于** PK。PK = `(cutover_id, event_logical_key)`；区分 `applied_payload_hash`（已应用）与 `pending_payload_hash`（处理中） |
| 同一除权日内容变更如何识别 | discover 时查 baseline：`applied_payload_hash ≠ 当前 ph` → 生成 revision trigger，写 `pending_payload_hash`；一致 → 跳过 |
| 历史日期晚到数据如何发现 | baseline 未覆盖该 event_logical_key → 视为新增事件，生成 trigger |
| baseline 后首轮为何不生成全历史 trigger | MCP bootstrap 时对当前 `stock_dividend` 全表（2181 行）逐行写 `applied_payload_hash`；首轮 discover 时全覆盖且一致 → 全部跳过 → 净新增 0 |
| 多次 bootstrap / 重新 cutover 如何幂等 | PK 含 cutover_id，新 cutover 重建独立基线，不覆盖前次；同 cutover 内 `ON CONFLICT DO UPDATE applied_payload_hash`（**仅 baseline_building 状态允许**） |
| generation 更换后是否重建独立 ledger | 是。PK 含 cutover_id，新 cutover 完全独立；旧 ledger 冻结只读 |

**v2.3 核心：两阶段 CAS（discover 原子占位 + commit 绑定 trigger，防 B→C 并发误登 + 防旧 trigger 覆盖）**：

> **v2.2 缺陷**：v2.2 文字说"trigger-bound CAS"，但 SQL 的 WHERE 只有 `(cutover_id, event_logical_key)`，没绑定 pending trigger ID/payload hash/预期旧 applied；discover 侧也没原子占位 SQL，两个并发 discover 仍可能分别创建 B/C trigger。

**阶段 1：discover 原子占位 pending slot（v2.3）**——生成 trigger 前，先原子占用 pending slot，只有占位成功才创建 trigger：
```sql
-- discover 发现 (logical_key, payload_hash=ph) 需要生成 trigger（trigger_id 已算好）
UPDATE qfq_discovery_baseline
SET pending_trigger_id = ?,           -- 本次拟生成的 trigger_id
    pending_payload_hash = ?,         -- = ph
    updated_at = NOW()
WHERE cutover_id = ?
  AND event_logical_key = ?
  AND pending_trigger_id IS NULL      -- 仅当当前无 pending 才占位（单 pending 串行）
  AND applied_payload_hash IS DISTINCT FROM ?   -- = ph，已应用则无需 trigger
RETURNING cutover_id, event_logical_key;
-- 返回一行 → 占位成功 → 创建 trigger（INSERT OR IGNORE）
-- 返回空：
--   (a) applied == ph → 已应用，无需 trigger；
--   (b) 已有 pending B（当前是 C）→ 本轮不创建 C，C 保留为 deferred_latest_payload_hash（B 完成后下轮发现），不覆盖 B 的 pending slot
```

**阶段 2：commit 绑定本 trigger CAS（v2.3）**——committed 时 applied 取本 trigger 自身 payload_hash，WHERE 绑定 pending_trigger_id + pending_payload_hash，阻止旧 trigger 覆盖新 applied：
```sql
-- trigger B committed（B.trigger_id, B.payload_hash 已知）：
BEGIN;
UPDATE qfq_trigger_queue SET status='committed', completed_at=?, last_event_id=? WHERE trigger_id=?;
UPDATE qfq_discovery_baseline
SET applied_payload_hash = ?,          -- = B 自身 payload_hash（非读 pending 列）；v2.4 新事件首次 committed 时从 NULL 推进到 B.hash
    pending_trigger_id = NULL,
    pending_payload_hash = NULL,
    last_trigger_id = ?,
    applied_at = NOW(),
    updated_at = NOW()
WHERE cutover_id = ?
  AND event_logical_key = ?
  AND pending_trigger_id = ?           -- 必须等于 B.trigger_id（CAS，防旧 trigger 覆盖）
  AND pending_payload_hash = ?         -- 必须等于 B.payload_hash
RETURNING cutover_id, event_logical_key;
-- 返回一行 → 正常推进（含新事件首次 applied NULL→B.hash）；
-- 返回空：
--   (a) applied 已经 == B.hash → 幂等重放，视为成功；
--   (b) pending 已属于更新的 trigger → 阻止旧 trigger 回退 applied 状态，报错/告警
COMMIT;
```

**关键规则（v2.4 写死）**：
- `applied` 更新值来自 **committed trigger 自身 payload_hash**，绝不读 `pending` 列；
- discover 占位与 commit 推进都通过 WHERE 绑定 `pending_trigger_id`/`pending_payload_hash` 做真 CAS；
- **同一 logical key 单 pending 串行**：B 未完成时 C 不生成 trigger；
- **v2.4：不持久化 deferred_latest_payload_hash**——B pending 期间 C 不入任何列；B committed 后，下一轮 discover 从 `stock_dividend` **当前快照**重新发现 C（此时 baseline applied 已推进到 B，C 的 payload != applied → 自然生成 C trigger）。这样无需额外列/CAS/清理契约，最简。
- 禁止旧 trigger 覆盖已被新 trigger 推进的 applied 状态（commit WHERE 的 pending_trigger_id==B 阻止）；
- **所有 CAS 用 `RETURNING` 校验返回行，不用 `.rowcount`（v2.3 修订点 2）**。

这样 baseline 始终反映"当前已成功应用的状态"，B/C 并发不会误登，崩溃重放安全，新事件不漏。

**bootstrap 激活后禁止覆盖 baseline**（v2.1）：`establish_discovery_baseline` 的 `ON CONFLICT DO UPDATE` **只在 cutover.status='baseline_building' 时允许**；cutover 激活（active）后，bootstrap 不得覆盖已应用基线（否则抹掉 committed 后推进的 applied hash）。

**MCP bootstrap 建基线（不生成 trigger）**：
```python
def establish_discovery_baseline(conn, *, cutover_id, price_source, source_generation):
    """bootstrap（仅 baseline_building 状态）为当前 stock_dividend 全表建 baseline，不生成 trigger。
    applied_payload_hash = 当前 payload_hash（视为"bootstrap 时刻已应用"）。"""
    rows = conn.execute(
        "SELECT code, ex_date, record_date, ann_date, end_date, "
        "cash_div_before_tax, cash_div_after_tax, cash_div, stk_div, "
        "stk_bo_rate, stk_co_rate, div_rat, div_proc "
        "FROM stock_dividend WHERE div_proc='实施' AND ex_date IS NOT NULL").fetchall()
    for (code, ex_date, record_date, ann_date, end_date, cdbt, cdat, cd,
         sd, sbr, scr, dr, dp) in rows:
        key = f"stock_dividend|{code}|{int(ex_date)}"
        ph = dividend_payload_hash(code, ex_date, record_date, ann_date, end_date,
                                    cdbt, cdat, cd, sd, sbr, scr, dr, dp)
        conn.execute(
            "INSERT INTO qfq_discovery_baseline "
            "(cutover_id, price_source, source_generation, event_logical_key, "
            " applied_payload_hash, pending_trigger_id, pending_payload_hash, baselined_at, updated_at) "
            "VALUES (?,?,?,?,?,NULL,NULL,?,?) "
            "ON CONFLICT (cutover_id, event_logical_key) DO UPDATE SET "
            " applied_payload_hash=excluded.applied_payload_hash, updated_at=excluded.updated_at",
            [cutover_id, price_source, source_generation, key, ph, now, now])
```

**普通 discover 改造（v2.4：两阶段 CAS + 新事件 INSERT baseline + 占位与 trigger 创建同事务）**：

> **v2.3 缺陷**（v2.4 复核 P0）：(a) 对 baseline 不存在的新 logical key，UPDATE 占位必然返回空，被误判为"已应用"→ bootstrap 后新增/晚到历史事件漏掉；(b) 占位与 trigger INSERT 两步无显式事务，崩溃会产生"有 pending slot、无 trigger"的永久死锁。

```python
key = f"stock_dividend|{code}|{int(ex_date)}"
ph = dividend_payload_hash(...)  # 共享函数（norm_div_val 已移中立模块）
trigger_id = trigger_id_v2("STOCK", code, ex_date, "stock_dividend", ph, ps, gen)

# v2.4：占位 + trigger 创建必须在同一显式事务（崩溃整体回滚，不留"有 pending 无 trigger"死锁）
conn.execute("BEGIN TRANSACTION")
try:
    # A. 尝试占用【已存在】baseline 行的 pending slot
    reserved = conn.execute(
        "UPDATE qfq_discovery_baseline "
        "SET pending_trigger_id=?, pending_payload_hash=?, updated_at=NOW() "
        "WHERE cutover_id=? AND event_logical_key=? "
        "  AND pending_trigger_id IS NULL "
        "  AND applied_payload_hash IS DISTINCT FROM ? "   -- applied==ph 则已应用，无需 trigger
        "RETURNING cutover_id, event_logical_key",
        [trigger_id, ph, cutover_id, key, ph]).fetchone()

    if reserved is None:
        # B. UPDATE 返回空：要么 applied==ph（已应用，跳过），要么 baseline 行不存在（新事件）
        #    用 INSERT ... ON CONFLICT DO NOTHING RETURNING 为新 logical key 创建 pending baseline 行
        reserved = conn.execute(
            "INSERT INTO qfq_discovery_baseline "
            "(cutover_id, price_source, source_generation, event_logical_key, "
            " applied_payload_hash, pending_trigger_id, pending_payload_hash, baselined_at, updated_at) "
            "VALUES (?, ?, ?, ?, NULL, ?, ?, NOW(), NOW()) "   -- v2.4：applied=NULL（新事件尚未应用过）
            "ON CONFLICT (cutover_id, event_logical_key) DO NOTHING "
            "RETURNING cutover_id, event_logical_key",
            [cutover_id, ps, gen, key, trigger_id, ph]).fetchone()
        # reserved 非空 → 新 baseline 行创建成功（新事件进入 pending）
        # reserved 为空 → 行已存在但 UPDATE 未占位（即 applied==ph 已应用）→ 跳过，不创建 trigger

    if reserved is not None:
        # C. 占位成功 → 创建 trigger（INSERT OR IGNORE 同事务，幂等）
        inserted = conn.execute(
            "INSERT OR IGNORE INTO qfq_trigger_queue "
            "(trigger_id, asset_type, code, ...) VALUES (?, 'STOCK', ?, ...) "
            "RETURNING trigger_id",
            [trigger_id, code, ...]).fetchone()
        if inserted is None:
            # trigger 已存在（同 trigger_id）→ 校验已存在 trigger 与 pending slot 一致
            # 若不一致（trigger 属另一 cutover/generation 或 payload 不符）→ ROLLBACK 抛错
            assert_existing_trigger_matches_pending_slot(conn, trigger_id, cutover_id, key, ph)
    conn.execute("COMMIT")
except Exception:
    conn.execute("ROLLBACK")
    raise
```

**新事件流程保证（v2.4）**：
- bootstrap 后新增分红事件 / 晚到历史事件：baseline 行不存在 → UPDATE 空 → INSERT 新行（applied=NULL, pending=新 trigger）→ 创建 trigger → 不漏；
- 占位与 trigger INSERT 同事务 → 崩溃整体回滚 → 不留死锁；
- applied==ph（已应用）→ UPDATE 空 + INSERT ON CONFLICT 空（行已存在）→ reserved 为空 → 不创建 trigger。

**保留 observation_cursor**：继续作进度记账，**不**用于过滤（断言 C 已确认它本就不被读做过滤）。

#### 3.2.4 全链路 SQL 世代过滤（33+ 处，回应 P0-5）

**实现策略**：`QFQResidentOrchestrator` 新增 `_gen()` 返回 `(price_source, source_generation, cutover_id)`（从 cfg 取当前 active cutover）；辅助 `_gen_where(alias="t")` 返回 `(sql_fragment, params)`，避免 33 处散落硬编码。

**需改 SQL 完整清单**（核实后的精确列表）：

| # | 文件:行号 | 类型 | 环节 | 世代过滤方式 |
|---|---|---|---|---|
| 1 | qfq_event_discovery.py:193-195 | SELECT 探针 | discover 分红 | + discovery baseline 比对 |
| 2 | qfq_event_discovery.py:196-202 | INSERT OR IGNORE | discover 分红 | trigger_id v2 + 列补 price_source/source_generation/cutover_id |
| 3 | qfq_event_discovery.py:288-290 | SELECT 探针 | discover factor_new | + baseline + gen |
| 4 | qfq_event_discovery.py:291-299 | INSERT OR IGNORE | discover factor_new | v2 + 列补 |
| 5 | qfq_event_discovery.py:388-390 | SELECT 探针 | discover revision | + gen |
| 6 | qfq_event_discovery.py:391-399 | INSERT OR IGNORE | discover revision | v2 + 列补 |
| 7 | qfq_resident_orchestrator.py:200-203 | SELECT | recover_stale_in_progress | + gen WHERE |
| 8 | qfq_resident_orchestrator.py:216-220 | UPDATE | recover 重试 | + gen WHERE |
| 9 | qfq_resident_orchestrator.py:222-225 | UPDATE | recover 回 pending | + gen WHERE |
| 10 | qfq_resident_orchestrator.py:237-241 | SELECT | promote_scheduled_due | + gen WHERE |
| 11 | qfq_resident_orchestrator.py:250-252 | UPDATE | promote | + gen WHERE |
| 12 | qfq_resident_orchestrator.py:262-266 | SELECT | recover_pending_due | + gen WHERE |
| 13 | qfq_resident_orchestrator.py:275-277 | UPDATE | recover pending | + gen WHERE |
| 14 | qfq_resident_orchestrator.py:309-321 | SELECT | claim 领取 | + gen WHERE（核心） |
| 15 | qfq_resident_orchestrator.py:327-330 | UPDATE | claim 标 in_progress | + gen WHERE |
| 16 | qfq_resident_orchestrator.py:371-375 | SELECT JOIN | _already_committed | + gen WHERE |
| 17 | qfq_resident_orchestrator.py:417-418 | UPDATE | last_event_id 预写 | + gen WHERE |
| 18 | qfq_resident_orchestrator.py:474-477 | UPDATE | outcome committed | + gen WHERE |
| 19 | qfq_resident_orchestrator.py:480-484 | UPDATE | **backfill resolved（收紧！）** | 见下方专门设计 |
| 20 | qfq_resident_orchestrator.py:495-499 | UPDATE | outcome dead_letter | + gen WHERE |
| 21 | qfq_resident_orchestrator.py:508-511 | UPDATE | outcome retryable_failed | + gen WHERE |
| 22 | qfq_resident_orchestrator.py:517-519 | SELECT | _bump_attempt | + gen WHERE |
| 23 | qfq_resident_orchestrator.py:522-524 | UPDATE | _bump_attempt | + gen WHERE |
| 24 | qfq_reschestrator.py:573-576 | SELECT COUNT | gate orphan in_progress | + gen WHERE |
| 25 | qfq_resident_orchestrator.py:588 | SELECT COUNT | gate dead_letter | + gen WHERE |
| 26 | qfq_resident_orchestrator.py:601-602 | SELECT | gate committed 检查 | 按 trigger_id（隐含同世代） |
| 27 | qfq_resident_orchestrator.py:769-772 | SELECT | _classify_bootstrap_security | + gen WHERE |
| 28 | qfq_orchestrator_cli.py:231 | SELECT | cmd_status | 按 price_source 分组 |
| 29 | qfq_orchestrator_cli.py:319-323 | SELECT | cmd_show_dead_letter | + gen WHERE |
| 30 | qfq_orchestrator_cli.py:520-522 | SELECT | cmd_reopen | + gen WHERE |
| 31 | qfq_orchestrator_cli.py:533-536 | UPDATE | cmd_reopen | + gen WHERE |
| 32 | quality_audit.py:473-475 | SELECT COUNT | QfqDeadLetter | + gen WHERE |
| 33 | quality_audit.py:479-483 | SELECT COUNT | QfqPendingSla | + gen WHERE |
| 34 | quality_audit.py:486-489 | SELECT COUNT | QfqStaleInProgress | + gen WHERE |
| (extra) | scripts/qfq_batch2_multiround.py:135-139 | INSERT OR IGNORE | canary 注入 | trigger_id v2 |

**v2.4 新增：baseline pending slot 与 trigger 一致性审计（3 条，发现任一阻断 gate）**：
```sql
-- 审计 1：pending_trigger_id 非空但 trigger_queue 不存在对应 trigger（死锁残留）
SELECT COUNT(*) FROM qfq_discovery_baseline b
WHERE b.pending_trigger_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM qfq_trigger_queue t WHERE t.trigger_id = b.pending_trigger_id);
-- 预期 0；非 0 → 说明崩溃留下"有 pending slot 无 trigger"死锁 → 阻断 gate

-- 审计 2：pending_trigger_id 指向错误 cutover/generation
SELECT COUNT(*) FROM qfq_discovery_baseline b
JOIN qfq_trigger_queue t ON t.trigger_id = b.pending_trigger_id
WHERE b.cutover_id != <当前 active cutover_id>
  OR t.price_source != b.price_source
  OR t.source_generation != b.source_generation;
-- 预期 0；非 0 → 跨 cutover/generation 污染 → 阻断 gate

-- 审计 3：pending_payload_hash 与对应 trigger 的 payload_hash 不一致
SELECT COUNT(*) FROM qfq_discovery_baseline b
JOIN qfq_trigger_queue t ON t.trigger_id = b.pending_trigger_id
WHERE b.pending_payload_hash != t.payload_hash;
-- 预期 0；非 0 → 占位与 trigger payload 漂移 → 阻断 gate
```
此 3 条审计接入 `quality_audit._audit_qfq_orchestration`，发现任一非 0 即报 error 并阻断水位提交。

**_apply_trigger_outcome 的 backfill resolved SQL 收紧（额外发现 #1）**：当前 `:480-484` 丢了 range/freq/trigger_id。改为：
```sql
UPDATE qfq_pending_backfill SET status='resolved', resolved_at=?, updated_at=?
WHERE asset_type=? AND code=? AND table_name=? AND freq=?
  AND range_start=? AND range_end=? AND trigger_id=?
  AND price_source=? AND source_generation=?
  AND status IN ('pending','retryable_failed','blocked')
```
只 resolve 该 trigger 对应的精确区间，杜绝同/跨世代误抹平。

**backfill 精确 key 的参数来源（v2.1 回应 P1-6）**：`_apply_trigger_outcome` 当前不直接持有每条 backfill 的精确 range_start/range_end。v2.1 明确参数来源——**不临时重算 range**（价格区间可能已变化，重算会错）：
1. **由 trigger unit 携带**：`_claim_and_merge` 返回的 unit 已含 `effective_dates`；reanchor 成功后，`_apply_trigger_outcome` 从本次重锚实际覆盖的区间（由 `_reanchor_security` 返回 `daily_range/minute_range`）构造精确 key；
2. **`_enqueue_pending` 返回对应 backfill key**：`_enqueue_pending` upsert 时返回 `(asset_type, code, table_name, freq, range_start, range_end)`，存入 unit，committed 时按此精确 key resolve；
3. **兜底**：若 unit 无携带 key，按 `trigger_id + table + generation` 查询待 resolve 的 backfill 行（`SELECT ... WHERE trigger_id=? AND price_source=? AND source_generation=?`），禁止按宽松 (asset_type,code,table) 批量 resolve。

#### 3.2.5 需变更主键表的 v2 迁移（回应审核重点检查项 3）

通用 `_migrate_duckdb_columns` 只能补列，**不能改 PK**。下列表需要把 generation 纳入隔离语义，其中改 PK 的必须建 v2 临时表原子 swap。每张表的统一迁移步骤：

```
1. CREATE TABLE <table>_v2 (... 新 PK 含 source_generation/cutover_id ..., CHECK 约束);
2. INSERT INTO <table>_v2 SELECT ..., <回填世代值> FROM <table>;   -- 历史数据映射
3. 行数校验：SELECT COUNT(*) FROM <table>_v2 == 原表;
4. 主键唯一性校验：SELECT dup_count == 0;
5. BEGIN; ALTER TABLE <table> RENAME TO <table>_legacy;
   ALTER TABLE <table>_v2 RENAME TO <table>; COMMIT;   -- 原子 swap
6. 回读 _verify_contract(<table>);
7. 记录 swap 摘要到 migration report;
中断恢复：重跑时若 <table>_v2 已存在且行数一致 → 跳到 step 5。
回退：RENAME <table> ← <table>_legacy（需在 migration report 记录原表名）。
```

**需 v2 swap 的表**：

| 表 | 当前 PK | v2 PK | 说明 |
|---|---|---|---|
| `qfq_pending_backfill` | (asset_type,code,table_name,freq,range_start,range_end) | + price_source, source_generation | 防 _enqueue_pending 跨世代覆盖 |
| `qfq_observation_cursor` | (detector_name, asset_type) | + price_source, source_generation | 防 MCP 覆盖旧 CLI cursor |
| `qfq_anchor_state` | (asset_type, code, price_source) | + source_generation | 不同 MCP generation 不共用 anchor |
| `qfq_watermark_intent` | (cycle_id, source, table_name, freq) | + source_generation, cutover_id | intent commit 时校验 cutover 仍是 active |

**仅补列、不改 PK 的表**（v2.1：source_generation 全部 NOT NULL，不再允许 NULL；历史值统一 `xtquant-legacy`/`mcp-gen1`）：

| 表 | 新增列（price_source/source_generation 均 NOT NULL DEFAULT；cutover_id 用哨兵禁 NULL） |
|---|---|
| `qfq_trigger_queue` | price_source, source_generation, cutover_id, retired_at, retire_reason |
| `qfq_cycle_run` | price_source, source_generation, cutover_id（绑定不可变世代） |
| `qfq_bootstrap_run` | price_source, source_generation, cutover_id |
| `qfq_fresh_capture` | source_generation, cutover_id（v2.1 回应 P1-5：证据表加世代） |
| `qfq_reanchor_event` | source_generation, cutover_id（v2.1 回应 P1-5：event 已有 price_source，补 generation） |
| `source_watermark` | source_generation, cutover_id（v2.1 回应 P1-4：**审计列，PK 不变** `(source,table_name,freq)`，保持每 source 唯一当前水位） |

**source_watermark 世代审计契约（v2.2 回应 v2.1-P1-2：非 QFQ 表回填规则）**：
- 保持 PK `(source, table_name, freq)` 不变（维持每个 source 的唯一当前水位）；
- 新增 `source_generation`/`cutover_id` 审计列；
- **回填规则（v2.2 写死）**：仅四张 QFQ 价格表（stock_daily/stock_minutes/etf_daily/etf_minutes）使用 QFQ 世代语义，回填 `xtquant-legacy`/`legacy-xtquant-pre-cutover`；**其他表（MCP 财务/基础表、akshare 等历史源、非 QFQ 数据集）用明确哨兵** `source_generation='not-qfq-managed'`、`cutover_id='not-applicable'`，不污染非价格源审计；
- 提交时（显式事务内，用 `_advance_watermark_on_conn`，见 §3.1 v2.2）验证：
  - intent 的 `cutover_id` 当前仍 active；
  - candidate watermark 不倒退（≥ 已提交水位）；
  - source_watermark 旧 generation 是否允许被当前 generation 接管（cutover 状态机授权）。
- 否则将来 mcp-gen2 无法证明水位来自哪个 cutover。

> **B-3a 落地（2026-08-05）**：source_watermark 最终 2.1 契约冻结为 **8 列**（source_generation/cutover_id 均 NOT NULL 无 DEFAULT，PK 不变 `(source,table_name,freq)`）。DDL/DDL/DUCKDB_COLS/SCHEMA_CONTRACT/writers.py/QFQ schema 共同引用 `qfq_schema_contracts.SOURCE_WATERMARK_2_1_DDL`（单一真相源，禁止两份手写）。三条写入路径（writers.py advance_watermark + _advance_watermark_on_conn + qfq_resident_orchestrator._advance_watermark）全改 8 列显式 INSERT，写保守 pre-cutover 哨兵（`pre_cutover_generation(table, source)`：QFQ 价格表→xtquant-legacy/legacy-xtquant-pre-cutover；非 QFQ 表→not-qfq-managed/not-applicable；**source 保留真实值不改写**）。这是 B-3a 扩列后的静态兼容桥：不查 active cutover、不实现动态世代接管。B-5 把静态哨兵替换为动态 active generation/cutover；B-6 激活 mcp/mcp-gen1/<active-cutover-id>。所有既有表 NOT NULL 新列（trigger_id_version=1、price_source/source_generation/cutover_id 哨兵）同步做静态 pre-cutover 写入适配，使现有 daemon/CLI/orchestrator/测试在新 2.1 空库上继续运行。状态识别/init 闸门/指纹冻结详见 `docs/mcp_migration/b3r-schema-exploration.md` §12 与 `quantstudio/pipeline/qfq_schema_contracts.py`。

### 3.3 P0-4：SQLite 物理隔离（回应 P0-4，审核重点检查项 2）

**方案 A：MCP generation 独立辅助库**（推荐，避免重建 977 万行）。

**路径配置与路由契约**（不散落硬编码）：
- `config/profiles/mcp_only/qfq_aux_paths.json`（新建，集中配置）：
  ```json
  {
    "default": "data/qfq_aux.db",
    "generations": {
      "mcp-gen1": "data/qfq_aux_mcp_gen1.db",
      "xtquant-legacy": "data/qfq_aux.db"
    }
  }
  ```
- `AuxDbRouter`（新建，`qfq_aux_router.py`）：按 `source_generation` 路由到对应辅助库；daemon 与 CLI 调用同一 router（防漂移）。
- `cutover_id` → 固定辅助库映射：cutover-init 时写入 `qfq_source_cutover.aux_db_path`，不可变。
- **辅助库不存在时 fail-closed**（不自动创建空库放行）：必须由 MCP bootstrap 显式 `init_sqlite_schema` 建立。

**factor 原始存储与 observation 状态库（v2.1 重大修订：完整独立库，回应 v2-P0-4）**：

> **v2 缺陷**：v2 计划 MCP 辅助库只放 observation/outbox，但 `_observe_factors` 用**同一 aux 连接**读 `adj_factor`/`fund_adj` + 写 observation。新库只有 observation 会报 `no such table: adj_factor`。

**v2.1 强制修订**：MCP 世代使用**一个完整的、独立的** `qfq_aux_mcp_gen1.db`，同时保存：
- MCP `adj_factor`（由 MCP adapter/MCPFactorRefresher 重新拉取写入）
- MCP `fund_adj`（同上）
- MCP `qfq_factor_observation`
- MCP `qfq_factor_revision_alert`
- MCP 世代游标/审计状态

旧 `qfq_aux.db` **全部冻结只读**，不再作为 MCP 因子源。理由（回应 v2-P0-4 + 采纳决策点 4 答案）：
1. 不再复用任何 xtquant/tushare 历史因子（MCP 唯一数据源原则）；
2. 与当前 `_observe_factors` 的同连接事务模型一致（不引入 FactorSnapshotStore/ObservationStore 双库重构，风险更低）；
3. MCP bootstrap 从零建基线，observation revision_no=1，不与旧 977 万行比较 → 不产生伪 revision。

**MCP 因子入库路径**（v2.1 拍板，采纳决策点 4）：MCP adapter / MCPFactorRefresher 重新拉取 adj_factor/fund_adj，写入新辅助库（`_inject_adjfactor` 路由到当前世代辅助库）。禁止读取旧 `qfq_aux.db` 因子作为 MCP 基线。

**旧库处置**：`qfq_aux.db` 冻结只读保留（不改名），作为历史证据可查询；MCP 世代绝不读写它。

**alert_id v2**：`sha1(asset_type|code|factor_time|revision_no|source_generation)`，防跨世代冲突。

**备份/验证/回退**：
- 备份：`qfq_aux_mcp_gen1.db` 随 cutover 备份；旧库冻结前 checksum；
- 验证：新库 observation 行数 = MCP 全市场证券数 × 因子时间点数（在 staging 校准）；
- 回退：删除 `qfq_aux_mcp_gen1.db`，router 回退到旧库（旧库只读未污染，可回退）。

### 3.4 P0-3 baseline + P1-5/P1-6：cutover 状态机（回应审核重点检查项 4，**v2.1 修订：partial unique 改独立表**）

> **v2-P0-1 修订**：v2 设计 `UNIQUE(price_source,status) WHERE status='active'`，但 **DuckDB 不支持 partial unique 约束**（实测 ParserException `syntax error at or near "WHERE"`）。v2.1 改用独立 `qfq_active_cutover` 表，语义更清晰。

**新建** `qfq_source_cutover`（DuckDB，去掉不可执行的 partial unique）：
```sql
CREATE TABLE IF NOT EXISTS qfq_source_cutover (
    cutover_id     VARCHAR PRIMARY KEY,
    price_source   VARCHAR NOT NULL,
    source_generation VARCHAR NOT NULL,
    cutover_time   TIMESTAMP NOT NULL,
    price_snapshot_version  VARCHAR,   -- 替代证据（见下）
    factor_snapshot_version VARCHAR,
    baseline_version VARCHAR NOT NULL,
    schema_version VARCHAR NOT NULL,
    config_hash    VARCHAR,
    aux_db_path    VARCHAR,             -- 该世代辅助库路径
    status         VARCHAR NOT NULL,    -- 见状态机
    evidence_path  VARCHAR,             -- 验证证据位置
    created_at     TIMESTAMP NOT NULL,
    updated_at     TIMESTAMP NOT NULL
    -- 无 partial unique（DuckDB 不支持）
)
```

**新建** `qfq_active_cutover`（v2.1：独立表，`price_source` 为 PK，保证每 price_source 最多一个 active）：
```sql
CREATE TABLE IF NOT EXISTS qfq_active_cutover (
    price_source   VARCHAR PRIMARY KEY,   -- 每 price_source 最多一个 active cutover
    cutover_id     VARCHAR NOT NULL,
    activated_at   TIMESTAMP NOT NULL,
    FOREIGN KEY (cutover_id) REFERENCES qfq_source_cutover(cutover_id)
)
```
- **`baseline_validated → active` 转移（v2.2：expected-old CAS，9 步完整事务）**——不能无条件 DELETE 当前 active：
  ```python
  conn.execute("BEGIN TRANSACTION")
  try:
      # 1. 锁定/读取当前 active 指针
      cur = conn.execute("SELECT cutover_id FROM qfq_active_cutover WHERE price_source=?",
                         [price_source]).fetchone()
      cur_active = cur[0] if cur else None
      # 2. 验证 cur_active == 调用方预期值（expected_old），或确认无 active；不一致 → ROLLBACK
      if cur_active != expected_old:
          raise CutoverCASFailed(f"active={cur_active} != expected {expected_old}")
      # 3. 验证新 cutover.status == 'baseline_validated'
      st = conn.execute("SELECT status FROM qfq_source_cutover WHERE cutover_id=?",
                        [new_cutover_id]).fetchone()
      if not st or st[0] != 'baseline_validated':
          raise CutoverCASFailed("新 cutover 非 baseline_validated")
      # 4. 验证新 cutover 证据/schema/config/aux DB 全部通过（应用层校验）
      verify_cutover_evidence(conn, new_cutover_id)
      # 5. 旧 source_cutover.status → superseded（若有旧 active）
      if cur_active is not None:
          conn.execute("UPDATE qfq_source_cutover SET status='superseded' WHERE cutover_id=?",
                       [cur_active])
      # 6. 删除 expected old active 指针（v2.3：CAS WHERE cutover_id=expected_old + RETURNING，不用 .rowcount）
      if expected_old is not None:
          row = conn.execute(
              "DELETE FROM qfq_active_cutover WHERE price_source=? AND cutover_id=? "
              "RETURNING cutover_id", [price_source, expected_old]).fetchone()
          # row 非空且 cutover_id==expected_old → 删除成功；空 → expected_old 已不在（已被他改），ROLLBACK
          if row is None:
              raise CutoverCASFailed("expected_old active 指针已不存在")
      # 7. 插入新 active 指针
      conn.execute("INSERT INTO qfq_active_cutover VALUES (?, ?, NOW())",
                   [price_source, new_cutover_id])
      # 8. 新 source_cutover.status → active
      conn.execute("UPDATE qfq_source_cutover SET status='active' WHERE cutover_id=?",
                   [new_cutover_id])
      # 9. 回读验证唯一 active（v2.3：仍用 COUNT，这是读校验非 CAS，COUNT 不受 rowcount 影响）
      cnt = conn.execute("SELECT COUNT(*) FROM qfq_active_cutover WHERE price_source=?",
                         [price_source]).fetchone()[0]
      assert cnt == 1
      conn.execute("COMMIT")
  except Exception:
      conn.execute("ROLLBACK"); raise
  ```
  任何预期值不一致必须 rollback，**不能无条件删除当前 active**。
- 旧 active 被 supersede（步骤 5）；新 active INSERT（步骤 7）；两者同事务（原子）；
- daemon 启动读 `qfq_active_cutover` 获取当前 active cutover_id，校验配置 generation 匹配。

**cutover 状态转移表（完整）**：

| 当前状态 | 允许动作 | 下一状态 | 失败状态 | 是否允许 daemon 运行 MCP 任务 |
|---|---|---|---|---|
| `planned` | 建立计划 | `prepared` | `failed` | 否 |
| `prepared` | schema 迁移 + 辅助库准备 | `baseline_building` | `failed` | 否 |
| `baseline_building` | MCP bootstrap + discovery baseline | `baseline_validated` | `failed` | 否 |
| `baseline_validated` | 用户/门控显式激活 | `active` | `failed` | 否（需显式激活） |
| `active` | 正常运行 | `superseded`（被新 cutover 替代） | `failed` | **是** |
| `failed` | 修复/回退 | `prepared` / `rolled_back` | — | 否 |
| `rolled_back` | 保留审计 | — | — | 否 |
| `superseded` | 保留审计 | — | — | 否（新 active 接管） |

**强制规则**：
- 每个环境同一 `price_source` **同时最多一个 active**（v2.1：由独立表 `qfq_active_cutover` 的 PK 保证，不再用 partial unique）；
- active 切换用 `qfq_active_cutover` 表的 CAS（DELETE+INSERT 同事务，防并发）；
- daemon 启动时校验配置 `source_generation` 与 `qfq_active_cutover` 指向的 active cutover 一致，不匹配 **fail-closed**；
- `cutover-init` 不能直接 active（必须经 baseline_validated → 用户显式激活）；
- **`baseline_validated → active` 这一步需要用户确认**（cutover 状态机中唯一的人工门）。

**snapshot version 替代证据**（回应 P1-5：MCP 可能无法提供冻结快照版本）：用组合证据：
- factor snapshot：max-time + row count + content hash（对 `adj_factor`/`fund_adj` 关键列做 sha256）；
- price snapshot：时间边界（min/max time）+ row count + 关键列 hash；
- manifest hash：bootstrap 产物清单的整体 hash。
写入 `price_snapshot_version`/`factor_snapshot_version`/`evidence_path`，不依赖人工填写字符串。

**DuckDB/SQLite 多阶段协议（回应 P1-6）**：
```
cutover=prepared
  → SQLite 基线完成（MCP aux 库 init + 首批 observation）
  → DuckDB schema/状态迁移完成（v2 swap + 列回填）
  → 两边一致性校验（cutover_id 一致、世代值一致、行数校准）
  → cutover=baseline_validated
  → 用户显式激活
  → cutover=active → daemon 才允许领取 MCP trigger
```
每步幂等、支持中断续跑（cutover 状态 + 各步 checkpoint 落 `qfq_source_cutover.evidence_path`）。

---

## 4. P1：started 周期恢复契约（回应 P0-7，生产 cutover 前完成）

### 4.1 owner 身份强校验（不只 PID，**v2.1 补 owner_cmdline_hash**）

`qfq_cycle_run` 新增列（迁移走显式 migration 函数）：
`owner_pid BIGINT, owner_create_time TIMESTAMP, owner_exe VARCHAR, owner_cmdline_hash VARCHAR（v2.1 新增：稳定的命令指纹，否则 cmdline 多维校验无法与持久化身份比对）, owner_instance_token VARCHAR, owner_kind VARCHAR（daemon/reconcile_cli/bootstrap_cli）, owner_host VARCHAR, heartbeat_at TIMESTAMP, lease_expires_at TIMESTAMP`。

**owner 存活判定**（复用现有 daemon 身份校验 `daemon_process.py` 模式）：pid + create_time + exe + **owner_cmdline_hash**（持久化命令指纹） + instance_token 多维校验。

**psutil.AccessDenied 必须 fail-closed**：不能据此判 owner 已死（可能只是权限不足）。

### 4.2 heartbeat 更新点（回应 P0-7，**v2.2：heartbeat 必须同事务 CAS 续租 lease**）

> **v2.1 缺陷**：只规定更新 `qfq_cycle_run.heartbeat_at`，没规定同步延长 `qfq_cycle_lease.expires_at`。竞态：全市场 MCP cycle 合法运行数小时，heartbeat 持续更新，但 lease expires_at 不续期 → 新进程删除过期 lease → 两 cycle 同跑。

- `_set_cycle_phase` 更新 heartbeat；
- **长循环内周期更新 heartbeat**：每处理 N 个 security、每隔固定秒数、fresh fetch 长操作期间；
- **每次 heartbeat 更新必须同事务 CAS 续租 lease**（v2.2 写死）：
  ```sql
  -- 续租（v2.3：用 RETURNING 校验返回 cycle_id==本次，不用受影响行数/.rowcount）
  BEGIN;
  UPDATE qfq_cycle_run SET heartbeat_at=?, lease_expires_at=? WHERE cycle_id=? AND owner_pid=?
    RETURNING cycle_id;
  UPDATE qfq_cycle_lease
  SET expires_at=NOW()+INTERVAL '<lease>' SECOND
  WHERE price_source=? AND source_generation=? AND cycle_id=?
    AND owner_pid=? AND owner_cmdline_hash=?
  RETURNING cycle_id;
  -- v2.3：应用层断言两条 RETURNING 都返回 cycle_id==本次，否则 ROLLBACK 并中止本周期（不用 .rowcount）
  COMMIT;
  ```
- heartbeat 更新失败明确**中止**（非降级）。

### 4.3 单活 cycle 原子契约（v2.3 修订：lease 续租 + owner/expires 快照回收 + RETURNING + 获取与 cycle 创建同事务）

> **v2.2 缺陷**：(a) lease 回收 SQL `WHERE cycle_id=?` 与 owner 检查之间有 TOCTOU 窗口；(b) 获取后用普通 SELECT 查 cycle_id，可能读到其他 owner 的 lease；(c) 获取 lease 与创建 cycle 非同事务，崩溃会留下无 cycle 的 lease。

**新建** `qfq_cycle_lease`（DuckDB）：
```sql
CREATE TABLE IF NOT EXISTS qfq_cycle_lease (
    price_source      VARCHAR NOT NULL,
    source_generation VARCHAR NOT NULL,
    cycle_id          VARCHAR NOT NULL,
    owner_pid         BIGINT NOT NULL,
    owner_cmdline_hash VARCHAR NOT NULL,
    acquired_at       TIMESTAMP NOT NULL,
    expires_at        TIMESTAMP NOT NULL,
    PRIMARY KEY (price_source, source_generation)  -- 每世代最多一个活跃 lease
)
```

**owner-aware 回收（v2.3：DELETE 加 owner/expires_at 快照 CAS + RETURNING）**——把读取到的旧 lease 信息作为 DELETE 条件，消除 TOCTOU：
```python
# 回收前，逐条校验过期 lease 的 owner 是否真失活
stale_leases = conn.execute(
    "SELECT cycle_id, owner_pid, owner_cmdline_hash, expires_at FROM qfq_cycle_lease "
    "WHERE price_source=? AND source_generation=? AND expires_at < NOW()").fetchall()
for cycle_id, pid, cmdline_hash, exp_at in stale_leases:
    alive = owner_alive(pid, cmdline_hash)  # 强身份多维校验（§4.1）
    # AccessDenied → fail-closed，不回收（可能只是权限不足）
    # alive=True → 该 lease 虽过期但 owner 活着 → 不回收（可能正卡在长 IO）
    # alive=False 且 heartbeat 也超时 → 才回收
    if not alive and heartbeat_also_stale(cycle_id):
        # v2.3：DELETE 用读取到的 owner/expires_at 快照作 CAS 条件 + RETURNING
        # 若期间 owner 已续租（expires_at 已变），DELETE 返回空，不误删活跃 lease
        row = conn.execute(
            "DELETE FROM qfq_cycle_lease "
            "WHERE price_source=? AND source_generation=? AND cycle_id=? "
            "  AND owner_pid=? AND owner_cmdline_hash=? AND expires_at=? AND expires_at < NOW() "
            "RETURNING cycle_id",
            [ps, gen, cycle_id, pid, cmdline_hash, exp_at]).fetchone()
        # row 非空且 cycle_id 匹配 → 回收成功
```
回收条件：**lease 超时 + cycle heartbeat 超时 + owner 强身份校验为 stale + DELETE 快照 CAS 返回行**（缺一不可，防误杀长循环 + 防 TOCTOU）。

**获取 lease + 创建 cycle 同一显式事务（v2.3 写死）**——获取与 cycle 创建必须同一 BEGIN/COMMIT，崩溃不留无 cycle 的 lease；用 RETURNING 校验，不用普通 SELECT（防读到其他 owner lease）：
```sql
BEGIN;
-- 获取 lease（ON CONFLICT DO NOTHING，被占则不覆盖）+ RETURNING 校验本次 cycle_id
INSERT INTO qfq_cycle_lease (price_source, source_generation, cycle_id, owner_pid,
                             owner_cmdline_hash, acquired_at, expires_at)
VALUES (?, ?, ?, ?, ?, NOW(), NOW()+INTERVAL '<lease>' SECOND)
ON CONFLICT(price_source, source_generation) DO NOTHING
RETURNING cycle_id;
-- 应用层：RETURNING 返回本次 cycle_id → 获取成功；返回空 → 获取失败（不再靠 INSERT 后普通 SELECT）
-- 同事务内创建 qfq_cycle_run（INSERT cycle 行），两者原子：
INSERT INTO qfq_cycle_run (cycle_id, ...) VALUES (?, ...);
COMMIT;
```
- lease 覆盖 daemon **和** reconcile CLI（不再只靠 daemon FileLock）；
- 周期 finalize/失败时释放 lease（`DELETE ... RETURNING cycle_id` 校验）。

### 4.4 stale 恢复正确顺序（回应 P0-7，不把 started 直接加进 supersede SQL）

```
1. owner/lease 恢复器确认周期失活（heartbeat 超时 + owner 不活跃 双条件）;
2. 将该周期标 interrupted 并提交;
3. 现有 supersede_stale_intents() 再清理终结周期的 intent。
```
**不**让普通 supersede 函数直接看到 status=started 就删 intent（否则活跃长周期的水位候选被误作废）。

stale 判定**不只用"创建超 N 分钟"**（全市场重锚可能合法运行数小时）：用 heartbeat 超时 + owner 不活跃双条件。

### 4.5 daemon 退出路径同步（回应 P0-7）

signal handler / stop / 异常退出 / 正常退出都同步更新 `qfq_cycle_run`，不只更新 `daemon_run_state.json`。

---

## 5. 历史退役设计（回应 P0-6，审核重点检查项 5）

### 5.1 退役范围（语义查询，非硬编码）

```sql
-- 退役所有 legacy xtquant 非终态 trigger（含 scheduled）
UPDATE qfq_trigger_queue
SET status='superseded', retired_at=?, retire_reason='legacy xtquant trigger retired during MCP-only cutover'
WHERE price_source='xtquant' AND source_generation='legacy'
  AND status IN ('scheduled','pending','in_progress','retryable_failed','blocked')
```
- **包含 2 个 scheduled trigger**（v1 漏掉）。
- **不含 committed**（保留为历史证据）。
- **dead_letter 单独决定**：退役但保证不进入 MCP gate（gate 按世代过滤已天然隔离）。

### 5.2 前置断言（v2.1 修订：ASSERT 非 DuckDB 语法，改为事务内应用层断言）

> **v2-P1-1 修订**：DuckDB 无 `ASSERT` 语法。v2.1 改为**事务内应用层断言**：
> ```python
> begin_transaction()
> value = conn.execute("SELECT COUNT(*) FROM ... WHERE ...").fetchone()[0]
> if value != expected:
>     conn.rollback()
>     raise CutoverPreconditionFailed(f"预期 {expected} 实际 {value}")
> # 所有 UPDATE ...
> # 所有后置检查 ...
> conn.commit()
> ```
> 任何异常执行 rollback。下方 SQL 片段为断言内容（实际由应用层 fetchone 比对执行，不是 ASSERT 关键字）。

```sql
-- 三个僵尸周期：状态/数量/source 预条件
-- 应用层：SELECT COUNT(*) FROM qfq_cycle_run WHERE cycle_id IN (...) AND status='started' → 必须等于 3

-- 四个 intent：数量/source/周期/状态预条件
ASSERT (SELECT COUNT(*) FROM qfq_watermark_intent
        WHERE cycle_id='cyc_5ea430f1023e' AND status='pending' AND source='xtquant') = 4;

-- 退役 trigger 数量动态冻结（与修复前只读快照一致）
-- 注：实际值由快照确定（含 scheduled），不硬编码
ASSERT (SELECT COUNT(*) FROM qfq_trigger_queue
        WHERE price_source='xtquant' AND source_generation='legacy'
        AND status IN ('scheduled','pending','in_progress','retryable_failed','blocked'))
       = <修复前快照冻结值>;

-- committed 不在更新集合
ASSERT (SELECT COUNT(*) FROM qfq_trigger_queue
        WHERE price_source='xtquant' AND status='committed') = <快照 committed 值>;

-- 无活跃 legacy owner（无 lease-active in_progress trigger）
ASSERT (SELECT COUNT(*) FROM qfq_trigger_queue
        WHERE price_source='xtquant' AND status='in_progress'
        AND claimed_at >= NOW() - INTERVAL '<claim_lease_sec>' SECOND) = 0;

-- source_watermark 不含将被错误提交的候选（四价格表无 MCP 水位）
ASSERT (SELECT COUNT(*) FROM source_watermark
        WHERE table_name IN ('stock_daily','stock_minutes','etf_daily','etf_minutes')
        AND source='mcp') = 0;
```
任何预条件不一致 → 事务**中止**。所有被更新行生成清单和 hash（写入 evidence）。

### 5.3 后置断言（v2.1：同样为事务内应用层断言，UPDATE 后事务提交前）

```sql
-- legacy 非终态 trigger 为 0（应用层 fetchone 比对，非 ASSERT）
-- SELECT COUNT(*) FROM qfq_trigger_queue
-- WHERE price_source='xtquant' AND source_generation='xtquant-legacy'
-- AND status IN ('scheduled','pending','in_progress','retryable_failed','blocked') → 必须等于 0

-- retired/superseded 数量等于冻结清单
-- SELECT COUNT(*) FROM qfq_trigger_queue
-- WHERE status='superseded' AND retire_reason LIKE '%MCP-only cutover%' → 等于 <冻结的非终态总数>

-- committed 数量不变
-- SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='committed' → 等于 <快照 committed 值>

-- pending intent 为 0（全部 superseded）
-- SELECT COUNT(*) FROM qfq_watermark_intent WHERE status='pending' → 等于 0

-- 三个僵尸周期已 interrupted
-- SELECT COUNT(*) FROM qfq_cycle_run WHERE cycle_id IN (...) AND status='interrupted' → 等于 3

-- source_watermark 完全不变（四价格表行数/水位不变）
-- source_watermark checksum 与修复前一致（应用层比对）

-- 四张价格表行数、最大时间不变（raw 摘要不变）
-- price tables row count + max(time) + raw OHLC checksum 与修复前一致（应用层比对）
```

### 5.4 僵尸周期 + intent 退役

- 3 个 started 周期 → `status='interrupted'`，error=`legacy xtquant cycle retired during MCP-only cutover`。
- 4 个 source=xtquant pending intent → `status='superseded'`。
- **禁止**：转 source=mcp、提交 candidate watermark、手工推进 source_watermark。

---

## 6. 强制验收标准

### 6.1 数据源隔离
- MCP-only 下 xtquant import/连接次数=0；
- 新 capture 全 source=mcp；新 event 全 price_source=mcp；新 intent 全 source=mcp；
- MCP claim 领取 xtquant trigger 数=0；MCP gate 不统计 xtquant dead_letter。

### 6.2 幂等与世代
- 同一分红事件允许一个历史 xtquant trigger + 一个新 MCP trigger；
- MCP 重复 discover 不重复生成；不同 generation 无 trigger ID 冲突；
- **MCP bootstrap 后首轮 discover 不重生全历史 trigger**（baseline 覆盖 2181 行，净新增=0，除非真有新事件/payload 变化）；
- 迁移重复执行结果一致。

### 6.3 崩溃恢复（回应 P0-7）
分别在 7 位置中断：cycle 创建后/defer 后/claim 后/capture 后/reanchor commit 后/gate 前/watermark commit 前。下一轮：正确识别 stale cycle（heartbeat+owner 双条件）、不重复改价、不丢 trigger、不提前推进水位、不留永久 pending intent。

### 6.4 数据不变量
修复前后：四价格表行数不变、raw OHLC 不变、已有 committed event 不变、回测语义不变、历史 xtquant 证据可查询；只有 QFQ 编排状态/世代/MCP 基线发生预期变化。

### 6.5 测试（回应审核：不写死 331）
- 当前 QFQ 测试**全量**通过（执行前重新收集数量）；
- 新增测试全通过、不减少既有测试；
- ConfigLint 两套 profile（主 config + mcp_only）通过；
- 代表性策略黄金结果一致；
- staging 迁移/中断/续跑/回退演练全绿。

---

## 7. 测试矩阵

- **单测**：世代隔离（trigger_id v1/v2 分世代）、MCP claim 不领 xtquant trigger、discovery baseline 防洪水（首轮净新增=0）、CLI 共享工厂 MCP fetcher（import xtquant=0）、显式 schema migration 幂等+回读校验、v2 swap 行数/唯一性、superseded 状态合法、cutover 状态机迁移规则（非法转移拒绝）、pending_backfill resolved 收紧后精确区间、alert_id v2、aux router 路由。
- **集成测**：MCP 世代 discover→claim→reanchor→gate→水位提交全链路；SQLite 物理隔离下 MCP bootstrap 不产生伪 revision；discovery baseline 跨 bootstrap 幂等。
- **崩溃恢复测**：7 位置故障注入 + owner 存活多维判定 + psutil.AccessDenied fail-closed + 单活 cycle CAS + heartbeat 长循环更新。
- **黄金回归**：QFQ 全量回归 + 代表性策略回测逐项一致。

---

## 8. 文档与同步（铁律#1）

本地实现 + 生产清障 + 测试 + 文档完成后汇报，待用户确认才提交推送。同步更新：
- `README.md`、`docs/strategy_toolbox.md`、`docs/prompt_engineering.md`；
- `docs/qfq-resident-runbook.md`、`docs/qfq-production-enablement-checklist.md`；
- 配置说明 / schema API 文档 / 迁移 runbook / release notes；
- **MCP 项目唯一权威进度报告**（`D:\miniQMT策略实盘\私募工作文件\QuantStudio-MCP全数据源替代任务文件\实时进度报告.md`），按阶段由 ZCode 审核通过后即时更新，不提前写"已完成"。

---

## 9. 关键决策点（v2.1：已采纳 v2 二次审核建议答案，共 6 项；v2 曾误称 5 项）

| # | 决策点 | v2.1 既定方案（采纳 v2 审核建议） |
|---|---|---|
| 1 | SQLite 隔离方案 | **方案 A：独立 MCP 辅助库**。该库**同时包含 MCP adj_factor/fund_adj + observation/outbox**（不只放状态表），与 `_observe_factors` 同连接事务模型一致。旧 `qfq_aux.db` 全冻结只读 |
| 2 | anchor_state | **认可不同 MCP generation 不共用 anchor**。**v2.4 B-3b 实现修订**：迁移用 shadow 表 `<table>__b3b_v2` 按 target DDL 建全 → 复制 → 事务内统一 RENAME swap（`<table>`→`<table>__b3b_legacy`，`<table>__b3b_v2`→`<table>`）→ COMMIT 前 DROP 临时 `__b3b_legacy`。**成功后不保留旧表**（事务提供回滚能力，不需成功后保留 `_legacy` 物理表）；旧"改名为带 cutover 后缀的只读历史表"策略 **已 superseded**。生产代码始终用规范名 `qfq_anchor_state` |
| 3 | 52723 个孤儿 trigger | **保留在生产状态表，标 superseded，暂不删除**。约 5.3 万行不值得为省空间破坏审计链。后续归档另设独立任务 + 用户确认 |
| 4 | MCP 因子存储 | **MCP 重新拉取 adj_factor/fund_adj 写入新 generation 库，禁止读旧 qfq_aux.db 因子作 MCP 基线**（MCP 唯一数据源原则） |
| 5 | snapshot 替代证据 | **认可组合证据，但须规范化**：固定哈希算法/版本、固定列顺序、固定行排序、NULL/浮点规范化、分区 hash + 总 manifest hash、row count、min/max time、数据获取时间边界、证据文件只读保存、staging 与生产均可重复计算 |
| 6 | 本轮范围 | 同意本轮仍只做设计审核，不动生产库。进入阶段 B 前需 v2.1 快速复核通过 |

---

## 附录 A：改动文件清单（阶段 B 实施时参照）

| 文件 | 改动 |
|---|---|
| `qfq_fresh_fetcher_factory.py`（新建） | 共享 fetcher 工厂（P0-1 + P1-2） |
| `qfq_schema_migration.py`（新建） | 显式 2.0→2.1 迁移 + v2 swap（P0-2 + §3.2.5） |
| `qfq_discovery_baseline.py`（新建） | discovery baseline ledger（P0-3） |
| `qfq_aux_router.py`（新建） | SQLite 辅助库按世代路由（P0-4） |
| `qfq_reanchor_schema.py` | SCHEMA_VERSION→2.1；DDL_DUCKDB/COLS/CONTRACT 补列 + v2 表；TRIGGER_STATUS 加 superseded；新建 qfq_source_cutover/qfq_discovery_baseline；SQLite DDL 独立库路由 |
| `qfq_orchestrator_types.py` | trigger_id_v1/v2；TriggerRecord.trigger_id_version；alert_id v2；QFQOrchestratorConfig 加 source_generation/cutover 字段 |
| `qfq_event_discovery.py` | scan/emit/consume 全链路加世代 + discovery baseline 防洪水；trigger_id v2；detection_source 标准化（P1-4）；scan/establish 共用 `dividend_payload_hash`（v2.1） |
| `qfq_dividend_payload.py`（新建，v2.1） | 共享 payload hash 函数（防 bootstrap 与 discover 漂移） |
| `qfq_resident_orchestrator.py` | 33+ SQL 世代过滤；_apply_trigger_outcome resolved 收紧 + committed 同事务推进 baseline applied hash（v2.1）；_commit_or_hold_watermarks 改用 `_advance_watermark_on_conn`（v2.1）；claim/recover/gate/_bump_attempt/_already_committed；begin_cycle 用 qfq_cycle_lease CAS + owner/heartbeat；bootstrap_completed 按 price_source+generation 过滤；supersede_stale_intents 配合 stale 恢复器 |
| `qfq_orchestrator_cli.py` | _make_orchestrator 调共享工厂；--sources-dir；CLI SQL 世代过滤；cutover-init/status 子命令 |
| `qfq_observation.py` | record_observations 按世代路由辅助库；alert_id v2 |
| `qfq_fresh_capture.py` | capture 路由按世代；source 字段一致 |
| `qfq_factor_refresh.py` / MCP factor refresher | MCP 世代重新拉取 adj_factor/fund_adj 写入新辅助库（v2.1 决策点 4） |
| `daemon.py` | _qfq_orchestrator 调共享工厂；**删除 watermark_advancer 传参（v2.1：改用 _advance_watermark_on_conn）**；退出路径同步 cycle 状态 |
| `daemon_lifecycle.py` | cycle owner/heartbeat 更新；stale 恢复集成 |
| `writers.py` | **B-3a 已改（更正原"无需改"结论）**：source_watermark DDL 用共享 `SOURCE_WATERMARK_2_1_DDL`（8列，审计列 NOT NULL）；两条位置 INSERT + orchestrator `_advance_watermark` 改 8 列显式 + pre-cutover 哨兵 + ON CONFLICT 更新审计列；`_init_tables` 接入 source_watermark 版本安全闸（P0-2）；`_upsert_pending_backfill_on_conn` 加 price_source/source_generation 列 + 新 8 列 PK + 内部 keyword 参数（price_source/source_generation）；`_advance_watermark_on_conn`（:699-712）继续复用为 QFQ 编排事务版 |
| `quality_audit.py` | Qfq* 检查按世代过滤 |
| `scripts/qfq_batch2_multiround.py` | trigger_id v2 |
| `config/profiles/mcp_only/qfq_aux_paths.json`（新建） | 辅助库路径集中配置 |
| `scripts/qfq_snapshot_evidence.py`（新建，v2.1） | 规范化 snapshot 证据计算（决策点 5：固定算法/列序/行序/规范化） |
| 测试 | 单测/集成/**崩溃恢复（含"水位写后 intent 提交前崩溃"事务回滚，v2.1）**/黄金回归（见 §7） |
| 文档 | README/strategy_toolbox/prompt_engineering/qfq-runbook/enablement-checklist/进度报告 |

**v2.1 新增表汇总**：`qfq_discovery_baseline`（PK 含 cutover_id）、`qfq_source_cutover`（去掉 partial unique）、`qfq_active_cutover`（独立 active 指针表）、`qfq_cycle_lease`（单活 CAS）、`qfq_anchor_state`（v2 swap 改 PK 后用规范名，旧表改名只读）。

## 10. B-4 实施注记（权威日期 2026-08-05，本地完成，CodeBuddy 独立复审通过）

B-4 采用独立 `scripts/qfq_b4_staging_drill.py`，而不是修改生产 daemon 路径。默认 preflight 0 数据库写、0 run-dir 写；`--execute` 仅操作 output 下全量副本。演练器在正式文件复制期间同时持有 daemon/collector 锁，创建 baseline/normal/recovery 三分支，并验证：

1. COMPLETE_2_0 dry-run；
2. COMMIT 前故障回滚及逻辑 hash 不变；
3. 正常迁移到 COMPLETE_2_1 + already-current 幂等；
4. COMMIT 后中断 + 新 report already-current 恢复（用户已接受 TD-42）；
5. MCP 配置的离线 bootstrap/first discover；
6. 正式主库/aux 四项证据前后不变；
7. 无 active pointer、无 mcp-gen1，证明未越过 B-5/B-6。

全量 run `b4_20260805_final` exit 0，约 43.657 GiB（单次 run 近似值）。pre-B-5 dividend discovery 首轮新增 2181、立即重放 0；当前实现为全表 hash 扫描，B-5 的 discovery-baseline/CAS 尚未落地，故 B-4 只冻结该实测基线与重放幂等，不把未来语义倒灌为当前验收。

B-4 报告：`output/mcp_migration/b4_20260805_final/b4_drill_report.json`。其 `production_ready=false`、`git_sync_authorized=false` 为强制门禁；B-4 独立复审已通过，允许进入 B-5 本地实施。

### 10.1 Windows COMMIT 后 hard-crash 边界

B-4 最终回归将 `after_commit_before_report` 冻结为：`COMMIT` durable 成功并设置 `committed=True` 后、正常 DuckDB connection cleanup 与首次 committed report 更新之前。真实 `os._exit` 因而模拟“进程在资源正常清理前终止”；正常/受控异常路径仍由 `finally` close。Windows crash 测试须串行、exit 严格为 92；禁止接受 `0xC0000005`。
