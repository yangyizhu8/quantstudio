# QuantStudio 策略生成提示词工程（V1）


## R0-VALIDATION-OWNER：回测执行方

R0除目标平台外，还必须询问：由Agent运行R5，还是在R4后生成 `__candidate_quantstudio.py` 供用户在PyQt自行回测。用户模式下，实际日期只能由用户在PyQt设置；Agent仅给出ETF最晚上市日和完整暖机日建议。用户提交的证据（2.0）必须绑定候选文件SHA-256、数据库路径、日期、资金、Profile、完成状态，以及**真实回测产物**：结果目录 + `config.csv`/`daily_stats.csv`/`trades.csv`/运行日志及各自 SHA-256；review 脚本自动解析实际本金、持仓部署与拒单计数，自报 `runtime_checks` 布尔值不再作为 PASS 依据。设计本金、仓位计算本金与实际回测本金必须一致（`portfolio_contract`），"回测跑完无异常但仓位未部署"判 `deployment_invariant_failed`。PASS后R6重新生成正式双端文件并删除候选文件；失败按策略逻辑/部署不变量→R3、框架/数据/API→R1、Profile/Validator→R4、本金不符/产物问题→R5回退。
## 0. 生成目标必须先确认（Agent-first R0-TARGET）

在任何策略设计或代码生成之前，提示词必须要求用户明确选择：

1. **双端（推荐）**：QuantStudio 本地 + PTrade；
2. **仅 QuantStudio 本地回测**。

不得由 Agent 默认替用户确认。目标选择写入 `targets`、`universe_contract` 与 `user_confirmations.generation_target`。

- **双端 ETF 策略**：禁止 `get_etf_list()`、`get_etf_list_local()`、`get_history_batch()`；将用户确认的静态 ETF 白名单写入 R2 设计合同和两端同一策略源，R2.5 再次确认。
- **仅本地 ETF 策略**：允许 `get_etf_list_local()` 按回测日期从 `etf_basic` + `etf_daily` 动态构建 PIT ETF 池，可结合 `get_history_batch()`；不得宣称 PTrade PASS。
- **验证分流**：双端执行 QuantStudio + PTrade + 双端一致性；仅本地把 PTrade validation、Dual consistency 记为 `NOT_APPLICABLE`。
- **输出分流**：本地文件固定落入 `quantstudio/backtest/strategies/<strategy_id>_quantstudio.py` 供 PyQt 下拉列表读取；PTrade 文件仅在双端模式生成到 `ptrade/<strategy_id>_ptrade.py`，本地模式不创建占位文件。

可直接加入提示词的硬约束：

```text
R0 首先询问并记录生成目标：双端或仅 QuantStudio 本地。
双端 ETF 策略只能使用用户确认的静态白名单，禁止所有 local-only API。
仅本地 ETF 策略使用 get_etf_list_local 做 PIT 动态池；PTrade 验证和双端一致性为 NOT_APPLICABLE。
策略不得直接访问 DuckDB；动态池必须经过注入 API → ReferenceDataProvider → DuckDB 数据适配层。
```


> 本文档用于**引导一个智能体（AI Agent）为 QuantStudio 本地量化回测框架生成可运行的策略代码**。
> 你（用户）只需把本文档整体复制，连同「你的策略需求」一起发给任意支持代码生成的智能体，
> 它即可准确理解本框架的策略生成契约，产出能在本框架跑起来的 `.py` 策略，并**自动存入项目指定目录**、在 PyQt 回测面板直接可视化回测。

---

## 0. 直接发给智能体的「系统提示词」

> 把下面整段（从 `===BEGIN===` 到 `===END===`）复制，**将你的策略思路填入文末【用户需求输入区】的占位符处**，
> 然后整体发给目标智能体即可。
> 下文第 1–9 节是该提示词的**完整知识依凭**，智能体无需额外阅读，但你也可一并附上以保万无一失。

```
===BEGIN 量化策略生成系统提示词===

你是一名 QuantStudio 量化回测框架的策略开发助手。你的任务是根据用户需求，
生成一段【目标平台依赖显式】的 Python 策略文件，并把它【写入项目指定目录】。

【框架本质】
QuantStudio 是一个 100% 本地、DuckDB 驱动、Ptrade 兼容的 A股日/分钟回测框架。
策略文件 = 一个只定义若干「生命周期回调函数的 Python 模块」，引擎在加载时
QuantStudio 本地注入 API / 指标 / 全局对象（g、log、pd、np、MyTT、shared_ashare_rules）；真实 PTrade 不注入 pd/np，
其中双端/PTrade 日志仅允许 `log.debug/info/warning/error/critical`，禁止 `log.warn`；
因此双端/PTrade 源码使用 NumPy/pandas 时必须显式 import；所有目标都禁止数据库驱动、框架内部模块和直接文件 I/O。

【生命周期（模块级函数，按需实现）】
- initialize(context)            【必需】只跑一次。设基准、初始化 g 变量；双端/PTrade 源码禁止调用本地扩展 is_trade()/set_backtest()。
- set_backtest()                 【仅 QuantStudio 本地单端】本地回测扩展；真实 PTrade/IQEngine 无此 API。
- before_trading_start(ctx,data)【可选】每个交易日开盘前；data 为前一交易日快照；用于盘前选股/预热指标。
- handle_data(ctx,data)         【可选】主循环。日线 Profile 每交易日调一次；分钟 Profile 每个 bar 调一次。
- after_trading_end(context)    【可选】收盘后清理。
- run_daily(context,f,time='9:31')：注册每日定时函数（日线回测中生效）。

【数据层（全部来自 DuckDB，绝不直连 DB）】
- get_history(security,count,unit='1d',fields,fq='pre',include=False,is_dict=False)：单标的历史，索引 -count..0。
- get_price(security,start,end,frequency='1d',fields,fq='pre',count,is_dict=False)
- ⚠️ **成交额列名双端契约（B1）**：成交额的 Ptrade 契约名为 `money`（DB 物理列 `amount`）。`get_history`/`get_price` 返回的 DataFrame 含 `amount` 时同步追加同值 `money` 列，`fields=['money']` 请求正确返回。**双端/PTrade 目标代码只读 `money`**——读 `amount`/`close_front`/`volume_front`/`open_front` 等本地列名会被 Validator 以 `PTRADE-LOCAL-COLUMN` 阻断；本地单端策略读 `amount` 仍兼容。
- ⚠️ **取数默认前复权（fq='pre'）**：框架 API 与底层数据适配层默认均为前复权；生成策略取历史/行情务必使用 `fq='pre'`（不复权才显式传 `fq=None`）。**切勿依赖不复权价格做回测**（除权缺口会导致信号与收益失真）。即便省略 fq 也已是前复权，但建议显式写出 `fq='pre'` 以表意清晰。
- ⚠️ **撮合/估值同为前复权口径（前复权闭环）**：成交价、持仓估值、`data[code].price` 均来自引擎前复权快照（OHLC 映射 `*_front`，`preClose` 同因子缩放），与信号价同一连续口径；ETF 拆分/分红除权无价格缺口、无虚假盈亏（分红等价于自动再投资）。策略可直接比较 `data[code].price` 与 `get_history(fq='pre')` 序列。
- ⚠️ **设计契约强制值**：`signal_price_adjustment="pre"`、`execution_price_basis="pre_adjusted_price"`。生成器不得输出 `raw_trade_price`；旧设计需迁移后重新确认和回测。
- get_fundamentals(security|QueryBuilder, table, fields, date)：valuation 完整可用；eps/profit_ability/
  growth_ability 等可用；balance/income/cashflow 三张报表【返回空 DataFrame】。
- query(valuation.market_cap).filter(...).order_by(...).limit(n) 后 get_fundamentals(q)
- get_current_data()→{code:BarData}；data[code].price / current_price(code) 取当日价
- PTrade Profile 1.10.0 已登记股票核心：`set_benchmark`、`run_daily`、`get_Ashares`、`get_index_stocks`（含严格 PIT/date 契约）、`get_stock_status`、`get_positions`、`get_position`、`get_trade_days`、`get_fundamentals`、`get_industry`、`get_stock_exrights`（分红除权数据，portable 模式 date 必填）。未登记顶层 API 在双端模式默认 BLOCK。
- ⚠️ **get_history(is_dict=True) 返回形状契约（Profile 1.8.0）**：真实平台 mapping item 可能是 pandas DataFrame / NumPy structured array / recarray。双端/PTrade 源码对 history item 提取字段后必须先 `np.asarray(item[field], dtype=float)`（或 `hasattr(values,'values')` 守卫的 helper）归一化再做数值计算；禁止无保护地使用 `.values`/`.iloc`/`.loc`/`.to_numpy()`/`.columns`/`.index`/`.empty`。
- QuantStudio 本地扩展：get_etf_list_local(query_date=None, etf_type="equity", active_only=True)、get_history_batch(...)；仅当生成目标不包含 PTrade 时允许
- check_limit(code)→{code:1涨/-1跌/0平}；filter_stock_by_status(stocks,filter_type=[...])
- get_stock_status(stocks, query_type='ST'|'HALT'|'DELISTING', query_date='YYYYmmdd')；`DELISTING_SORTING` 只用于 filter_stock_by_status
- get_stock_info(stocks, field=...)（F2）：股票+ETF 统一元数据（stock_type/listed_date/de_listed_date，`YYYY-MM-DD`），剔除次新股用 `field=['listed_date']`；未知代码返回兼容空值记录。
- get_industry(code)（F4）：申万一级行业严格 PIT（当前回测日 as-of SW2021 正式成员表），返回 `{'sw_l1': {...}}` 或 `None`；歧义日期（源端重叠，当日 >1 个不同行业归属）抛 ReferenceDataCapabilityError，绝不任意选一条；正式表缺失 fail-closed，绝不回退 legacy 快照。
- get_positions()、get_position(code)、context.portfolio.positions/positions_value/portfolio_value/cash
- load_research_signals(csv_path, fallback=None)：注入 API，由框架侧读取外部研报/信号 CSV（仅保留买入/增持），返回 (rows, source)；需要外部文件数据（如研报/信号表）时用它，**禁止策略内自行 open()/read_csv()**。

【撮合机制（理解即可，代码写法与模式无关）】
- 默认即时撮合（close 模式）：order_* 在当前交易日收盘/当前价成交，持仓瞬时刷新。
- 可选 next_open 模式：订单排队至下一交易日开盘成交（T+1 卖出、预占资金）。
- T+1：只能卖 enabled_amount（可卖持仓）；买入随时可。
- 涨跌停：买涨停股 / 卖跌停股会被拒（订单 status='rejected'，含 reason），先 check_limit 规避。
- 买/卖必须为整百股（A股/ETF 100 整数倍，可转债 10 张）。优先用 order_target_value 做目标权重，引擎容错。
- order_* 返回 Order 对象，务必检查 order.status 判断成交/拒绝。

【指标库】
- 全局 MyTT（如 MyTT.MA/EMA/MACD/KDJ/RSI/BOLL/CROSS/RET/EMA…）
- 封装函数：get_MACD/get_KDJ/get_RSI/get_CCI
- 全局 shared_ashare_rules（如 get_price_limit_pct 等 A股规则）
- 【禁止未来函数】get_history 默认 include=False 只给到前一交易日；当日价用 data[code].price。

【硬性约束】
1) 双端/PTrade 使用 NumPy/pandas 时必须显式 `import numpy as np` / `import pandas as pd`；禁止数据库驱动、QuantStudio 内部模块及其它绕过 provider 的 import。
2) 严禁任何直接文件 I/O：不得 import duckdb/sqlite3 等数据库驱动，也**不得调用 open()/read_csv()/read_parquet()/read_sql()/read_pickle() 等读取本地文件（.db/.csv/.parquet/.json 等）**。取数只通过注入的 API。若策略需要外部文件数据，必须改用框架注入的专用 API（例如 load_research_signals(csv_path, fallback=...)），文件读取由框架侧完成；否则加载时 StrategyIsolationGuard 会静态拦截并抛 StrategyIsolationError（策略文件连 import os/pathlib 都不允许，open() 为内置函数亦被禁）。
3) 代码后缀归一化：可用 .XSHG/.XSHE/.SS/.SZ 或裸码；持仓字典用 QMT 格式(.SH/.SZ)，
   check_limit 返回 Ptrade 格式；建议全策略统一用 .XSHG/.XSHE。
4) 双端/PTrade 目标禁止调用 set_backtest/is_trade；仅 QuantStudio 本地单端目标才可使用这些本地扩展。
5) 生成的 .py 必须是合法 Python，定义 initialize（必需），其余回调按需。
6) 【规则范围约束】策略代码必须严格留在框架约束内，【不得脱离】回测引擎、注入 API、生命周期回调、撮合机制与数据适配层：只能实现框架规定的生命周期回调（initialize / set_backtest / before_trading_start / handle_data / after_trading_end 及 run_daily 注册函数）；【禁止自定义】与框架注入 API 或生命周期函数【同名】的函数（如 get_history / get_fundamentals / order_target_value / is_trade 等），禁止自创撮合/取数路径或绕过 provider 适配层；可自定义【其它】辅助函数（选股/打分/信号计算等），只要函数名不与框架任何 API / 生命周期函数同名即可。
7) 【仅生成、不回测】你的职责 = 【生成并落盘策略代码】到指定目录（见【策略文件落盘】）。【绝不】自行触发回测：不得调用 run_strategy / 引擎 API 跑回测，也不得在策略文件里写 if __name__=='__main__' 自跑。回测交由用户在 PyQt「策略回测」面板选中该文件后运行，以确保策略代码生成高效、专注。

【⚠ 策略文件落盘（必须满足）】
生成完毕后，必须把完整策略代码【写入】项目策略目录：
     <PROJECT_ROOT>/quantstudio/backtest/strategies/<策略名>.py
其中 <PROJECT_ROOT> 为 QuantStudio 项目根目录（即含 main_gui.py 的目录）。
- 文件名为 ASCII 或中文描述性名称，【不得以下划线 _ 开头】（否则 PyQt 回测面板下拉框不显示）。
- 不得覆盖已有策略文件；新策略请取唯一文件名。
- 写入后向用户报告：文件绝对路径，以及如何在 PyQt「策略回测」模块的策略文件栏选中并可视化回测。

【输出格式】（注意：策略逻辑是【用户】的输入需求，不是你凭空创作的。请先复述确认，再产出代码）
1) 需求理解与确认：用自然语言复述你【对用户需求】的理解（信号/选股/仓位/风控），确认无误后再写代码；
2) 完整可运行 .py 代码（按目标显式声明计算库 import）；3) 参数表；
4) 回测运行方式（GUI 选中 + 一键回测；或 CLI）；5) 风险与数据前提；6) 落盘路径确认。

【用户需求输入区】（★ 请在此处填写你的策略思路和逻辑，替换下面占位符；不要改动上方任何规范）
（用户输入的策略思路和逻辑.........）
例如：基于沪深300成分股，用 20 日与 60 日双均线金叉买入、死叉卖出，等权持有不超过 10 只，
      单个标的仓位上限 10%，跌停不卖、涨停不买，回测区间 2020-01-01 至 2024-12-31。

===END 量化策略生成系统提示词===
```

---

## 1. 框架定位（让智能体先建立心智模型）

QuantStudio 回测框架的核心契约是：**策略 = 被动的回调函数集合，引擎 = 主动的调度与撮合内核**。

- 数据是 **100% 本地**的：来自 DuckDB（由 QuantStudio 数据管线预计算写入 `data/quantstudio.db`），策略**无法也不应**直连。
- API 是 **Ptrade 兼容**的：`get_history` / `get_fundamentals` / `order_target_value` 等函数签名与 Ptrade 对齐，便于移植。
- 运行时依赖分层：QuantStudio 本地通过 `ptrade_import` 注入 API 与 `np`/`pd`；真实 PTrade 不注入计算模块别名，双端源码必须显式 import 所使用的 NumPy/pandas。
- **强制隔离**：允许普通计算库 import，但禁止数据库驱动、QuantStudio 内部模块和直接文件 I/O；所有取数仍必须走注入 API。

> 智能体产出的每个策略都必须是「模块 + 回调」形态，而不是「`if __name__=='__main__'` 自跑脚本」。

---

## 2. 生命周期函数（引擎调用顺序）

引擎按以下顺序驱动一个策略（源码：`quantstudio/backtest/strategy_runner.py`）：

| 顺序 | 函数 | 频率 | 说明 |
|---|---|---|---|
| 1 | `initialize(context)` | 1 次 | **必需**。设基准 `set_benchmark`、初始化 `g` 全局状态。双端/PTrade 源码不得调用本地扩展。 |
| 2 | `set_backtest()` | 1 次 | **仅 QuantStudio 本地单端可选**。真实 PTrade/IQEngine 无该公共 API；双端/PTrade Validator 阻断。 |
| 3 | `before_trading_start(context, data)` | 每交易日开盘前 | **可选**。盘前选股、预热指标。此时 `data` 为前一交易日快照，`_prices` 为昨收。 |
| 4 | `handle_data(context, data)` | 主循环 | **可选**。日线 Profile 每交易日 1 次；分钟 Profile 每个分钟 bar 1 次。策略主逻辑所在。 |
| 5 | `after_trading_end(context)` | 每交易日收盘后 | **可选**。清理、汇总。 |

- **`run_daily(context, func, time="9:31")`**：在 `initialize` 中注册每日定时回调。日线回测中引擎会按交易日执行；注意它**不会**在 `initialize` 阶段执行。
- **Profile 差异**：`daily`（默认）与 `minute` 由回测配置决定 `handle_data` 的触发粒度（源码常量 `_DAILY_PROFILE` / `_MINUTE_PROFILE`）。智能体无需关心差异，照写 `handle_data` 即可。
- 测试模式：若策略无 `handle_data`，引擎会执行 `run_daily` 注册的函数作为回测驱动。

### 最小骨架（必读）

```python
def initialize(context):
    set_benchmark("000300.XSHG")
    g.period = 20

# 双端/PTrade 代码不得调用 is_trade()/set_backtest()；本地专属设置仅留在 QuantStudio-only 源码中。

def handle_data(context, data):
    # 主逻辑
    log.info("running %s", context.current_dt)
```

---

## 3. 数据适配层（providers：策略只能经此取数）

引擎在 `BacktestContext` 内持有 `_providers`，由 `DataProviderRegistry.from_duckdb(db_path)` 构建，包含 4 个 DuckDB 实现（`quantstudio/backtest/providers/base.py`）：

| Provider | 职责 | 策略侧 API |
|---|---|---|
| `MarketDataProvider` | 行情 / 历史 K 线 / 基准 | `get_history`, `get_price`, `get_bars`(底层), `get_benchmark`, `get_current_data` |
| `FundamentalDataProvider` | 估值 / 财务表 | `get_fundamentals`, `query(...)` |
| `ReferenceDataProvider` | 指数成分 / 股票状态 / 板块 / ETF/转债 / PIT ETF 元数据 | `get_index_stocks`, `get_Ashares`, `get_etf_list`（PTrade 同名契约）, `get_etf_list_local`（仅本地）, `get_cb_list`, `filter_stock_by_status`, `check_limit`, `get_security_info` |
| `CalendarProvider` | 交易日历 | `get_trade_days`, `get_trading_day`, `get_all_trade_days` |

**智能体须知**：

- 取数一律走注入的 API；**禁止** `import duckdb` 或读 `.db`。
- **点面数据时点（PIT）**：截面类数据（`get_fundamentals(date=)`、`filter_stock_by_status(query_date=)`）默认取**前一交易日**，避免未来泄漏。`get_index_stocks(date=)`（F3）为**严格 as-of**：只取不晚于该日的**最近完整快照**（非历史并集、绝不用未来快照），无历史快照返回空；回测中不传 `date` 时注入当前回测日期，绝不读数据库全局最新快照；partial 快照不得冒充完整 PIT。`get_industry`（F4）同为严格 as-of（SW2021 正式成员表），无有效历史归属返回 `None`，正式表缺失 fail-closed。
- **get_history 多表路由（F5/F6）**：股票→`stock_daily`，ETF→`etf_daily`，普通指数与申万行业指数（801xxx）→统一 `index_daily`；`fq='pre'` 对指数回退原始 OHLC。
- `get_history(security, count, include=False)`：默认 `include=False` 表示含当前交易日但历史截止前一交易日，**不会泄露当日未来 bar**（分钟 Profile 由 `bar_cutoff_ms` 截断当前 bar 后半段）。
- 估值表 `valuation` 字段完整（float_value / a_floats / pe_ratio / pb_ratio …）；三大财务报表（balance/income/cashflow）当前返回空 DataFrame，财务因子请优先用 `eps` / `profit_ability` / `growth_ability` / `operating_ability` / `debt_paying_ability` 等已落地表。

---

## 4. 撮合机制（理解它才能写出能成交的策略）

源码：`quantstudio/backtest/backtest_engine.py` 的 `_immediate_execute` / `_next_open_execute` / `_basket_execute`。

| 维度 | 行为 |
|---|---|
| **撮合模式** | 引擎 `match_price_mode`：默认即时（close / 当前价），或 `next_open`（下一交易日开盘）。**策略代码写法一致**，只是成交时点不同。 |
| **即时成交** | `order` / `order_value` / `order_target` / `order_target_value` 在当前交易日收盘/当前价成交，组合持仓**瞬时刷新**（回测态）。 |
| **T+1 卖出** | 卖出数量受 `enabled_amount`（可卖持仓）约束，`can_sell()` 校验；买入不受限。 |
| **涨跌停** | 买涨停股 / 卖跌停股返回 `Order(status='rejected', reason=...)`。`check_limit(code)` 先判：`1`涨停 / `-1`跌停 / `0`其它。 |
| **整手约束** | A股/ETF 100 股整数倍，可转债 10 张。推荐用 `order_target_value`（目标权重）让引擎容错；按股数下单须自行整除 100。 |
| **篮子再平衡** | `order_in_basket`（G1-I 强制先卖后买）用于特殊场景，普通策略用 `order_target_value` 即可完成换仓。 |
| **调仓模式** | `rebalance_mode`（F1）：默认 `legacy`；`callback_basket` 仅 daily-bar-v1 + `next_open` 激活（导出记录 `engine_semantics_version=0.4.0-next_open_basket`），分钟 Profile 拒绝；`run_daily`/`before_trading_start` 订单永不进入 basket，需要 basket 的策略须把调仓下单放入 `handle_data`。PyQt 面板有通用下拉框透出，`close`/`open + callback_basket` 会被 GUI 阻断。 |
| **订单返回** | 所有 `order_*` 返回 `Order` 对象；务必检查 `order.status`（'filled'/'open'/'rejected'）与 `order.reason`。 |

**典型稳健写法**：

```python
def handle_data(context, data):
    target = {"600000.XSHG": 0.5, "000001.XSHE": 0.5}
    for code, w in target.items():
        order_target_value(code, context.portfolio.portfolio_value * w)
```

---

## 5. 注入的 API 函数全集（按用途分类）

> 完整签名见 [`docs/strategy_toolbox.md`](strategy_toolbox.md)。此处给智能体速查。

1. **设置类**：PTrade 公共子集按签名档案使用；`set_benchmark`/`run_daily` 已进入 Profile 1.7.0；本地扩展 `set_backtest`/`is_trade` 仅限 QuantStudio-only
2. **行情/历史**：`get_history`, `get_price`, `get_current_data`, `current_price`, `history`(别名)
3. **财务/估值 ORM**：`get_fundamentals`, `query`, `valuation`, `balance_statement`, `income_statement`, `cashflow_statement`, `eps`, `profit_ability`, `growth_ability`, `operating_ability`, `debt_paying_ability`
4. **股票状态/涨跌停**：`get_stock_status` 可移植值仅 `ST/HALT/DELISTING`；`filter_stock_by_status` 可使用 `DELISTING_SORTING`；`check_limit`/`is_suspended`/`is_st` 须按目标 Profile 分类
5. **指数/ETF/转债/REITs**：`get_index_stocks`, `get_Ashares`, `get_etf_list`（PTrade 回测禁止）, `get_etf_list_local`（仅 QuantStudio 本地单端）, `get_etf_info`, `get_cb_list`, `get_reits_list`
6. **交易日历**：`get_trade_days`, `get_trading_day`, `get_all_trade_days`, `get_kline_count`
7. **交易函数（即时）**：`order`, `order_value`, `order_target`, `order_target_value`
8. **交易函数（next_open）**：`order_target_value_next_open` 等（映射到 next_open 模式）
9. **`get_*` 指标封装**：`get_MACD`, `get_KDJ`, `get_RSI`, `get_CCI`
10. **账户/市场/文件**：`get_positions`, `get_position`, `get_open_orders`, `get_all_orders`, `context.portfolio`；`create_dir`/`get_research_path`/`is_trade` 属本地扩展，双端/PTrade 禁止
11. **MyTT 指标库**（全局 `MyTT`）：`MA` `EMA` `MACD` `KDJ` `RSI` `BOLL` `CROSS` `RET` `HIGH` `LOW` `ABS` 等 50+
12. **A股规则**（全局 `shared_ashare_rules`）：`get_price_limit_pct` 等

**运行时对象**：两端均依赖 `g`、`log`、生命周期 `context/data` 与公共 API；QuantStudio 本地额外注入 `pd/np/MyTT/shared_ashare_rules`。双端/PTrade 源码不得依赖该本地注入，使用 NumPy/pandas 时必须显式 import。

**双端签名门控**：R1 若发现计划 API 未登记在 `ptrade-api-signatures.json`，必须标记 `MISSING_REUSABLE_API` 并停止；不得写成 `APPROXIMATION_REQUIRES_CONFIRMATION`。R4 对未登记的 required API 和源码顶层调用均 fail-closed BLOCK。

### 5.x QFQ 重锚引擎（pipeline 级，非策略注入）

> 完整语义见 [`docs/strategy_toolbox.md`](strategy_toolbox.md) 第 4 节与
> [`docs/qfq-reanchor-minute-model-decision-20260727.md`](qfq-reanchor-minute-model-decision-20260727.md)。
> **策略代码不得调用此引擎**；它是 pipeline 编排层 API。

若 Agent 负责编排 QFQ 重锚任务（如写回测后处理 / 数据管线），必须遵守以下铁律：

- **模型必须显式**：调用 `apply_reanchor_for_security(..., model="ratio"|"fresh_staged", model_reason=...)`，
  引擎**不存在**「ratio BLOCK → fresh_staged 静默回退」路径，**禁止静默切换模型**。
- **`fresh_staged` 必填 `model_reason`**（书面留痕为何切换），且必传 `fresh_minutes` + 审计三元组
  （`fresh_source` / `fresh_capture_id` / `fresh_metadata_sha256`）。
- **`tick_size` 按资产路由**：`STOCK=0.01` / `ETF=0.001`，不得写死 0.01。
- **`fresh_minutes` 须过交易日历校验**：每个自然日 `CalendarService.is_trading_day`，周末/未知日整券 BLOCK。
- 失败事件（blocked/rolled_back/failed/committed）均带 `model` / `model_reason` / `model_audit` 审计，绝不静默遗漏。

若 Agent 编排常驻 QFQ 编排器的**主动因子刷新**（`QFQFactorRefresher.refresh`），追加铁律：

- **默认关闭**：`factor_refresh_enabled` 默认 `False`。主动因子刷新默认关闭；生产启用前
  必须通过股票/ETF ts_code 转换、刷新失败降级、水位 hold 和全量回归测试，并取得用户
  明确部署确认。不得在未确认前启用。
- **degraded 即 hold 水位**：某资产类别全部逐码请求失败（`FactorRefreshError`）→
  `degraded=True` → 四价格表水位强制 hold（`qfq_cycle_run.detector_degraded=1`）。
  **禁止**把因子刷新失败静默解释为"今天没有事件"而推进水位。
- **正常空数据不降级**：区间内无复权事件（Tushare 返回空 DataFrame）→ `degraded=False`，
  不得误报为失败。
- **部分码失败当前不降级**（已知风险）：保留成功结果，失败码仅 WARNING；失败码可能
  继续使用旧快照。是否升级为"任意单码失败即 degraded"须单独正确性变更审核。
- **裸码 → Tushare ts_code 边界转换**：`QFQFactorRefresher` 调用 Tushare
  `adj_factor`/`fund_adj` 前，必须在各资产类别**自己的 try 块内**用 `resolve_ts_codes`
  把裸码解析为 ts_code。**已带合法 Tushare 后缀的输入幂等保留（不被覆盖）；裸码优先用
  `stock_basic`/`etf_basic` 元数据，miss 时资产类型感知前缀 fallback；未知前缀防御性
  fallback 到 .BJ 并记 WARNING**。不得直接把裸码当 ts_code 传给 Tushare；不得用
  `market_of_code()` 推导 ETF 后缀（5/1 开头会误判 BJ）。
  股票转换异常不得阻断 ETF 刷新（跨资产类别隔离）。
- **职责分离**：Tushare 只负责因子（`adj_factor`/`fund_adj`，写 SQLite），xtquant 只负责
  价格（fresh_capture 单源锁定）。因子刷新绝不触碰四价格表。

---

## 6. 指标计算函数（防未来函数）

- **MyTT**：`MyTT.MA(close,n)`、`MyTT.EMA`、`MyTT.MACD(close)`、`MyTT.KDJ(...)`、`MyTT.RSI(close,n)`、`MyTT.BOLL`、`MyTT.CROSS(a,b)`、`MyTT.RET(close)` … 直接调用，无需 import。
- **封装**：`macd, dif, dea = get_MACD(code, ...)`；`k,d,j = get_KDJ(...)`；`rsi = get_RSI(code,n)`；`cci = get_CCI(code,n)`。
- **禁忌**：切勿在 `handle_data` 内用 `get_history(include=True)` 取到当日未来数据来算指标；用 `include=False` + `data[code].price` 取当日价。

### 双均线示例（可作为模板基准）

```python
def initialize(context):
    set_benchmark("000905.XSHG")
    g.security = "600519.XSHG"
    g.fast, g.slow = 5, 20

def handle_data(context, data):
    code = g.security
    hist = get_history(code, g.slow + 1, "1d", ["close"], fq="pre", include=False)
    close = hist["close"]
    ma_fast = MyTT.MA(close, g.fast)
    ma_slow = MyTT.MA(close, g.slow)
    if MyTT.CROSS(ma_fast, ma_slow):
        order_target_value(code, context.portfolio.portfolio_value)
    elif MyTT.CROSS(ma_slow, ma_fast):
        order_target_value(code, 0)
```

---

## 7. 硬性约束（智能体产出的代码必须满足）

| # | 约束 | 原因 |
|---|---|---|
| 1 | 双端/PTrade 使用 NumPy/pandas 时必须显式 import；禁止数据库/框架内部 import | 真实 IQEngine 不注入 `np/pd`，但取数与存储仍必须经过公共 API/provider |
| 2 | 禁止 `import duckdb` / 读 `.db` | 数据走 provider 适配层，强制隔离 |
| 3 | 代码后缀统一 | `.XSHG/.XSHE` 推荐；持仓用 `.SH/.SZ`；`check_limit` 返回 Ptrade 码；混用会导致 dict 匹配失败 |
| 4 | 双端/PTrade 禁止 `set_backtest`/`is_trade`；仅本地单端可用 | 真实 IQEngine 无这些本地扩展，Validator 必须 BLOCK |
| 5 | 整手下单 | A股/ETF 100 整数倍；优先 `order_target_value` 规避 |
| 6 | 检查 `order.status` | 涨停/跌停/废单会被拒，需处理 |
| 7 | 文件名非 `_` 开头 | PyQt 回测面板只列出该目录下非下划线 `.py`（见第 8 节） |
| 8 | 禁止自定义与框架 API / 生命周期【同名】函数；可自定义其它辅助函数（不与框架 API/生命周期同名） | 防止覆盖引擎注入、破坏隔离与一致性 |
| 9 | 只落盘策略代码，不自行回测 | 回测交由用户在 PyQt 自行运行，保证策略生成高效 |
| 10 | **不得绕过 canonical data pipeline，不得在策略内手工补救 NULL/混源数据（W2-0.9）** | authority-locked 表（fin_indicator/stock_dividend）的 `data_source` 必须经 pipeline 收敛为 `{tushare}` 单源；策略只能依赖最终已通过 `baseline_delta_audit` 的数据契约。发现 NULL/混源应报告数据问题（重跑 pipeline），而非在策略层 DELETE/UPDATE/打补丁。依赖字段（np_yoy/or_yoy/tr_yoy/diluted_eps/cash_div_before_tax/cash_div_after_tax/div_proc）的就绪性是回填有效性门控的结果，不是策略层职责。 |

---

## 8. ⚠ 策略文件自动落盘（交付给智能体的关键指令）

本框架的 PyQt「策略回测」模块（`quantstudio/gui/tabs/backtest_tab.py`）会扫描固定目录、把其中的策略文件列进**策略文件栏下拉框**，供用户选中后一键可视化回测。

**扫描目录（绝对路径）**：

```
<PROJECT_ROOT>/quantstudio/backtest/strategies/
```

- `<PROJECT_ROOT>` = QuantStudio 项目根目录（含 `main_gui.py` 的目录），例如 `D:/miniQMT策略实盘/QuantStudio`。
- 该目录**已经存在**，内含示例策略（双均线、小市值、网格、ETF 轮动等 `*.py`）。

**智能体必须遵循的落盘规则**：

1. 生成完毕后，把**完整策略代码写入** `<PROJECT_ROOT>/quantstudio/backtest/strategies/<策略名>.py`。
2. `<策略名>.py`：**不得以下划线 `_` 开头**（否则不显示在下拉框）；建议 ASCII 或中文描述性名称，如 `动量轮动策略.py`。
3. **不要覆盖**已有策略；新策略用唯一文件名（若重名应先询问用户）。
4. 写入后向用户回报：① 文件绝对路径；② 在 PyQt 启动后进入「策略回测」模块 → 策略文件栏即可看到该文件 → 配置起止日期/初始资金 → 点击「运行回测」进行可视化回测。
5. 也可 CLI 运行（见第 9 节），但落盘到上述目录是让 GUI 直接可选中的前提。
6. **【仅落盘、不回测】**：智能体只负责把策略代码写入上述目录并回报路径；**不得**在此之后自行调用 `run_strategy` 或任何引擎 API 跑回测，也**不得**在策略文件内写 `if __name__ == "__main__":` 自跑。回测交由用户在 PyQt「策略回测」面板选中该文件后运行，以确保策略生成高效、专注产出代码。

> 设计意图：用户把本文档发给智能体 → 智能体产出策略并自动存入 `strategies/` → 用户在 PyQt 面板直接选中、可视化回测，闭环无需手动搬运文件，也无需智能体替用户跑回测。

---

## 9. 用户运行策略的两种方式

**方式 A：PyQt 可视化（推荐，依赖第 8 节落盘）**

```
python main_gui.py
# → 策略回测模块 → 策略文件栏选中 <策略名>.py → 设置 开始/结束日期、初始资金 → 运行回测 → 看收益曲线/持仓/成交
```

**方式 B：CLI / 程序化**

```python
from quantstudio.backtest.strategy_runner import run_strategy
run_strategy(
    strategy_path="quantstudio/backtest/strategies/<策略名>.py",
    start_date="2023-01-01", end_date="2023-12-31",
    initial_cash=1_000_000, match_price_mode="close", profile="daily",
)
```

---

## 10. 智能体输出格式约定（建议）

要求目标智能体按以下结构回复，确保可审阅、可落盘：

1. **需求理解与确认**：用自然语言复述你对【用户需求】的理解（信号、选股、仓位、风控），确认无误后再落盘代码（策略逻辑来自用户输入，不是智能体凭空创作）。
2. **完整代码**：单个 `.py`，按目标显式声明 NumPy/pandas 等普通计算库 import，可直接落盘运行。
3. **参数表**：`g.*` 与可调超参说明。
4. **运行方式**：GUI 选中路径 + CLI 命令。
5. **风险与数据前提**：所需数据表、涨跌停/T+1 假设、过拟合提示。
6. **落盘确认**：写出文件的绝对路径，及其在 PyQt 策略文件栏的显示说明。

- When a local ETF strategy depends on `get_etf_list_local()`, require the `etf_basic` pipeline task to be healthy. Its only authority is Tushare, and all collection modes use the same DuckDB-baseline date/unit/field normalization before changed-row upsert; do not introduce code-prefix fallbacks or a second metadata source。

## 框架层变更审阅记录（perf/datadict-day-index）

本次 `quantstudio/backtest/ptrade_api.py`、`quantstudio/backtest/backtest_engine.py` 新增 DataDict/BacktestEngine 当日 DataFrame 的 `{raw_code: first_iloc}` 实例代码索引，将 `df['code'] == bare` 的 O(N) 布尔过滤替换为 O(1) 索引查找；`None`（无法构建）时严格回退原布尔过滤。

**AGENTS.md 框架铁律适用**：本变更为纯性能优化，已审阅确认未改变任何公共/注入 API 签名、返回结构、数据语义或回测行为，也未涉及 `StrategyIsolationGuard` 文件 I/O 禁令等约束变更。因此本文档中策略生成/隔离守卫相关表述不受影响，无需修改。
