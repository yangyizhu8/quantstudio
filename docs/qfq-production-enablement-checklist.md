# QFQ Rebase 生产启用 Checklist 与三步渐进启用手册

> 文档版本：2026-07-31
> 适用范围：全市场 raw 准入（- 档，5487 只）后的 QFQ 重锚（rebase）生产启用。
> 强约束（2026-07-31 更新）：**`enabled=true` 已于本日经用户明确确认开启，进入三步渐进【步骤A observation】期**（daemon 每轮 fail-closed，不写生产价格）。daemon 停止/启动由用户控制；**涉及 `--allow-production` 的写库命令（bootstrap / reconcile）须逐步骤显式确认**；正式库变更前必须备份；本步配置改动已先提交 GitHub（铁律：生产配置变更先同步再启用）。**

---

## 0. 前置结论（批次2 canary 已通过）

- 9/9 canary ETF committed（含 5 只 TICK_TOLERANCE，eps=1e-3 生效）+ 守恒 + 正式库未污染。
- eps=1e-3 是**精准放宽**：放行 ≤1tick 的 tick 噪声，仍拦截真实 >1tick 数据源不一致（精度探针 159915 有 1 行 |Δ|>0.001 被门控正确拦截）。
- 多日常驻验证（staging，本批新增）：bootstrap→增量 reconcile→空闲轮，幂等、无错误重锚、守恒全绿（见 §6）。

---

## 1. 生产启用 Checklist（最终检查）

### 1.1 系统状态
- [ ] **daemon 停止**（无 `collector_run.lock`，无运行中编排进程）
- [ ] **正式库备份完成**（`data/quantstudio.db` + `data/qfq_aux.db` 备份到带时间戳副本；正式库变更前必须）
- [ ] **miniQMT 可用** + `trade_calendar` 完整（`xtdata.get_trading_dates` 已填充，上海午夜口径）
- [ ] **磁盘空间充足**（全历史 fresh 缓存：ETF/股票分钟线 + 复权因子；按 5487 只估算）

### 1.2 功能验证
- [ ] **全量回归 0 failed**（含批次2 eps 测试，当前 222 passed）
- [ ] **全市场 raw 准入预检完成**（- 档 5487 只，批次2 准入清单就绪）
- [ ] **dry-run 事件发现正常**（编排器 discovery 在 dry-run 下产出 trigger_queue，0 dead_letter）
- [ ] **canary committed + 守恒**（批次2 结论）
- [ ] **多日常驻稳定**（staging 多轮验证，见 §6）

### 1.3 配置准备
- [ ] **准入名单配置就绪**（`config/qfq_rebase_admissible_securities.json` 等，- 档 5487 只）
- [ ] **`collector_tasks.json` 的 `qfq_orchestrator` 块 ready**（`enabled` 默认 `false`，待用户确认后改 `true`）
- [ ] **bootstrap_plan 生成**（首次启用前必须跑一次 `bootstrap-plan` 确认候选范围，再 `bootstrap-run` 建基线）

### 1.4 关键启用约束（来自多轮验证实测，务必遵守）
- **启用顺序：必须先 `bootstrap-run` 建基线，再 `reconcile-once` 做增量。**
  `reconcile-once` 在 `require_bootstrap=true` 且无可匹配 completed bootstrap 时 **fail-closed**（不处理 trigger、不推进水位）。
- **bootstrap 基线必须完整覆盖准入名单且 0 blocked 项。**
  `bootstrap-plan --admissible` 必须按 `by_asset` 将 5487 只 STOCK/ETF 全部直接写为
  `pending`，不依赖分红/因子候选表。`bootstrap_completed` 判定要求本轮证券级状态机
  `pending/in_progress/blocked/failed/dead_letter` 计数全为 0；任一 blocked 项都会让
  整条 reconcile 流水线 fail-closed。`excluded` 是名单外旧候选的合法审计终态，
  不阻止完成，但只允许由 `bootstrap-plan --admissible` 以
  `block_reason=NOT_ADMISSI-LE` 生成，不得用于掩盖准入证券的执行失败。明确废弃的历史
  run 可标记为 `superseded`，但当前 run 的 blocked/failed 禁止这样处置。因此批次2的
  精度探针 159915（已知 blocked）**不得进入生产 bootstrap 名单**——它属于
  「观测/排查」对象，不应放入准入名单。
- **版本标识**：`bootstrap_run.schema_version` 必须 = 当前 `SCHEMA_VERSION`（**reanchor-2.1**，v2.4 B-3a 升级；存量 2.0 bootstrap 判不匹配 → fail-closed 重建，由 B-3b 显式 migration runner `quantstudio.pipeline.qfq_schema_migration` 处理：`python -m quantstudio.pipeline.qfq_schema_migration --db <staging> --allowed-root <root> [--apply]`，默认 dry-run，正式库硬拒绝），
  `config_hash`/`baseline_version` 落库为 NULL 可跳过对应校验。
- **B-3b.3 migration report contract**: the final report path is reserved with `O_CREAT|O_EXCL` before any database read-write access; no temporary publish file and no `os.replace` are used.
  Report states are `PENDING`, `DRY_RUN_COMPLETE`, `ROLLED_BACK`, `MIGRATION_COMMITTED`, `ALREADY_CURRENT`, and `FAILED_PRECHECK`.
  If report/audit processing fails after COMMIT, the runner raises `MigrationCommittedReportError`; do not retry apply blindly. Reopen the database and generate a fresh report through the COMPLETE_2_1 already-current audit path.

---

## 2. 三步渐进启用方案（操作手册）

### 步骤 A：Observation（dry-run，1-2 个交易日）
- **操作**：`enabled=true` + `require_bootstrap=true`（无 completed bootstrap）。daemon 启动后惰性建 qfq 表，每轮 `run_post_ingest` **fail-closed**（连 discovery 都不执行，直接结束本轮），不写生产价格。仅用 CLI 只读命令观察（`status` / `show-pending` / `bootstrap-audit`）。
- **观察**：daemon 与编排器集成无回归（连续多轮 fail-closed 干净、无崩溃/异常、qfq 表正常建出）+ 无 dead_letter / 水位异常。
- **重要修正（实测 2026-07-31）**：fail-closed 发生在 discovery **之前**，故 Step A 期间**看不到 `triggers_found > 0`**（trigger_queue 不会增长）。`triggers_found > 0` 的实际可见时点是 Step - 建完 completed bootstrap 之后。Step A 的真正价值 = 验证 daemon 与 QFQ 编排器集成稳定、无回归。
- **通过条件**：daemon 连续 N 轮（跨 1-2 交易日）fail-closed 干净、qfq 表已建、`dead_letter = 0`、无异常日志。

### 步骤 -：Canary（12 只证券，2-3 个交易日）
- **canary 子集机制（2026-07-31 已实现）**：`config/qfq_canary_securities.json` 包含 12 只正式库中实际存在的实施分红候选，均属于 STOCK 准入集，并明确排除精度探针 `159915`。当前正式库不存在“ETF 因子观察候选 ∩ ETF 准入集”，因此不以无效 ETF 凑数；ETF 过滤路径由单元测试覆盖。
- **操作顺序**：备份正式库 → `bootstrap-plan --codes config/qfq_canary_securities.json --execute --allow-production` → `bootstrap-run --execute --allow-production`（建基线，**必须 0 blocked**）→ `reconcile-once --codes config/qfq_canary_securities.json --execute --allow-production` 增量。bootstrap 必须先于 reconcile（否则 fail-closed）。Scoped reconcile 仅处理名单内证券并强制 hold 全局水位；Step C 全量周期才允许省略 `--codes`。
- **观察**：committed + raw/back 守恒 + 无 dead_letter。
- **通过条件**：canary 名单内证券 **100% committed** + **raw/back 守恒** + dead_letter=0。

### 步骤 C：全市场（准入名单全部 5487 只）
- **操作**：放开准入名单全部证券，持续监控。
- **监控**：每日检查 committed / dead_letter / 水位（watermark）/ pending_backfill 超期。
- **稳定标准**：连续 **5 个交易日** committed 率 100%（准入名单内）、dead_letter 累计 0、pending_backfill 超期 0、正式库 raw/back 守恒 → 确认「生产就绪」。

> 监控工具：`scripts/qfq_rebase_monitor.py`（只读，支持 `--once` 快照 / `--watch <秒>` 持续）。

---

## 3. 回退方案

- **紧急关闭**：`enabled=false`（daemon 走旧水位路径，不再触发 rebase；已写入的 `*_front` 不受影响，属历史重锚结果）。
- **数据回退**：用 §1.1 的正式库备份恢复（`quantstudio.db` + `qfq_aux.db`）；恢复前先停 daemon。
- **验证**：关闭后跑一轮 daemon（或一次 `--override enabled=false` 的 reconcile 观察），确认 `qfq_cycle_run` 仍正常推进、水位按旧路径更新、无异常报错。
- **部分回退**：若仅某类证券异常，可将该类从准入名单剔除并重做 bootstrap（不污染其他类）。

---

## 4. 稳定性指标（运维看板）

| 指标 | 目标 | 来源 |
|---|---|---|
| 准入名单内 committed 率 | 100% | `qfq_reanchor_event` / `qfq_trigger_queue` |
| dead_letter 累计 | 0 | `qfq_trigger_queue.status='dead_letter'` |
| pending_backfill 超期（>7天） | 0 | `qfq_pending_backfill` |
| detector_degraded | 0（最近一轮） | `qfq_cycle_run.detector_degraded` |
| 正式库 raw/back 守恒 | 误差 < 1e-6 | `*_front` 列 checksum 比对 |

---

## 5. 交付物（本阶段，均未 commit/push）

1. `scripts/qfq_rebase_monitor.py` — 只读监控脚本（快照 / 持续 / 告警）
2. `scripts/qfq_batch2_multiround.py` — staging 多轮验证（bootstrap→增量→空闲）
3. `data/staging_batch2_20260730/batch2_multiround_report.json` — 多轮验证报告
4. 本文档 `docs/qfq-production-enablement-checklist.md` — checklist + 三步启用手册 + 回退方案
5. **状态声明（2026-07-31 更新）**：`qfq_orchestrator.enabled` 已按用户确认改为 `true`，进入【步骤A observation】期（daemon 每轮 fail-closed，不写生产价格）。该配置改动已先提交 GitHub（铁律：生产配置变更先同步再启用）。Step A 由用户启动 daemon 观察；Step - 起涉及 `--allow-production` 写库命令，须逐步骤显式确认。**

---

## 6. 多轮验证报告摘要（staging，2026-07-31）

| 轮次 | 阶段 | 动作 | committed | blocked | dead_letter | 守恒 |
|---|---|---|---|---|---|---|
| 1 | bootstrap | 9 只 ETF 批量重锚（0 blocked，clean） | 9 | 0 | 0 | ✓ |
| 2 | reconcile 增量 | 注入 9 增量 trigger | 9 | 0 | 0 | ✓ |
| 3 | reconcile 空闲 | 无新事件 | 0 | 0 | 0 | ✓ |
| 4 | reconcile 空闲 | 无新事件 | 0 | 0 | 0 | ✓ |
| 5 | reconcile 空闲 | 无新事件 | 0 | 0 | 0 | ✓ |

验证结论：
- **幂等性**（重复执行不重复写价事件）：✓
- **无错误重锚**（空闲轮 0 新增 reanchor_event）：✓
- **全轮守恒**（etf_minutes `*_front` 不变，误差 < 1e-6）：✓
- **trigger 粒度修复**（159205 注入 2 个 `factor_observation` ex_dates → 2 个 committed 重锚事件，全 ex_dates 枚举）：✓
- 全程 dead_letter=0、detector_degraded=0、pending_backfill 超期=0。

> 说明：本工作区 QFQ 编排器此前从未初始化（`data/quantstudio.db` 无 `qfq_*` 表），故
> 多轮验证在 staging 副本（仅含 canary ETF + 近期窗口 + 已填充 `trade_calendar`）上跑真实
> 编排路径（`bootstrap-run` / `reconcile-once` + 真实 xtquant 取数）。门控与生产代码为同一份。

---

## 7. Canary 子集与 `--codes` 过滤（Step - 准备，2026-07-31 已完成）

### 7.1 子集名单文件
`config/qfq_canary_securities.json` 当前包含 12 只 6 位裸码：

```json
{
  "codes": [
    "000006", "000007", "001201", "002005", "002006", "300001",
    "301000", "600004", "601002", "603000", "605001", "688001"
  ]
}
```

上述代码在生成时均满足“正式库实施分红候选 ∩ STOCK 准入集”，并明确排除
精度探针 `159915`。当前正式库不存在“ETF 因子观察候选 ∩ ETF 准入集”，因此
本轮生产 canary 不加入不会进入 bootstrap 计划的 ETF；ETF 分支仍有过滤单测覆盖。

### 7.2 范围限定机制
- `bootstrap_plan(conn, as_of_ms=..., codes_filter=...)` 在 DuckDB 的
  `stock_dividend(div_proc='实施')` 和 SQLite 的 `qfq_factor_observation` 两类候选查询中
  使用参数化 `code IN (...)`，只将指定代码固化为 bootstrap item。
- CLI `bootstrap-plan --codes` 接受逗号分隔的 6 位裸码，或 JSON 数组/包含 `codes`
  数组的 JSON 文件；空名单和非法代码 fail-closed，拒绝退化为全量计划。
- `bootstrap-run` 按 `run_id` 执行已固化的 item，无需也不应再次解释 `--codes`。
- Step C 不传 `--codes` 即恢复原有全量候选语义；公共返回结构、重锚门控和撮合逻辑不变。

### 7.3 Step - 命令
```bash
# 先备份 data/quantstudio.db 与 data/qfq_aux.db
python -m quantstudio.pipeline.qfq_orchestrator_cli \
  --db data/quantstudio.db --aux-db data/qfq_aux.db \
  --override enabled=true --execute --allow-production \
  bootstrap-plan --codes config/qfq_canary_securities.json

python -m quantstudio.pipeline.qfq_orchestrator_cli \
  --db data/quantstudio.db --aux-db data/qfq_aux.db \
  --override enabled=true --execute --allow-production bootstrap-run

python -m quantstudio.pipeline.qfq_orchestrator_cli \
  --db data/quantstudio.db --aux-db data/qfq_aux.db \
  --override enabled=true --execute --allow-production reconcile-once \
  --codes config/qfq_canary_securities.json
```

计划和执行验收：12 只候选、无 `159915`、`blocked=0`；scoped reconcile 仅处理
名单内证券并保持全局水位 held。随后检查 committed、raw/back 守恒和 `dead_letter=0`；
Step C 执行全量 reconcile 时才省略 `--codes`。

## 8. MCP cutover B-4 staging 副本演练（2026-08-05，本地完成，CodeBuddy 独立复审通过）

### 8.1 命令与安全门

```powershell
python scripts/qfq_b4_staging_drill.py --run-id b4_preflight_20260805
python scripts/qfq_b4_staging_drill.py --run-id b4_20260805_final --execute
```

- 默认 preflight 0 数据库写、0 run-dir 写；全量执行只写 `output/mcp_migration/b4_20260805_final/`。
- 副本复制窗口同时持有 `.daemon.lock` / `.collector_run.lock`；正式库 migration runner 继续绝对硬拒绝生产路径。
- 需要保守磁盘预留：`5 × main size + aux size + 10 Gi-`，用于 baseline、normal/recovery 迁移分支和 shadow/checkpoint 增长。
- 用户已明确接受 TD-42：COMMIT 后 report 灾难中断时，以 D- 物理 schema 为权威，使用新 report 路径执行 COMPLETE_2_1 already-current 审计恢复。

### 8.2 全量结果

- 运行目录：`output/mcp_migration/b4_20260805_final/`，总计约 43.657 Gi-（单次 run 近似值）。
- baseline：COMPLETE_2_0；normal：COMPLETE_2_1；recovery：COMPLETE_2_1。
- normal report 状态：DRY_RUN_COMPLETE / ROLLED_BACK / DRY_RUN_COMPLETE / MIGRATION_COMMITTED / ALREADY_CURRENT。
- recovery：`after_commit_before_report` 后 D- 已为 COMPLETE_2_1；新 report 路径恢复为 ALREADY_CURRENT。
- MCP 离线 bootstrap：trigger 数不增加；因子首轮新增/修订均为 0。
- B-4 evidence-time pre-B-5 dividend discover: first pass 2181, immediate replay 0. This remains historical pre-B-5 evidence; the current B-5 discovery-baseline/CAS implementation has passed independent final review.
- `qfq_active_cutover=0`；10 张含 generation 的表中 `mcp-gen1` 全为 0；未越过 B-6。
- 正式主库与 aux 的 size/mtime_ns/SHA-256 前后完全一致。

### 8.3 当前门禁

B-4 evidence-time gate: B-4 review allowed local B-5 implementation. B-5 has now passed independent final review (P0=0/P1=0/P2=3), and GitHub synchronization has separate explicit post-repair confirmation. Formal migration and mcp-gen1 active activation remain unauthorized.

### 8.4 Windows hard-crash 回归纪律

- `after_commit_before_report` 位于 durable COMMIT 后、正常 connection cleanup/report 前。
- Windows 真实 `os._exit(92)` 测试必须严格串行；不得与另一 DuckDB pytest 进程并发。
- exit code 必须精确为 92；`0xC0000005` 不可接受，不得放宽。
- 最终串行证据：原始测试 20/20；migration+B-4 87 passed/1 skipped；扩展回归 827 passed/1 skipped。

## B-5 local staging gate (2026-08-06)

Before any future B-6 activation, verify the following on a staging copy only:

- [ ] `qfq_source_cutover` is `baseline_building` or `baseline_validated`; it is not `active`.
- [ ] `aux_db_path` is immutable and points to a generation-specific SQLite file. Initialize it only with `qfq_orchestrator_cli aux-init`; missing dynamic aux files must fail closed.
- [ ] `baseline-build --execute` records the current `stock_dividend(div_proc='??', ex_date IS NOT NULL)` payload hashes and creates no triggers.
- [ ] The first dynamic discover after baseline produces zero net historical dividend triggers; a true new logical key creates a nullable-applied pending row and a v2 trigger in one transaction.
- [ ] Repeat discovery is idempotent; `audit_pending_slots` reports zero orphan, generation-mismatch, and payload-mismatch rows.
- [ ] Trigger/cycle/bootstrap/watermark/backfill/event/capture audits are filtered to the same `(price_source, source_generation, cutover_id)`.
- [ ] Legacy `xtquant-legacy` behavior and the frozen pre-B-5 dividend baseline (first pass 2181, immediate replay 0) remain unchanged.
- [ ] No production main DB or production aux DB was opened read-write. GitHub synchronization is permitted only by the explicit post-repair confirmation recorded on 2026-08-06; it does not authorize production migration or mcp-gen1 activation.


## B-6 local/staging implementation gate (2026-08-06)

- PyQt manual full pulls of the four QFQ-managed price tables own a one-task
  coordination cycle; direct watermark advancement is prohibited. A passed
  post-ingest gate commits the candidate; a held/failed gate leaves the old
  watermark and emits a GUI warning.
- `cutover-evidence` freezes immutable main/aux table evidence. Full-table hashing is streamed in bounded batches; it must not materialize multi-gigabyte price tables in Python. Re-running with
  identical content is idempotent; a changed evidence payload cannot overwrite
  the manifest.
- `cutover-activate` is staging-only and verifies evidence, expected-old
  active-pointer CAS, legacy stale-cycle interruption, pending-intent
  supersede, and retirement of all legacy non-terminal trigger states,
  including `scheduled`. `dead_letter` and `committed` remain untouched.
- Fault injection must prove that failures before COMMIT roll back the pointer,
  cutover status, cycle, intent, and trigger changes as one transaction.
- The command rejects the configured formal DB even with `--allow-production`;
  no formal migration or real `mcp-gen1` activation is authorized by B-6 local
  implementation.


## B-6 WP4 command gate (2026-08-06)

- `cutover-activate --dry-run` is read-only: `transaction_started=false`, no
  evidence file, no database mutation, and no formal path bypass.
- `cutover-prep-staging --source-db ... --source-aux ... --dest ...` requires
  explicit staging/hermetic sources; it holds both framework locks, rejects
  non-empty WAL/journal sidecars, verifies source/staging SHA-256, and writes
  exclusive marker/manifest files.
- `cutover-canary --codes 510500,159919,000001` resolves the active dynamic
  identity, checks baseline/replay, forces global watermark hold for scoped
  validation, and performs staging-only abort recovery before canary execution.
- Formal main/aux migration and formal `mcp-gen1`/active activation remain
  prohibited regardless of `--allow-production`.
