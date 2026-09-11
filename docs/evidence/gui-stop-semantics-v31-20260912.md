# GUI 停止语义 v3.1 实施 验收证据 · 2026-09-12

依据：`docs/gui-stop-semantics-design.md` v3.1 终版（§二三级语义 / §八 v2 闭合 / §九 v3 游标 / §十终版记录）。
派单：总调度《GUI 停止语义 v3.1（六步第 3 步）》；本件为实施完成回报（V1-V5 证据）。

## 1. 实施范围（对照派单 ①-⑥）

| # | 项 | 落点 | 规模 |
|---|---|---|---|
| ① | Worker 接线 cancel_check + L1 批量旗标 + 结构化取消 | `quantstudio/gui/workers.py` | +59 / -14 |
| ② | ⏹ 停止按钮 + 状态机 running→stop_requested→stopping→stopped + 三态文案 + L1 守卫 | `quantstudio/gui/tabs/task_tab.py` | +102 / -4 |
| ③ | collector 执行契约：execute 可选 cancel_check + 日批/每股边界接线 + 游标会话 | `quantstudio/pipeline/daemon.py` | +172 / -3 |
| ④ | `.gitignore` 追加 `data/task_resume/`（审计 B2 闭合） | `.gitignore` | +3 |
| ⑤ | 游标模块（schema + 推进/判废/清除/降级四规则） | `quantstudio/pipeline/task_resume.py`（新增） | 244 行 |
| ⑥ | 新增验收测试 | `tests/test_gui_task_stop.py`（新增） | 403 行 / 12 用例 |

**不触碰核验（逐项确认零改动）**：daemon 三段式停止 · QFQ cycle 状态机 · 官方水位提交路径 · 其他 tab · 回测取消路径。

## 2. 写前快照（AGENTS.md「写前快照」纪律）

```
git stash create -u -m "baseline-gui-stop-v31-20260912"
  -> 53f2bd5dac2b1c6691d195672e4c3d33a28e71b3
git stash store -m "baseline-gui-stop-v31-20260912" 53f2bd5d...
  -> stash@{0} baseline-gui-stop-v31-20260912
四目标文件改动前 git status --porcelain 全为空（无他人未提交改动叠加）
HEAD 实施前: 3d74998（ahead 1，未推送——推送批统一走用户确认，禁代推）
```

回退手段：`git reset --hard 53f2bd5dac2b1c6691d195672e4c3d33a28e71b3`

## 3. 实施期关键裁定（**提请审计复核**）

1. **游标仅在 `cancel_check` 下发时启用**——常驻 daemon 链零变化（设计红线「零触碰 daemon 执行链」）；
   同时使 V5「cancel_check=None 行为逐位一致」在**调用签名层**也成立。
2. **停止时 QFQ cycle 悬置**——取消路径**不调用** `qfq_run_post_ingest`（v2 定案「停止代码不读写 cycle 状态机」），
   陈旧 open cycle 交由 `begin_cycle` 内建 `supersede_stale_intents` 自动处置（审计已判定，零额外代码）。
3. **三处 `except` 补 `TaskCancelled` 透传**（原坐标 430 `_execute_task` 换源回退 / 855 `_run_with_source` / 1055 流式路径）——
   否则停止会被误判为「换源重试」（重新拉一遍）或「批次失败 + 记审计失败」。
4. **游标语义 = 已完成单元的连续前缀**——并发乱序完成（as_completed / wait）下绝不跳过未完成单元（向安全侧失败）。
5. **窗口指纹 = task + mode + window.start**（`end` 逐日漂移不参与指纹）——若 end 参与，则「今天停、明天续」恒判废，
   与「停止即续传」语义冲突。**本条为实施裁定，请审计确认。**
6. **V5 兼容加固（实测回归驱动）**——初版无条件传 `cancel_check=` 曾致 5 个既有 GUI 用例失败
   （`test_gui_task_audit_separation.py` 的 collector 替身签名不含该可选参数）；改为**未下发谓词时按原签名调用**后回归全绿。

## 4. 验收结果

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| V1 | L1 批间停止 | **PASS** | `test_v1_batch_stop_between_tasks`（旗标悬挂 → 零任务派发、stopped=True）、`test_v1_batch_stop_after_first_task`（第 1 个任务后置旗标 → 仅 t1 派发） |
| V2 | L2 日批边界取消（注入第 N 边界命中） | **PASS** | `test_v2_day_batch_cancel_fires_at_injected_boundary`：真实 `_run_with_source_streaming` 分片循环，命中后仅处理第 1 片、**水位零推进**、游标落盘 last_completed=2024-01-01 |
| V2+ / V8 | 游标续传（从 N+1 起拉） | **PASS**（方法级） | `test_v2_resume_starts_after_cursor`：新宿主同窗口 → `_open_day_resume` 返回 2024-01-02（跳过已完成日）+ `resumed_from` 命中 + 续传运行正常推进水位 |
| V3 | 状态机与按钮态 | **PASS** | `test_v3_tab_stop_state_machine_and_buttons`：running→stop_requested→stopping→stopped 全路径；停止请求**幂等**（二次点击文案不变）；按钮复原。`test_v3_cancelled_task_done_marks_stopped`：TaskCancelled → 任务状态「已停止」（非「失败」） |
| V4 | 回归（GUI 套件 + 全量套件） | **PASS** | GUI：`73 → 85 passed`（+12 新用例，零回归）；全量：`61 failed / 2886 passed`，失败集合与实施前**逐条一致**（NEW_FAILURES=0、NO_LONGER_FAILING=0） |
| V5 | 向后兼容（cancel_check=None） | **PASS** | `test_v5_backward_compat_without_cancel_check`：不建游标、起点不变、全分片正常处理、水位正常推进、无游标文件残留；worker 层：None 时按原签名调用（既有替身零感知） |
| V9-① | 成功完成清除游标 | **PASS** | `test_normal_completion_clears_cursor` + `execute_task` finally 内 `clear(reason="completed")` |
| V9-② | 窗口指纹不一致判废 | **PASS** | `test_v9_cursor_invalidated_on_window_mismatch`（新窗口指纹 → 陈旧游标文件被判废删除） |
| V9-③ | 官方水位越过窗口判废 | **PASS** | `test_v9_cursor_invalidated_when_watermark_moved`（水位 ≥ last_completed / 水位基线变化 → 判废） |
| V9-④ | 游标写失败降级 v2 | **PASS** | `test_v9_cursor_write_failure_degrades_to_v2`（写盘失败 → degraded=True、try_resume 返回 None = 全窗重拉，**不抛异常**） |
| 接线 | cancel_check 透传 | **PASS** | `test_worker_passes_cancel_check_through`（LockedTaskWorker → collector.execute_task 收到同一谓词对象） |

```
$ python -m pytest tests/test_gui_task_stop.py -q
............                                                             [100%]
12 passed in 2.87s

$ python -m pytest tests -k gui -q
85 passed, 2874 deselected in 16.96s
```

## 5. V4 全量套件（无新增失败）

| 时点 | 命令 | 结果 |
|---|---|---|
| 实施前（参考基线，55e788b） | `python -m pytest tests -q -rf` | 61 failed, 2873 passed, 3 skipped, 8 xfailed |
| 实施后（3d74998 + 本改动） | `python -m pytest tests -q -rf` | **61 failed, 2886 passed**, 4 skipped, 8 xfailed, in 1210.40s |

- 失败数 **不变**；通过数 **+13**（= 新增 12 用例 + QuestDB 适配器笔 1 项）。
- 失败集合逐条比对（`-rf` 摘要解析 vs 参考集）：**NEW_FAILURES = 0**、NO_LONGER_FAILING = 0 → 集合完全相同。
- 61 条为**实施前既有**失败，落点 `pipeline/` `backtest/` 策略生成/契约等模块，与本改动无关（本次零触碰）。

## 6. 隔离与安全核验

- 测试游标目录经 `QUANTSTUDIO_RESUME_DIR` 重定向到 `tmp_path`，**绝不写真实 `data/task_resume/`**；
- **未触碰 `data/quantstudio.db`**（主库有 GUI 写锁 + 回填活跃）：本次全部测试无任何真实库读写；
- 与 QuestDB 回填文件不相交（本改动 = workers/task_tab/daemon 契约 + task_resume + 测试；回填 = questdb_adapter/config）。

## 7. 已知限制（诚实登记）

1. **单任务停止等待期的逐条进度文案未达设计 §8.3 全额**：collector 的日批进度走 `logger` 而非进度信号，
   故单任务等待期显示固定的「停止已请求：当前日批完成后停止（分钟表最长约 20-30 分钟）」；
   批跑路径可显示「正在停止…（执行 X (i/N)）」（worker 有 per-task 进度信号）。
   若要单任务也显示「当前日批 X/Y 完成」，需给 collector 增加 progress 回调参数——**超出本版文件范围，建议独立立项**。
2. **V8 真实拉取级黄金对比未做**：需真实数据拉取 + 主库写入，与派单条件②（禁碰主库）直接冲突；
   本版以方法级等价测试覆盖（续传起点正确 + 水位推进时机不变 + 停止路径水位零推进）。
   真实「不中断运行 vs 停止后续传运行」终态逐位一致，建议在 V6 之后专项（可用隔离 DB 影子库进行）。
3. **V9-⑤（陈旧 open cycle supersede）无新增代码亦无新增测试**——审计已判定为 `begin_cycle` 内建
   `supersede_stale_intents` 自动处置；本版仅确保停止路径不主动触碰 cycle。

## 8. 回退

- 零副作用回退点：`git reset --hard 53f2bd5dac2b1c6691d195672e4c3d33a28e71b3`
- V4 出现任何新增失败 → 立即回退（本条未触发）。
