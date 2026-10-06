# 验收证据：D1 —— 设计契约本金「运行前确认门」+ 零成交「运行后提示」

> 六步流水线**第 4 步（验收）**｜2026-10-06｜实施方：策略线会话（DSH）
> 方案件：`docs/gui-capital-contract-confirm-and-zerotrade-notice-design.md`（②审计 PASS，P0 级）
> 改动：`run_ptrade_strategy.py`（件一：抽纯查询 + A 改复用）、`gui/tabs/backtest_tab.py`（件二 `_on_run` 确认门 + 件三 `_on_finished` 提示）
> 回退点：`git stash` → `d2aebbe5ab3bde0ee34567237341565aa38f5585`（操作前建；既有 95 条栈未动）
> 实施前 HEAD：`bb8a600`（实施期间他线推进至 `8b63b82`，不影响本件）
> **合计：2 文件、0 行删除**（件一 +25 相对 A 件状态；`backtest_tab.py` +46）

## 1. GUI 交互验收（离屏 Qt + 真实 BacktestTab 实例 + 模拟点击）

方法：`QT_QPA_PLATFORM=offscreen` 实例化真实 `BacktestTab`（stub 主窗/stub Worker/stub 结果窗），
patch `QMessageBox.exec` 按**按钮文案**（继续 / 取消）模拟点击，逐场景断言。

| # | 判据 | 结果 | 实测 |
| --- | --- | --- | --- |
| **V-D1** | 本金不足 → 弹询问框（含 required/当前值/两条出路） | **PASS** | 弹框出现，标题「初始资金低于策略设计契约」，正文含 `1,000,000` 与 `100,000`，提示含【继续】【取消】 |
| **V-D2** | 选「取消」→ **不启动回测** | **PASS** | **未创建 Worker**（`_on_run` 提前 return；表单保留可改金额） |
| **V-D3** | 选「继续」→ 照常启动 | **PASS** | 弹框出现 **且** 创建 Worker |
| **V-D4** | 本金充足（100 万）→ 不弹框 | **PASS** | 弹框**未出现**、Worker 正常创建 |
| **V-D5** | legacy 策略（无 design）→ 不弹框 | **PASS** | 弹框**未出现**、Worker 正常创建 |
| **V-D6** | 零成交 → 结果窗口前出现提示 | **PASS** | `_on_finished(trade_records=[])` → 弹出「回测全程零成交」 |
| **V-D7** | 有成交 → 无新提示 | **PASS** | `_on_finished(trade_records=[…])` → **无**提示 |

> 注：测试脚本自身经 3 轮修正（`QMessageBox.button()` 只接受 StandardButton／Worker stub 信号对象／主窗 `hold_worker`）——**均为测试桩缺陷，非生产代码问题**，最终 6/6 场景全 PASS。

## 2. 文案与数值验收

| # | 判据 | 结果 | 实测 |
| --- | --- | --- | --- |
| **V-D8** | **A 件文案逐字不变** | **PASS** | 直接调用 `_check_design_capital_contract(pub, 100000)`，输出与 A/B 期实测原文**逐字一致**（含「可能因此无法足额建仓」） |
| **V-D9** | **引擎数值零变更** | **PASS** | 与 A/B 基线（`20261006_004325_strategy`）比：`daily_stats.csv` `2e81d88b3355f558`、`trades.csv` `0a6c0e65ad94c9ad` —— **三者逐位一致**；`config.csv` 差异仅因记录**数据库路径**变化（`bd2aea30` vs `a0a8db70`），非数值 |
| **V-D10** | 契约门禁 | **PASS** | `run_contract_gate.py --strategies` → **CONTRACT GATE : PASS**（契约矩阵 + pytest 契约套件 + 6 策略 api_portability，白名单无触发） |

### 2.1 一件无关但必须报告的实测发现（**非本件引入**）

`find_design_for_strategy` 对两条路径的解析结果不同：

| 路径 | status | resolve 结果 |
| --- | --- | --- |
| **已发布**（`quantstudio/backtest/strategies/…`） | **RESOLVED** | `required_initial_cash=1000000.0`（GUI 走的就是这条 ✓） |
| 工作区（`agent_workspace/csi300_slow_kd_reversal/strategy.py`） | **LEDGER_MISMATCH** | `None` |

→ **GUI 路径（本件的目标场景）解析正常 ✓**；工作区路径的 `LEDGER_MISMATCH` 属 strategy-compiler 域（与已登记的 2 个既存测试失败同域），**本件不改**，建议随该域一并复查。

### 2.2 环境变更（须记录）

实施期间发现 `data/quantstudio.old_20260920.db`（前几轮使用的已批准外部库）**已不存在**；
`data/quantstudio.db`（主库）**当前可读**（daemon 锁已释放，`stock_daily` 9,809,315 行）。
V-D8/D9 因此改用主库运行，结论不受影响（**非本件引入**）。

## 3. 影响面声明（复核方案件 §5）

| 维度 | 实测确认 |
| --- | --- |
| 引擎数值/产物 | **零差异**（V-D9：daily_stats / trades 逐位一致） |
| A 件行为与文案 | **逐字不变**（V-D8） |
| 资金充足路径 | **流程逐字一致**（V-D4：无弹框、直接跑） |
| legacy / null 设计 | **无弹框**（V-D5） |
| 有成交路径 | **无新提示**（V-D7） |
| CLI / 脚本路径 | **完全不受影响**（件一只是纯查询函数，无交互） |
| 引擎数值逻辑 | **未触碰** |

## 4. 待办（⑤用户确认后）

| # | 项 |
| --- | --- |
| 1 | ⑤用户确认 → ⑥双仓库推送。**精确清单**：`run_ptrade_strategy.py`（M，含 A 件 + 件一）、`backtest_engine.py`（M，含 B 件）、`gui/tabs/backtest_tab.py`（M，件二/三）、方案件、本证据件（A，均为新增） |
| 2 | 提交信息须含**同文件叠加声明**：`run_ptrade_strategy.py` 上叠加了 A 件（未提交）与 D1 件一，两者同属本批 |
| 3 | 推送后三方 40 位核对 + 留痕行 + 副本同步门（触及 `quantstudio/` 共享层，**不豁免**） |
| 4 | 建议随 strategy-compiler 域复查：工作区路径 `LEDGER_MISMATCH`、2 个既存测试失败 |

## 5. 结论

**六步①方案 →②审计（PASS）→③实施 →④验收（V-D1~D10 全 PASS）已完成。**
用户此前「GUI 看不出问题、无从干预」的体感缺口**已闭合**：本金不足时**开跑前弹框询问**（取消→不启动、继续→照跑），
零成交时**结果窗口前显式提示**；而**资金充足 / legacy / 有成交**三条健康路径**逐字不变**，**引擎数值零变更**（V-D9）。
**待用户确认后进入⑥双仓库推送。**

---

## 附：反向卷入事故登记（2026-10-06，用户裁定 A「接受归属 + 登记」）

**事实**：本件的 **B hunk**（`backtest_engine.py` 零成交诊断分支）在提交前被**他线 POS-01 件**的提交
`b55ff6c`（`fix(engine): POS-01 cost_basis 摊薄口径对齐 PTrade`）**一并携带入库**。
证据：`git log -S "全程零成交：{len(self.result.nav_history)}" -- quantstudio/backtest/backtest_engine.py` → 归属 `b55ff6c`；
该提交信息自述「**CLI hunks 因他线 D1/A 叠加暂缓**」——他线**已在主动避让**本线的 CLI 侧改动，
但 engine 文件因两线同处一文件而未能分离。

**影响面**：**内容与验收均无影响**（B hunk 逐字正确、V-B1/B2/B3 全 PASS）；唯一缺陷是 **git 归属**。

**处置（裁定 A）**：**接受归属 + 显式登记**，不重写他线提交（重写跨会话历史风险 > 收益；且裁定时该提交尚未推送）。
本件剩余改动（A + D1 件一 + 件二/三 + 文档）**独立提交**，提交信息含本登记。

**同时登记本线一处操作失误**：hunk 级外科提交的还原步骤使用了**错误的备份文件名**
（Python 侧备份写作 `run_ptrade_strategy.py.shared_current`，PowerShell 侧按 `runner.shared_current.py` 查找）
→ 还原失败、工作树一度缺 POS-01 内容；**1 分钟内用正确备份修复并逐项核实**（两文件 `cost_basis=Y` 且本线改动=Y）。
教训：**备份与还原必须同一套命名约定，且还原后必须逐项验证**（已纳入提交前核对清单）。
