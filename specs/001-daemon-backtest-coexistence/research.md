# Research: 常驻采集与回测共存

**Date**: 2026-10-27 | **Spec**: [./spec.md](./spec.md) | **Plan**: [./plan.md](./plan.md)

本文件消解 plan 的 Technical Context 中所有技术未知。每项含 Decision / Rationale / Alternatives considered。

---

## R1 协作件形态：文件请求 + 租约 TTL

**Decision**：新增 `data/backtest_window.request`（JSON，原子写），带 `heartbeat_at` + `ttl_sec`（默认 120s，心跳 10s）。

**Rationale**：
- 项目已有同型范式：`daemon_stop.request`（`daemon_stop_request_path` / `read_stop_request` / `consume_stop_request_if_matched`，`daemon_lifecycle.py:389-410`）；`qs_export` 亦照此三函数模式（`qs_export.py:17`）。沿用范式不引入新依赖、跨进程天然可用、可人工检视。
- 租约是 fail-safe 的关键：GUI 崩溃/回测进程被强杀时心跳停止，daemon 在 TTL 后自行恢复采集——满足 spec FR-008/009「宁可恢复采集」。

**Alternatives considered**：
- *内存/信号量/命名管道*：跨进程不可靠，Windows 下语义差异大；且 fail-safe 需要持久化证据。
- *SQLite 状态表*：引入额外写者与锁，与"不碰存储"约束冲突。
- *直接复用 `daemon_stop.request`*：语义是**停止 daemon**（`self._running=False` 后退出），无法表达"让路但继续存活"，会误杀采集线。

---

## R2 让路粒度：分级（任务边界 30s → 任务内单元边界）

**Decision**：先等任务边界；超过阈值（默认 30s，可配）请求任务内单元边界中断。

**Rationale**：
- 既有实现已有两级边界：`run_one_cycle` 在每任务前/后消费停止请求（`daemon_lifecycle.py:820-859`），且停止时置 `collector._running=False`，注释明确「per_stock/per_date 已支持 `_running` 检查」（`daemon_lifecycle.py:718-723`；检查点见 `daemon.py:2043/2302/2319/2530/3893`）。
- 只等任务边界时，若单任务本身极长，用户会被无限期阻塞（用户明确提出的场景）；只做单元中断则浪费已完成的增量。分级兼顾两者。

**Alternatives considered**：
- *只等任务边界*：长任务下不可接受。
- *直接打断任务内部*：无阈值时过于激进，会让短任务也被打断、采集碎片化。
- *固定等待上限后放弃回测*：把问题推回用户，违背"免手动停采集"。

---

## R3 互斥原语：复用 `collector_run.lock`，不新建锁

**Decision**：回测在窗口请求生效后 `try_acquire` 既有 `CollectorRunLock`（0 超时），拿到后**持有至回测结束**。

**Rationale**：
- `run_one_cycle` 已在 `CollectorRunLock(timeout=5)` 内执行（`daemon_lifecycle.py:691`），拿不到即"本轮跳过"（`:694-696`）。回测持锁即天然阻止 daemon 进入轮次。
- 不新建锁 = 不引入第二套互斥语义，避免双锁死锁；文件锁由 OS 在进程死亡时释放，崩溃安全。

**Alternatives considered**：
- *新建 backtest.lock 并由 daemon 双向检查*：两把锁需要固定获取顺序，易死锁，且与既有"拿不到即跳过"范式重复。
- *仅靠窗口请求不持锁*：存在竞态——daemon 恰在请求写入前进入轮次，回测侧无法确认"此刻可安全打开库"。

---

## R4 中断标志：新增 `_yield_requested`，不复用 `_running` / `stop.request`

**Decision**：`ResidentCollector` 新增 `_yield_requested` 属性；在既有 `if not self._running:` 检查点旁追加 `or self._yield_requested`。

**Rationale**：
- `_running=False` 的语义是**daemon 退出**（主循环 `while self._running`，`daemon.py:2434`；`_graceful_exit` 亦依赖它）。用它做让路会让 daemon 死掉，违背"回测后自动恢复"。
- 新标志与停止语义正交：让路只中断当前轮，daemon 继续 tick 并在窗口结束后恢复。

**Alternatives considered**：
- *复用 `stop.request`*：同上，会退出 daemon。
- *用 `should_stop` 回调注入*：`should_stop` 目前只覆盖质量审计与 qfq orchestrator（`daemon.py:2707`、`qfq_resident_orchestrator.py:1866`），未覆盖 per_stock/per_date 主路径；要覆盖仍需改同样 4 处，不如直接加标志清晰。

**风险与实测项**：4 处 `_running` 检查点是否覆盖全部任务类型（tushare 全市场按天、per_stock、分片、qfq post-ingest）需实测确认；未覆盖的路径退化为"等任务边界"，不会破坏正确性（只影响等待时长）。

---

## R5 fail-safe 兜底：租约 TTL + finally 双保险

**Decision**：正常路径 `finally` 必删窗口请求并释放锁；异常路径由租约 TTL 兜底。

**Rationale**：
- 回测跑在 **QThread**（`workers.py:386-454`），GUI 进程崩溃 = 回测一起消失，`finally` 不会执行 ⇒ 必须有不依赖进程存活的兜底。
- TTL 判定在 daemon 侧（采集方），符合"宁可恢复采集"的方向：最坏情况是回测窗被提前解除（回测侧随后预检失败并显式报错），而不是采集永久停摆。

**Alternatives considered**：
- *依赖 GUI 关闭时清理*：崩溃/强杀时无效（spec Edge Case 明确要求覆盖）。
- *用 Windows 作业对象/父进程监控*：平台耦合、复杂度高、且仍需处理"回测进程被单独 kill"。

---

## R6 空数据防线：入口预检 + 取数层 fail-closed + 结果证据

**Decision**：三层——①拿锁后只读试连 + 试查；②`DuckDBDataAccess._get_conn()` 增加 `fail_closed` 开关（默认 `False`，回测入口置 `True`），连接不可用即抛 `DatabaseAccessError`；③`run_backtest` 返回前校验取数证据。

**Rationale**：
- 现状：`_get_conn()` 失败只记首条 WARNING 并返回 `None`，各取数方法 `if conn is None` 直接返回 ⇒ 正是"空数据静默跑完"的成因（`duckdb_data_access.py:183-204`）。
- 默认关闭保证**纯增益**：既有调用方行为零变化；仅回测入口开启，失败即显式。
- 结果证据用于区分「取数成功但策略合法零交易」与「取数失败」（spec FR-011 明确要求）。

**Alternatives considered**：
- *只在入口预检*：回测中途失去访问仍会静默（预检通过后连接可能被外部抢占）。
- *直接把 `_get_conn` 改为始终抛错*：会改变既有所有调用方行为，违反纯增益。

---

## R7 双端入口：统一在 `run_backtest` 内

**Decision**：共存语义实现于 `run_backtest`（`run_ptrade_strategy.py:96`），GUI 与 CLI 均经它；新增 `coexistence=True` 参数。

**Rationale**：
- 该函数自述"CLI 和 GUI 共用的回测入口"（docstring），GUI 侧 `BacktestWorker` 与 CLI `main` 都已调用它 ⇒ 单一内核 + 双薄壳天然成立，对等性可机检（同参产物哈希）。

**Alternatives considered**：
- *新建 `run_backtest_coexist()` 包装*：两套入口易分叉，且 GUI 需改调用点两处；不如参数化。

---

## R8 请求队列：串行、上限 3、超出显式拒绝

**Decision**：GUI 侧维护待执行队列（上限 3）；超出即拒绝并提示当前队列与执行情况。CLI 为单次调用，天然不排队。

**Rationale**：用户已裁定（clarify Q2）。上限 3 防止误连点堆积，显式拒绝满足「不静默丢弃」。

**Alternatives considered**：合并同参请求（用户未选）、无上限排队（会掩盖误操作）。

---

## R9 等待读数：真实计时，不做预估

**Decision**：只显示"已等待 X 秒"与当前阶段（等任务收尾 / 已请求中断），不显示预计剩余时间。

**Rationale**：单轮/单任务耗时分布无数据（research.md 证据缺口），任何预估都是编造；spec FR-006 明确禁止。

**Alternatives considered**：基于历史均值的 ETA——无分布基础，且会误导用户。

---

## R10 明确排除的路线（记录理由，防止重复提议）

- **读副本/快照**：用户已定"不需要同时跑"；15GB 级复制成本与"回测看到哪份数据"的解释成本不值得。
- **单写者服务/客户端-服务器化**：改数据面，触碰共享核心，且分钟级百万次查询代理化后难以保证逐位一致。
- **升级 duckdb 1.5.3+/2.0（Quack）**：项目因 issue #23645 主动钉死 1.4.x，入口有版本闸；放开等于推翻既有裁定。
- **语句级让路**：破坏事务完整性。
