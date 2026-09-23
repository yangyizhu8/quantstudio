# CASE-007 客户事故归档：GUI 启动卡死（WAL 残留 × 构造期同步打开，2026-09-23）

> 状态：**已闭环**（实施段 21 笔上远程；本会话贡献 10 笔）
> 事件：`python main_gui.py` 启动后**窗口永不出现**——进程存活、CPU 94.7%、50 线程、RSS 3.46 GB，无报错
> 根因：主库带 **2.17 GB 未检查点 WAL** → 任何 `duckdb.connect()` 都要**先回放 WAL**（实测 **1338.4 s ≈ 22.3 分钟**），
> 而 GUI 把这次打开放在 **`MainWindow` 构造期同步执行** → 构造永不返回 → 窗口永不出现
> 次因：本机 duckdb **1.4.5** 下 `connect()` 为**长时间阻塞**而非快速失败 → 既有忙态重试（2×0.2 s）**永不触发**
> 闭环：用户裁定 → 六笔实施 → V1a–V8 验收 → 五步推送（三方核对一致 `000b5bb`）

## 1. 事件与定位

| 项 | 实测 |
|---|---|
| 现象 | 无窗口、无 traceback；进程 CPU 94.7% / 50 线程 / RSS 3.46 GB |
| 卡点（faulthandler 线程栈） | `main_gui:33 → main_window:96 __init__ → :146 _setup_navigation → :159 _create_tab → task_tab:71 __init__ → :209 _load_tasks → :213 _render_tasks → db_helper:186 get_watermarks → :152 query_duckdb → :99 _safe_query`（**connect 本身**） |
| 库/WAL | `quantstudio.db` 38.08 GB；`quantstudio.db.wal` **2.17 GB**（mtime 15:54:46，未检查点） |
| 打开行为 | 新只读探针 connect 期间 CPU 2.66 s/4 s（≈66% 单核）、RSS 增长 → **在回放 WAL**，非等锁 |
| 阻塞链 | DuckDB 点名：`File is already open in PID 39176 / 11180 / 38544`（探针遗留进程，已清） |
| 环境 | Python 3.11.9 + duckdb 1.4.5；PyQt6/qfluentwidgets 均可导入 → **非依赖问题** |

## 2. 一次性维护（CHECKPOINT）

| 阶段 | 实测 |
|---|---|
| `open RW`（= WAL 回放） | **1338.4 s ≈ 22.3 分钟** |
| `CHECKPOINT` 本体 | **7.3 s** |
| WAL / 主库 | **2.33 GB → 0.00 GB**；38.08 → 38.87 GB（吸收 WAL） |
| 冷启动（维护后） | 12.4 s（对比修复前 >15 分钟不出现） |
| 安全 | 用与 GUI 同版本解释器（1.4.5）；未用 1.5.x 打开生产库 |

## 3. 修复六笔（分笔提交，精确路径）

| 笔 | 内容 | 提交 | 关键实测 |
|---|---|---|---|
| 笔1 | **非阻塞启动**：首屏只建 tab0 + 其余分帧增量创建 + 首查/首刷延后（拆分：JSON 读取留构造期、DB 查询延后 → `self.tasks` 契约不变） | `22d7c5b` | 构造 **14.94 s → 1.49 s**；**V1a 2.45 s**（≤5 s） |
| 笔2 | **只读查询超时降级 + 降级后自动恢复**（deadline 5 s / 单槽 daemon 线程 / 30 s 自动重试 + 手动刷新） | `7ca1d6a` | 用例 **5 passed**（超时降级/自动收割/真故障上抛/不新起线程/契约透传） |
| 笔3 | **daemon 轮次收尾安全检查点**（`ResidentCollector.close()`，每轮采集后必调）+ 共享模块 `pipeline/db_checkpoint.py` | `aef6b9d` | 用例 **5 passed**（含副本库真实收敛 WAL 归零） |
| 笔4 | **WAL 体积巡检**（`scripts/wal_health_check.py`，含 **daemon 运行前置守卫**） | `28eaf41` | 实跑：WAL 3.0 MB/阈值 268.4 MB 正常；daemon 在跑 → **拒绝 exit 2**（防自卡死） |
| 笔5 | **版本闸三入口**（GUI / daemon `-m` / GUI 拉起子进程 / activate_venv.bat；逃生阀 `QS_DUCKDB_VERSION_GATE=0`） | `b429f98` | **真实进程触发拒启 exit 3**（1.5.3 三入口）；机制用例 8 passed |
| 笔6 | **口径修正**（`activate_venv.bat`：官方解释器=Python311）+ V8 收口 | `0089425`、`000b5bb` | 见 CASE-008 §4 |

## 4. 验收（V1a–V8）

| # | 判据 | 实测 |
|---|---|---|
| V1a | 大 WAL 场景窗口可交互 ≤5 s | **2.45 s** ✓ |
| V1b | 首查完成时间不显著劣化 | 构造 14.94→1.49 s；首查移入事件循环 ✓ |
| V2 | 降级文案 + 自动恢复 + 数据不损坏 | 5 passed + UI 侧 30 s 自动重试/手动刷新 ✓ |
| V3 | daemon 收尾后 WAL 上限 | 收尾检查点机制 + 副本实证归零 ✓ |
| V4 | 巡检告警 + 防自卡死 | 实跑 + daemon 在跑即拒（exit 2）✓ |
| V5 | 回归全绿 | GUI 面 **87 passed**；**更大范围 312 passed / 22 文件 / 127.87 s 零失败零跳过** ✓ |
| V6 | 证据入档 | `docs/evidence/gui-nonblocking-implementation-20260923.md` 等 3 份 ✓ |
| V7 | 版本闸真实触发 | 1.5.3 三入口 exit 3 + 逃生阀验证 ✓ |
| V8 | 主 venv 迁移 | 降级 1.4.5 + 补装 + GUI 冒烟 1.76 s + psutil 自愈 PASS ✓ |

**黄金对比**：经审核采信**不适用**（改动面未触回测引擎/策略/注入 API/数据语义），依据见实施验收档 §3.5。

## 5. 实施期发现与更正（如实记录）

1. **「直启」形态结构上不支持**：`python quantstudio/pipeline/daemon.py` 必然
   `ImportError: attempted relative import with no known parent package`（模块用相对导入）；
   补 sys.path 引导无效 → **已回退，不留死代码**；受支持入口 = `-m` / GUI 拉起子进程 / `-c import main; main()`。
2. **cmdline 不能判启动形态**：`daemon_status.json.cmdline` 在 `-m` 下与直启**同形**（`sys.argv[0]`=模块绝对路径）
   → 更正此前「09-21 为直启」的推断（应为 `-m`）。
3. **测试时序回归修法**：笔1 初版整体延后 `_load_tasks` 使既有用例读 `tab.tasks` 为空而失败 →
   改为**拆分**（保契约不变）后 87 passed。
4. **探针遗留进程**：排查期 3 个探针卡在内核 I/O 等待（`taskkill` 报 no running instance，
   `Invoke-CimMethod Terminate` 才清除）→ 已清理；防复发纳入笔2（查询必带超时）。
5. **测量口径误报**：V8 冒烟首版用 `MainWindowTitle` 且标题过滤过宽，误报 Edge 窗口
   （标题含「QuantStudio-Max项目审核」）→ 收紧为精确匹配「数据管线控制台」+ ctypes 枚举窗口后 PASS。

## 6. 归档信息

- 卷宗编号：**CASE-007**（CASE-006 已占用）
- 证据指针：`docs/evidence/gui-startup-wal-checkpoint-baseline-20260923.md`（维护+冷启动基线）、
  `docs/evidence/gui-nonblocking-implementation-20260923.md`（实施+验收 V1a–V8）、
  `docs/gui-startup-nonblocking-design.md`（方案，过审）
- 涉及提交：`7ca1d6a` `22d7c5b` `ea4bca1` `aef6b9d` `28eaf41` `b429f98` `5ee3149` `478ce38` `0089425` `000b5bb`
- 三方核对：local = origin/main = quantstudio-plus = quantstudio = `000b5bb`（0 笔残留）
- 关联案：**CASE-008**（duckdb 混版统一，同批交付）
