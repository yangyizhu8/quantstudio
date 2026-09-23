# A 案卷宗：duckdb 版本环境地图与钉版落点（2026-09-23）

- 归属：客户运维会话（A 案排查扩面：全机环境地图 / 9-21 降级落点 / daemon 版本取证）
- 关联：`docs/evidence/gui-startup-wal-checkpoint-baseline-20260923.md`、`docs/gui-startup-nonblocking-design.md`
- 结论摘要：**钉版 `duckdb>=1.4.5,<1.5` 从未覆盖实际运行环境**；机器上并存 **1.4.5 / 1.5.3 / 1.5.4** 三版本

---

## 1. 全机 Python 环境地图（一页表）

| 解释器 / venv | py | duckdb | duckdb dist-info mtime | 说明 |
|---|---|---|---|---|
| `C:\python3.12.9\python.exe` | 3.12.9 | **1.4.5** | **2026-09-23 17:40** | 系统默认解释器；**17:40 补装 1.4.5**（入卷口径：**总调度裁定 (a) 授权、回补执行执行**），17:41 起新代际 |
| `C:\Users\Administrator\...\Python311\python.exe` | 3.11.9 | **1.4.5** | **2026-09-21 09:19** | GUI/我今日 CHECKPOINT 所用解释器 |
| `D:\miniQMT策略实盘\_runtime\venv_quant_studio` | 3.11.9 | **1.5.4** | **2026-07-19 22:26** | **QuantStudio 官方主 venv**（`scripts/activate_venv.bat` 指向它） |
| `D:\...\trading-battle-back\venv_miniQMT` | 3.12.9 | **1.5.3** | **2026-05-31 01:52** | **09-21~09-23 daemon 实际所用**（写主库两天） |
| 其余 20 个 venv（agent_quant 族 / trading 族 / 其它项目） | 3.11.9 或 3.12.9 | **无 duckdb** | — | 与主库无交互面 |

（完整 24 项清单见本次排查输出；`py -0p` 仅注册两个系统解释器：3.12.9 主 + 3.11）

## 2. 钉版落点核实（9-21 降级落点）

| 事实 | 证据 |
|---|---|
| 钉版提交时点 | `5913785 2026-09-21 12:55:44 fix(deps): duckdb 钉版 >=1.4.5,<1.5 — issue duckdb#23645` |
| 9-21 降级实际落点 | **`Python311`**：`duckdb-1.4.5.dist-info` mtime **2026-09-21 09:19**（早于钉版提交 3.6 小时） |
| 今日新增落点 | **`python3.12.9`**：`duckdb-1.4.5.dist-info` mtime **2026-09-23 17:40**，`INSTALLER=pip`（**入卷口径：总调度裁定 (a) 授权、回补执行执行**；17:41 起新代际生效） |
| **未覆盖** | `venv_miniQMT` = **1.5.3**（自 2026-05-31，早于钉版 3 个月余）；**官方主 venv `venv_quant_studio` = 1.5.4**（自 2026-07-19） |

### 定谳（A 案定性）
**钉版修复从未覆盖实际运行环境**：
- 09-21 钉版生效后，**daemon 仍以 `venv_miniQMT`（1.5.3）写主库两天**（09-21~09-23）；
- **官方主 venv（GUI/daemon 的激活脚本指向）至今仍是 1.5.4** —— 即按官方启动方式运行的任何 QuantStudio 进程，都**不受钉版保护**；
- 故「钉版」当前仅是一份**声明**，与运行期实际版本脱钩（今日 3.12.9 的 17:40 补装说明有人已在补救，但主 venv 与 venv_miniQMT 仍未覆盖）。

## 3. daemon 1564 实际 duckdb 版本取证

| 项 | 结论 |
|---|---|
| 直接证据 | **无**：`data/logs/daemon.log`、`daemon_launch*.txt` 均**无 duckdb 版本记录**（grep `duckdb|__version__` 仅命中 `[DuckDBWriter]` 日志前缀，非版本） |
| 环境推定 | **1.5.3**：`daemon_status.json` 记 `exe=...\venv_miniQMT\Scripts\python.exe`；该 venv 的 `duckdb-1.5.3.dist-info` mtime **2026-05-31** 早于 daemon 启动时间 **2026-09-21 15:44:23**，且期间无版本变更痕迹 |
| 取证等级 | **环境推定（强）**，非运行期自证；如需运行期自证需在 daemon 启动路径加版本打点（已入设计文档 ⑥/V7 范围） |

## 4. 对今晚紧急窗令（daemon 由主 venv 启动）的影响 —— 需裁定

「QuantStudio 主 venv」= `_runtime\venv_quant_studio`（`scripts/activate_venv.bat` 官方指向），但其 **duckdb = 1.5.4**，**违反钉版**。故今晚按令执行前必须先择一：

| 选项 | 动作 | 风险 |
|---|---|---|
| **T1（推荐）** | 今晚改用 **1.4.5 解释器**启动（`Python311` 或 `python3.12.9`，两者均已 1.4.5） | 与「主 venv」口径不一致，但版本合规、零安装风险 |
| T2 | 先把主 venv 降到 1.4.5（`_runtime\venv_quant_studio`），再按令启动 | 主 venv 是 GUI/daemon 共同环境，降级需回归验证（跨面变更） |
| T3 | 先降 `venv_miniQMT` 到 1.4.5 | **受阻**：同 venv 的 `OSkhQuant` 子系统使用 duckdb（含 read_only 读主库）→ 按裁定「发现依赖即受阻、禁止强降」 |

## 5. 更正声明（本会话）

我此前回报「QuantStudio 无专用 venv（`.venv`/`venv`/`env` 均不存在）」**结论有误**：
当时仅检索**仓库内**目录；实际主 venv 位于**仓库外** `D:\miniQMT策略实盘\_runtime\venv_quant_studio`，
由仓内 `scripts/activate_venv.bat` 引用。此处更正，并以本文件 §1 为准。

## 6. 今晚口径落地闭环证据（2026-09-23 复核，只读）

| 判据 | 实测 | 结论 |
|---|---|---|
| `daemon_status.json` | `pid=40096`；`exe=C:\python3.12.9\python.exe`；`started_at=2026-09-23T17:41:08`；`status=running` | **已切换到 1.4.5 解释器**（不再是 `venv_miniQMT`） |
| 该解释器 duckdb 版本 | **1.4.5** | 与钉版一致 ✓ |
| 进程侧交叉核验 | `PID=40096` start `09-23 17:41:06`，exe `C:\python3.12.9\python.exe`，cmdline `-m quantstudio.pipeline.daemon --mode forever --config-dir …\mcp_only --instance-token b1c4bb34…` | 与 status 一致（wrapper 形态 `-m`） |
| 主库健康 | `quantstudio.db` 38.87 GB（mtime 18:44:59）；**`quantstudio.db.wal` = 0 GB**（mtime 20:40:08） | 1.4.5 下运行 ~3 小时，**WAL 未膨胀**（对比案发时 2.17 GB）✓ |

**口径落地判定**：**成立**——运行中 daemon 已由合规解释器（`python3.12.9` + duckdb **1.4.5**）承担，
且 WAL 保持 0，未复现案发形态。本证据随**实施批**归档（裁定②口径：总调度裁定 (a) 授权、回补执行执行）。

## 7. 待办

1. ~~裁定今晚启动口径（T1/T2/T3）~~ → **已裁**：今晚口径已落地并确认（17:40 补装 3.12.9+1.4.5 → 17:41 新代际）；
2. ~~确认 17:40 补装执行方~~ → **已定入卷口径**：**总调度裁定 (a) 授权 + 回补执行执行**（两方如实记，非「不明执行方」）；
3. **主 venv（`venv_quant_studio` 1.5.4）与 `venv_miniQMT`（1.5.3）的覆盖方案** → 并入设计文档 §3-⑥ 与 §4.4 连接级隔离约定（裁定① = 方案 1：版本闸三入口 + trading 侧 read_only 消费；附注：read_only 读者会回放 WAL，须避开 daemon 写入高峰）。
