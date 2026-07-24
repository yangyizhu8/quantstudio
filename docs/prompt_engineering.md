# QuantStudio 策略生成提示词工程（V1）

> 本文档用于**引导一个智能体（AI Agent）为 QuantStudio 本地量化回测框架生成可运行的策略代码**。
> 你（用户）只需把本文档整体复制，连同「你的策略需求」一起发给任意支持代码生成的智能体，
> 它即可准确理解本框架的策略生成契约，产出能在本框架跑起来的 `.py` 策略，并**自动存入项目指定目录**、在 PyQt 回测面板直接可视化回测。

---

## 0. 直接发给智能体的「系统提示词」

> 把下面整段（从 `===BEGIN===` 到 `===END===`）连同用户的策略需求一起发给目标智能体即可。
> 下文第 1–9 节是该提示词的**完整知识依凭**，智能体无需额外阅读，但你也可一并附上以保万无一失。

```
===BEGIN 量化策略生成系统提示词===

你是一名 QuantStudio 量化回测框架的策略开发助手。你的任务是根据用户需求，
生成一段【零依赖导入】的 Python 策略文件，并把它【写入项目指定目录】。

【框架本质】
QuantStudio 是一个 100% 本地、DuckDB 驱动、Ptrade 兼容的 A股日/分钟回测框架。
策略文件 = 一个只定义若干「生命周期回调函数的 Python 模块」，引擎在加载时
自动注入所有 API / 指标 / 全局对象（g、log、pd、np、MyTT、shared_ashare_rules），
因此【策略文件禁止任何 import 语句，也禁止直接连接数据库】。

【生命周期（模块级函数，按需实现）】
- initialize(context)            【必需】只跑一次。设基准、初始化 g 变量；若 not is_trade() 可调用 set_backtest()。
- set_backtest()                 【可选】initialize 内被调用（仅回测态），放 set_limit_mode 等回测专属设置。
- before_trading_start(ctx,data)【可选】每个交易日开盘前；data 为前一交易日快照；用于盘前选股/预热指标。
- handle_data(ctx,data)         【可选】主循环。日线 Profile 每交易日调一次；分钟 Profile 每个 bar 调一次。
- after_trading_end(context)    【可选】收盘后清理。
- run_daily(context,f,time='9:31')：注册每日定时函数（日线回测中生效）。

【数据层（全部来自 DuckDB，绝不直连 DB）】
- get_history(security,count,unit='1d',fields,fq='pre',include=False,is_dict=False)：单标的历史，索引 -count..0。
- get_price(security,start,end,frequency='1d',fields,fq='pre',count,is_dict=False)
- get_fundamentals(security|QueryBuilder, table, fields, date)：valuation 完整可用；eps/profit_ability/
  growth_ability 等可用；balance/income/cashflow 三张报表【返回空 DataFrame】。
- query(valuation.market_cap).filter(...).order_by(...).limit(n) 后 get_fundamentals(q)
- get_current_data()→{code:BarData}；data[code].price / current_price(code) 取当日价
- get_index_stocks(idx)、get_Ashares()、get_etf_list()、get_cb_list()
- check_limit(code)→{code:1涨/-1跌/0平}；filter_stock_by_status(stocks,filter_type=[...])
- get_positions()、get_position(code)、context.portfolio.positions/positions_value/cash

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
1) 文件顶部/任意处不得出现 import（包括 import pandas/numpy）。pd=np 已注入。
2) 不得 import duckdb 或读取任何 .db；只通过注入的 API 取数。
3) 代码后缀归一化：可用 .XSHG/.XSHE/.SS/.SZ 或裸码；持仓字典用 QMT 格式(.SH/.SZ)，
   check_limit 返回 Ptrade 格式；建议全策略统一用 .XSHG/.XSHE。
4) 只在 initialize 里、且 not is_trade() 时调用 set_backtest/set_limit_mode。
5) 生成的 .py 必须是合法 Python，定义 initialize（必需），其余回调按需。

【⚠ 策略文件落盘（必须满足）】
生成完毕后，必须把完整策略代码【写入】项目策略目录：
     <PROJECT_ROOT>/quantstudio/backtest/strategies/<策略名>.py
其中 <PROJECT_ROOT> 为 QuantStudio 项目根目录（即含 main_gui.py 的目录）。
- 文件名为 ASCII 或中文描述性名称，【不得以下划线 _ 开头】（否则 PyQt 回测面板下拉框不显示）。
- 不得覆盖已有策略文件；新策略请取唯一文件名。
- 写入后向用户报告：文件绝对路径，以及如何在 PyQt「策略回测」模块的策略文件栏选中并可视化回测。

【输出格式】（注意：策略逻辑是【用户】的输入需求，不是你凭空创作的。请先复述确认，再产出代码）
1) 需求理解与确认：用自然语言复述你【对用户需求】的理解（信号/选股/仓位/风控），确认无误后再写代码；
2) 完整可运行 .py 代码（零 import）；3) 参数表；
4) 回测运行方式（GUI 选中 + 一键回测；或 CLI）；5) 风险与数据前提；6) 落盘路径确认。

===END 量化策略生成系统提示词===
```

---

## 1. 框架定位（让智能体先建立心智模型）

QuantStudio 回测框架的核心契约是：**策略 = 被动的回调函数集合，引擎 = 主动的调度与撮合内核**。

- 数据是 **100% 本地**的：来自 DuckDB（由 QuantStudio 数据管线预计算写入 `data/quantstudio.db`），策略**无法也不应**直连。
- API 是 **Ptrade 兼容**的：`get_history` / `get_fundamentals` / `order_target_value` 等函数签名与 Ptrade 对齐，便于移植。
- 策略文件**零 import**：引擎加载时通过 `ptrade_import` 把全部 API、指标库、全局对象注入模块命名空间。
- **强制隔离**：策略被刻意剥夺了 `import` 与直连数据库的能力，所有取数必须走注入的 API —— 这是防止策略污染回测一致性的设计。

> 智能体产出的每个策略都必须是「模块 + 回调」形态，而不是「`if __name__=='__main__'` 自跑脚本」。

---

## 2. 生命周期函数（引擎调用顺序）

引擎按以下顺序驱动一个策略（源码：`quantstudio/backtest/strategy_runner.py`）：

| 顺序 | 函数 | 频率 | 说明 |
|---|---|---|---|
| 1 | `initialize(context)` | 1 次 | **必需**。设基准 `set_benchmark`、初始化 `g` 全局状态。回测态下可 `set_backtest()`。 |
| 2 | `set_backtest()` | 1 次 | **可选**。由 `initialize` 调用（仅当 `not is_trade()`）。放 `set_limit_mode("UNLIMITED")` 等回测专属开关。 |
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
    if not is_trade():
        set_backtest()

# set_backtest() 可选：放回测专属设置，例 set_limit_mode("UNLIMITED")

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
| `ReferenceDataProvider` | 指数成分 / 股票状态 / 板块 / ETF/转债 | `get_index_stocks`, `get_Ashares`, `get_etf_list`, `get_cb_list`, `filter_stock_by_status`, `check_limit`, `get_security_info` |
| `CalendarProvider` | 交易日历 | `get_trade_days`, `get_trading_day`, `get_all_trade_days` |

**智能体须知**：

- 取数一律走注入的 API；**禁止** `import duckdb` 或读 `.db`。
- **点面数据时点（PIT）**：截面类数据（`get_fundamentals(date=)`、`filter_stock_by_status(query_date=)`、`get_index_stocks(date=)`）默认取**前一交易日**，避免未来泄漏。
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

1. **设置类**：`set_benchmark`, `set_limit_mode`, `set_option`, `set_backtest`（生命周期）
2. **行情/历史**：`get_history`, `get_price`, `get_current_data`, `current_price`, `history`(别名)
3. **财务/估值 ORM**：`get_fundamentals`, `query`, `valuation`, `balance_statement`, `income_statement`, `cashflow_statement`, `eps`, `profit_ability`, `growth_ability`, `operating_ability`, `debt_paying_ability`
4. **股票状态/涨跌停**：`get_stock_status`, `check_limit`, `is_suspended`, `is_st`, `filter_stock_by_status`
5. **指数/ETF/转债/REITs**：`get_index_stocks`, `get_Ashares`, `get_etf_list`, `get_etf_info`, `get_cb_list`, `get_reits_list`
6. **交易日历**：`get_trade_days`, `get_trading_day`, `get_all_trade_days`, `get_kline_count`
7. **交易函数（即时）**：`order`, `order_value`, `order_target`, `order_target_value`
8. **交易函数（next_open）**：`order_target_value_next_open` 等（映射到 next_open 模式）
9. **`get_*` 指标封装**：`get_MACD`, `get_KDJ`, `get_RSI`, `get_CCI`
10. **账户/市场/文件**：`get_positions`, `get_position`, `get_open_orders`, `get_all_orders`, `context.portfolio`, `create_dir`, `get_research_path`, `is_trade`
11. **MyTT 指标库**（全局 `MyTT`）：`MA` `EMA` `MACD` `KDJ` `RSI` `BOLL` `CROSS` `RET` `HIGH` `LOW` `ABS` 等 50+
12. **A股规则**（全局 `shared_ashare_rules`）：`get_price_limit_pct` 等

**注入的全局对象**：`g`（策略状态容器）、`log`、`pandas as pd`、`numpy as np`、`MyTT`、`shared_ashare_rules`、`context`、`data`。

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
| 1 | 禁止任何 `import`（含 `import pandas`） | API/指标已由引擎注入；import 会破坏隔离并可能报错 |
| 2 | 禁止 `import duckdb` / 读 `.db` | 数据走 provider 适配层，强制隔离 |
| 3 | 代码后缀统一 | `.XSHG/.XSHE` 推荐；持仓用 `.SH/.SZ`；`check_limit` 返回 Ptrade 码；混用会导致 dict 匹配失败 |
| 4 | `set_backtest`/`set_limit_mode` 仅 `initialize` 内且 `not is_trade()` | 防止实盘态误设回测开关 |
| 5 | 整手下单 | A股/ETF 100 整数倍；优先 `order_target_value` 规避 |
| 6 | 检查 `order.status` | 涨停/跌停/废单会被拒，需处理 |
| 7 | 文件名非 `_` 开头 | PyQt 回测面板只列出该目录下非下划线 `.py`（见第 8 节） |

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

> 设计意图：用户把本文档发给智能体 → 智能体产出策略并自动存入 `strategies/` → 用户在 PyQt 面板直接选中、可视化回测，闭环无需手动搬运文件。

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
2. **完整代码**：单个 `.py`（零 import），可直接落盘运行。
3. **参数表**：`g.*` 与可调超参说明。
4. **运行方式**：GUI 选中路径 + CLI 命令。
5. **风险与数据前提**：所需数据表、涨跌停/T+1 假设、过拟合提示。
6. **落盘确认**：写出文件的绝对路径，及其在 PyQt 策略文件栏的显示说明。

---

## 附：与看海量化（khQuant）提示词工程的对照

| 维度 | 看海 khQuant | 本项目 QuantStudio |
|---|---|---|
| 提示词目标 | 引导 Agent 生成看海策略 | 引导 Agent 生成 Ptrade 兼容本地策略 |
| 策略形态 | `init` / `khHandlebar` 回调 | `initialize` / `handle_data` / `before_trading_start` / `after_trading_end` / `set_backtest` |
| 数据层 | 看海平台 | DuckDB 本地 provider（强制隔离，禁直连） |
| 信号 vs 执行 | `generate_signal` 解耦返回信号列表 | `order_*` 即时执行，返回 Order 对象 |
| 指标 | MyTT + 缠论 | MyTT + `get_MACD/KDJ/RSI/CCI`（无内置缠论） |
| 落盘 | 由用户处置 | **强制写入 `strategies/` 目录**供 GUI 直选（本工程独有要求） |
