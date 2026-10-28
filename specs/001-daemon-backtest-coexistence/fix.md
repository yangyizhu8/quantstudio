# 实施留痕（fix 阶段）— 常驻采集与回测共存

**Feature**: `specs/001-daemon-backtest-coexistence` | **开工**: 2026-10-27（检查点 A 批准后）
**用途**: 记录实施期的前置核对、与既有在途工作线的叠加关系、以及对 tasks 的必要修订（不静默改）。

---

## 一、并行线核对（开工前置 · ZCode 执行约束 1）

**核对时间**: 2026-10-27 22:49 | **命令**: `git worktree list` / `git status --porcelain` / `git diff <file>` / 两分支 diff 对拍

### 1. 主检出（`D:\miniQMT策略实盘\QuantStudio`，branch=main，HEAD=76e9e50）

工作树存在**他线未提交改动**，其中 3 个文件与本单目标文件重叠：

| 文件 | 未提交改动量 | 内容 | 与本单关系 |
|---|---|---|---|
| `quantstudio/backtest/run_ptrade_strategy.py` | +3/-1 | M5.2 件7 `match_profile` 参数透传 | **同文件、不同位置**（参数表 + EngineConfig 调用），可叠加 |
| `quantstudio/gui/workers.py` | +1 | `match_profile` 透传 | 同上 |
| `quantstudio/gui/tabs/backtest_tab.py` | +9 | match_profile 下拉 + params 传参 | 同上 |

`daemon_lifecycle.py` / `daemon.py` / `duckdb_data_access.py` 在主检出中**干净**（无未提交改动）。

### 2. 在途工作线 `wf-minutes-fix`（f404368）/ `wf/write-scope`（76e4a1f）

| 事实 | 结论 |
|---|---|
| 两分支 vs main 的 `daemon_lifecycle.py` diff 均为**净删除**（-19 / -9 行） | 两分支**落后于 main**，其内容已在 main（`c50f934` A1–A10、`c96d09e` B1–B7） |
| 两 worktree 各自工作树里 `daemon_lifecycle.py` 为 **M**（未提交） | 内容 = `write_run_state()` 内追加 `write_retry` / `write_scope` **观测字段** |
| main 的 `write_run_state()`（`daemon_lifecycle.py:221-244`） | **已包含**上述两段观测字段 |

**判定**：所谓"改动面重叠"实为两线落后于 main 的重复展示；本单改动落在 `write_run_state()` **之外**（tick 判定 / `run_one_cycle` 让路 / 其 `**fields` 传参），与两线**零重叠**。

### 3. 本单落码纪律

- 不建立 `git stash`、不回退他线改动、不 `checkout`；
- 落码后逐文件 `git diff` 自检，确认他线 match_profile 改动**仍在**（未被覆盖）；
- 提交留到用户确认后的⑥推送窗；届时按铁律 10：精确文件清单 + 提交信息**显式记录**「同文件含他线未提交的 M5.2 match_profile 改动」，并按唯一临时文件名写 commit message（铁律 3/10 + 记忆 c3fda7d1）。

---

## 二、实现路径修订（相对 tasks.md）

### 修订 R1：T007「daemon.py 加 `_yield_requested` + 4 处检查点」→ 改为复用既有原语

**原计划**：给 `ResidentCollector` 加 `_yield_requested` 属性，在 4 处 `if not self._running:` 旁追加判断。

**实际实现**：**daemon.py 零改动**。改用既有 `execute_task(cancel_check=...)` + `_task_boundary()`（`daemon.py:713-820`）——该原语命中即 `raise TaskCancelled`，语义正是「任务内单元边界中断 + 水位保持 + 下轮重跑」。

**为什么更好**：
1. 复用既有原语，符合 constitution 一（最小可逆、落点所有者）；
2. 不触碰 `daemon.py` 执行链——原代码注释明确「常驻 daemon 链零变化（设计红线）」，本次以**注入参数**方式扩展，未改其代码；
3. 中断语义已被既有 8 处停止路径验证过（水位保持、游标保留、事务完整），风险更低。

**代价**：`cancel_check` 每个单元边界被调用一次（日批每交易日、per_stock 每股）；已加 1s 轻缓存（`DaemonLifecycle._current_window`），无窗口时仅一次 Python 函数调用。

### 修订 R2：让路时的质量审计

原 spec FR-017 只要求「本轮如实标记为让路中断」。实现选择：让路时审计**仍启动**但在第一个检查点立即中止（`should_stop` 注入 `yield_requested`），未执行项计入 `skipped_after_stop`——保留"每轮必审计"框架，同时不拖延让路。

---

## 三、退化项影响面（对应 T011 覆盖矩阵第四节）

| 退化项 | 声明 |
|---|---|
| D1 单元执行中不可中断（分钟单只约 17s / 日批单批） | 退化为等该单元结束；**不影响正确性**（边界处才中断，事务已提交） |
| D2 qfq post-ingest 进行中不可中断 | 退化为等该阶段结束；让路发生在任务循环内时**直接跳过** post-ingest（水位保持） |
| D3 质量审计 | 让路时在第一个检查点中止，未执行项如实计入 `skipped_after_stop` |
| D4 写冲突退避 | ≤30s 上界，退化为等退避结束 |
| D5 未走 `execute_task` 的采集入口 | 不响应让路，退化为等整轮；不产生错误结果 |

统一规则：**退化只延长等待，不改变结果**；等待期用户可见真实读数并可取消。

---

## 四、逐轮记录

| 轮次 | 任务 | 结论 | 证据 |
|---|---|---|---|
| 1 | T001 基线复现 | 复现成功（形态 A：只读打开失败，`IOException`） | `docs/evidence/backtest-coexistence-20261027/baseline-repro.txt` |
| 1 | T002–T005 协议 | 14 项单测全绿 | `tests/test_backtest_window_protocol.py` |
| 1 | T006–T010 daemon 让路 | 5 项单测全绿（含"让路≠停止"三断言） | `tests/test_daemon_yield_boundaries.py` |
| 1 | T011 覆盖矩阵 | 已落盘，含 5 项退化影响面 | `docs/evidence/backtest-coexistence-20261027/yield-coverage.md` |
| 2 | T012–T017 接入与 GUI | 完成（含最小预检） | `run_ptrade_strategy.py` / `workers.py` / `backtest_tab.py` / `task_tab.py` |
| 2 | 回归首跑 | **16 失败**：`AttributeError: _yield_checked_at` | 见下节 |
| 2 | 回归二跑 | **5 失败**：fake `execute_task` 不接受 `cancel_check` | 见下节 |
| 2 | 回归三跑 | **全绿**（50 + 48 passed） | `test_daemon_lifecycle` / `hold_and_skip` / `gui_backtest_timer` / `data_source_priority` |

---

## 五、回归修复（第 2 轮 · 两处根因）

### R-1 `AttributeError: '_yield_checked_at'`

**现象**：既有 `test_daemon_lifecycle` / `test_daemon_hold_and_skip_weekdays` 共 16 项失败。
**根因**：既有测试用**绕过 `__init__`** 的方式构造 `DaemonLifecycle`（`__new__` / 手工装字段），我新增的缓存字段不存在。
**修复**：`_current_window()` / `_record_window_skip()` 改防御式惰性建字段（`getattr(...) is None` 时自愈），**不改既有测试**。
**理由**：测试构造方式反映的是既有契约（对象可能以非标准方式被创建），代码应容忍，而不是要求所有调用方补齐初始化。

### R-2 `TypeError: execute_task() got an unexpected keyword argument 'cancel_check'`

**现象**：修完 R-1 后仍有 5 项失败。
**根因**：既有 fake collector 的 `execute_task` 签名不接受 `cancel_check`。
**修复**：`_collector_cancel_check_supported(collector)` 用 `inspect.signature` 探测（含 `**kwargs` 判定）；不支持则**降级为不传**——即退化为"等任务边界"，仍是安全纯增益行为。
**注意**：真实 `ResidentCollector.execute_task`（`daemon.py:713-716`）有该参数，故真实路径总是走单元中断。

### 自检结果

```
git diff --stat（5 个既有文件）：234 insertions(+), 17 deletions(-)
他线 match_profile 改动：仍在（未被覆盖）
新增未跟踪：backtest_window.py / 2 个测试文件 / 复现脚本 / specs/ / 证据目录
```

---

## 六、MVP 完成度（Setup + Foundational + US1）

| 范围 | 状态 |
|---|---|
| T001–T011 Setup + Foundational | ✅ 完成（含覆盖矩阵） |
| T012–T017 US1 主路径 + GUI | ✅ 完成 |
| T018 US1 集成验证（场景 A/B） | ⏳ **待用户择窗**（需生产主库 + daemon 占用窗口，铁律 19） |
| US2–US5（T019–T037） | 未开始（等待批） |


---

## 七、口径变更（2026-10-27 深夜 · 用户裁定）

### 变更内容

| 项 | 原口径（已作废） | 新口径（当前） |
|---|---|---|
| 回测期间采集 | 让路但**进程存活**（只是不发起新轮） | **真停**：优雅停止并退出进程 |
| 回测结束后 | **自动恢复**（fail-safe：宁可恢复采集） | **保持停止**，由用户手动启动 |
| PyQt 按钮 | 始终「运行中」 | 回测后切「未开启」，提示「已因回测停止」 |
| 命令行 | 无需操作 | 需用户重新执行启动命令 |

用户原话要点：回测跑完自动继续拉取会造成资源浪费；何时恢复应由用户决定。

### 影响的制品（已同步）

- `spec.md`：US1/US4/US5、Edge Cases、FR-001..019、SC-001..013、Assumptions、Out of Scope —— 已按新口径重写。
- `plan.md`：核心设计整体重写（停止通道复用 `daemon_stop.request` + `reason`；不再有窗口/租约）。
- `tasks.md`：Phase 6 重写；顶部加口径变更说明。

### 代码改动（相对上一版）

| 文件 | 处置 |
|---|---|
| `daemon_lifecycle.py` | **回滚到 HEAD**（备份 `.specify/backup-daemon_lifecycle-pre-rollback-20261028.py`），随后仅加最小改动：`consume_stop_request_with_reason()` + 轮末 `stop_reason` / `yield_reason="backtest_stop"` |
| `backtest_window.py` | 重写为 `CollectorStopContext`（请求停止 → 等待退出 → 拿锁 → 回测 → 不重启 + 写 `backtest_stop.flag`） |
| `run_ptrade_strategy.py` | 接入新上下文；结束提示「采集已停止，请手动启动」；保留预检/`fail_closed`/结果非空校验 |
| `backtest_tab.py` | 开关文案改「回测前自动停采集，结束后需手动启动」；完成提示读 flag |
| `task_tab.py` | `_exit_status_text()` 区分「因回测停止」与「异常退出」 |

### 被删除的上一版机制（避免双轨）

- `backtest_window.request` 窗口请求 + 租约 TTL + `WindowObserver`
- daemon 侧窗口判定、`_yield_requested` 注入、`cancel_check` 注入（新口径下 `daemon_stop.request` 已足够）

### 验证

- 重写单测 29 passed（`test_backtest_window_protocol.py` / `test_daemon_yield_boundaries.py` / `test_backtest_empty_data_guard.py`）
- 既有回归 72 passed（`test_daemon_lifecycle` / `test_gui_backtest_timer` / `test_daemon_hold_and_skip_weekdays`）
- **未做真实启停集成**（需用户开采集窗口）：T018 / T023 / T028 / T033 仍挂"机会窗执行"

### 已知代价（用户已接受）

- 用户忘记重启 → 采集停摆；以显式状态提示缓解，系统不自动拉起。
- 每次回测需启停采集进程（握手/版本闸/锁）。


---

## 八、Converge 收口（2026-10-28）

### 首轮 converge 结果

`missing 0 / partial 2 / contradicts 0` —— 两条 partial 已追加为 T045 / T046。

### T045 GUI 超时三选项（FR-019 / SC-013）

- 位置：`backtest_tab._on_error` 先拦截 `CollectorStopTimeout` → `_handle_stop_timeout()`；
- 范围：**只挂在回测等待上下文**，不做全局拦截（CLI 仍按其自身退出码 4 + stderr 提示）；
- 映射（对齐 contracts/cli-gui-contract.md 退出码契约）：
  - 继续等待 = 重入等待（同参重新发起）
  - 强制终止 = `daemon_process.force_kill_daemon()` 成功后继续回测（仅限卡死兜底）
  - 取消 = 不启动回测，等价 CLI 130。

### T046 启动清留痕（FR-004 / FR-009）

- **汇聚点清理**：`DaemonLifecycle.run_forever()` 开头调用 `clear_stop_flag()`。
- 为什么在这里：GUI 按钮启动与 CLI 启动命令最终都进入 `run_forever`（daemon 子进程主循环），
  一处覆盖两条入口，避免"只清 GUI 路径、CLI 启动后界面仍误报"的漏洞。

### 复跑 converge

代码面缺口清零 → **Converged（代码面）· 待集成窗口**。

### 对账表结论（不升级 ⏳ 的理由）

`quality-reconciliation.md`：**15 ✅ / 7 ⏳ / 3 ➖**（未变）。

T045/T046 闭合的是**代码缺口**，不是**验证缺口**：7 项 ⏳ 全部依赖真实采集进程
（停得掉 / 不复活 / 读数真 / 强杀后可见 / 跨周期 / 任务类型实测 / SC 场景执行）。
把代码补齐当成证据升级，正是本项目明令禁止的"以完成声明替代证据"。


---

## 九、实施期发现的框架缺陷（2026-10-28 02:00 · 已修）

### F-1 `denied` 被当成「daemon 没在跑」→ 重复启动撞锁

**现象**（用户截图）：点「开启」→ 弹「常驻进程启动后立即退出。bootstrap 日志尾部：另一个 daemon 已在运行」。

**取证**（只读诊断，全程未动进程）：

| 证据 | 值 |
|---|---|
| `data/logs/gui_gate_diag.log` | `{"ts":"2026-10-28 01:41:56","verdict":"denied","status_raw":{"pid":36500,"status":"running"},"proc_pids":[]}` |
| `daemon_status.json` | pid=36500，create_time 与活进程差 **0.0s**，token=39f8920d… |
| `.daemon.lock` | 不可获取（确有活持有者） |
| 失败 bootstrap | `daemon_bootstrap_aa3f6622….log`（1:41:57） |
| 权限 | daemon 由 `scripts/start_daemon_guarded.ps1`（提权 PowerShell）启动；GUI 普通权限 → 读其 cmdline 得 `psutil.AccessDenied` |

**根因**：`verify_daemon_identity()` 返回 `denied`（无权读 ≠ 不存在），而
`is_daemon_running()` 只认 `("alive","unknown")` → 判 False → 放行启动；
第二道闸 `find_running_daemon_pids()` 遇 `AccessDenied` 直接 `continue` → `proc_pids: []`。
两源同时失效 → Popen 新实例 → 撞 `.daemon.lock` → 用户只看到「启动后立即退出」。

**修复**（`quantstudio/gui/daemon_process.py`）：
1. `is_daemon_running()`：`denied` 与 `unknown` 同族 → **按在运行对待**（查不到 ≠ 不存在）；
2. `find_running_daemon_pids()`：`AccessDenied` 时若 PID 与 `status.pid` 相同，返回
   `{"pid": …, "started_at": "unknown"}` 作为**第二源信号**（不再静默跳过）；
3. 新增 `tests/test_daemon_identity_denied.py`（6 项）；`test_daemon_gui_start_paths` 21 项仍绿。

**与本 feature 的关系**：`daemon_process.py` 本单从未改动，属既有缺陷；
但会让 GUI 无法正确显示采集状态，直接影响集成验证，故按「框架问题立即解决」当轮修复。

### F-1 附：守护脚本不自动重启（已核实，与本 feature 兼容）

`scripts/start_daemon_guarded.ps1` 的主体是 `while (-not $p.HasExited) { Start-Sleep 10 }`
+ 退出后写 ExitCode / 心跳，**没有任何重启逻辑**（"重启后对账"指下轮启动时补记 UNKNOWN）。
故「回测前真停采集 → 结束不自动恢复」不会被守护包装拉回。

### 遗留（不在本单范围）

daemon 以高完整性运行、GUI 以普通权限运行时，"强制终止"（taskkill）可能同样 `AccessDenied`；
正常「停止」走 `daemon_stop.request` 文件通道（daemon 自己退出），不受影响。
若后续需要 GUI 管理提权 daemon，应单独立项（提权/降权方案），不在本单夹带。


### F-2 停采集先于「策略文件校验」→ 低级错误白停采集（2026-10-28 02:2x，已修）

**现象**（用户终端实录）：`python -m ... 全球资产轮动.py ...` 运行后：

```
[coexist] 已等待 0s · 等待采集收尾（任务边界）
[coexist] 采集已停止（原因：回测）。需要时请手动启动采集。
FileNotFoundError: '鍏ㄧ悆璧勪骇杞姩.py'
```

采集被停掉，但回测在 `load_strategy` 阶段就失败——用户白停一次采集，且按新口径**不会自动恢复**。

**根因**：`CollectorStopContext.__enter__`（停采集）在 `_run_backtest_impl → load_strategy` 之前；
任何不依赖数据库的前置校验都发生在停止之后。

**修复**：新增 `_precheck_strategy_before_stop()`，在 coexistence 分支**构造停止上下文之前**校验
「文件存在 + `StrategyIsolationGuard.validate_path`（AST）」；不通过则**不停采集**直接报错，
并在错误里列出最近修改的 5 个策略文件、提示中文文件名编码问题。

**测试**：`test_missing_strategy_does_not_stop_collector`（断言 request_stop 调用次数 = 0）、
`test_existing_strategy_passes_precheck`（校验通过才停）。

### F-2 附：文件名乱码不是本仓库代码问题

实测本机：控制台 `chcp=936`，但 `python -c "print(sys.argv)" 全球资产轮动.py` 得到
`['全球资产轮动.py']`（正常），且 `quantstudio/backtest/strategies/全球资产轮动.py` 确实存在。
说明乱码发生在**那一次调用的参数传递链**（UTF-8 字节被按 GBK 解释），属环境/终端编码问题。
规避：用英文文件名，或先 `chcp 65001`；本仓库侧已通过 F-2 保证「即使传错也不会停采集」。


### F-3 两个回调混用 → 引擎首日即 TypeError（2026-10-28 02:3x，已修）

**现象**（用户实测）：回测跑到第一天即崩：

```
File "backtest_engine.py", line 554, in run
    self._progress_callback(i + 1, total_days, day_str)
TypeError: main.<locals>._cli_progress() takes 1 positional argument but 3 were given
```

**根因**：我把「等待采集停止的读数」与「引擎逐日进度」塞进了同一个 `progress_callback`：

- 引擎契约是**三参数** `(current, total, date_str)`（`backtest_engine.py:553-554`）；
- 等待读数是**单条字符串**。

CLI 传了单参数函数 → 引擎一调用就 TypeError。GUI 路径同款（`BacktestWorker._on_engine_progress` 是三参数，
等待读数一旦触发同样会崩），只是尚未跑到等待阶段。

**修复**：拆成两个通道。

| 回调 | 签名 | 用途 | CLI | GUI |
|---|---|---|---|---|
| `progress_callback` | `(current, total, date_str)` | 引擎逐日进度（既有） | 不传（引擎有 `if` 保护） | `_on_engine_progress` |
| `wait_callback` | `(msg: str)` | 等待采集停止的真实读数 | 打印到 stderr | `progress.emit`（`[coexist]` 前缀仍由 Tab 识别） |

**测试**：新增 `test_wait_callback_is_separate_from_progress`（断言读数只走 `wait_callback`、
`progress_callback` 不被单参数调用）；合计 40 passed。

**教训**：把"同一参数承载两种语义"当成省事，是典型的分叉陷阱——两个消费者签名不同，
就必须两个通道，哪怕看起来"都是进度"。


### F-6 采集轮持库时，前置检查抢在共存流程之前 exit 3（2026-10-28 09:36 实证 · 已修）

**现象**（T023 一键脚本首跑）：采集轮正在写库时发起回测，CLI 1.55s 退出码 3：
main() 的 _check_data_readiness（read_only 连接被 daemon 写锁拒绝）先于共存流程执行
→ 「免手动停采集」在**采集轮内**这个最需要它的时点反而失效（此前 02:58/03:03 成功
仅因 daemon 恰好空闲）。

**修复**（run_ptrade_strategy.py）：
1. 新增 _db_is_locked()：探测库是否被其他进程持有（中文/英文锁错误消息双匹配）；
2. main()：coexistence 且锁被持 ⇒ 跳过前置检查，放行进入共存流程（打印提示）；
3. run_backtest 的 ctx 内、_precheck_db_available 之后**复检数据就绪**——
   此刻无锁竞争，四表仍空/缺列即真不就绪 ⇒ raise（维持「空数据静默跑完 = 0」红线）。

**配套**（test_T023_T043/common.py）：db_fingerprint 在库被持锁时等待重试
（默认 180s，采集轮写锁期不再秒失败——T043 同因秒退已修）。

**测试**：既有 3 用例补 _check_data_readiness mock；新增
test_ctx_recheck_rejects_not_ready_after_stop（复检失败必须 raise）、
test_ctx_recheck_passes_when_ready（恰好复检一次）。合计 62 passed。


### F-7 观测记录（2026-10-28 09:56–10:07 · 非缺陷，参数特征）

**场景**：T023 一键脚本在采集轮（QFQ post-ingest 长轮）运行中发起回测。

**时间线（daemon.log 全程留痕）**：
- 09:56:00 回测发起 stop（reason=backtest）
- 09:56:07 **单元边界中断完美工作**（7 秒响应）：「任务边界(post_ingest_unit)收到停止请求，中断遍历；
  本轮已完成 unit=35、剩余 unit=25；水位按 hold_until_consistent 保持；已 committed 单元保留、
  未提交单元 trigger 保持 pending 下轮清障重算；QualityAudit 中止（已完成 0、跳过 29，记未完整审计）」
- 09:56:07 → 10:07:14 中断后收尾约 **11 分钟**（QFQ 清障/收尾，期间持库锁无增量日志）
- 10:07:14 干净退出（「已清理本进程 status 文件」）

**结论**：
1. 单元边界中断、水位安全保持、优雅退出链路**全部按 spec 兑现**；
2. 「停止耗时」= 中断响应(≈7s) + **收尾(≈11min，随轮内剩余工作量变化)** ——
   60s 默认停止窗口对长轮必然超时；CLI exit 4（停超时，提示加大 --coexistence-stop-timeout）
   与 GUI 三选项「继续等待」正是为此设计的正确路径，且已在真实场景各自兑现；
3. 回测端 60s 超时退出（exit 4）不视为缺陷；后续若需「长轮免手动停成功率」，
   合理配置为加大 stop timeout 或接受超时三选项交互。


### C1 · 超时/取消/强杀路径丢失「因回测停止」归因（2026-10-28 ZCode 终审阻断项 · 已修）

**缺陷**（backtest_window.py CollectorStopContext.__enter__）：wait_daemon_exit 超时抛
CollectorStopTimeout（或取消抛 WindowCancelled）时异常从 __enter__ 传出 ⇒ with 体未进入 ⇒
__exit__ 不执行 ⇒ flag 永不落盘；GUI 强杀后续跑重建 ctx 时无 daemon ⇒ stop_requested=False
⇒ 同样无 flag。后果：超时+取消、强杀+续跑两条路径下 GUI 只显示「未开启/异常退出」，
丢失 FR-009/FR-004 归因语义。

**修复**：
1. __enter__ wait 段 try/except (CollectorStopTimeout, WindowCancelled) —— write_stop_flag
   补写归因（注明停止请求已发出、采集将在任务边界退出）后 re-raise；
2. 新增 note_force_killed()：GUI 强杀成功后由 backtest_tab._handle_stop_timeout 调用补写；
3. 顺手修正 request_stop 过时注释（F-5 后 daemon_alive 对 AccessDenied 已按活着处理，
   二者差异改为「身份校验 vs 仅进程存在」的分工说明）——终审重点4 文档漂移项。

**测试**：tests/test_c1_stop_flag_attribution.py 五用例——超时写 flag / 取消写 flag /
note_force_killed 写 flag / GUI 强杀分支源码含调用点 / 正常路径 __exit__ 语义回归。


### 附 · VS Code「未知自动化拉起 daemon」悬案定谳（2026-10-28 09:1x 总调度问询）

- 当前 daemon 43336 父链实测：daemon ← start_daemon_guarded.ps1 wrapper(50696) ← VS Code
  集成终端(31724, shellIntegration.ps1, 10-26 起) ← Code.exe。所谓「AppData\Loc... 未知脚本」
  = VS Code 终端标配 shellIntegration.ps1，非自动化；拉起动作为**用户 03:19 手动执行**
  （START pid=43336 wrapper_pid=50696 与父链逐位吻合）。
- 14388 死于 03:15:56 = 我方 T023 自检回测 reason=backtest 停止（daemon.log 留痕）；
  「每 ~10 分钟影响」真实源 = 计划任务 QS_DaemonHeartbeat（只读巡检不拉起，10min 节拍）；
  真正自动拉起源 = QS_DaemonForever（每日 06:05，含 46612 撞锁 10 秒死案例，与 F-1 同族）；
  「41892 拉起 43840 / Agent-Quant 自行恢复」= trading 侧 AgentQuant-Harness/Loop 计划任务。
- ✅ 口径冲突已裁定已实施（2026-10-28 用户裁定：尊重手动恢复语义）：
  QS_DaemonForever 的启动入口 start_daemon_guarded.ps1 已加 flag-skip 守卫
  （backtest_stop.flag 存在且 reason=backtest 时跳过自动拉起，含 -Force 逃生门与 skip 日志，
  由 ZCode 实施并活体验证）。06:05/boot 触发不再违背手动恢复语义；
  「验证窗避开 06:05」注意事项随之失效（flag 机制自动保护）。
