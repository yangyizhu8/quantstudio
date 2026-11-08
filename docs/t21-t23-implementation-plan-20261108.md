# T2.1 + T2.3 实施方案（②审三条件交付物）

> 2026-11-08 ｜ 节点⑳ ｜ 裁 B=甲照准，②审通过附三条件
> 对应：`docs/art-index-corruption-rootcause-20261108.md` §五 治本方案 T2.1 / T2.3

---

## 条件③：铁律 23「运行期间禁 CHECKPOINT」红线语境核对

### 3.1 红线原文（`knowledge/iron-rules/23-WAL残留处置.md`）

> **红线**：禁止运行期间 CHECKPOINT；禁止手工删除 `.wal`

**原文语境（同文件第 1 条）**：
> **触发条件**：采集进程被强杀/崩溃后，或 `data/quantstudio.db.wal` > 256MB 时——
> **先关 GUI 与 daemon**，再运行项目根 `check_wal.sh` / `check_wal.ps1`。

**⇒ 红线的原始语境是：运维人员用 `check_wal.ps1`（外部脚本、独立进程）对**
**「运行中的 daemon 正在写的库」执行 CHECKPOINT。**

### 3.2 T2.1 的形态（与红线语境的三点差异）

| 维度 | 铁律 23 红线语境 | T2.1 形态 | 是否同类 |
|---|---|---|---|
| **执行主体** | 外部运维脚本（`check_wal.ps1`，独立进程） | **daemon 自身**（同进程） | ❌ 不同 |
| **库状态** | daemon **正在运行/写库中** | **DaemonLifecycle 空闲期（不持有 collector、不持有连接）** | ❌ 不同 |
| **触发方式** | 人工运维（强杀后/WAL>256MB） | 引擎内部周期自检（每 30 分钟） | ❌ 不同 |

### 3.3 关键论据：`DaemonLifecycle` 空闲期确实不持有连接

`daemon_lifecycle.py:579` 原文：
> 「轻量调度循环：**持 .daemon.lock，无 collector 无 conn**。」

`run_one_cycle` 的职责边界（`daemon_lifecycle.py:697`）：
> 「整轮采集：collector_run.lock 内 from_configs + 遍历 + 审计 + **close**。」

**⇒ 采集轮次结束即 `close()`，空闲期无连接 ⇒ T2.1 在其间执行 CHECKPOINT 不构成「运行期间对活跃写库 CHECKPOINT」。**

### 3.4 另一个必须记录的发现：**铁律 23 的暴露面判据已被实测推翻**

铁律 23 第 4 条原文（2026-10-19）：
> 1.5.0–1.5.5 存在 ART index-delete 致命回归（`duckdb#23645`：1.5.x 由静默错删变为 FATAL，
> **1.5.6 未修复**）；**本项目对该缺陷暴露面为 0**（非唯一 ART = 0；41 个主键三臂验证不丢），
> 故 1.5.6 纳入放行集合、1.5.5 及以下不放行

**⇒ 两条已被 11-07 / 11-08 实测推翻**：

| 旧判断 | 实测 |
|---|---|
| 「本项目对该缺陷暴露面为 0」 | ❌ **两表中招**：`etf_daily`（11-07）、`stock_minutes`（11-08） |
| 「非唯一 ART = 0 ⇒ 无风险」 | ❌ **坏的是 PRIMARY KEY 的 ART**，与"非唯一"无关（issue #23645 明确此点） |

**⇒ 这说明：知识库早已记录该缺陷（#23645），但风险判据选错了维度（用"非唯一索引数量"当判据，而实际风险面是"PK 索引 + 高 unchanged 率的 upsert"）。**

**⇒ 建议（候裁）：铁律 23 的暴露面判据应改为**
> 「PK 索引 + 周期性 upsert 且大部分行 unchanged 的大表（`stock_minutes`/`etf_minutes`/`stock_daily`/`etf_daily`）
> 均属暴露面；1.5.6 未修复该缺陷 ⇒ 必须靠 T2.1/T2.3 + canary 缓解，不能以"非唯一 ART=0"豁免」

### 3.5 结论

| 问题 | 结论 |
|---|---|
| T2.1 是否与铁律 23 红线**实质冲突**？ | **不冲突**——红线约束的是「外部脚本对运行中库 CHECKPOINT」，T2.1 是「引擎自身在空闲期周期自检」 |
| 是否需要停呈用户裁？ | **不需要**（无实质冲突） |
| 但需不需要改铁律 23？ | **需要**——§3.4 的暴露面判据已被证伪，建议改判据（候裁，不擅自改） |

---

## 条件①：T2.3「任何进程打开库后先 checkpoint」入口清单（逐入口一行）

**T2.3 定义**：任何进程**打开主库后、开始业务前**，若 WAL > 0 则执行一次 `checkpoint_database()`。
目的：收干净上一次进程遗留的 pending WAL，避免 #23788 场景。

| # | 入口 | 文件/位置 | 是否加 | 理由 |
|---|---|---|---|---|
| 1 | **daemon forever**（主路径） | `daemon_lifecycle.py:609` 循环 | **T2.1 覆盖**（周期 + 启动首 tick） | 轻量调度，空闲无连接 |
| 2 | **daemon once** | `daemon.py:run_once` | **加**（启动时一次） | 之前强杀遗留的 WAL 在此收干净 |
| 3 | **GUI 启动** | `main_gui.py` 入口 | **加**（启动时一次） | 09-23 事故就是 GUI 打开 2.17GB WAL 卡死 22 分钟 |
| 4 | **回填工具**（`backfill_minutes_from_questdb.py`） | `build_stack()` 前 | **不加** | 它自己 `build_stack()` 会开写连接；加 checkpoint 反而多一次开库；且它**不应**在 daemon 运行时跑 |
| 5 | **对账工具**（`check_minutes_coverage.py`） | 只读打开 | **不加** | 只读，不收 WAL（铁律 23 已证：read_only 打开会回放但不写回） |
| 6 | **一次性运维脚本**（`scripts/*` 写库类） | 各自入口 | **不加** | 按铁律 23，运维脚本应走 `check_wal.ps1` 专用流程，不混入自动 checkpoint |
| 7 | **pytest / 测试** | conftest | **不加** | 测试用临时库，无遗留 WAL 问题 |

**⇒ 实际只加 3 个入口（#1 由 T2.1 覆盖、#2 once、#3 GUI）**，其余 4 个明确不加并写明理由（**禁盲加**）。

---

## 条件②：单测 + 重启验证作为进重建的前置门

| 门 | 内容 | 通过判据 |
|---|---|---|
| **门 1 单测** | ①间隔判定纯函数（`should_run_periodic_checkpoint`）②`wal=0` 时零开销返回 ③超时不阻塞 ④入口清单与实现一致 | pytest 全绿 |
| **门 2 重启验证** | 启动 daemon forever → 观察日志出现 `[T2.1] 周期 CHECKPOINT` 且 `wal X→0` | 日志实测出现 ≥2 次周期触发（缩短 interval 到 60s 验证） |

**⇒ 门 1+2 全过才进阶段 2（重建）。**

---

## 实施清单

| 项 | 文件 | 改动 |
|---|---|---|
| **T2.1 周期 CHECKPOINT** | `daemon_lifecycle.py` | 新增 `_CHECKPOINT_INTERVAL_S=1800`、`_should_periodic_checkpoint()`、`_run_periodic_checkpoint()`；循环内 L665 后插入调用 |
| **T2.1 启动首 tick** | 同上 | 首个 tick 强制触发（`_last_checkpoint_ts=0`） |
| **T2.3 once 入口** | `daemon.py:run_once` | 启动时调 `checkpoint_database()`（已 import） |
| **T2.3 GUI 入口** | `main_gui.py` | 启动时调 `checkpoint_database()`（带超时，不阻断窗口） |
| **配置项** | `collector_tasks.json` | `daemon_schedule.checkpoint_interval_sec`（默认 1800，可关=0） |
| **单测** | `tests/test_periodic_checkpoint.py` | 间隔判定 / 零开销 / 超时 / 入口清单 |

**回退开关**：`QS_PERIODIC_CHECKPOINT=0` ⇒ 完全关闭（逐位回退旧行为）。
