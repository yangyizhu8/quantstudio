# specs/001 refine 候选项

> 本文件只登记**待裁定的语义疑问与 refine 候选**，不改动本目录既有的 spec / plan / tasks / contracts 基线。
> 初登记 2026-10-30 21:55；**代码定位完成并改判 2026-10-30 22:30**；呈批：2026-11-04 框架项同轮。

## RC-001 · 副本根回测是否触发主 daemon 停止 —— **改判：原语义疑问不成立（静态证据）**

### 结论（先给结论）

**副本根回测（`QUANTSTUDIO_DATA_ROOT`=副本）不会停主 daemon。** 停采集是通过在 `DATA_ROOT` 下写停止请求/标记文件并等待 daemon 退出实现的，而 `DATA_ROOT` 在模块加载时读取 `QUANTSTUDIO_DATA_ROOT` —— 副本根回测的停止请求落在**副本目录**，主 daemon（`DATA_ROOT`=主库）看不到。

因此原登记的现象归因需要修正：**2026-10-30 20:08:10 的主 daemon 停止，来源是同刻误启动的那次回测（未设 `QUANTSTUDIO_DATA_ROOT` ⇒ `DATA_ROOT`=主库），不是 20:41/20:54 的黄金逐位 A/B 两次（两者均设了副本根）**。

### 静态证据链（只读定位，逐 file:line）

| # | 位置 | 事实 |
| --- | --- | --- |
| 1 | `quantstudio/_paths.py:30` | `get_data_root()` 优先读环境变量 `QUANTSTUDIO_DATA_ROOT` |
| 2 | `quantstudio/_paths.py:49-50` | `DATA_ROOT = get_data_root()` —— **模块加载即求值一次**（注释：配置在会话内不变） |
| 3 | `quantstudio/pipeline/backtest_window.py:40-42` | `_data_root()` → `from quantstudio._paths import DATA_ROOT` |
| 4 | `quantstudio/pipeline/backtest_window.py:46,50,54,58` | `run_lock_path` / `stop_request_path` / `stop_flag_path` / `daemon_status_path` **全部** = `_data_root() / <文件名>` |
| 5 | `quantstudio/pipeline/backtest_window.py:299-305` | `CollectorStopContext.__enter__`：`clear_stop_flag()` → `request_stop("backtest")` → `wait_daemon_exit(...)` |
| 6 | `quantstudio/backtest/run_ptrade_strategy.py:127-192` | coexist 段：`if not coexistence: return` → `_precheck_strategy_before_stop` → `with CollectorStopContext(...)`；**整段无 `QUANTSTUDIO_DATA_ROOT` 引用、无副本根判断** |
| 7 | `quantstudio/backtest/run_ptrade_strategy.py:557` | 注释声明共存语义：默认「回测前停采集，结束后不自动重启」（spec FR-003/004） |

⇒ 链条闭合：停采集 = 在 `DATA_ROOT` 下**写文件**（非直接杀进程），故 `DATA_ROOT` 一旦被环境变量重定向到副本，主库目录下不会出现停止请求 ⇒ 主 daemon 不受影响。

### 动态证据的两侧

| 观测 | 读数 | 说明 |
| --- | --- | --- |
| 误启动那次（20:08:08 启动，未设数据根） | `daemon.log` 20:08:10 `[DaemonLifecycle] 已清理本进程 status 文件` | 与「`DATA_ROOT`=主库 ⇒ 停止请求落主库 ⇒ 主 daemon 退出」吻合 |
| A/B 两次（20:41 / 20:54，均设副本根） | 各自日志首行 `[coexist] 已等待 0.0s · 等待采集收尾（任务边界）` | 停止请求**已发出但 0.0s 返回** ⇒ 落副本目录且副本无 daemon 在跑，与「不影响主 daemon」吻合 |
| A/B 期间主 daemon | 20:08:10 停 → **21:51:34 才自愈重启**（pid=29152，非本会话操作） | ⇒ A/B 期间主 daemon 本就处于停止态，**无法用 A/B 动态反证「副本根不停主 daemon」**；本结论以静态证据链为准 |

### 真正暴露的问题（改写后的候选）

原语义疑问不成立，但暴露两个**独立**问题，建议作为 11-04 呈批素材：

| 编号 | 问题 | 候选处置 |
| --- | --- | --- |
| RC-001a | **未设 `QUANTSTUDIO_DATA_ROOT` 的 CLI 回测会停主 daemon**，且此刻若参数非法仍可能先停后失败（本次即 `--help` 未知参数被忽略、按默认窗口启动并停采集） | ① CLI 在 `run_ptrade_strategy.py` 主入口对**未知参数 fail-fast**（当前未知参数被静默忽略）；② 启动回测前打印一行「有效数据根 = <DATA_ROOT>，将停主 daemon」让操作者可见 |
| RC-001b | `DATA_ROOT` 是模块级常量（`_paths.py:49-50`），会话中途改环境变量不生效 | 若未来出现「同会话切换数据根」需求，需改为延迟求值或显式 reload 通道；当前无此需求，登记备查 |

### 状态

- RC-001（原疑问）：**已关闭**（代码定位改判为不成立，静态证据链闭合；总调度 2026-10-30 照准关闭）。
- RC-001a / RC-001b：**候选（未立项）**，并入 2026-11-04 框架项同轮呈批；**RC-001a 优先**（未知参数 fail-fast + 启动打印「有效数据根=<DATA_ROOT>，将停主 daemon」——2026-10-30 误启动事故即活例）。

## 附：本次暴露的申报纪律缺失（已入记忆，非 spec 项）

- 2026-10-30 20:08 起多次回测期间主 daemon 状态**当轮回执未申报**（20:08 误启动那次确实停了主 daemon）。
- 处置：新增会话纪律「任何回测（含副本根）后回执必须附 daemon 状态一行（RUNNING PID / NOT RUNNING + 依据来源 + 是否触发停服 + 恢复时点）」，已写入 global 常驻记忆 `c0ad9026`。
