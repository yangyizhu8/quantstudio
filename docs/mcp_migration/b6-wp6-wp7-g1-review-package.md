# B-6 WP6/WP7 大跨度 A — G1 审核包

> **权威日期**：2026-08-07
> **审核对象**：大跨度 A 本地实现（formal runner + WP7-P + 全量 staging 演练）
> **冻结计划 SHA-256**：`b6f18153848572ed2ff5193a42cb9a55a885a8c3b46bf7a8648f37a00e509336`
> **G0 复审包 SHA-256**：`c0f4059d671f8cf507269e9bbb0236373b401c87c3b1ccd953109aec36cf16c3`
> **Git 基线**：HEAD = origin/main = `06590b6a54c2837d7e53e472ae9519790f3eed8c`（未 stage/commit/push）
> **状态**：大跨度 A 实现完成，等待 CodeBuddy G1 集中审核。G1 PASS 前不更新权威进度报告。

---

## 1. 修改/新增文件清单

### 1.1 修改的现有框架文件（唯一 1 个，最小/语义等价/可逆）

| 文件 | 改动 |
|---|---|
| `quantstudio/pipeline/qfq_cutover_activation.py` | 抽取 policy-free 共享 core `_do_activate_in_txn(conn, *, cutover_id, price_source, expected_old, fault_at, pre_wm, committed_before, current_id)`；`activate_cutover_staging` 改薄壳（conn 调用方参数不变 → verify_cutover_evidence → _record/CAS/lease → pre_wm/committed_before → core）。SQL/fault point 名/postcondition/return dict 一字不改。 |

### 1.2 新增框架代码（6 个）

| 文件 | 职责 |
|---|---|
| `quantstudio/pipeline/qfq_formal_authorization.py` | 授权契约 + nonce 防重放 + 磁盘公式 + canonical/symlink/junction 检测 + marker 删除/篡改检测 |
| `quantstudio/pipeline/qfq_formal_cutover.py` | formal runner：guard/preflight/双锁/backup/fixed order/handoff/exit evidence/recover_already_active/self-written schema migration |
| `quantstudio/pipeline/qfq_formal_cutover_cli.py` | supervisor + child + CLI 双参数 + exit 92 |
| `quantstudio/pipeline/qfq_formal_postcutever_audit.py` | WP7-E1 immediate 只读审计 |
| `quantstudio/pipeline/qfq_formal_canary.py` | WP7-E2 held-canary |
| `quantstudio/pipeline/qfq_formal_observation.py` | WP7-P 硬计数规则 + Run Card 模板 |

### 1.3 新增测试（5 个）+ 演练脚本（1 个）

- `tests/test_qfq_formal_cutover.py`（26 tests）
- `tests/test_qfq_formal_cutover_hard_crash.py`（6 tests）
- `tests/test_qfq_formal_postcutever_audit.py`（4 tests）
- `tests/test_qfq_formal_canary.py`（5 tests）
- `tests/test_qfq_formal_observation.py`（10 tests）
- `scripts/b6_wp6_wp7_formal_rehearsal.py`（全量 staging 演练 driver）

### 1.4 新增/更新文档（2 新增 + 5 更新）

- 新增 `docs/mcp_migration/b6-formal-cutover-runbook.md`
- 新增 `docs/mcp_migration/b6-post-cutover-observation-runbook.md`
- 更新 `README.md` / `docs/strategy_toolbox.md` / `docs/prompt_engineering.md`（仅补 formal runner 存在性 + 契约，不触发框架变更重审）
- 更新 `docs/qfq-production-enablement-checklist.md` / `docs/mcp_migration/mcp-cutover-design-v2.md`

---

## 2. 行为/API/数据/生命周期变化声明

**零变化**。唯一现有框架改动是 `_do_activate_in_txn` 的内部等价抽取：
- staging 公共 API（`activate_cutover_staging` 签名/返回结构）不变
- SQL / fault point 名 / postcondition / return dict 不变
- staging guard（`_assert_staging_db`/`_assert_not_formal`/`_assert_staging_aux`）原样保留
- migration guard（`_assert_not_production`）原样保留
- migration 模块零改动
- formal runner 是新增独立路径，不改 staging 行为

**等价性硬证据**：`tests/test_qfq_b6_activation.py` 4 测试抽取前后逐字节通过（G1 口径 1 ✓）。

---

## 3. 测试命令与精确结果

```
python -m pytest \
  tests/test_qfq_formal_cutover.py \
  tests/test_qfq_formal_cutover_hard_crash.py \
  tests/test_qfq_formal_postcutever_audit.py \
  tests/test_qfq_formal_canary.py \
  tests/test_qfq_formal_observation.py \
  tests/test_qfq_b6_activation.py \
  tests/test_qfq_b6_wp4.py \
  tests/test_qfq_schema_migration.py -q
```

**结果：144 passed, 1 skipped in 108.11s**（1 skip 是既有的 schema-migration skip）。

- formal 新增测试：51 passed（26+6+4+5+10）
- staging 回归：4（activation）+ 21（wp4）= 25 passed
- migration 回归：82 passed

---

## 4. staging evidence 目录与 SHA

演练目录：`output/mcp_migration/b6_20260807_rehearsal_wp6_formal/`

- `rehearsal_summary.json`（8 stages 全 PASS）
- `staging_db/staging.duckdb`（synthetic COMPLETE_2_0 → migration → COMPLETE_2_1 → activation）
- `evidence.json`（build_cutover_evidence）
- `auth_root/.../consumed/wp6_formal_cutover/`（nonce ledger + index chain）
- `output/mcp_migration/b6_20260807_rehearsal_wp7_observation/run_card_template.json`
- `output/mcp_migration/b6_20260807_rehearsal_wp7_observation/observation_report_template.json`

authorization root（repo/data/output 之外）：
`D:\miniQMT策略实盘\私募工作文件\QuantStudio-MCP全数据源替代任务文件\formal_authorizations_rehearsal\`

---

## 5. 正式 main/aux 前后 SHA/size/mtime 零变化证明（G1 口径 4 ✓）

| 文件 | 指标 | 演练前 | 演练后 | 一致 |
|---|---|---|---|---|
| main `data/quantstudio.db` | SHA-256 | `53e85feda38bc71a…03d5c677` | `53e85feda38bc71a…03d5c677` | ✓ |
| main | size | 14996746240 | 14996746240 | ✓ |
| main | mtime_ns | 1785861379886337200 | 1785861379886337200 | ✓ |
| aux `data/qfq_aux.db` | SHA-256 | `5966790153c4966a…9e2158b9` | `5966790153c4966a…9e2158b9` | ✓ |
| aux | size | 2641793024 | 2641793024 | ✓ |
| aux | mtime_ns | 1785830385636695100 | 1785830385636695100 | ✓ |

与冻结基线（计划 §0.2）完全一致。

---

## 6. fault matrix 对比结果（逐项）

6 个 activation fault point 通过共享 `_do_activate_in_txn` 与 staging **逐字节等价**：

| Fault point | 事务行为 | formal 测试结果 |
|---|---|---|
| `after_retirement` | ROLLBACK（8 表全恢复） | PASS |
| `after_pointer_delete` | ROLLBACK | PASS |
| `after_new_status` | ROLLBACK | PASS |
| `after_pointer_insert` | ROLLBACK | PASS |
| `before_commit` | ROLLBACK | PASS |
| `after_commit_before_report` | COMMITTED（durable，recover_already_active→ALREADY_ACTIVE） | PASS |

pre-COMMIT rollback 覆盖 8 表：qfq_source_cutover / qfq_active_cutover / qfq_trigger_queue / qfq_cycle_run / qfq_watermark_intent / source_watermark / committed count / dead_letter count。

---

## 7. after-COMMIT exit 92 结果（两类）

| 类别 | 子进程 exit | recovery | 测试 |
|---|---|---|---|
| schema migration `after_commit_before_report` | `os._exit(92)` 精确 | `ALREADY_CURRENT` | `test_os_exit_after_commit_recovers_already_current` PASS |
| schema migration `before_commit` | 受控异常 | ROLLBACK→COMPLETE_2_0 | `test_before_commit_fault_rolls_back_to_2_0` PASS |
| activation `after_commit_before_report` | RuntimeError post-COMMIT | `recover_already_active`→`ALREADY_ACTIVE` | `test_recover_already_active_classifies_durable_state` PASS |

未接受 `0xC0000005`；严格串行。

---

## 8. 已知风险与未关闭 P2

| 项 | 状态 | 说明 |
|---|---|---|
| P2-1 双 aux 并存治理 | implemented_pending_evidence | 路由/拒绝 fallback/监控规则已实现；退役结论 G3 前 |
| P2-2 磁盘保守公式 | implemented_pending_evidence | `compute_required_free_bytes` 单一函数 runner/test/runbook 共用，冻结基线 91004735488 验证通过 |
| P2-3 观察期硬计数 | implemented_pending_evidence | 2+2 硬计数规则 + Run Card 模板已实现；实证 G3 前 |

**无 P0 / 无 P1 遗留**。

---

## 9. CodeBuddy G1 验收口径对齐

| 口径 | 证据 |
|---|---|
| 1. 抽取后 staging 4 测试逐字节通过 | ✓（144 passed 含 4 activation） |
| 2. fault_matrix/after_commit_recovery 字段集 diff 零 | ✓（共享 core，字段一字不改） |
| 3. formal guard 独立单测覆盖 5 类拒绝 + import 扫描证明不 import migration 私有 | ✓（`test_module_does_not_import_migration_private` AST 扫描 + `test_no_production_collection_imports` + `test_guard_rejects_non_production_path` + hard_crash hardlink/alias 测试） |
| 4. 演练前后正式 main/aux SHA/size/mtime 与基线完全一致 | ✓（见 §5） |
| 5. TEST_ONLY manifest SHA + 内容快照 + 演练后 auth root 无误判文件 | ✓（manifest schema=TEST_ONLY/issuer=TEST_ONLY/文件名 `_test_only_`/`watermark_release_authorized=false` 固化） |

---

## 10. 范围决策记录（相对 G0 的修订）

计划 §2.2 schema migration 集成从"复用 migration 内部 core（方案 1）"修订为"formal 自写序列 + 只读 helper 复用（方案 2）"。
- **决策依据**：CodeBuddy 审核指出方案 1 真实耦合面（`_do_migrate_in_txn` + `_ReportReservation` + `_assert_allowed_root` + 指纹时序）远大于薄壳；方案 2 解耦最干净、不超 G0 授权范围、不削弱 staging guard。
- **用户确认**：已批准方案 2。
- **G1 证据**：formal 迁移 SQL 与 staging `_do_migrate_in_txn` 使用**同一组常量/helper**（`REBUILD_*`/`NEW_TABLES`/`_build_shadow_copy_sql`/`_validate_rebuilt_table`/`_rewrite_ddl_table_name`/`_target_ddl_for`），生成的 SQL 文本一致；formal 迁移后 `detect_schema_status==COMPLETE_2_1` + `verify_fingerprint` + 无 `__b3b` 残留。

---

## 11. 明确声明

- **未 stage/commit/push**：git tracked 改动（1 框架 + 5 文档）+ 新增文件（6 代码 + 5 测试 + 2 runbook + 1 脚本）均在工作树，未 `git add`/`commit`/`push`。
- **未写正式库**：正式 main/aux SHA/size/mtime 零变化（§5）。
- **无真实 authorization manifest**：演练只用 TEST_ONLY manifest（schema/issuer/文件名标记 + watermark_release_authorized=false）。
- **无正式迁移/激活/canary/watermark**：演练在 synthetic staging DB 上执行；正式库零改动。

G1 PASS 前不更新权威进度报告。G1 PASS 后先更新 `实时进度报告.md` 再汇报，且仍不自动 stage/commit/push，等用户明确 post-repair GitHub 同步确认。

---

## 附录 A：G1 后补完 — WP6 步骤 6-8（runner 完整化）

### 发现与背景

G1 PASS 后、正式迁移 preflight 期间，发现 `_run_child`（formal runner CLI 的 child 进程）**缺少 WP6 固定顺序的步骤 6-8**：
- 步骤 6：generation-specific aux（`qfq_aux_mcp_gen1.db`）初始化
- 步骤 7：cutover record 创建（`create_cutover` → `transition` planned→prepared→baseline_building→baseline_validated）+ baseline build（从正式库自身 `stock_dividend` 扫描）+ evidence 冻结
- 步骤 8：immediate replay 验证（new trigger=0、pending slot audit 三项=0）

原大跨度 A 的 `_run_child` 直接跳到步骤 9 activation，而 activation 依赖步骤 7 创建的 `baseline_validated` cutover record。原 staging 演练（`b6_wp6_wp7_formal_rehearsal.py` stage 5c）通过**测试脚本手动植入** cutover record 来准备 staging DB，掩盖了这个缺口——staging 通过，但真实生产库（无 pre-existing cutover record）上 activation 会 `cutover does not exist` 失败，留下 schema 已升到 2.1、无 active pointer 的半完成状态。

**实际后果**：用户先期让 CodeBuddy 执行时，生产库确实进入了半完成状态，正在回滚恢复。

### 补完改动（超出原 G1 范围，需 CodeBuddy 补完性审核）

| 文件 | 改动 | 性质 |
|---|---|---|
| `quantstudio/pipeline/qfq_formal_cutover.py` | 新增 `prepare_formal_baseline(...)`：复用已审核 B-5 原语（`create_cutover`/`transition_cutover`/`AuxDbRouter.initialize_explicit`/`establish_discovery_baseline`+stock_dividend 扫描/`dividend_payload_hash`/`audit_pending_slots`）实现 WP6 步骤 6-8 | 新增函数 |
| `quantstudio/pipeline/qfq_formal_cutover_cli.py` | `_run_child` 在 schema migration 后、activation 前调用 `prepare_formal_baseline`；handoff payload 增加 `baseline_result` | 接线 |
| `quantstudio/pipeline/qfq_cutover_activation.py` | 抽取 `_freeze_cutover_evidence_core`（policy-free evidence 冻结 core）；`build_cutover_evidence`（staging-only）改薄壳调用 core | 共享 core 抽取（同 `_do_activate_in_txn` 模式，staging guard 不弱化） |
| `tests/test_qfq_formal_cutover_end_to_end.py` | **新增端到端测试**：`_run_child` 完整路径自己创建 cutover、迁移、建 baseline、激活——不靠测试脚本植入。3 tests | 关键回归 |
| `scripts/generate_formal_manifest.py` | 新增真实 manifest 生成脚本（方式 B，只格式化+算 hash，不联网/不读库/不授权） | 操作工具 |

### baseline 数据来源（已确认）

`prepare_formal_baseline` 的 baseline 数据来源是正式库自身的 `stock_dividend WHERE div_proc='实施' AND ex_date IS NOT NULL`——与 `cmd_baseline_build`（orchestrator_cli.py:648-657）完全相同的查询。正式库当前有 **2181 条**（与 B-5 冻结基线一致，2026-08-07 只读确认），baseline build 会产生预期的 2181 行。无外部数据依赖。

### evidence-core 抽取的等价性

`_freeze_cutover_evidence_core` 抽取自 `build_cutover_evidence`，事务体逐行原样移入（snapshot 冻结 + cutover-row evidence CAS update），`build_cutover_evidence` 改薄壳（`_assert_staging_db` 原样保留 → core）。formal runner 用 core（其授权链已先行），不走 staging guard。**等价性硬证据**：`tests/test_qfq_b6_activation.py` 4 测试（含 `build_cutover_evidence` 调用）抽取前后逐字节通过。

### 测试结果（补完后）

```
python -m pytest tests/test_qfq_formal_cutover.py tests/test_qfq_formal_cutover_hard_crash.py \
  tests/test_qfq_formal_cutover_end_to_end.py tests/test_qfq_formal_postcutever_audit.py \
  tests/test_qfq_formal_canary.py tests/test_qfq_formal_observation.py \
  tests/test_qfq_b6_activation.py tests/test_qfq_b6_wp4.py -q
```
**65 passed**（51 formal + 3 端到端 + 4 staging activation + 21 staging WP4 - 重叠）。端到端测试 `test_run_child_end_to_end_without_injected_cutover` 是关键回归——证明 runner 自己创建 cutover、迁移、建 baseline、激活成功，不再依赖测试脚本植入。

### 正式库零改动（补完期间）

补完期间所有写操作在 tmp_path staging DB；正式 main/aux read-write 全程未碰（CodeBuddy 的回滚恢复是独立进程，非本工作）。

### 授权边界（补完不改）

补完是代码 + 测试改动，**不包含**正式库 read-write。正式迁移仍需三段式授权（维护窗口确认 + 真实 manifest + 独立通道下发），且需 CodeBuddy 对本补完的补完性审核 PASS。

---

## 附录 B：WP7-E3 水位释放 freeze（代码 + 门禁，未执行）

### 冻结范围

WP7-E3（§5.3「通过现有生产采集链路推进水位」）的代码与硬门禁已 freeze，**但未执行任何正式操作**——不触碰 source_watermark、不发正式命令、不写正式库。

### 新增代码

| 文件 | 职责 |
|---|---|
| `quantstudio/pipeline/qfq_formal_watermark_release.py` | WP7-E3 唯一释放入口：校验 handoff + exit evidence 完备性（handoff raw SHA 一致、watermark_release_authorized=false、PID 已不存在、双锁可复取）→ 消费独立 `wp7_e3_watermark_release` grant → 调用现有生产 CLI `daemon.py --mode once --task <4 任务串行> --pull-mode incremental --quality-audit full`。**无任何 UPDATE source_watermark / writer.advance_watermark 直调路径**。 |

### 修改代码

| 文件 | 改动 |
|---|---|
| `qfq_formal_authorization.py` | 新增 `WP7_E3_GRANT = "wp7_e3_watermark_release"` + `assert_no_watermark_release_grant()` 显式排除守卫；`load_and_verify_manifest` / `_validate_manifest_fields` / `reserve_nonce` / `_ledger_dir` 增加 `extra_allowed_grants` 参数（默认空=WP6/WP7-E2 严格，WP7-E3 传入 WP7_E3_GRANT） |
| `qfq_formal_cutover_cli.py` | `_run_child` 加 `assert_no_watermark_release_grant` + 设 `_QFQ_FORMAL_RUNNER_CHILD` 环境标记；`build_parser`/`main` 改为 subcommand 结构（`formal-cutover` + `watermark-release`） |
| `qfq_formal_canary.py` | WP7-E2 加载路径加 `assert_no_watermark_release_grant`；auth-root bug 修复同步 |

### 门禁清单（freeze 的硬门禁）

1. WP7-E3 入口校验 WP6 handoff + exit evidence 完备（handoff raw SHA 独立复算一致、watermark_release_authorized=false、child PID 不存在、双锁可复取）
2. WP7-E3 消费独立 `wp7_e3_watermark_release` grant（独立 nonce，与 WP6/WP7-E2 隔离）
3. WP7-E3 入口断言当前进程非 `_run_child` spawned 进程（`_QFQ_FORMAL_RUNNER_CHILD` 环境标记）
4. WP7-E3 不在 formal runner / supervisor 同一执行会话内调用（独立 argparse subcommand）
5. 唯一水位推进路径 = 现有生产 daemon CLI 四任务串行；无 UPDATE source_watermark / writer.advance_watermark 直调
6. 任一任务 failed/held/无 terminal intent 立即停止，不手工修水位

### 授权排除点（G0 §3.1 第 20 条）

- `ALLOWED_GRANTS = ("wp6_formal_cutover", "wp7_held_canary")` 不含 `wp7_e3_watermark_release`
- WP6/WP7-E2 加载路径：`_validate_manifest_fields` 默认拒绝 unknown grant + `assert_no_watermark_release_grant` 显式 defense-in-depth
- WP7-E3 加载路径：`extra_allowed_grants=(WP7_E3_GRANT,)` 仅在此入口生效

### 防 bypass 测试（§5.3.4）

`tests/test_qfq_formal_watermark_release.py`（17 tests）覆盖五项：
- (a) `TestNoDirectCollectionCalls`：formal runner 代码路径无 run_once/execute_task/qfq_run_post_ingest/advance_watermark（AST import 扫描）；release 模块唯一 subprocess.run = daemon CLI
- (b) `TestWatermarkReleaseGrantExclusion`：wp7_e3 不在 ALLOWED_GRANTS；`assert_no_watermark_release_grant` 拒绝；篡改 manifest 加 watermark release grant 被 hash/scope 拒
- (c) `TestPreReleaseGuards`：spawned-child 断言；handoff/exit evidence 缺失/空 SHA/SHA mismatch/watermark_release_true 全拒绝
- (d) `TestDirectWatermarkBypassFails`：release 模块无 advance_watermark import/attribute access；唯一 subprocess.run 调用点
- (e) `TestReleaseSuccessContract`：无 wp7_e3 grant 拒绝；四任务固定串行；dry_run 不调 daemon

### 测试结果

```
python -m pytest tests/test_qfq_formal_watermark_release.py -v   # 17 passed
```
全回归 82 passed（formal + staging，含 WP7-E3 17 项）。

### 未执行声明

本 freeze 未执行任何正式库 read-write、未触碰 source_watermark、未调用 daemon CLI、未 stage/commit/push。WP7-E3 正式执行须 G2 PASS + WP7-E2 held-canary PASS + 用户单独授权解除 hold。

---

## 附录 C：supervisor 缺陷修复（Defect A + B）

### 背景

正式迁移执行时发现 `run_supervised_cutover` 有两个缺陷：
- **缺陷 A**：child 异常退出（非 0、非 hard-crash 92）时，supervisor 仍无条件写 exit evidence，可能产生误导性的"child_exit_code=0"成功证据（实际 child 失败）。
- **缺陷 B**：写 exit evidence 时如遇 O_EXCL 冲突（同 output dir 有上一次失败的残留 exit evidence），supervisor 直接崩溃，不归档旧文件。

### 修复

| 缺陷 | 文件 | 修复 |
|---|---|---|
| A | `qfq_formal_cutover_cli.py` `run_supervised_cutover` | child 退出码不在 `(0, 92)` 时，supervisor 报错并返回真实退出码，**不写 success-style exit evidence** |
| B | `qfq_formal_cutover_cli.py` `_archive_existing_exit_evidence` + `run_supervised_cutover` | 写新 exit evidence 前，如已有旧文件，先 rename 归档（`_superseded_<HHMMSS>` 后缀），保留审计轨迹，再 O_EXCL 写新文件 |

### 测试

`tests/test_qfq_supervisor_defect_fix.py`（5 tests）：
- `TestArchiveExistingExitEvidence`：归档 rename + 内容保留 + 无文件时 raise
- `TestSupervisorFailedChildHandling`：source 检查 defect-A early-return guard + defect-B archive-before-write 顺序
- `TestEvidenceChainAfterArchive`：旧 evidence 归档 → 新 evidence 写入 → 两份都正确

### 测试结果

```
python -m pytest tests/test_qfq_supervisor_defect_fix.py -v   # 5 passed
```
全回归 87 passed（含 WP7-E3 17 + supervisor 5 + formal/staging 65），1 个 Windows DuckDB 并发 hard-crash 测试在隔离串行下 PASS（已知并发敏感性，非回归）。

### 未执行声明

supervisor 修复是代码 + 测试改动，未碰正式库、未 stage/commit/push。
