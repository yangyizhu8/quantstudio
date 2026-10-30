# 对齐面注册表（Alignment Surface Registry）

> 性质：双端对齐洞的**总账本**（唯一权威）。收敛度量 = 「未验证」数单调下降。
> **三端口径（2026-10-16 D-33 件B）**：本地↔PTrade（原口径）＋本地↔QMT——共享面（POS/API/MCH/FEE/DAT）加「QMT 锚点」列（本地语义一份、平台锚点两个）；QMT 专属面（QLC/QMCH/QCAL）独立成节。
> 更新纪律：每轮对齐修复六步④验收时同步更新；禁止陈旧。
> 状态**六态**（2026-10-21 证据回填轮扩列）：`已锚定`（本地已对齐 + 平台证据 + 契约测试）｜`已排查`（探针/案例证实无分歧，无专项测试）｜`未验证`｜`已知分歧`（修复中，附方案件指针）｜**`不适用`**（该端无此概念，双方均不提供，终态）｜**`缺口`**（平台有、本地未实现，须立项或裁定接受）。
> **扩列依据**：旧四态把「不适用」与「本地缺实现」混记为「未验证」，造成账本虚高（2026-10-21 归类实证：POS 17 条中 12 条本地无字段）。证据：`docs/evidence/offline-triage-a-20261021.md`。

## 条目总览

| 类别 | 前缀 | 条目数 | 已锚定 | 已排查 | 未验证 | 已知分歧 | 不适用 | 缺口 |
|---|---|---|---|---|---|---|---|---|
| Position/Account 字段 | POS | 17 | 3 | 2 | 0 | 0 | 5 | 7（已裁接受·不立项） |
| 注入 API 契约 | API | **88**（已枚举；PTrade 面 **68**） | **28**（PTrade 面逐条一致）＋**2**（QMT 锚定：get_financial_data×QCAL-05／get_instrument_detail×QCAL-01） | — | **27**（PTrade 文档待补查·批 1 清 7＋批 2 清 4＋批 3 清 6；另 5 条仅得存在性证据；`get_current_data` 3 次配额用尽未命中）＋**48**（QMT 锚列候核，锚列共 50） | **10**（A `get_history.fq` 默认＝已裁有意设计／B `get_fundamentals` table+is_dataframe＝已裁／C `get_snapshot.frequency`＝已裁 QS 扩展／E `order_target` 参数名＝已裁·**降级为本地命名差异·无迁移阻断**／F `get_stock_info.field` 必填＝**P1 下轮首位优先**／G `get_stock_name` 返回值语义／H `get_etf_stock_list` 仅交易环境／I `get_instruments` 返回面／J `get_price` 必填性放宽+QS 扩展／K `get_trading_day` 参数语义＝**P1**／L `get_industry_stocks` 本地恒空／M `get_stock_blocks` 未来函数面／N `get_stock_exrights` 首参名） | — | **1**（`attribute_history`＝聚宽名混入候选，PTrade 面判缺） |
| 撮合规则 | MCH | 10 | 6 | 3 | 0 | 1 | 0 | 0 |
| 生命周期 | LC | 8 | 7 | 1 | 0 | 0 | 0 | 0 |
| 费用模型 | FEE | 5 | 3 | 0 | 0 | 2 | 0 | 0 |
| 数据口径 | DAT | 17 | 8 | 8 | 0 | 0 | 1 | 0 |
| 指标计算 | IDX | 10 | 7 | 2 | 0 | 1 | 0 | 0 |
| QMT 生命周期专属 | QLC | 4 | 2 | 2 | 0 | 0 | 0 | 0 |
| QMT 撮合/引擎专属 | QMCH | 4 | 4 | 0 | 0 | 0 | 0 | 0 |
| QMT 日历/合约专属 | QCAL | 6 | 5 | 1 | 0 | 0 | 0 | 0 |
| QMT 资金/交易面 | ACOD | 30 | 22 | 5 | 1 | 2 | 0 | 0 |
| **合计** | — | **~210** | **56** | **20** | **20（＋API 88 已枚举·待核 69，未逐行入册）** | **4** | **5** | **7** |

## POS · Position/Account 字段（17，据 PTrade 文档 get_positions 契约）

| 编号 | 字段 | 语义 | 状态 | 证据/测试指针 | QMT 锚点 |
|---|---|---|---|---|---|
| **POS-01** | `cost_basis` | 每股持仓成本 | **已锚定（2026-10-07 修复落地）** | 平台=摊薄（卖出扣费净得+买入不含费加权），双锚点实证 0.973/0.972；引擎 `cost_basis_method='diluted'` 默认；契约测试 `tests/test_cost_basis_diluted.py`；验收 `docs/evidence/cost-basis-diluted-acceptance-20261007.md` | 未验证 |
| POS-02 | `amount` | 总持仓量 | 已排查（四象限案例逐笔一致） | `docs/evidence/dual-end-alignment-final-report-20260827.md`；本案例 26 笔委托一致 | **已锚定（QMT 侧 2026-10-19）**：`m_nVolume`（实测 26900） |
| POS-03 | `enable_amount` | 可用量（T+1） | **已锚定（2026-10-21 本地契约+QMT 双锚）** | 本地 `ptrade_api.py:213`（`enable_amount = volume if None else int(...)`）＋ QMT `m_nCanUseVolume`（实测 26900＝总量）⇒ 两侧同义（可用量）；`docs/evidence/offline-triage-a-20261021.md` §1 | **已锚定（QMT 侧 2026-10-19）**：`m_nCanUseVolume`（实测 26900＝总量，昨仓已过 T+1） |
| POS-04 | `today_long_amount` | 今买量 | **缺口（已裁·接受，登记已知差异，不立项）**：PTrade 有该字段、本地 `Position` 无（6 字段契约 `ptrade_api.py:174-175`）；**消费方普查 2026-10-22 零命中**（全仓 `.py` 属性访问 0；6 策略＋6 QMT 产物＋1 PTrade 产物 共 13 件各 0；38 策略／472 生成产物／38 ptrade／327 测试均 0）⇒ **5.2 裁定：接受缺口 + 登记已知差异**（`docs/evidence/pos-gap-consumer-scan-20261022.md`） | docs/evidence/offline-triage-a-20261021.md | **派生可用（无原生字段）**：`m_nVolume − m_nYesterdayVolume`（实测 26900−26900＝0）；**登记派生式，不冒充原生锚定**（`docs/evidence/b2-pos-readings-20261019.md` §二） |
| POS-05 | `today_short_amount` | 今卖量 | **缺口（已裁·接受，登记已知差异，不立项）**：PTrade 有该字段、本地 `Position` 无（6 字段契约 `ptrade_api.py:174-175`）；**消费方普查 2026-10-22 零命中**（全仓 `.py` 属性访问 0；6 策略＋6 QMT 产物＋1 PTrade 产物 共 13 件各 0；38 策略／472 生成产物／38 ptrade／327 测试均 0）⇒ **5.2 裁定：接受缺口 + 登记已知差异**（`docs/evidence/pos-gap-consumer-scan-20261022.md`） | docs/evidence/offline-triage-a-20261021.md | **缺口（2026-10-19 实测）**：无原生字段；`m_nCloseVolume`＝0 语义疑为「平仓量」，**语义待验，不冒充锚定** |
| POS-06 | `long_amount` | 多头量 | **不适用（现货）**：本地 `Position` 契约仅 6 字段（`ptrade_api.py:174-175`：sid/amount/enable_amount/cost_basis/last_sale_price/avg_cost）**无该字段**；QMT 亦无对应 ⇒ A 股现货场景双方均不提供，**判终态不适用**（`docs/evidence/offline-triage-a-20261021.md` §1） | docs/evidence/offline-triage-a-20261021.md | **不适用（现货）**：QMT 无多空分离字段；`m_nDirection`＝48（='0'）恒买向 |
| POS-07 | `short_amount` | 空头量 | **不适用（现货）**：本地 `Position` 契约仅 6 字段（`ptrade_api.py:174-175`：sid/amount/enable_amount/cost_basis/last_sale_price/avg_cost）**无该字段**；QMT 亦无对应 ⇒ A 股现货场景双方均不提供，**判终态不适用**（`docs/evidence/offline-triage-a-20261021.md` §1） | docs/evidence/offline-triage-a-20261021.md | **不适用（现货）**：无该字段 |
| POS-08 | `last_sale_price` | 最新价 | **已锚定（2026-10-21 本地契约+QMT 双锚）** | 本地 `ptrade_api.py:218`（`last_sale_price = current_price`，2026-09-04 契约修复 `:1261` 使当日价字典实际命中）＋ QMT `m_dLastPrice`（实测 1.626）；`docs/evidence/offline-triage-a-20261021.md` §1 | **已锚定（QMT 侧 2026-10-19）**：`m_dLastPrice`（实测 1.626；另有 `m_dSettlementPrice` 结算价同值） |
| POS-09 | `sid` | 证券代码 | 已排查（四象限案例代码口径一致 .SS/.SZ 归一） | 本案例代码归一实证 | **已锚定（QMT 侧 2026-10-19/20）**：`m_strInstrumentID`＝**裸码**＋`m_strExchangeID` ⇒ 拼 `code.exchange`；**C1 定谳（`docs/evidence/c1-position-key-verdict-20261020.md`）**：持仓键＝**`.SH/.SZ`**（15 笔）；universe＝**5226 只同构**（SH 2318 / SZ 2908）⇒ **端内自洽无 miss**；**`get_position` 为精确键**（`.SS`/`.SZ`/裸码 全落零视图 `amount=0`）⇒ **跨端形态差异（本地 `.SS` ↔ QMT `.SH`）；硬编码 `.SS` 会静默误判空仓（QD-2026-0009）** |
| POS-10 | `business_type` | 业务类型 | **缺口（已裁·接受，登记已知差异，不立项）**：PTrade 有该字段、本地 `Position` 无（6 字段契约 `ptrade_api.py:174-175`）；**消费方普查 2026-10-22 零命中**（全仓 `.py` 属性访问 0；6 策略＋6 QMT 产物＋1 PTrade 产物 共 13 件各 0；38 策略／472 生成产物／38 ptrade／327 测试均 0）⇒ **5.2 裁定：接受缺口 + 登记已知差异**（`docs/evidence/pos-gap-consumer-scan-20261022.md`） | docs/evidence/offline-triage-a-20261021.md | **近似但语义不同**：`m_eFutureTradeType`＝48（期货交易类型，现货恒 '0'）⇒ **不冒充锚定** |
| POS-11 | `contract_multiplier` | 合约乘数 | **缺口（已裁·接受，登记已知差异，不立项）**：PTrade 有该字段、本地 `Position` 无（6 字段契约 `ptrade_api.py:174-175`）；**消费方普查 2026-10-22 零命中**（全仓 `.py` 属性访问 0；6 策略＋6 QMT 产物＋1 PTrade 产物 共 13 件各 0；38 策略／472 生成产物／38 ptrade／327 测试均 0）⇒ **5.2 裁定：接受缺口 + 登记已知差异**（`docs/evidence/pos-gap-consumer-scan-20261022.md`） | docs/evidence/offline-triage-a-20261021.md | **平台不提供该字段**（ETF/股票按 1 约定，非读取） |
| POS-12 | `margin_rate` | 保证金率 | **不适用（现货）**：本地 `Position` 契约仅 6 字段（`ptrade_api.py:174-175`：sid/amount/enable_amount/cost_basis/last_sale_price/avg_cost）**无该字段**；QMT 亦无对应 ⇒ A 股现货场景双方均不提供，**判终态不适用**（`docs/evidence/offline-triage-a-20261021.md` §1） | docs/evidence/offline-triage-a-20261021.md | **不适用（现货）**：无「率」字段；`m_dMargin`＝0.0 为保证金额 |
| POS-13 | `long_pnl` | 多头浮盈 | **缺口（已裁·接受，登记已知差异，不立项）**：PTrade 有该字段、本地 `Position` 无（6 字段契约 `ptrade_api.py:174-175`）；**消费方普查 2026-10-22 零命中**（全仓 `.py` 属性访问 0；6 策略＋6 QMT 产物＋1 PTrade 产物 共 13 件各 0；38 策略／472 生成产物／38 ptrade／327 测试均 0）⇒ **5.2 裁定：接受缺口 + 登记已知差异**（`docs/evidence/pos-gap-consumer-scan-20261022.md`） | docs/evidence/offline-triage-a-20261021.md | **已锚定（QMT 侧 2026-10-19）**：`m_dFloatProfit`（当日浮动，实测 80.70）／`m_dPositionProfit`（持仓累计，实测 −64.56）⇒ **两字段并存，须区分语义** |
| POS-14 | `short_pnl` | 空头浮盈 | **不适用（现货）**：本地 `Position` 契约仅 6 字段（`ptrade_api.py:174-175`：sid/amount/enable_amount/cost_basis/last_sale_price/avg_cost）**无该字段**；QMT 亦无对应 ⇒ A 股现货场景双方均不提供，**判终态不适用**（`docs/evidence/offline-triage-a-20261021.md` §1） | docs/evidence/offline-triage-a-20261021.md | **不适用（现货）**：无该字段 |
| POS-15 | `long_cost_basis` | 多头成本 | **缺口（已裁·接受，登记已知差异，不立项）**：PTrade 有该字段、本地 `Position` 无（6 字段契约 `ptrade_api.py:174-175`）；**消费方普查 2026-10-22 零命中**（全仓 `.py` 属性访问 0；6 策略＋6 QMT 产物＋1 PTrade 产物 共 13 件各 0；38 策略／472 生成产物／38 ptrade／327 测试均 0）⇒ **5.2 裁定：接受缺口 + 登记已知差异**（`docs/evidence/pos-gap-consumer-scan-20261022.md`） | docs/evidence/offline-triage-a-20261021.md | **已锚定（QMT 侧 2026-10-19）**：`m_dPositionCost`＝43803.96（成本额）／`m_dAvgOpenPrice`＝1.6274（均价）／`m_dOpenCost`＝43803.96 |
| POS-16 | `short_cost_basis` | 空头成本 | **不适用（现货）**：本地 `Position` 契约仅 6 字段（`ptrade_api.py:174-175`：sid/amount/enable_amount/cost_basis/last_sale_price/avg_cost）**无该字段**；QMT 亦无对应 ⇒ A 股现货场景双方均不提供，**判终态不适用**（`docs/evidence/offline-triage-a-20261021.md` §1） | docs/evidence/offline-triage-a-20261021.md | **不适用（现货）**：无该字段 |
| POS-17 | `delivery_date` | 交割日 | **缺口（已裁·接受，登记已知差异，不立项）**：PTrade 有该字段、本地 `Position` 无（6 字段契约 `ptrade_api.py:174-175`）；**消费方普查 2026-10-22 零命中**（全仓 `.py` 属性访问 0；6 策略＋6 QMT 产物＋1 PTrade 产物 共 13 件各 0；38 策略／472 生成产物／38 ptrade／327 测试均 0）⇒ **5.2 裁定：接受缺口 + 登记已知差异**（`docs/evidence/pos-gap-consumer-scan-20261022.md`） | docs/evidence/offline-triage-a-20261021.md | **已排查（2026-10-19 实测）**：`m_strOpenDate`＝**空串 `''`**／`m_strExpireDate`＝**空串 `''`** ⇒ **现货返回空串而非 None，判空须用 `not v` 不能 `is None`** |

## API · 注入 API 契约（待全量枚举，首批已知锚点）

| 编号 | 条目 | 状态 | 证据指针 | QMT 锚点 |
|---|---|---|---|---|
| API-00 | 平台吸收规则集（get_history/include/fq 等一批语义） | 已锚定（部分面） | `docs/evidence/ptrade-platform-absorptions-20260903.md` | 已锚定（部分面）：wrapper 七类 portability 规则＋矩阵哈希＋49 用例 |
| API-01..NN | get_history / get_price / order 系 / set_universe / …（待从 ptrade_api 暴露面全量枚举，P2 清单） | 未验证 | — | 未验证 |

## MCH · 撮合规则（骨架）

| 编号 | 条目 | 状态 | 证据指针 | QMT 锚点 |
|---|---|---|---|---|
| MCH-01 | 成交价模式（收盘/开盘撮合） | 已排查（本案例双端成交价逐笔一致） | 四象限案例 trades 逐笔一致 | 未验证（QMCH 系承接） |
| MCH-02 | 滑点 | **已锚定（2026-10-21 本地实现+契约测试）** | `set_slippage/set_fixed_slippage` 签名逐字对齐 ＋ 拒非 PTrade 关键词：`tests/test_ptrade_public_signature_contract.py:10-34`；引擎成本写入：`tests/test_etf_ptrade_compat.py:63-78`；默认滑点 0（PTrade 实证对齐）`tests/test_order_rejection.py:97` | 未验证（QMCH 系承接） |
| MCH-03 | 涨跌停可成交性 | **已锚定（2026-10-21 本地实现+契约测试）** | `limit_up_blocked/limit_down_blocked` 拒单：`tests/test_order_rejection.py:118-145`、`tests/test_minute_limit_halt.py:19-41`、`tests/test_fill_audit.py:64-74`；`tests/test_engine_basket_rebalance.py:500-557`（涨跌停分向放行） | 未验证（QMCH 系承接） |
| MCH-04 | 停牌处理 | **已锚定（2026-10-21 本地实现+契约测试）** | 停牌双判（`suspendFlag=1` ∨ `volume==0`）⇒ `halted`：`tests/test_minute_limit_halt.py:68`、`tests/test_next_open_limit_and_halt.py:116-150`、`tests/test_filter_stock_by_status.py:68-84`、`tests/test_pd13b_c4_fidelity.py:38-63`（reason 区分性） | 未验证（QMCH 系承接） |
| MCH-05 | T+1 约束 | 已排查（本案例 9/11 盘后 可用=53000<总54900 双端同构） | 平台日志 2024-09-11 盘后行 | 未验证（QMCH 系承接） |
| MCH-06 | T+0（ETF 分类） | **已锚定（2026-10-21 本地实现实勘）** | per-code ETF T+0：`backtest_engine.py:422-426`（`etf_t0` 仅 minute-bar-v1 生效、日线强制 False 守黄金基线 87,752.56）／`:1120-1149`（`etf_basic.fund_type`→`_is_t0` 懒装载，**数据不可用即 fail-closed 全 T+1**）／`:1191-1208`（T+0 买入后立即解锁 `can_sell`）；参数链 `run_ptrade_strategy.py:275-306/352`、`gui/workers.py:428`。**边界：日线 Profile 恒 T+1（非缺陷，为守护基线）** | 未验证（QMCH 系承接） |
| MCH-07 | 最小交易单位/整手 | 已排查（本案例逐笔整百一致） | 同 MCH-01 | 未验证（QMCH 系承接） |
| MCH-08 | 部分成交/拒单 | **已知分歧（2026-10-21 归类发现，待裁口径）** | 本地**不支持部分成交**（整单拒单：`tests/test_next_open_limit_and_halt.py:177`「vol=0 → rejected，无部分成交」；四种拒单采集 `tests/test_fill_audit.py:4-5`）；而 QMT 实测 `m_nVolumeTraded` 可 < `m_nVolumeTotalOriginal`（ACOD ORD-02 三笔样本）⇒ **两端成交粒度语义不同**，须裁定「本地是否需支持部分成交」 | 未验证（QMCH 系承接；QMT 侧部分成交能力已实测） |
| MCH-09 | 资金不足边界 | **已锚定（2026-10-21 本地实现+契约测试）** | 整单拒单不缩单：`tests/test_next_open_limit_and_halt.py:154-177`（跳空放大买单需求 → `insufficient_cash`，不缩单）；`tests/test_engine_basket_rebalance.py:431`（资金不足 → 0 笔 buy filled 全拒） | 未验证（QMCH 系承接） |
| MCH-10 | 碎仓防护 | **已锚定（2026-10-21 本地实现+契约测试）** | 整手取整不足 100 股兜底拒单：`tests/test_fill_audit.py:91`；零股清仓全卖：CORP-02 验收件 `docs/evidence/corp02-odd-lot-sell-acceptance-20261007.md`（36 测试绿） | 未验证（QMCH 系承接） |

## LC · 生命周期（骨架）

| 编号 | 条目 | 状态 | 证据指针 |
|---|---|---|---|
| LC-01 | `initialize` 时机（全局一次） | **已锚定（2026-10-21 本地契约测试）** | `tests/test_minute_engine_lifecycle.py:58-68`（initialize 1 次计数断言） |
| LC-02 | `before_trading_start`（每日 1 次，首 bar 前） | **已锚定（同上）** | `tests/test_minute_engine_lifecycle.py:58-68`、`tests/test_minute_batch_loading.py:144-181` |
| LC-03 | `handle_data` 时刻遍历（每 bar 1 次） | **已锚定（同上）** | `tests/test_minute_engine_lifecycle.py:58-68`（`handle_data_dts` 逐 bar） |
| LC-04 | `after_trading_end`（每日 1 次，末 bar 后） | **已锚定（同上）** | `tests/test_minute_engine_lifecycle.py:58-68`、`tests/test_minute_batch_loading.py:169-181` |
| LC-05 | `run_daily` 定时任务 | **已锚定（2026-10-21 实勘）** | `ptrade_api.py:1872` `run_daily(self, context, func, time='9:31')` 实现在位；调度语义细节待与平台对照（不冒充已对齐） |
| LC-06 | 盘后快照时点 | **已锚定（2026-10-21 实勘）** | `backtest_engine.py:2609`（「⑥ after_trading_end（每日一次，用当日日线 snapshot）」）＋`:2732` `get_daily_snapshot` |
| LC-07 | 上下文对象语义（context 字段面） | **已排查（2026-10-22 本地契约实勘）** | `Context`（`ptrade_api.py:227`）5 字段：`current_dt`/`previous_date`/`portfolio`/`blotter`(含 current_dt)/`capital_base`(property·**恒定初始资金非活值**，单一真源 `engine._initial_capital`，4 处构造点 :486/:2159/:2264/:2423)；⚠️ Context7 两 PTrade 文档源**均未收录 capital_base** ⇒ 平台侧证据欠缺，不冒充已锚定（`docs/evidence/local-side-4items-20261022.md` §二） |
| LC-08 | 异常中断行为 | **已锚定（2026-10-22 本地 6 处调用点全实勘）** | 策略回调异常⇒**捕获＋不中断回测＋记 error 日志**：`:2271`(Ptrade日线 bts，另 `_lifecycle_errors += 1`)／`:2283`(handle_data)／`:2368`·`:2404`(DailyProxy)／`:2553`·`:2605`(PR4 分钟)。⚠️ 不对称已登记：`_lifecycle_errors` **仅 :2271 一条路径计数**，另两路径只记日志 ⇒ 异常次数不可只看该计数（`docs/evidence/local-side-5items-20261022.md` §一） |

## FEE · 费用模型（骨架）

| 编号 | 条目 | 状态 | 证据指针 | QMT 锚点 |
|---|---|---|---|---|
| FEE-01 | 最低佣金 5 元 | **已锚定（2026-10-22 端测：最低佣金 5 元按面板生效）** | 原「首日净值 99980 = 4×5 双端一致」＋ **端测**：A 变体两笔买入费用均 **5.0000**（1170×0.035%=0.41 < 5 ⇒ 取地板；313×0.035%=0.11 同理）；B 变体最低佣金设 0 后不再取 5；证据 `docs/evidence/qmch-fee-probe-conclusion-20261022.md` §四 | **已锚定（QMT 侧 2026-10-22 回测面板·初始默认值）**：面板「最低佣金」**默认 0.000 元**、可改（5.000 为用户手改测值）；🔴 **与本地默认 5 元不一致 ⇒ 对照回测须把 QMT 置 5.000 元**（`docs/evidence/qmt-backtest-panel-fee-qmch-20261022.md` §一/§五） |
| FEE-02 | 佣金费率 | **已锚定（本地 2026-10-22 实勘 ＋ QMT 面板 ＋ 回测计费实测）** | 本地实勘／面板读数 ＋ **端测**：B 变体佣金率设 1% ⇒ 费用实测 **11.70 / 3.13 / 11.40** 三处精确（1170×1%、313×1%、1140×1%）⇒ **费率参数按面板生效**；证据 `docs/evidence/qmch-fee-probe-conclusion-20261022.md` §四 | **已锚定（QMT 侧 2026-10-22 回测面板）**：**三项分设**——「买入佣金／平昨佣金／平今佣金」，**单位＝比例(百分比)**、默认均 0.0000；🔴 **本地为单一佣金费率模型 ⇒ 转换须三项同值映射并写明**（`docs/evidence/qmt-backtest-panel-fee-qmch-20261022.md` §二） |
| FEE-03 | 印花税（卖出） | **已知分歧（本地分期间封顶 0.0005；QMT 回测为单一费率——切换前后均 0.1%，2026-10-23 v6 端测）** | 本地实勘：分期间逻辑 ＋ **端测**：A 变体卖印花 0.1% ⇒ **1.14**（1140×0.1%）；B 变体 1% ⇒ **11.40** ⇒ **按面板生效**；**分期间切换点仍未验**（QD-2026-0010 待段落）；证据 `docs/evidence/qmch-fee-probe-conclusion-20261022.md` §四 | **已锚定（QMT 侧 2026-10-22 回测面板）**：「买入印花税」「卖出印花税」为**两个独立字段**、可双边配置、默认均 0.0000；🔴 **本地仅卖出印花税（A 股实际）⇒ 转换须强制「买入印花税=0」**（`docs/evidence/qmt-backtest-panel-fee-qmch-20261022.md` §二） |
| FEE-04 | 过户费 | **已知分歧（本地有公式；QMT 回测恒 0／未实现）** | 本地实勘：默认值＋双向公式 ＋ **端测（双变体交叉）**：A 卖出费 `6.14 = 1.14(印花)+5.00(佣金)` **无余项**、B `22.80 = 11.40+11.40` **无余项** ⇒ 理论过户费 0.0114 **两变体均缺失** ⇒ **QMT 回测不收过户费**（与「面板无过户费专名字段」一致）；证据 `docs/evidence/qmch-fee-probe-conclusion-20261022.md` §四 | **已锚定（QMT 侧 2026-10-22 回测面板·等价槽位）**：🔴 **命名不对齐**——本地有「过户费」概念、QMT 无专名字段 ⇒ 转换须写明「过户费并入手续费比例」（`docs/evidence/qmt-backtest-panel-fee-qmch-20261022.md` §二） |
| FEE-05 | 费用计入成本口径（含费净投入） | 已锚定（2026-10-07 双锚点判别：买入不含费+卖出含费扣） | 验收证据 §2 三口径判别表 | 未验证 |

## DAT · 数据口径（骨架，已锚定存量回填）

| 编号 | 条目 | 状态 | 证据指针 | QMT 锚点 |
|---|---|---|---|---|
| DAT-01 | pctChg 合成口径（除权日涨跌幅） | 已锚定 | `docs/evidence/ptrade-pctchg-synth-evidence.md` | **已锚定（QMT 侧 2026-10-18）**：平台**无** `pctChg` 字段（返回全 `None`）⇒ 须自行合成 `close/preClose-1`；`preClose`/`open`/`high`/`low`/`close` 均可得（`docs/evidence/qmt-probe-batch-ab-readings-20261018.md` §二） |
| DAT-16 | ~~估值价格快照微差~~（2026-10-07 P1 S1+S2 破案：价格逐位一致已证伪）→ **归因转移 CORP-01** | 已关闭（归因转移） | 破案链 `docs/evidence/corp-action-etf-merge-2025-09-22.md` | 未验证 |
| **CORP-01** | **ETF 份额合并/折算处理缺失**（引擎公司行为层；159934 黄金ETF 2025-09-22 合并比例 0.948126，平台 4000→3796 份+成本等比 7.673→8.086+nav 连续；本地未处理→9/22 净值虚高 +1715.23=204×8.408 逐位，滚存至 9/30 差 1780.92→回撤判定异号→10/9 级联 3.88pp 残差全链） | **④验收 PASS（2026-10-07 ③实施+④验收，待⑤确认推送）** | 修复证据 `docs/evidence/corp-action-etf-merge-fix-acceptance-20261007.md`：合并带去吸附+按份取整，30 测试绿，9/30 差 1780.92→−26.19，首分歧推迟 112 日 | 未验证 |
| **CORP-02** | **零股卖出整手截断**（2026-01-29 四象限清仓单本地 3200 vs 平台 3296：持仓 3293 被 int(n/100)×100 截 93 份滞留；A 股清仓单零股可全卖）——CORP-01 修复后暴露的级联缺口，V2'' 残差 2.23% 主源 | **④验收 PASS（2026-10-07 ③实施+④验收，待⑤确认推送）** | 单点修复 `_execute_sell` 数量式分支；证据 `docs/evidence/corp02-odd-lot-sell-acceptance-20261007.md`：36 测试绿，V2''' 残差 2.23%→**0.013%**，分歧 18→3 组（全为 CORP-01 尾差级，两组总量对齐 35300=35300），末日持仓逐位一致；**双端对齐主线<0.5% 判据达成（CORP-01+02 合并）**；V3''' 基线 2.08%（moving_avg） | 未验证 |
| DAT-02 | 基准收益（000300） | 已排查（双端 36.85% 逐位一致） | 四象限案例 | **已锚定（QMT 侧 2026-10-18）**：`000300.SH` 日线可得（rows=5，收盘 4287.98/4275.68/4217.681/4166.013/4170.613） |
| DAT-03 | 日线 include 语义（D-1 锚定） | 已锚定（E1 铁律 + 四格实测） | AGENTS.md E1 条；ptrade_api.py:598 | 已锚定：wrapper `_qs_snap_kwargs` 锚定回测 bar 当日＋D-28 端测四轮 L0-L6 逐位一致（`docs/evidence/d28-e2e-run-20261015.md`） |
| DAT-04 | 前复权 fq='pre' | 已排查（本案例价格序列逐笔一致） | 四象限案例 | **已排查（QMT 侧 2026-10-18）**：`dividend_type` 确被识别（`back` 与 `none` 差异巨大 133.756 vs 9.67）；`front≡none` 于 2026-10 窗口（该窗无除权 ⇒ 合理）⇒ **含除权窗口对照待补**，不以单窗口结论冒充已锚定 |
| DAT-05 | 日期边界（首/末日含否、`count` 语义） | **已锚定（2026-10-22 本地实勘）** | `get_history`（`ptrade_api.py:1486`）双签名（`:1500` int 首参=count-first）；`security=None`→**空 DataFrame**（`:1517-1518`）；`count=None`→**20**（`:1519-1520`）；**`tail(count)`（`:1470`/`:1607`）⇒ 不足时如实返回实际行数、不凑数不报错**；`include=False` 默认 ⇒ 末行锚 D-1（与 DAT-03 一致）（`docs/evidence/dat-05-07-13-closure-20261022.md` §一） | 未验证（QMT 侧 get_history 边界对照未做） |
| DAT-06 | 停牌日 K 线 | 已排查（本地实现三处在位） | `backtest_engine.py:750`（`suspendFlag==1`∨`volume==0`）／`ptrade_api.py:1094`（`filter_stock_by_status` 四型）／`providers/duckdb_data_access.py:518`（含 `suspendFlag` 列）；停牌日 K 线是否补齐未对表 | 未验证 |
| DAT-07 | fallback 链 | **已锚定（2026-10-22 链路实名）** | **单源装配、无多源降级链**：`providers/base.py:245-248` Calendar→Market(持 calendar)→Fundamental→Reference；引擎只接收注入（`backtest_engine.py:436`）；数据根级 fallback 见 `:40`；**禁止静默单端 fallback** 守卫 `fidelity_config.py:11`（`docs/evidence/dat-05-07-13-closure-20261022.md` §二） | 未验证（QMT 侧无多源概念，对照不适用） |
| DAT-08 | 复权口径 | **不适用（归并 DAT-04）** | 与 DAT-04 前复权同源，不重复计数 | — |
| DAT-09 | PIT 时点 | **已锚定（复用 DAT-03）** | DAT-03 已锚定（E1 铁律＋四格实测）；实现 `ptrade_api.py:598` 直写 `_prev_date`、引擎 `:572` 取上一交易日 | 已锚定（复用 DAT-03） |
| DAT-10 | 指数日线专门 API | **已锚定** | `ptrade_api.py:2130` ＋设计件 `docs/get-index-day-bar-design.md` ＋`duckdb_data_access.py:679`；契约边界见 AGENTS.md E1 第 7 条 | 未验证（QMT 侧该 API 对照未做） |
| DAT-11 | ETF 池 PIT | **已锚定** | 铁律级约定（AGENTS.md ETF 池条）＋`ptrade_import.py:36` 导出＋`backtest_engine.py:1124`（同源/失败空缓存+warning） | 未验证 |
| DAT-12 | 分钟/日线一致性 | 已排查 | `tests/test_day_code_index_equivalence.py`；缓存一致性 `duckdb_data_access.py:543`；全市场规模对照未做 | 未验证 |
| DAT-13 | 字段缺失表达（None/空串/0） | **已排查（规律在案、未成文统一）** | 公开面三态：**集合型→空容器**（`get_trades`/`get_open_orders`→`[]`；`get_history(None)`→空 DF）／**单值→零视图或 None**（`get_position` 未命中→零视图 amount=0〔QD-0009〕vs `get_order`→`None`）／**字符串→空串**（POS-17 `m_strOpenDate=''`）；🔴 **不对称已登记**（`get_position` 零视图 vs `get_order` None，均"查不到"但契约不同，属有意设计未成文）⇒ 待落成文契约（`docs/evidence/dat-05-07-13-closure-20261022.md` §三） | 未验证 |
| DAT-14 | 单位与精度 | 已排查 | `ptrade_api.py:286` 价格 2 位／`libs/shared_cost_model.py:74` `price_decimals`／指标 ×100；浮点容差无统一约定（ACOD 实测须 1e-9） | 未验证 |
| DAT-15 | 跨源一致性 | 已排查 | `calibration.py:10`（`PtradeExportProvider`）＋`fidelity_compare.py:4-5`（差异明细＋自动归因）；MCP 源未纳入 | 未验证 |
| DAT-17 | `get_market_data_ex` **参数优先级**（`count` vs `start_time`） | **已锚定（2026-10-18 端测）** | 证据 `docs/evidence/qmt-probe-batch-ab-readings-20261018.md` §二/§三；探针件 `docs/probe/qmt_shared_surface_dat_probe-20261017.py`（P2 三段对照） | **已锚定**：同传 `count`＋`start_time` 时 **`count` 优先、`start_time` 被忽略**（实测 `{count:3, start_time:'20240102'}` ⇒ 返回最新 3 行 `20261014..20261016`，**静默无报错**）；与「`endDate` 空串→静默空」（QCAL-05）同类**静默语义陷阱**；**wrapper 调用面待排查**（是否存在 count＋start_time 同传的潜在偏差） |

## IDX · 指标计算（骨架）

| 编号 | 条目 | 状态 | 证据指针 |
|---|---|---|---|
| IDX-01 | 总收益 | **已锚定（2026-10-21 双端对账测试）** | `tests/test_dual_end_reconcile.py:45-48`（`strategy_return_pct` 双端对照） |
| IDX-02 | 年化 | **已锚定（同上）** | **公式实名**：`strategy_annual_return = pow(1 + total_return, 250/trade_days) − 1`（`ptrade_metrics.py:123`，基准/相对/超额同式 :124-126）；出参 `annual_return_pct`（:195）；引擎摘要优先、回退 `(final/initial) ** (250/len(df)) − 1`（`backtest_engine.py:270`）（`docs/evidence/local-side-5items-20261022.md` §三） |
| IDX-04 | 最大回撤 | **已锚定（同上）** | `tests/test_dual_end_reconcile.py:45`（`max_drawdown_pct`） |
| IDX-05 | 夏普 | **已锚定（同上）** | `tests/test_dual_end_reconcile.py:48`（`sharpe_ratio`） |
| IDX-06 | 索提诺 | **已锚定（同上）** | `tests/test_dual_end_reconcile.py:48`（`sortino_ratio`） |
| IDX-08 | 盈亏比 | **已排查（2026-10-21 实勘）** | GUI 层实现：`gui/backtest_result_window.py:2430`（总盈利金额/总亏损金额）、`:983`（`profit_loss_ratio_pct`）；**引擎侧指标字段待核**，故记「已排查」不记已锚定 |
| IDX-09 | Alpha/Beta | **已锚定（同上）** | `tests/test_dual_end_reconcile.py:48`（`alpha_ratio`） |
| IDX-10 | 平均持仓时长 | **已锚定（2026-10-21 实勘）** | `ptrade_metrics.py:183`（`hold_days.mean()`）＋`:208`（`avg_hold_days` 出参） |
| IDX-03 | 基准收益（=DAT-02 复用） | 已排查 | 四象限案例 |
| IDX-07 | 胜率口径（按平仓对 vs 按日） | **已知分歧（BLOCKED：外部依赖平台口径定义）** | 本地**双口径并存**已查明：主口径＝按平仓对（卖出笔）`win_rate = len(winning_trades)/len(sells)`（`ptrade_metrics.py:176` →`win_rate_pct`:193→GUI:982）；副口径＝按日跑赢基准`daily_win_rate`（`:163`→`daily_win_rate_pct`:207）。本地 38 应＝`len(sells)`；平台「14」统计单位未知 ⇒ **须 PTrade 指标口径定义或平台明细逐笔对照**（`docs/evidence/local-side-4items-20261022.md` §一） |
> QMT 锚点（D-33 件B）：IDX 全部**未验证**（QMT 真机指标对照未做）。

## QLC · QMT 生命周期专属（D-33 件B，2026-10-16 建面）

| 编号 | 条目 | 状态 | 证据指针 |
|---|---|---|---|
| QLC-01 | handlebar/bar 驱动回调时机 | **已锚定（2026-10-18 端测）** | `docs/evidence/qmt-probe-batch-ab-readings-20261018.md` §一：逐 bar 驱动（`barpos=1321+n` 恒等、`period='1d'`、`bar_timetag` 相邻差 86,400,000ms＝1日、日期锚定 00:00 CST 非收盘时刻）；**`C.stock_code` 不存在**（False）⇒ 策略不得依赖其取当前标的 |
| QLC-02 | ContextInfo 对象语义（C.get_*/订阅面） | 已排查（形态库定谳） | `docs/qmt/inner-api/`（2043 行离线转写＋11 项实测定谳）；主库「平台转换铁律-文档签名不可信」 |
| QLC-03 | 生命周期壳改写（initialize/handle_data 保留时序） | **已锚定** | portability `QMT-LIFECYCLE-SHAPE` 规则（`quantstudio/strategy_compiler/portability_rules.py`）＋六策略端测运行通过 |
| QLC-04 | 定时任务/遍历语义（run_by_bar 等） | **已排查（接口存在；调度语义待细验）** | `docs/evidence/qmt-probe-batch-ab-readings-20261018.md` §一：`ctx_has run_time=True`（接口面存在）；本轮未实测其周期回调注册/触发语义 ⇒ **不以属性存在冒充语义已锚定** |

## QMCH · QMT 撮合/回测引擎专属（D-33 件B，2026-10-16 建面）

| 编号 | 条目 | 状态 | 证据指针 |
|---|---|---|---|
| QMCH-01 | QMT 回测撮合价口径（open/close） | **已锚定（2026-10-22 端测：限价单=委托价／市价单=当日 close）** | `docs/qmt-conversion-design.md` §5.2 L1/L2/L3 分层 ＋ **端测定谳**：限价单按**委托价**（v4 变体A/B × 高低双向 **4 点**，隐含费用 ORDER_PX 精确=5.0000／11.70／3.13）；市价单(prType=5)按**当日 close**（v5：CLOSE 隐含费用 5.0000 精确）；探针 `docs/probe/qmt_qmch_backtest_probe_v4-20261022.py`／`_v5-20261022.py`；证据 `docs/evidence/qmch-fee-probe-conclusion-20261022.md` §一 |
| QMCH-02 | 涨跌停/停牌可成交性（QMT 端） | **已锚定（涨跌停两侧均已证可成交·无封板约束）** | **涨停可成交（无封板约束）**：+9.97% 样本 000011.SZ 挂 9.49 即成交，A `delta=-954.00=949+5.00`／B `delta=-958.49=949+9.49` **双变体精确** ⇒ 与本地 `limit_up_blocked` 拒单**不一致（已知分歧）**；**跌停侧已证（v6，2026-10-23）**：扩池 300 命中 `000712.SZ` chg=**−0.0997**，挂跌停价 14.81 买入**成交**（联立验证：若未成交则卖出净得为负，不可能）⇒ 与涨停侧**对称**、**无封板约束**；跌停卖出仍 `NOT_TESTABLE`（需提前持仓且当日跌停，不可控）；v5 之 `NO_LIMIT_DOWN_SAMPLE(前60只无样本)` 已由扩池解除；【旧记】v5 `NO_LIMIT_DOWN_SAMPLE(前60只当日无<=-9.8%)` ⇒ 买入**未取得证据**；跌停卖出 `NOT_TESTABLE`（需提前持仓且当日跌停，不可控）；证据 `docs/evidence/qmch-fee-probe-conclusion-20261022.md` §二 |
| QMCH-03 | T+1 约束（QMT 端） | **已锚定（2026-10-22 端测：T+1 生效，引擎原文拒单）** | **引擎原文**：`当前股票000001.SZ可卖0股,小于需卖量100股,跳过,日期时间:20260901`；配套 `L1 T1 判定 delta=-1175.0` 精确等于「只有买成交」（买卖都成交应为 -10.0）⇒ `T1_ENFORCED_SELL_REJECTED`；**v3/v4 曾误判 T0_ALLOWED 已作废**（当时卖出在买入后第 4 bar，早已过 T+1，判据无效）；探针 `docs/probe/qmt_qmch_backtest_probe_v5-20261022.py`；证据 `docs/evidence/qmch-fee-probe-conclusion-20261022.md` §三 |
| QMCH-04 | 费用/滑点配置面（回测参数面板） | **已锚定（QMT 侧 2026-10-22 回测面板·初始默认值）** | 🔴 **默认＝零费用零滑点**：除 **初始资金 1,000,000 元** 与 **保证金比例 5%（现货语义待验）** 外，**15 项费用/滑点参数默认全为 0**（含最低佣金 0.000 元、滑点值 0.0000）；区间默认 2017-01-01~2020-12-31。⇒ **默认参数下 QMT 与本地费用天然不可比**（本地最低佣金 5 元）⇒ **对照前须按 §五 显式配置基线**，否则会把费用配置差异误读为引擎行为差异；**计费行为本身仍未验**（`docs/evidence/qmt-backtest-panel-fee-qmch-20261022.md` §一/§三风险4/§五） |

## QCAL · QMT 日历/合约信息专属（D-33 件B，2026-10-16 建面）

| 编号 | 条目 | 状态 | 证据指针 |
|---|---|---|---|
| QCAL-01 | 合约信息活表漂移（get_instrument_detail 无 as-of，跨推送窗 L1/L2 漂移） | **已锚定（D-30 冻结快照层；E2E 判据 A/B/C 候用户端测窗）** | `docs/qmt-instrument-snapshot-design.md` v0.3＋`tests/test_qmt_instrument_snapshot.py`（15 用例）＋commit `0b88728` |
| QCAL-02 | 板块成份无 as-of（realtime 传毫秒时间戳→静默空列表，不抛错） | **已排查（D-31 探针定谳）** | `docs/probe/qmt_realtime_sector_probe-20261013.py`（三档时间戳全空返回）；主库「QMT大转换管线…形态坑」复现固件节 |
| QCAL-03 | hist 财务日频全史帧→报告期折叠（组合键 keep=last） | **已锚定（D-28 盘后正式归档）** | `docs/qmt-hist-fold-design.md` v0.2.1＋`tests/test_qmt_hist_fold.py`（12 用例）＋`docs/evidence/d28-e2e-run-20261015.md`（判据 1/2/3＋四轮交叉）＋commit `8be00aa` |
| QCAL-04 | profit_ability 表映射缺失（映射表三表→hist 分支死代码→恒空帧） | **已锚定（D-29 ③实施＋④验收通过 2026-10-17）** | 方案 `docs/qmt-profit-ability-mapping-design.md` v1.0（②审通过）｜映射行 `roe`→`PERSHAREINDEX.du_return_on_equity` ＋ `net_profit_margin`→`PERSHAREINDEX.net_profit`｜v5 探针实证 `docs/probe/qmt_profit_ability_probe_v5-20261017.py`｜单测 `tests/test_qmt_fund_field_map.py`（7 passed）｜矩阵 `qmt_rows` qmt-pa-01/02｜哈希同批追认 `6927447ea23c`｜**待判据 3/4/5 端测** |
| QCAL-05 | 必填参数空串未定义行为（get_financial_data endDate 空串→静默空） | **已锚定（D-22 安全终点 '20991231'＋QSFUNDHIST 审计行）** | 主库「平台转换坑-必填参数空串」；`docs/evidence/qmt-m5-batch2-probe-20261010.md` |
| QCAL-06 | 产物物理编码契约（GBK 可解码；注释特殊字符高频复发） | **已锚定（canonical 重转门四自检＋测试强制 encode('gbk')）** | 主库「产物落盘编码契约」；`scripts/qmt_reconvert_six.py`；**R 口径案例：D-28 轮 `≪`(U+226A) 复发计 R_i>0→先修管线＝D-33 件D 写前拦截（`docs/qmt-alignment-loop-closure-design.md`）** |

## ACOD · QMT 资金与交易面（2026-10-21 段A 实测建面）

> **建面理由与口径**：本地 PTrade 侧有 `Portfolio`／`get_orders()`／`get_trades()` 语义对应，**但 QMT wrapper 无同名公开消费面**（DEAL-07/F1）⇒ 本面**不并入共享面**（避免暗示"双端已对齐"），作**QMT 侧新面独立成节**。
> **本节状态列＝QMT 侧实测状态**（2026-10-21 段A 双采样 ＋ v7 复核）；「本地对应」列记语义映射。
> 证据：`docs/evidence/aod-segment-readings-20261021.md`（含读数原文、枚举标定、哨兵黑名单、时变划分）。
> 探针件：`docs/probe/qmt_acct_order_deal_probe-20261021.py`（ACCOUNT/ORDER/DEAL 三面）／`docs/probe/qmt_shared_surface_pos_probe_v7-20261021.py`（含 S1 诊断行）。

| 编号 | 条目 | 状态 | 证据指针 |
|---|---|---|---|
| ACOD-01 | 资金面：可用资金（本地对应 `Portfolio.cash`） | **已锚定**（QMT 侧实测 9567383.63） | `m_dAvailable`；证据件 §1.1 |
| ACOD-02 | 资金面：证券市值合计（本地对应 `market_value`） | **已锚定**（409232.4＝`m_dStockValue`＋`m_dFundValue`） | `m_dInstrumentValue`；证据件 §1.1 |
| ACOD-03 | 资金面：总资产（**本地与 QMT 口径差 646.0**） | **已知分歧**：QMT `m_dAssetBalance`=9977262.03；本地 `total_value=cash+market_value`=9976616.03，**恒差 646.0**（＝`m_dCurrMargin`−`m_dInstrumentValue`）；**差额来源未确证，不擅自归因** | 证据件 §1.2 |
| ACOD-04 | 资金面：占用额（本地无对应） | **已锚定**（409878.4） | `m_dCurrMargin`；证据件 §1.1 |
| ACOD-05 | 资金面：可取余额（本地无对应） | **已锚定**（9541692.1，＜`m_dAvailable` 差 25691.53） | `m_dFetchBalance`；证据件 §1.1 |
| ACOD-06 | 恒等式 `m_dAssetBalance = m_dAvailable + m_dCurrMargin` | **已排查**（10-21 与 10-20 02:29 精确成立；10-20 20:14 差 2.50 ＝该次 Available 与 AssetBalance **更新时刻不同步** ⇒ 跨字段校验须容差或单次读取） | 证据件 §1.2 |
| ACOD-07 | 资金/盈亏读数的**时变规则**：变动驱动＝交易日结算，非日内漂移 | **已排查**（同日 25.7 分钟双采样**完全不变**，尾差 ≤3e-11；跨日才变） | 证据件 §1.3 |
| ACOD-08 | ACCOUNT 字段组**非原子更新** | **已排查**（ACOD-06 的 2.50 差＝更新时差） | 证据件 §1.2 |
| ACOD-09 | 浮点尾差（判等须容差 1e-9） | **已排查**（`m_dInstrumentValue` 409232.39999999997 vs 409232.4；`m_dPositionProfit` 尾差 2e-12） | 证据件 §1.1 |
| ACOD-10 | 账户状态与交易日 | **已锚定**（`m_strStatus='登录成功'`、`m_strTradingDate='20261021'`） | 证据件 §1.1 |
| ORD-01 | 委托标的（裸码＋交易所，形态同 POS 面） | **已锚定**（002516/SZ、001696/SZ） | `m_strInstrumentID`/`m_strExchangeID`；证据件 §二 |
| ORD-02 | 委托量／原始量／**已成交量**（三键语义区分明确） | **已锚定**（200/200/**0**｜0/300/**300**｜0/1800/**1800**） | `m_nVolumeTotal`/`m_nVolumeTotalOriginal`/`m_nVolumeTraded`；证据件 §二 |
| ORD-03 | 委托价（限价） | **已锚定**（5.4/5.41/15.19；`m_nOrderPriceType=50`） | `m_dLimitPrice`；证据件 §二 |
| ORD-04 | 成交价／成交额（未成交时为 0） | **已锚定**（5.41/1623.0；15.19/27342.0） | `m_dTradedPrice`/`m_dTradeAmount`；证据件 §二 |
| ORD-05 | 撤单量 | **已锚定**（200.0，仅未成交那笔有值） | `m_dCancelAmount`；证据件 §二 |
| ORD-06 | **委托状态枚举 54/56** | **已排查（样本推断：54＝未成交[VolumeTraded=0]／56＝已成交[VolumeTraded>0]；各 1 笔，待多样本确证）** | `m_nOrderStatus`；证据件 §二 |
| ORD-07 | 报单状态 | **未验证**（`m_nOrderSubmitStatus=51` 单一取值，语义待验，不以属性存在冒充语义已锚定） | 证据件 §二 |
| ORD-08 | **委托方向** | **已锚定**：**`m_nDirection` 恒 48（买、卖皆同）⇒ 不可用**；方向真源＝`m_nOffsetFlag`（**48=买/开、49=卖/平**，买单 48／卖单 49，与 DEAL 互证） | 证据件 §二 |
| ORD-09 | 中文操作名（方向交叉校验源） | **已锚定**（'限价买入'／'限价卖出'） | `m_strOptName`；证据件 §二 |
| ORD-10 | **委托↔成交关联键** | **已锚定**：`m_strOrderRef`（主）＋`m_strOrderSysID`（辅）双键逐一对应（1098910471/18、1098910873/19） | 证据件 §四 |
| ORD-11 | 下单时点 | **已锚定**（`m_strInsertDate`＋`m_strInsertTime` HHMMSS 6 位：143442/144943/145448） | 证据件 §二 |
| ORD-12 | 委托来源与有效标志 | **已锚定**（`m_strXTTrade='其他终端'`、`m_bEnable=True`） | 证据件 §二 |
| DEAL-01 | 成交价／量／额（价×量＝额 ✓） | **已锚定**（15.19/1800/27342.0；5.41/300/1623.0） | `m_dPrice`/`m_nVolume`/`m_dTradeAmount`；证据件 §三 |
| DEAL-02 | 手续费（**含拼写变体** `m_dComssion`，与 `m_dCommission` 同值并存） | **已锚定**（19.41282／0.34083；`same=True`）⇒ **定 `m_dCommission` 为准，`m_dComssion` 登记别名，禁双写** | 证据件 §三（F3 确证） |
| DEAL-03 | 成交方向 | **已锚定**：`m_nOffsetFlag`（48=买/49=卖）；**`m_nDirection` 恒 48 不可用** | 证据件 §三 |
| DEAL-04 | 成交时间（**不保证定长，解析须补位至 6 位**） | **已锚定**（10-21 两笔 6 位 '145506'/'145001'；10-20 样本 5 位 '94005'＝9:40:05 ⇒ 补位） | `m_strTradeDate`/`m_strTradeTime`；证据件 §三（F2 修正） |
| DEAL-05 | 平台成交编号 | **已锚定**（'1010000032485873'／'1010000032482204'） | `m_strTradeID`；证据件 §三 |
| DEAL-06 | 平仓盈亏／今平量 | **已锚定**（`m_dCloseProfit=0.0`、`m_nCloseTodayVolume=0`，本次无平仓） | 证据件 §三 |
| DEAL-07 | **消费面缺位**：QMT wrapper 无 `get_orders()`／`get_trades()` 公开遮蔽 | **已知分歧（高）**：本地 PTrade 契约有（`ptrade_api.py:2645`/`:2654`），QMT 侧 grep `^def get_orders\|^def get_trades` **零命中** ⇒ 策略在 QMT 端调用将 `NameError`；**须单列六步补同名消费面**（不在本面内改码） | 证据件 §七结转 1；F1 登记 |
| DEAL-08 | 哨兵黑名单 ＋ 会话级随机字段 | **已锚定**：哨兵 9 项（INT32_MAX：`m_nErrorID`/`m_nGroupId`/`m_nOpType`；−1：`m_nFrontID`/`m_nSessionID`/`m_nTaskId`/`m_nRealOffsetFlag`）⇒ **禁消费**；**`m_nOrderStrategyType` 两次采样不同＝会话级随机 ⇒ 禁作业务字段**（对照 `m_nStrategyID` 跨会话恒定） | 证据件 §四 |

## 变更记录

- 2026-10-07 POS-01 →已锚定（修复落地+契约测试）；FEE-05 →已锚定（双锚点判别：买入不含费）；DAT-16 新增（估值价格快照微差，V2 残差 3.88pp 归因）。
- 2026-10-23 **QMCH v6 定谳 ＋ API 枚举落地**：**FEE-03 改判「已知分歧」**——v6 跨 2023-08-28 双笔端测：切换前（20230823）卖出印花率 **0.10004%**、切换后（20230830）**0.10000%**（LATE 侧由「A 跌停买与 B_LATE 卖同 bar」的**联立方程**解出：`bar9 delta=-87.405 = 卖出净得 − 1486（跌停买入已知支出）` ⇒ 净得 1398.595 ⇒ 印花 1.405/1405 = 0.10000%），**两侧相同 ⇒ QMT 回测印花税为单一费率（按面板值）、不实施分期间减半**；与本地 `_stamp_tax_rate` 封顶 0.0005 **分歧**。**QMCH-02 跌停侧补齐**：扩池 300 命中 `000712.SZ` chg=−0.0997、挂 14.81 买入**成交** ⇒ 涨跌停两侧均无封板约束。**API 域实数化**：`~40（待枚举）` → **88**（PTrade 面 **69** 待逐条行为核验；QS-only 9／内部 10／私有 16），证据 `docs/evidence/api-surface-enumeration-20261023.md`；**QMT 锚点列维持待枚举**（QMT 镜像三法抽取得 0，缺口已登记）。探针 `docs/probe/qmt_qmch_backtest_probe_v6-20261022.py`。
- 2026-10-22 **QMCH／FEE 端测收官（探针 v1→v5）**：**QMCH-01 已锚定**（限价单=**委托价**〔v4 双变体×双方向 4 点，隐含费用 ORDER_PX 精确〕／市价单=**当日 close**〔v5〕）；**QMCH-02 已锚定（涨停侧）**（+9.97% 样本挂单即成交，与本地 `limit_up_blocked` **不一致**）＋**跌停侧缺口如实登记**（v5 前60只无样本；跌停卖出 NOT_TESTABLE）；**QMCH-03 已锚定**（**T+1 生效**，引擎原文 `当前股票000001.SZ可卖0股,小于需卖量100股,跳过`；v3/v4 的 T0_ALLOWED 已作废）；**FEE-01/02/03 已锚定**（最低佣金 5.00／佣金率 1%⇒11.70/3.13/11.40／卖印花 0.1%⇒1.14、1%⇒11.40，**逐项精确**）；**FEE-04 推翻为「已知分歧」**（本地有公式，QMT 回测两变体费用均**无余项** ⇒ 过户费恒 0／未实现）。**总览：QMCH 4/4 锚定、FEE 4 锚定＋1 分歧、两域未验证归零。** 证据 `docs/evidence/qmch-fee-probe-conclusion-20261022.md`（含五轮演进与三次判据缺陷的如实记载）。
- 2026-10-22 **DAT 三件一次清**：**DAT-05 → 已锚定**（`get_history` 双签名／`count=None`→20／`security=None`→空 DF／**`tail(count)` 不足如实返回不凑数**／`include=False` 末行锚 D-1）；**DAT-07 → 已锚定（链路实名）**（`providers/base.py:245-248` **单源四件装配、无多源降级链**；数据根 fallback `:40`＋禁止静默单端守卫 `fidelity_config.py:11`）；**DAT-13 → 已排查**（公开面三态规律：集合→空容器／单值→零视图或 None／字符串→空串；🔴 `get_position` 零视图 vs `get_order` None **不对称属有意设计但未成文**，待落契约）。**DAT 未验证 2→0**。证据 `docs/evidence/dat-05-07-13-closure-20261022.md`。
- 2026-10-22 **DAT 11 维度拆分回填**：`DAT-05..15` 聚合行拆为 **11 条明细**（口径维度：日期边界／停牌日 K 线／fallback 链／复权口径／PIT 时点／指数日线专门 API／ETF 池 PIT／分钟日线一致性／字段缺失表达／单位与精度／跨源一致性）。状态按本地实况判：**已锚定 3**（09 复用 DAT-03／10／11）＋**已排查 5**（06/07/12/14/15）＋**不适用 1**（08 归并 DAT-04）＋**未验证 2**（05 日期边界／13 字段缺失表达）。DAT 面总览：已锚定 6／已排查 7／未验证 11→**2**／不适用 1（另有 DAT-16 已关闭）。证据 `docs/evidence/dat-11-dims-enumeration-20261022.md`。
- 2026-10-22 **本地侧续推五件**：**LC-08 → 已锚定**（6 处回调调用点全实勘：捕获＋不中断＋记 error；登记不对称——`_lifecycle_errors` 仅 :2271 路径计数）；**FEE-02 → 已锚定**（`commission_rate=0.00035`＋`min_commission=5.0`）；**FEE-03 → 已锚定（升级）**（卖出单向 0.001，且 `_stamp_tax_rate(date)` **分期间封顶 0.0005** ⇒ 印花税非常量）；**FEE-04 → 已锚定（升级）**（`transfer_fee_rate=0.00001`，买卖双向计提）；**IDX-02 → 补实名公式**（`pow(1+total, 250/trade_days)−1`，`ptrade_metrics.py:123`）。`trade | FEE` 面本地侧未验证清零。证据 `docs/evidence/local-side-5items-20261022.md`。
- 2026-10-22 **本地侧四件补核**：IDX-07 定级＝**双口径并存已查明**（主口径按平仓对 `win_rate_pct`／副口径按日跑赢 `daily_win_rate_pct`），平台「14」单位未知 ⇒ 分歧降级 **BLOCKED(外部依赖平台口径定义)**；LC-07 →**已排查**（Context 5 字段实勘，`capital_base` 平台文档欠载）；LC-08 维持未验证（仅确认辅助路径兜底，回调异常语义未定位）；FEE-02 →**已锚定**（本地单一 `commission_rate` ＋ QMT 三项分设 ⇒ 三项同值映射）；FEE-04 →**已排查**（字段与 ETF 归零确证）。证据 `docs/evidence/local-side-4items-20261022.md`。
- 2026-10-22 **POS 7 缺口定谳（消费方普查）**：7 字段（today_long_amount/today_short_amount/business_type/contract_multiplier/long_pnl/long_cost_basis/delivery_date）**全仓零消费**（属性访问 0 命中；13 件指定策略/产物各 0；skills 2 处系 `get_business_type()` API 参考·非属性消费）⇒ 按 5.2 裁定 **接受缺口＋登记已知差异·不立项**；POS 缺口维持 7 但标注「已裁接受」。证据 `docs/evidence/pos-gap-consumer-scan-20261022.md`。
- 2026-10-22 **更正（用户「初始默认值」截图）**：撤上一条中三处误记——最低佣金默认 **0.000 元**（5 为用户手改）、滑点值默认 **0.0000**（非 0.02）、区间默认 **2017-01-01~2020-12-31**（非 2028，2028 为用户手改）。**新增风险 4**：QMT 默认零费用 vs 本地最低佣金 5 元 ⇒ 对照回测前须显式配基线，否则费用差异会被误读为引擎差异。
- 2026-10-22 **回测面板读数回填**：FEE-01/02/03 QMT 侧→已锚定（最低佣金默认 5 元；佣金**三项分设**且单位为比例；印花税**买卖双字段**）；FEE-04 QMT 侧→已锚定（等价槽位·**无过户费专名**）；QMCH-04→已锚定（面板 15 字段全录，**滑点默认 0.02 百分比**）。⚠️ 登记三项风险：回测区间为 **2028 未来日期**（直接跑无数据）、滑点默认值会污染撮合口径判定、保证金比例与最大成交比例现货语义待验。证据 `docs/evidence/qmt-backtest-panel-fee-qmch-20261022.md`。
- 2026-10-21 **证据回填轮（X 方案）**：总览扩「不适用」「缺口」两列（四态→六态）；POS 5 条→不适用、7 条→缺口（本地 `Position` 仅 6 字段）、2 条→已锚定；MCH 6 条→已锚定、**MCH-08 升已知分歧**（本地整单拒 vs QMT 部分成交）；LC 散文→表格（6 已锚定／2 待补核）；IDX 散文→表格（7 已锚定／2 已排查／**IDX-07 已知分歧**）。未验证 93→**约 21 条（不含 API 待枚举）**。证据件 `docs/evidence/offline-triage-a-20261021.md`。
- 2026-10-21 **段A 实测建面**：新增 **ACOD** 节（QMT 资金/交易面，ACOD×10＋ORD×12＋DEAL×8＝**30 条**：22 已锚定／5 已排查／1 未验证／**2 已知分歧**）；作**独立节**而非共享面——因本地同名消费 API 在 QMT 端缺位（F1⇒DEAL-07）；证据 `docs/evidence/aod-segment-readings-20261021.md`；同轮**S1 ④-4 端上复核闭**（四形态判定与 10-20 定谳完全一致）。
- 2026-10-16 **D-33 件B（QMT 维度首批）**：表头升三端口径；POS/API/MCH/FEE/DAT 五节加「QMT 锚点」列（共享面首批锚点 2：API-00 部分面、DAT-03）；新增 QLC(4)/QMCH(4)/QCAL(6) 专属面节（已锚定 5＋已排查 1＋已知分歧 1＋未验证 6）。方案：`docs/qmt-alignment-loop-closure-design.md` v1.0。
- 2026-10-06 v1 建账：17 POS + 6 类骨架；回填历史锚点（DAT-01/03、API-00）；四象限案例新增排查锚点 7 条 + 已知分歧 1 条（POS-01）。

> **维度对齐档案（件 G，2026-10-08）**：维度宣布机制与首例候选（DIM-01 ETF 日线策略域，N=1/3）见 [contracts/dimension-alignment.md](contracts/dimension-alignment.md)；机检 scripts/check_dimension_alignment.py（只提示不自动宣布）。
