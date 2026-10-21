# 本地侧补核四件（IDX-07／LC-07／LC-08／FEE-02·04 · 2026-10-22）

- **触发**：总调度令「IDX-07 定级／LC-07·08／FEE-02·04 本地侧自行推进照进」
- **性质**：只读取证 ＋ 账本回填；**零代码改动**

## 一、IDX-07 胜率口径 —— 本地**双口径并存**（核心发现）

| 口径 | 代码 | 定义 | 出参 |
| --- | --- | --- | --- |
| **主口径：按平仓对（卖出笔）** | `ptrade_metrics.py:176` | `win_rate = len(winning_trades) / len(sells)` | `win_rate_pct`（:193）→ GUI「胜率」直显（`backtest_result_window.py:982`） |
| 副口径：按日跑赢基准 | `ptrade_metrics.py:163` | `daily_win_rate = (strategy_returns > benchmark_returns).mean()` | `daily_win_rate_pct`（:207） |

**定级结论（如实）**：
- 本地**确实存在两条口径**，且 GUI 默认展示的是「按平仓对」的 `win_rate_pct`（38 笔的来源应为 `len(sells)`）；
- 平台侧"14 笔"的**统计单位未知**（可能：按平仓对但剔除 ETF 对冲腿／按有效交易区段／按盈利区间）——**本地代码无法判定**；
- ⇒ 分歧状态由「待探针验证后定级」改为 **`BLOCKED(外部依赖：平台指标口径定义)`**，解除条件＝取到 PTrade 胜率指标定义文档或平台明细逐笔对照。

## 二、LC-07 上下文对象语义 —— **本地契约已实勘**

`Context`（`ptrade_api.py:227`）字段面共 **5 项**：

| 字段 | 语义 | 证据 |
| --- | --- | --- |
| `current_dt` | 当前 bar/日 Timestamp | `:230` |
| `previous_date` | 前一交易日 Timestamp | `:231` |
| `portfolio` | Portfolio 对象（cash/market_value/total_value） | `:232` |
| `blotter` | 动态对象，含 `current_dt`（兼容 `context.blotter.current_dt.strftime()`） | `:234` |
| `capital_base` | **恒定初始资金（非活值）** property | `:236-259`；单一真源 `engine._initial_capital`；4 处构造点 `:486/:2159/:2264/:2423` |

**⚠️ 平台文档欠载（已登记于代码注释）**：Context7 两 PTrade 文档源（`/websites/ptradeapi`、`/kay-ou/ptradeapi`）**均未收录 `capital_base` 条目** ⇒ 本地语义依据「策略内契约声明＋引擎初始资金口径」，非平台文档。
⇒ 判定 **已排查（本地契约实勘完整；平台侧证据欠缺，不冒充已锚定）**。

## 三、LC-08 异常中断行为 —— **仍未定位（不冒充）**

已确认的仅是**辅助路径兜底**：`backtest_engine.py:704` `except Exception: pass`（数据层诊断汇总自身异常不得引入新失败路径）。
**策略回调（handle_data / before_trading_start 等）抛异常时引擎的行为**——是否中断回测、是否记录、是否继续下一 bar——**未定位到调用点证据**。
⇒ 维持 **未验证**，下一步：定位 `_run_ptrade_strategy` 内回调调用处（`handle_data` 等）的异常处理分支。**不以"代码里有很多 except"冒充语义已对齐**。

## 四、FEE-02 佣金费率（本地侧）

`set_commission(**kwargs)`（`ptrade_api.py:781-797`）：
- `commission_ratio` → `cost.commission_rate`（:791）
- `min_commission` → `cost.min_commission`（:793）
- `type='ETF'` ⇒ **`stamp_tax_rate = 0.0` / `transfer_fee_rate = 0.0`**（:796-797，"贴近场内 ETF 交易口径"）

⇒ **本地侧已锚定**：佣金费率可配、单一 `commission_rate` 模型成立。
**与 QMT 的对齐关系**：本地单一费率 ←→ QMT「买入/平昨/平今」三项分设 ⇒ **三项同值映射**（写入 DEAL-07 批次文档）。

## 五、FEE-04 过户费（本地侧）

- `cost.transfer_fee_rate` **字段存在**（:797 的置零语句即证据）；
- ETF 路径下**默认归零**（`set_commission(type='ETF')`）；
- **股票路径下的默认值与计费公式本次未取**（`cost` 对象定义未在 `costs.py`，文件不存在；定义应内联于 `ptrade_api`/引擎）。

⇒ 判定 **已排查（字段与 ETF 归零逻辑确证；默认值与股票计费公式待补）**。

## 六、待补项（下轮，仍纯本地）

1. LC-08「策略回调异常语义」→ 定位回调调用点。
2. FEE-04「`TradeCost` 默认值 + 股票过户费公式」→ 定位类定义。
3. FEE-02「佣金费率默认值」（同上，与 §六.2 同源）。
