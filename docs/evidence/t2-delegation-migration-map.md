# T2 契约迁移对照表（GUI×daemon 修复·委托化验收附件）

- 日期：2026-09-20｜归属：dev（T2）｜状态：随代码段提交
- 硬要求来源：总调度 2026-09-20 裁定（A 案附项）——旧断言→新等价断言逐条映射，四项覆盖、语义差如实标注、不静默。

## 一、替换清单（旧 8 条 → 新 6 条）

| 旧测试（已删） | 旧断言语义 | 新等价（tests/test_gui_task_delegation.py） |
|---|---|---|
| audit_separation::task_success_not_converted… | 任务成功不被无关全库审计转失败 | test_task_success_via_manifest_and_no_dangling：once_done+nonce_ok → task_ok=True，审计为独立字段 |
| audit_separation::final_signal_after_collector_close | 终信号前 collector 已关/锁已释 | 同上用例：终信号前 proc.poll() 非 None（子进程已退，无悬挂） |
| audit_separation::real_task_failure_remains… | 真任务失败保持失败 | test_task_failure_stays_failure_with_log_tail：日志尾随错误返回，不吞现场 |
| audit_separation::audit_exception_as_warning | 审计异常降级告警不转任务失败 | manifest+nonce 判定与审计字段分离（quality_audit_ok 独立） |
| audit_separation::run_all_propagates_each_qfq… | RunAll 逐任务结果传播 | test_run_all_propagates_each_result：results 逐任务 ok/error + 串行三子进程 |
| task_stop::worker_passes_cancel_check_through | cancel_check 透传 collector（协作式） | test_task_cancel_terminates_subprocess_and_reports_stop：见 §二·语义差 |
| task_stop::v1_batch_stop_after_first_task | 批间停止不派发后续 | test_run_all_batch_stop_skips_remaining：第 2 任务不派发（start 调用数≤2） |
| task_stop::v1_batch_stop_between_tasks（第 8 条残余，删死码后暴露亦属旧形态） | 批间停止 | 同上覆盖 |

## 二、语义差如实标注（不许静默）

**取消语义：协作式 cancel_check → 子进程硬停**
- 旧：谓词传入 collector.execute_task，日批/每股边界协作退出，无半途写。
- 新：谓词触发 `proc.terminate()`（**Windows 下为 TerminateProcess 硬停**，POSIX SIGTERM）。
- 兜底（总调度 2026-09-20 裁定）：锁残留由**批一 P0 写锁死亡自愈**处置；数据完整性由**写事务原子性**保障（中止即整事务回滚，无半写）。
- 停止文案如实告知：「已停止（采集子进程已终止；已拉取部分由水位续跑，未完成审计）」。

## 三、新形态补充保证（旧无对应）

| 保证 | 用例 |
|---|---|
| 采集期有界提示（不无限等待） | test_task_busy_returns_bounded_hint |
| workers.py 零 FileLock 构造（终态断言） | test_lock_preserve_file.py 静态契约 |

## 四、回归基线

- GUI 套件：**94 passed, 0 failed**（96 − 8 旧 + 6 新）；
| - 锁契约：4 passed；委托契约：6 passed；
- 既有 59 失败基线（V3 底稿）不受影响（GUI 段零新增）。
