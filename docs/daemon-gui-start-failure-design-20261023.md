# 设计方案：GUI「进程常驻增量拉取」启动失败的诊断可读性与判据双源修复

- 立件：2026-10-23（客户运维线 N3）
- 状态：**②审通过**（总调度 2026-10-23 三裁：方案通过／FP-5 默认开一次照准／第三态文案照准）
- 类型：框架层（GUI 进程管理 + daemon 启动日志 + 状态判据）；**纯增益**，不触碰采集与数据口径
- 关联证据：`data/logs/daemon.log`（01:56:18 / 01:56:25）、`data/daemon_status.json`、两个 0B `daemon_bootstrap_*.log`

## 1 背景与动机

**现象**：已有 daemon 实例持锁时，GUI 点「进程常驻增量拉取：已停止（点击启动）」→ 报「启动失败：常驻进程启动后立即退出。bootstrap 日志尾部：」且**尾部为空**；按钮长期显示「已停止」，与实况（PID 53256，`status=running`）不符。

**影响**：
1. **可诊断性归零**——决定性的原因（`另一个 daemon 已在运行`）只进 `daemon.log`，而 GUI 只读 0B 的 `daemon_bootstrap_{token}.log`；
2. **误导性状态**——显示「已停止」诱导反复点启动，每次产生一个 0B bootstrap + 一次抢锁失败（本次已 2 次）；
3. **误判风险**——用户可能据此误判采集已停而手工启停/停服。

**必要性**：属 GUI/进程管理层的可诊断性与判据缺陷（非使用问题），按「框架问题立即解决」须闭环；修复不改变采集行为，可纯增益。

## 2 范围与边界

**改动文件（5）**：

| 文件 | 改动点 |
|---|---|
| `quantstudio/pipeline/daemon.py` | `acquire_instance_lock()` 失败分支：退出前向 stdout 补写一行致命原因（GUI 场景）；`isatty()` 为真时行为不变 |
| `quantstudio/gui/daemon_process.py` | `read_bootstrap_log_tail()` 增加回退链；新增 `find_running_daemon_pids()`（psutil 只读枚举，零锁副作用） |
| `quantstudio/gui/tabs/task_tab.py` | `_start_daemon` 前置判据单源→双源 + 第三态文案；`_toggle_daemon`/`_update_daemon_btn` 实时化；stopped 态低频探测 |
| `quantstudio/gui/main_window.py` | 第二入口 `toggle_daemon()`（L473）同源判据与文案同步（仅判据/文案，不改流程） |
| `tests/test_daemon_gui_start_paths.py`（新建） | A1–A15 三态矩阵 + 负向对照 |

**不改动（硬边界）**：`.daemon.lock` 语义；`daemon_status.json` 契约；daemon 生命周期/`run_forever`/采集/审计/水位线；FIX-5 初衷（bootstrap 不承载全量日志，只多一行致命原因）；任何策略文件；CLI 行为。

**兼容边界**：旧 GUI×新 daemon、新 GUI×旧 daemon 均可用（回退链对缺失文件容错）；`verify_daemon_identity` 五态语义不变（`unknown` 不得当死亡）。

## 3 功能点分解表（逐点门控）

| FP | 内容 | 验收判据 |
|---|---|---|
| **FP-1**（D-B） | `read_bootstrap_log_tail()` 回退链：①有内容⇒原样（语义不变）；②缺失或去空白后为空⇒读 `daemon.log` 尾 ≤200 行并前置标注 `[bootstrap 为空 ⇒ 以下为 daemon.log 尾部（可能含其他实例日志）]`；③皆无⇒明确文案（含两文件名），不抛 | **A1** bootstrap=0B ∧ daemon.log 含「另一个 daemon 已在运行」⇒ 返回值非空且含该串+标注行<br>**A2（负向）** bootstrap 有内容⇒不含标注行、不含 daemon.log 内容<br>**A3（边界）** 两文件皆无⇒返回含两文件名的提示，不抛异常 |
| **FP-2**（D-A） | 抢锁失败时先 `print(..., file=sys.stdout, flush=True)`：`[bootstrap] 启动失败：另一个 daemon 已在运行（token={token[:8]}，状态文件：data/daemon_status.json）`，再走原 `logger.error + sys.exit(1)` | **A4** GUI 场景（stdout 非 tty）⇒ bootstrap 非 0B 且含该串+token 前 8 位<br>**A5（负向）** CLI 场景（isatty=True）⇒ stdout 不出现该行<br>**A6（回归）** 抢锁成功路径 stdout 无新增输出 |
| **FP-3**（D-C） | `_start_daemon` 双源判据：①`is_daemon_running()`⇒原「已在运行」（不变）；②否⇒`find_running_daemon_pids()`（cmdline 含 `quantstudio.pipeline.daemon` 且非自身）命中⇒第三态文案（PID/启动时间/「状态文件不可读，请按刷新或先停止」）且**不启动**；③双源皆空⇒启动 | **A7** status alive⇒「已在运行」且 `start_daemon_subprocess` 调用数=0<br>**A8** status 缺失∧进程存在⇒第三态文案（含 PID）且未启动<br>**A9** status 缺失∧无进程⇒正常启动（调用数=1）<br>**A10（负向）** 判据 False∧真实撞锁⇒经 FP-1 回退后用户可见「另一个 daemon 已在运行」，不再「立即退出+空尾巴」 |
| **FP-4**（R-1） | 点击/绘制前 `_sync_daemon_state()` 实时化；`_on_daemon_poll` 在 stopped 态 5s 低频探测（仅读 status/进程枚举），running 态维持原 1s | **A11** 外部启动后 ≤5s 按钮自动变「🟢 运行中」<br>**A12（负向）** 停止后 ≤5s 变「🔴 已停止」<br>**A13（性能）** stopped 态 60s 内 CPU 增量 <0.5%（实测记录） |
| **FP-5**（R-2） | 判定为 False 的分支写一条诊断到 `daemon.log`：`[GUI] 前置判据诊断 status=… verdict=… pids=…`；env `QS_GUI_DAEMON_DIAG`（**默认开一次**，R-2 定谳后转默认关） | **A14** 触发⇒daemon.log 出现含 `verdict=` 的诊断行<br>**A15** env=0⇒不写 |

**顺序**：FP-1 → FP-2 → FP-3 → FP-4 → FP-5；每点过判据才进下点，任一点红即停在该点。

## 4 风险与规避

| 风险 | 等级 | 规避 |
|---|---|---|
| 回退读 daemon.log 暴露他实例日志 | 中 | 显式标注 + 仅尾部 ≤200 行；FP-2 落地后正常场景不再走回退 |
| 试探 `.daemon.lock` 干扰在启实例 | 高（已规避） | **不试探锁**，改 psutil 进程枚举（只读、零锁副作用） |
| print 与日志交错 | 低 | 单行 + flush + 固定前缀 `[bootstrap]` |
| 低频轮询开销 | 低 | 仅 stopped 态 5s，A13 实测设门 |
| 第二入口漏改致判据不一致 | 中 | `main_window.toggle_daemon`（L473）纳入同改；A7/A8 两入口各跑一次 |
| 旧 GUI×新 daemon | 低 | FP-2 仅多一行 stdout，旧 GUI 忽略无影响 |
| 误把「在跑但状态坏」当正常启动 | 中 | 双源任一命中即不启动（对"启动"侧 fail-closed） |

## 5 验收要点

- **功能**：A1–A15 全绿 + `pytest tests/ -k "daemon or gui"` 全绿；
- **场景（三态矩阵，受控窗内真实进程演练）**：①无 daemon⇒启动成功、按钮变运行中；②有 daemon（status 正常）⇒「已在运行」、无新子进程/无新 0B bootstrap；③有 daemon（status 移走/损坏）⇒第三态文案（含 PID）、无新子进程；status 复原后 ≤5s 自愈；
- **边界**：两日志皆缺；status JSON 半损坏；`.daemon.lock` 被无 status 实例持有；连点 5 次⇒不再累积 0B bootstrap。

## 6 质量判据

1. 问题彻底修复：A10 负向对照下「启动失败+空尾巴」不可再现，弹窗必含可行动原因；
2. 无回归：既有 tests 全绿；启动/停止/握手/优雅停四路径行为不变（A6+场景 1/2）；
3. 零行为漂移：采集与写入路径 `git diff` 零改动；
4. 工程标准：逐点门控留痕；负向对照齐（A2/A5/A10/A12/A15）；证据落 `docs/evidence/`；
5. **三型判定**：FP-1/2/3/5 = **新增检测型**（只增可见信息与拦截，不改既有结果）；FP-4 = **纯恢复型**（UI 状态回到"反映实况"的原意图）；**无数据修正型**。

## 7 回退条件

- 任一 FP 使既有 GUI/daemon 用例转红 ⇒ 停在该点并回退该点；
- FP-4 引入 CPU 增量超 A13 门 ⇒ 回退 FP-4（保留其余）；
- FP-3 出现「误拦正常启动」（A9 红）⇒ 立即回退 FP-3，仅保留 FP-1/2；
- 回退手段：逐文件 `cp` 还原 + `git diff` 自检（遵守写前快照纪律），不做批量 git 操作。

## 8 知识反哺检索留痕

```
知识反哺：[无命中] 12-客户运维/已知错误·daemon 启动失败（检索域：客户运维案例、daemon 生命周期、Windows 计划任务），2026-10-23
知识反哺：[近似非本案] 客户运维案例·台账（project/9efb8ee9，2026-10-23）——分钟数据跑 2 天案，根因 D1 fail-fast/D2 隔离区，与本案无重叠
知识反哺：[近似非本案] Windows 计划任务 ExecutionTimeLimit（global/9038d80a，2026-10-23）——进程树被杀形态，本案为锁互斥，不套用
```

## 9 ②审三裁记录（总调度 2026-10-23）

1. 方案通过，落档 → 审计 → FP-1→5 逐点门控实施；
2. FP-5 **默认开一次**照准（R-2 定谳需现场值，得出后转默认关）；
3. **第三态文案照准**：「检测到常驻进程正在运行（PID xxx，启动于 yyyy-mm-dd hh:mm），但状态文件不可读；请点『刷新』同步显示，或先『停止』再启动」；
4. A1–A15 + 三态矩阵验收照案。

## 10 实施前核对结论（2026-10-23 03:0x）

- **第二入口存在**：`quantstudio/gui/main_window.py:473 def toggle_daemon(self, start: bool, on_done=None)` ⇒ 纳入同改；
- GUI 框架 = **PyQt6**（`task_tab.py:9-12`）；
- `read_daemon_status()`（`daemon_lifecycle.py:144-151`）缺失/损坏**均返 None** ⇒ FP-5 诊断需额外记录"文件是否存在/解析是否失败"以区分二者；
- 既有回归面：`test_daemon_identity_macos.py`、`test_daemon_lifecycle.py`、`test_daemon_once_exit_contract.py`、`test_gui_task_stop.py` 等 18 个 daemon/gui 测试件。
