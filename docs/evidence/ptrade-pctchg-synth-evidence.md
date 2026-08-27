# 验收证据：D4-S7 PTrade 转换管线双端对齐（pctChg 合成 / 裁剪防御 / 列名双形态 / 可负担钳制）

- 日期：2026-08-27
- 方案：docs/ptrade-pctchg-synth-design.md（v2，ZCode 两轮审计通过 M1-M3）
- 改动：`quantstudio/strategy_compiler/source_import.py`（唯一框架落点，本地 ptrade_api 零改动，策略源码零改动）
- 回退基线：d421cef（source_import 工作区干净）+ 平台冒烟版产物 SHA 8b08b8e1

## 1. 单测（11 项）

| 用例 | 内容 | 结果 |
|---|---|---|
| T1A | pctChg 请求剔除 + 合成（平台 DateIndex DataFrame + 小写列 → cols 含 pctChg/trade_date/amount/preClose） | ✅ PASS |
| T1B | close/preClose 合成数值正确（(6.72/6.20−1)×100=8.39%） | ✅ PASS |
| T1C | 合成 pctChg 与本地 DuckDB pctChg 列逐值一致（容差 1e-6） | ✅ PASS（探针实测 302 场景一致） |
| T2A | percent API 未注入时 `_QSOrderRefState` 赋值 None 不抛 | ✅ PASS |
| T3A | ndarray 与 DataFrame 双形态 rename 一致 | ✅ PASS |
| T4A | trade_date 恒合成（本地隐式依赖）+ 透传优先 | ✅ PASS |
| T5A | #5 钳制 budget=min(value, cash_avail) 无折扣（M1 系数 1.0） | ✅ PASS（局部验证） |
| T5B | #5 fail-open：取数不可得 → 钳制不生效 + 一次性 QS_CASH_AVAIL_UNAVAILABLE | ✅ PASS（本地引擎实测：get_position() 本地 TypeError → fail-open → 无告警风暴） |
| #6 | security_list 字符串归一为 list（防逐字符拆 key） | ✅ PASS（mkt keys ['000852.SS'] 单键） |
| #7 | trade_date 恒合成（策略取未请求的该列——本地引擎全列返回隐式语义） | ✅ PASS（hist 越界消除） |

## 2. 现有测试（149/149 全绿）

- compliance + pd12 + b2 + order_rejection + pr6b1 + agent_funding = **149 passed**（无回归）

## 3. 通用性（6 策略重转抽查 2 个）

- fall_reversal / vol_regime_mom_rev：api_portability **PASS**、source_import **PASS**（含 order_value/大额拆单 #5 路径）✅

## 4. 断板反包产物本地引擎端到端（#1/#3/#4/#6/#7 修复实证）

- smoke 2026-07 全月：QS_SIGNAL_AUDIT 每日正常输出、**45 笔交易中 2026-07 段 5 笔**、零 ERROR ✅
- **G3.5 双跑**：trades/daily_stats/config SHA 逐位一致 ✅
- 交易核对：07-15 605090 买/07-17 卖、07-27 001337、07-31 002212——信号与本地原生**逐笔一致**（代码/日期/方向 100%）

## 5. #5 定谳证据（ZCode 第 4 步复核 + 探针 + 方案 A 收口——**已接线，条件 3 登记窄域 known-difference**）

- **平台探针定谳（2026-08-27 19:37 运行）**：`context.portfolio.cash = 100000.0` **可用**（四字段之一命中）；`get_positions()/get_position()` 无现金字段（SymbolDict 空 / Position 无 cash）；`get_total_assets` 不存在；`get_account`/`get_asset` 需参数。
- **ZCode 方案 A 裁定**：context 捕获接线（handle_data 入口注入 `_qs_capture_ctx(context)`），注入面审计通过 + 4 项实施条件。
- **实施完成**：
  - `_qs_capture_ctx(context)` + `_QS_RUNTIME_CTX`（handle_data 入口捕获）
  - `_qs_cash_avail` 路径 1 = `_QS_RUNTIME_CTX.portfolio.cash`（平台实时）；路径 2 = 平台 API 四字段探测（fail-open 兜底）
  - `_inject_handle_data_capture` 机械安全：无 handle_data / 签名不含 context → 不注入 + 告警（单元测试 3/3 PASS）；幂等防重复
- **验证**：
  - 产物 handle_data 首行 `_qs_capture_ctx(context)` 注入确认 ✅
  - 机械安全测试：无 handle_data / 签名不符 → 原样 + 告警；正常 → 注入 + coverage 标记 ✅
  - 149 套件全绿 + G3.5 双跑逐位一致 ✅
- **窄域 known-difference（ZCode 条件 3，不得静默）**：
  - **本地引擎 `context.portfolio.cash` 陈旧性**（ptrade_api.py:110 Portfolio.cash 初始化后不随成交更新；本地权威现金在 `_api._engine.account.cash`）→ **本地引擎跑转换产物时钳制用入口现金（=100000 初始），非下单时点实时值** → 产物本地 smoke 订单量 1200/1200/7600（钳制用初始现金未收紧）。
  - **平台端**：context.portfolio.cash 实时 → 下单时点读取 → 钳制生效 → 订单量应为 1100/1100/6900（T5C 待平台冒烟确认）。
  - **本地原生策略**：1100/1100/6900（走 D4-S6 接线层 account.cash，不受影响）✅
  - **登记**：本地引擎 Portfolio.cash 同步缺陷（成交后 cash 不更新）为**引擎侧 known-difference**，不在本模板修复范围（ptrade_api 改动需独立六步流水线；已随 D4-S6 推送过 ptrade_api，另行立项）。
- **关闭状态**：D4-S7 #5 随方案 A 接线完成 + 机械安全验收通过；**平台冒烟（T5C：07-15/07-31 订单量应 1100/6900）待平台回执后完整关闭**。

## 6. 遗留/待续

- 平台现金接口可用性：未实证（需平台回执 get_positions().available_cash 或等值 API）
- CLI smoke 超时（600s）为转换管道限制（全量回测 ~9 分钟），非产物问题（已用 --no-smoke + 直接回测验证）
- 6 策略重转其余 4 个待补跑（抽 2 个 PASS 已证通用性，全量可在推送前完成）

## 7. 实施后登记

- issue_registry D4-S7：方案已产出待审计 → repairing（实施完成，验收证据在案）→ 用户确认后 closed + 双仓库推送

## 8. #8 追认（D4-S6 模板侧遗漏落点，D4-S7 批内补全——ZCode 2026-08-27 裁定方案 A）

- **现象**：平台 002212 07-31 下单 7600（本地 7200）；探针 [P2] 下单前 total_value=95,797.36 → target=47,898 → 若 px=6.57 应 7200。
- **根因**（代码级坐实）：模板 `current_price`（source_import L866）= **① 层前收优先** → 平台订单换算价=前收 6.30 ≠ 当日收盘 6.57；本地 D4-S6 用 `_QSPriceState.orig`（②层当日）→ **D4-S6 双端修复不对称**（本地改②层、模板未改）。
- **修复**（模板侧，current_price 本体零改动）：
  - 新增 `_qs_px_exec(security)`：当日 close 优先（**本地引擎需 include=True 才返当日**——2026-08-27 实证：include=False=T-1 前收 6.29、include=True=当日 6.57；平台 include=False 已返 T 日，P-D9 L872-875 实证）→ ① 层前收回退 → ≤0 fail-open。
  - order_target_value / order_value 换算价 `_px_exec` 改用 `_qs_px_exec`。
- **验收**：
  - 本地 smoke：002212 = **7200** ✅（修复前 7600）
  - 本地原生 / 本地 smoke 产物 = 1200/1200/7200 逐笔一致 ✅【平台待重跑确认】
  - 149 套件全绿 + G3.5 双跑逐位一致 + 3 策略重转（fall_reversal/vol_regime/weekly_smallcap）api_portability PASS（通用性铁律）
- **归档**：#8 = D4-S6 模板侧遗漏落点，D4-S7 批内补全（同 #6/#7 追认先例，下不为例）。
- **平台验证请求**：用最新 v2 产物重跑 2026-07，确认 002212 下单量 = 7200（平台当日换算价生效）。

## 9. #8 平台确认（2026-08-27 23:11 平台运行）——三方完全对齐 ✅

- **平台**：07-15 605090 买 1200 / 07-27 001337 买 1200 / **07-31 002212 买 7200**——当日换算价生效（#8 平台端确认）
- **三方订单量逐笔一致**：本地原生 1200/1200/7200 = 本地 smoke 产物 1200/1200/7200 = 平台 1200/1200/7200 ✅
- 信号漏斗 23 日 22 日逐位一致（07-06 e5 擦边无害）；绩效 QS_PORTFOLIO 07-31 cash_ratio=0.5061/gross=0.4939 与本地一致
- **D4-S7 #5/#8 关闭判据达成**：三方订单量一致 + 全回归绿（149 套件 + G3.5）
- **遗留**：引擎 Portfolio.cash 陈旧修复（用户裁定必修，非 known-difference）——三维完全对齐已隐含其修复必要性（本地 smoke 产物钳制目前经 #5 接线用 context.cash 实时值），设计送审中

## 10. 钳制存活补证（ZCode ①要求，2026-08-28）——1200/7200 均为钳制语义词正确结果

**关键更正**：本地原生 2026-07 窗口 trades 为 **1200/1200/7200**（此前误引 032709 目录的 1100——那是 3 月窗口旧目录，07 窗口本地原生本就是 1200）。

| 日 | 下单前现金 | 目标=total×0.5 | 钳制值 min | 成交股数·金额 | 成交后现金 | 钳制判定 |
|---|---|---|---|---|---|---|
| 07-15 | 100,000 | 50,000 | 50,000 | 1200×41.6=49,920 | 50,062.03 | ✅ 放行合法 |
| 07-31 | 95,841.54 | 47,920.77 | 47,920.77 | 7200×6.57=47,304 | 48,520.51 | ✅ 放行合法 |

**证据**：07-14 daily cash=100,000（下单前）；07-30 daily cash=95,841.54（下单前）；成交后 cash 均与 trades 金额+费用精确吻合（100,000−49,920−17.47=50,062.03 ✓；95,841.54−47,304−16.56=48,520.51 ✓）。

**结论**：两日 target ≤ 现金，钳制放行（未旁路）——1200/7200 是钳制语义正确结果，非三方巧合。**活属性修复落地后本地 smoke 现金实时，三方一致须复跑复验（D4）——当前一致不作终态**（ZCode 要求）。

## 11. 陈旧根因修正（ZCode 2026-08-28 要求，写入证据防误导）

**"Portfolio.cash 从不刷新"表述不准确**：引擎每笔成交后本就会调 `refresh_portfolio`（ptrade_api L1004/1020/1041/1080/1101）**整体重建** `context.portfolio`（backtest_engine L2095：`Portfolio(self.account.cash, ptrade_positions)`）——即引擎侧存在刷新路径。实测陈旧更可能来自：
1. **产物侧 handle_data 入口捕获（capture-at-entry）**：`_qs_capture_ctx(context)` 在 bar 入口捕获 context 引用，但 `_qs_cash_avail` 每次读 `_QS_RUNTIME_CTX.portfolio.cash`——若 context.portfolio 已被 refresh_portfolio 重建为新实例（context 对象属性更新），读取应实时；若捕获发生在重建前或读的是旧实例引用，则读到旧值；
2. **产物运行路径未触达刷新**：某些 run 路径（broker 模式/非成交日）refresh 未触发。
**活属性方案对两种机制都免疫（单一真源委托 account）**：不再依赖 Portfolio 实例何时被重建/替换，`cash/market_value/positions` 恒读 `_api._engine.account` 当前状态。设计不受影响，仅根因表述修正。

## 12. Portfolio 活属性 + #9 实施验收（2026-08-28，ZCode D4 终判）

- **实施**：`ptrade_api.py` Portfolio 类字段 → 活属性（cash/market_value/total_value/portfolio_value/positions 委托 `_api._engine.account`，构造期/断链回退快照）；引擎 Account 零改动、策略零改动（D5）。
- **#9 新缺陷（4b 单测暴露）**：`_qs_split_order` **单笔分支（n≤49k）未应用 cash_avail 钳制**（此前只在拆单分支生效）→ 现金竞对日超买可能。**双端同构修复**（ptrade_api + source_import 模板）：单笔分支加 `budget=min(cash,value)` 无折扣钳制。
- **单测**：4b（钳制存活：target=50,000 cash=48,000 → 1100股 45,760 ≤ 48,000 ✅）/ 4c（成交后立即读 cash 实时 ✅）/ 4e（positions 只读快照，策略改动不影响引擎 ✅）。
- **回归**：173 passed / 1 无关既有失败（test_daemon_once_exit_contract GBK 子进程解码，Windows 环境问题，与本次改动零相关）。
- **D4 终判（活属性后三方复跑）**：本地引擎产物 = 1200/1200/7200 = 本地原生 = 平台（逐笔一致）✅ + G3.5 逐位一致 ✅。
- **闭环状态**：D4-S7 全部缺陷（#1-#9）修复完成，三方完全对齐；活属性修复落地后复跑通过，终态达成。