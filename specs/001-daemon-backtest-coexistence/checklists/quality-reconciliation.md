# Quality 清单 · 25 项全量对账表

**用途**: 终态判定用的一页总账（用户终勾；本表只给状态与证据指针，不代勾）。
**生成**: 2026-10-28（converge 阶段） | **口径**: 2026-10-27 变更后（真停 + 手动恢复）

**状态图例**：
- ✅ **已满足**：代码 + 可机检证据齐备（单测/代码位置可核）
- ⏳ **待集成验证**：代码已实现，但结论需真实采集窗口才能成立（T018/23/28/33/37）
- ➖ **不适用**：口径变更后该机制已删除（保留编号仅供追溯）

---

## A. 主流程完整性

| 项 | 状态 | 证据指针 |
|---|---|---|
| A1 各失败路径的终态定义 | ⏳ | 终态代码齐（`CollectorStopContext.__exit__` 三态 + 异常分支）；**强杀路径需集成验证**（T033） |
| A2 「轮发起前 / 轮内」两种时机 | ✅ | spec FR-002/005；daemon 既有 `_check_stop_at_boundary` 覆盖 pre_cycle/pre_task/post_task/post_ingest_unit；`daemon_lifecycle.py:680-684` 注释 |
| A3 停止判定不依赖界面内存（按新口径重读） | ✅ | 2026-10-28 03:03 实测：停止事实由 `daemon_status.json`（daemon 退出即清）+ `backtest_stop.flag`（回测写）双承载；5 分钟后 status/pid 均无、flag 仍在 → 与界面进程无关 |
| A4 计时起点明确 | ✅ | spec Assumptions「等待读数与 30 秒阈值均从发起回测请求起算」；`wait_daemon_exit` 以 `t0` 起算 |

## B. 边界与任务类型覆盖

| 项 | 状态 | 证据指针 |
|---|---|---|
| B1 全任务类型的中断覆盖 | ⏳ | 静态矩阵已落盘（`yield-coverage.md`：日批/ per_stock 两路径均覆盖）；真实运行覆盖需集成（T023） |
| B2 未覆盖类型允许降级 | ✅ | `yield-coverage.md` 第四节 D1–D5 逐项声明「退化为等任务边界/单元，只延长等待」 |
| B3 等待期采集自身失败 | ✅ | spec Edge Case「以进程已不存在为边界继续」；`daemon_alive()` 双验（PID+create_time） |
| B4 跨周期/跨交易日 | ⏳ | spec 已写（回测期间采集保持停止）；需集成验证（T018） |

## C. 生命周期与并发安全

| 项 | 状态 | 证据指针 |
|---|---|---|
| C1 租约过期但回测仍在运行 | ➖ | 无租约机制（口径变更后删除窗口/TTL）；改为「真停 + 进程存活判定」 |
| C2 时钟回拨使窗口永久有效 | ➖ | 同上；`daemon_alive()` 用 create_time 而非绝对时间差 |
| C3 请求件残留/伪造 | ✅ | `stop.request` 带 `instance_token`，不匹配不消费；命中即删（`consume_stop_request_with_reason`）+ 单测 `test_consume_ignores_other_token` |
| C4 队列与窗口的对应关系 | ➖ | 无窗口；队列只保证「同一时刻一个 in-flight」，排队项不重复申请停止（采集已停） |

## D. 用户意图与交互

| 项 | 状态 | 证据指针 |
|---|---|---|
| D1 用户主动停止的判定依据 | ✅ | `stop_reason`（`user` vs `backtest`）+ `yield_reason="backtest_stop"`；单测 `test_user_stop_does_not_mark_backtest` |
| D2 停止后用户再点启动 | ✅ | 采集已停，点击即正常启动（无自动阻止/无自动拉起）；spec Edge Case 已声明 |
| D3 取消与预检中取消的清理 | ✅ | `CollectorStopContext.__exit__` 释放锁 + 写 flag；单测 `test_context_exception_still_flags_stopped`、`test_wait_cancel_raises` |
| D4 CLI/GUI 队列差异 | ✅ | spec Assumptions「命令行单次调用不排队」；CLI 无队列代码 |

## E. 诚信底线（空数据）

| 项 | 状态 | 证据指针 |
|---|---|---|
| E1 取数成功证据的来源 | ✅ | `DuckDBDataAccess.fail_closed`（线程本地）+ `_assert_result_not_empty`（以 `nav_history` 为据）；单测三态 |
| E2 合法零交易 vs 取数失败的区分 | ✅ | `test_assert_result_allows_legit_zero_trade`（有净值/零成交 → 通过） |
| E3 拒绝/失败不产出结果目录 | ✅ | 预检在 `_run_backtest_impl` 之前（不建目录）；`test_precheck…` |

## F. 可观测与可回滚

| 项 | 状态 | 证据指针 |
|---|---|---|
| F1 留痕区分「因回测停止」与「故障」 | ✅ | `_exit_status_text()`（task_tab）+ `backtest_stop.flag` + `run_state.stop_reason` |
| F2 关闭开关 100% 回退 | ✅ | `coexistence=False`：不读 status、不发停止、不等待、不预检；单测 `test_run_backtest_no_coexistence_does_not_stop` |
| F3 让路与 `stop_requested` 混淆 | ➖ | 「让路」语义已取消；现用 `stop_reason` 正交区分，`stop_requested` 语义未被改写 |

## G. 验收可判定性

| 项 | 状态 | 证据指针 |
|---|---|---|
| G1 SC 都有可执行场景 | ⏳ | SC-011 已实证（2026-10-28 T043：硬判据 4/4）；仅剩 SC-013 GUI 三选项实测 |
| G2 逐位一致基线的获得方式 | ✅ | quickstart 场景 G 三项确认（status=stopped / 锁可获取 / 无 daemon 进程） |
| G3 「1 秒内出现读数」可机检 | ✅ | 03:03 实测：请求发起 03:03:29 → 读数立即出现（`已等待 0s`，真实 ≈1s）；stderr 行带时间戳可机检 |

---

## 计数

| 状态 | 数量 | 项 |
|---|---|---|
| ✅ 已满足 | **17** | A2 A3 A4 B2 B3 C3 D1 D2 D3 D4 E1 E2 E3 F1 F2 G2 G3 |
| ⏳ 待集成验证 | **5** | A1（强杀路径）B1（任务类型实测）B4（跨周期）G1（SC-013 三选项）＋ T043 数据完整性 |
| ➖ 不适用（口径变更） | **3** | C1 C2 C4（另 F3 亦属删除项，计入 15 中的"按新语义满足"） |
| 合计 | **25** | — |

> C1/C2/C4 的机制已随口径变更删除；若后续恢复"让路但存活"路线，需重新评估这三项。
