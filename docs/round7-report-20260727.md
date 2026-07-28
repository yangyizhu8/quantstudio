# 第七轮审计阻断修复 + 干净 worktree 分离提交报告（2026-07-27）

> 时间线说明：会话基准日 2026-07-27；工作站时钟显示 2026-07-28 凌晨。
> 本报告如实记录两个时间戳，以会话基准日命名。

## 一、本轮范围（用户批示）

第六轮六项阻断 + validator PIT 修订已由用户本地验收通过
（用户实测全量 1430 passed, 1 warning, 0 failed）。本轮修复剩余两个审计阻断，
并按用户指示建立干净 worktree、拆分两个独立拟提交。
**GitHub 同步继续冻结，等待用户最终确认。**

## 二、阻断 1：fresh_staged 来源字段强制（事务外 ValueError）

### 问题
`model="fresh_staged"` 此前允许 `fresh_source` / `fresh_capture_id` /
`fresh_metadata_sha256` 全部为空后照常 committed——审计链路可被静默绕过。

### 修复（quantstudio/pipeline/qfq_reanchor_engine.py）
`apply_reanchor_for_security` 在 **进入事务（BEGIN）之前** 强制校验，缺任一项
抛 `ValueError`，绝不写价格 / 事件 / anchor：

| 校验项 | 规则 |
| --- | --- |
| `model_reason` | 非空（原有，保留） |
| `fresh_minutes` | 非空（原有，保留） |
| `fresh_source` | 非空字符串 |
| `fresh_capture_id` | 非空字符串 |
| `fresh_metadata_sha256` | `_is_valid_sha256`：`re.fullmatch(r"[0-9a-fA-F]{64}", v.strip())` |
| `freqs` | 非空，且逐项过 `_canon_minute_freq`（非分钟 freq 事务外拒绝） |

**语义变更说明**：非分钟 freq（如 `("daily",)`）原为进入事务后 failed 事件，
现前移为事务外 ValueError（不落事件）。round6 的
`test_failed_event_carries_audit` 相应改为事务内故障注入
（monkeypatch `update_daily_front_from_staged` 抛 RuntimeError）验证 failed
事件审计，断言全部保留。

## 三、阻断 2：precheck BLOCK 事件携带 coverage

### 问题
raw mismatch 等 precheck 已算出 coverage 后抛异常时，`result.minute_coverage`
仍为空，blocked 事件缺 `staged_count/target_count/matched_count/raw_mismatch`
与 precheck 阶段标记——无法审计"执行到哪一步、各统计值是多少"。

### 修复（结构化 ReanchorBlocked）
1. `ReanchorBlocked` 扩展 `coverage` / `phase` / `freq` 三个关键字参数；
2. `apply_fresh_minute_staged` 三处 precheck 抛点
   （minute_coverage_incomplete / minute_raw_null / minute_raw_mismatch）
   均携带 `coverage=coverage, phase="precheck", freq=freq_c`；
3. 编排层 `except ReanchorBlocked` 将 `e.coverage` 回填
   `result.minute_coverage[freq]`，事件 `minute_ratio_plan` JSON 增加
   `precheck_phase` 字段（`_audit_json(phase=...)`）；
4. rolled_back（postcheck）事件同样带 `precheck_phase="postcheck"`。

## 四、测试

### 新增 TestRound7AuditBlockers（tests/test_qfq_reanchor_batch2.py，6 项）
- `test_fresh_staged_requires_source_fields[3 param]`：缺任一来源字段 →
  事务外 ValueError + 无事件/anchor/价格未动；
- `test_fresh_staged_rejects_bad_sha256`：8 位 hex → ValueError；
- `test_fresh_staged_rejects_empty_freqs`：空 freqs → ValueError；
- `test_fresh_staged_valid_sha256_proceeds`：合法 64 hex → 正常进 precheck；
- `test_blocked_event_carries_coverage`：raw mismatch → blocked 事件
  coverage{staged==target==matched, raw_mismatch>0} + precheck_phase=="precheck"；
- `test_blocked_coverage_incomplete_records_counts`：缺 bar →
  coverage{staged<target, missing_staged>0} + precheck_phase。

### 既有测试回归（收紧契约的预期回归）
`TestFreshStagedModel` / `TestRound6Blockers` 全部 `fresh_staged` call site
补传模块级 `_AUDIT_KW` 审计三元组（fresh_source / fresh_capture_id /
fresh_metadata_sha256），两处类级 `_AUDIT_KW` 改为复用模块级常量。

### dirty main 实测（修复后）
- `tests/test_qfq_reanchor_batch2.py`：**83 passed**（含 round7 新增 8 例）
- `batch1 + guardrails + validator_index_pit`：**112 passed**
- 全量：见第六节。

## 五、干净 worktree 分离提交

### 基线
- 基线 SHA：`036de67beeca137df18a684775efade8474d5308`（main HEAD，
  "feat: F1-F6 framework repair"）
- worktree：`D:\miniQMT策略实盘\qfq-round6-worktree`，分支 `qfq-round6-split`
- **未在 dirty main 上 stage 任何文件**

### 拟提交 A（QFQ 基础设施 + B-1 + docs/tests/fixture）
- 新增：`quantstudio/pipeline/qfq_reanchor_engine.py` / `qfq_calendar.py` /
  `qfq_observation.py` / `qfq_reanchor_schema.py`
- 新增：`tests/test_qfq_reanchor_batch1.py` / `test_qfq_reanchor_batch2.py` /
  `tests/fixtures/qfq_real_reanchor/`（14 个文件，564KB parquet+json）
- 新增 scripts：`capture_fresh_xtquant_golden.py` /
  `calibrate_real_reanchor_tol.py` / `extract_qfq_reanchor_real_fixture.py` /
  `dump_real_method_ab_samples.py`
- 新增 docs：`qfq-reanchor-batch2-fix-report-20260727.md` /
  `qfq-reanchor-batch2-fix-report-round5-20260728.md` /
  `qfq-reanchor-batch2-real-ab-samples-20260727.txt` /
  `qfq-reanchor-minute-model-decision-20260727.md` /
  `round6-report-20260727.md` / `round7-report-20260727.md`（本文）
- 修改（hunk 级分离）：
  - `quantstudio/pipeline/writers.py`：**仅 QFQ 事务感知方法 hunk**
    （`@@ -566,6 +572,169 @@`：`_advance_watermark_on_conn` +
    `_upsert_pending_backfill_on_conn`）。排除同文件 fin_indicator /
    stock_dividend DDL、列清单、index_constituents_snapshot_meta 等
    6 个 staging hunks；
  - `README.md`：仅 QFQ 重锚引擎章节 hunk（`@@ -144`），排除 W2 staging
    章节 hunk（`@@ -232`）；
  - `docs/strategy_toolbox.md`：仅第 4 节 QFQ hunk（`@@ -207`），排除
    get_stock_exrights 行变更（`@@ -110`）；
  - `docs/prompt_engineering.md`：仅 5.x QFQ 编排铁律 hunk（`@@ -241`），
    排除 PTrade Profile 1.10.0 行变更（`@@ -71`）。

### 拟提交 B（validator PIT 独立框架变更）
- `quantstudio/pipeline/validator.py`：PIT 去重语义
  （不同 ann_date 版本全保留；完整主键去重；update_flag 优先）+ 入口
  `reset_index(drop=True)` 防御；
- `tests/test_pipeline_guardrails.py`：PIT 测试重写（5 项）；
- `docs/pipeline-tech-debt.md`：PIT 修订章节 + 表格第 2 行修订。

### 明确排除（两个提交均不含）
行业 / 指数（index_constituents_snapshot_meta）/ PTrade Profile /
策略候选（bbi_etf_rotation 等）/ financial・dividend staging
（alignment_rules.json、collector_tasks.json、tushare_adapter、daemon、
aligner、quality_audit、config_lint、backtest 引擎与 providers、
tests/test_authoritative_source_policy 等）/ zip / dist / db。

## 六、验证（已回填，2026-07-28）

- worktree 专项（batch1 + batch2 + pipeline_guardrails + validator PIT）：
  **190 passed, 0 failed**。
- worktree 全量：**1338 passed, 23 failed, 12 skipped, 9 errors, 1 warning**。
- 纯基线对照（detached `036de67`，不含任何 A/B 改动，同环境全量）：
  **1162 passed, 23 failed, 12 skipped, 9 errors, 1 warning**。
  失败/错误集合与 worktree 完全一致，均为仓库历史遗留：
  `test_smallcap_overnight_scalp_agent*.py`、`test_strategy_fidelity_gates.py`、
  `test_strategy_alignment_regressions.py`、`test_first_cover_event_daily_agent.py`
  （ERROR 发生于 fixture 收集阶段）。
  **结论：23 failed / 9 errors 为基线固有，A/B 两个拟提交未引入任何新失败；
  A/B 涉及的专项测试 0 failed。**
- dirty main 全量（含全部未拆分改动）：**1439 passed, 1 warning, 0 failed**。

## 七、GitHub 同步状态

**冻结。** 两个拟提交仅存在于本地分支 `qfq-round6-split`
（worktree `D:\miniQMT策略实盘\qfq-round6-worktree`），未 push。
等待用户最终 GitHub 同步确认（铁律）。

## 八、第七轮.5：基线重放（2026-07-28，三交付阻断修复）

用户验收第七轮代码/阻断/拆分本身通过，但指出三个交付阻断，本节记录闭合过程。

### 8.1 阻断修复
1. **基线过期**：origin/main 已前进至
   `d8a0791f8e2dc7adaa22e2a21ce4550a6a7f9761`（036de67 之后新增
   9d92c5b F2-F6 industry gates + merge d8a0791，改动文件集与 A/B 完全不相交，
   cherry-pick 零冲突）。已从 d8a0791 新建干净分支 **`codex/qfq-round7-sync`**
   （worktree `D:\miniQMT策略实盘\qfq-round7-sync-worktree`），按序重放 A/B。
2. **trailing whitespace**：`tests/test_qfq_reanchor_batch2.py:1881`
   （docstring 行尾空格）已修复；
   `git diff --check origin/main...HEAD` **无任何输出**。
3. **Commit B 缺 PIT index 回归测试**：`tests/test_validator_index_pit.py`
   （5 项非连续 index / reset_index(drop=True) 防御回归）已并入重放后的
   validator 提交，Commit B 最终含 4 文件：validator.py、
   test_pipeline_guardrails.py、test_validator_index_pit.py、
   pipeline-tech-debt.md。

### 8.2 重放提交
- 基线：`d8a0791f8e2dc7adaa22e2a21ce4550a6a7f9761`（= origin/main）
- Commit A'（QFQ + B-1 + fixture/tests/docs）：34 files changed, 9204 insertions(+)，writers.py 仅 `@@ -566` 单 hunk
- Commit B'（validator PIT）：4 files changed, 284 insertions(+), 42 deletions(-)
- origin/main...HEAD 总计：38 files changed, 9488 insertions(+), 42 deletions(-)
- 最终 SHA 以 `git log codex/qfq-round7-sync` 为准（报告为提交内容物，
  无法自引用自身 SHA；交付回执中另行列明）。

### 8.3 验证（d8a0791 基线，2026-07-28）
- 重放分支专项（batch1 + batch2 + guardrails + validator_index_pit）：
  **195 passed, 0 failed**（190 + 新增 5 项 index_pit）。
- 纯 d8a0791 全量：**23 failed, 1178 passed, 12 skipped, 9 errors, 1 warning**。
- 重放分支全量：**23 failed, 1359 passed, 12 skipped, 9 errors, 1 warning**。
- 失败/错误 nodeid 集合**逐项完全一致**（32 项），零新增：
  `test_smallcap_overnight_scalp_agent_v2.py`(10)、
  `test_first_cover_event_daily_agent.py`(9, fixture 收集 ERROR)、
  `test_smallcap_overnight_scalp_agent.py`(6)、
  `test_strategy_fidelity_gates.py`(4)、`test_gui_rebalance_mode.py`(2)、
  `test_strategy_alignment_regressions.py`(1)——均为基线固有历史遗留，
  d8a0791 未全绿，故适用"不新增任何失败"标准且已满足。

### 8.4 GitHub 同步状态（第七轮.5）
**继续冻结。** `codex/qfq-round7-sync` 仅存在本地，未 push、未开 PR、
未触碰 origin/main。等待用户最终 GitHub 同步确认（铁律）。
