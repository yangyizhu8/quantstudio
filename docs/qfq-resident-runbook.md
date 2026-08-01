# QFQ 常驻编排器运维手册（Runbook）

> **状态**：常驻编排器 + authoritative rebase 引擎 + 阶段6A 粒度修复全部完成并通过验收。
> R3 真实 staging 验收 committed>0 证实功能可用。**生产配置仍关闭**：`qfq_orchestrator.enabled=false`、
> `factor_refresh_enabled=false`。**日期**：2026-07-30

> ⚠️ 本 runbook 覆盖常驻 QFQ 编排器的运维操作：启用前检查、bootstrap、日常协调、
> degraded 处置、故障排查、回退。**生产启用必须取得用户明确部署确认**，且须先通过
> staging 演练 + 全量回归 + 部署门控。在确认前禁止把 `enabled` 设为 true。

## 1. 架构概览

常驻 QFQ 编排器（`quantstudio.pipeline.qfq_resident_orchestrator.QFQResidentOrchestrator`）
在 daemon 每轮采集周期结束后，自动发现股票/ETF 除权除息事件，用 fresh xtquant 数据
重锚日线/分钟前复权（`*_front`）字段，失败自动登记并恢复，质量门控不通过不推进水位。

### 核心组件
| 组件 | 模块 | 职责 |
|------|------|------|
| 事件发现 | `qfq_event_discovery` | 分红扫描 + 因子 observation + factor_new/revision 触发 |
| 因子刷新 | `qfq_factor_refresh` | 主动拉 adj_factor/fund_adj，全失败→degraded→水位 hold |
| Fresh 采集 | `qfq_fresh_capture` | xtquant download-before-get，单源锁定 |
| 重锚引擎 | `qfq_reanchor_engine` | 单证券 fresh_staged 重锚 + 四态事件审计 |
| 编排器 | `qfq_resident_orchestrator` | 周期编排：recover→discover→claim→reanchor→gate→水位 |
| CLI | `qfq_orchestrator_cli` | 运维命令（status/bootstrap/reconcile/retry/reopen） |

### 数据分层
- **DuckDB 主库**（`data/quantstudio.db`）：价格表（stock_daily/etf_daily/stock_minutes/etf_minutes）
  + QFQ 编排表（qfq_trigger_queue/qfq_cycle_run/qfq_bootstrap_run 等）+ stock_dividend
- **SQLite 辅助库**（`data/qfq_aux.db`）：adj_factor（股票）/ fund_adj（ETF，独立表）
  + qfq_factor_observation / qfq_factor_revision_alert

### 四张受控价格表（水位延迟提交）
stock_daily / stock_minutes / etf_daily / etf_minutes。水位在协调周期 gate 通过后
统一提交，未通过则 hold（`qfq_cycle_run.detector_degraded=1`）。

## 1.5 重锚模型：ratio / fresh_staged / fresh_authoritative_rebase

重锚引擎（`qfq_reanchor_engine.apply_reanchor_for_security`）支持三种模型：

| 模型 | 用途 | 状态 |
|------|------|------|
| `ratio` | 传统比例复权（历史基准） | 生产在用 |
| `fresh_staged` | 单证券 fresh_staged 重锚（合成自洽验证） | 已实现/验证 |
| `fresh_authoritative_rebase` | 权威 oracle 重基（xtquant front 为权威） | 阶段1-6 完成，未生产启用 |

### authoritative rebase 信任边界
- xtquant `get_kline` 返回的 `front` 字段作为**权威 oracle**。
- 引擎不通过经验复权公式重新证明源端 front 的经济语义，只验证：
  - raw 与 fresh oracle 逐 bar 对齐（同名 OHLC/V/AMT）；
  - 写后 front 与 fresh oracle 逐 bar 一致；
  - 行数 / 主键 / `*_back` / volume / amount 守恒。
- **不检测源端语义**：fresh capture 阶段形成的同步 front 污染（同步偏移）无法被确定性条件检测。
  这是信任边界核心风险，接受以 xtquant front 为权威前提（独立 oracle 见 design spec §8）。

### trigger 粒度（增量重基）
- 编排器按 ex_date 拆 trigger；`_claim_and_merge` 按 `(asset_type, code)` 合并为一个工作单元。
- 阶段6A 修复后，`_reanchor_security` 在 rebase 模式下**从 `stock_dividend` + `factor_observation`
  取该证券全量 ex_dates**（而非仅本轮领取的 pending trigger 子集），保证增量轮次一次性全历史
  重基，不丢历史除权日。仅影响 rebase 模式（`ratio`/`fresh_staged` 逐位不变）。

### R3 真实 staging 验收结论
- 2.1 单证券直接 apply（全 ex_dates）→ 4/4 committed（000012 多次分红 / 002864 送转 / 510300 ETF
  / 600000 银行分红），front 调整比率与 xtquant 前复权逐日一致（机器精度 ~1e-16），raw/back/行数守恒。
- 2.2 编排器 reconcile-once → `committed=6/7 单元`（ETF 无 ex_date 不入队），正式库 SHA 不变。
- 对比 fresh_staged 演练 `committed=0` 被乘法校验 BLOCK → **committed>0 证实 rebase 功能可用**。
- 证据：`output/qfq_rebase_r3_20260730/`。

## 2. 部署门控（生产启用前必须全部满足）

> 当前 `enabled=false`。**取得用户明确部署确认**前禁止启用。以下任一项不满足则保持关闭。

- [ ] 全量回归 0 failed（`pytest tests/`）
- [ ] R3 staging 验收 `committed>0` + 守恒通过（`output/qfq_rebase_r3_20260730/`）
- [ ] trigger 粒度修复测试通过（阶段6A：3 用例，全量 qfq 回归 214 passed）
- [ ] miniQMT 可用 + `trade_calendar` 完整
- [ ] daemon 已停止（无 `collector_run.lock`）
- [ ] 全市场 raw 准入预检（扩到 5202 股票 + 1605 ETF，差异率可接受）
- [ ] `dead_letter` 清零 + `pending_backfill` 无超期
- [ ] 至少一个真实除权事件 committed
- [ ] 多日常驻运行稳定
- [ ] 取得用户明确部署确认

### 三步启用（渐进，降低爆炸半径）
1. **observation / dry-run**：只发现事件 + 生成计划，不写价（`reconcile-once` 不加 `--execute`）
2. **canary 少量证券写入**：`bootstrap-plan` 仅含少量证券 → `bootstrap-run --execute`
3. **全市场扩展**：确认 canary 稳定后放开全量 `bootstrap-plan` + `bootstrap-run`

> ts_code 转换：QFQFactorRefresher + daemon._fetch_adj_factor 均用 resolve_ts_codes
> （元数据优先，资产感知前缀 fallback）。ETF 裸码不再误判 BJ。

## 3. 配置

`config/collector_tasks.json` 的 `qfq_orchestrator` 块：

```json
"qfq_orchestrator": {
  "enabled": false,
  "factor_refresh_enabled": false,
  "require_bootstrap": true,
  "price_source": "xtquant",
  "claim_lease_sec": 300,
  "retry_max": 5,
  "retry_backoff_sec": [60, 120, 300, 600, 1800],
  "bootstrap_batch_size": 50,
  "quality_thresholds": {"dead_letter_max": 0}
}
```

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `enabled` | false | 编排器总开关。false 时 daemon 走旧水位路径 |
| `factor_refresh_enabled` | false | 主动因子刷新（独立 opt-in） |
| `require_bootstrap` | true | 无 completed bootstrap 时 fail-closed（不处理 trigger） |
| `price_source` | xtquant | 价格源锁定（只允许 xtquant） |
| `quality_thresholds.dead_letter_max` | 0 | dead_letter 零容忍（gate 不过→水位 hold） |

## 4. CLI 运维命令

> 变更类命令默认 dry-run，`--execute` 才落库。指向正式库需 `--allow-production`。
> staging 演练用 `--db` 指向 staging 副本。

### 全景状态（只读）
```bash
python -m quantstudio.pipeline.qfq_orchestrator_cli \
  --db data/quantstudio.db --aux-db data/qfq_aux.db status
```

### Bootstrap（首次部署建立基线）
```bash
# 1. Step B canary 计划（严格限定配置中的 6 位裸码）
python -m quantstudio.pipeline.qfq_orchestrator_cli --db <DB> --aux-db <AUX> \
  --override enabled=true --execute --allow-production \
  bootstrap-plan --codes config/qfq_canary_securities.json

# 2. 分批重锚（真实 xtquant 取数，每批 bootstrap_batch_size 个）
python -m quantstudio.pipeline.qfq_orchestrator_cli --db <DB> --aux-db <AUX> \
  --override enabled=true --execute --allow-production bootstrap-run

# 3. 审计（completed 须 pending/in_progress/blocked/failed/dead_letter 全 0）
python -m quantstudio.pipeline.qfq_orchestrator_cli --db <DB> bootstrap-audit
```

`bootstrap-plan` 默认扫描全量候选。Step B 必须使用 `--codes`；该参数接受逗号分隔
的 6 位裸码，或 JSON 数组/包含 `codes` 数组的 JSON 文件。空名单和非法代码会
直接拒绝，避免意外退化为全量 bootstrap。计划生成后确认其中无 `159915`，执行
结果须 `blocked=0`。

### 手动协调一轮（紧急/调试）
```bash
# Canary / 指定证券：恢复、发现、领取、gate 均只处理名单内代码
python -m quantstudio.pipeline.qfq_orchestrator_cli --db <DB> --aux-db <AUX> \
  --override enabled=true --execute reconcile-once \
  --codes config/qfq_canary_securities.json

# 全市场：仅 Step C 明确放开后省略 --codes
python -m quantstudio.pipeline.qfq_orchestrator_cli --db <DB> --aux-db <AUX> \
  --override enabled=true --execute reconcile-once
```

`reconcile-once --codes` 与 `bootstrap-plan --codes` 使用相同的 6 位裸码解析规则。
指定范围时，仅恢复、晋升、发现、领取和 gate 检查名单内证券；范围外队列不变。
Scoped reconcile 无论名单内 gate 是否通过，均强制 hold 全局水位，避免局部结果被误判为
全库已完成。只有不传 `--codes` 的全量周期可以提交全局水位。

### 恢复（stale in_progress / retry 到期 / scheduled 晋升）
```bash
python -m quantstudio.pipeline.qfq_orchestrator_cli --db <DB> --execute retry-due
```

### 重开 dead_letter
```bash
python -m quantstudio.pipeline.qfq_orchestrator_cli --db <DB> --execute reopen --trigger-id <ID>
```

## 5. Degraded 处置

**触发条件**：因子刷新某资产类别全部逐码失败（`FactorRefreshError`）。
**效果**：`degraded=True` → gate 强制不通过 → 四价格表水位 hold +
`qfq_cycle_run.detector_degraded=1`。

### 排查步骤
1. 查 `qfq_cycle_run.detector_degraded`：`1` 表示本轮检测器不可信
2. 查 refresh 日志：`[qfq] 因子刷新 degraded（stock_failed=..., etf_failed=...）`
3. 排查根因：Tushare 不可用 / token 失效 / 网络中断
4. 根因解除后，下一轮自动恢复（degraded 是单轮状态，不持久阻断）

> **已知风险（风险2）**：部分码失败（非全失败）当前**不**触发 degraded，失败码可能
> 继续使用旧快照。是否升级为"任意单码失败即 degraded"另立后续正确性变更审核。

## 6. 常见问题

### Q: reconcile-once 报 "require_bootstrap=true 且无可匹配 completed bootstrap"
A: 首次部署未 bootstrap。先跑 `bootstrap-plan` + `bootstrap-run` 建立 baseline，
   或 staging 演练时用 `--override require_bootstrap=false` 临时跳过。

### Q: 重锚 status=blocked（fresh 比例超容差）
A: fresh xtquant 基准与库内基准比例不一致，引擎**正确拦截**（不贸然覆写）。
   这是保守行为，不是 bug。确认 fresh 数据是否来自正确时点。

### Q: dead_letter 持续增长
A: 用 `show-dead-letter` 查看，`reopen` 重试或人工排查。dead_letter_max=0 时
   gate 会持续 hold 水位，须先清零 dead_letter 才能恢复水位推进。

### Q: front 污染样本在真实数据下不被修正
A: 真实数据下重锚常被 BLOCK（基准不一致），front 修正需数据基准一致的场景。
   用 `scripts/qfq_front_fix_verification.py`（合成自洽 fresh）验证修正语义。

## 7. 回退方案

### 紧急关闭（最安全）
```json
"qfq_orchestrator": {"enabled": false}
```
关闭后：不建立 QFQ cycle、不执行 post-ingest、价格表水位走旧 `writer.advance_watermark()`。
非价格任务行为完全不变。

### 回退验证
1. `enabled=false` 后跑一轮 daemon，确认水位正常推进（旧路径）
2. `status` 命令确认无活跃 cycle
3. 四价格表 raw/`*_back` 不受影响（QFQ 只动 `*_front`）

## 8. staging 演练

详见 `scripts/qfq_staging_rehearsal.py`（首轮已通过）+ 设计文档
`docs/superpowers/specs/2026-07-29-qfq-staging-rehearsal-design.md`。

```bash
# 核心守恒闭环演练（aux 全量 + 主库小样本）
python scripts/qfq_staging_rehearsal.py

# front 修正语义验证（合成自洽 fresh）
python scripts/qfq_front_fix_verification.py
```

证据输出到 `output/qfq_staging_rehearsal_<STAMP>/`（守恒报告、前后 CSV、迁移 trace）。
