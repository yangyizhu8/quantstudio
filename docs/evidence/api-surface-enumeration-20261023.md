# API 公开面枚举（2026-10-23）

- **性质**：纯本地只读枚举（AST ＋ 本地平台镜像），**零源码改动**。
- **触发**：总调度令「API ~38 枚举下轮自推」。
- **方法**：`quantstudio/backtest/ptrade_api.py` 用 **AST** 枚举类内方法（**不用正则**，避 `_`/`__` 私有误计）；对照源＝本地 PTrade 官方镜像 `knowledge/platform/ptrade/01-API文档主页/002-index.md`（119 个标识符）∪ docstring「对应 Ptrade」标注（双证据源）。

## 一、结论摘要

| 归属 | 数量 | 含义 | 是否需端对照 |
| --- | --- | --- | --- |
| **PTRADE** | **69** | PTrade 平台有对应 ⇒ 双端需逐条行为核验 | ✅ **真实待核面** |
| QS_ONLY | 9 | QS 本地扩展（无平台对应） | ❌ |
| INTERNAL | 10 | 框架内部（引擎调用），非策略 API | ❌ |
| PRIV | 16 | 私有（下划线） | ❌ |
| **公开面合计** | **88** | 69 + 9 + 10 | — |

## 二、「~38」的真相

| 数字 | 来源 | 性质 |
| --- | --- | --- |
| **38** | docstring 明示「对应 Ptrade」的条数 | 单证据源（仅代码注释） |
| 43 | 同上（含补判） | 注释口径 |
| 60 | PTrade 镜像链接抽取命中数 | 单证据源（仅镜像） |
| **69** | **两证据源并集** | ✅ 真实待核面 |

⇒ 旧估「~38」**偏低约 31 条**（低估算源＝docstring 单轨；镜像里另有一批未标注「对应」）。
⇒ registry 合计行「API 待枚举 ~38」据此更正为 **API 88（待核 69）**。

## 三、两处如实登记的缺口

| 缺口 | 实况 | 处置 |
| --- | --- | --- |
| **QMT 侧对照源未接通** | `knowledge/platform/ptrade/04-QMT文档-第三方收录/010-qmtapi.md` 用链接/裸名/通用三法抽取均得 **0**（结构非链接式） | **本轮只完成 PTrade 一端对拍**；registry API 域「QMT 锚点」列维持**待枚举**并注明原因 |
| 镜像不完备 | PTrade 镜像抽得 119 个标识符，平台实际 API 更多 | 故采用**双证据源并集**；「镜像有本地无」单列，不混入「本地独有」 |

## 四、平台有·本地无（59 条）—— 多为交易端专有，不属回测注入面

主体＝**融资融券（`get_margin*` 9 条）／期货合约／可转债特殊委托／集合竞价扩展／对账文件类／生命周期钩子**（`before_trading_start`、`after_trading_order`、`after_trading_cancel_order`、`is_trade`、`log` 等）。
⇒ 属**能力域缺口**（本地无对应），**不是**「注入 API 契约」条目，故不逐条入 registry。

---

# 附：全量清单（机器生成）

# API 公开面清单（枚举终稿 v3，2026-10-23）

- 本地源：`quantstudio/backtest/ptrade_api.py`（AST 枚举，非正则）
- PTrade 镜像：`knowledge/platform/ptrade/01-API文档主页/002-index.md`
- QMT 镜像：`knowledge/platform/ptrade/04-QMT文档-第三方收录/010-qmtapi.md`
- 分类证据源双轨：①镜像链接/裸名 ②docstring「对应 Ptrade」标注

## 汇总

| 归属 | 数量 | 含义 |
| --- | --- | --- |
| PTRADE | 68 | PTrade 平台有对应 ⇒ 双端需逐个行为对照 |
| QS_ONLY | 4 | QS 本地扩展，无平台对应 ⇒ 不需端测 |
| INTERNAL | 10 | 框架内部（引擎调用），非策略 API |
| TBD | 6 | 两证据源均未覆盖 ⇒ 待人工判定 |
| PRIV | 16 | 私有（下划线） |

## 全量 104 条

| API | 必填参数 | 归属 | PTrade镜像 | QMT镜像 | 本地摘要 |
| --- | --- | --- | --- | --- | --- |
| `attach(engine, curr_data, prev_data, curr_date, prev_date, prices)` | 5 | INTERNAL | - | - | 每个交易日注入引擎数据（向后兼容别名 = attach_day）。 |
| `attach_bar(engine, bar_data, curr_date, prev_date, prices, pct_chg_map, current_bar_ts)` | 4 | INTERNAL | - | - | PR4: 每 bar 一次注入引擎数据，跳过 preload（避免每 bar DB 查询）。 |
| `attach_day(engine, curr_data, prev_data, curr_date, prev_date, prices)` | 5 | INTERNAL | - | - | PR4: 每日一次注入引擎数据，含 preload（行情/估值/参考数据预加载）。 |
| `attach_day_minute_history(df, day_str)` | 2 | INTERNAL | - | - | Phase 4B：注入当日全 universe 的全量分钟 bar（引擎 _run_minute_day 调用）。 |
| `clear_limit_cache()` | 0 | INTERNAL | - | - | P1′：清空缓存（run 隔离 / 复用实例）。 |
| `load_research_signals(csv_path, fallback)` | 1 | INTERNAL | - | - | 读取每日更新的首次覆盖研报 CSV（框架侧 I/O，供策略经注入 API 调用）。 |
| `reset_session()` | 0 | INTERNAL | - | - | 清理一次策略运行的可变状态。 |
| `set_fidelity_config(fidelity_config)` | 1 | INTERNAL | - | - | P-A0/P-A2：注入 PTrade 保真模式配置（opt-in，验证转换产物用）。 |
| `set_limit_mode(mode)` | 1 | INTERNAL | Y | - |  |
| `set_volume_ratio(volume_ratio)` | 0 | INTERNAL | Y | - | 设置成交量占比限制 |
| `_bare_to_qmt(bare)` | 1 | PRIV | - | - |  |
| `_curr_data_for_execute()` | 0 | PRIV | - | - | PR4: _immediate_execute 的 curr_data 来源。 |
| `_execute_query(qb)` | 1 | PRIV | - | - | 执行 ORM query（query(valuation...).filter().order_by()）。 |
| `_fidelity_ashares(date)` | 0 | PRIV | - | - | P-A0：平台 A 股池快照消费（裸码 → Ptrade 格式 .SZ/.SS，惰性加载缓存）。 |
| `_filter_delisted_pit(codes, date_str)` | 2 | PRIV | - | - | 任务一批复③：PIT 退市过滤（stock_delist 334 条退市日历消费）。 |
| `_get_current_price(bare_code)` | 1 | PRIV | - | - | 从当日数据获取最新价 |
| `_get_proxy_intraday_history(sec_list, count, fields, is_dict, field_map)` | 5 | PRIV | - | - | Return only completed synthetic bars for daily-open-close-proxy-v1 |
| `_limit_cache_diag()` | 0 | PRIV | - | - | P1′：QS_LIMIT_CACHE_DIAG=1 ⇒ 强制禁用缓存，恢复逐码 QS_DIAG。 |
| `_limit_cache_limit()` | 0 | PRIV | - | - | P1′：日期桶上限（QS_LIMIT_CACHE_DATES，默认 8；0=关闭）。 |
| `_lookup_position(positions, security)` | 2 | PRIV | - | - | 在持仓 dict 中按裸码归一化查找（支持 .SS/.XSHG/.SZ/.XSHE/裸码 互通）。 |
| `_make_noop_order(security)` | 1 | PRIV | - | - | 构造无操作 Order（order_target delta=0 等）— A3。 |
| `_offer_pool_codes(codes)` | 1 | PRIV | - | - | A2 第二来源（2026-09-26）：把「指数成分发现」型策略的成分集并入当日池。 |
| `_resolve_status_source(stocks, query_date)` | 1 | PRIV | - | - | Resolve stock-status data for an explicit PIT date. |
| `_route_next_open(security, instruction, target_value, shares)` | 2 | PRIV | - | - | G1-I: next_open 模式订单路由（设计 v2 §3.6）。 |
| `_sync_pool_to_data_access()` | 0 | PRIV | - | - | A 通道（2026-09-26）：把当日池写入数据访问层（全池共享 bars 缓存）。 |
| `_to_ptrade_code(bare_code)` | 1 | PRIV | - | - | Return PTrade strategy/CSV codes via the authoritative normalizer. |
| `attribute_history(security, count, unit, fields)` | 2 | PTRADE | - | - | 获取历史数据（对应 Ptrade attribute_history，返回单列 Series） |
| `cancel_order(order_param)` | 1 | PTRADE | Y | - | 撤单。 |
| `check_limit(security, query_date)` | 1 | PTRADE | Y | - | 涨跌停判定（对齐 PTrade check_limit；返回 1/-1/2/-2/0 五态）。 |
| `convert_position_from_csv(path)` | 1 | PTRADE | Y | - | 从 CSV 读取底仓参数列表（对应 Ptrade convert_position_from_csv，仅回测） |
| `create_dir(user_path)` | 1 | PTRADE | Y | - | 创建文件子目录路径（对应 Ptrade create_dir） |
| `current_price(security)` | 1 | PTRADE | - | - | 获取当前价（对应 Ptrade current_price） |
| `filter_stock_by_status(stocks, filter_type, query_date)` | 1 | PTRADE | - | - | 过滤 ST/停牌/退市（对应 Ptrade filter_stock_by_status，4 种 filter_type 全支持）。 |
| `get_CCI(high, low, close, n)` | 3 | PTRADE | - | - | Ptrade 原生 CCI |
| `get_KDJ(high, low, close, n, m1, m2)` | 3 | PTRADE | - | - | Ptrade 原生 KDJ |
| `get_MACD(close, short, long, m)` | 1 | PTRADE | - | - | Ptrade 原生 MACD |
| `get_RSI(close, n)` | 1 | PTRADE | - | - | Ptrade 原生 RSI |
| `get_all_positions()` | 0 | PTRADE | Y | - | 获取全部持仓（对应 Ptrade get_all_positions，仅交易） |
| `get_all_trades_days(date)` | 0 | PTRADE | Y | - | 获取全部交易日，返回 ndarray |
| `get_business_type()` | 0 | PTRADE | Y | - | 获取业务类型 |
| `get_cb_info()` | 0 | PTRADE | Y | - | 获取可转债基础信息（对应 Ptrade get_cb_info） |
| `get_cb_list()` | 0 | PTRADE | Y | - | 获取可转债市场代码列表（对应 Ptrade get_cb_list） |
| `get_current_data()` | 0 | PTRADE | - | - | 返回当日全部行情数据（对应 Ptrade get_current_data） |
| `get_current_kline_count()` | 0 | PTRADE | Y | - | 获取当前时间的分钟 bar 数量（对应 Ptrade get_current_kline_count） |
| `get_dominant_contract(contract, date)` | 1 | PTRADE | - | - | 获取主力合约代码（对应 Ptrade get_dominant_contract） |
| `get_etf_info(etf_code)` | 1 | PTRADE | Y | - | 获取 ETF 信息（对应 Ptrade get_etf_info） |
| `get_etf_list()` | 0 | PTRADE | Y | - | 获取 ETF 代码列表（对应 Ptrade get_etf_list） |
| `get_etf_stock_info(etf_code, security)` | 2 | PTRADE | Y | - | 获取 ETF 成分券信息（对应 Ptrade get_etf_stock_info） |
| `get_etf_stock_list(etf_code)` | 1 | PTRADE | Y | - | 获取 ETF 成分券列表（对应 Ptrade get_etf_stock_list） |
| `get_frequency()` | 0 | PTRADE | Y | - | 获取回测频率 |
| `get_fundamentals(security, table, fields, date, start_year, end_year, report_types, merge_type, is_dataframe)` | 1 | PTRADE | Y | - | 获取财务/估值数据（对应 Ptrade get_fundamentals）。 |
| `get_history(security, count, unit, fields, frequency, field, security_list, fq, include, fill, is_dict)` | 0 | PTRADE | Y | - | 获取历史数据（对应 Ptrade get_history），兼容两种签名： |
| `get_index_stocks(index_code, date)` | 1 | PTRADE | Y | - | 获取指数成分股（对应 Ptrade get_index_stocks），严格 PIT（F3）。 |
| `get_industry_stocks(industry_code)` | 1 | PTRADE | Y | - | 获取行业成份股（对应 Ptrade get_industry_stocks） |
| `get_instruments(contract)` | 1 | PTRADE | Y | - | Return basic security metadata; futures remain unsupported locally |
| `get_ipo_stocks()` | 0 | PTRADE | Y | - | 获取当日 IPO 申购标的（对应 Ptrade get_ipo_stocks） |
| `get_margin_rate(transaction_code)` | 1 | PTRADE | Y | - | 获取保证金比例（对应 Ptrade get_margin_rate，期货） |
| `get_market_detail(finance_mic)` | 1 | PTRADE | Y | - | 获取市场详细信息（对应 Ptrade get_market_detail） |
| `get_market_list()` | 0 | PTRADE | Y | - | Return exchange, index, and block market identifiers. |
| `get_open_orders(security)` | 0 | PTRADE | Y | - | 获取未成交订单。 |
| `get_order(order_id)` | 1 | PTRADE | Y | - | 根据 order_id 查订单。 |
| `get_orders(security)` | 0 | PTRADE | Y | - | 获取当日订单列表 |
| `get_position(security)` | 1 | PTRADE | Y | - | 获取单只标的持仓（对应 Ptrade get_position） |
| `get_positions(security)` | 0 | PTRADE | Y | - | 获取持仓（对应 Ptrade get_positions） |
| `get_price(security, start_date, end_date, frequency, fields, fq, count, is_dict)` | 1 | PTRADE | Y | - | 获取历史行情（对应 Ptrade get_price） |
| `get_reits_list(date)` | 0 | PTRADE | Y | - | 获取公募 REITs 基金代码列表（对应 Ptrade get_reits_list） |
| `get_research_path()` | 0 | PTRADE | Y | - | 获取研究界面根目录路径（对应 Ptrade get_research_path，回测/交易） |
| `get_security_info(code)` | 1 | PTRADE | - | - | 获取证券基础信息（对应 Ptrade get_security_info） |
| `get_snapshot(security, frequency)` | 1 | PTRADE | Y | - | 获取实时快照（回测模式返回当日 bar 数据）。 |
| `get_stock_blocks(stock_code)` | 1 | PTRADE | Y | - | 获取证券所属板块（对应 Ptrade get_stock_blocks） |
| `get_stock_exrights(security, date)` | 1 | PTRADE | Y | - | 获取证券除权除息信息（对应 Ptrade get_stock_exrights） |
| `get_stock_info(stocks, field)` | 1 | PTRADE | Y | - | Return PTrade-shaped stock metadata from the formal reference laye |
| `get_stock_name(stocks)` | 1 | PTRADE | Y | - | 获取股票名称（DuckDB 无 name 字段，返回代码作为名称） |
| `get_stock_status(stocks, query_type, query_date)` | 1 | PTRADE | Y | - | 获取股票状态（与 PTrade 公共签名对齐）。 |
| `get_trade_days(start_date, end_date, count)` | 0 | PTRADE | Y | - | 获取交易日列表，返回 ndarray |
| `get_trades()` | 0 | PTRADE | Y | - | 获取当日成交列表 |
| `get_trades_file(save_path)` | 0 | PTRADE | Y | - | 获取对账数据文件（对应 Ptrade get_trades_file，仅回测） |
| `get_trading_day(day)` | 0 | PTRADE | Y | - | 获取交易日（day>0 未来N天，day<0 过去N天，day=0 当天） |
| `get_trading_day_by_date(query_date, day)` | 1 | PTRADE | Y | - | 根据日期获取对应的交易日 |
| `get_trend_data(date, stocks, market)` | 0 | PTRADE | Y | - | 获取集中竞价期间代码数据（对应 Ptrade get_trend_data） |
| `get_underlying_code(symbols)` | 1 | PTRADE | Y | - | 获取证券关联代码（对应 Ptrade get_underlying_code，交易模块） |
| `get_user_name(login_account)` | 0 | PTRADE | Y | - | 获取登录终端的资金账号（对应 Ptrade get_user_name，回测/交易） |
| `order(security, amount, limit_price)` | 2 | PTRADE | Y | - | 按股数下单（即时执行） |
| `order_target(security, target_amount, limit_price)` | 2 | PTRADE | Y | - | 调仓到目标股数（对应 Ptrade order_target）。 |
| `order_target_value(security, value, limit_price)` | 2 | PTRADE | Y | - | 目标市值调仓（对应 Ptrade order_target_value） |
| `order_value(security, value, limit_price)` | 2 | PTRADE | Y | - | 按金额买入/卖出（对应 Ptrade order_value，增量操作） |
| `run_daily(context, func, time)` | 2 | PTRADE | Y | - | 定时任务（回测模式中等效于在 handle_data 中调用） |
| `set_benchmark(sids)` | 1 | PTRADE | Y | - | 设置基准指数 |
| `set_commission()` | 0 | PTRADE | Y | - | 支持策略在 initialize 中动态调整回测成本。 |
| `set_fixed_slippage(fixedslippage)` | 0 | PTRADE | Y | - | Set fixed yuan-per-share slippage with the PTrade signature. |
| `set_parameters()` | 0 | PTRADE | Y | - | 设置策略配置参数（对应 Ptrade set_parameters，仅交易） |
| `set_slippage(slippage)` | 0 | PTRADE | Y | - | Set proportional slippage with the real PTrade call signature. |
| `set_universe(security_list)` | 1 | PTRADE | Y | - | A 通道（2026-09-26）：记录策略当日 PIT 池（原为 no-op：DuckDB 模式无需订阅）。 |
| `set_yesterday_position(poslist)` | 1 | PTRADE | Y | - | 设置初始持仓（底仓） |
| `get_etf_list_local(query_date, etf_type, active_only)` | 0 | QS_ONLY | - | - | Return a PIT ETF universe for QuantStudio-only local backtests. |
| `get_index_day_bar(security, count, fields)` | 1 | QS_ONLY | - | - | 返回已完成指数日线（QuantStudio 本地专用 API，docs/get-index-day-bar-design.md 20 |
| `get_industry(code)` | 1 | QS_ONLY | - | - | 获取证券行业信息（LOCAL_ONLY 本地扩展，非 Ptrade API），APPROXIMATION_REQUIRES_CONF |
| `order_at_price(security, amount, execution_price)` | 3 | QS_ONLY | - | - | QuantStudio-only explicit-price execution for completed-bar resear |
| `get_Ashares(date, exclude_bse, include_delisted)` | 0 | TBD | - | - | 获取全 A 股列表 |
| `get_fundamentals_batch(security_list, table, fields, date)` | 1 | TBD | - | - | B1：批量查询多只股票基本面。返回 DataFrame，index=ptrade_code。 |
| `get_history_batch(security_list, count, unit, fields, fq, include)` | 2 | TBD | - | - | B1：批量查询多只股票历史行情。返回 CodeDict（{ptrade_code: DataFrame}）。 |
| `get_signals()` | 0 | TBD | - | - | 兼容旧接口（即时执行模式下返回空列表） |
| `get_strategy_events(event_type, effective_date, start_date, end_date, security_list)` | 1 | TBD | - | - | Return generic locally-ingested strategy events through the API la |
| `query()` | 0 | TBD | - | - | module-level |

## PTrade 有但本地无（59）

- `after_trading_cancel_order`（after_trading_cancel_order(交）
- `after_trading_end`（after_trading_end）
- `after_trading_order`（after_trading_order(交易)）
- `before_trading_start`（before_trading_start）
- `buy_close`（buy_close(回测/交易(期货))）
- `buy_open`（buy_open(回测/交易(期货))）
- `cancel_order_ex`（cancel_order_ex(交易)）
- `debt_to_stock_order`（debt_to_stock_order(交易)）
- `etf_basket_order`（etf_basket_order(交易)）
- `etf_purchase_redemption`（etf_purchase_redemption(交易)）
- `get_all_orders`（get_all_orders(交易)）
- `get_assure_security_list`（get_assure_security_list(交易)）
- `get_crdt_fund`（cash_group=None）
- `get_deliver`（get_deliver(交易)）
- `get_enslo_security_info`（cash_group=None）
- `get_fundjour`（get_fundjour(交易)）
- `get_gear_price`（get_gear_price(交易)）
- `get_individual_entrust`（get_individual_entrust(交易)）
- `get_individual_transcation`（get_individual_transcation(交）
- `get_lucky_info`（start_date, end_date）
- `get_margin_assert`（get_margin_assert(交易)）
- `get_margin_contract`（get_margin_contract(交易)）
- `get_margin_contractreal`（get_margin_contractreal(交易)）
- `get_margin_entrans_amount`（get_margin_entrans_amount(交易）
- `get_margincash_close_amount`（get_margincash_close_amount(）
- `get_margincash_open_amount`（get_margincash_open_amount(交）
- `get_margincash_stocks`（get_margincash_stocks(交易)）
- `get_marginsec_close_amount`（get_marginsec_close_amount(交）
- `get_marginsec_open_amount`（get_marginsec_open_amount(交易）
- `get_marginsec_stocks`（get_marginsec_stocks(交易)）
- `get_sort_msg`（get_sort_msg(交易)）
- `get_tick_direction`（get_tick_direction(交易)）
- `get_trade_name`（get_trade_name(交易)）
- `handle_data`（handle_data）
- `initialize`（initialize）
- `ipo_stocks_order`（ipo_stocks_order(交易)）
- `is_trade`（is_trade(回测/交易)）
- `log`（log(回测/交易)）
- `margin_trade`（margin_trade(交易)）
- `margincash_close`（margincash_close(交易)）
- `margincash_direct_refund`（margincash_direct_refund(交易)）
- `margincash_open`（margincash_open(交易)）
- `marginsec_close`（marginsec_close(交易)）
- `marginsec_direct_refund`（marginsec_direct_refund(交易)）
- `marginsec_open`（marginsec_open(交易)）
- `on_order_response`（on_order_response()）
- `on_trade_response`（on_trade_response()）
- `order_market`（order_market(交易)）
- `order_tick`（order_tick(交易)）
- `permission_test`（permission_test(交易)）
- `q11`（Ptrade无法登录）
- `run_interval`（run_interval）
- `sell_close`（sell_close(回测/交易(期货))）
- `sell_open`（sell_open(回测/交易(期货))）
- `send_email`（send_email(交易)）
- `send_qywx`（send_qywx(交易)）
- `set_future_commission`（set_future_commission(回测(期货)）
- `set_margin_rate`（set_margin_rate(回测(期货))）
- `tick_data`（tick_data）

## QMT 有但本地无（0）
