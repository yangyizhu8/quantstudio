# QFQ Rebase 生产启用 Checklist 与三步渐进启用手册

> 文档版本：2026-07-31
> 适用范围：全市场 raw 准入（B 档，5487 只）后的 QFQ 重锚（rebase）生产启用。
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
- [ ] **全市场 raw 准入预检完成**（B 档 5487 只，批次2 准入清单就绪）
- [ ] **dry-run 事件发现正常**（编排器 discovery 在 dry-run 下产出 trigger_queue，0 dead_letter）
- [ ] **canary committed + 守恒**（批次2 结论）
- [ ] **多日常驻稳定**（staging 多轮验证，见 §6）

### 1.3 配置准备
- [ ] **准入名单配置就绪**（`config/qfq_rebase_admissible_securities.json` 等，B 档 5487 只）
- [ ] **`collector_tasks.json` 的 `qfq_orchestrator` 块 ready**（`enabled` 默认 `false`，待用户确认后改 `true`）
- [ ] **bootstrap_plan 生成**（首次启用前必须跑一次 `bootstrap-plan` 确认候选范围，再 `bootstrap-run` 建基线）

### 1.4 关键启用约束（来自多轮验证实测，务必遵守）
- **启用顺序：必须先 `bootstrap-run` 建基线，再 `reconcile-once` 做增量。**
  `reconcile-once` 在 `require_bootstrap=true` 且无可匹配 completed bootstrap 时 **fail-closed**（不处理 trigger、不推进水位）。
- **bootstrap 基线必须 0 blocked 项。** `bootstrap_completed` 判定要求证券级状态机
  `pending/in_progress/blocked/failed/dead_letter` 计数全为 0；任一 blocked 项都会让
  整条 reconcile 流水线 fail-closed。因此批次2 的精度探针 159915（已知 blocked）
  **不得进入生产 bootstrap 名单**——它属于「观测/排查」对象，不应放入准入名单。
- **版本标识**：`bootstrap_run.schema_version` 必须 = 当前 `SCHEMA_VERSION`（reanchor-2.0），
  `config_hash`/`baseline_version` 落库为 NULL 可跳过对应校验。

---

## 2. 三步渐进启用方案（操作手册）

### 步骤 A：Observation（dry-run，1-2 个交易日）
- **操作**：`enabled=true` + `require_bootstrap=true`（无 completed bootstrap）。daemon 启动后惰性建 qfq 表，每轮 `run_post_ingest` **fail-closed**（连 discovery 都不执行，直接结束本轮），不写生产价格。仅用 CLI 只读命令观察（`status` / `show-pending` / `bootstrap-audit`）。
- **观察**：daemon 与编排器集成无回归（连续多轮 fail-closed 干净、无崩溃/异常、qfq 表正常建出）+ 无 dead_letter / 水位异常。
- **重要修正（实测 2026-07-31）**：fail-closed 发生在 discovery **之前**，故 Step A 期间**看不到 `triggers_found > 0`**（trigger_queue 不会增长）。`triggers_found > 0` 的实际可见时点是 Step B 建完 completed bootstrap 之后。Step A 的真正价值 = 验证 daemon 与 QFQ 编排器集成稳定、无回归。
- **通过条件**：daemon 连续 N 轮（跨 1-2 交易日）fail-closed 干净、qfq 表已建、`dead_letter = 0`、无异常日志。

### 步骤 B：Canary（10-20 只证券，2-3 个交易日）
- **canary 子集机制（2026-07-31 确定）**：`config/qfq_canary_securities.json`（10-20 只代表性 code，含 TICK_TOLERANCE 代表 + 常规 ETF/股票基线；**须排除精度探针 159915**）。`bootstrap-plan` 经 `--codes` 过滤只处理该名单（详见 §7）。
- **操作顺序**：`bootstrap-plan`（dry 确认候选）→ `bootstrap-run --execute --allow-production`（建基线，**必须 0 blocked**）→ `reconcile-once --execute --allow-production` 增量。bootstrap 必须先于 reconcile（否则 fail-closed）。
- **观察**：committed + 守恒（`*_front` 列不变）+ 无 dead_letter。
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
5. **状态声明（2026-07-31 更新）**：`qfq_orchestrator.enabled` 已按用户确认改为 `true`，进入【步骤A observation】期（daemon 每轮 fail-closed，不写生产价格）。该配置改动已先提交 GitHub（铁律：生产配置变更先同步再启用）。Step A 由用户启动 daemon 观察；Step B 起涉及 `--allow-production` 写库命令，须逐步骤显式确认。**

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

## 7. Canary 子集与 `--codes` 过滤（Step B 准备，2026-07-31 确定）

### 7.1 子集名单文件
在 `config/` 下生成 `qfq_canary_securities.json`：
```json
{ "codes": ["510300", "159919", "510500", "512100", "159205", "159215", "159218", "588200", "159740"] }
```
（10-20 只代表性证券：含 TICK_TOLERANCE 代表 + 常规 ETF/股票基线；**须排除精度探针 159915**，
因其有 1 行 |Δ|>0.001 真实差异会被 blocked，导致 bootstrap 整条 fail-closed。）

### 7.2 范围限定机制
- `bootstrap_plan` 当前候选来源 = `stock_dividend(div_proc='实施')` + `qfq_factor_observation`（aux），
  **无内建 code 过滤参数**。
- Step B 准备阶段需给 `bootstrap-plan` / `bootstrap-run` 增加 `--codes` 开关（或读取
  `qfq_canary_securities.json`）以只处理 canary 名单内的 code；该过滤为纯子集裁剪（不改变引擎 /
  门控语义），属最小、可逆实现，单独评估后实施，不混入其他改动。
- Step C 扩展时只需将名单换为全量准入（`config/qfq_rebase_admissible_securities.json`，5487 只），
  其余命令不变。

### 7.3 为何不能「全局 enabled 后靠准入名单自动限范围」
`bootstrap_plan` 枚举的是「有除权/因子观察的证券」，不读准入名单文件；若不加 `--codes` 过滤，
bootstrap 会覆盖全市场候选（数千只），违背 Step B 的 canary 隔离意图。故必须显式子集过滤。
