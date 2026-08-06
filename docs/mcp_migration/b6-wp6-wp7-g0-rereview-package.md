# B-6 WP6 / WP7 G0 复审包

> **权威日期**：2026-08-06（星期四）  
> **复审对象**：`docs/mcp_migration/b6-wp6-wp7-formal-cutover-plan.md`  
> **复审对象 SHA-256**?`b6f18153848572ed2ff5193a42cb9a55a885a8c3b46bf7a8648f37a00e509336`  
> **首轮结论**：`REVISE / P0=0 / P1=5 / P2=3 / 大跨度 A=NO`  
> **当前动作**：只完成计划修订，未开始实现、未访问正式库 read-write、未 stage/commit/push、未更新权威报告为审核通过。

## 1. P1 修订映射

| P1 | 修订结果 | 位置 |
|---|---|---|
| P1-1 Authorization manifest 防篡改 | 增加用户离线生成、独立授权根、raw bytes SHA-256、独立通道 expected hash、CLI 双参数、parse 前 hash、跨 run-dir 持久 nonce ledger、O_EXCL 预留并烧毁 nonce、consumption 绑定、ACL 附加防护、watermark scope 禁止 | §3.1、§3.1.1、§7.1 |
| P1-2 Windows 双锁/stale 算法 | 增加 PID/create_time/exe/cmdline/token 五验、alive/denied/stale 三态、双锁固定顺序、二次扫描、mtime 仅诊断、stale archive、禁止自动 kill、显式 force-stop 外部授权 | §3.3.1、§7.1 |
| P1-3 pre-SHA / Git 工作树口径 | 分离代码身份、tracked 工作树、formal main/aux pre-evidence、config SHA；只要求 tracked clean，ignored/untracked 不误拒；formal evidence 在授权/preflight/双锁/写前多次核对 | §3.3.2、§7.1 |
| P1-4 Fault matrix 等价 | 明确 formal/staging 共享事务 core 或版本化语义向量；六个 activation fault point 逐项等价；migration/activation 两类 after-COMMIT exact exit 92；pre-COMMIT 全量快照回滚；ALREADY_CURRENT/ALREADY_ACTIVE recovery | §3.5.1、§3.7.1、§7.1 |
| P1-5 水位释放入口 | formal child+supervisor 完全退出，独立 exit evidence；formal runner 禁止导入生产采集入口；WP7-E3 唯一使用现有 `daemon.py --mode once -> run_once -> execute_task -> qfq cycle -> post_ingest`，四任务串行，任一 held/failed 立即停 | §3.8、§5.3、§7.2 |

## 2. P2 处置映射

| P2 | 修订结果 | 负责人/期限 |
|---|---|---|
| 双 aux 并存治理 | dynamic 绝不 fallback legacy；legacy 只读保留；监控 open path/mtime/integrity；G3+观察+rollback retention+用户批准后才退役 | ZCode/Codex 实施，CodeBuddy 审核，用户接受；设计 G1 前、结论 G3 前 |
| 磁盘公式 | 固化 `5*main + 2*aux + max(10GiB,20%*(main+aux))`；当前基线 `91004735488` bytes；代码/runbook/test 共享同一函数/常量 | G1 前 |
| 观察期计数 | 改为至少 2 个完整盘后采集成功 + 2 个独立增量重放成功；非交易日不计；半日市条件计数；失败自动延长 | 规则 G1 前、实证 G3 前 |

## 3. 额外一致性修订

- handoff 不能自证 runner 已退出；新增 supervisor 发布的 `formal_runner_exit_evidence.json`，WP7 必须同时验证 handoff 与 exit evidence。
- 修正 handoff/双锁时序：child 持锁发布 handoff 时只记录 `locks_release_pending=true`；child 退出后由 supervisor 复取并释放双锁，再在 exit evidence 记录 `locks_released_verified=true`。
- handoff 不再要求在文件内容中包含“自身 SHA”；改由 supervisor 对 handoff raw bytes 复算 SHA-256，并由 exit evidence 与 WP7 独立复算共同绑定。
- nonce 防重放账本固定在 authorization root 的跨 run-dir 持久路径；O_EXCL marker 一旦创建即烧毁 nonce，禁止删除后重试。
- formal runner authorization scope 最多包含 WP6 + held-canary，不包含 watermark release。
- formal runner 和 WP7-E3 分属不同进程、不同入口、不同授权阶段。
- 权威进度报告仍遵守“CodeBuddy PASS 后更新”，本次 REVISE 修订没有提前登记审核通过。

## 4. 请 CodeBuddy 复审

请读取完整修订计划并一次性返回：

```text
结论：PASS / REVISE / BLOCK
首轮 P1 关闭：P1-1 CLOSED/OPEN；P1-2 CLOSED/OPEN；P1-3 CLOSED/OPEN；P1-4 CLOSED/OPEN；P1-5 CLOSED/OPEN
首轮 P2 处置充分：P2-1 YES/NO；P2-2 YES/NO；P2-3 YES/NO
P0：数量 + 完整清单
P1：数量 + 完整清单
P2：数量 + 完整清单

必须修改：
1. ...

允许保持：
1. ...

建议的大跨度 A 实施任务：
- 文件范围
- 测试范围
- evidence
- 禁止事项
- 出口条件

是否允许进入大跨度 A：YES / NO
```

本轮仍只审核计划，不授权正式库写入，不授权 Git stage/commit/push。若 PASS，请直接给出可执行的大跨度 A 任务安排。
