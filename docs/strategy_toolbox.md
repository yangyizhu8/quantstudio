# QuantStudio 策略工具箱（PTrade 兼容回测 API）

本框架在 QuantStudio 本地回测引擎上**完整模拟 Ptrade 平台的 API 接口**，Ptrade 策略可
原封不动移植运行。数据 100% 来自 DuckDB（QuantStudio 数据管线产出），不依赖任何外部
回测平台。

> 对照参考：本文档结构对齐看海量化（khQuant）[3.2 策略工具箱](
> https://khsci.com/khQuant/chapter13/)，但全部条目均为**本框架实际实装**的 API。

---

## 0. 基本约定

- **策略文件无需任何 import 语句**：引擎加载策略时，自动把 `ptrade_import` 模块的全部
  名称注入到策略命名空间（含全部 API、MyTT 指标库、A股规则函数、`g`/`log`、
  `pandas`/`numpy`）。即 `from ptrade_import import *` 的效果由引擎代为完成。
- **策略隔离（强制）**：策略代码禁止直接 `import duckdb/sqlite3/sqlalchemy/pymysql`、
  `quantstudio.pipeline`、`quantstudio.backtest.providers`、`quantstudio._paths`，也
  禁止直接调用 `open/read_csv/read_parquet/read_sql/read_pickle`。数据访问必须走注入的
  Ptrade API。违反将被 `StrategyIsolationGuard` 拒绝加载。
- **代码归一化**：所有 API 内部按「裸码」归一化查找，`.SS`/`.XSHG`/`.SZ`/`.XSHE`/裸码
  互通等价（详见 §2 `data` 与 `CodeDict`）。
- **未来函数防护**：`get_history`/`get_price` 在分钟回测下锚定当前 bar 时间戳，截断未来
  数据；分钟频率数据缺失时**上抛 `FrequencyCapabilityError`**（不静默回退日线）。

---

## 1. 生命周期回调函数

| 函数 | 必填 | 签名 | 调用时机 / 用途 |
|------|------|------|----------------|
| `initialize` | **必需** | `def initialize(context):` | 回测开始前调用一次。配置基准、佣金、滑点、初始持仓、订阅池等。 |
| `before_trading_start` | 可选 | `def before_trading_start(context, data):` | 每交易日开盘前调用（09:31 前）。做盘前选股、预计算。 |
| `handle_data` | 可选 | `def handle_data(context, data):` | **逐 bar 心跳**（日线=每日、分钟=每根 bar）。主力策略逻辑在此编写。 |
| `after_trading_end` | 可选 | `def after_trading_end(context):` | 每交易日收盘后调用。做收盘统计、日志。 |
| `set_backtest` | 可选 | `def set_backtest(context, backtest_obj):` | 接收回测配置对象（实盘移植兼容钩子）。 |

> 与看海框架对照：看海本章仅明示 `init` 与 `khHandlebar` 两个回调；本框架提供 Ptrade
> 原生的完整 5 个生命周期钩子（`initialize`≈`init`，`handle_data`≈`khHandlebar`，并额外
> 提供 `before_trading_start`/`after_trading_end`/`set_backtest`）。

---

## 2. 全局对象与数据结构

| 对象 | 关键属性 / 用法 |
|------|----------------|
| `g`（GlobalVars） | 全局变量：`g.index`、`g.buy_stock_count`、`g.screen_stock_count`、`g.df`、`g.pre_position_list`。策略跨 bar 共享状态。 |
| `log` | `log.info/warning/error/critical/debug(msg)`，转发到框架 logger。 |
| `context`（Context） | `context.current_dt`（pd.Timestamp 当前交易日）、`context.previous_date`、`context.portfolio`、`context.blotter.current_dt`。 |
| `context.portfolio` | `cash`、`positions`（dict，键为 Ptrade 格式代码）、`market_value`、`total_value`。 |
| `data`（DataDict） | `data[code]` → `BarData`。支持 `.SS/.XSHG/.SZ/.XSHE/裸码` 互通取值；惰性构建。 |
| `BarData` | `open`/`high`/`low`/`close`/`price`(=close)/`volume`/`preclose`/`high_limit`/`low_limit`（涨跌停价由 A股规则精确计算）。 |
| `Position` | `sid`/`amount`/`enable_amount`(可卖，T+1 由引擎控)/`cost_basis`/`last_sale_price`/`avg_cost`/`market_value`。 |
| `CodeDict` | 归一化查值的 dict 子类，用于 `get_history(is_dict=True)`/`get_price(is_dict=True)`/`check_limit` 等返回，任意后缀键均可取值。 |

---

## 3. 工具函数与 API 函数（全量）

### 3.1 设置函数（回测参数）

| 函数 | 说明 |
|------|------|
| `set_benchmark(sids)` | 设置基准指数（如 `'000300'`）。 |
| `set_limit_mode(mode)` | 设置限价模式（`'LIMIT'`）。 |
| `set_universe(security_list)` | 设置股票池（DuckDB 模式无需订阅，空实现）。 |
| `set_commission(**kw)` | 设置佣金：`commission_ratio`/`min_commission`；`type='ETF'` 时关闭印花税与过户费。 |
| `set_slippage(slippage=0.1)` | 设置比例滑点（Ptrade 签名 `set_slippage(slippage=...)`）。 |
| `set_fixed_slippage(fixedslippage=0.1)` | 设置每股固定滑点（Ptrade 签名）。 |
| `set_backtest(*a, **kw)` | 兼容实盘钩子（回测空实现）。 |
| `set_volume_ratio(v=0.25)` | 设置成交量占比限制。 |
| `set_yesterday_position(poslist)` | 设置初始底仓（CSV dict 列表：`sid/amount/cost_basis`）。 |
| `set_parameters(**kw)` | 设置策略参数（交易端兼容，回测记录参数）。 |

### 3.2 行情 / 历史数据

| 函数 | 说明 |
|------|------|
| `get_history(...)` | 获取历史 K 线，**双签名兼容**：`get_history(security,count,unit='1d',fields=...)` 与 Ptrade 官方 `get_history(count,frequency='1d',field='close',security_list=...)`。支持 `is_dict=True` 返回 `{code:DataFrame}`。字段映射 `money→amount`、`price→close`、`factor→pctChg`。 |
| `get_history_batch(sec_list,count,unit='1d',fields=...,fq=...,include=...)` | B1 批量取数：强制 list 入参 + 返回 `CodeDict`，消除逐只 N+1 查询。 |
| `get_price(security,start_date,end_date,frequency='1d',fields,fq,count,is_dict)` | 按日期区间/数量取历史行情，返回 DataFrame 或 `CodeDict`。 |
| `attribute_history(security,count,unit='1d',fields)` | 取历史数据最近一行（单列 Series）。 |
| `current_price(security)` | 当前价。 |
| `get_current_data()` | 当日全市场行情 dict（`code→BarData`）。 |
| `get_snapshot(security,frequency='1d')` | 实时快照（回测返回当日 bar）。 |
| `get_Ashares(date=None)` | 全 A 股列表。 |

### 3.3 财务 / 估值（ORM + 多表）

| 函数 | 说明 |
|------|------|
| `query(*fields)` | Ptrade ORM 入口：`query(valuation.code, valuation.market_cap)` → `QueryBuilder`。 |
| `valuation` | ORM 表描述符：`valuation.code/market_cap/circulating_market_cap/pe_ratio/pb_ratio/ps_ratio/turnover_ratio` 等。 |
| `get_fundamentals(security,table='valuation',fields,date,...)` | 财务/估值数据。支持 10 张表（Ptrade 口径）：`valuation`(完整可用)、`balance/income/cashflow_statement`(暂无源表→返回带字段名的**空 DataFrame**)、`eps`/`profit_ability`/`growth_ability`(`fin_indicator` 部分字段)、`operating_ability`/`debt_paying_ability`(暂无源表→空)。 |
| `get_fundamentals_batch(sec_list,table,fields,date)` | B1 批量取数：强制 list，走预加载内存路径。 |

> 估值字段单位：市值类（market_cap/circ_mv/total_mv）为**亿元**。

### 3.4 股票状态 / 过滤 / 涨跌停 / 基础信息

| 函数 | 说明 |
|------|------|
| `filter_stock_by_status(stocks,filter_type,query_date)` | 过滤 ST/停牌/退市。4 种 `filter_type` 全支持：`'ST'`(官方 ST/*ST 或退市风险)、`'HALT'`(停牌)、`'DELISTING'`(已退市)、`'DELISTING_SORTING'`(退市整理期)。默认 `['ST','HALT','DELISTING']`。 |
| `check_limit(security,query_date)` | 涨跌停检查，返回 `CodeDict`：`1`涨停/`-1`跌停/`0`正常（主板10%/创业板20%/科创板20%/北交所30%/ST5%，见 `shared_ashare_rules`）。 |
| `get_stock_status(stocks,query_type='ST',query_date)` | 返回 `{code:bool}`：`'ST'`/`'HALT'`/`'DELISTING_SORTING'`。 |
| `get_stock_info(stocks,field)` | 证券元数据（上市日期等），返回按原代码键的 dict。 |
| `get_security_info(code)` | 证券基础信息对象（`.start_date`/`.display_name` 等），用于剔除次新股。 |
| `get_industry(code)` | 申万行业信息（`{'sw_l1':{...}}`），无数据返回 `None`。 |
| `get_industry_stocks(industry_code)` | 行业成份股（尾缀 `.XBHS`），无源表返回空 list。 |
| `get_stock_blocks(code)` | 板块归属（`HY/DY/GN/ZJHHY`），无源表返回 `None`。 |
| `get_stock_exrights(code,date)` | 除权除息信息，无源表返回 `None`。 |
| `get_stock_name(stocks)` | 股票名称（DuckDB 无名称字段时回退为代码）。 |

### 3.5 指数 / 板块 / ETF / 可转债 / REITs

| 函数 | 说明（本地数据可用性） |
|------|------|
| `get_index_stocks(index_code,date)` | 指数成份股（ReferenceDataProvider，可用）。 |
| `get_reits_list(date)` | 公募 REITs 列表，无源表返回空 list。 |
| `get_etf_list()` | ETF 代码列表（按代码段提取活跃品种，可用）。 |
| `get_etf_info(etf_code)` | ETF 信息，无申赎明细表→仅基础字段。 |
| `get_etf_stock_list(etf_code)` | ETF 成分券，无源表返回空 list。 |
| `get_etf_stock_info(etf_code,security)` | ETF 成分券信息，无源表返回空 dict。 |
| `get_ipo_stocks()` | 当日 IPO 申购标的，回测返回空 dict。 |
| `get_cb_list()` | 可转债代码列表（沪 11x/13x、深 12x，可用）。 |
| `get_cb_info()` | 可转债基础信息，无源表返回空 DataFrame。 |

### 3.6 交易日历

| 函数 | 说明 |
|------|------|
| `get_trading_day(day=0)` | 交易日偏移：`day>0`未来 N 天，`day<0`过去 N 天，返回 `datetime.date`（支持 `.strftime()`/日期减法）。 |
| `get_trade_days(start,end,count)` | 交易日列表，返回 ndarray。 |
| `get_all_trades_days(date)` | 全部交易日，返回 ndarray。 |
| `get_trading_day_by_date(query_date,day=0)` | 按日期取对应交易日。 |
| `get_current_kline_count()` | 当前交易日分钟 bar 数（日线回测为当日序号，收盘=240）。 |
| `get_frequency()` | 返回 `'daily'`。 |
| `get_business_type()` | 返回 `'stock'`。 |

### 3.7 交易函数（即时执行 / next_open）

| 函数 | 说明 |
|------|------|
| `order(security,amount,limit_price)` | 按股数下单：`amount>0`买、`amount<0`卖。**返回 Order 对象**（可查 `.status` 感知涨跌停阻断/资金不足）。 |
| `order_target(security,target_amount,limit_price)` | 调仓到目标股数（绝对）。 |
| `order_value(security,value,limit_price)` | 按金额下单（**增量**：`value>0`加仓、`value<0`减仓）。 |
| `order_target_value(security,value,limit_price)` | 调仓到目标市值（`value=0`全卖）。 |
| `run_daily(context,func,time='9:31')` | 注册定时任务（日线回测中等效每日调用；`initialize` 时仅注册不立即执行）。 |

成交价模式 `match_price_mode`：
- `close`/`open`：**即时执行**，调用后账户立即更新，下一行可见最新状态。
- `next_open`：T 日入队 + 预扣，T+1 开盘成交（避免穿越）。

持仓 / 订单查询：

| 函数 | 说明 |
|------|------|
| `get_positions(security=None)` | 全部/单只持仓（dict，后缀互通）。 |
| `get_position(security)` | 单只持仓；空仓返回 `amount=0` 的 `Position`（非 `None`）。 |
| `get_all_positions()` | 全部持仓（柜台格式 dict list）。 |
| `get_orders(security)` | 当日订单列表。 |
| `get_trades()` | 当日成交列表。 |
| `get_open_orders(security)` | 未成交订单（`next_open` 模式返回 pending 队列，即时模式为空）。 |
| `get_order(order_id)` | 按 id 查订单。 |
| `cancel_order(order_param)` | 撤单（`next_open` 模式移除 pending + 归还预扣；即时模式 no-op）。 |

### 3.8 技术指标（get_* 封装，底层 MyTT）

| 函数 | 说明 |
|------|------|
| `get_MACD(close,short=12,long=26,m=9)` | 返回 `(DIF,DEA,MACD)`。 |
| `get_KDJ(high,low,close,n=9,m1=3,m2=3)` | 返回 `(K,D,J)`。 |
| `get_RSI(close,n=6)` | RSI。 |
| `get_CCI(high,low,close,n=14)` | CCI。 |

### 3.9 市场 / 文件 / 账户 / 期货（降级实现）

| 函数 | 说明 |
|------|------|
| `get_market_list()` | 交易市场列表 DataFrame（SS/SZ/BJ/CSI/XBHS）。 |
| `get_market_detail(finance_mic)` | 市场产品代码列表（XSHG/XSHE/CSI/XBHS）。 |
| `get_trend_data(date,stocks,market)` | 集合竞价近似数据（用当日 open/量额近似）。 |
| `create_dir(user_path)` | 创建研究子目录（回测输出根下），返回 bool。 |
| `get_trades_file(save_path='')` | 导出成交记录为 CSV，返回路径。 |
| `convert_position_from_csv(path)` | 从 CSV 读底仓参数列表（`sid/enable_amount/amount/cost_basis`）。 |
| `get_instruments(contract)` | 证券元数据（期货不支持，返回空 dict）。 |
| `get_dominant_contract(contract,date)` | 主力合约，无期货数据返回 `{}`。 |
| `get_margin_rate(code)` | 保证金比例，无期货返回 `1.0`。 |
| `get_underlying_code(symbols)` | 关联代码，无源表返回 `{}`。 |
| `get_user_name(login_account=True)` | 资金账号（回测返回 `'BACKTEST_ACCOUNT'`）。 |
| `get_research_path()` | 研究根目录路径。 |

### 3.10 MyTT 技术指标库（50+，通达信/同花顺风格）

策略可直接调用（引擎已注入），无需 import：

- **基础运算**：`RD` `RET` `LAST` `REF` `DIFF` `STD` `SUM` `IF` `MAX` `MIN` `ABS` `LN` `POW` `SQRT` `SIN` `COS` `TAN` `CONST`
- **统计/形态**：`HHV` `LLV` `HHVBARS` `LLVBARS` `AVEDEV` `SLOPE` `FORCAST` `COUNT` `EVERY` `EXIST` `FILTER` `BARSLAST` `BARSLASTCOUNT` `CROSS` `LONGCROSS` `VALUEWHEN` `BETWEEN` `TOPRANGE` `LOWRANGE`
- **均线与核心指标**：`MA` `SMA` `EMA` `WMA` `DMA` `MACD` `KDJ` `RSI` `WR` `BIAS` `BOLL` `PSY` `CCI` `ATR` `BBI` `DMI` `TRIX` `CR` `EMV` `DPO` `BRAR` `MTM` `MASS` `ROC` `EXPMA` `OBV` `MFI` `ASI` `SAR`

> 均为数组运算（输入/输出 `numpy array` 或 `pandas Series`），与通达信公式语义一致。
> 本框架未内置看海的缠论工具箱（`KhChanLunTools`），如需分型/笔段可在策略层用 MyTT 自行实现。

### 3.11 A股交易规则（shared_ashare_rules，已注入）

| 函数 | 说明 |
|------|------|
| `is_price_limit_blocked(code,price)` | 是否涨停价封板。 |
| `is_t1_blocked(code,volume)` | T+1 可卖校验。 |
| `round_to_lot(amount,code)` | 整手规整（A股/ETF 100 整数倍，可转债 10 张）。 |
| `get_price_limit_pct(code)` | 涨跌幅限制（主板10%/创业板20%/科创板20%/北交所30%/ST5%）。 |
| `is_star_market(code)` / `is_chinext_market(code)` / `is_bse_market(code)` | 板块判定。 |
| `is_st_stock(code)` | 是否 ST/*ST。 |

---

## 4. 最小策略示例

```python
# 小市值轮动（示意）：仅需实现生命周期函数，无需 import
def initialize(context):
    set_benchmark('000300')
    set_commission(commission_ratio=0.0003, min_commission=5.0, type='stock')
    g.N = 20          # 选股数量
    g.hold_days = 15  # 调仓周期
    g.day = 0

def before_trading_start(context, data):
    # 盘前剔除 ST/停牌/退市，按流通市值升序取前 N
    pool = get_Ashares(context.previous_date)
    pool = filter_stock_by_status(pool, ['ST', 'HALT', 'DELISTING'])
    df = get_fundamentals_batch(pool, 'valuation', fields=['float_value'],
                                date=context.previous_date)
    if len(df) > 0:
        df = df.sort_values('float_value').head(g.N)
        g.targets = list(df.index)

def handle_data(context, data):
    g.day = (g.day + 1) % g.hold_days
    if g.day != 0:
        return
    for code in g.targets:
        if check_limit(code).get(code, 0) == 1:   # 涨停不追
            continue
        order_target_value(code, context.portfolio.total_value / g.N)

def after_trading_end(context):
    log.info("当日资产 %.2f", context.portfolio.total_value)
```

---

## 5. 与看海框架（khQuant）对照

| 维度 | 看海 khQuant（chapter13） | QuantStudio（本框架） |
|------|--------------------------|----------------------|
| 生命周期 | `init` + `khHandlebar`（本章明示） | `initialize` + `before_trading_start` + `handle_data` + `after_trading_end` + `set_backtest`（完整 Ptrade 钩子） |
| 数据查询 | `khGet/khPrice/khIndex/khHas` | `get_history`/`get_price`/`attribute_history`/`current_price`/`get_current_data`/`get_snapshot` + `CodeDict` 归一化 |
| 财务/估值 | （ORM 不在本章） | `query`/`valuation` ORM + `get_fundamentals`（10 张表，valuation 完整） |
| 历史/指标 | `khHistory` + MyTT + 缠论 `KhChanLunTools` | `get_history`/`get_price` + MyTT（50+）+ `get_MACD/KDJ/RSI/CCI`（无内置缠论） |
| 交易信号 | `generate_signal`/`calculate_max_buy_volume`/`round_price`（返回信号列表，与回测解耦） | `order`/`order_value`/`order_target`/`order_target_value`（**即时执行**，返回 Order 对象，与引擎紧耦合） |
| 时间工具 | `is_trade_time`/`is_trade_day`/`get_trade_days_count` | `get_trading_day`/`get_trade_days`/`get_all_trades_days`/`get_trading_day_by_date`/`get_current_kline_count` |
| ETF 工具 | `is_etf`/`is_t0_etf` | `get_etf_list`/`get_etf_info`/`get_etf_stock_list` 等（无 `is_etf` 布尔，改用列表/信息类 API） |
| 辅助 | `get_stock_names`/`normalize_stock_code` | `get_stock_name`/`get_security_info`/`normalize_*`（内部归一化，策略一般无需主动调用） |
| 导入方式 | `from khQuantImport import *` | 引擎自动注入 `ptrade_import` 全部名称（策略零 import） |
| 数据来源 | 看海平台 | DuckDB（QuantStudio 数据管线产出），100% 本地 |

---

## 6. 运行前置

- **数据库就绪**：`data/quantstudio.db`（约 12GB，不随 git 分发，按 `data/README.md` 单独获取）。
- **凭证就绪**：`config/secrets.env`（填 `TUSHARE_TOKEN` 等，程序启动时自动加载）。
- **运行**：`python main_gui.py` 或在 `python -m quantstudio.pipeline.daemon` 之外，用
  `StrategyRunner`/`BacktestEngine` 加载策略文件即可，详见 README「快速开始」。

---

*本文档条目均来自源码实装（`quantstudio/backtest/ptrade_api.py`、`ptrade_import.py`、
`strategy_runner.py`、`libs/MyTT.py`、`libs/shared_ashare_rules.py`），与看海框架仅为
结构对照，不代表两框架 API 完全等价。*
