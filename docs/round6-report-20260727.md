# 第六轮修复报告 — QFQ B-1 六阻断 + validator PIT 独立修订

> 文档状态：草稿（全量回归结果待后台补全，见 §4）
> 同步状态：🚫 **禁止 stage/commit/push/PR/GitHub 同步**（沿用第五轮"暂不批准"，本轮未获批准）

---

## 0. 时间线双轨记录（重要，务必先读）

| 维度 | 值 | 说明 |
|------|----|------|
| **本会话基准日期** | **2026-07-27** | 本轮所有"完成 / 批准 / 实现"以该日期为权威基准。 |
| **工作站文件系统时间** | 2026-07-28 凌晨 | 用户工作站时钟显示值，仅作为文件系统时间戳**如实记录**，绝不作为本轮完成时间。 |

> ⚠️ **双轨声明**：本报告中任何"本轮完成"均以 **2026-07-27（会话基准日期）** 为准。
> 工作站时钟显示的 `2026-07-28 凌晨` 属于未来时间戳（相对会话基准日期），仅记录为
> 操作系统文件系统写入时刻，不参与"完成时间"判定。两者不一致时，以本会话基准日期为准。

---

## 1. 范围与铁律

- **铁律（沿用）**：本地修复 → 汇报 → 用户确认 → 才 GitHub 同步。本轮继续**禁止**任何
  stage / commit / push / PR / GitHub 同步动作。
- **validator PIT 变更独立性**：`validator.py` 的 PIT 去重语义修订是**独立框架行为变更**，
  **不与 QFQ B-1 捆绑**——单独修改、单独回归、单独汇报（见 §3）。
- **性能优化不改行为**：本轮所有修复仅修正语义错误与审计缺失，不改变 `ratio` 模式的既有行为。

---

## 2. 第六轮六项阻断修复（QFQ 重锚引擎）

模块：`quantstudio/pipeline/qfq_reanchor_engine.py`；测试：`tests/test_qfq_reanchor_batch2.py::TestRound6Blockers`（14 项，全部通过）。

| # | 阻断 | 修复要点（文件位置） | 验证测试 |
|---|------|----------------------|----------|
| 1 | `minute_raw_match` 漏检 `raw=NULL` | postcheck(8) 先显式拦截 raw 一侧 `IS NULL OR NOT isfinite() OR <=0`（SQL 三值逻辑陷阱：`ABS(NULL-x)>eps` 结果为 NULL，WHERE 按非真过滤会**静默漏检**），再比逐 bar `abs diff`；`n_invalid>0` 直接抛 `minute_raw_match` 整券回滚 | `test_raw_corruption_rolls_back`（6 组：open NULL / high nan / low 0 / close+3e-9 / high inf / close+0.05 → 断言 `rolled_back` + block_reason ∈ `{minute_raw_match, scale_consistency}`） |
| 2 | fresh minute 未验证交易日 | `stage_fresh_minutes` 新增 `calendar` 参数，逐自然日调用 `CalendarService.is_trading_day`；周末/非开市日整券 BLOCK（`fresh_minutes_non_trading_day`），未知日 BLOCK（`fresh_minutes_unknown_day`）；`calendar=None` 抛 `ValueError` | `test_weekend_bar_blocks`、`test_unknown_day_blocks`、`test_stage_fresh_minutes_requires_calendar` |
| 3 | 失败事件无 `model`/`model_reason` | 扩展 `_record_failure_event`（新增 `minute_ratio_plan`/`postcheck_summary`/`rows_detail`），新增 `_audit_json()` 透传 `model`/`model_reason`/`model_audit{...}`；`blocked`/`rolled_back`/`failed`/`committed` **四态均记录** `fresh_source`/`fresh_capture_id`/`metadata_sha256`/`tick_size`/`freqs`/`coverage` 摘要 | `test_blocked_event_carries_audit`、`test_rolled_back_event_carries_audit`、`test_failed_event_carries_audit` |
| 4 | `tick_size` 写死 0.01 | `resolve_tick_size(asset_type, tol)` 按资产路由：`STOCK=0.01` / `ETF=0.001`；显式 `tol.tick_size` 可覆盖；未知资产抛异常；`run_postchecks` 内 `tick` 变量统一取路由值；事件 `model_audit.tick_size` 记录实际值 | `test_resolve_tick_size_routing`、`test_etf_fresh_staged_tick_routed_and_audited` |
| 5 | 文档门禁未完成 | README.md「QFQ 重锚引擎」小节 + `docs/strategy_toolbox.md` 第 4 节 + `docs/prompt_engineering.md` 第 5.x 节 + `docs/pipeline-tech-debt.md` PIT 修订节 + 决策文档 B-1 状态更新为"已批准/已实现"（见 §5） | 文档审阅（人工） |
| 6 | 全量测试非 1 failed 而是 23 failed | 排查结论见 §4：23 failed 中绝大部分已被并行变更修复，仅剩 validator 旧契约测试（已随 §3 修复）+ 1 项无关测试；目标回归 batch2 + guardrails 已全绿 | 见 §4 |

### 2.1 决策文档状态

`docs/qfq-reanchor-minute-model-decision-20260727.md`：
- 决策请求项已改为 **`[x] 批准 B-1`**（用户批准于 **2026-07-27**，已实现）。
- 记录批准边界：模型显式、`model_reason` 必填、只 `UPDATE` 四 `*_front` 列、`tick` 按资产路由、逐日交易日历校验。
- 页脚注明：第六轮更新 + GitHub 同步待用户批准。

---

## 3. validator PIT 去重语义修订（独立框架行为变更，不与 B-1 捆绑）

模块：`quantstudio/pipeline/validator.py`（L361-389）。测试：`tests/test_pipeline_guardrails.py`（5 项，18 passed）。

### 3.1 旧语义（错误）

`keep="last"`（或按 `ann_date` 降序 `keep="first"`）会**吞掉财务重述版**：同一
`(code, end_date)` 下不同 `ann_date` 的公告版本只留一份，历史重述轨迹丢失，违反 PIT 语义。

### 3.2 新语义（正确 PIT）

```python
if "update_flag" in df.columns:
    upd_rank = pd.to_numeric(df["update_flag"], errors="coerce").fillna(-1)
    order = upd_rank.sort_values(ascending=False, kind="stable").index
    df = df.loc[order].drop_duplicates(subset=pk_cols, keep="first")
    df = df.loc[df.index.sort_values()]   # 恢复原行序（确定性输出）
else:
    df = df.drop_duplicates(subset=pk_cols, keep="last")
```

1. **不同 `ann_date` 版本全部保留**（不回退 `max(ann_date)`，不吞重述版）。
2. 仅对**完全相同** `(code, end_date, ann_date)` 完整主键去重。
3. 有 `update_flag` 时，同完整主键优先 `update_flag=1`；无则确定性重复去重。
4. 输出恢复原始行序，保证确定性。

适用表（主键含 `ann_date`）：`balance_statement` / `fin_indicator` / `income_statement` /
`cashflow_statement` / `stock_float_share`；`fin_indicator` 含 `update_flag` 列。

### 3.3 测试（共 5 项，18 passed）

| 测试 | 断言 |
|------|------|
| `test_financial_dedup_keeps_latest_ann_date`（**契约更新**） | 000159 初版+重述版**都保留**，len==2，ann_date 顺序 `[v1, v2]` |
| `test_financial_dedup_retains_all_ann_date_versions` | 3 个不同 ann_date 全保留，`fixed_count==0` |
| `test_financial_dedup_same_ann_date_keeps_one` | 同完整主键仅留 1 条（无 flag 保留最后一条 5.3e8） |
| `test_financial_dedup_update_flag_priority` | `flag=1` 优先（5.25e8）；不同 ann_date 仍保留 |
| `test_financial_pit_asof_sees_correct_version` | as-of 两次公告之间见初版 5.2e8，之后见重述版 3.69e9 |

---

## 4. 测试结果汇总

| 范围 | 命令 | 结果 | 备注 |
|------|------|------|------|
| batch1 | `pytest tests/test_qfq_reanchor_batch1.py -q` | **89 passed** | 后台已完成（jufyKD） |
| batch2（单独） | `pytest tests/test_qfq_reanchor_batch2.py -q` | **75 passed** | 由 93−18 推算（含 round6 14 项） |
| batch1 + batch2 | 合并 | **164 passed** | 目标 150 → **已超额达成** |
| batch2 + guardrails | `pytest tests/test_qfq_reanchor_batch2.py tests/test_pipeline_guardrails.py -q` | **93 passed** | 含 round6 14 项 + guardrails 18 项 |
| round-6 专项 | `TestRound6Blockers` | **14 passed** | 六阻断覆盖 |
| guardrails PIT | `test_pipeline_guardrails.py` | **18 passed** | 5 项 PIT 测试 |
| 全量 | `pytest -q` | **1408 passed, 0 failed, 1 warning** | 后台已完成（rrnnp4，6m04s）；相对用户实测基准 23 failed → **0 failed** |

> 用户实测基准（本轮触发前）：`23 failed, 1361 passed, 1 warning`。
> 排查：23 failed 中绝大部分已被并行变更修复，仅剩 validator 旧契约测试（已随 §3 修复）
> 与 1 项**无关**测试 `test_ptrade_profile_registered_stock_apis`。全量结果以本表后台补全值为准。

---

## 5. 文档门禁完成情况（第六轮阻断 5）

| 文档 | 状态 | 内容 |
|------|------|------|
| `README.md` | ✅ 已补 | 新增「QFQ 重锚引擎（fresh_staged / ratio 双模型）」小节：双模型选择语义、事务与四态事件、纵深防御 postcheck、tick 资产路由、交易日历校验 |
| `docs/strategy_toolbox.md` | ✅ 已补 | 新增第 4 节：入口 API 签名、模型选择、事务/四态事件、postcheck、tick 路由、交易日历校验 |
| `docs/prompt_engineering.md` | ✅ 已补 | 新增 5.x 节：明确引擎为 pipeline 级（非策略注入）+ Agent 编排铁律 |
| `docs/pipeline-tech-debt.md` | ✅ 已补 | 修正第 2 行旧语义；新增「2026-07-27 validator PIT 去重语义修订」独立章节 |
| `docs/qfq-reanchor-minute-model-decision-20260727.md` | ✅ 已更新 | B-1 状态改为"已批准/已实现" + 批准边界 |

---

## 6. 工作区状态（诚实记录，关键）

`git status --short` 显示工作区**远非干净**，包含大量跨多轮累积的未提交修改：

- **本轮 QFQ/validator/docs 变更**：`qfq_reanchor_engine.py`（新模块，untracked）、`validator.py`、
  `test_qfq_reanchor_batch2.py`、`test_pipeline_guardrails.py`、`README.md`、`docs/strategy_toolbox.md`、
  `docs/prompt_engineering.md`、`docs/pipeline-tech-debt.md`、决策文档（untracked）。
- **历史累积未提交修改（多轮"暂不批准同步"遗留）**：`config/alignment_rules.json`、
  `config/collector_tasks.json`、`quantstudio/backtest/backtest_engine.py`、`providers/duckdb_data_access.py`、
  `providers/duckdb_provider.py`、`ptrade_api.py`、`pipeline/aligner.py`、`config_lint.py`、`daemon.py`、
  `quality_audit.py`、`sources/tushare_adapter.py`、`writers.py`、`skills/quantstudio-strategy-compiler/references/ptrade-api-signatures.json`、
  及多个 untracked 文件（`qfq_calendar.py`/`qfq_observation.py`/`qfq_reanchor_schema.py`、`scripts/`、`ptrade/`、
  `docs/qfq-reanchor-*-report-*.md`、`data/quantstudio.zip`、`.workbuddy/`、`agent_workspace/`、`dist/`）。

> **结论（同步前置条件复核）**：
> - **测试全绿替代条件 ✅ 已满足**：全量 pytest = **1408 passed, 0 failed, 1 warning**（相对用户实测基准 23 failed 已清零）；本轮 QFQ/validator 目标测试全绿。
> - **干净 worktree 替代条件 ❌ 不满足**：工作区含大量跨多轮累积未提交修改，并非"仅 QFQ patch+docs"。
> - **同步仍被禁止 🚫**：无论哪个条件，本轮未获 GitHub 同步批准，严禁 stage/commit/push/PR。
>
> 建议（待批准后）：先用 `git status` 梳理提交粒度，按轮次/模块分批 commit，避免一次性巨型混合提交；
> 提交前再跑全量确认；再发起 PR。`.workbuddy/`、`data/quantstudio.zip`、`dist/`、`agent_workspace/` 等
> 应确认是否纳入版本控制（建议加 `.gitignore` 或排除）。

---

## 7. 下一步

1. ✅ 后台全量回归（rrnnp4：1408 passed / 0 failed）+ batch1（jufyKD：89 passed）已完成，§4 已补全。
2. 🚫 **维持禁止 GitHub 同步**，待用户明确批准。
3. 批准后建议：梳理提交粒度 → 分批 commit → 全量复验 → PR。

---

*本会话基准日期：2026-07-27。工作站文件系统时间：2026-07-28 凌晨（仅作时间戳如实记录，非完成时间）。*
