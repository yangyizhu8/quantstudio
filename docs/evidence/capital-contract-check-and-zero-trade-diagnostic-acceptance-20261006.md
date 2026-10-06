# 验收证据：设计契约本金校验（A）+ 零成交诊断（B）

> 六步流水线**第 4 步（验收）**｜2026-10-06｜实施方：策略线会话（DSH）
> 方案件：`docs/design-contract-capital-check-and-zero-trade-diagnostic-design.md`（②审计 PASS）
> 改动：`quantstudio/backtest/run_ptrade_strategy.py` **+44**｜`quantstudio/backtest/backtest_engine.py` **+10**
> **合计 2 文件 54 行插入、0 行删除（纯新增）**｜AST 解析 OK｜每处 edit 后 `git diff` 自检通过
> 回退点：`git stash` → `0c5747a3c4862b1189f959d49311d4e1a7b836b8`（操作前建；既有 92 条栈未动）
> 实施前 HEAD：`bb8a600`｜两目标文件工作区**均干净**（已核）

## 1. 端到端验收（真实策略：csi300_slow_kd_reversal，窗口 2026-01-05~2026-02-27）

| # | 判据 | 结果 | 实测 |
| --- | --- | --- | --- |
| **V-A1** | 本金不足 → 出现契约告警（含四要素） | **PASS** | capital=100,000 时出现 `[Backtest] 初始资金低于策略设计契约`，含 required / capital / sizing_mode / design 路径 |
| **V-A2** | 本金充足 → **无新增输出** | **PASS** | capital=1,000,000 时**无**契约告警 |
| **V-B1** | 零成交 + 无错误 → 出现零成交诊断 | **PASS** | 输出 `[Backtest] 全程零成交：34 个交易日、0 笔成交、执行错误 0。可能原因：①…②…③…` |
| **V-B3** | 有成交 → **无新增输出** | **PASS** | capital=1,000,000 时 34 笔成交、**无**零成交诊断 |
| **V-B4** | 端到端两条同现 | **PASS** | 10 万本金回测中 A、B 两条诊断**同时出现** |
| **V-C4** | **既有策略同参回测三件套 SHA 逐位不变** | **PASS** | vs 修复前基线（`20261005_223519_strategy`）：`config.csv` `a0a8db70fc678dd6` / `daily_stats.csv` `2e81d88b3355f558` / `trades.csv` `0a6c0e65ad94c9ad` **三者全部 IDENTICAL** |

> V-C4 是「纯增益」的**决定性证据**：修复前后**数值与产物零差异**，两条新诊断只在异常情形出现。

## 2. 边界与鲁棒性验收

| # | 判据 | 结果 | 实测 |
| --- | --- | --- | --- |
| **V-A3** | `required_initial_cash = null`（runtime_total_value）→ 无输出 | **PASS** | 全仓 3 个该型 RESOLVED 策略（`CANSLIM突破成长选股策略.py` / `恐慌抄底事件驱动逆向策略.py` / `沪深300均值回归超跌反弹.py`）**全部零输出** |
| **V-A4** | legacy 策略（无 design）→ 无输出 | **PASS** | 样本 `ashare_manual_pool_2d_momentum_top2_quantstudio.py`（`status != RESOLVED`）**零输出** |
| **V-A5** | 构造异常（文件不存在）→ 不抛、不输出 | **PASS** | `_check_design_capital_contract('<不存在路径>', 100000)` **无抛出、零输出**（诊断自身不引入新失败路径） |
| **V-B2** | 零成交 **+ 有生命周期错误** → **既有文案逐字不变、新分支不越位** | **PASS** | 合成抛错策略（34 次 `handle_data` 错误、0 成交）：既有文案 `策略可能空跑：零成交 + 存在 34 次生命周期执行错误 —— 本回测结果不代表策略行为` **逐字出现**；新增分支**未触发**（`elif` 正确让位于 `if`） |

## 3. 回归验收

| # | 判据 | 结果 | 实测 |
| --- | --- | --- | --- |
| **V-C1** | `run_contract_gate.py --strategies` | **PASS** | **CONTRACT GATE : PASS**（契约矩阵门禁 + pytest 契约套件 + **6 策略 api_portability 冒烟**，既有白名单无触发） |
| **V-C2 / V-C3** | 零成交断言与日志断言相关测试 | **PASS（零回归）** | 见下节 |

### 3.1 V-C2 / V-C3 详情（**含 2 个既存失败的证据链**）

受控测试集（含全部零成交断言用例与 `test_user_pyqt_candidate_flow.py:125` 的日志行断言）实测：

| 侧 | 结果 |
| --- | --- |
| **改动后**（本工作树） | `2 failed, 107 passed, 8 xfailed` |
| **改动前**（独立 worktree 检出 `bb8a600`） | `2 failed, 107 passed, 8 xfailed` |

**失败用例同名同数**：
- `tests/test_next_open_limit_and_halt.py::test_drain_rejects_buy_when_t1_gap_exceeds_cash_no_partial`
- `tests/test_user_pyqt_candidate_flow.py::test_user_mode_candidate_then_evidence_then_formal_promotion`
（后者失败于 `publish_agent_strategy.py:49` 的 `R5.5 robustness evidence missing from the ledger`，与 A/B 无因果关系）

**判定方法（非推测）**：用 `git worktree add <临时路径> HEAD` 检出**改动前的 `bb8a600`**，在**独立工作树**跑同一批测试，得**完全一致**的通过/失败统计与失败名单；
并先核验基线侧两文件确无 A/B 痕迹（`_check_design_capital_contract` 命中 0）。对照完成后已 `git worktree remove --force` 清理，**未触碰本工作树**。

→ **两失败为改动前既存**，**非本次 A/B 引入**；本次改动对测试面**零回归**。

> 建议：该 2 个既存失败与 `R5.5` 台账门禁相关，建议由总调度转相应归属会话复查（本件不处理，避免越界）。

## 4. 影响面声明（复核方案件 §4）

| 维度 | 实测确认 |
| --- | --- |
| 数值/产物 | **零差异**（V-C4 三件套 SHA 逐位一致） |
| 健康路径日志 | **零新增**（V-A2/V-B3） |
| legacy / null 设计 / 异常 | **零输出**（V-A3/A4/A5） |
| 既有空跑告警 | **逐字不变**（V-B2） |
| 策略源码 | **零改动**（符合「仅限框架层」铁律） |
| 撮合/费用/持仓/净值/信号 | **未触碰** |

## 5. 待办（⑤用户确认后）

| # | 项 |
| --- | --- |
| 1 | ⑤用户确认 → ⑥双仓库推送（**精确清单**：2 个代码文件 + 方案件 + 本证据件；提交信息须含**同文件叠加声明**） |
| 2 | 推送后三方 40 位核对 + 留痕行 |
| 3 | QuantStudio-trading 副本同步门（触及 `quantstudio/` 共享层，**不豁免**） |
| 4 | 建议转归属会话复查上述 2 个既存测试失败 |

## 6. 结论

**六步①方案 →②审计（PASS）→③实施 →④验收（V-A1~A5 / V-B1~B4 / V-C1~C4 全 PASS）已完成。**
本次事故模式（本金错配导致的静默零成交）**已被两条显式诊断覆盖**：运行开始时校验设计契约（A）、运行结束时零成交即报告（B）；
且**对健康路径零影响**（三件套 SHA 逐位一致、测试面零回归）。**待用户确认后进入⑥双仓库推送。**

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
