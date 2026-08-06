# B-6 WP6 Formal Cutover Runbook

> **权威日期**：2026-08-07
> **状态**：大跨度 A 本地实现（待 CodeBuddy G1 审核）
> **授权边界**：本 runbook 只定义正式切换的操作契约与命令。正式执行须由用户在未来维护窗口离线生成真实 authorization manifest 后，按 G0/G2 授权执行。本阶段不写正式库、不 stage/commit/push、不更新权威进度报告为完成。

## 1. 总体流程（WP6 固定顺序）

正式切换在一个维护窗口内按下列固定顺序执行，任一步 fail-closed 即停止：

1. **authorization / preflight**：用户离线生成 manifest，CLI 双参数校验
2. **双锁**：`.daemon.lock` → `.collector_run.lock`（非阻塞，固定顺序）
3. **sidecar / 正式文件 evidence 再验证**（TOCTOU）
4. **fresh backup**：main/aux/config/manifests（双锁窗口内，全 O_EXCL）
5. **main schema**：`COMPLETE_2_0 → COMPLETE_2_1`
6. **generation-specific aux**：`qfq_aux_mcp_gen1.db` 初始化（O_EXCL/空校验）
7. **baseline build**：历史 baseline，不产生历史 trigger
8. **immediate baseline replay**：new trigger=0、pending slot audit=0
9. **activation**（共享 `_do_activate_in_txn`）：retirement + pointer CAS + mcp-gen1 active
10. **验证 source_watermark 完全不变**
11. **durable COMMIT**
12. **handoff**（child 仍持锁，`locks_release_pending=true`）
13. **child 退出**
14. **supervisor exit evidence**（复算 handoff raw SHA + `locks_released_verified=true`）

## 2. Authorization manifest 契约

### 2.1 离线生成（用户，未来维护窗口）

真实 manifest 由用户/项目负责人离线生成，存放于授权根目录（必须在 repo/data/正式DB/output evidence 之外）：

```
<authorization_root>\<cutover_id>\authorization.json
```

manifest 字段（必须）：
- `schema` / `version`
- `git_commit_sha`（精确 HEAD）
- `checkout_canonical_root`
- `formal_main_canonical_path` / `formal_aux_canonical_path`
- `formal_main_sha256` / `formal_aux_sha256` / `formal_main_size` / `formal_aux_size` / `formal_main_mtime_ns` / `formal_aux_mtime_ns`
- `config_sha`
- `cutover_id` / `price_source` / `source_generation` / `aux_db_path`
- `operation_grants`：每 grant 独立 `nonce`，grant 名 ∈ `wp6_formal_cutover` / `wp7_held_canary`
- `maintenance_window_id` / `issuer` / `approved_by`
- `watermark_release_authorized = false`（水位释放是独立未来授权）

### 2.2 CLI 双参数（fail-closed）

```
python -m quantstudio.pipeline.qfq_formal_cutover_cli \
  --authorization <path> \
  --authorization-sha256 <64hex> \
  --output-dir output/mcp_migration/b6_<date>_wp6_formal/ \
  [--dry-run | --execute]
```

- `expected hash` **绝不**从 manifest 自身或同目录 companion 读取
- 先读原始字节算 SHA-256，再 parse JSON
- 以下篡改均 fail-closed：单字节篡改、pre-SHA 篡改、commit/path/grant/watermark flag 篡改、自我声明 hash、二次读取内容变化

### 2.3 nonce 防重放

- ledger 跨 run-dir 持久：`<authorization_root>\consumed\<grant>\<nonce>.json`
- `O_CREAT|O_EXCL` 原子创建 marker；marker 已存在 → replay BLOCK
- **marker 删除/篡改检测**：`index.json` + `index_digest.json` 不可变摘要链；marker 文件被删除后重放仍被 index 链拒绝（不能只用 ACL 推断）
- WP6 与 held-canary 使用独立 nonce + 独立 grant ledger

## 3. 磁盘保守公式（P2-2，runner/test/runbook 共用同一函数）

```python
from quantstudio.pipeline.qfq_formal_authorization import compute_required_free_bytes
reserve = max(10 GiB, ceil(0.20 * (main_size + aux_size)))
required_free = 5 * main_size + 2 * aux_size + reserve
```

冻结基线参考值（main=14996746240, aux=2641793024）：`91004735488` bytes。
执行时必须按实时 size 重算，不能写死。`free < required` 立即 BLOCK。
降低系数属设计变更，须重新审核。

## 4. Windows 双锁与 daemon identity（§3.3.1）

固定顺序：
1. 读 `daemon_status.json` → `verify_daemon_identity` 五验（PID/create_time/exe/cmdline/token）
2. `alive` → BLOCK；`denied` → BLOCK；`stale` ≠ 可写
3. 非阻塞先 `.daemon.lock` 再 `.collector_run.lock`，任一 busy → BLOCK（不参考 mtime 放行）
4. 两锁后重新 identity 五验 + 进程扫描（daemon/collector/GUI worker/已知 writer）
5. sidecar + 正式文件 evidence TOCTOU 再检查

- **formal runner 禁止自动 kill**
- stale archive 仅在六条件全满足时执行（identity=stale、双锁已持有、二次扫描无 owner、原 status 复制进 evidence、authorization scope 明确允许 `archive_stale_daemon_status`、canonical path 校验）
- mtime 只进 evidence，不作 stale 阈值

## 5. handoff 与 exit evidence

### 5.1 handoff（`formal_cutover_handoff.json`，O_EXCL）

child 在仍持双锁时发布。字段：`connections_closed=true`、`locks_release_pending=true`、`watermark_release_authorized=false`、child PID/create_time、expected_exit_code、main/aux before/after evidence、backup/rollback manifest、migration/activation 状态、active pointer、legacy retirement counts、committed/dead_letter 守恒、watermark unchanged、aux SHA/integrity。**handoff 不得自含自身 SHA**。

### 5.2 supervisor exit evidence（`formal_runner_exit_evidence.json`，O_EXCL）

child 退出 → 验证 PID 不存在 + exit code 精确 → 从 handoff raw bytes 独立复算 SHA → supervisor 复取双锁（daemon→collector）二次 identity/descendant/sidecar 检查 → 释放（collector→daemon）→ 记录 `locks_released_verified=true` + handoff raw SHA。

WP7-E1/E2/E3 必须同时验证 handoff 与 exit evidence，并独立复算 handoff raw SHA。

## 6. after-COMMIT recovery（net-new ALREADY_ACTIVE）

`after_commit_before_report` fault 后，新进程 read-only 重开 → 识别 activation 已 durable（cutover=active、唯一 active pointer、legacy non-terminal=0、watermark unchanged）→ 不重跑 retirement/CAS → 新 O_EXCL 路径补发 report/handoff，状态枚举 `ALREADY_ACTIVE`。

`ALREADY_ACTIVE` 是 formal 新增枚举（migration 只有 `ALREADY_CURRENT`）；与 staging `after_commit_recovery.json` 的"逐项一致"指**字段名集合**，非枚举值复用。

## 7. formal runner 硬边界（§5.3.4 / §18）

formal runner 模块**不得 import**：
- `ResidentCollector.run_once()` / `execute_task()`
- `qfq_run_post_ingest()`
- `writer.advance_watermark()`

未来正式水位释放唯一入口是现有生产 CLI：

```
python -m quantstudio.pipeline.daemon --mode once \
  --config-dir config/profiles/mcp_only \
  --task <task-name> \
  --pull-mode incremental \
  --quality-audit full
```

固定任务：`mcp_etf_daily` / `mcp_etf_minutes` / `mcp_stock_daily` / `mcp_stock_minutes`。
不得新增 production watermark API；不得手工 UPDATE source_watermark。

## 8. schema migration 集成（方法 2）

formal runner 自写 `COMPLETE_2_0 → COMPLETE_2_1` 迁移序列（shadow 建→复制→校验→新表→swap→legacy 清理→fingerprint 回读→COMMIT→audit），只复用 migration 的只读 helper/常量（`detect_schema_status` / `_snapshot` / `verify_fingerprint` / 指纹常量 / `REBUILD_*` / `NEW_TABLES` / SQL 构造 helper）。

**不 import** migration 私有状态机（`_do_migrate_in_txn` / `_ReportReservation` / `_assert_not_production` / `_assert_allowed_root`）。migration 模块零改动，staging guard 原样保留。

G1 证据：formal 迁移后 `detect_schema_status == COMPLETE_2_1` + `verify_fingerprint(TARGET_MAIN_DB_2_1_FINGERPRINT, reject_extra=True)` + 无 `__b3b` shadow/legacy 残留 + 每表行数/内容 hash 与 staging 迁移后 `_snapshot` 逐项一致 + formal 迁移 SQL 与 `_do_migrate_in_txn` 逐语句 diff（预期除包装时序外 SQL 文本一致）。

## 9. 停止与 BLOCK 条件

任一触发立即停止，不自动继续：
- 正式 pre-evidence 与授权不一致
- backup 不完整或不可恢复
- schema 进入 PARTIAL/MIXED/UNKNOWN
- active pointer 不唯一
- source watermark 在 held-canary 前变化
- committed/dead-letter 历史改变
- canary 产生非预期 trigger/intent
- after-COMMIT 状态无法分类

## 10. 出口条件（G2 审核前）

```
formal migration complete
mcp-gen1 active
active pointer correct
watermark still held
normal production collection not started
```

G2 PASS 后用户另行明确授权解除 watermark hold，才能进入 WP7-E3 正常水位释放（见 post-cutover observation runbook）。
