---
title: "xtquant版本下载 | 迅投知识库"
source_url: "https://dict.thinktrader.net/nativeApi/download_xtquant.html"
crawled_at: 2026-10-12T03:03:53+08:00
platform: QMT
category: 03-原生Python-XtQuant
depth: 4
tags: [平台/QMT, 状态/已抓取]
---

| 更新日期 | 版本 | 下载 | 更新说明 |
| --- | --- | --- | --- |
| 20251219 | xtquant_250807 | [点击下载](https://dict.thinktrader.net/packages/xtquant_250807.rar) | token模式，K线全推和全推数据加载模式调整 从xtdc.init()后立刻加载，调整为在第一次使用数据时加载  xttrader支持智能算法 获取智能算法参数配置信息接口get_smart_algo_param 智能算法下单接口smart_algo_order_async 智能算法任务查询接口query_smart_algo_task 智能算法任务撤销接口cancel_smart_algo_task_async  获取当前连接订阅的数据信息 xtdata.get_current_connect_sub_info（需要投研版本）  获取客户端所有订阅信息 xtdata.get_all_sub_info()（需要投研版本）  获取委托在千档队列中的排名 xtdata.get_order_rank() （需要投研版本）  token模式可以设置千档数据源模式 xtdc.set_thousand_source_mode()  token模式同datadir只允许启动一个xtdc进程  get_tabular_data参数不合法返回None  大单统计字段调整，查阅xtdata.md文档  vix市场的订阅和全推支持显示夜盘真实时间  BugFix: 修复期货0点前夜盘复权不生效的问题 |
| 20250516 | xtquant_250516 | [点击下载](https://dict.thinktrader.net/packages/xtquant_250516.rar) 压缩包目录结构已调整到和之前一致，带有xtquant文件夹 | 支持python3.13版本  xttrader 支持银证转账 银行信息查询 xttrader.query_bank_info() 银行账户余额查询xttrader.query_bank_amount() 银证转账转入 xttrader.bank_transfer_in() 银证转账转出 xttrader.bank_transfer_out() 银行卡流水记录查询xttrader.query_bank_transfer_stream()  xttrader 支持期货和期权资金划转 权资金转期货 xttrader.ctp_transfer_option_to_future() 期货资金转期权 xttrader.ctp_transfer_future_to_option()  xttrader支持北交所 xtconstant 添加北交所市价报价方式说明  xttrader 交易数据字段调整 委托 XtOrder 新增 股东代码 secu_account 证券名称 instrument_name 成交 XtTrade 新增 股东代码 secu_account 证券名称 instrument_name 持仓 XtPosition 新增 股东代码 secu_account 证券名称 instrument_name 当前价 last_price 盈亏比例 profit_rate 浮动盈亏 float_profit 持仓盈亏 position_profit 开仓日期 open_date（只对期货可用） 账号资金 XtAsset 新增 可取余额 fetch_balance 当前余额 current_balance（当前余额 = 可用资金 + 冻结资金）  xtdata获取数据函数支持以datetime形式传入时间范围  合约信息添加交易日字段 TradingDay xtdata.get_instrument_detail()  支持获取大单统计数据（需要vip权限） xtdata.get_transactioncount()  支持获取带有分类信息的板块列表（需要投研版本） xtdata.get_sector_info()  期权合约信息添加期权预估保证金 OptEstimatedMargin（需要投研版本） xtdata.get_option_detail_data()  郑商所期货、期权品种提供标准化代码的字段（如 MA2504.ZF）（需要投研版本）  xtdata获取数据函数支持ATM市场 xtdata.get_market_data() xtdata.get_market_data_ex()  BugFix: 订阅数据问题 |
| 20241017 | xtquant_241014 | [点击下载](https://dict.thinktrader.net/packages/xtquant_241014.rar) | xtdata.get_option_detail_data()期权多空方向类型判断调整  xtdata.get_trading_calendar() 自动下载所需的节假日数据  xtdata.get_instrument_detail() 字段ExpireDate类型由int调整为str  tick数据增加现手字段(tickvol)，即当前tick累计成交量与上条数据的差值  新增函数xtdata.get_formula_result()，用于获取subscribe_formula()的模型结果  修复token模式下启用K线全推后单支订阅数据周期错误的问题 |
| 20240926 | xtquant_240920a | 有已知缺陷，暂不提供下载  在token模式下，启用K线全推后单支订阅数据周期错误 | 修复xtdata.subscribe_quote()返回订阅号为None的问题 |
| 20240923 | xtquant_240920 | 有已知缺陷，暂不提供下载 | token模式，可以设置初始化的市场列表 xtdatacenter.set_init_markets()  函数xtdata.get_period_list() 结果结构调整  添加函数 xtdata.get_trading_contract_list() 获取当前主力合约可交易标的列表  添加用于获取交易时段的系列函数 xtdata.get_trading_period() xtdata.get_all_trading_periods() xtdata.get_all_kline_trading_periods()  添加函数 xtdata.subscribe_quote2() 支持复权方式参数 |
| 20240822 | xtquant_240812 | [点击下载](https://dict.thinktrader.net/packages/xtquant_240812.rar) | 期货夜盘显示真实时间功能默认开启 未开启时， 周六凌晨的行情数据时间为周一 开启时，周六凌晨的行情数据时间为周六  新增板块：过期上交所、过期深交所 沪港通深港通板块不再显示历史标的  撤单接口的市场参数支持字符串格式，如SH、SF xttrader.cancel_order_stock_sysid()和xttrader.cancel_order_stock_sysid_async()  添加函数xtdata.get_his_option_list_batch()和xtdata.get_his_option_list() 获取历史上某段时间的指定品种期权信息列表 依赖数据'optionhistorycontract'  期权函数支持商品期权品种 xtdata.get_option_undl_data()和xtdata.get_option_list()  郑商所期权标的代码调整为4位 xtdata.get_option_detail_data()  移除郑商所过滤重复tick的逻辑 |
| 20240617 | xtquant_240613 | [点击下载](https://dict.thinktrader.net/packages/xtquant_240613.rar) | 支持python3.12版本  xtdata支持选择端口范围，在范围内自动连接  添加函数xtdata.get_full_kline() 批量获取当日K线数据（需要开启K线全推）  支持获取新闻公告数据 xtdata.get_market_data()系列函数 数据周期：announcement  支持获取涨跌停连板数据 xtdata.get_market_data()系列函数 数据周期：limitupperformance  支持获取港股通持股明细数据 xtdata.get_market_data()系列函数 数据周期：hktdetails,hktstatistics  支持获取外盘的行情数据（需购买相应服务） 行情订阅xtdata.subscribe_quote()和行情获取xtdata.get_market_data()系列函数，支持美股品种的获取  支持订阅vba模型（连接投研端） xtdata.subscribe_formula()  token模式下初始化全推市场可选 xtdatacenter.set_wholequote_market_list()  token模式下行情连接优选机制调整 xtdatacenter.set_allow_optmize_address()会使用第一个地址作为全推连接  token模式下期货周末夜盘数据时间模式可选,可以选择展示为周一凌晨时间或真实的周六凌晨时间 xtdatacenter.set_future_realtime_mode() |
| 20240329 | xtquant_240329 | [点击下载](https://dict.thinktrader.net/packages/xtquant_240329.rar) | 郑商所期货品种支持使用4位年月代码（例如：CF2303.ZF） xtdata.get_instrument_detail()支持使用4位年月代码获取 历史主力合约数据 新增4位年月代码字段  支持获取etf的iopv数据 分笔数据添加iopv字段（pe） xtdata.get_market_data()系列函数 数据周期：etfiopv1m（分钟级） etfiopv1d（日级）  期权函数xtdata.get_option_detail_data() 新增标的品种代码字段 OptUndlCodeFull  新增板块 上证转债、沪深转债、T+0基金  连接状态监听接口回调数据结构调整 xtdata.watch_quote_server_status() xtdata.watch_xtquant_status()  新增接口 xtdata.subscribe_formula() 支持连接投研端调用vba  token模式下支持按用户权限放开并行接入数量  本地python回测支持多线程  优化7*24连续交易的问题 |
| 20240205 | xtquant_240119b | [点击下载](https://dict.thinktrader.net/packages/xtquant_240119b.rar) | 修复token模式下偶发的订阅数据异常问题 有问题的版本：240119, 240119a  token模式下并行接入数量放宽至10个 |
| 20240129 | xtquant_240119a | [点击下载](https://dict.thinktrader.net/packages/xtquant_240119a.rar) | 合约信息接口支持参数控制获取全部字段 xtdata.get_instrument_detail(iscomplete = True)并添加以下字段 期货和期权手续费方式(ChargeType) 开仓手续费(率)(ChargeOpen) 平仓手续费(率)(ChargeClose) 开今仓(日内开仓)手续费(率)(ChargeTodayOpen) 平今仓(日内平仓)手续费(率)(ChargeTodayClose) 交割月持仓倍数(OpenInterestMultiple)  添加客户端连接状态监听接口 xtdata.watch_xtquant_status()  支持获取退市可转债数据 xtdata.get_market_data()系列函数 数据周期：delistchangebond  支持获取待发可转债数据 xtdata.get_market_data()系列函数 数据周期：replacechangebond  优化K线全推的断线重连逻辑 |
| 20240119 | xtquant_240119 | [点击下载](https://dict.thinktrader.net/packages/xtquant_240119.rar) | xtdata.subscribe_quote()和xtdata.get_market_data()系列函数，添加新的K线数据周期 新周期包含：周线(1w)、月线(1mon)、季度线(1q)、半年线(1hy)、年线(1y)  支持获取千档委买委卖队列数据 订阅函数 xtdata.subscribe_l2thousand_queue() 获取函数 xtdata.get_l2thousand_queue()  支持港股lv2数据（待后续迅投lv2数据源上线后可用） 支持获取港股席位数据 订阅函数 xtdata.subscribe_quote(period = 'brokerqueue') 获取函数 xtdata.get_broker_queue_data()  xtdata.get_full_tick()在VIP模式下提供成交笔数字段（transactionNum）  xtdata.get_option_detail_data()支持获取商品期权数据  支持获取历史主力合约数据 xtdata.get_market_data()系列函数 数据周期：historymaincontract  修复 获取上证期权、深证期权tick行情数据价格精度错误的问题  修复 期货夜盘分钟线获取不到的问题  优化下载数据流程  支持设置行情源自动连接目标地址范围 xtdatacenter.set_allow_optmize_address()  xtdata.get_local_data()支持指定数据路径 |
| 20231228 | xtquant_231209a | [点击下载](https://dict.thinktrader.net/packages/xtquant_231209a.rar) | 修复xtdata.get_trading_calendar()获取历史范围返回数据重复的问题  添加xtdata.get_trading_calendar()目前仅支持SH,SZ市场的说明（其他市场交易日历陆续对接中）  添加快照指标数据周期 'snapshotindex'（包含量比、涨速、换手等字段）  修复板块指数（BKZS）分钟线获取不到的问题  添加xtdatacenter中的北交所、沪深京A股板块  修复xtdata.subscribe_whole_quote()订阅全推数据中的pvolume字段单位错误  （股票、转债的pvolume单位为股，所有品种volume单位均为手，其余品种情况详见网页文档） |
| 20231209 | xtquant_231209 | [点击下载](https://dict.thinktrader.net/packages/xtquant_231209.rar) | 添加ETF申赎清单信息相关接口  下载数据 xtdata.download_etf_info()  获取数据 xtdata.get_etf_info()  添加节假日下载的接口 download_holiday_data（获取交易日历依赖节假日）  添加涨跌停数据，数据周期'stoppricedata'  添加连接成功时连接状态日志  添加财务数据文档中十大股东、股东数的字段说明  修复历史st数据获取失败的问题  修复xtdatacenter提供数据时，和接收进程运行目录不同出现获取失败的问题  优化模块退出时的表现  移除xtdata.get_industry()接口 |
| 20231124 | xtquant_231101c | [点击下载](https://dict.thinktrader.net/packages/xtquant_231101c.rar) | 修复xtdata.get_market_data()系列的内存泄漏问题  xtdatacenter.init()在重要市场初始化失败时抛出异常信息  全推数据在第一次使用时订阅，减少不必要的带宽占用  修复xtdatacenter退出时崩溃的问题  修复同目录下xtdatacenter重复启动卡住的问题  补全期货全推的月份连续合约（例如 ag01.SF）和交易合约（例如 ag2401.SF）  日志相关优化 |
| 20231110 | xtquant_231101b | [点击下载](https://dict.thinktrader.net/packages/xtquant_231101b.rar) | 修复过期合约板块成分为空的问题  优化xtdatatcenter监听端口后连接接入的时序  xtdata.download_history_data添加增量下载参数，支持指定起始时间的增量下载  修复token无效时调用接口崩溃的问题 |
| 20231106 | xtquant_231101a | [点击下载](https://dict.thinktrader.net/packages/xtquant_231101a.rar) | 修复退出时发生异常的问题  优化初始化过程中行情连接和数据订阅的时序 |
| 20231101 | xtquant_231101 | [点击下载](https://dict.thinktrader.net/packages/xtquant_20231101.rar) | 添加xtdatacenter，支持以token方式登录行情服务  添加xtdata.QuoteServer，支持通过xtdata控制、监控行情连接  补充xtdata中对转债交易场景、ETF交易场景的数据支持  调整了一些底层数据交互的实现方式  完善xttrader期货交易场景下的开平仓方向字段 |
| 20230920 | xtquant_230825b | [点击下载](https://dict.thinktrader.net/packages/xtquant_0825b_2023-09-20.rar) | 对应当前QMT券商版公版的下载python库  券商版会有版本升级跟不上的情况，通常请使用这个公版版本以保证兼容性 |
| 20230905 | xtquant_230825a | [点击下载](https://dict.thinktrader.net/packages/xtquant_20230825a.rar) | - |
| 20230825 | xtquant_230825 | [点击下载](https://dict.thinktrader.net/packages/xtquant_20230825.rar) | - |
| 20230301 | xtquant_230301 | [点击下载](https://dict.thinktrader.net/packages/xtquant_20230301.rar) | - |
| 20220817 | xtquant_220817 | [点击下载](https://dict.thinktrader.net/packages/xtquant_20220817.rar) | - |

上次更新:

邀请注册送VIP优惠券

分享下方的内容给好友、QQ群、微信群,好友注册您即可获得VIP优惠券

玩转qmt,上迅投qmt知识库

登录后获取

[常见问题](https://dict.thinktrader.net/nativeApi/question_function.html)
