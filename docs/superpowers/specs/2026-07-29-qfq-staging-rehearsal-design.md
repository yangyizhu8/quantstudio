# QFQ 常驻编排器 Staging 演练设计（首轮：核心守恒闭环）

> 日期：2026-07-29
> 范围：原 QFQ 任务清单「阶段三：Staging 演练」的首轮，聚焦核心数据守恒闭环验证。
> 前置：B1/B2/B3 + C3 已 merge 到 main（PR #6）；生产配置 `qfq_orchestrator.enabled=false`。

## 1. 目标

在带 marker 的安全 staging 副本上，验证 QFQ 编排器一轮协调周期（`reconcile_once`）对真实
股票/ETF 数据的**核心守恒性**，证明启用生产闭环前"不破"：

- **raw OHLC**（open/high/low/close）演练前后逐行精确一致；
- **`*_back`** 四列演练前后逐行精确一致；
- **行数**：stock_daily/etf_daily/stock_minutes/etf_minutes 演练前后行数一致；
- **`*_front`**：对已正确的前复权数据保持不变；对构造的污染样本被修正到 fresh xtquant 值；
- **水位**：quality gate 失败不推进；gate 通过才推进；
- **正式库**：演练前后 SHA 完全一致（证明未污染正式库）。

## 2. 数据环境（混合方案）

- **staging 目录**：`data/staging_qfq_rehearsal_20260729/`
  - 含 `.quantstudio_staging.json` marker（标明 staging 用途、创建时间、源库 SHA）。
  - 子目录 `config/`、`logs/`、`evidence/`（参考已有 staging 惯例）。
- **qfq_aux.db**：全量复制（810MB，因子/observation 主战场）。
- **quantstudio.db（staging 小样本）**：新建 staging DuckDB，从正式库只读 SELECT 抽取：
  - 6 只真实证券的行情：`000012/000025/000060/600000`（股票，stock_daily + stock_minutes）、
    `510300/159919`（ETF，etf_daily + etf_minutes）全量行；
  - 相关 `stock_dividend` 行（6 只 + 全表 schema）；
  - `stock_basic`/`etf_basic`（元数据，供 `resolve_ts_codes`）；
  - `source_watermark`（水位基线）；
  - QFQ 编排器所需的全部 DuckDB 表（`init_duckdb_schema` 建空表后填充）。

候选证券已核实数据完整（见探索记录）：
- 股票 stock_daily ~2076 行、stock_minutes ~4338 行；
- ETF etf_daily ~2075 行、etf_minutes ~32053 行；
- 000012 等有 2026-07-26 的真实"实施"分红记录（含 cash_div_before_tax 等完整字段）。

## 3. 演练脚本

`scripts/qfq_staging_rehearsal.py`（~250 行，单一职责：建环境 → 记基线 → 驱动 → 对比 → 出证据）：

### 3.1 安全前置检查
- daemon 无 lock（`collector_run.lock` 不存在）；
- 正式库 quantstudio.db 的 SHA 记录到 marker（演练后必须不变）。

### 3.2 建 staging 环境
- 建 marker + 子目录；
- 复制 qfq_aux.db 全量（文件复制，不读正式 aux 内容到内存）；
- 建小样本 staging DuckDB：CREATE TABLE AS SELECT 从正式库只读抽取上述数据。

### 3.3 记录基线（演练前快照）
- 6 只证券的 raw/close/close_back/close_front + open/high/low 对应列导出 CSV；
- 全表行数；
- 关键列内容 SHA（每只证券每张表一个 SHA，用于精确比对）。

### 3.4 ETF 因子分库
- 跑 `migrate_split_etf_factors(staging_aux, dry_run=True)` → 记录预检；
- 跑 `migrate_split_etf_factors(staging_aux, dry_run=False)` → 验证 ETF 因子从 adj_factor 迁到
  fund_adj、adj_factor 不含 ETF 行、行数守恒。

### 3.5 驱动协调周期
- 先 CLI `reconcile_once`（不带 --execute）dry-run 确认计划；
- 再 CLI `reconcile_once --execute --override enabled=true`（指向 staging 库，受 `_guard_mutating`
  保护——正式库需 `--allow-production`，staging 无此参数即放行）；
- miniQMT 已确认在运行，XtquantFreshFetcher 可真正下载 fresh 价格。

### 3.6 front 修正验证（构造污染场景）
- 对其中 1 只证券（600000）的若干行 `close_front`/`open_front` 等注入污染值（设为 close+1），
  记录污染前真实值；
- 演练后断言：被污染的行 front 被修正回 fresh xtquant 值；其它行 front 不变。
- 这覆盖"修正"语义（不止证明"不破坏"）。

### 3.7 对比守恒
- 演练后重新导出 CSV + SHA，与基线逐项比对；
- 输出守恒对比表（每只证券每张表：raw 一致/back 一致/行数一致/front 状态）。

### 3.8 正式库 SHA 校验
- 演练后再算正式库 quantstudio.db SHA，与 marker 中记录比对，必须完全一致。

## 4. 证据固化

输出到 `output/qfq_staging_rehearsal_20260729/`：
- `environment.json`：环境信息（Python/duckdb 版本、git HEAD、时间）；
- `baseline_git_status.txt`：演练开始时 git 状态；
- `staging_manifest.json`：marker 内容（源库 SHA、创建时间、staging 路径）；
- `baseline_*.csv` / `post_*.csv`：每只证券每张表的演练前后快照；
- `factor_migration_trace.json`：ETF 因子分库 trace（dry_run 预检 + 实跑行数）；
- `reconcile_summary.json`：协调周期 summary（committed/blocked/水位状态）；
- `conservation_report.md`：守恒对比总表 + 结论；
- `formal_db_sha_check.json`：正式库前后 SHA 比对；
- `risk_and_rollback.md`：风险与回退说明。

## 5. 关键守恒断言（脚本必须全部判定 PASS/FAIL）

1. raw OHLC（open/high/low/close）演练前后逐行精确一致（6 只证券 × 4 张表）；
2. `*_back` 四列演练前后逐行精确一致；
3. 行数：4 张价格表演练前后一致；
4. `*_front`：未污染行保持不变；被污染行被修正到 fresh xtquant 值；
5. ETF 因子分库：迁移后 adj_factor 不含 ETF 行、fund_adj 含原 ETF 行、行数守恒；
6. 水位：协调周期 summary 的 watermarks_committed/held 状态符合 gate 结果；
7. 正式库 SHA：演练前后完全一致。

## 6. 不覆盖（首轮，留待后续轮次）

- xtquant 临时失败 / pending 下轮恢复 / daemon 重启（需人为中断/造异常）；
- future scheduled 到期晋升 / factor revision / 多轮恢复（需多轮驱动）。

## 7. 安全保证

- 全程不写正式库：脚本只读正式库（read_only=True 连接）+ staging marker 闸门 +
  演练后正式库 SHA 校验三重保护；
- staging 库是独立副本，演练失败/异常不影响正式库；
- 任何断言 FAIL → 脚本立即停止并报告，不继续后续步骤。

## 8. 完成判据

- 7 项守恒断言全部 PASS；
- `output/qfq_staging_rehearsal_20260729/` 证据目录完整；
- 正式库 SHA 校验通过；
- 守恒报告明确结论：核心守恒闭环验证通过 / 或列出具体 FAIL 项。
