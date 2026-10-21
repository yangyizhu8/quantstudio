---
title: "# 版本更新说明"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1cfsjkbf8f3is/TdxQuantVersion.html"
crawled_at: 2026-10-12T02:41:24+08:00
platform: TDX
category: 01-入门与安装
depth: 1
tags: [平台/TDX, 状态/已抓取]
---

# [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1cfsjkbf8f3is/TdxQuantVersion.html#版本更新说明) 版本更新说明

TdxAiData和宽客论坛：
[https://sns.tdx.com.cn/site/tdx_sns/page_index.html#/CircleDetail?id=388968&systemType=1 (opens new window)](https://sns.tdx.com.cn/site/tdx_sns/page_index.html#/CircleDetail?id=388968&systemType=1)
通达信智能公众号（用微信扫一扫，获取更多信息）：
![](https://help.tdx.com.cn/quant/uploads/mindoc/images/m_4d9ea12031d285f84b770f2b714496ff_r.jpeg)

**📅 2026-09-30 更新说明**

- 1.tqcenter和tdxaidata升级为1.2.2

- 2.增加get_sector_list_ds,优化对扩展市场的支持

- 3.增加获取集合排名行情get_domain_sorthq函数

- 4.修正部分细节问题

**📅 2026-09-20 更新说明**

- 1.tdxaidata升级为1.1.1

- 2.提供batch类协议

- 3.获取历史成交query_history_trade

- 4.修正部分细节问题

**📅 2026-09-04 更新说明**

- 1.全面支持tqserver(即tdxaidata)后台数据模式,此模式不需要开启通达信客户端

- 2.提供tdxaidata清华源pip库下载安装

- 3.download_file支持限售解禁数据

- 4.get_more_info中增加期货字段

- 5.get_gpjy_value支持取ETF份额等数据

- 6.AI平台中支持通达信AiData技能

- 7.修正部分细节问题

**📅 2026-07-24 更新说明**

- 1.增加get_zdt_data get_exday_data等函数

- 2.增加AI平台将内容提交给Lambda量化的方法

- 3.修正部分界面的问题

**📅 2026-07-10 更新说明**

- 1.资金类公式使用时自动刷新统计数据

- 2.修正部分股票最后K线因子计算的问题

- 3.download_file支持龙虎榜数据

- 4.get_stock_list支持查询融资融券成份股

**📅 2026-07-04 更新说明**

- 1.修正部分股票取最后一个K线成交量的问题

- 2.完善get_gpjy_value取数据的逻辑

**📅 2026-06-25 更新说明**

- 1.支持港股创业板和港股基金等

- 2.修正指数返回复权因子的问题

- 3.解决下单接口有时取不到委托记录的问题

- 4.支持只查询可撤委托

- 5.内部逻辑优化

**📅 2026-06-19 更新说明**

- 1.支持直接调用计数

- 2.自动交易函数增加返回错误

- 3.download_file支持经营分析数据文件

- 4.部分函数的完善

**📅 2026-06-12 更新说明**

- 1.修正撤单等细节,支持交易弹框中的市价显示

- 2.get_match_stkinfo支持全部品种

- 3.AI平台中,支持通达信TQ-Python和通达信TQ-Local两种Skill调用

**📅 2026-06-05 更新说明**

- 1.支持批量内部运行，断开连接，删除策略，打开编辑、消息筛选

- 2.修复了内部启动处理带有空格的路径时会截断的问题，优化断开连接细节

- 3.底层逻辑优化，提升数据返回速度

- 4.新增接口：检索证券信息get_match_stkinfo

- 5.全面支持AI平台直接调用TQ数据(无需Python代码)

**📅 2026-05-22 更新说明**

- 1.新增函数：get_pricevol 批量获取价格和成交量

- 2.新增函数：formula_get_all 获取对应种类的全部公式名称和代码

- 3.新增函数：formula_get_info 获取公式相关信息

- 4.新增函数：formula_process_mul_exp

- 5.新增逻辑：formula_process_mul系列count=-1时，可以根据start_time和end_time返回结果

**📅 2026-05-15 更新说明**

- 1.策略管理器新增“新建文件”按钮，并优化相应体验

- 2.断开连接功能优化，可以主动退出策略

- 3.修复get_market_data获取数据时如果传入的代码包含尚未上市代码会报错的问题

- 4.新增get_gb_info_by_date，可以仅根据start和end获取指定时间段的股本

**📅 2026-05-09 更新说明**

- 1.支持信用还券等下单类型

- 2.加入内部编辑器

**📅 2026-04-16 更新说明**

- 1.下单函数支持期货期权等类型

- 2.支持多头持仓,期货动态权益等持仓资金栏目

- 3.增加.QHZ期货指数类型

- 4.支持880096,880097,880098等成份股

**📅 2026-03-27 更新说明**

- 新增函数：获取股票所属板块get_relation

- 新增函数：调用客户端功能接口exec_to_tdx

- 新增函数：撤单cancel_order_stock

- 新增函数：账户资产查询query_stock_asset

- 更新函数：交易类账户函数逻辑更新

- 更新函数：调用通达信公式返回值字段名由"Data"改为"Value"

- 更新函数：order_stock对于模拟账户自动下单

- 更新函数：order_stock新增信用交易：担保品买入、担保品卖出，融资买入，融券卖出

- 更新函数：get_stock_list_in_sector访问空的自定义板块会返回空集而不是报错

- 问题修复：修复了get_market_data、refresh_kline等函数无法处理期权的问题

- 其他更新：期货期权类型支持，新增相关宏定义（常量枚举）

**📅 2026-03-20 更新说明**

- 新增函数：获取资金账户句柄stock_account

- 新增函数：查询账户委托信息query_stock_orders

- 新增函数：查询账户持仓信息query_stock_positions

- 新增函数：交易执行函数order_stock

- 更新函数：get_stock_list_in_sector新增block_type=2，可取对应期货代码

- 更新函数：get_more_info新增字段QHMainYYMM

- 更新函数：get_stock_list新增参数92: 国内期货主力合约

- 更新函数：get_cb_info改名为get_kzz_info

**📅 2026-03-06 更新说明**

- 新增函数：获取跟踪指数的ETF信息get_trackzs_etf_info

- 更新函数：refresh_cache新增参数 'ZS' 表示沪深京指数

- 更新函数：get_stock_list新增参数91 跟踪指数的ETF信息

- 其他修正：未识别的市场后缀由默认的SZ改为OT

- 其他修正：修复get_market_data某些情况下会报NoneType的bug

**📅 2026-02-28 更新说明**

- 问题修复：修复了formula_process_mul_zb等入参retrun_count拼写错误问题

- 更新函数：get_more_info，get_cb_info，get_market_snapshot加上了字段筛选功能

- 更新函数：get_more_info等支持更多行情数据项，输出顺序进行归整

- 其他修正：tqcenter几处细节修改

**📅 2026-02-12 更新说明**

- 更新函数：send_user_block可以添加股票进自选股，自选股简称为ZXG

- 其他更新：批量调用公式内部优化提速

- 其他更新：新增港股指数（.HI）

- 其他更新：解决多个客户端同时运行时的TQ冲突的问题

**📅 2026-02-07 更新说明**

- 新增函数：批量调用选股公式formula_process_mul_xg

- 新增函数：批量调用指标公式formula_process_mul_zb

- 更新函数：get_stock_list、 get_sector_list、 get_stock_list_in_sector新增参数list_type，可以选择返回股票名称

- 更新函数：tdx_formula返回做出修改，条件选股和专家选股只返回'1'和'0'

- 更新函数：formula_zb新增参数xsflag，可以设置返回数据的小数位数

- 更新函数：download_file新增下载：最近舆情、综合信息文件

- 更新函数：get_stock_info新增部分数据字段输出

**📅 2026-01-31 更新说明**

- 新增功能：支持调用通达信公式进行计算

- 新增函数：格式化K线数据formula_format_data

- 新增函数：向通达信公式系统设置数据formula_set_data

- 新增函数：向通达信公式系统设置数据信息formula_set_data_info

- 新增函数：获取公式中的设置数据formula_get_data

- 新增函数：调用通达信技术指标公式formula_zb

- 新增函数：调用通达信条件选股公式formula_xg

- 新增函数：调用通达信专家系统公式formula_exp

- 新增函数：获取股票更多信息get_more_info

- 新增函数：获取每天的股本数据get_gb_info

- 更新函数：刷新行情缓存refresh_cache，新增参数force和market，可指定强制刷新或指定市场刷新

- 其他更新：新增中证指数（.CSI），中金所期货（.CFF），宏观数据（.HG）等市场后缀识别和数据获取

- 其他更新：获取非指定日期的股票交易数据，板块交易数据等数据时增加了对应日期返回。

- 问题修复：修复了部分市场数据返回时小数位数不对导致的精度问题。

- 问题修复：修复了获取Python3.9以及之前版本依赖库错误问题。

**📅 2026-01-17 正式发布**

[安装Python及开发环境](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1cfsjkbf8f3is/mindoc-1d00970eq1rtc.html) →
