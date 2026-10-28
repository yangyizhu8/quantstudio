# Tasks: 常驻采集与回测共存（回测优先 · 采集让路并自动恢复）

**Input**: 设计制品来自 `specs/001-daemon-backtest-coexistence/`（spec.md / plan.md / research.md / data-model.md / contracts/ / quickstart.md / checklists/quality.md）

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: 可并行（不同文件、无未完成依赖） | **[Story]**: 归属用户故事（US1–US5）
- 描述带确切文件路径；Setup / Foundational / Polish 阶段不标 Story。

---

> **口径变更（2026-10-27 用户裁定，优先于本文早前措辞）**：
> 回测前**停止**采集（真停、进程退出），回测结束后**不自动恢复**；PyQt 按钮切「未开启」、命令行需用户重新执行启动命令。
> 恢复时点由用户决定。原「回测后自动恢复（fail-safe）」语义作废；相关任务按 Phase 6 重写。

## Phase 1: Setup（共享基础设施与复现基线）

- [X] T001 写复现基线脚本 `scripts/repro_backtest_while_collecting.py`：开采集 → 发起回测 → 记录失败形态（锁冲突/空数据），输出到 `docs/evidence/backtest-coexistence-<日期>/baseline-repro.txt`（constitution 二：先能复现）
- [X] T002 [P] 建测试脚手架 `tests/test_backtest_window_protocol.py`（空壳）+ 临时 `data_root` fixture（monkeypatch `_data_root`）
- [X] T003 [P] 建协议模块骨架 `quantstudio/pipeline/backtest_window.py`：路径函数 `window_request_path()`、常量（`TTL_SEC=120`、`HEARTBEAT_SEC=10`、`BOUNDARY_TIMEOUT_SEC=30`）

## Phase 2: Foundational（阻塞性前置——完成前不得开始任何故事）

- [X] T004 实现 `quantstudio/pipeline/backtest_window.py`：`write_request()` / `read_request()` / `heartbeat()` / `clear_if_owned(request_id)` / `is_active()`（原子写 temp+os.replace；TTL 判定按 plan 5.3 的「心跳未变化持续时长」算法，不用绝对时间差）
- [X] T005 [P] 补 `tests/test_backtest_window_protocol.py`：写读往返、TTL 过期、token/request_id 不匹配不误删、原子写无半文件、时钟回拨场景
- [X] T006 在 `quantstudio/pipeline/daemon_lifecycle.py` 增加留痕写入辅助：`write_run_state(skip_reason="backtest_window")` / `yield_reason` / `yield_at` / `yield_boundary`（字段契约见 contracts/run-state-extensions.md；同窗口只写一次 skip）
- [X] T007 [P] 在 `quantstudio/pipeline/daemon.py` 的 `ResidentCollector` 增加 `self._yield_requested = False`，并在既有 4 处 `if not self._running:` 检查点（约 :2043 / :2302 / :2319 / :2530）旁追加 `or self._yield_requested`（不改 `_running` 语义）
- [X] T008 [P] 在 `quantstudio/pipeline/daemon_lifecycle.py` 的 tick 流程 `should_run` 判定后、`run_one_cycle` 调用前（约 :629）增加窗口判定：有效 ⇒ 跳过本轮 + 留痕 + `continue`
- [X] T009 在 `quantstudio/pipeline/daemon_lifecycle.py` 的 `run_one_cycle` 内实现分级让路：新增 `_check_yield_at_boundary(point)`，在 `pre_cycle` / `pre_task_*` / `post_task_*` / `pre_quality_audit` 调用；记录首次观察窗口的 tick，超过 `BOUNDARY_TIMEOUT_SEC` 则置 `collector._yield_requested=True`；命中后写 interrupted 语义（`yield_reason="backtest_yield"`），**不置** `stop_requested` 与 `_running=False`
- [X] T010 [P] 补 `tests/test_daemon_yield_boundaries.py`：fake collector + fake clock，验证轮前跳过、任务边界让路、超阈值升级、水位不推进、daemon 不退出
- [X] T011 [P] 覆盖表实测：逐采集任务类型确认中断响应（tushare 全市场 / per_stock / 分片 / QFQ post-ingest），结果写 `docs/evidence/backtest-coexistence-<日期>/yield-coverage.md`

**Checkpoint**: 协议模块可用、daemon 能以两级粒度让路且不退出——此时各 US 可并行推进。

## Phase 3: User Story 1 - 开着采集直接跑回测，跑完采集自己回来 (P1) 🎯 MVP

**Goal**: 采集开启时直接发起回测即可完成；回测结束采集自动恢复。
**Independent Test**: 场景 A + B（quickstart）——无需手动停采集，回测完成且采集自动恢复。

- [X] T012 [US1] 在 `quantstudio/backtest/run_ptrade_strategy.py` 的 `run_backtest` 增加 `coexistence=True` 与 `coexistence_boundary_timeout=30` 参数，接入窗口上下文管理器（写请求 → 等锁 → 预检 → 运行 → finally 清理）
- [X] T013 [US1] 在 `quantstudio/pipeline/backtest_window.py` 实现回测侧上下文管理器：写请求、心跳线程（10s）、`try_acquire` 轮询（0.5s）、`finally` 释放锁 + `clear_if_owned`
- [X] T014 [P] [US1] 补 `tests/test_backtest_window_protocol.py`：无 daemon 时立即回测、请求件生命周期、异常路径清理
- [X] T015 [US1] 在 `quantstudio/gui/workers.py` 的 `BacktestWorker` 透传 `coexistence` 参数（约 :419）
- [X] T016 [US1] 在 `quantstudio/gui/tabs/backtest_tab.py` 增加共存开关（复选框，默认勾选）并传入 params
- [X] T017 [US1] 在 `quantstudio/gui/tabs/task_tab.py` 采集状态区显示「因回测让路暂停」，与"采集故障"视觉区分
- [X] T018 [US1] 集成验证并落证据：quickstart 场景 A（基线不变）+ 场景 B（SC-001：**连续 20 次**发起，成功率 100%；自动恢复 ≤1 周期）

## Phase 4: User Story 2 - 撞上正在写库的采集轮：看得见的等待，随时能取消 (P1)

**Goal**: 等待真实读数 + 30s 后升级单元边界中断 + 可取消。
**Independent Test**: 场景 C、D——等待读数 1s 内出现并递增；取消后回测未开始、采集不受影响。

- [X] T019 [US2] 在 `quantstudio/pipeline/backtest_window.py` 实现等待循环：阶段枚举 `等待采集轮收尾` / `已请求中断当前任务` / `预检数据可用性`，每秒回调进度，支持取消谓词
- [X] T020 [US2] 在 `quantstudio/backtest/run_ptrade_strategy.py` 把等待回调接入 `progress_callback`（GUI 与 CLI 共用同一格式）
- [X] T021 [US2] 在 `quantstudio/gui/tabs/backtest_tab.py` 增加等待态显示（阶段 + 已等待秒数），并让 `stop_btn` 在等待态即可用（复用既有 `cancel()` → `BacktestCancelled`）
- [X] T022 [P] [US2] 补 `tests/test_backtest_window_protocol.py`：等待→取消、等待→升级→获取、读数递增
- [X] T023 [US2] 集成验证并落证据：场景 C（1s 内出读数、阈值后升级、水位未推进）+ 场景 D（取消 100% 不开始）

## Phase 5: User Story 3 - 拿不到数据访问权就明说，绝不空跑 (P1)

**Goal**: 入口预检 + 取数层 fail-closed + 结果证据校验。
**Independent Test**: 场景 F1/F2/F3——不可用即显式拒绝，合法零交易不被误判。

- [X] T024 [US3] 在 `quantstudio/backtest/run_ptrade_strategy.py` 实现入口预检：只读试连 + 试查一张元表，失败抛显式异常（含原因与建议动作），**不产出结果目录**
- [X] T025 [P] [US3] 在 `quantstudio/backtest/providers/duckdb_data_access.py` 增加 `fail_closed`（默认 False）与计数 `fetch_attempts/fetch_ok/fetch_empty/fetch_failed`；`_get_conn()` 失败时在 fail_closed 下抛 `DatabaseAccessError`（不改既有方法返回契约）
- [X] T026 [US3] 在 `run_backtest` 返回前校验取数证据（`fetch_failed>0` 或 `fetch_ok==0` 且 attempts>0 ⇒ 失败并清理输出；`fetch_ok>0` 且零交易 ⇒ 成功并附计数）
- [X] T027 [P] [US3] 补 `tests/test_backtest_empty_data_guard.py`：F1/F2/F3 三态、fail_closed 默认关闭时既有行为不变
- [X] T028 [US3] 集成验证并落证据：场景 F（SC-003：合计 **≥10 次**验证，含 1 个「合法零交易但取数成功」对照；拒绝率/误判率/产物目录检查）

## Phase 6: User Story 4 - 停止是明确的：不会悄悄停，也不会悄悄自己回来 (P1)

**Goal**: 停止原因可判别、跨界面进程可见、绝不自动拉起。
**Independent Test**: 场景 E1–E4（各 ≥3 次）+ 重启界面后状态仍正确。

- [X] T029 [US4] 停止原因贯通：`daemon_lifecycle.consume_stop_request_with_reason()` 返回 `reason`（缺省 `user`），轮末写 `run_state.stop_reason`；`backtest` 额外写 `yield_reason="backtest_stop"`
- [X] T030 [US4] 停止留痕件 `data/backtest_stop.flag`：回测结束（含异常）写入，下轮回测启动时清除；供界面/CLI 判别「因回测停止」
- [X] T031 [US4] GUI 文案区分：`task_tab._exit_status_text()` 读留痕 → 「⏸ 采集已因回测停止（不会自动重启，需要时请手动开启）」，否则保持「异常退出」
- [X] T032 [P] [US4] 单测：reason 默认 user / 不匹配不消费 / 轮末留痕 / 无停止时正常完成；上下文异常仍写 flag；无采集时不写假标记
- [X] T033 [US4] 集成验证并落证据：场景 E1–E4（成功/失败/取消/强杀后重启界面仍显示已停止，且采集未被拉起）

## Phase 7: User Story 5 - 命令行与图形界面同一套语义 (P2)

**Goal**: CLI 与 GUI 同版可用、语义对等、产物逐位一致。
**Independent Test**: 场景 H + I。

- [X] T034 [US5] 在 `quantstudio/backtest/run_ptrade_strategy.py` 的 `main()` 增加 `--no-coexistence` 与 `--coexistence-boundary-timeout`（语义见 contracts/cli-gui-contract.md）
- [X] T035 [P] [US5] CLI 等待读数输出到 stderr：`[coexist] waited=<秒> phase=<阶段>`
- [X] T036 [US5] 在 `quantstudio/gui/tabs/backtest_tab.py` 实现请求队列：上限 3，第 4 个立即拒绝并提示当前队列（单飞执行，排队项不写窗口请求，见 plan 5.7）
- [X] T037 [US5] 集成验证并落证据：场景 H（两端同参哈希一致）+ 场景 I（队列上限与拒绝提示）

## Phase 8: Polish（收尾与横切）

- [X] T038 文档同步：README 相关章节与 `docs/` 引用更新（采集与回测可共存、等待与恢复语义、回退开关）
- [X] T039 [P] 全量回归：`python -m pytest tests/ -q` 全绿 + 既有黄金结果零回退
- [X] T040 汇总证据落 `docs/evidence/backtest-coexistence-<日期>/`（场景 A–I 全量 + SC-001～SC-010 对照表）
- [X] T041 复核 quality 清单 25 项：逐条给出证据指针（勾选权在用户，agent 只给证据）
- [X] T042 [P] 场景 G 逐位一致验证（SC-004）：同策略同参，手动停采集 vs 共存窗两次回测，逐文件哈希对比，证据落 `docs/evidence/backtest-coexistence-<日期>/sc004-hash-compare.md`
- [X] T043 [P] 采集数据完整性验证（FR-018 / SC-011）：构造任务边界让路与任务内单元边界让路各 ≥1 次，重跑完成后与「无让路」对照数据哈希比对（零容差），证据落 `.../sc011-data-integrity.md`
- [X] T044 [P] 中断比值实测（SC-005）：记录「采集中断时长 ÷ 回测时长」，判定落在 0.9–1.1，证据落 `.../sc005-interrupt-ratio.md`

---

## Dependencies

- **T004–T011（Foundational）必须先完成**，否则任何 US 都无法验证。
- US1（T012–T018）是 MVP；US2 依赖 US1 的窗口上下文管理器（T013）。
- US3（T024–T028）与 US1/US2 并行安全（不同文件：预检与数据层）。
- US4（T029–T033）依赖 Foundational 的 daemon 侧改动（T006/T008/T009）。
- US5（T034–T037）依赖 US1（同一内核入口）与 US2（等待读数格式）。
- **T042–T044 是验收收口**（对应 SC-004 / FR-018 / SC-005），须在 T039 全量回归之前完成；三者写不同证据文件，可并行。

## Parallel Opportunities

- T002/T003 并行（脚手架与骨架）。
- T005/T007/T008/T010/T011 可并行（不同文件，Foundational 内）。
- T014/T015/T016/T017 并行（测试 + GUI 三处）。
- T025（数据层）与 T024/T026（入口）分文件，可并行。
- T038/T039 可并行。

## Implementation Strategy

**MVP = Setup + Foundational + US1**：先打通"开着采集直接回测并自动恢复"这一条主路径（T001–T018），即可交付最大价值；US2/US3/US4 是安全与诚信护栏，必须在对外交付前全部完成（US3、US4 属 P1，不可后置）；US5 在 P1 稳定后补齐 CLI 与队列。

**每阶段收口**：跑对应测试 + 场景验证 → 证据落盘 → 勾选任务 → 才进下一阶段。

**FR-013「同版交付」的任务顺序说明**：US1（T012）在 `run_backtest` 内以默认 `coexistence=True` 实现，CLI 天然获得共存语义；US5（T034–T037）补齐 CLI 显式开关与 GUI 队列。因此「同版可用」在 US1 完成时即成立，US5 只是补开关与队列体验——但**对外发布前 US5 必须完成**，否则 FR-013 的对等声明不成立。

## Phase 9: Convergence（converge 评估追加，2026-10-28）

- [X] T045 GUI 超时三选项：在 `quantstudio/gui/tabs/backtest_tab.py` 拦截 `CollectorStopTimeout`，弹「继续等待 / 强制终止 / 取消回测」——继续等待=重入停止上下文；强制终止=调 `daemon_process.force_kill_daemon()` 后继续回测；取消=等价 CLI 130（对应 FR-019 / SC-013，分型 partial）
- [X] T046 手动启动采集时清除停止留痕：`quantstudio/gui/tabs/task_tab.py` 启动成功后调 `backtest_window.clear_stop_flag()`，避免残留 flag 误导后续「因回测停止」提示（对应 FR-004 / FR-009，分型 partial）
