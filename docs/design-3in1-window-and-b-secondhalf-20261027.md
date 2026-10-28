# 三用窗合并方案（QS_DaemonForever 切换 + 件2 主库回写 + W-2②③ 实施）+ B 第二半分片/增量

**日期**：2026-10-27 ｜ **出件**：主仓 dev 会话 ｜ **状态**：**①方案，待 ②审**
**状态**：B-1 已落地（`4a31618`）；本件覆盖 **三用窗** 与 **B 第二半** 两块
**铁律依据**：01 六步流水线 · 18 纯增益审计 · 19 daemon 占用纪律 · 21 方案知识反哺 · 22 R1/R4 · 23 WAL 残留 · 25 上游可变因子判据

---

## 知识反哺检索留痕（R1）

知识反哺：[命中] daemon 生命周期跨会话占用纪律（iron-rules/19-daemon占用纪律，2026-10-27）
—— 采纳要点：本件三用窗**一次性停机**，须前置占用声明 + 时点编排 + 恢复验证；避让他线窗口。

知识反哺：[命中] WAL 残留处置（iron-rules/23-WAL残留处置，2026-10-27）
—— 采纳要点：件2 回写 813,140 行将显著增大 WAL，窗口内**不得 CHECKPOINT**，收敛步须在回写完成后独立执行；禁手工删 `.wal`。

知识反哺：[命中] schema 指纹：加列＝契约变更（iron-rules/24-schema指纹加列，2026-10-27）
—— 采纳要点：本件**不做任何 DDL**（仅数据回写 + 脚本/任务形态变更），不触发指纹契约变更；回写仅 UPDATE 既有列。

知识反哺：[命中] 修复前置纯增益审计（iron-rules/18-纯增益审计，2026-10-27）
—— 采纳要点：B 第二半分片接入按「**新增检测型 + 纯恢复型**」复合判定，只对显式声明表集生效（零衰减）；件2 回写按「**数据修正型**」判定（diff 只能归因于被修正行集）。

知识反哺：[无命中] 三用窗时点编排 / Task Scheduler 脱离 job 形态 —— 项目知识库无对应条目（属新领域），本件为首次沉淀源。

---

## 一、范围与三件的关系

| 件 | 内容 | 是否需停 daemon |
|---|---|---|
| **C. QS_DaemonForever** | 计划任务拉起（Task Scheduler 服务启动）⇒ 脱离 DSH 工具进程树/任何 Job | **需**（切换拉起形态） |
| **B. 件2 主库回写** | `close_front_ratio` **813,140 行**回写（主库 **52.7GB**） | **需**（写库独占） |
| **D. W-2②③ 脚本四项** | wrapper 独立/心跳/双路 ExitCode/对账（已完成）+ 告警推送 + 30min 去重 + stale 标记 | 否（纯脚本层），**同窗顺做** |

⇒ **一次停启三用**：停机窗口只开一次，C 切换 + B 回写 + D 顺做。

---

## 二、A. 三用窗时序编排

| 步 | 动作 | 判据/校验 | 预计耗时 |
|---|---|---|---|
| A0 | **占用声明**（19 号铁律）+ 确认无他线窗口 | 群内声明时段 | 5 min |
| A1 | 停 daemon（`Stop-Process` 经 wrapper 正常路径）+ 确认进程归零 | 双形态正则查进程 = 0 | 1 min |
| A2 | **预检探表**：`close_front_ratio` 行数/PK/是否有索引 | 行数 ≈ 813,140 | 2 min |
| A3 | **WAL 处置**：确认起始 WAL 基线（0MB 已知） | `check_wal.ps1` 探测模式 | 2 min |
| A4 | **件2 回写执行**（分批 + 断点续跑） | 每批 `updated` 行数累加 = 813,140 | **待窗口内实测（见 §三）** |
| A5 | **WAL 收敛**：回写后 CHECKPOINT | `check_wal.ps1 -Force`，WAL→0 | 待测（参照：1GB→500.6s） |
| A6 | **D. W-2②③ 脚本项**（推送通道 + 去重 + stale 标记） | 脚本语法/单测 | 15 min |
| A7 | **C. QS_DaemonForever 注册 + 切换** | `schtasks /Query` 状态 Ready；删旧拉起路径 | 10 min |
| A8 | **新形态拉起**（经计划任务） | 进程父子链父 = `taskeng`/`svchost`（非 DSH 进程树） | 2 min |
| A9 | **恢复验证**：巡检 OK + 日志续写 + WAL 基线 | 巡检 exit=0；今日轮续跑 | 5 min |

**窗口总估**：A0-A3 10 min + A4（待测）+ A5（待测）+ A6-A9 ~35 min。
**回退路径**：ASTop 记录 → 任一步失败 ⇒ A8 用旧 wrapper 手动拉起 ⇒ 件2 回写可断点续跑（分批事务各自提交）。

---

## 三、B. 件2 四评估（含"窗口内预检步"取数限制）

### 3.1 回写时长（**待窗口实测**）

- 规模：`close_front_ratio` **813,140 行**，主库 **52.7GB**
- DuckDB `UPDATE` 语义 = **delete + insert**（MVCC 追加），非原地改写
- **取数限制（如实）**：daemon 持主库写锁，**本方案阶段无法只读探测该表结构/索引** ⇒ 时长评估以 **A2 预检步 + A4 首两批实测**外推：
  - 首两批（如各 5 万行）实测耗时 ⇒ 线性外推
  - 若单批 > 180s ⇒ 触发**降批**（改 1 万行/批）
- **保守预估带**：15–45 min（**未实测，仅量级参考**，不作承诺）

### 3.2 WAL 增量风险

- 813,140 行 UPDATE（delete+insert）⇒ WAL 增量估 **0.5–2 GB 量级**（参照既有 1.004GB 事件）
- **风险**：窗口内 WAL 增长 + 回写完成后的 CHECKPOINT 耗时（参照 **1GB → 500.6s**）
- **处置**：A5 独立收敛步；**禁用**"回写中途 CHECKPOINT"

### 3.3 执行主体

- **策略研发**提供回写脚本/口径（`close_front_ratio` 语义归其域）
- **本线**负责窗口编排、停机/拉起、WAL 收敛、验证
- 需在窗口前**拿到脚本 + dry-run 结果**

### 3.4 三用窗时序依赖

```
A4（回写）必须在 A1（停 daemon）之后、A8（拉起）之前
A5（WAL 收敛）必须在 A4 之后、A8 之前（避免新写与收敛竞争）
A6（D 脚本）可与 A4 并行（不碰库）——但为降低窗口复杂度，排在 A4 之后串行
A7（C 切换）必须在 A8 之前
```

---

## 四、C. QS_DaemonForever（假设 b 的决定性实验 + 长期形态）

### 4.1 设计要点

| 项 | 内容 |
|---|---|
| 承载 | **Task Scheduler 计划任务**（`schtasks` / `Register-ScheduledTask`） |
| 关键性质 | 任务由 **Task Scheduler 服务**（`scheduletask` / `svchost`）启动 ⇒ **进程父链脱离 DSH 工具进程树与任何 Job Object** |
| 触发 | 开机 + 每日一次（或常驻 + 失败重启） |
| 运行账户 | S4U + LeastPrivilege（无交互登录，不弹窗） |
| **切换后判据** | ①父进程 = Task Scheduler（**非** powershell 工具链）②若此后不再出现"wrapper 与 daemon 同时消失"⇒ **Job Object 终止假设 (b) 成立** |
| 回退 | 保留旧 `start_daemon_guarded.ps1` 手动拉起路径；计划任务可 `Unregister` |
| 风险 | 若假设 (b) 不成立（六死另有他因），则切换后仍会死 ⇒ **此时按 4689 审计 + 新形态证据继续排查**（本件不承诺"切换即解决"） |

### 4.2 与六死取证的关系（结论引用，不重开）

- 4689 审计：`pid=3032 Exit Status: 0x0`（六死）
- wrapper 同日无 4689 记录、未写 EXIT ⇒ 二者几乎同时终止
- **两假设并列**：(a) 正常退出 + wrapper 被连带；**(b) Job Object/进程树终止置码 0**
- **本件不做定谳**，只提供**决定性实验**（切换后才可判）

---

## 五、D. W-2②③ 四项（引用既有方案，不重写）

来源：`docs/design-daemon-w2-23-merged-20261026.md`（2026-10-26 ②审四项全准）
- §1.1 四项（wrapper 独立/心跳/双路 ExitCode/对账）—— **已完成并实战验证**（对账两次补记 `UNKNOWN(lost)`）
- **新增（本次）**：stderr 按运行分文件 + 指针文件（**已完成，未提交**）—— 第六死 stderr 被截断的教训
- 告警推送（R-3 三件套 + 30min 去重 `verdict|attribution`）— **本窗 A6 实施**
- `daemon_status_stale.json` 外置 stale 标记 + 消费者契约 — **本窗 A6 实施**

---

## 六、E. B 第二半：passthrough 分片 + 增量锚点

### 6.1 缺陷（已定谳）

```
mcp_adapter.py:618   export_dataset(..., row_limit=None)
```
⇒ 服务端 v1 默认 **row_limit=2,000,000、最老优先** ⇒ `sw_weight` 4.48M 只落 2M，**丢 2.48M = 55.4%**（实证吻合）。

### 6.2 6 任务逐个取证矩阵（**已闭合，非推定**）

| 任务 | passthrough 入口（源） | 全量语义 | row_limit 保护 |
|---|---|---|---|
| `ai_research_snapshot` | `_WIDE_TEXT_PASSTHROUGH` L242 + `_PASSTHROUGH_TABLES` L249 → **L521 宽文本入口** | `start=2024-01-01, end=None` | ❌ 不在 |
| `cnthesims_events` | L241 → **L521** | 同上 | ❌ |
| `llm_text_events` | L243 → **L521** | 同上 | ❌ |
| `llm_text_events_enriched` | L243 → **L521** | 同上 | ❌ |
| `llm_text_raw_feed` | L243 → **L521** | 同上 | ❌ |
| `sw_weight` | canonical L148 + 表映射 `dataset_kind=passthrough` → **L540 大表路由入口** | 同上 | ❌ |

**共同落点**：`mcp_adapter.py:618`。
**保护名单现状**：`_ROW_LIMIT_BUDGET_TABLES = frozenset({"stock_float_share"})`（L854，注释：「刻意收窄为显式声明——未声明的既有大表分批行为逐位不变（零衰减）」）
**注**：`ai_research_snapshot` 在 `questdb_table_map.json` 的 `tables` 中**无条目**，其 passthrough 身份由 adapter 侧两个集合承载（L242/L249）——**非表映射声明**。

### 6.3 两改点（零新造机制）

| 改点 | 内容 | 零衰减保证 |
|---|---|---|
| **①分片** | passthrough 入口接入既有 `_export_batches()` 窗口切分 + 显式传 `row_limit` | **仅对显式声明表集生效**（新增独立 frozenset，沿 `_ROW_LIMIT_BUDGET_TABLES` 既有收窄模式）⇒ 未声明表逐位不变 |
| **②增量** | 6 任务改增量锚点 | **复用既有水位基础**：6 表全部 `watermark_confidence: derived`（`trade_date`/`event_time`/`publish_time`）⇒ 不新建水位机制 |

### 6.4 两硬门（验收不可放宽）

1. **不得静默截断**：触限即**分片续拉**（下一窗继续），**禁"取最老截断"**
   - 验收判据：**落库行数 ≥ 源表行数**，且**末窗不因触限丢尾**
2. **6 任务逐个取证**：✅ **已闭合**（§6.2 矩阵）

---

## 七、附节：写路径并发取证（六轮收敛，结论 6 行）

1. **「线1 还原直写主库」—— 证伪**：`_requires_qfq_restore`(L1858)/`_restore_qfq_if_required`(L1862)/`_restore_to_raw`(L2080) 三函数体**零写动作**，纯内存变换。
2. **`(b)` 空锁假设 —— 排除**：`_tables_of('STOCK'/'ETF')` 分钟表名精确 ∈ `_SERIAL_WRITE_TABLES`；`ASSET_TABLE_MAP` 为模块内字面量 ⇒ 引擎获**真 RLock**。
3. **RLock 可重入假设 —— 降为次候选**：orchestrator 无线程（同步方法，daemon L406/458/489 批次调度线程调用），`ThreadPoolExecutor` worker 只跑 `process_one_day`/`process_one` ⇒ 引擎与 writer **不同线程**。
4. **共享连接假设 —— 不成立**：`_write_locked`(L1383/L1426) 两处均 `self._conn()`（每次新建），非 `_ensure_shared_conn()`。
5. **`on_conn` 假设 —— 不成立**：`_upsert_pending_backfill_on_conn`(L1974) 所写为**回补状态控制表**（非价格行），且**全仓 0 调用点**。
6. **★ 实例级收敛（实测佐证）**：同进程多 Connection **共享同一 DatabaseInstance**（`PRAGMA database_list` 实测 database_id 相同）；`_SERIAL_WRITE_TABLES` 仅 2 表有锁、其余写表**无锁** ⇒ **跨表并发在同一 instance 上交错** ⇒ `write-write conflict on key`；**冲突报告只给 key（(code,time) 主键）不给表名** ⇒ 失败侧只记自己的表 ⇒ **`peers_n=0` 与 `write_scope 零重叠` 的结构性根源**。

### 7.1 锁矩阵（框架层立项量化依据）

**任务定义启用表 74 张；`_SERIAL_WRITE_TABLES` 仅 2 张 ⇒ 有锁覆盖率 2/74 = 2.7%；无锁 72 张 = 97.3%**

**daemon.log 实际写过 14 张**：
- **有表锁（2）**：`stock_minutes`、`etf_minutes`
- **无表锁（12）**：`etf_basic`、`etf_daily`、`etf_dividend`、`index_daily`、`industry_classification`、`stock_basic`、`stock_daily`、`stock_daily_valuation`、`stock_dividend`、`stock_float_share`、`stock_namechange`、`trade_calendar`

**无锁表全集（72 张，按域）**：
- 日线/行情：`stock_daily`、`etf_daily`、`index_daily`、`sw_daily`、`ths_index`、`limit_*`（4 张）、`daily_info`
- 财务/基本面：`balance_statement`、`cashflow_statement`、`income_statement`、`fin_indicator`、`stock_basic`、`etf_basic`、`stock_namechange`、`stock_dividend`、`etf_dividend`、`stock_float_share`、`stock_daily_valuation`
- 成分/分类：`index_classify`、`index_constituents`、`industry_classification`、`sw_weight`、`sector_top300_daily`
- 资金/龙虎/两融：`hm_detail`、`hm_list`、`margin`、`block_trade`、`top_list`、`top_inst`、`ws_lhb`、`ws_margintrade`
- 情绪/文本：`news_sentiment`、`xueqiu_sentiment`、`survey_sentiment`、`inst_survey`、`sentiment_factor_daily`、`market_breadth_daily`、`llm_text_*`（3 张）、`cnthesims_*`（2 张）、`ai_research_snapshot`、`rsshub_raw`、`tdx_theme_*`（4 张）
- ETF 专题：`ws_asfund`、`ws_blocktrade`、`ws_etf_holders`、`ws_etf_holdings`、`ws_ipo`、`ws_reserve`、`etf_sentiment_daily`、`etf_share_size`、`moneyflow_*`（3 张）
- 其它：`broker_monthly`、`broker_recommend`、`cninfo_first_rating`、`em_first_cover_rating`、`factor_value`、`forecast_vip`、`gisisi_daily`、`idx_anns`、`limit_cpt_list`、`style_cross_section_daily`、`ths_hot`、`slb_len`、`trade_calendar`

**⇒ 立项依据**：锁覆盖 2.7%，而 daemon 实写面 12/14 张无锁 ⇒ **同实例跨表并发是常态而非例外**。

### 7.2 修复形态（**只登记，不定案**）

| 候选形态 | 说明 | 权衡 |
|---|---|---|
| **扩大锁覆盖** | 把 72 张全加入 `_SERIAL_WRITE_TABLES` | 简单；但**表级锁仍不解决跨表并发**（同实例写仍是并发事务），且串行化面积过大拖慢采集 |
| **单写连接队列化** | 所有写经**单一 writer 线程/队列**串行 | 从根上消除同实例并发；代价=吞吐上限=单连接写速 |
| **写汇集** | 多 worker 只做取数/转换，写统一汇集到写线程批处理 | 吞吐最优；实现成本最高 |
| （次选）实例内写事务互斥 | 单一 `threading.Lock` 包住**所有表**的写事务（非按表） | 改动最小（把 2 张表锁扩成 1 把全局锁），可作**过渡**；但与"锁粒度"原设计意图冲突 |

⇒ **本件只登记，不定案**；stock 面**框架层①方案件明日（10-28）启动**，形态选型在其内定。

---

## 八、验收标准

| # | 判据 | 方法 | 通过线 |
|---|---|---|---|
| 1 | 三用窗顺序无倒置 | 窗口记录（A1→A8 时间戳） | 严格单调 |
| 2 | 件2 回写完整 | `updated` 行数累加 | **= 813,140**（误差 0） |
| 3 | WAL 收敛 | `check_wal.ps1` | WAL ≤ 基线（0MB 量级） |
| 4 | QS_DaemonForever 生效 | 父进程链查询 | 父 = Task Scheduler，**非** DSH 工具树 |
| 5 | daemon 恢复 | 巡检 + 日志续写 | 巡检 exit=0、今日轮续跑 |
| 6 | D 四项 | 脚本语法 + 单测 | 全绿 |
| 7 | B 第二半 | 分片完整性 + 增量锚点 | **落库 ≥ 源表行数**、末窗不丢尾；6 表增量生效 |
| 8 | 零回归 | 既有测试 | 全绿 |

## 九、回退条件

| 触发 | 回退动作 |
|---|---|
| 件2 回写单批 > 300s | 降批（5万→1万行） |
| WAL > 4GB | **中止回写**，先收敛，再评估 |
| QS_DaemonForever 拉起失败 | 用旧 wrapper 手动拉起（保留路径） |
| 切换后 daemon 仍死（第七死+） | 假设 (b) 否定 ⇒ 保留新形态，按 4689 审计继续排查（不回到旧形态） |
| B 第二半分片后行数仍 < 源表 | **判失败**，禁以"接近"放行 |

---

*本件为 ①方案，未实施。②审通过后方可进入 ③实施。*
