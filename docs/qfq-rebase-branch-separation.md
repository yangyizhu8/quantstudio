# QFQ Authoritative Rebase — 分支分离与提交指引

> 用途：将 dirty 工作区中混在一起的 4 类改动（QFQ rebase / money-alias / AGENTS.md 双端铁律 / backtest 性能）精确分离，使 QFQ rebase 单独落到 `feat/qfq-authoritative-rebase` 分支，其余留在 `fix/phase1-money-alias-and-skill-archive`。
> 生成日期：2026-07-30

## 0. 前置结论：阶段 4（编排器接入）已通过审核 ✅

| 审核点 | 结果 | 证据 |
|---|---|---|
| 编排器用 rebase 模式 | ✅ | `quantstudio/pipeline/qfq_resident_orchestrator.py` `_reanchor_security` 调 `model="fresh_authoritative_rebase"`（全仓 `apply_reanchor_for_security(` 仅此一处，无其他 `fresh_staged` 硬编码） |
| `INSERT OR REPLACE` 清理 | ✅ | `qfq_fresh_capture.py` 无实际 `INSERT OR REPLACE` 语句，`_insert` 已委托 plain INSERT + 主键冲突抛错 |
| 端到端 FakeFreshFetcher | ✅ | `tests/test_qfq_resident_orchestrator.py::test_e2e_commit_flow` 10 passed（覆盖 model + capture_id + metadata_sha256 + ex_dates + trigger_surface 全流程） |
| 全 qfq 回归 | ✅ | 211 passed（authoritative 29 + batch1 104 + batch2 83 + orchestrator 10，含重叠去重） |

> 阶段 4 通过后方可进入提交/实盘/文档。实盘 R3（`validate_qfq_rebase_precision.py --mode run/observe`）与 R4 文档/上线门禁均依赖阶段 4，须在分离后单独推进。

## 1. QFQ rebase 文件清单（迁往 `feat/qfq-authoritative-rebase`）

### Modified（7 项）
- `docs/qfq-rebase-precision-validation-20260729.md`
- `docs/superpowers/specs/2026-07-29-fresh-authoritative-rebase-design.md`
- `quantstudio/pipeline/qfq_fresh_capture.py`
- `quantstudio/pipeline/qfq_reanchor_engine.py`
- `quantstudio/pipeline/qfq_resident_orchestrator.py`
- `tests/test_qfq_reanchor_batch2.py`
- `tests/test_qfq_resident_orchestrator.py`

### Untracked（6 项）
- `docs/qfq-raw-admission-preflight-20260729.md`（阶段 1 raw 准入预检报告）
- `tests/test_qfq_authoritative_rebase.py`
- `scripts/preflight_raw_admission.py`
- `scripts/validate_qfq_rebase_precision.py`
- `tests/fixtures/qfq_raw_admission/`（18 个 `.csv.gz`：9 证券 × {daily, 1min}）
- `tests/fixtures/qfq_rebase_precision/`（8 个：`fresh_daily/` 下 000012/002864/159919/510300/600000/600039/600519/600875）
- `docs/evidence/`（9 个：`qfq_raw_admission_preflight_20260729/` 4 + `qfq_rebase_precision_validation_20260729/` 5）

## 2. 留在 `fix/phase1-money-alias-and-skill-archive` 的（非 QFQ）

- `AGENTS.md`、`README.md`、`docs/interface-contract.md`
- `quantstudio/backtest/backtest_engine.py`
- `quantstudio/backtest/providers/base.py`
- `quantstudio/backtest/providers/duckdb_data_access.py`
- `quantstudio/backtest/providers/duckdb_provider.py`
- `quantstudio/backtest/strategies/first_board_pullback_daily__candidate_quantstudio.py`
- `skills/quantstudio-strategy-compiler/references/probe_strategy_rules.py`
- `skills/quantstudio-strategy-compiler/references/probe_strategy_rules_result.json`
- `tests/test_daemon_lifecycle.py`

## 3. 两分支都严禁提交的项（scratch / 大文件 / 本地运行时）

- `bench_artifacts/`（全部 `_tmp_*`、`_preflight_scratch/`、`diff_daemon_qfq.txt` 等）
- `data/quantstudio.zip`、`data/quantstudio.zip.baiduyun.uploading.cfg`、`data/quantstudio (2).zip`
- `data/staging_qfq_rehearsal_20260729/`（本地运行时 DB）
- `dist/`、`skills.zip`、`.workbuddy/`、`C/`
- `agent_workspace/round7_split/commitA_qfq_only.patch`（patch 产物，可选排除）
- `ptrade/*.RETIRED_DO_NOT_UPLOAD`、`*.RETIRED_DO_NOT_UPLOAD`

## 4. 本地安全操作指引（PowerShell）

> 核心风险：`qfq_reanchor_engine.py` 等在 `origin/main` 上若存在旧版本，`git switch -c feat origin/main` 会因"脏工作区文件将被覆盖"而**直接拒绝切换**。必须先 stash 腾空工作树。

```powershell
# 0. 确认基线（预期 origin/main = 6e5e2ed）
git log --oneline -1 origin/main

# 1. 暂存全部 dirty（含 untracked），腾空工作树
git stash push -u -m "qfq-rebase-wip"

# 2. 从 origin/main 建并切到新分支（工作树已干净，可切）
git switch -c feat/qfq-authoritative-rebase origin/main

# 3. 取回全部改动
#    冲突风险说明：stash 父基是 fix/phase1 HEAD（172984e），新分支基是 origin/main（6e5e2ed），
#    两者差 2 个 commit（money-alias + skill）。
#    - QFQ 文件（qfq_reanchor_engine.py 等）：这两个 commit 不碰 QFQ → 无冲突
#    - money-alias 文件（backtest_engine.py / duckdb_provider.py 等）：172984e 相对 6e5e2ed 有改动，
#      stash 里也有同名改动 → 可能冲突。若冲突，只关心 QFQ 部分（应无冲突）；
#      money-alias 的冲突直接保留 stash 版本：git checkout --theirs <冲突文件>
#      （反正它们不会被 add 到 QFQ 分支，保留 stash 版即可）
git stash pop

# 4. 分批只 add QFQ 文件（严禁 -A），逐个核对
git add docs/qfq-rebase-precision-validation-20260729.md `
        docs/superpowers/specs/2026-07-29-fresh-authoritative-rebase-design.md `
        docs/qfq-raw-admission-preflight-20260729.md
git add quantstudio/pipeline/qfq_fresh_capture.py `
        quantstudio/pipeline/qfq_reanchor_engine.py `
        quantstudio/pipeline/qfq_resident_orchestrator.py
git add tests/test_qfq_reanchor_batch2.py `
        tests/test_qfq_resident_orchestrator.py `
        tests/test_qfq_authoritative_rebase.py
git add scripts/preflight_raw_admission.py scripts/validate_qfq_rebase_precision.py
git add tests/fixtures/qfq_raw_admission/ tests/fixtures/qfq_rebase_precision/ docs/evidence/
git diff --cached --stat        # 确认只有第 1 节 QFQ 文件入暂存区

# 5. 提交（不 push）。用临时文件承载多行 message（PowerShell 不支持 bash heredoc）
$msg = @'
feat(qfq): fresh_authoritative_rebase 引擎 + 编排器接入（R1/R2/阶段4）

- R1: fresh_authoritative_rebase 模型注册 + precheck（raw 逐 bar 对齐 +
  全历史严格覆盖 + 基本校验，移除理想化乘法/加法假设）
- R2: 确定性 postcheck（写后 front==staged + 守恒 + 跨表）+ 事务回滚 +
  capture 不可变契约（冲突检测 + 崩溃恢复幂等）
- 阶段4: 编排器显式选择 rebase + capture INSERT→plain INSERT 清理 +
  端到端 FakeFreshFetcher（10 passed）
- 配套: raw 准入预检脚本 + 精度验证脚本 + 冻结证据/fixture
- 回归: ratio/fresh_staged 逐位不变（211 passed）
- 生产配置: qfq_orchestrator.enabled=false（未启用）
'@
$msg | Set-Content /tmp/qfq_commit_msg.txt
git commit -F /tmp/qfq_commit_msg.txt
Remove-Item /tmp/qfq_commit_msg.txt

# 6. 切回 fix/phase1，剩余非-QFQ dirty（money-alias/AGENTS.md/backtest/skills）随工作树带回
git switch fix/phase1-money-alias-and-skill-archive
git status --short              # 应只剩非-QFQ 的 dirty 项
```

### 关键校验点
执行完第 6 步后，`git status` 必须**不再出现**任何 `qfq_*` / `docs/evidence` / `tests/fixtures/qfq_*` 文件——说明 QFQ 改动已干净迁到 `feat/qfq-authoritative-rebase`，`fix/phase1` 回到纯 money-alias + AGENTS.md + backtest 线程。

### 冲突处理
若第 3 步 `git stash pop` 报冲突，先解冲突再继续，**不要 `--force`**。解完后重复第 4–6 步。

## 5. 执行后验证

### 5.1 切回 fix/phase1 后确认 QFQ 已迁走
```powershell
git status --short | Select-String -Pattern "qfq|evidence|fixture"
# 应为空（QFQ 已迁走）
```

### 5.2 在 feat/qfq-authoritative-rebase 上跑回归
```powershell
python -m pytest tests/test_qfq_authoritative_rebase.py tests/test_qfq_resident_orchestrator.py tests/test_qfq_reanchor_batch1.py tests/test_qfq_reanchor_batch2.py -q
# 应 211 passed
```

## 6. 分离后待办（阶段 4 之后，各自独立推进）
- **`feat/qfq-authoritative-rebase`**：R3 实盘验收（`validate_qfq_rebase_precision.py --mode run/observe`，依赖 xtquant 在线 + 交易日）、R4 文档与上线门禁；提交前须经用户确认（框架层铁律）。
- **`fix/phase1-money-alias-and-skill-archive`**：money-alias(B1) + skill-archive(A2–A5) + AGENTS.md 双端铁律；同样分批 add → `git diff --cached` 核对 → 确认 message 后提交。
