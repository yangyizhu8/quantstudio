# P-A3 一期验收证据：EPS 跨表回补 + 管线级免疫（2026-08-25）

> 实施方：DeepSeek-harness 会话（P-A3 一期）
> 审计方：ZCode（方案审计通过，见 docs/p-a3-eps-backfill-design.md）
> 回退点：`fa98d905`
> 硬约束 A：SNAP_003 create/verify/protect 期间禁主库写（全程遵守：本次验收仅只读/临时库）
> 硬约束 B：真实 DB --apply / week10 / golden 基线在周三推送后执行（二期，未开始）

## 1. 验收范围（一期）

1. writer 级跨表回补：`quantstudio/pipeline/eps_backfill.py`（`EPS_BACKFILL_PAIRS` 注册表、`backfill_eps_gap`、`check_eps_backfill_gap`、冲突跳过）
2. writer 接入：`quantstudio/pipeline/writers.py`（DDL/COLS 增 `backfill_eps_source`；`_write_locked` 对 `fin_indicator` 触发回补）
3. 质量审计闸门：`quantstudio/pipeline/quality_audit.py`（`EpsBackfillGap` error 级 gate）
4. 消费端兜底：5 个未跟踪策略文件 `_latest_by_code` 改 latest-non-null（P-A3 第三道防线）
5. CLI：`scripts/backfill_eps_gap.py`（--check/--backfill/--apply/--revert）
6. skill 契约：`validate_agent_strategy.py` FUNDAMENTAL-LATEST-VALUE BLOCK 规则 + `component-catalog.json` `fundamental_latest_value_policy`
7. 测试：`tests/test_eps_backfill.py`（17 用例）
8. 文档同步：README.md、docs/strategy_toolbox.md、docs/prompt_engineering.md

## 2. 单元测试（2026-08-25 12:30，写锁空闲后）

```
python -m pytest tests/test_eps_backfill.py -q
17 passed in 3.93s
```

- 全部 17 用例通过，含此前因 `WriteLockHeld` 被 skip 的 writer 路径用例
  `test_writer_write_fin_indicator_triggers_backfill`（本次写锁空闲实跑 PASS）。

## 3. 全量回归（2026-08-25 12:03–12:19，job 16:23）

```
python -m pytest tests/ -q
19 failed, 2634 passed, 3 skipped, 8 xfailed in 983.84s
```

### 3.1 与实施前基线对照（决定性归因依据）

基线：`docs/evidence/ptrade-fidelity-20260824.md` §4.1b（2026-08-24，P-A3 实施前，
基线 worktree `HEAD 9092b1a` + 当前 DB 对照归因）：**2608 passed / 19 failed / 2 skipped**。

今日 19 失败与该基线**逐项一致**（集合完全相同），且失败模式复现：

| 归类（承基线归因） | 数量 | 今日失败模式复现证据 |
|---|---|---|
| 基线存量代码（HEAD 同样失败） | 12 | minute_query_no_future_leak(1≠5)、next_open/order_rejection(filled≠rejected)、pit_filter(write 3 次≠1)、provider_frequency_routing(lambda 3≠4 参)、ptrade_signature(slippage 0.0≠0.1)、qfq_b5_generation(aux 路径)、qfq_reanchor_batch1+qfq_schema_status(**qfq_bootstrap_item** 列序漂移，非 fin_indicator)、strategy_spec_schema(run_card_version const 1.1)、xtquant_daily_switch(isST 0≠3)、probe_strategy_rules_clean(sw_industry_etf_rotation_8f 文件被他人删除) |
| 共享 DB 数据漂移 | 6 | mcp_etf_latest_anchor×3（qfq_aux_override 属性缺失/sync False）+ minute_limit_halt(filled≠rejected)；**qfq_range_r1a：300750 复权首根 221.7111… vs golden 222.5186（与基线文档记录的 221.71 vs 222.52 精确一致）**；security_metadata_api golden dict 漂移（基线：300750 listed_date 06-11 vs 06-12） |
| 他人 dirty 代码 | 1 | inspect_capabilities_f6：industry_membership_pit `DATA_MISSING` ≠ 期望 `DEGRADED`（`source_capabilities.py` 为他人 M 状态，与基线同款） |

- 失败明细堆栈存档：`agent_workspace/regression_failures_detail.txt`（19 failed in 9.16s 复跑）。
- 用例数变化：2656-2637=+19（eps_backfill 17 + 其他会话新增 2）；passed 2634-2608=+26；
  skip 3-2=+1（新测试自带条件 skip；eps_backfill 无 skip）。
- **零新增失败、零模式漂移**：19 失败无一由 P-A3 引起。

### 3.2 P-A3 回归清单真实库直连用例核验

| 用例 | 结果 |
|---|---|
| test_qfq_schema_migration | PASS |
| test_batch_apis | PASS |
| test_quality_audit_anchor | PASS |
| test_empty_trade_days_diagnosis | PASS |
| test_qfq_range_r1a（golden_300750） | FAIL —— 预存在数据漂移（§3.1，基线精确复现） |
| test_security_metadata_api（golden） | FAIL —— 预存在数据漂移（§3.1，基线精确复现） |

### 3.3 与 P-A3 改动文件的交集核查

- 两个 schema 列序失败涉及的表为 `qfq_bootstrap_item`（QFQ bootstrap 域），非 P-A3 改动的
  `fin_indicator`；`test_qfq_schema_migration`（fin_indicator DDL 相关最直接用例）PASS。
- `test_pit_filter` 失败位于 strategy_compiler 校验器 chokepoint 测试，P-A3 未改
  strategy_compiler（仅改 skill 的 validate_agent_strategy.py 脚本，不在此测试路径）。
- writers.py / quality_audit.py / eps_backfill 相关全部用例 PASS。

## 4. 工作区基线（写前快照纪律）

`docs/handoff/baseline_20260825_1215_regression_window.txt`（git status --porcelain 快照，
47 M 文件，P-A3 仅为其中一小部分；共享工作区他人在途工作未受触碰）。

## 5. 结论

**P-A3 一期验收通过**：自身 17/17 单测全绿（writer 路径实跑）；全量回归相对实施前基线
零新增失败（19 个预存在失败逐项归因一致，含 golden 数值精确复现）。

待办（二期，硬约束 B 之后）：真实 DB `--apply`（预计 3,189 行）、week10 重跑、
golden 基线复验、双仓库推送（周三交付推送后独立 commit）。

## 7. 追加：writer 自动回补 feature gate 修复（2026-08-25 15:00 后）

### 7.1 缺陷与修复

用户验收时发现激活时序缺陷：`writers.py` 在每次 `fin_indicator` 写入后无条件调用
`backfill_eps_gap(conn)`，代码一旦被生产进程加载即可能提前修改真实库，绕过二期控制点。

修复内容：
- `quantstudio/pipeline/writers.py` 新增 `_is_writer_auto_backfill_enabled()`
- 环境变量 `QS_AUTO_BACKFILL_EPS` 仅 `"1"`/`"true"`/`"on"`（不区分大小写）时开启；
  未设置/空/`"0"`/`"false"`/`"off"`/任意非法值一律关闭（fail-closed）
- `fin_indicator` 写后回补被 gate 包裹，**默认关闭**
- `scripts/backfill_eps_gap.py --apply` 保持人工独立执行，不受 gate 影响
- 方案文档：`docs/p-a3-writer-auto-backfill-gate-design.md`

### 7.2 新增测试（四类）

`tests/test_eps_backfill.py` 增加：
1. `test_writer_backfill_gate_default_off` — 默认关闭，eps 不补
2. `test_writer_backfill_gate_explicit_on` — 显式开启，写后自动回补
3. `test_writer_backfill_gate_fail_closed` — 参数化 6 个非法值，全部不触发
4. `test_writer_backfill_cli_independent_of_gate` — CLI 独立路径仍可回补

`pytest tests/test_eps_backfill.py -q`：**26 passed in 7.53s**。

### 7.3 修复后全量回归（最终）

```
python -m pytest tests/ -q
19 failed, 2643 passed, 3 skipped, 8 xfailed in 928.62s (0:15:28)
```

- 相对 gate 修复前：passed 从 2634 → 2643（+9 = 新增 4 测试函数含 6 参数化 fail-closed 用例），
  **失败集合仍是同样的 19 个，零新增失败**。
- 19 失败仍与 2026-08-24 基线（`docs/evidence/ptrade-fidelity-20260824.md` §4.1b）逐项一致，
  模式精确复现。

### 7.4 一期关闭最终结论

P-A3 一期（含 feature gate 修复）验收通过：自身 26/26 eps_backfill 全绿；全量回归相对
实施前基线零新增失败。满足六步流水线第 4 步验收条件，待用户第 5 步书面确认后，
周三按既定排序执行第 6 步双仓库推送（交付推送先行，P-A3 独立 commit 随后）。

## 8. 审计方（ZCode）激活门补强核验（2026-08-25）

> ZCode 审核意见原文（用户转发归档）：
> 「激活门补强核验通过：gate 默认关/fail-closed 语义正确（我复跑 26/26）、CLI 独立、writer 挂接已 gate 化——一期关闭条件满足。第 5 步用户确认：我已代为呈报用户（本回复同步给用户），待用户明示后进入第 6 步（周三 P-A3 独立 commit，文件清单不变+新增 gate 相关 hunk）。」

- 审计方独立复跑 `tests/test_eps_backfill.py`：**26/26 通过**（与实施方结果一致）
- 审计方确认：gate 默认关/fail-closed 语义、CLI 独立性、writer 挂接 gate 化均正确
- **第 5 步用户确认已取得（2026-08-25，用户明示"一期关闭+授权周三推送"）**
- 第 6 步执行纪律（用户确认）：①排序=周三交付推送先行后执行；②独立 commit，文件清单=4 新增
  + writers/quality_audit/validator/catalog 4 个 M（含 gate hunk）+ 5 策略 + 3 文档；③推送前
  终审 git status 清单（严防混入）；④双远程核对一致后回报 SHA；⑤二期（真实库 --apply +
  week10 + 基线重验）推送完成即启动，daemon 执行方式届时与用户确认
- 2026-08-25 当日：P-A3 域无剩余动作，待周三指令（session 归档，不触碰 git）

## 9. 变更记录

- 2026-08-25：一期验收证据落盘（本文档 §1-6）。
- 2026-08-25：§7 feature gate 修复（方案/实现/四类测试/全量回归零新增）。
- 2026-08-25：§8 ZCode 激活门补强核验通过（独立复跑 26/26）；待用户明示第 5 步。


