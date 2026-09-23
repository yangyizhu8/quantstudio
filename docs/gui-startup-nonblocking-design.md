# GUI 启动卡死修复设计（非阻塞启动 + WAL 收敛）— 六步第 1 步

- 状态：**方案（待审计）**｜日期：2026-09-23｜归属：客户运维会话
- 案件：`python .\main_gui.py` 启动后窗口永不出现（进程 CPU 94.7%、RSS 3.46 GB、50 线程）
- 关联证据：`docs/evidence/gui-startup-wal-checkpoint-baseline-20260923.md`
- 审核前置：ZCode 2026-09-23 归因通过（代码级独立复核四项：调用链逐帧一致 / `_safe_query` 只覆盖快速失败 / 全仓主库无 CHECKPOINT / pyproject 双处钉版属实）

---

## 1. 问题定义（实测基线）

| 项 | 实测值 |
|---|---|
| 修复前冷启动 | **>15 分钟窗口未出现**（进程仍在 WAL 回放，人工终止） |
| 主库 / WAL | 38.08 GB / **2.17 GB（未检查点）** |
| `duckdb.connect()` 打开耗时（=WAL 回放） | **1338.4 s ≈ 22.3 分钟**（单核 ~94%） |
| `CHECKPOINT` 本体耗时 | **7.3 s**（2.33 GB WAL → 0） |
| 一次性维护后冷启动 | **12.4 s**（窗口标题出现为锚；RSS 437 MB） |

**调用链（逐帧，审核已独立复核）**：
```
main_gui.py:33 → gui/main_window.py:96 __init__ → :146 _setup_navigation → :159 _create_tab
→ gui/tabs/task_tab.py:71 __init__ → :209 _load_tasks → :213 _render_tasks
→ gui/db_helper.py:186 get_watermarks → :152 query_duckdb → :99 _safe_query
→ duckdb.connect(read_only=True)      ← 阻塞点（构造期同步执行）
```

## 2. 根因定谳（三层，均有证据）

### R1 WAL 未被收敛（增长与残留机制）
- **硬杀无干净关闭史**：`data/logs/daemon.log` 末行 **2026-09-23 15:54:53**（连续 `stock_minutes` 5 万行 upsert），与 `quantstudio.db.wal` mtime **15:54:46** 吻合；该日志内 `graceful shutdown` / `已清理 status 文件` / `CHECKPOINT` / `Traceback` **关键词 0 命中** → 进程**未走优雅收尾**即消失（硬杀/异常终止）。
- **非人为关闭检查点**：全仓 grep **无** `wal_autocheckpoint` / `disable_checkpoint_on_shutdown` / `checkpoint_threshold` 覆盖（排除「阈值被调大」这一成因）。
- **官方文档口径**（Context7 `/websites/duckdb_current`，crash guide）：崩溃后「start a new DuckDB session … **will automatically replay the WAL and perform a checkpoint**」→ 即**残留 WAL 由下一次会话承担回放 + 检查点**；若下一次会话是**只读**，则只回放、不收敛（见 R2 实证）。
- **候选成因仍未定谳项（禁止未证实归因）**：单一巨事务、长持有连接阻塞检查点、混版写入（见 §5）。**取证计划见 §7**。

### R2 只读连接无法收敛 WAL（本次副本实验实证）
副本实验（`agent_workspace/wal_semantics_probe.py`，临时库，未触生产）：

| 步骤 | 结果 |
|---|---|
| 关闭自动/关闭时检查点 + 硬杀（`os._exit`） | 留下 `exp.db.wal` |
| **`read_only=True` 打开**（读到 2,000,000 行） | **WAL 未收敛**（0.01 → 0.01 MB） |
| **`read_write` 打开** | **WAL 收敛**（0.01 → 0.00，文件删除） |

⇒ **GUI 只读路径永远无法自愈**：每打开一次就要回放一次（生产规模 = 22 分钟/次），WAL 不会消失。**收敛责任只能落在写者（daemon / 维护脚本）**。

### R3 启动期同步打开 + 防御不覆盖「慢的成功」
- GUI 在 `MainWindow` 构造期同步执行首次 DB 读取 → 阻塞即无窗口、无报错（用户视角「起不来」）。
- `_safe_query` 的防御（`READ_ONLY_RETRY_ATTEMPTS=2` / `0.2 s`）只覆盖 **IOException(busy) 快速失败**；WAL 回放是**「慢的成功」**——不抛异常、不返回 → 重试与降级**永不触发**（审核确认缺口）。
- 附带证据：本机 duckdb **1.4.5** 下 `connect()` 在冲突/回放下表现为**长时间阻塞**（而非快速失败），与既有防御假设（1.5.5 快速 IOException）不一致。

## 3. 改动范围（文件面）

| # | 落点 | 改动 | 对应根因 |
|---|---|---|---|
| ① | `main_gui.py` / `gui/main_window.py` | 首个 DB 读取**移出构造期**：窗口先 `show()`，读取经 `QTimer.singleShot(0, …)` 或 worker 线程执行 | R3 |
| ② | `gui/db_helper.py` | `_safe_query` 增加**超时 + 显式降级**：连接/查询在工作线程内带 deadline（默认 ~5 s，可配），超时即返回空 DataFrame + `busy_hint()` 文案（「数据库正在恢复/采集中，请稍后刷新」）；**降级后必须给出路（审核修订①）**：自动重试（每 30 s，退避与上限可配）**＋** 界面「手动刷新」入口——两者都要，避免用户停在空表无动作；异常分类不变（真故障仍上抛） | R3 |
| ③ | `pipeline/daemon_lifecycle.py`（收尾处） | **轮次收尾/退出前 CHECKPOINT**（含优雅退出路径与正常轮次边界） | R1 |
| ④ | `scripts/`（新增巡检；**运行宿主与告警去向见 §4.5**） | **WAL 体积巡检 + 阈值告警**：`wal > 阈值`（建议 256 MB）即告警；**执行前置条件（审核修订③，关键）**：必须先确认 **daemon 不在运行**（status 文件 + 进程探测）且 `RW open` **自带超时放弃**——否则巡检自身会因排他锁长时间阻塞，变成新的卡死进程 | R1 |
| ⑤ | 文档 | README + `docs/strategy_toolbox.md` + `docs/prompt_engineering.md` 涉及 GUI/daemon 运维表述同步（若涉） | 铁律 |
| ⑥ | **启动版本闸（三入口）**：`main_gui.py`、`quantstudio/pipeline/daemon.py`（`-m` 与**直启** `python daemon.py …` 两形态）、`scripts/activate_venv.bat` / GUI 拉起路径 | 启动即校验 `duckdb.__version__` 属 **1.4.x**；非 1.4.x **拒启**并打印修复指引（钉版依据：`pyproject` 双处 `duckdb>=1.4.5,<1.5`）；**必须覆盖 wrapper / 直启 / GUI 拉起三入口**（09-23 定谳的「直启」路径在内）。**主 venv `_runtime\venv_quant_studio`（duckdb 1.5.4）迁移路径**：①（推荐）将该 venv 降到 1.4.5，与钉版一致；② 临时改由已合规解释器（`Python311` 或 `python3.12.9`，均 1.4.5）承担 GUI/daemon，并在 `activate_venv.bat` 注释标注；闸门落地后该 venv 未迁移将被**拒启**（预期行为，禁止静默绕过） | 裁定① |

> **实施期更正（2026-09-23，笔5 实测）**：「直启文件形态」（`python quantstudio/pipeline/daemon.py`）
> 在本代码库**结构上不被支持**——该模块使用相对导入（`from .task_resume import …`），无包上下文必然
> `ImportError: attempted relative import with no known parent package`（补 sys.path 引导亦无效，已回退）。
> 故闸门**实际覆盖的受支持入口**为：① `main_gui.py`（GUI）② `-m quantstudio.pipeline.daemon`
> ③ GUI 拉起的 daemon 子进程（继承解释器且子进程自身过闸；等价形态
> `python -c "from quantstudio.pipeline.daemon import main; main()"`）④ `scripts/activate_venv.bat`。
> 另：`daemon_status.json.cmdline` 在 `-m` 下与直启同形，**不能据此判断启动形态**（A 案卷宗已更正）。

**不做**：不改任何数据语义、表结构、水位、复权、回测行为；不改 `_safe_query` 既有返回契约（空 DataFrame 降级语义保留）；不用 1.5.x 打开生产库。

## 4. 必答项（审计要求）

### 4.1 锁语义清单（DuckDB 单文件库）
| 场景 | 行为 | 依据 |
|---|---|---|
| 多进程**只读**共享同一库文件 | **允许**（官方推荐用于「multiple processes must access the same database file simultaneously」） | 官方文档（R/PHP/CLI 客户端文档一致） |
| 任一进程持**读写**（排他） | 其他进程打开被拒或阻塞（本机 1.4.5 实测为**长时间阻塞**；1.5.x 口径为快速 IOException） | 本机实测 + 项目既有 A/T 系列证据 |
| 只读打开且库有残留 WAL | **会回放**（能读到数据），但**不收敛 WAL** | 本方案 §2 R2 副本实验 |

### 4.2 `read_only=True` 在有 WAL 时的行为
- **每次打开都要回放**（生产实测 22.3 分钟/次；副本实验同语义）；
- 回放**不写回主库**（只读无写权限）→ **WAL 永不消失**；
- ⇒ **结论**：GUI 侧只读连接**永远无法收敛 WAL**，只能由写者检查点（本方案 ③/④）。

### 4.3 `CHECKPOINT` 被并发连接阻塞时的行为与失败路径
- 官方文档：`checkpoint(database)` = 「Synchronize WAL with file **without interrupting transactions**」；
  `force_checkpoint(database)` = 「…**interrupting transactions**」。
- ⇒ 普通 `CHECKPOINT` **不打断事务**（遇并发事务会等待/配合），**FORCE CHECKPOINT** 才打断；
- **失败路径**：若他进程持排他锁（daemon 采集中），`CHECKPOINT` 无法执行 → **必须在库空闲窗口执行**（这正是 §5 候选 B 的「空闲时检查点」设计依据）；执行失败须显式记录并下次重试，不得静默。
- **失败路径补充（审核修订③，关键）**：巡检/维护脚本尝试 `CHECKPOINT` 前**必须先确认 daemon 不在运行**（`daemon_status.json` 状态 + 进程探测），且 `RW open` **自带超时放弃**（超时即退出并记录，不得无限等待）——本机 1.4.5 下 `RW open` 遇排他锁为**长时间阻塞**（§4.1 实测），若不做此约束，**巡检自己就会变成新的卡死进程**（09-23 案即此形态）。

### 4.4 连接级隔离约定（裁定①，约定文本）

| 侧 | 约定 |
|---|---|
| QuantStudio（写侧） | 版本闸（⑥）保证**只有 1.4.x** 能启动 GUI/daemon；daemon 为**唯一写者**，轮次收尾检查点（③） |
| trading 侧（读侧） | `venv_miniQMT` **保持 1.5.3 不动**；其 duckdb 使用**限定为 read_only 消费**（`OSkhQuant` 子系统），**不得对主库执行任何写操作** |
| 兼容性依据 | 1.5.3 读 1.4.5 写的库 = **向后兼容成立**（本机实证：1.5.3 曾读主库；反向亦成立——今日 CHECKPOINT 与 GUI 均在 1.4.5 下成功读/写同一主库） |
| **附注（今日 GUI 案教训，必须遵守）** | **read_only 读者打开时会回放 WAL**（§4.2 副本实验实证）→ OSkhQuant 读主库应**避开 daemon 写入高峰**（建议：避开 daemon 轮次执行窗，或改读副本），避免读者在回放/锁等待上长时间阻塞 |

### 4.5 巡检运行宿主与告警去向（审核修订②，否则 V4 无法验收）

| 项 | 设计 |
|---|---|
| 运行宿主 | ① **首选**：`daemon_lifecycle` 空闲期附带执行（daemon 自身知晓「不在采集中」，天然满足 §4.3 前置条件）；② 备选：独立脚本 `scripts/wal_health_check.py` + Windows 计划任务（每 30 min） |
| 周期 | daemon 空闲期每轮一次；计划任务形态每 30 min |
| 前置检查 | daemon 运行探测（status 文件 + 进程）＋ `RW open` 超时放弃（§4.3） |
| 告警去向 | ① 结构化日志（`data/logs/wal_health.log`，含体积/路径/阈值/是否已检查点）；② GUI 顶部横幅（复用 `busy_hint()` 通道）；③ 与既有告警通道一致（若项目已接飞书/邮件则同通道，落地时按现有实现对齐） |
| 告警判据 | `wal > 阈值(默认 256 MB)`；连续 2 次命中才告警（防抖）；检查点成功/失败均记录结果 |

## 5. ③ 形态比选与成本核算（审核要求）

| 候选 | 内容 | 成本（实测） | 评价 |
|---|---|---|---|
| **A：轮次收尾 CHECKPOINT** | daemon 每轮结束/优雅退出前执行 `CHECKPOINT` | **7.3 s / 2.33 GB**（≈秒级） | **主选**：成本远低于「留 WAL」的代价（下次任何打开 22 分钟） |
| **B：WAL 超阈值 + 空闲时检查点 + 体积巡检告警** | 巡检 `wal` 体积；超阈值（建议 256 MB）告警；库空闲时执行检查点 | 巡检≈零成本；检查点同上 | **兜底 + 防复发监控**（覆盖硬杀场景：硬杀后无人收尾，B 仍能收敛并告警） |
| 仅 B 不 A | 依赖巡检周期 | 硬杀后最长一个巡检周期内仍留大 WAL | 不足 |
| 仅 A 不 B | 硬杀后无收尾 | 硬杀即留 WAL 且**无告警** | 不足 |

**结论：A + B 组合**（A 为主路径，B 覆盖硬杀并承担监控告警）。

## 6. 验收标准（预钉，方案内即含）

| # | 判据 |
|---|---|
| V1a | **构造大 WAL 场景**（副本库：关闭检查点 + 硬杀制造 ≥100 MB WAL）下，修复后 **窗口可交互时间 ≤ 5 s**（对比修复前 22 分钟级；异步化后窗口先显示，不等首查） |
| V1b | 同场景下 **首查完成时间**单独记录并验收（正常库基线 = 12.4 s 冷启动；异步化后首查完成**不应显著劣化**，阈值待实测后钉；与 V1a 分列，不得合并成一个数） |
| V2 | `_safe_query` **超时降级 + 降级后自动恢复**：人为制造慢打开 → deadline 到即返回空 DataFrame + `busy_hint()` 文案出现；**随后 ≤30 s 内自动重试成功并回填数据**（或手动刷新立即生效）；**数据不损坏**（校验行数/抽样哈希不变） |
| V3 | **daemon 收尾后 WAL 体积上限**：正常轮次结束 / 优雅退出后 `wal == 0`（或 ≤ 阈值，取严者） |
| V4 | **WAL 巡检告警**：人为把 WAL 造到阈值以上 → 告警出现（含体积与路径） |
| V5 | GUI 既有功能回归全绿（GUI 相关测试套件 + 手工冒烟：任务页/水印/浏览）+ **黄金结果对比**（既有基线策略回测逐项一致） |
| V6 | 证据入 `docs/evidence/`（含 V1a/V1b 计时、V2 文案与数据校验、V3 体积、V4 告警） |
| V7 | **版本闸闭环（审核修订⑤）**：以 **`venv_miniQMT`（duckdb 1.5.3）实际触发** GUI 与 daemon 启动 → 断言**拒启**并输出修复指引；**验证器过 ≠ 闸过**，须以真实进程触发为准；三入口逐一验证：**wrapper / 直启 `python daemon.py …` / GUI 拉起** |
| V8 | **主 venv 迁移验证**：`_runtime\venv_quant_studio`（1.5.4）按 §3-⑥ 迁移路径 ① 或 ② 落地后**可正常启动**；未迁移时必须**被拒启并给出指引**（不得静默绕过） |

## 7. 未定谳项与取证计划（禁止未证实归因）

| 项 | 现状 | 取证动作 |
|---|---|---|
| WAL 增长主因 | **硬杀定谳（强）**：09-23 `daemon.log` 末行 **15:54:53** 与 `quantstudio.db.wal` mtime **15:54:46** 吻合；日志内 `graceful shutdown`/`已清理 status`/`CHECKPOINT`/`Traceback` **0 命中**；`daemon_status.json` 显示该 daemon 为 **直启**（非 wrapper）。巨事务 / 长连接阻塞检查点仍为次级候选 | ① 副本库对照实验（长连接 + 写入 + 硬杀）量化各成因贡献；② 如需进一步定谳，在 daemon 收尾路径加打点后再观察一轮 |
| 混版写入（**已定谳 → 裁定① 处置**） | **定谳**：钉版仅覆盖 `Python311`（09-21 09:19）与 `python3.12.9`（09-23 17:40）；**官方主 venv `venv_quant_studio` = 1.5.4、daemon 实际所用 `venv_miniQMT` = 1.5.3 均未被覆盖**（详见 `docs/evidence/duckdb-version-environment-map-20260923.md`） | 已裁：**版本闸（⑥）+ 连接级隔离约定（§4.4）**；trading 侧 venv 保持 1.5.3（read_only 消费）；**禁止**用 1.5.x 打开生产库（安全红线不变） |
| 长持有连接阻塞检查点 | 未测 | 副本实验：长连接持有时执行 `CHECKPOINT` 的等待/失败路径 |

## 8. 风险与回退

| # | 风险 | 缓解 | 回退 |
|---|---|---|---|
| 1 | GUI 异步化引入时序问题（读取晚于 UI 渲染） | 保留同步降级兜底：窗口先显示 + 首屏占位「读取中」；回归覆盖任务页渲染 | 单文件回退（`main_window.py`/`db_helper.py`） |
| 2 | `_safe_query` 超时把「慢查询」误判为故障 | deadline 只作用于**连接与首查**（默认 5 s，可配），慢查询走既有路径；降级文案明确区分 | 关闭超时开关（env/配置） |
| 3 | daemon 收尾 CHECKPOINT 延长轮次边界 | 实测秒级（7.3 s / 2.33 GB）；若 WAL 更大则改由 B 在空闲窗执行 | 关闭 A，仅留 B |
| 4 | 巡检误告警 | 阈值可配 + 连续 N 次命中才告警 | 关闭巡检 |

## 9. 派单与期限

| 项 | 内容 |
|---|---|
| 归属 | 客户运维会话（六步第 1 步 + 实施）；审计 = ZCode |
| 期限 | 本方案：**24h 内提交审计**；实施：审计通过后 D+1；验收：D+1.5；用户确认 + 双推：D+2 |
| 完成目标 | V1–V6 全绿 + 证据入档 + README/docs 引用同步 + 客户通知（若涉交付面） |
| 六步纪律 | 方案→审计→实施→验收→用户确认→双仓库推送（+ trading 同步门：触及 `quantstudio/`、`main_gui.py` → **不豁免**） |

## 10. 裁定与口径记录（A 案）

| 项 | 裁定 / 事实 | 时点 |
|---|---|---|
| 降级路径 | **裁定① = 方案 1**：QuantStudio 侧版本闸（覆盖 wrapper / 直启 / GUI 三入口）+ 连接级隔离约定；trading 侧 `venv_miniQMT` 保持 **1.5.3**，其 duckdb 限定 **read_only 消费**（1.5.3 读 1.4.5 写的库 = 向后兼容成立） | 2026-09-23（ZCode 审核） |
| 隔离约定附注 | **read_only 读者打开时会回放 WAL**（今日 GUI 案教训）→ OSkhQuant 读主库须**避开 daemon 写入高峰**（已写入 §4.4 约定文本） | 同上 |
| 今晚启动口径 | 已落地并确认：17:40 补装 `python3.12.9` + duckdb 1.4.5 → 17:41 新代际；入卷口径 = **总调度裁定 (a) 授权 + 回补执行执行**（两方如实记，非「不明执行方」） | 2026-09-23 |
| 24h 修订采纳范围 | 五点修订 + ⑥ 三入口版本闸 + **V7 以 `venv_miniQMT`（1.5.3）实际触发拒启断言为准（验证器过 ≠ 闸过）** + **主 venv（1.5.4）处置方案并入 ⑥** | 2026-09-23 |
| 本会话更正 | 此前「QuantStudio 无专用 venv」结论**有误**：主 venv 位于**仓库外** `_runtime\venv_quant_studio`（由仓内 `scripts\activate_venv.bat` 引用）；以 `docs/evidence/duckdb-version-environment-map-20260923.md` §1 为准 | 2026-09-23 |
