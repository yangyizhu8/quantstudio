# W2-0.9 框架修复报告（2026-07-28，W2-0.8 审核不通过后的最终收口）

> **状态**：W2-0.9 框架修复已落地（W2-0.8 五项缺陷 A-E + Phase 5 的补完 + 缺陷 C/F 收口 + 全量 2 性能回归修复）。
> 全量测试 **1571 passed, 1 warning**（唯一 warning 为既有 `test_nan_excluded` log 警告，与 W2 无关，原样保留）。
> **正式源库全程逐字节未变**（SHA-256 `56c08b4e7f8ad3e9ff8ba107d4e5445907212483a241b1daa461ae8cc6cc2d40` 与 Phase 0 基线一致）。
> 保留的真实 staging（`data/staging_fin_growth_dividend_20260728`）保持只读证据状态，未重置。
> 本报告为 W2-0.9 最终版（W2-0.8 报告内容已并入并修正）。

## W2-0.9 对 W2-0.8 审核发现的逐项补完

W2-0.8 审核发现 9 项问题，W2-0.9 逐项关闭：

| 审核发现 | W2-0.9 关闭证据 |
|------|------|
| **baseline-delta 不可达**（被 errors_count==0 阻断） | `validate_audit_evidence` 不再要求 `errors_count==0`；改为要求 `baseline_delta_passed=True`；phase_audit 调整顺序：先 delta 再 strict validate；evidence 在 strict validate 前写入含 delta 字段 |
| **worktree 路径未强制** | phase_prepare 非 dry-run 必须 fail-closed BLOCK staging_root 在 Git worktree/项目根/data 目录内；新增 `TestPrepareWorktreeInternalBlocked`（3 测试）；runbook 改为 `D:\QuantStudio_W2_Staging\..._r2` |
| **detached runner 误杀风险** | `_w2_detached_runner.py` 重写：scan-orphans 默认只报告不 kill；kill 为独立显式命令（PID+staging_root+argv 再验证）；run-task 检测同 staging_root 活跃任务 fail-closed；匹配绑定 root/argv/task；launcher 文件名带 task+nonce；`test_w2_detached_runner_safety.py`（9 测试） |
| **reconciliation 配置未执行**（只读 enabled） | `_authority_reconcile` 严格校验 mode/scope（config_lint fail-fast + 运行时 fail-fast）；mode=`purge_non_authoritative`/scope=`full_range_only` 未知值 → BLOCK 不 DELETE |
| **reconciliation 无事务** | 表 DELETE + 可选 watermark DELETE + 后置 source 集合校验位于同一事务（BEGIN/COMMIT/ROLLBACK）；失败 ROLLBACK 不留半删除状态；空表不静默 PASS |
| **writer fallback 缺 div_proc** | W2-0.8 fallback `str_cols` 确实不含 div_proc（已确认 W2-0.8 报告表述不准确）；W2-0.9 已将 `div_proc`/`div_rat` 加入 fallback 白名单 + DESCRIBE 失败记录 warning（不静默）+ `test_div_proc_preserved_when_describe_fails` 测试 |
| **CLI 实际退出码测试不足** | `test_daemon_once_exit_contract.py` 增加真实 main/CLI 子进程退出契约（nonexistent task→非0；task fail→非0；quality-audit none/full；run_once 结构化返回） |
| **强制文档遗漏**（strategy_toolbox/prompt_engineering） | 两文档已补"数据质量契约/不得手工补救 NULL 混源"铁律 |
| **全量 2 failures**（性能回归） | duckdb_data_access/duckdb_provider/ptrade_api 3 文件**已回退到 HEAD**（batch 优化无条件查不存在的表 + 破坏测试替身）；2 failures 修复；性能等价修复组=回退状态 |

## W2-0.9 新增/修改框架文件

**W2 框架正确性修复组**：
- `quantstudio/pipeline/writers.py` — DDL 驱动 VARCHAR 类型保护 + fallback 白名单含 div_proc + DESCRIBE 失败 warning
- `quantstudio/pipeline/daemon.py` — `_authority_reconcile` 完整契约+事务；`run_once` 结构化返回；`--quality-audit` CLI；退出码传播
- `quantstudio/pipeline/config_lint.py` — authority_reconciliation 契约 fail-fast 校验
- `config/collector_tasks.json` — fin_indicator/stock_dividend authority_reconciliation 契约
- `scripts/backfill_fin_growth_dividend_staging.py` — phase_run_task 传 `--quality-audit none`+ledger cross-check+preflight；phase_prepare worktree BLOCK；phase_audit baseline-delta 接入重做；validate_audit_evidence delta policy
- `scripts/_w2_detached_runner.py` — 安全收口（不自动 kill + 同 root 阻断 + 唯一 launcher）

**回测取数纯性能等价修复组**（= 回退到 HEAD，未提交优化）：
- `quantstudio/backtest/providers/duckdb_data_access.py`、`duckdb_provider.py`、`ptrade_api.py` — 回退到 HEAD（git diff 为空，仅 CRLF）。优化（`query_bars_by_count_batch`）因无条件查不存在的表 + 破坏测试替身未达等价，按审核要求回退。

## 测试矩阵（W2-0.9 真实输出）

```powershell
# W2-0.9 专项（13 文件）
python -m pytest -q tests/test_stock_dividend_framework_repair.py tests/test_authority_reconciliation.py tests/test_daemon_once_exit_contract.py tests/test_staging_baseline_delta_audit.py tests/test_w2_detached_runner_safety.py tests/test_fin_growth_dividend_staging_tool.py tests/test_stock_dividend_failure_gate.py tests/test_authoritative_source_policy.py tests/test_financial_dividend_schema_migration.py tests/test_quality_audit.py tests/test_pipeline_guardrails.py tests/test_market_preload_no_daily_cache.py tests/test_provider_frequency_routing.py
# 全量
python -m pytest -q
```

**结果**：W2-0.9 专项 **165 passed**；全量 **1571 passed, 1 warning**（唯一 warning 为既有 `test_pr6b2a_cp3_oracle.py::test_nan_excluded` log 警告，与 W2 无关，原样保留）。

## 新增测试文件

| 文件 | 项数 | 覆盖 |
|------|:--:|------|
| `test_stock_dividend_framework_repair.py` | 6 | div_proc 实施/None/取消保留、数值列仍 coerce、DESCRIBE 失败时 fallback 保留 div_proc、fin_indicator data_source 保留 |
| `test_authority_reconciliation.py` | 11 | full_range 清理；incremental/fallback/未声明/actual-source 不运行；invalid mode/scope fail-fast；cleanup_watermark=false；空表不静默 PASS；事务 rollback；其他表不受影响 |
| `test_daemon_once_exit_contract.py` | 11 | run_once 结构化返回；quality none/full；task 不存在/failed/audit failed；真实 CLI 子进程退出契约 |
| `test_staging_baseline_delta_audit.py` | 6 | clean PASS；target/new/regressed BLOCK；inherited 允许；evidence 字段完整 |
| `test_w2_detached_runner_safety.py` | 9 | scan 只报告；精确匹配不误伤；同 root 阻断；kill 显式+再验证；唯一 launcher 文件名 |

## 行为变更边界（AGENTS.md 铁律）

均为框架行为/正确性变更，已单独说明影响/风险/迁移：
- writer：仅改类型保护（VARCHAR 不 coerce + fallback 含 div_proc + DESCRIBE warning），不改公共 API/列顺序/其他表行为
- daemon：run_once 新增可选 `quality_audit`（默认 full）；返回 None→dict；CLI 新增 `--quality-audit`（默认 full）
- config_lint：新增 authority_reconciliation 契约校验（未声明该键的旧任务不受影响）
- staging 工具：phase_prepare worktree BLOCK（非 dry-run）；phase_run_task preflight + ledger cross-check + `--quality-audit none`；phase_audit baseline-delta 接入；validate_audit_evidence delta policy
- 回退的 3 个性能文件：与 HEAD 一致，无行为变更


## 背景：W2 第一次真实执行暴露的 5 项框架缺陷

W2 真实执行（Phase 0-4）跑通 fin_indicator（数据任务 success）后，stock_dividend 暴露 5 个框架代码缺陷（详见 `output/w2_fin_growth_dividend_20260728/phase4_defect_report.md`）。本工作包在**本地**修复全部缺陷 + 临时 DB 测试 + 文档更新，不触碰真实 staging / 正式库 / Git。

## 修复内容（代码调用链 + 变更）

### 缺陷 A：writer DDL 驱动 VARCHAR 类型保护
- **根因**：`writers.py:473-485` 对 object dtype 列执行 `pd.to_numeric(errors="coerce")`，`str_cols` 白名单不含 `div_proc` → `"实施"`→NaN→NULL。
- **修复**：写入前 `DESCRIBE {table}` 取目标列实际 DuckDB 类型，VARCHAR 列一律不进 `to_numeric`；DESCRIBE 取不到时回退 str_cols 白名单（含新增 div_proc 等）。保留数值列 dtype/空值/异常行为、公共 writer API、其他表写入语义不变。
- **文件**：`quantstudio/pipeline/writers.py`

### 缺陷 B：通用 authority_reconciliation 契约
- **根因**：authority-locked 表 full_range 回填后，upsert 只触及本次有数据的 code，旧 NULL/akshare 历史行残留 → SourceTraceability/AuthoritySourceViolation 审计失败。无纯配置路径。
- **修复**：新增 `ResidentCollector._authority_reconcile(task, source, table, batch_id)`。触发条件（全部满足）：`authority_reconciliation.enabled=true` + `authoritative_source` 非空 + `allow_fallback=false` + `mode=full_range`。执行 `DELETE FROM <table> WHERE data_source IS DISTINCT FROM <auth>`（NULL-safe）+ 同表非权威 watermark 清理 + 后置校验 source 集合恰为 `{auth}`。cleanup 失败 → batch failed，不推进水位。fin_indicator/stock_dividend 走**同一通用路径**（per_stock 与 per_trade_date 两个成功分支均接入）。无写死表名。
- **配置**：`config/collector_tasks.json` 为 fin_indicator/stock_dividend 增加 `authority_reconciliation: {enabled, mode, scope, cleanup_source_watermark}` 契约。
- **文件**：`quantstudio/pipeline/daemon.py`, `config/collector_tasks.json`

### 缺陷 D：run_once/CLI 退出码传播
- **根因**：`run_once` 丢弃 `execute_task`/`_run_full_quality_audit` 返回值；CLI once 路径不按结果设退出码 → batch failed 但 exit 0。
- **修复**：`run_once` 返回结构化 `{task_found, task_ok, audit_run, audit_ok}`；CLI 按 task 不存在/failed/audit failed → exit 1。staging `phase_run_task` 增加 batch ledger final status cross-check（独立验证 batch status=success）。
- **文件**：`quantstudio/pipeline/daemon.py`, `scripts/backfill_fin_growth_dividend_staging.py`

### 缺陷 E：--quality-audit full|none + 分阶段 audit 分离
- **根因**：单任务后立即全库 audit，尚未回填的兄弟目标表导致当前任务假失败。
- **修复**：daemon CLI 新增 `--quality-audit {full,none}`（默认 **full**，生产/常驻语义不变）；staging `phase_run_task` 显式传 `--quality-audit none`，最终统一 audit 由 Phase 6 执行。
- **文件**：`quantstudio/pipeline/daemon.py`, `scripts/backfill_fin_growth_dividend_staging.py`

### Phase 5：staging baseline-delta audit
- **根因**：正式源库有与 W2 无关的既有错误（balance_statement/WatermarkConsistency），直接要求 staging 全库 errors_count=0 会被继承问题阻断。
- **修复**：新增 `run_baseline_delta_audit`（staging 工具）。对源库只读跑一次 baseline audit，对 staging 跑相同 audit，分类：`target_table_errors`（fin_indicator/stock_dividend 必须为空）/ `inherited_unchanged_errors`（非目标表同 key 同量，允许）/ `new_errors`（源库没有的，BLOCK）/ `regressed_errors`（count 增加，BLOCK）。PASS = target/new/regressed 全空。baseline 错误从不静默删除，全部分类记录在 `audit_evidence.baseline_delta_audit`。phase_audit 集成为通过门控。
- **文件**：`scripts/backfill_fin_growth_dividend_staging.py`

### 缺陷 C/F：Git worktree 外 staging 路径 + preflight
- **根因**：`git.exe` 持有 worktree 内 staging.db，daemon 2506 次真实 IO 失败。
- **修复**：`docs/staging-runbook.md` 强制长跑 staging DB 位于 Git worktree 外（推荐 `D:\QuantStudio_W2_Staging\...`）+ 任务前 preflight 独占检查 + 腾讯电脑管家白名单说明 + IO error 必须 failed（不得容忍失败率后继续）。

## 新增测试（4 文件，23 项）

| 文件 | 项数 | 覆盖 |
|------|:--:|------|
| `test_stock_dividend_framework_repair.py` | 5 | div_proc 实施/None/取消保留、数值列仍 coerce、fin_indicator data_source 保留 |
| `test_authority_reconciliation.py` | 5 | full_range 清除非权威行+watermark；incremental/allow_fallback/未声明不执行；其他表 watermark 不受影响 |
| `test_daemon_once_exit_contract.py` | 8 | run_once 结构化结果；quality none/full；task 不存在/failed/audit failed；CLI flag 存在 |
| `test_staging_baseline_delta_audit.py` | 5 | clean PASS；target/new/regressed BLOCK；inherited 允许 |

现有 `test_fin_growth_dividend_staging_tool.py`（58 项）已适配 ledger cross-check + `--quality-audit none`（fake child 默认写 success batch；real-subprocess E2E 加 `--quality-audit none`）。

## 测试矩阵

```powershell
# W2-0.8 专项
python -m pytest -q tests/test_stock_dividend_framework_repair.py tests/test_authority_reconciliation.py tests/test_daemon_once_exit_contract.py tests/test_staging_baseline_delta_audit.py tests/test_fin_growth_dividend_staging_tool.py tests/test_stock_dividend_failure_gate.py tests/test_authoritative_source_policy.py tests/test_financial_dividend_schema_migration.py tests/test_quality_audit.py tests/test_pipeline_guardrails.py
# 全量
python -m pytest -q
```

**结果**：W2-0.8 专项全 PASS；全量 **1529 passed, 1 warning**（基线 1484 + 新增 45 = 1529；唯一 warning 为既有 `test_pr6b2a_cp3_oracle.py::test_nan_excluded` log 警告，与 W2 无关，原样保留）。

## 行为变更边界（AGENTS.md 铁律）

本次均为**框架行为/正确性变更**（非纯性能优化），已按铁律单独说明影响、风险、迁移方案：
- writer：仅改类型保护逻辑（VARCHAR 列不 coerce），不改公共 API/列顺序/空值语义/其他表行为
- daemon：run_once 签名**新增可选参数** `quality_audit`（默认 "full"，向后兼容）；返回值由 None 变 dict（调用方未读则无影响）；CLI 新增 `--quality-audit` 参数（默认 full，不传则行为不变）
- staging 工具：phase_run_task 新增 ledger cross-check + 传 `--quality-audit none`；phase_audit 新增 baseline-delta 门控
- 配置：collector_tasks.json 增加 authority_reconciliation 契约（不影响未声明该键的旧任务）

## 下一步（待用户明确授权）

1. 框架修复审核通过后，使用 **Git worktree 外**的新 staging 路径从 Phase 2 重新执行完整 W2
2. 实际 promotion 与框架代码/文档 GitHub 同步**分别**等待用户下一次明确确认
