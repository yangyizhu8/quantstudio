# B-6 WP7 Post-Cutover Observation Runbook

> **权威日期**：2026-08-07
> **状态**：大跨度 A 本地实现（WP7-P 规则 + 模板已实现，WP7-E 正式执行 + 实证待 G2/G3 授权）
> **授权边界**：本 runbook 定义切换后的审计、held-canary、水位释放与观察期。WP7-E 正式执行须由用户在 G2 PASS 后明确授权解除 watermark hold；观察期实证在 G3 前完成。本阶段不写正式库、不 stage/commit/push。

## 1. WP7 阶段总览

| 阶段 | 内容 | 授权 |
|---|---|---|
| WP7-E1 | immediate post-COMMIT 只读审计 | G2 PASS 后 |
| WP7-E2 | 正式 bounded held-canary（强制全局 watermark hold） | G2 PASS 后 |
| WP7-E3 | 通过现有生产采集链路推进水位 | 用户明确授权解除 hold 后 |
| WP7-E4 | 生产功能验证（GUI/daemon/MCP/回测） | E3 四任务全成功后 |
| WP7-E5 | 观察期（2+2 硬计数） | E4 后，G3 前实证 |
| WP7-E6 | 最终闭环审计 | E5 + G3 |

## 2. WP7-E1：Immediate post-COMMIT audit

`qfq_formal_postcutever_audit.audit_immediate(handoff_dir, config_dir)`

只读检查（必须全 PASS 才进入 held-canary）：
- handoff + exit evidence 同时存在，且独立复算的 handoff raw SHA 与 exit evidence 记录一致
- exit evidence `locks_released_verified=true`
- schema=`COMPLETE_2_1`
- active pointer 唯一、cutover=active
- aux 路由：`AuxDbRouter.path_for('mcp-gen1')`，绝不 fallback legacy（P2-1）
- legacy nonterminal=0、pending intent=0、started cycle=0
- committed/dead_letter 守恒
- `audit_pending_slots`=0（orphan/generation_mismatch/payload_mismatch）
- source watermark 仍为切换前证据
- mcp-gen1 aux `PRAGMA quick_check=ok`
- `watermark_release_authorized=false`

## 3. WP7-E2：正式 held-canary

`qfq_formal_canary.run_held_canary(...)`

- **严格早于 watermark release**
- 只在完整 handoff + exit evidence 后运行（复算 handoff SHA）
- 验证 `watermark_release_authorized=false`
- 独立消费 `wp7_held_canary` grant + 独立 nonce（与 WP6 nonce 隔离）
- scoped canary 强制全局 watermark hold（`watermark_policy=hold_until_consistent`）
- canary codes 默认 `config/qfq_canary_securities.json` 或 manifest 显式覆盖
- 断言集：baseline preserved、no_new_mcp_trigger、no_mcp_intent、source_watermark_unchanged、prices_unchanged、global_watermark_forced_hold（`finalized_held`）
- **unexpected trigger/intent → P0；source watermark changed → P0**
- abort recovery 只针对 canary 子进程，不 touch daemon/其他研究进程
- timeout 可恢复；evidence 用新 O_EXCL 路径

## 4. WP7-E3：唯一允许的首次水位释放入口（§5.3.2）

**禁止**：手工 `UPDATE source_watermark`、直调 `writer.advance_watermark()`、formal runner 调用采集入口。

唯一入口（现有生产 CLI）：

```
python -m quantstudio.pipeline.daemon --mode once `
  --config-dir config/profiles/mcp_only `
  --task <task-name> `
  --pull-mode incremental `
  --quality-audit full
```

固定任务（串行，禁止并行）：
- `mcp_etf_daily`
- `mcp_etf_minutes`
- `mcp_stock_daily`
- `mcp_stock_minutes`

调用链必须保持：
```
daemon.py --mode once
  -> CollectorRunLock
  -> ResidentCollector.from_configs(mcp_only)
  -> run_once(task_name, incremental, full)
  -> execute_task(...)
  -> _needs_manual_qfq_cycle()
  -> qfq_begin_cycle()
  -> existing fetch/align/validate/write
  -> _advance_or_defer_watermark()
  -> qfq_run_post_ingest()
  -> normal gate commit/hold
  -> full quality audit
  -> collector.close()
```

每个任务必须满足：runtime identity=`mcp/mcp-gen1/<formal-cutover-id>`、candidate intent 已生成、QFQ gate status=`finalized`、exactly one matching intent committed、watermark identity/last_batch_id 正确、quality audit 满足既定门。
**任一任务 failed/held/无 terminal intent：立即停止后续任务，不手工修水位。**

## 5. WP7-E5：观察期硬计数（P2-3，§5.5）

**硬出口**（不按日历日）：

```
complete_post_close_cycles_success >= 2
incremental_replay_cycles_success  >= 2
```

### 5.1 完整盘后采集周期定义

一次 `complete_post_close_cycle_success` 必须满足：
- `trade_calendar` 标记为有效交易日
- 到达配置的盘后调度窗口
- production daemon `DaemonLifecycle.run_one_cycle()` 遍历全部 eligible incremental tasks
- `traversal_completed=true`
- 四价格表 QFQ cycle 有明确终态
- 无未解释 failed/held/pending
- full quality audit 满足既定门
- run state / cycle / watermark / alerts evidence 完整

**非交易日不计数**。半日市仅在全条件满足时才计为一个完整周期，否则只记录不计数。

### 5.2 增量重放周期定义

一次 `incremental_replay_cycle_success` 必须在某次成功盘后周期之后：
- 从已提交 watermark 继续
- 无非预期全量重拉
- 无历史 trigger replay
- 无重复写入/重复 intent
- next-cycle baseline/pending-slot audit=0
- watermark 保持幂等或仅按新业务数据单调推进
- 必须是独立正式采集周期（非同进程函数重调）

### 5.3 观察期限

- 建议目标 1～2 个完整交易日
- 遇非交易日、数据源延迟、半日市不满足条件或任一周期失败 → 观察期自动延长，直到 2+2 满足
- **无"时间到了自动通过"**
- 每个计数绑定 run_id/scheduled_date/cycle_id/watermark before/after/evidence hash
- 每日 Run Card 固化（active identity、四表 watermark、cycle/trigger/intent、dead letter、aux integrity/sidecar、daemon/service、MCP API、GUI 状态、回测抽样、告警、两个累计值）

### 5.4 实现状态

- **规则 + Run Card 模板**：G1 前完成（`qfq_formal_observation.py`，`record_run_card` / `is_complete_post_close_cycle` / `is_incremental_replay_cycle` / `observation_report`）
- **正式观察 evidence**：G3 前完成

## 6. WP7-E6：最终闭环审计条件

```
formal COMPLETE_2_1
active pointer unique and correct
formal mcp-gen1 aux valid
legacy nonterminal=0
held canary PASS
normal watermark gate PASS
next-cycle idempotency PASS
GUI/daemon/MCP PASS
golden backtest identical
observation 2+2 PASS
rollback retained and tested
P0=0 / P1=0 / P2 resolved or explicitly accepted
authoritative report updated
```

## 7. 双 aux 治理（P2-1，§3.6.1）

- legacy：`data/qfq_aux.db`；dynamic：`data/qfq_aux_mcp_gen1.db`
- `source_generation=mcp-gen1` 只能路由到 cutover record 冻结的 dynamic aux；缺失/hash/integrity 失败一律 fail-closed，**绝不 fallback 到 qfq_aux.db**
- active pointer 切换后，正常 mcp runtime 禁止打开 legacy aux read-write
- legacy aux 保持只读回滚证据，不删除/覆盖/继续写
- 监控告警：dynamic identity 打开 legacy aux、legacy aux mtime 变化、dynamic aux integrity/sidecar 异常
- 退役前标记 `legacy_readonly_retained`；最终退役须 G3 + 观察期 + rollback retention + 用户单独批准

## 8. 授权边界提醒

- WP7-E3 前用户必须明确授权解除 watermark hold（不能从 G2 PASS 自动推断）
- GitHub 同步确认 ≠ 正式迁移授权；正式迁移授权 ≠ GitHub 同步确认
- G3 PASS 前权威进度报告不得登记"项目闭环"
