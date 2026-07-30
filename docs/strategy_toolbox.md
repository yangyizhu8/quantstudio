# QuantStudio 策略工具箱（PTrade 兼容回测 API）

本框架在 QuantStudio 本地回测引擎上对齐**已登记并验证的 PTrade 回测公共 API 子集**，同时提供明确标记的 QuantStudio 本地扩展。
只有通过 PTrade Profile Validator 的策略才可声明可移植；本地运行成功不等于真实平台兼容。数据 100% 来自 DuckDB（QuantStudio 数据管线产出），不依赖任何外部
回测平台。

---

## 0. 基本约定

- **运行时依赖按目标分层**：QuantStudio 本地加载时会注入 `ptrade_import` 的 API、`g`/`log` 以及 `np`/`pd`；真实 PTrade/IQEngine 不注入 `numpy`/`pandas` 别名。双端/PTrade 策略使用数组或 DataFrame 时必须显式写 `import numpy as np` / `import pandas as pd`。普通计算库 import 允许，数据库驱动、QuantStudio 内部模块和直接文件 I/O 仍禁止。
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
| `set_backtest` | 仅 QuantStudio 本地可选 | `def set_backtest(context, backtest_obj):` | 本地回测扩展/兼容钩子；真实 PTrade/IQEngine 无此公共生命周期 API。 |

---

## 2. 全局对象与数据结构

| 对象 | 关键属性 / 用法 |
|------|----------------|
| `g`（GlobalVars） | 全局变量：`g.index`、`g.buy_stock_count`、`g.screen_stock_count`、`g.df`、`g.pre_position_list`。策略跨 bar 共享状态。 |
| `log` | `log.info/warning/error/critical/debug(msg)`，转发到框架 logger。**支持 printf 风格多参**：`log.info("信号=%s 条数=%d", src, n)` 按 `%` 风格格式化（对齐真实 Ptrade）；同时兼容 f-string / 单字符串写法。 |
| `context`（Context） | `context.current_dt`（pd.Timestamp 当前交易日）、`context.previous_date`、`context.portfolio`、`context.blotter.current_dt`。 |
| `context.portfolio` | `cash`、`positions`（dict，键为 Ptrade 格式代码）、`market_value`、`total_value`；**PTrade 标准属性**：`portfolio_value`（= 组合总净值，同 `total_value`）、`positions_value`（= 持仓市值，同 `market_value`）。 |
| `data`（DataDict） | `data[code]` → `BarData`。支持 `.SS/.XSHG/.SZ/.XSHE/裸码` 互通取值；惰性构建。 |
| `BarData` | `open`/`high`/`low`/`close`/`price`(=close)/`volume`/`preclose`/`high_limit`/`low_limit`（涨跌停价由 A股规则精确计算）。 |
| `Position` | `sid`/`amount`/`enable_amount`(可卖，T+1 由引擎控)/`cost_basis`/`last_sale_price`/`avg_cost`/`market_value`。 |
| `CodeDict` | 归一化查值的 dict 子类，用于 `get_history(is_dict=True)`/`get_price(is_dict=True)`/`check_limit` 等返回，任意后缀键均可取值。 |

---

## 3. 工具函数与 API 函数（全量）

### 3.1 设置函数（回测参数）

| 函数 | 说明 |
|------|------|
| `set_benchmark(sids)` | **PTrade Profile 1.7.0 已登记**。设置基准指数；双端源码使用 PTrade 后缀，例如 `set_benchmark('000300.SS')`。 |
| `set_limit_mode(mode)` | 设置限价模式（`'LIMIT'`）。 |
| `set_universe(security_list)` | 设置股票池（DuckDB 模式无需订阅，空实现）。 |
| `set_commission(**kw)` | 设置佣金：`commission_ratio`/`min_commission`；`type='ETF'` 时关闭印花税与过户费。 |
| `set_slippage(slippage=0.1)` | 设置比例滑点（Ptrade 签名 `set_slippage(slippage=...)`）。 |
| `set_fixed_slippage(fixedslippage=0.1)` | 设置每股固定滑点（Ptrade 签名）。 |
| `set_backtest(*a, **kw)` | **QuantStudio 本地扩展**（回测空实现）。真实 PTrade/IQEngine 无此公共 API；双端/PTrade Validator 阻断。 |
| `set_volume_ratio(v=0.25)` | 设置成交量占比限制。 |
| `set_yesterday_position(poslist)` | 设置初始底仓（CSV dict 列表：`sid/amount/cost_basis`）。 |

> 可移植性红线：`set_backtest()`、`is_trade()` 仅供 QuantStudio 本地单端策略；双端/PTrade 源码不得调用。日志请使用 `log.warning(...)`，禁止 `log.warn(...)`。

> **签名默认拒绝**：双端设计的 `components.required_apis` 和源码中的外部顶层调用都必须登记在 `skills/quantstudio-strategy-compiler/references/ptrade-api-signatures.json`。未登记 API 会直接 `BLOCK`，不能归类为用户可确认的执行近似。
| `set_parameters(**kw)` | 设置策略参数（交易端兼容，回测记录参数）。 |

### 3.2 行情 / 历史数据

> 前复权为框架默认：注入 API（`get_history`/`get_price`/`get_history_batch`）与底层数据适配层（`providers.get_bars`/`get_bars_by_count`）默认均为 `fq='pre'`；策略不传 `fq` 即获前复权价，需不复权须显式 `fq=None`。
>
> **撮合/估值同为前复权口径（前复权闭环）**：引擎每日全市场快照 `query_daily_snapshot`（成交价、持仓估值、`data[code].price`、`BarData` OHLC 的唯一来源）将 OHLC 映射为前复权列（`*_front`，缺失回退原始价），`preClose` 按 `close_front/close` 同因子缩放，保证 `(close-preClose)/preClose` 与真实日收益一致。因此 ETF 份额拆分、股票分红除权不会产生价格缺口与虚假盈亏；代价是成交价为前复权价（分红等价于自动再投资，前复权回测标准口径）。`pctChg`/`volume`/`amount` 保持原始口径。
>
> **成交额列双端契约（B1，返回端逆映射）**：DB 物理列为 `amount`，Ptrade 官方契约列名为 `money`。`get_history`/`get_price` 返回的 DataFrame 在含 `amount` 列时同步追加**同值 `money` 列**（列尾追加，`amount` 保留、数值不变）；请求 `fields=['money']` 时也正确返回 `money` 列。双端/PTrade 目标策略**必须只读 `money`**（读 `amount`/`close_front`/`volume_front`/`open_front` 等本地物理列名会被 Validator 以 `PTRADE-LOCAL-COLUMN` 规则阻断）；本地单端策略读 `amount` 仍兼容。纯返回端别名，黄金回归已证明对回测数值零影响。

> Agent-first `agent_strategy_design.json` 只允许 `execution_price_basis="pre_adjusted_price"`；信号、撮合、成交、现金和估值不得声明为 raw 口径。

| 函数 | 说明 |
|------|------|
| `get_history(...)` | 获取历史 K 线，**双签名兼容**：`get_history(security,count,unit='1d',fields=...,fq='pre',include=False)` 与 Ptrade 官方 `get_history(count,frequency='1d',field='close',security_list=...,fq='pre',include=False)`。**fq 默认 `'pre'`（前复权）**。支持 `is_dict=True` 返回 `{code:DataFrame}`（本地适配层）。**PTrade Profile 1.8.0 返回形状契约**：真实平台上 `is_dict=True` 的 mapping item 可能是 pandas DataFrame / NumPy structured array / recarray，`item[field]` 可能是 Series 或 ndarray；双端/PTrade 源码必须先 `np.asarray(item[field], dtype=float)`（或 `hasattr(values,'values')` 守卫的 helper）归一化再参与数值计算，对 history item 的无保护 `.values`/`.iloc`/`.loc`/`.to_numpy()`/`.columns`/`.index`/`.empty` 访问会被 Validator 阻断。字段映射 `money→amount`、`price→close`、`factor→pctChg`；**B1 返回端逆映射**：返回 DataFrame 含 `amount` 列时同步追加同值 `money` 列（Ptrade 契约名，双端策略只读 `money`）。**`include` 控制历史数据可见边界（防未来函数）**：`include=False` 截止 `previous_date`（不含当前交易日），`include=True` 延伸至 `current_date`（含当前交易日）。**不同 `include` 值不会共享历史查询缓存（缓存键含 `include`）**，混合调用须分别取数。**多表路由（F5/F6）**：普通股票→`stock_daily`，ETF→`etf_daily`，普通指数与申万行业指数（801xxx）→统一 `index_daily`（指数历史不足时按既有契约用 `INDEX_ETF_MAP` 跟踪 ETF 代理，如 000300→510300）；`fq='pre'` 对指数回退原始 OHLC（指数无复权列，绝不套用 ETF 前复权逻辑）。 |
| `get_history_batch(sec_list,count,unit='1d',fields=...,fq='pre',include=...)` | **QuantStudio 本地扩展**：强制 list 入参 + 返回 `CodeDict`，消除策略侧逐只 N+1 调用；**复用 `get_history` 的 `get_bars_by_count` 活跃路径**（不读取已停用的 `_preload_daily` 全市场缓存，当前底层仍按代码逐只查询，并非单次批量扫描）。仅本地单端策略允许；双端/PTrade 目标由 Validator 阻断。**fq 默认 `'pre'`**。 |
| `get_price(security,start_date,end_date,frequency='1d',fields,fq='pre',count,is_dict)` | 按日期区间/数量取历史行情，返回 DataFrame 或 `CodeDict`。**fq 默认 `'pre'`（前复权）**。**B1 返回端逆映射**：支持 `fields=['money']` 请求成交额；返回含 `amount` 列时同步追加同值 `money` 列（Ptrade 契约名）。 |
| `attribute_history(security,count,unit='1d',fields)` | 取历史数据最近一行（单列 Series）。 |
| `current_price(security)` | 当前价。 |
| `get_current_data()` | 当日全市场行情 dict（`code→BarData`）。 |
| `get_snapshot(security,frequency='1d')` | 实时快照（回测返回当日 bar）。 |
| `get_Ashares(date=None)` | **PTrade Profile 1.7.0 已登记**。全 A 股列表；传日期时双端源码统一使用 `YYYYmmdd` 字符串。 |

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
| `get_stock_status(stocks,query_type='ST',query_date=None)` | **PTrade Profile 1.7.0 已登记**，返回 `{code:bool}`。可移植值仅为 `'ST'`/`'HALT'`/`'DELISTING'`；`query_date` 使用 `YYYYmmdd`。`'DELISTING_SORTING'` 仅保留为本地兼容别名，双端源码禁止使用。 |
| `get_stock_info(stocks,field)` | **股票+ETF 统一证券元数据（F2，PTrade Profile 1.9.0 登记）**。返回按调用方原代码键的 dict：`stock_name`、`stock_type`（`'stock'`/`'etf'`）、`listed_date`/`de_listed_date`（`YYYY-MM-DD`，未退市为 `None`）、`exchange_type`、`code`。`field` 传字符串或列表均按名过滤。**股票行为与历史完全一致（2026-07-27 审核修订）**：`stock_name`=裸码、`listed_date`=`stock_daily` 首根K线。**仅扩展 ETF**：`stock_name`=真实名称、`stock_type='etf'`、`listed_date`=`etf_basic.list_date`（缺失时按 etf_basic 管线契约用首个 `etf_daily` 交易日补齐并标记 `etf_daily_min_fallback`）、`de_listed_date`=`etf_basic.delist_date`。未知代码保持兼容空值行为（`stock_type='stock'`、日期为 `None`、名称为入参代码）。本地 ETF 元数据支持 ≠ PTrade 真实 ETF 支持（PTrade 运行未验证）。 |
| `get_security_info(code)` | 本地扩展。证券基础信息对象（`.start_date`/`.display_name` 等），用于剔除次新股；底层同 F2 统一元数据层，股票与 ETF 均可返回上市日。 |
| `get_industry(code)` | **申万一级行业（PTrade Profile 1.9.0 登记，capability = APPROXIMATION_REQUIRES_CONFIRMATION，非 PIT READY）**。返回 `{'sw_l1': {'industry_code','industry_name','classification_system','classification_version'}}`。回测上下文自动以**当前回测日期** as-of 查询正式 `industry_membership`（SW/SW2021）有效区间（`effective_from <= d AND (effective_to IS NULL OR effective_to >= d)`）。**关键语义边界（F4 审核，2026-07-27）**：官方 `index_member` 仅提供 `in_date`/`out_date`，**无任何冲突裁决规则**；故 canonical 表**原样保留重叠区间**（如 SW2021 重新分类致同一证券某日同属新旧两类），**不应用任何自定义“生效日较新者胜”裁决**。as-of 命中重叠区间时 `get_industry` **抛 `ReferenceDataCapabilityError`（fail-closed）**，绝不返回任意自定义裁决近似，能力明确标注 APPROXIMATION_REQUIRES_CONFIRMATION（因 canonical 表原样保留重叠区间、非 PIT READY，但运行时重叠一律 fail-closed）。无有效历史归属返回 `None`，**绝不使用最新行业回填过去**。正式表缺失时抛 `ReferenceDataCapabilityError`（fail-closed），**绝不回退 legacy `sw_industry` 快照**（该表仅为审计保留）。 |
| `get_industry_stocks(industry_code)` | 行业成份股（尾缀 `.XBHS`），无源表返回空 list。 |
| `get_stock_blocks(code)` | 板块归属（`HY/DY/GN/ZJHHY`），无源表返回 `None`。 |
| `get_stock_exrights(security, date=None)` | **PTrade Profile 1.10.0 已登记**。获取证券除权除息信息。完整签名：`get_stock_exrights(security, date=None)`，contexts: research/backtest/trade。返回 DataFrame（date 索引，8 列 PTrade 兼容：allotted_ps/rationed_ps/rationed_px/bonus_ps/exer_forward_a/exer_backward_a/bexer_backward_a/b），或 `None`（无数据/缺表/`date=None`）。portable usage 必须显式传 `date`；源表 `stock_dividend`（tushare 权威源），schema 兼容旧列。受 Tushare 接口频率限制（~200 次/分钟），批量调用需间隔。 |
| `get_stock_name(stocks)` | 股票名称（DuckDB 无名称字段时回退为代码）。 |

### 3.5 指数 / 板块 / ETF / 可转债 / REITs

| 函数 | 说明（本地数据可用性） |
|------|------|
| `get_index_stocks(index_code,date)` | **指数成份股，严格 PIT（F3，PTrade Profile 1.9.0 登记）**。显式 `date`：只取不晚于该日的**最近 status='complete' 快照**（as-of），不是历史并集、绝不使用未来快照；无历史快照返回空（fail-closed）。**完整性只由 `index_constituents_snapshot_meta` 批次契约在打点写入时判定**（n/expected_count/status，不依赖未来数据）；无 meta 的指数 fail-closed。回测中未传 `date` 时自动注入**当前回测日期**（绝不读数据库全局最新快照）；非回测直接调用保留“最新快照”兼容。返回标准 `.SS/.SZ` 代码、去重、顺序确定。指数代码支持 `000300`/`000300.SH`/`.SS`/`.XBHS`。 |
| `get_reits_list(date)` | 公募 REITs 列表，无源表返回空 list。 |
| `get_etf_list()` | **PTrade 同名兼容 API**。PTrade 回测 profile 不可用；Validator 对所有回测策略阻断调用，避免“本地通过、PTrade 上传失败”的假兼容。不得扩展成本地动态池。 |
| `get_etf_list_local(query_date=None,etf_type="equity",active_only=True)` | **QuantStudio 本地回测专用扩展 API**。`query_date=None` 时使用当前回测日；经 `ReferenceDataProvider`/DuckDB 适配层从 `etf_basic` 元数据与 `etf_daily` 历史可用性构造 PIT ETF 池，返回 `.SS/.SZ` 代码。`equity` 仅返回分类为境内股票型且 `is_cross_border=false` 的 ETF。仅本地单端策略允许。 |
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
| `run_daily(context,func,time='9:31',reference_security=None)` | **PTrade Profile 1.7.0 已登记**，仅在 `initialize` 注册。日线 Profile 的精确盘中时刻不可证明，`09:31` 等盘中调度必须选择分钟 Profile。 |

成交价模式 `match_price_mode`：
- `close`/`open`：**即时执行**，调用后账户立即更新，下一行可见最新状态。
- `next_open`：T 日入队 + 预扣，T+1 开盘成交（避免穿越）。

调仓模式 `rebalance_mode`（F1，`EngineConfig.rebalance_mode` 单一配置路径）：
- `legacy`（默认）：现有单订单/pending 行为，修复前后结果逐项一致。
- `callback_basket`：basket 原子再平衡（先卖后买），**仅 daily-bar-v1 + `next_open` 激活**，结果导出记录 `engine_semantics_version=0.4.0-next_open_basket`（`next_open + legacy` 保持 `0.2.0-next_open_pending`）；分钟 Profile 显式拒绝。生命周期边界不变：只有 `handle_data` 的订单进入 basket，`run_daily`/`before_trading_start` 永不进入；需要 basket 的策略必须把调仓下单放入 `handle_data`。PyQt 回测面板提供通用下拉框透出（内部值 `legacy`/`callback_basket`），`close`/`open + callback_basket` 在点击运行前被 GUI 阻断。

持仓 / 订单查询：

| 函数 | 说明 |
|------|------|
| `get_positions(security=None)` | **PTrade Profile 1.7.0 已登记**。全部/单只持仓（dict，后缀互通）。 |
| `get_position(security)` | **PTrade Profile 1.7.0 已登记**。单只持仓；空仓返回 `amount=0` 的 `Position`（非 `None`）。 |
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
| `load_research_signals(csv_path, fallback=None)` | 注入 API：由**框架侧**读取外部研报/信号 CSV（仅保留买入/增持，自动兼容 `评级代码` 混用 `006/007` 与单数字 `6/7`、发布日期带时间戳后缀等脏格式），返回 `(rows, source)`。策略需要外部文件数据（如研报/信号表）时必须调用它，**禁止策略内自行 `open()/read_csv()`**（会被 `StrategyIsolationGuard` 拦截）。CSV 缺失/解析失败回退 `fallback`。 |
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

---

## 4. QFQ 重锚引擎（pipeline 级，fresh_staged / ratio 双模型）

> 模块：`quantstudio.pipeline.qfq_reanchor_engine`。**这是 pipeline 编排层 API，不是注入策略的 API**——
> 策略代码不应直接调用。本节供 pipeline 调用方 / Agent 编排重锚任务时参考（门禁要求记录）。
> 决策文档：`docs/qfq-reanchor-minute-model-decision-20260727.md`（B-1 已于 2026-07-27 批准并实现）。

### 4.1 入口 API

```python
apply_reanchor_for_security(
    conn,                              # 显式 DuckDB 连接（调用方持有，非内部新建）
    *, asset_type: str,               # "STOCK" | "ETF"
    code: str,
    fresh_daily: pd.DataFrame,        # 日线前复权（ratio 模式用）
    calendar: CalendarService,        # 必须；fresh 分钟逐自然日校验 is_trading_day
    freqs: Sequence[str] = ("1min",),
    golden_minutes: Optional[pd.DataFrame] = None,   # ratio 模式方法 A 黄金抽验
    ex_dates_ms: Sequence[int] = (),
    allow_multi_segment: bool = False,
    tol: Optional[ReanchorTolerances] = None,
    price_source: str = "xtquant",
    trigger_surface: str = "batch2",
    event_id: Optional[str] = None,
    list_date_ms: Optional[int] = None,
    # —— 模型选择（B-1 批准边界，铁律）——
    model: str = "ratio",             # "ratio" | "fresh_staged"，必须显式
    model_reason: Optional[str] = None,   # fresh_staged 必填；写入事件审计
    fresh_minutes: Optional[pd.DataFrame] = None,  # fresh_staged 必填
    fresh_source: Optional[str] = None,        # 事件审计：如 "xtquant"
    fresh_capture_id: Optional[str] = None,    # 事件审计：采集批次 id
    fresh_metadata_sha256: Optional[str] = None,  # 事件审计：fresh 元数据哈希
) -> ReanchorResult
```

### 4.2 模型选择语义（禁止静默切换）

- **`ratio`**（默认）：方法 B（按 freq 独立、OHLC 交叉验证、稳定簇）+ 方法 A 黄金抽验（3 日 × 5 bar，09:30 不计入）。原行为逐位不变；**禁止**传 `fresh_minutes`（防呆：`ValueError`）。
- **`fresh_staged`**：fresh xtquant 分钟前复权**逐值写入**四 `*_front` 列。必须提供 `fresh_minutes`
  （列 = `code/time/freq?` + OHLC raw + 四 `*_front`；多 freq 须含 `freq` 列），且 `model_reason` 必填。
- 引擎**不存在**「ratio BLOCK → fresh_staged 静默回退」路径；切换模型必须由调用方显式改写 `model` 并书面留痕 `model_reason`。

### 4.3 事务与四态事件审计

单证券调用在一个显式连接上完成。所有 `blocked`/`rolled_back`/`failed`/`committed` 事件
**都记录** `model` / `model_reason` / `model_audit`（含 `fresh_source`、`fresh_capture_id`、
`metadata_sha256`、`tick_size`、`freqs`、`minute_coverage` / precheck 摘要）：

| status | 触发 | 事务 |
|--------|------|------|
| `committed` | 全部通过 | anchor 推进 + 价格修正**同一事务** |
| `blocked` | 方法 B/A 或数据契约失败（含周末/未知日、raw NULL/NaN/Inf/≤0） | 回滚 + 独立短事务 `blocked` 事件 |
| `rolled_back` | COMMIT 前 postcheck 失败 | 回滚 + 独立短事务 `rolled_back` 事件 |
| `failed` | 其它异常 | 记录 `failed` 事件后**重新抛出** |

三种失败路径都**绝不推进 anchor**，绝不污染已提交数据。

### 4.4 纵深防御 postcheck（COMMIT 前硬门禁）

`minute_staged_match`（精确）> `scale_consistency`（容差）> `minute_tick_error`（≤1 tick）> `minute_raw_match`（eps=1e-9 最精确）：

- **`minute_raw_match`**：先显式拦截 raw 一侧 `IS NULL OR NOT isfinite() OR <=0`（SQL 三值逻辑陷阱：
  `ABS(NULL-x)>eps` 结果为 NULL，WHERE 按非真过滤会**静默漏检**），再比逐 bar abs diff；`n_invalid>0` 直接抛 `minute_raw_match` 并整券回滚。
- **`minute_tick_error`**：同样先拦截 NULL/NaN/Inf/≤0，再比 `ABS(diff) <= tick_size`。
- 覆盖率（coverage）：`fresh_minutes` 须覆盖目标 freq 全交易日，缺失即 BLOCK；结果 `ReanchorResult.minute_coverage` 写入事件审计。

### 4.5 tick_size 资产路由（第六轮阻断 4）

`tick_size` **不能写死 0.01**，须按资产/市场路由：`resolve_tick_size(asset_type, tol)`
= `STOCK=0.01` / `ETF=0.001`；显式 `tol.tick_size` 可覆盖；未知资产抛异常。
事件 `model_audit.tick_size` 记录实际使用值（合成/真实回归均校验）。

### 4.6 交易日历校验（第六轮阻断 2）

`stage_fresh_minutes(conn, asset_type, code, freq, fm, tol, calendar=...)` 对**每个**自然日
调用 `CalendarService.is_trading_day` 校验：

- 周末或非开市日 → 整券 BLOCK（`fresh_minutes_non_trading_day`）。
- 日历 provider 未覆盖的未知日 → 整券 BLOCK（`fresh_minutes_unknown_day`）。
- `calendar=None` → 直接抛 `ValueError`（强制调用方显式注入日历）。

钟面时刻合法（如周六 09:31）≠ 自然日开市，必须逐日校验；session-aware 窗口为 `[09:31,11:30] ∪ [13:01,15:00]`（09:30 不计入连续竞价）。

### 4.7 主动因子刷新与 detector degraded（常驻编排器，pipeline 调用方参考）

> 模块：`quantstudio.pipeline.qfq_factor_refresh.QFQFactorRefresher` +
> `qfq_maintenance.resolve_ts_codes` + `aligner.raw_to_tushare_ts_code`。
> 策略代码不应直接调用。本节供常驻 QFQ 编排器 / Agent 编排因子刷新任务时参考。

常驻 QFQ 编排器在事件发现之前主动刷新股票 `adj_factor`（写 `adj_factor` 表）与 ETF
`fund_adj`（写独立 `fund_adj` 表），避免陈旧因子快照被误解释为"今天没有事件"。

**配置与启用**：
- `qfq_orchestrator.factor_refresh_enabled` 默认 `False`（独立 opt-in）。
  主动因子刷新默认关闭。生产启用前必须通过股票/ETF ts_code 转换、刷新失败降级、
  水位 hold 和全量回归测试，并取得用户明确部署确认。

**degraded 契约**（`RefreshResult`）：
- 某资产类别**全部逐码请求失败**（`fetch_adj_factor` 抛 `FactorRefreshError`）→
  `degraded=True` → daemon 四价格表水位强制 hold、`qfq_cycle_run.detector_degraded=1`。
- **正常返回空数据不降级**：区间内无复权事件时 Tushare 返回空 DataFrame（不抛异常），
  返回 0 行，`degraded=False`（不误报）。
- **部分码失败不降级**：保留成功结果落库，失败码仅 WARNING。这是当前明确但有风险
  的契约（部分失败码可能继续使用旧快照），是否升级为"任意单码失败即 degraded"另立
  后续正确性变更审核。

**裸码 → Tushare ts_code 转换**（C3 修复）：
- `QFQFactorRefresher.refresh` 在调用 Tushare `adj_factor`/`fund_adj` 前，在各资产类别
  **自己的 try 块内**用 `resolve_ts_codes(codes, asset_type, main_db)` 把裸码解析为
  Tushare ts_code。股票转换异常不影响 ETF（跨资产类别隔离）。
- `resolve_ts_codes`：**已带合法 Tushare 后缀（.SH/.SZ/.BJ，含 .SS→.SH）的输入幂等保留、
  不被元数据覆盖**；对裸码优先查 `stock_basic`/`etf_basic` 元数据表权威 ts_code（单次参数化
  `WHERE code IN (SELECT unnest(?))`，输出顺序/数量与输入一致、不丢码）；裸码元数据 miss
  时用资产类型感知前缀规则 fallback；未知首位前缀防御性 fallback 到 .BJ 并聚合 WARNING。
- `raw_to_tushare_ts_code(code, asset_type)`：纯前缀规则（无 DB 依赖）。STOCK 6→SH /
  0,3→SZ / 4,8→BJ；ETF 5→SH / 1→SZ / 其余→BJ（防御性）。已带 `.SH/.SZ/.BJ` 幂等，
  兼容 `.SS`→`.SH`，未知后缀抛 `ValueError`。
- `fetch_adj_factor` 入库时用 `normalize_code(ts_code, "tushare_to_raw")` 转裸码，故
  落库仍为裸码口径，与 `get_*_universe` 返回的裸码语义一致。
- **范围说明**：本转换仅作用于 `QFQFactorRefresher` 的主动刷新路径，不覆盖 daemon 其它
  Tushare 因子调用方。`daemon._fetch_adj_factor` 收到裸 ETF 时仍依赖现有 `market_of_code()`
  （6→SH/0,3→SZ/其余→BJ），对 5/1 开头的 ETF 会错误推导 `.BJ`，作为独立残余风险登记。

**RateLimiter**：限流统一由 `fetch_adj_factor` 内部逐码 `adapter.rate_limiter.acquire()`
负责（每码一次）。`refresh` 的 `rate_limiter` 参数仅为兼容保留，不再主动调用。

**职责分离**：Tushare 负责因子（`adj_factor`/`fund_adj`），xtquant 负责价格
（fresh_capture 单源锁定）；因子刷新只写 SQLite 因子表，绝不触碰四价格表。

> 本框架未内置缠论工具箱（分型/笔段），如需可在策略层用 MyTT 自行实现。

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
# QuantStudio-only 小市值轮动示意（使用本地 batch/check_limit 扩展，不声明 PTrade 可移植）
def initialize(context):
    set_benchmark('000300.SS')
    set_commission(commission_ratio=0.0003, min_commission=5.0, type='stock')
    g.N = 20          # 选股数量
    g.hold_days = 15  # 调仓周期
    g.day = 0

def before_trading_start(context, data):
    # 盘前剔除 ST/停牌/退市，按流通市值升序取前 N
    api_date = context.previous_date.strftime('%Y%m%d')
    pool = get_Ashares(api_date)
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

## 6. 运行前置

- **数据库就绪**：`data/quantstudio.db`（约 12GB，不随 git 分发，按 `data/README.md` 单独获取）。
- **凭证就绪**：`config/secrets.env`（填 `TUSHARE_TOKEN` 等，程序启动时自动加载）。
- **运行**：`python main_gui.py` 或在 `python -m quantstudio.pipeline.daemon` 之外，用
  `StrategyRunner`/`BacktestEngine` 加载策略文件即可，详见 README「快速开始」。

## 6.1 数据质量契约（W2-0.9，策略层铁律）

策略生成与回测**只能依赖 canonical data pipeline 的最终已通过数据契约**，不得绕过：

- **authority-locked 表（fin_indicator / stock_dividend）**：最终 `data_source` 集合必须恰好为 `{tushare}`（`allow_fallback=false`）。经 W2 全量回填 + authority reconciliation 后，旧 NULL/akshare 历史行已被清理。**策略代码不得在表内手工补救 NULL 或混源数据**——若发现 NULL/混源，说明数据契约未通过，应报告数据问题而非在策略层打补丁。
- **不得在策略内手工 DELETE/UPDATE 修复数据**：数据修复必须经 canonical pipeline（staging → audit → promotion），策略层只读已通过审计的数据。
- **依赖字段（np_yoy/or_yoy/tr_yoy/diluted_eps/cash_div_before_tax/cash_div_after_tax/div_proc）**：这些字段的存在与有效非零是回填有效性门控的结果（`baseline_delta_passed=true`，目标表零 error）。策略应假设它们已就绪；若回测报字段缺失/全 NULL，先查 staging audit 是否通过，而非改策略。
- **div_proc 口径**：stock_dividend 仅保留 `div_proc='实施'` 的记录（adapter 过滤 + writer DDL 类型保护，确保 VARCHAR 值不被 numeric coercion 吞掉）。

---

> **动态 ETF 池目标规则**：双端（QuantStudio + PTrade）策略必须使用用户在 R2/R2.5 明确确认的静态 ETF 白名单，两端共用同一列表，并禁止 `get_etf_list_local()`/`get_history_batch()`；仅 QuantStudio 本地策略才允许动态 API。`get_etf_list_local()` 只负责 ETF 分类、上市/退市时间、查询日期、历史数据存在性与代码格式，MA、动量、流动性、异常放量、TopN 等策略逻辑必须留在策略文件。

> **元数据前置条件**：本地数据库必须存在 `etf_basic`。使用 `python scripts/sync_etf_basic.py --db data/quantstudio.db` 从 Tushare `fund_basic` 同步，并以 `etf_daily` 首末行情补齐缺失日期。缺表时接口抛出明确的 `ReferenceDataCapabilityError`，不会降级冒充“境内股票型 ETF”。

## 用户PyQt候选文件

`<strategy_id>__candidate_quantstudio.py` 只用于R4后由用户在PyQt执行R5。它必须带有非正式/禁止上传PTrade标记和候选哈希。策略内不得写死回测日期；实际区间由用户设置。R5 证据 2.0 必须绑定真实回测产物：结果目录 + `config.csv`/`daily_stats.csv`/`trades.csv`/运行日志及各自 SHA-256；review 脚本自动解析实际本金、match mode、调仓后持仓数、资金暴露/现金占比、买卖笔数与 `insufficient_cash` 等拒单计数，并对照设计中的 `portfolio_contract` 与 `r5_deployment_invariants`——本金不符（`capital_contract_mismatch`）或仓位未按设计部署（`deployment_invariant_failed`，例如目标20只实际只持有2只）即使回测无异常跑完也一律 BLOCK。自报的 `runtime_checks` 布尔值不再作为 PASS 权威依据。证据审核PASS后，R6生成正式文件并删除候选文件。真实 PTrade 运行失败后，旧候选/上传文件会被重命名为 `*.RETIRED_DO_NOT_UPLOAD` 并作废旧哈希，必须重新生成。

> **`etf_basic` freshness:** metadata is maintained by the enabled `etf_basic` collector task with Tushare as the single authority. Full, incremental, and resident collection share the same baseline normalization and changed-row upsert path. The compatibility command `scripts/sync_etf_basic.py` uses the same contract. Restart a resident collector after deploying task/config changes。

## 框架层变更审阅记录（perf/datadict-day-index）

本次 `quantstudio/backtest/ptrade_api.py`、`quantstudio/backtest/backtest_engine.py` 新增 DataDict/BacktestEngine 当日 DataFrame 的 `{raw_code: first_iloc}` 实例代码索引，将 `df['code'] == bare` 的 O(N) 布尔过滤替换为 O(1) 索引查找；`None`（无法构建）时严格回退原布尔过滤。

**AGENTS.md 框架铁律适用**：本变更为纯性能优化，已审阅确认未改变 `data[code]`（DataDict）、`get_current_price`、`is_halted`、`pct_chg` 等任何接口的函数名、签名、返回类型、返回字段、空值/异常行为或兼容行为；未改变数据语义或回测行为。因此本文档第 3 节 DataDict/`BarData`/`Position` 等条目不受影响，无需修改。
