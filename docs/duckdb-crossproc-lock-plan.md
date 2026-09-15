# GUI×daemon DuckDB 锁冲突修复实施计划（A + D + A′）· v1.1

> **排期勘误（2026-09-16，总调度修正）**：dev EOD 进度检查点 = **9/16**；本方 A6 全量验收 = **9/17–18**。此前文档中"9/18 EOD / 9/19–20 验收"等表述为勘误前旧排期。基线轮实际执行于 **9/15 深夜–9/16 凌晨**，文件名与日期标注已同步更正为 0916（内容与数字**未变**，冻结口径不受影响）。

> 归属：策略研发专属会话｜状态：**已过审生效**（总调度 2026-09-16 审核结论：四条裁定全数吸收；T2 缺口实证闭合、`preserve_lock_file=True` 发现确认）
> 硬约束：所有复现/验收步骤**只读或影子库**，禁触生产主库写面

---

## 〇、审核裁定与前置实证

### 0.1 三项裁定（已并入正文）
| 裁定 | 结论 |
|---|---|
| 第七节落地方式 | **认可**——复用 CollectorRunLock/.daemon.lock 锁族、平台策略分立、既有 `daemon_lifecycle.py:361-363` unlink 隐患一并修；**注记**：锁文件生命周期策略属**行为变更**，须双平台测试路径覆盖 + 验收文档单列 |
| 重试预算 | **全窗统一 30s，不做时段特判**（时段特判=隐藏行为分支，违纯增益精神）；验收增项=06:00 轮启动延迟实测记录 |
| N=10 与 3×2 矩阵 | **照准**；前后双轮 + 每格**四项记录**（成功率/报错文本/归因命中/静默空数据检查） |

### 0.2 🔴 T2 前置实证：collector_run.lock 持有期（已完成）
**结论：空闲期释放 → D 设计成立，无缺口**（无需"触发文件 + 常驻下轮执行"通道）。

静态取证：
- `daemon.py:3454-3495`（forever）：仅 `acquire_instance_lock()`（持 **`.daemon.lock`**）+ `run_forever()`，**无** `with CollectorRunLock` 包裹整个循环。
- `daemon_lifecycle.py:417-418`：docstring "轻量调度循环：**持 .daemon.lock，无 collector 无 conn**"。
- `daemon_lifecycle.py:326-327`："空闲期不创建 ResidentCollector、不持有 DuckDB 连接；到执行点在 collector_run.lock 内**临时** from_configs + 跑轮次 + close"。
- `daemon_lifecycle.py:527`（`run_one_cycle`）：**每轮** `CollectorRunLock(timeout=5)`；`L530-532` 拿不到 → "本轮跳过"。
- `daemon_lifecycle.py:800-801`（健康检查）：`try_acquire()` 非阻塞探测。

动态实测（影子 `QUANTSTUDIO_DATA_ROOT`；锁路径随影子根独立）：

| 态 | 实测 | 含义 |
|---|---|---|
| 空闲期 | `try_acquire` = **True** | 委托的 once 子进程可立即执行 |
| 采集期（子进程持锁） | 非阻塞 = **False**；`timeout=2` → **Timeout (2.0s)** | 对应 once 子进程 `CollectorRunLock(timeout=30)`（`daemon.py:3366`）**30s 超时 exit 1** → T2 须处理的有界排队路径 |
| 强杀持有者后 | 0.8s 后 = **True** | **崩溃自愈成立**（进程终止即释放） |
| 正常释放后 | 锁文件 `exists` = **False** | **新发现** ↓ |

### 0.3 新技术落地点：`preserve_lock_file=True`
`filelock 3.32.0`：`WindowsFileLock._release()` 末尾 `if not self._preserve_lock_file: Path(lock_file).unlink()`；`FileLock.__init__` 签名含 `preserve_lock_file`（默认 **False**——现行代码每次释放都删锁文件，正是 POSIX inode 陷阱来源）；实测置 **True** → 释放后**锁文件保留**。
⇒ **第七节"锁文件永不删除"有官方原生实现，无需自创**。构造点清单（实施逐点收口）：
`daemon_lifecycle.py:296`/`310`/`346`、`gui/workers.py:118`/`264`、`daemon.py:1947`、`qfq_formal_cutover.py:199`、`qfq_orchestrator_cli.py:743`。**该改动属行为变更**（按裁定注记：双平台测试路径覆盖 + 验收文档单列）。

---

## 一、背景与动机

### 1.1 现象
PyQt GUI 在使用期间，常驻增量拉取进程因 DuckDB 跨进程锁冲突失败（"不修则 7×24 自动拉取等于摆设"）。

### 1.2 机制与量化（duckdb 1.5.5 / Windows）
DuckDB **跨进程独占**：库被任一进程打开时，其他进程**不能以读写模式打开**，**与只读无关**。

| 持有者 | 他人尝试 | 结果 |
|---|---|---|
| 只读 | 只读 | ✅ 允许 |
| **只读** | **读写** | ❌ 立即抛 IOException（非阻塞） |
| 读写 | 只读 / 读写 | ❌ / ❌ |

| GUI 侧形态 | 常驻进程 RW 打开失败率 |
|---|---|
| 空闲（未持连接） | **0.0%** |
| 每 1s 一次只读短查询（`db_helper` 形态） | **53.8%** |
| 持**只读**长连接 | **100.0%** |
| 持读写连接（`LockedTaskWorker`） | **100.0%** |

### 1.3 三冲突源与代码定位
| 源 | 描述 | 定位 |
|---|---|---|
| 源① | GUI 高频只读短查询 ↔ daemon RW 打开 | `gui/db_helper.py:37-58`、`L65-72`/`L74-80`/`L98-104` |
| 源② | GUI 进程内持 RW（手动拉取/全部执行）↔ daemon | `gui/workers.py:93-222`(`L131`)、`L262+`；`tabs/task_tab.py:651-704`、`L771-845` |
| 源③ | daemon 持 RW（采集期）↔ GUI 只读 | `daemon.py:224` → `writers.py:411/428/432` |

常驻侧：`writers.py:411 __init__` → `L428 _init_tables()` → `L432 _conn()` = `duckdb.connect(db)`（读写、**无重试**）→ 构造期即抛 → 任务失败。`daemon.py:185-207 close()` 仅保证 daemon 自身空闲期不持锁。

### 1.4 既有裁决修订案（本批随代码落）
`docs/duckdb-lock-timeout-design.md`：
- **L10/L15 修订**：原文"跨进程锁竞争（connect/execute **阻塞**）已被探针证伪"+据此删除 connect 层锁超时/重试 → **修订为**：当时**只证"阻塞不存在"**（正确），**遗漏"硬失败存在"**（RW 打开在他人持连接时 100% 失败，含只读持有者）→ "跨进程无竞争"前提不成立，**connect 层必须有退避重试**；原文删除重试的裁决**撤回**。
- **L43 门②复核**：原"探针示证**跨进程无冲突**"→ 复核为**同进程结论**，跨进程不成立；口径改写为"同进程形态不回归 + 跨进程由本批 A/D 处置"，附本次量化数字。

### 1.5 动机
① 7×24 拉取可用性前提被破坏；② 反向同样受损（daemon/worker 持 RW 期间 GUI 只读失败→界面空白）；③ 影响回测取数/审计/导出/MCP 替代与客户交付；④ 代价不对称。

---

## 二、范围与边界

### 2.1 范围内
| 项 | 内容 | 落点 | 承担 |
|---|---|---|---|
| **A** | daemon 写侧 RW 打开**退避重试**（1/2/4/8/15s，**全窗统一 30s**）；耗尽→可观测失败 + 持有者归因 | `pipeline/writers.py` | dev |
| **D** | GUI 写操作**委托单写者** + 复用既有锁族 + daemon 未运行→拉起 once 子进程 | `gui/workers.py`、`gui/daemon_process.py` | dev |
| **A′** | GUI 只读打开**快速重试 1–2 次** | `gui/db_helper.py` | 策略研发 |
| **源③** | 降级提示 + 预计等待 | `gui/db_helper.py` + `gui/tabs/*` | 策略研发 |

### 2.2 范围外（明确不做 + 理由）
- **C（GUI 读解耦）**：挂 MCP 二期；**E（QuestDB 迁移）**：备案。
- **数据语义类**：禁改 API 签名/返回结构/写入语义/水位推进/停止语义钩子；重试**只作用于"打开连接"**。
- **B（全量写互斥）**：裁定不做——源①/源③不由写者互斥覆盖（只读亦挡写者）；全量互斥引入 GUI 阻塞/死锁面且需穷举写入口（漏一即失效）；**后人复议须先提交新证据**。

### 2.3 硬约束
纯增益；既有测试/契约门全绿；策略源码零改动；不新增第三方依赖。

---

## 三、任务拆解

### 前置 P0：真实 GUI 基线轮（实际执行 9/15 深夜-9/16 凌晨）（**全轮影子化**，裁定①）
- 影子根：`<项目根>/agent_workspace/shadow_lockprobe/`；`QUANTSTUDIO_DATA_ROOT` **须进程启动前注入**（`_paths.py:49-50` 模块加载即解析一次）。
- 三方（GUI / daemon / 探测）**全部指向影子根**；任一落在生产即中止（校验点：三方打印 `db_path()` 与 `collector_run_lock_path()`）。
- 三态：空闲 / 浏览 / **手动拉取（影子库上真实执行小任务拉取）**。
- 降级预案（预裁）：真实 GUI 起不来 → 基线降级 holder 构造（仅参照），验收判据改用终轮绝对指标，前后对照缺席说明，**不阻塞验收**。
- 产出 `docs/evidence/gui-daemon-lock-baseline-20260916.md` → **产出即冻结为验收对照**（裁定②）。

### T1｜writers.py 重试层（A）— 0.75d（dev）
落点 `writers.py L430-432/_434-438/_411-428`；辅助 `_open_rw_with_backoff(path, seq=(1,2,4,8,15))` **仅包裹 `duckdb.connect(...)`**；成功路径与现状**逐位一致**；耗尽→抛带归因异常（db_path + **重试轨迹** + **持有者归因** + 原始异常）。
持有者归因：`psutil.Process.open_files()` 为**基准**（双平台）；WMI 仅 Windows **增强层**。
**与 E-3 关系（钉死）**：重试="等窗口放行"，**不构成"窗口已开"宣告**；E-3 门禁仍以"RW-open 试开成功"为唯一判据；重试层**不写窗口状态**；探测与重试共享 `collector_run.lock` 串行化。

### T2｜GUI 委托通道 + 锁链（D）— 1.5d（dev）
前置实证见 §0.2（空闲期释放 → D 成立）。现成通道 `daemon --mode once --task X --pull-mode Y --config-dir Z --quality-audit full`（`daemon.py:3337-3366`；含 `CollectorRunLock(timeout=30)`、主库强校验、`--runtime-manifest`、结构化结果）。
落点：`gui/daemon_process.py` 新增 `start_once_subprocess(...)`（复用 `L68` 模式）；`gui/workers.py` 两组 worker 改委托；**采集期排队路径**=30s 超时 exit 1 → GUI 提示"完成后可执行"+ 重试入口（不无限等）；锁构造点加 `preserve_lock_file=True`（§0.3）+ 修 `daemon_lifecycle.py:361-363` unlink 隐患。

### T3｜db_helper 只读快速重试（A′）— 0.25d（策略研发）
`_safe_query`（L37-58）加 1–2 次快速重试（~150–300ms）；`list_tables`/`table_rowcount`/`get_table_columns` 统一走它；**返回契约不变**。

### T4｜源③ 降级提示（0.5d，策略研发）
文案"数据库采集中（守护进程正在写入），预计 X 秒后可刷新"；时长**来源写明**=`daemon_schedule.check_interval_sec`（300s）+ daemon status；无值不编数字；`db_helper` 不改返回类型（新增只读属性）。

### T5｜文档修订（0.5d，dev；与代码同批）
`duckdb-lock-timeout-design.md`（§1.4 修订 + L43 门②复核）；README/`strategy_toolbox.md`/`prompt_engineering.md`；本计划落盘。

### T6｜测试（1d，dev；含平台维）
重试层/委托通道/锁生命周期（`preserve_lock_file` 前后 + **POSIX 删锁陷阱红态**）/锁残留自愈；平台参数化照 `tests/test_daemon_identity_macos.py` 先例（Windows 全跑 + POSIX `skipif`）。

### 3.1 共享核心文件纪律（两线各自全套）
stash create+store → `git diff` 自检 → **路径限定提交** `git commit -- <paths>` → 提交信息分层 → 批 5 前双远程 HEAD 逐位一致。

---

## 四、风险描述
1. **重试延迟**：+30s（全窗统一）；`daemon_schedule={06:00, 300s, 周日跳过}`；03:00=云同步（增量/gap 驱动）不受侵占；06:30 dev 写窗由 E-3 覆盖；**验收增项**=06:00 轮延迟实测。
2. **文件锁死锁/残留**：`daemon_lifecycle.py:361-363` unlink 隐患 → `preserve_lock_file=True` + 平台策略（POSIX 不删 / Windows 仅确认未持有时清理）；自愈实证成立（§0.2）；不静默清锁。
3. **排队 UX**：采集期委托 30s 超时 → 提示 + 重试入人口，不无限等。
4. **E-3 交互**：重试不得被解读为"窗口已开"；共享锁串行化；轨迹可审计。
5. **macOS 差异**：验收只 Windows E2E；POSIX 走单测 + 客户验证清单。
6. **纯增益破坏**：重试误包写入路径 / T4 改返回类型 → 三证 + diff 审拦截。
7. **掩盖真故障**：耗尽失败必须携带归因 + 轨迹 + 原始异常。

---

## 五、验收要点
**六条照录**：①GUI 持续操作 N 连续成功 100%；②daemon 采集期 GUI 只读可用或显式降级；③既有测试/契约门全绿零回归；④失败可观测不静默；⑤真实 GUI 前后双轮；⑥锁持有者可归因。
**细则**：N=10（两轮各一次）；**3×2 矩阵**（GUI 空闲/浏览/手动拉取 × daemon 空闲/采集中）每格**四项记录**；新增=06:00 轮延迟实测 + 锁生命周期行为变更单列；**全轮影子化**；产出 `docs/evidence/gui-daemon-lock-acceptance-<date>.md`。

---

## 六、质量判据
1. **纯增益三证**：API 契约零变化 diff 审 / 既有回归全绿（+ 契约门）/ 黄金场景对照。
2. **重试不掩盖真故障**：耗尽失败必须携带持有者信息 + 重试轨迹 + 原始异常文本；禁静默吞。
3. **提示不误导**：预计时长来源写明（`check_interval_sec` 实测 / daemon status），无值不编数字。
4. **文档与代码同批提交**；设计文档修订与本批同一 commit。

---

## 七、平台兼容性硬要求（逐条落地）
1. **锁禁自创**：复用 `CollectorRunLock`/`collector_run_lock_path()`（`daemon_lifecycle.py:285-316`）+ `.daemon.lock`（`L342-366`），均基于 `filelock`（3.32.0 实测在库，双平台实战）；不引入新库。
2. **POSIX flock 陷阱**：锁文件永不删除 → **全部构造点加 `preserve_lock_file=True`**（§0.3）；平台清理策略分立并测试；既有 unlink 隐患同批修；残留自愈两平台各写明。
3. **持有者归因基准 = psutil**（`Process.open_files()` 双平台）；WMI 仅增强层。
4. **测试矩阵加平台维**（Windows 全跑 + POSIX `skipif`；红态契约照 901300d 先例）。
5. **验收覆盖如实声明**：Windows=真实 GUI 双轮 E2E；macOS=单测 + 客户验证清单，**不宣称已 E2E**。
6. **回滚 fail-safe**：锁机制异常 → 退回无锁直连现状 + 显式告警，禁 fail-closed 卡死拉取。

---

## 八、执行流程与闸门
A1 落盘 → **A2 基线轮（实际 9/15 深夜-9/16 凌晨，全轮影子化）** → dev EOD 进度检查点 **9/16**（T1→T2→T5→T6）｜我方 T3/T4 并行 → **A6 全量验收 9/17–18**（N=10 双轮 + 3×2 矩阵 + 06:00 延迟实测 + 锁生命周期单列）→ 验收文档经总调度审 → 呈用户确认 → **批 5（9/20–21）**（代码+文档同批、双仓库推送、客户 macOS 验证清单随发布通知）。
全程只读/影子库；禁触生产主库写面；共享核心文件纪律全套；路径限定提交。
