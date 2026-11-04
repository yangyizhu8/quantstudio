# 回归债清债批 · 分族清单（2026-11-03 立批）

> **立批依据**：总调度 2026-11-03 23:5x 裁定「裁二＝立『回归债清债批』（P3 非阻断转换测试）；铁律 08 语义＝**立批即处置（归属＋期限）**，非挂账」。
> **清单来源**：`pytest tests -q`（2026-11-03 23:07:07 → 23:40:39，**2012.90s**）实测
> `94 failed, 4380 passed, 5 skipped, 8 xfailed, 9 errors`；逐项明细取自 `output/conv-test-20261103/pytest_full.log`（UTF-16LE）。
> **分族方法**：机械可复核——按**各失败件所测源模块的 import 目标**归族（脚本逻辑见 §五），非按文件名猜测。
> **基线件**：`docs/evidence/qmt-conv-correctness-baseline-20261103.md`（`5cd6327`，首份入册全量基线）。
> **与铁律 07 的关系**：判据口径按裁一执行——「改动面相关既有功能全绿 ＋ **全库无新增失败**（对上一份入册基线）」。

---

## 一、分族总览

| 族 | 名称 | 文件数 | 项数 | 构成 | 建议归属 |
|---|---|---|---|---|---|
| **F1** | pipeline / delivery 他线在途族 | 28 | **62**（F62） | `quantstudio.pipeline.**`、`quantstudio.delivery.**`、`quantstudio.strategy_compiler.**`、`quantstudio.backtest.providers.**` 为主 | **【已裁 2026-11-04】→ pipeline 族各线认领**（随 11-04 呈批面） |
| **F2** | 策略 agent 产物面族 | 9 | **32**（F32） | 策略 agent 产物（`output/generated_strategies/**`、agent_workspace）+ 策略发布契约 | **【已裁 2026-11-04】→ 策略研发／⑫ 认领**（随 11-04 呈批面） |
| **F3** | fixture 缺失族 | 1 | **9**（全 E9） | 单一文件，`FileNotFoundError` 形态 | **dev 线**认领（补产物或改 fixture 生成）；改动最小，建议最先清 |
| — | **合计** | **38** | **103** | 94 FAILED ＋ 9 ERROR | — |

> **与本批的关系**：`293c520`／`cc37029`／`5cd6327` 三笔经干净树反证（`f60845f` vs 当前，24↔23 同构）判定**零新增失败**；
> 交集仅 3 项（`test_minute_limit_halt`／`test_next_open_limit_and_halt`／`test_engine_config`），均已由 R82 F3 四臂对照归因。

---

## 二、F1 · pipeline / delivery 他线在途族（28 文件 / 62 项）

| 项数 | 失败件 | 所测源模块（import 实证） |
|---|---|---|
| 7 | `test_source_import.py` | `quantstudio.strategy_compiler.source_import` / `ir_nodes` / `validator` / `backtest.ptrade_import` |
| 6 | `test_phase4_minute_batch_provider.py` | `backtest.providers.duckdb_*` / `providers.frequency*` |
| 6 | `test_phase4_minute_memory_slice.py` | `backtest.providers.frequency*` / `backtest.ptrade_api` / `pipeline.writers` |
| 5 | `test_strategy_fidelity_gates.py` | 保真门禁（产物样本目录） |
| 4 | `test_qfq_schema_migration.py` | `pipeline.qfq_schema_migrat*` / `quantstudio.pipeline` |
| 3 | `test_audit_qfq_staleness.py` | `pipeline.qfq_revision*` |
| 3 | `test_batch_apis.py` | `backtest.backtest_engine` / `backtest.ptrade_api` |
| 3 | `test_gui_rebalance_mode.py` | `backtest.backtest_engine` / `backtest.run_ptrade_strateg*` / `gui.tabs.backtest_tab` |
| 3 | `test_pr6b1_orchestrator.py` | `strategy_compiler.orchestr*` / `render*` / `validator` |
| 2 | `test_ptrade_public_signature_contract.py` | `backtest.backtest_engine` / `backtest.ptrade_api` |
| 2 | `test_qfq_range_window_guards.py` | `pipeline.sources.mcp_adapt*` |
| 2 | `test_write_integrity_r2_serial.py` | `pipeline.writers` |
| 1 | `test_engine_config.py` | `quantstudio._paths` / `backtest.backtest_engine` ⚠️**隔离环境产物**（见 §四） |
| 1 | `test_eps_backfill.py` | `pipeline.eps_backfill` / `pipeline.quality_audit` / `pipeline.writers` |
| 1 | `test_gui_task_audit_separation.py` | `gui.tabs.task_tab` / `gui.workers` / `pipeline.daemon` |
| 1 | `test_inspect_capabilities_f6.py` | （未取到 import） |
| 1 | `test_mcp_empty_tombstone.py` | `pipeline.sources` |
| 1 | `test_minute_limit_halt.py` | `backtest.backtest_engine` / `backtest.ptrade_api` ⚠️**既有基线红**（F3 已归因） |
| 1 | `test_minute_query_no_future_leak.py` | `backtest.providers.duckdb_*` / `pipeline.writers` |
| 1 | `test_minutes_conflict_m1m2.py` | `pipeline.writers` |
| 1 | `test_next_open_limit_and_halt.py` | `backtest.backtest_engine` ⚠️**既有基线红**（F3 已归因） |
| 1 | `test_probe_strategy_rules_clean.py` | （未取到 import） |
| 1 | `test_provider_frequency_routing.py` | `backtest.providers.base` / `duckdb_*` / `frequency*` |
| 1 | `test_ptrade_fidelity_config.py` | `backtest.fidelity_config` / `ptrade_api` / `run_ptrade_strateg*` / `gui.workers` |
| 1 | `test_ptrade_profile_registered_stock_apis.py` | `backtest.ptrade_api` |
| 1 | `test_qfq_range_r1a.py` | `backtest.providers.duckdb_*` / `ptrade_api` |
| 1 | `test_security_metadata_api.py` | `backtest.providers.duckdb_*` / `ptrade_api` |
| 1 | `test_writer_channel_contract.py` | （未取到 import） |

---

## 三、F2 · 策略 agent 产物面族（9 文件 / 32 项）

| 项数 | 失败件 | 说明 |
|---|---|---|
| 10 | `test_fin_growth_dividend_staging_tool.py` | 股息/成长 staging 工具产物面 |
| 10 | `test_smallcap_overnight_scalp_agent_v2.py` | 小市值隔夜 agent v2（依赖 `output/generated_strategies/**`） |
| 6 | `test_smallcap_overnight_scalp_agent.py` | 同上 v1 |
| 1 | `test_agent_first_strategy_skill.py` | `backtest.strategy_runner` + agent skill |
| 1 | `test_strategy_alignment_regressions.py` | `backtest.strategy_runner` / `ptrade_api` / `providers` |
| 1 | `test_strategy_name_chinese_contract.py` | 中文名契约 |
| 1 | `test_strategy_spec_schema.py` | `quantstudio.strategy_compiler` 冻结 schema 样例 |
| 1 | `test_target_aware_strategy_skill.py` | 目标感知 skill |
| 1 | `test_user_pyqt_candidate_flow.py` | 用户模式候选→证据→转正流程 |

---

## 四、F3 · fixture 缺失族（1 文件 / 9 项，全为 ERROR）

**根因实证**（日志原文摘录）：

```
E   FileNotFoundError: [Errno 2] No such file or directory:
    'output\\generated_strategies\\first_cover_event_daily\\agent_workspace\\strategy.py'
```

- 失败件：`tests/test_first_cover_event_daily_agent.py`（**9 个 ERROR 全为 fixture setup 阶段**，非断言失败）
- 性质：fixture 依赖 `output/generated_strategies/first_cover_event_daily/agent_workspace/strategy.py` 这一**策略产物**，
  该产物当前**不存在**（未生成或被清理）；`output/` 属 `.gitignore:31` 忽略面 ⇒ 干净克隆必缺失
- 两条可选修复路径（择一，须认领线定）：
  - **A**：fixture 改为**自建临时产物**（写入 pytest `tmp_path`），不依赖 `output/` —— 推荐，与「干净克隆可跑」原则一致
  - **B**：把该产物降级为**入库 fixture**（小体积、去敏），放 `tests/fixtures/`

---

## 五、分族方法与可复核性

1. 从 `pytest_full.log`（UTF-16LE）逐行取 `^(FAILED|ERROR)\s+(\S+::\S+)` ⇒ 103 条明细；
2. 每个失败件读源码，正则抽 `^\s*(?:from|import)\s+(quantstudio[\w\.]*)` 与字符串常量里的 `quantstudio/**.py` 路径；
3. 归族判据（顺序命中）：
   - **F3**：`ERROR` 计数 > 0 **或**（引用 `output/generated_strategies|agent_workspace` 且文件名含 `fidelity|sample|required`）；
   - **F2**：命中 `agent_first_strategy|strategy_skill|strategy_name_chinese|strategy_spec_schema|target_aware|user_pyqt_candidate|smallcap_overnight|fin_growth_dividend|strategy_alignment`；
   - **F1**：其余（以 `quantstudio.pipeline.**`／`delivery.**`／`strategy_compiler.**`／`backtest.providers.**` 为主）。
4. 全脚本输出可复现；本清单**不含猜测项**，未取到 import 的 5 个文件已如实标注「（未取到 import）」。

## 六、处置纪律（铁律 08）

| 项 | 内容 |
|---|---|
| **立批即处置** | 本批**不是挂账**：每一族须由认领线给出**归属＋期限**，否则不得关闭 |
| **期限建议** | F3（9 项·fixture）≤2 个工作日（改动最小、收益最高，可直接恢复 9 项）；F2（32 项）≤5 个工作日；F1（62 项）≤10 个工作日（含 2 项既有基线红需先定性） |
| **本线边界** | 本会话**零改动**上述任何失败项（零越权）；仅提供清单、归因与判据 |
| **关闭判据** | 各族全项转绿 **且** 下一轮全量基线读数不低于本基线（`4380 passed`）且失败数下降；按裁一「无新增失败」口径复核 |
| **基线刷新** | 每轮全量回归读数入册 `docs/evidence/`（承接 `5cd6327` 首份基线） |

## 七、变更日志

| 时间 | 内容 |
|---|---|
| 2026-11-03 23:5x | v1.0 立批：103 项机械分三族（F1 62 / F2 32 / F3 9），含分族判据、期限建议、关闭判据；随 11-04 呈批件呈各线认领 |

---

## 八、11-04 呈批面（一行版，可直接摘入 ⑤件／呈批件）

**「回归债清债批（P3 非阻断转换测试）· 认领请求」**——2026-11-03 23:07 全量回归实测
`94 failed / 4380 passed / 9 errors`（`2012.90s`，基线件 `docs/evidence/qmt-conv-correctness-baseline-20261103.md`，commit `5cd6327`），
103 项按「所测源模块」机械分三族，请下列各线**认领并回执归属＋期限**（铁律 08：立批即处置，非挂账）：

| 族 | 项数 | 认领线 | 期限建议 | 说明 |
|---|---|---|---|---|
| F1 pipeline / delivery 他线在途 | 62 | **pipeline 族各线** | ≤10 工作日 | 含 2 项既有基线红（`test_minute_limit_halt`／`test_next_open_limit_and_halt`）需先定性 |
| F2 策略 agent 产物面 | 32 | **策略研发／⑫** | ≤5 工作日 | 主体为 `output/generated_strategies/**` 与 agent 产物面 |
| F3 fixture 缺失 | 9 | **dev 线** | ≤2 工作日 | 单一文件 9 个 ERROR，均为产物文件缺失；改 fixture 或降级入库 fixture 即可恢复 |

**随附复核结论（1 行，只出结论、不改已关条目）**：

> `ISS-T5-L`（`get_industry_stocks` 恒返空）关闭所依据的「DuckDB 暂无行业成分表」**表述不准确**——
> 库内实有 `industry_membership` **7,738 行**（含 `effective_from`/`effective_to` 双时态）与 `industry_classification` **62 行**；
> 缺失的是 **provider 层反向查询未接线**（`DuckDBReferenceDataProvider` 覆写了 `get_industry(code, date)` 单向查询，
> **未覆写** `get_industry_stocks`，落 `providers/base.py:188` 默认 `return []`）。
> ⇒ 该条目「登记即可·策略侧规避」的**结论可维持**（策略仍不应依赖行业池），但**根因表述应由「无数据表」更正为「反向查询未接线」**，留待该条目下次复核时更正。

## 九、变更日志

| 时间 | 内容 |
|---|---|
| 2026-11-04 00:2x | v1.1 两裁落地：F1→pipeline 各族／F2→策略研发·⑫／F3→dev 认领行填实；新增 §八 **11-04 呈批面一行版** ＋ `ISS-T5-L` 复核结论（7,738 行实测） |
