# QuantStudio 客户反馈（2026-09-28）writers/export 三项请求 + 两项附带 · 修复设计方案

- **日期**：2026-09-28（六步流水线 步骤1 溯源归因 + 步骤2 送审件）
- **来源**：客户反馈（运维口径「美德双休」，部署 `D:\QuantStudioNew`）在跑期间提出三项请求 + 两项附带
- **客户环境**：Windows 11 / 16GB / Python 3.12.10 / duckdb 1.4.5（与本仓钉版一致）
- **本仓基线**：`D:\miniQMT策略实盘\QuantStudio`，HEAD `a7fe175`
- **前序同客户件**：`docs/mcp-pull-four-issues-fix-design.md`（2026-09-23 四问题案）、`docs/backfill-line-tracking-items.md`（锚漂移跟踪项）
- **派单口径**：`docs/handoff/dispatch-calendar-20260911.md:3062`（§三八四）
- **风险窗**：客户在跑，要求在 4–5 天内给出可落地处置

---

## 〇、待用户裁定（送审问题，5 项）

| # | 待裁点 | 本件建议 |
|---|---|---|
| Q1 | T2「跳过一致行」的默认开关 | **默认关 + 灰度**（`QS_UPSERT_SKIP_IDENTICAL`，与四问题案 `async_mode` 同款）；验收逐位等价通过后由用户裁定转默认开 |
| Q2 | `WriteResult` 审计字段是否扩字段 | **保留 `new`/`updated` 语义不变**（避免破坏 `batch_audit` 守恒检查与客户读数），**新增可选 `changed`**（仅开关开启时由 `RETURNING` 统计，默认不统计） |
| Q3 | 复权锚漂移是否单独立项做云端核查 | **不单独立项**；改为「一次只读云端全历史比对 + 并入错误一 T3 收敛窗 + 复用 FAIL count 趋势判据」（§三 T4） |
| Q4 | CLI `--config-dir` 勘误形态 | **只改文档**（补记参数 + 明示默认值≠生产 profile）；改默认值解析 profile 属行为变更，需另行呈裁 |
| Q5 | T3 内存调查件的范围 | **先做归因取证再定修复**；「QFQ-Invariant alloc 失败 ×2」需向客户索取原文与时间戳后方可闭环（§三 T3 诚实边界） |

---

## 一、步骤1 溯源归因结论（分层：现象 / 直接原因 / 代码缺陷 / 业务边界 / 隐性风险）

### 请求 ② — upsert 慢路径（最紧急，客户量化最充分）

| 分层 | 结论 | 证据 |
|---|---|---|
| 现象 | 增量入库吞吐 **≈100 万行/h**，而同规模**纯插入 ≈250 万行/h**；日志读数为「新增 0 + 更新 49,9xx」——即**零新增、全量走 UPDATE** | 客户反馈；日志文案见 `writers.py:861-862` |
| 直接原因 | 幂等 upsert **无条件**对每个主键冲突行执行 `DO UPDATE SET`（即使各列取值逐位相同） | `writers.py:830-836` |
| 机理 | DuckDB 对带 PK/UNIQUE（ART 索引）的表，`UPDATE` 一律改写为 **DELETE + INSERT**，且**按 2048 行 chunk** 在流水线中重写；这正是「delete 相位」的来源，宽表代价尤高 | 官方文档 *Indexes · Constraint Checking in UPDATE*（本仓 `pyproject.toml` 注释亦指向同族问题） |
| 代码缺陷 | ① `updated_rows` 由**写前** `SELECT COUNT(*) ... WHERE pk IN (...)` 统计，语义是「**主键已存在**」而**不是「值有变化」** → 客户读数被系统性放大为「更新 49,9xx」 | `writers.py:821-829`、`writers.py:840-844` |
| 代码缺陷 | ② 写后审计 `new_rows = len(df) - updated_rows` 依赖①的口径；**任何把 `updated_rows` 改成「实际变化行数」的做法都会连带破坏 `new_rows`** | `writers.py:842`；`quality_audit.py:288-313`（`WriteCountConservation`） |
| 业务边界 | 无（纯框架实现问题，不涉数据质量判定、不改任何口径） | — |
| 隐性风险 | 大批量 `DO UPDATE` 与 1.5.x 的 ART index-delete **fatal 回归同族**（`duckdb#23645`；1.5.0–1.5.5 命中，**1.5.6 未修复**）；本仓已用 `duckdb==1.5.6` 单版本锁定 + 版本闸（放行集合 `{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}`，判定源唯一 `quantstudio/pipeline/duckdb_version_policy.py`）上锁 | `pyproject.toml:20`、`pyproject.toml:50`、`quantstudio/pipeline/duckdb_version_policy.py` |

**派单候选形态的勘误（本件已实测，实测读数见 §九）**：
派单给出的候选形态 `WHERE row IS DISTINCT FROM excluded` **不是合法 DuckDB 语法**——
实测 `duckdb 1.4.5` 报 `BinderException: Table "t" does not have a column named "t"`；
`(t.*) IS DISTINCT FROM (excluded.*)` 亦不支持（`STAR expression is not supported here`）。
**合法等价形态为下列两者**，本件推荐形态 B：

```sql
-- 形态 A：逐列（列多时冗长、易漏列）
... ON CONFLICT (pk...) DO UPDATE SET c1=EXCLUDED.c1, ...
WHERE t.c1 IS DISTINCT FROM EXCLUDED.c1 OR t.c2 IS DISTINCT FROM EXCLUDED.c2 OR ...

-- 形态 B：行值元组（实测通过，推荐）
... ON CONFLICT (pk...) DO UPDATE SET c1=EXCLUDED.c1, ...
WHERE (t.c1, t.c2, ...) IS DISTINCT FROM (EXCLUDED.c1, EXCLUDED.c2, ...)
```

### 请求 ① — 批级断点/水位线

| 分层 | 结论 | 证据 |
|---|---|---|
| 现象 | 任务中途崩溃/停止后，**只能从任务级窗口或证券前缀续跑**，粒度粗；大表任务重跑代价高 | 客户请求 |
| 直接原因 | 现有续跑粒度 = **任务级窗口**（day）与**证券前缀**（stock），非「任务内批次窗口」 | `daemon.py:729-791`（`_open_day_resume` / `_open_stock_resume`） |
| 代码缺陷 | upsert 通道**无批级进度落盘**：`daemon.py:557-577` 的任务循环中，批次异常只记录并换源，无断点写入 | `daemon.py:557-577` |
| 既有可复用面 | ① `TaskResumeCursor`（`task_name/mode/window_start/window_end/watermark_at_start/last_completed` + `advance()/advance_a4()/mark_stopped()/clear()`）；② **分片级续跑先例** `write_passthrough_chunked` + `_pt_staging_ledger`（staging 跨尝试持久化 → ledger 记分片键与行数 → 不一致则重建 → 全片完成后原子换名）；③ 表级水位 `source_watermark`；④ `batch_audit` 表 | `task_resume.py:82-199`、`task_resume.py:223-260`、`writers.py:955-1031`、`writers.py:923`、`writers.py:1033-1077`、`batch_audit.py:76-121` |
| 业务边界 | 断点续跑**只有在写入通道幂等时才是安全的**；upsert 通道天然幂等（主键 upsert），passthrough 通道靠 staging+原子换名实现幂等——两者前提不同，不可互相搬用结论 | `writers.py:685-691`、`writers.py:913-921` |
| 隐性风险 | 断点「写入时机」若早于「写提交成功」，崩溃后会误跳过未提交批次 → **数据缺口**；必须 fail-closed | 见 §三 T1 |

### 请求 ③ — 写入路径内存增长（调查件）

| 分层 | 结论 | 证据 |
|---|---|---|
| 现象 | 内存 **0.5GB → 6.3GB → Hang**，伴 2 次「QFQ-Invariant alloc 失败」 | 客户证据包（派单转述） |
| 直接原因（已定位） | **因子完整性自检把整表读进内存**：`audit_factor_integrity` 用 keyset 分页**逐页累积到 `frames` 列表**，最后**一次性** `pd.DataFrame([r for page in frames for r in page])`，再对全量做 `groupby/shift/assign`（多份副本） | `qfq_invariant.py:357-371`、`qfq_invariant.py:386-404` |
| 量级 | `adj_factor` **25,219,309 行 / 5,815 码**、`fund_adj` **11,930,536 行 / 2,277 码**（注：`qfq_invariant.py:356` 注释写「1586 万行」，与文档读数**不一致**，属待勘正的注释漂移） | `docs/pipeline-tech-debt.md:244` vs `qfq_invariant.py:356` |
| 次热点 | `verify_reanchor_selfcheck` 对**单码** `ORDER BY time LIMIT 500_000` 直接 `fetchdf()` | `qfq_invariant.py:667-720` |
| 旁证（耗时占比） | invariant 自检吃掉写入阶段的 **71%~96%**：stock_minutes 179 shards/880 万行 → `invariant=10926.3s`（write 占 91.2%）；etf_minutes → `invariant=638.6s~1042.1s`；stock_daily → `invariant=260.8s` | `data/logs/daemon.log.2026-09-18:394`、`daemon.log.2026-09-17:5284`、`daemon.log.2026-09-21:717` |
| 无防护 | 全仓**无任何内存监控/自愈**：`psutil` 仅用于守护进程身份校验（PID/create_time/exe/cmdline），不查 RSS；无 watchdog、无 OOM 前置告警 | `daemon_lifecycle.py:32`、`daemon_lifecycle.py:83` |
| 业务边界（诚实边界） | 「QFQ-Invariant alloc 失败 ×2」在**代码与日志中均无原文命中**，仅存在于派单转述；客户 6.3GB 峰值与**四问题案问题2（`stock_float_share` OOM，采集端 `_fetch_all_pages` 全量累积）同族** — 必须先判定 6.3GB 究竟落在**采集端 / 写入端 / 自检端**哪一段 | `docs/mcp-pull-four-issues-fix-design.md:26-34`；本件 §九 未命中记录 |

### 附带 A — 复权锚漂移清单评估

| 分层 | 结论 | 证据 |
|---|---|---|
| 现象 | stock 13 码 / etf 5 码漂移 >0.5%；**etf 入库后由 1 码增至 5 码且未自愈** | 客户反馈（派单转述） |
| 判据 | `dev = |actual/expect - 1|`；`>0.5%` → FAIL，`0.3%~0.5%` → WARN | `quality_audit.py:558`、`quality_audit.py:631`、`quality_audit.py:686`、`quality_audit.py:778` |
| 已知同族跟踪项 | 2026-09-22 读数 `FAIL>0.5%: count=60 (max=0.6603)`，样本含 `159220` 等；线调度定性 = **错误一案 85-code 台阶在审计面的显影**，判据 = 换代际后 **FAIL count 趋势下降** | `docs/backfill-line-tracking-items.md:5-13` |
| 已排除项 | 客户端 `fund_adj` 与云端 `etf_adj_factor` **每码最新因子 2165/2165 完全一致** ⇒ **非「最新锚源」分歧**，分歧只可能在**历史因子段** | `docs/mcp-pull-four-issues-fix-design.md:46` |
| 自动修复路径 | 漂移后可经常驻编排器触发单证券重锚（`fresh_authoritative_rebase` → `apply_reanchor_for_security` → 写后自洽检查 + 黄金行刷新）；「未自愈」的可能成因需按该链路逐环取证 | `qfq_resident_orchestrator.py:509-613`、`qfq_resident_orchestrator.py:621-645` |
| 隐性风险 | 漂移清单本身是**采样审计**（候选取自除权表 + 因子变化点前 90 天窗），不是全量普查 ⇒ 「13/5」是**采样读数**，不能直接当作全库缺陷码数 | `quality_audit.py:631-641` |

### 附带 B — CLI `--config-dir` 手册勘误

| 分层 | 结论 | 证据 |
|---|---|---|
| 代码事实 | `--config-dir` 默认值 = `ROOT / "config"`；生产唯一数据源配置在 `config/profiles/mcp_only/`（`collector_tasks.json`）；数据根解析亦优先读 `config/profiles/mcp_only/data_config.json` | `daemon.py:3625-3626`、`_paths.py:25`、`docs/handover/pre-handover-test-plan.md:12-19` |
| 缺陷 | 仓库 `README.md` **完全未记录 `--config-dir`**（grep 0 命中）⇒ 用户按默认值启动会解析到 `config/`（profile 的**父目录**）而非生产 profile，可触发 `--allow-non-main-target` 拒绝，或得到空任务集 | `README.md`（0 命中）、`daemon.py:3638-3640` |
| 业务边界 | 属文档/可用性缺陷，非数据缺陷 | — |

---

## 二、背景与动机

客户（美德双休，部署 `D:\QuantStudioNew`）在**四问题案落地后继续在跑**，暴露三类**新的吞吐/健壮性**瓶颈与两项附带诉求：

1. **增量入库慢**：零新增批仍全量走 `DO UPDATE`，实测吞吐只有纯插入的 **40%**（100 万 vs 250 万行/h）；
2. **崩溃代价高**：任务级续跑粒度太粗，大表任务中途失败需从窗口头重跑；
3. **内存增长与 Hang**：0.5GB→6.3GB→Hang，写入路径内存无监控、无自愈。

**不修的影响**：客户在 4–5 天风险窗内可能**再次 Hang**（每次 Hang 都要人工介入 + 重跑）；增量拉取窗口被慢路径拉长，逼近上游 60s 单作业预算 → 触发连锁 `export_exceeds_time_budget`（四问题案问题 1 的同族风险）。

---

## 三、任务拆解

### T1 批级断点/水位线（行为性功能 ⇒ 设计含验收 + 回退）

**目标**：任务内按**批次窗口**提交断点，崩溃后只重跑未完成批次；成功完成后清理断点。

**设计要点**
- **断点载体**：新增批级断点表（键 `(task_name, table, freq, window_key)`，值含 `batch_id / status / rows_written / updated_at`），落主库；**不改 `source_watermark` 语义**（它仍是 source/table/freq 级增量水位，职责不同，不越权）。
- **写入时机（fail-closed，红线）**：断点**只在** `writer.write()` 返回且写后回读验证通过后提交；断点提交失败 → **不推进**，下次重放该批（upsert 幂等保证安全）。**禁止**在写提交前写断点。
- **续跑语义**：任务启动读断点 → 跳过 `status=completed` 的窗口 → 其余按原顺序重跑；断点缺失 → 退回现有「任务级窗口/证券前缀」行为（**零回归**）。
- **与既有机制的关系**：`TaskResumeCursor` 继续承担任务级游标；本件的批级断点是其**下层细化**，不替换。
- **开关**：`QS_BATCH_CHECKPOINT`（默认关）→ 关闭时逐字节等同现状。
- **不引入的方向**：不把 passthrough 的 staging+原子换名机制搬到 upsert 通道（前提不同：upsert 靠主键幂等，staging 靠换名原子性）。

### T2 跳过「已存在且一致」行（消 DO UPDATE 慢路径）

**目标**：主键冲突且**各列逐位相同**的行不进入 delete 相位；吞吐逼近纯插入。

**改动**（`writers.py` `_write_locked`）
- SQL 追加 `WHERE` 子句，采用**形态 B（行值元组 `IS DISTINCT FROM`）**；列集合取**本批 df 列**（与现有 `update_set` 同源），主键列一并纳入比较。
- **审计口径保持不变**（Q2 建议）：`updated_rows` 仍 = 写前 `SELECT COUNT(*) ... WHERE pk IN (...)`（「主键已存在」）；`new_rows` 仍 = `len(df) - updated_rows`。**不把 `updated_rows` 改成「实际变化行数」**——否则 `new_rows` 与 `batch_audit` 守恒检查同时被破坏。
- 如需「实际变化行数」，用 `RETURNING 1` 计数并写入**新增可选字段** `changed`（默认不统计，避免额外materialize开销）。

**等价性设计（从严，逐位证明）**
> 命题：「数据一致行跳过」≡ 语义等价。
- ① **NULL 安全**：`IS DISTINCT FROM` 为 NULL-safe（NULL vs NULL → 非 distinct → 跳过）。旧实现下「NULL 覆盖 NULL」终态亦为 NULL ⇒ 终态一致；NULL vs 非 NULL → distinct → 更新 ⇒ 一致。以 4 种 NULL 组合单测钉住。
- ② **数值逐位**：DOUBLE 列按 **bit 级**比较，**不引入容差**（容差会破坏逐位等价）；pandas `NaN` → DuckDB `NULL`。`-0.0` vs `0.0` 需实测确认判据方向（若判为 distinct → 仍更新，终态不变，等价性不破）——列入验收矩阵。
- ③ **终态不变量**：同一输入批，旧/新实现在**同一份影子库**上的表内容（按主键排序、逐列含 NULL 标记）**完全一致**。
- ④ **幂等重放**：同一批连写两次，第二次 `changed=0` 且表指纹不变。

### T3 写入路径内存增长排查（调查件，并入同件）

**目标**：先**归因**，再定修复；不预设修复方案。

**已定位候选（按可疑度）**
1. `qfq_invariant.audit_factor_integrity`：**全表累积**（`frames` 列表 + 一次性 `pd.DataFrame`）+ 全量 `groupby/shift/assign` 多副本（`qfq_invariant.py:357-404`）；
2. `qfq_invariant.verify_reanchor_selfcheck`：单码 50 万行 `fetchdf()`（`qfq_invariant.py:667-720`）；
3. `writers._write_locked`：整批 `conn.register("_tmp_write", df)`，而**写入批大小无上限**（历史上单批达 3,566,210 行）（`writers.py:794`；`data/logs/writers_urgent_invalidation_20260920/04_触发者鉴定_1902前20行.txt:19`）。

**调查动作（只读取证）**
- 分阶段 RSS 采样（采集 / 对齐 / 校验 / 写 / **invariant 自检** 五段），对 stock_minutes 与 etf_minutes 两表各跑一轮；
- 与四问题案「6.3GB 峰值」对照，判定峰值落在哪一段；
- 向客户索取「QFQ-Invariant alloc 失败 ×2」**原文 + 时间戳**（当前代码/日志无命中，无法闭环）。

**诚实边界**：本件**不承诺**「6.3GB 已归因到 invariant」；在客户原文到位前，T3 的输出是**归因报告 + 修复候选清单**，不是已完成修复。

### T4 复权锚漂移清单评估（附带；给出「是否需云端核查」建议）

**结论与建议**
- **需要一次只读云端比对**：云端 `etf_adj_factor` **全历史** × 本地 `fund_adj` 逐 `code-day` 比对（stock 侧同理对 `adj_factor`）。理由：本地库是云端副本，**本地无法自证「历史因子是云端错还是本地错」**；且已证「最新因子 2165/2165 一致」⇒ 分歧只可能在历史段。
- **但不单独立项**：已有跟踪项与判据（`backfill-line-tracking-items.md` 跟踪项 1），且定性为错误一案台阶的显影 ⇒ 建议**并入错误一 T3 收敛窗**，复用「FAIL count 趋势下降」判据。
- **「etf 1→5 未自愈」的前置**：需**两份清单的时间戳对齐**才能谈「未自愈」。当前我方**无该两份清单落盘** ⇒ 需客户提供，或由我方在收敛窗跑一次审计留档；同时按 `qfq_resident_orchestrator.py:509-645` 链路逐环取证（是否触发重锚、是否黄金行校验失败被阻断）。
- **口径提醒**：「13 码 / 5 码」是**采样审计读数**（候选来自除权表 + 因子变化点前 90 天窗），**不等于**全库缺陷码数。

### T5 CLI `--config-dir` 手册勘误（小件）

- **最小修正（Q4 建议：只改文档）**：`README.md` / 手册补记 `--config-dir`；明示
  「默认值 `ROOT/config` **不是**生产 profile；生产须显式 `--config-dir config/profiles/mcp_only`」。
- **备选（行为变更，需另裁）**：让 CLI 默认值解析到 profile（`config/profiles/mcp_only`），使「不带参数启动」即生产口径；须评估对 GUI 启动路径（`daemon_process.py` 显式传 `--config-dir`）与既有测试的影响。

---

## 四、范围与边界

### 改动文件（精确清单，实施期定稿）

| # | 文件 | 改动性质 |
|---|---|---|
| 1 | `quantstudio/pipeline/writers.py` | T2：`_write_locked` upsert 增 `WHERE ... IS DISTINCT FROM ...`（开关化）；可选 `changed` 统计 |
| 2 | `quantstudio/pipeline/writers.py` | T1：批级断点表 DDL + 读/写/清理 API（开关化） |
| 3 | `quantstudio/pipeline/daemon.py` | T1：任务循环接入断点读/写（`daemon.py:557-577`、`daemon.py:627-666`） |
| 4 | `quantstudio/pipeline/qfq_invariant.py` | T3：**分阶段 RSS 采样探针**（只读取证；修复待归因后另立） |
| 5 | `README.md`（+ 手册所在处） | T5：补记 `--config-dir` 与默认值口径 |
| 6 | `tests/test_writer_upsert_skip_identical.py`（新增） | T2 等价性矩阵 + 幂等重放 + NULL/数值边界 |
| 7 | `tests/test_batch_checkpoint.py`（新增） | T1 中断续跑 + 开关关闭零变更 |
| 8 | `docs/evidence/*-acceptance-*.md`（新增） | 验收证据件 |

### 不改动范围（硬边界）

- **不改**任何写入数据的口径：复权公式、`adj_latest_global` 语义、PIT/日期边界、列名/字段/单位契约一律不动；
- **不改** `source_watermark` 语义与主键定义（`source_watermark` 仍是表级增量水位）；
- **不改** `WriteResult.new/updated` 的既有语义与 `batch_audit` 守恒检查（`quality_audit.py:288-313`）；
- **不放宽**任何质量门禁阈值；
- **不删除/不清空**隔离区与既有审计表；
- **不升级** DuckDB（保持 `duckdb==1.5.6` 声明面；放行集合 `{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}`）；
- 不动引擎、策略、回测、注入 API。

### 兼容边界

- T2 开关**默认关** ⇒ 未开启路径逐字节等同现状（含日志文案）；
- T2 开启后**日志文案不变**（仍是「新增 X + 更新 Y」，Y 仍=主键已存在行数）⇒ 客户既有读数口径不突变；
- T1 开关**默认关** ⇒ 不产生新表、不产生新日志；
- T3 探针为**只读**，不改变任何写入行为。

---

## 五、风险描述

| # | 风险 | 等级 | 规避手段 |
|---|---|---|---|
| R1 | `IS DISTINCT FROM` 与旧实现在**浮点/`-0.0`/`NaN`** 上判据不一致 → 终态漂移 | **高** | 逐位等价协议（§六 P1/P3）先行；任何一条不一致即视为**不等价**，回退 |
| R2 | 误把 `updated_rows` 改成「实际变化行数」→ 破坏 `new_rows` 与守恒检查 | **高** | 硬边界（§四）；`new/updated` 语义冻结；`changed` 另立可选字段 |
| R3 | T2 虽减少变化行，但**未消除** delete 相位 ⇒ 不声称「消除 #23645 风险」 | 中 | 验收含 ART index-delete 观测（P5）；文案与通知件避免过度承诺 |
| R4 | T1 断点写入时机错误（先写断点后写数据）→ 崩溃后误跳过 → **数据缺口** | **高** | fail-closed 铁律（§三 T1）；中断续跑终态指纹必须与「一次跑完」逐位一致 |
| R5 | 共享核心文件 `writers.py` 与他线并行改动叠加 | 中 | 每次 edit 后即时 `git diff` 自检 + 精确清单提交；发现他人未提交改动则叠加事实记入 commit message |
| R6 | T3 归因所需客户原文缺失 → 调查件无法闭环 | 中 | 明确列为前置（§三 T3 诚实边界）；未到位时**不宣称闭环** |
| R7 | T4 云端比对为跨域只读操作，可能被他线写窗挤占 | 低 | 排只读窗（DB 静止态），不与他线争窗 |

---

## 六、验收要点

### 功能验收
1. **T2 吞吐**：同一「零新增」批，开启开关后吞吐提升至纯插入的 **≥80%**（对照基线 100 万行/h；目标 ≥200 万行/h），以实测行/h 为证；
2. **T2 等价**：P1 逐位等价协议**全表通过**（24 张 upsert 表）；
3. **T1 续跑**：人为在批 k 中途终止 → 重启 → **只重跑未完成批**（断点记录 + 跳过批清单为证）→ 终态表指纹与「不中断跑完」**逐位一致**；
4. **T5 文档**：按修正后的手册命令（显式 `--config-dir`）启动，任务集与生产 profile 一致。

### 等价性逐位证明协议（T2 专用，从严）
- **P1 影子库双跑**：同输入批 → 旧/新实现分别写**同一份影子库**（或用 `git stash create` 前后两版）→ 导出 `SELECT * FROM t ORDER BY <pk>` 规范化行集 → 逐列逐位（含 NULL 标记）比对，要求**完全一致**；
- **P2 幂等重放**：同批连写两次 → 第二次 `changed=0` 且表指纹不变；
- **P3 边界矩阵**：4 种 NULL 组合（NULL/NULL、NULL/非 NULL、非 NULL/NULL、非 NULL/非 NULL）× 数值边界（`NaN`、`-0.0` vs `0.0`、极值、整数/浮点列）逐格断言；
- **P4 全表覆盖**：对 `writers.py:795-820` 列出的**全部 upsert 表**跑 P1；
- **P5 ART 观测**：批大小 5 万 / 50 万 / 350 万三档，记录行/h 与是否出现 index-delete 报错。

### 场景验收
- 分钟表 1min/5min/15min/30min/60min 五频段 upsert 等价；
- 「全新增 / 全一致 / 部分变化」三类批次各跑 P1；
- 复权相关列（close/pre_close/adj 因子）在 T2 改动前后**终态逐位一致**。

### 边界验收
- 单行 df、空 df（`len(df)==0` 早退路径 `writers.py:703-705`）行为不变；
- 主键含 NULL 行的行为（若存在）维持旧行为；
- 开关关闭时日志、审计字段、执行路径与现状逐字节一致。

### 回归验收
- `tests/test_pit_filter.py`、`test_validator_behavior.py`、`test_full_quality_audit_repair.py`、
  `test_batch_audit*`、`test_truthfulness_regression.py` 套件全绿（注意 `test_pit_filter::test_validator_is_single_chokepoint` 为**基线即红**，见 `docs/handoff/customer-ops-ledger.md:49`，需按基线口径比对）；
- 全量测试对比基线：不新增红项。

---

## 七、质量判据

| 判据 | 门槛 | 证据形式 |
|---|---|---|
| T2 生效 | 零新增批吞吐 ≥ 200 万行/h（对照 100 万） | 实测行/h + 开关前后对照 |
| T2 等价 | 全表逐位等价，**零差异** | 影子库 diff + 边界矩阵单测 |
| T2 审计兼容 | `new`/`updated` 语义与 `batch_audit` 守恒检查零变化 | 单测 + `WriteCountConservation` 复跑 |
| T1 正确 | 中断续跑终态指纹 = 一次跑完指纹（逐位） | 终止-重启实测日志 + 指纹 |
| T1 零回归 | 开关关闭时逐字节等同现状 | 关闭态回归套件 |
| T3 诚实 | 归因报告 + 客户原文（或显式声明未获原文） | 归因报告 + 索取记录 |
| T5 可复现 | 按手册命令启动即达生产 profile | 命令行实录 + 任务集对比 |
| 工程标准 | 改动面 = §四清单；无顺手重构；每任务独立 commit 可单项回退 | `git diff --stat` + 精确清单 |

---

## 八、回退条件

- 任一 P1/P3 等价性断言不通过 → **立即回退 T2，不议「差异极小」**；
- T1 中断续跑出现任何**批被误跳过**（终态指纹不一致）→ **无条件回退 T1** 并重新归因；
- 每任务独立 commit；实施前 `git stash create -u` + `git stash store` 建零副作用回退点；
- **T2/T1 均以开关承载**（`QS_UPSERT_SKIP_IDENTICAL` / `QS_BATCH_CHECKPOINT`，默认关）⇒ 回退可先「关开关」，再按需 `git revert`；
- 推送前保留用户确认闸门（六步流水线步骤 5）。

---

## 九、本轮取证附录（只读实测，2026-09-28）

### 9.1 DuckDB upsert 语义实测（`py 3.11.9` + `duckdb 1.4.5`，与客户环境同版）

| 形态 | 结果 |
|---|---|
| `WHERE t.b IS DISTINCT FROM EXCLUDED.b OR t.c IS DISTINCT FROM EXCLUDED.c` | **OK** |
| `WHERE t IS DISTINCT FROM excluded`（派单候选） | **ERR** `BinderException: Table "t" does not have a column named "t"` |
| `WHERE (t.*) IS DISTINCT FROM (excluded.*)` | **ERR** `BinderException: STAR expression is not supported here` |
| `WHERE (t.a, t.b, t.c) IS DISTINCT FROM (excluded.a, excluded.b, excluded.c)` | **OK** |
| `RETURNING` 观测 | 一致行**不返回**（`(1,1)` 未返回）；变化行 `(2,99)` 与新增行 `(3,3)` 返回 ⇒ 可作 `changed` 计数来源 |

> 结论：派单候选形态需按形态 A/B 改写；形态 B 推荐。

### 9.2 未命中记录（诚实边界）

- 「QFQ-Invariant alloc 失败」在 `quantstudio/` 源码与 `data/logs/` 日志中**无原文命中**（仅派单转述）；
- `06 计数.txt` 所属 2026-09-20 事故（`writers_urgent_invalidation_20260920/03_计数.txt`）的原文为
  `Failed to delete all rows from index = 170` / `database has been invalidated = 168`，
  属 **duckdb 1.5.x ART index-delete fatal 族**（`duckdb#23645`），与本件 T2 同族但**非本件 T3 的同一现象**，需区分归因。

### 9.3 证据文件清单

| 用途 | 路径 |
|---|---|
| 派单原文 | `docs/handoff/dispatch-calendar-20260911.md:3056-3062` |
| 前序同客户方案件 | `docs/mcp-pull-four-issues-fix-design.md` |
| 锚漂移跟踪项 | `docs/backfill-line-tracking-items.md:5-13` |
| 因子表行数读数 | `docs/pipeline-tech-debt.md:244` |
| 阶段时延（invariant 占比） | `data/logs/daemon.log.2026-09-18:394`、`daemon.log.2026-09-17:5284,3940`、`daemon.log.2026-09-21:717` |
| 2026-09-20 库毒化事故 | `data/logs/writers_urgent_invalidation_20260920/03_计数.txt`、`04_触发者鉴定_1902前20行.txt` |
| 客户运维台账 | `docs/handoff/customer-ops-ledger.md` |
| 本轮探针脚本（DuckDB upsert 语义实测） | `docs/evidence/probe-duckdb-upsert-distinct-from-20260928.py`（已随本件归档） |

---

## 十、实施记录（实施期回填）

- 回退点：（待建）
- 共享核心文件叠加事实：（待核 `writers.py` / `daemon.py` 的 `git status --porcelain`）
- T2 commit：（待填）
- T1 commit：（待填）
- T3 归因报告：（待填）
