---
title: "# 聚宽用户引导文档"
source_url: "https://dict.thinktrader.net/strategy/JoinQuant2QMT.html"
crawled_at: 2026-10-12T03:03:16+08:00
platform: QMT
category: 06-策略迁移
depth: 3
tags: [平台/QMT, 状态/已抓取]
---

# [#](https://dict.thinktrader.net/strategy/JoinQuant2QMT.html#聚宽用户引导文档) 聚宽用户引导文档

## [#](https://dict.thinktrader.net/strategy/JoinQuant2QMT.html#_1-结构对比) 1. 结构对比

### [#](https://dict.thinktrader.net/strategy/JoinQuant2QMT.html#聚宽代码结构) 聚宽代码结构

```
def initialize(context):
    # 定义一个全局变量, 保存要操作的股票，如000001平安银行
    g.security = '000001.XSHE'
    # 运行函数
    run_daily(market_open, time='every_bar')
# 每个单位时间(如果按天回测,则每天调用一次,如果按分钟,则每分钟调用一次)调用一次
def market_open(context):
    if g.security not in context.portfolio.positions:
        order(g.security, 1000)
    else:
        order(g.security, -800)
```

### [#](https://dict.thinktrader.net/strategy/JoinQuant2QMT.html#qmt代码结构) QMT代码结构

```
#示例说明：本策略，在回测的每个周期买入主图标的
def init(C):
	#init handlebar函数的入参是ContextInfo对象 可以缩写为C
	#设置测试标的为主图品种
	C.stock= C.stockcode + '.' +C.market
	#accountid为测试的ID 回测模式资金账号可以填任意字符串
	C.accountid = "testS"
def handlebar(C):
    # handlebar在回测时，会在回测周期的每根bar被执行一次
    # handlebar在实时行情下，会随着主图tick的更新被调用

    # passorder是QMT的综合下单函数，具体参数字段参考官方文档
    passorder(23, 1101,C.accountid, C.stock, 5, -1, 1, C)
```

QMT中，所有数据都存储在本地，所有的策略计算都在电脑本地运行，您可以自由使用下载到的数据与python库

## [#](https://dict.thinktrader.net/strategy/JoinQuant2QMT.html#_2-聚宽代码移植qmt示例) 2.聚宽代码移植QMT示例

### [#](https://dict.thinktrader.net/strategy/JoinQuant2QMT.html#聚宽代码) 聚宽代码

> 当价格高于5日均线平均价格1.05时买入，当价格低于5日平均价格0.95时卖出。

```
# 导入函数库
import jqdata

# 初始化函数，设定要操作的股票、基准等等
def initialize(context):
    # 定义一个全局变量, 保存要操作的股票
    # 000001(股票:平安银行)
    g.security = '000001.XSHE'
    # 设定沪深300作为基准
    set_benchmark('000300.XSHG')
    # 开启动态复权模式(真实价格)
    set_option('use_real_price', True)

# 每个单位时间(如果按天回测,则每天调用一次,如果按分钟,则每分钟调用一次)调用一次
def handle_data(context, data):
    security = g.security
    # 获取股票的收盘价
    close_data = attribute_history(security, 5, '1d', ['close'])
    # 取得过去五天的平均价格
    MA5 = close_data['close'].mean()
    # 取得上一时间点价格
    current_price = close_data['close'][-1]
    # 取得当前的现金
    cash = context.portfolio.cash

    # 如果上一时间点价格高出五天平均价5%, 则全仓买入
    if current_price > 1.05*MA5:
        # 用所有 cash 买入股票
        order_value(security, cash)
        # 记录这次买入
        log.info("Buying %s" % (security))
    # 如果上一时间点价格低于五天平均价, 则空仓卖出
    elif current_price < 0.95*MA5 and context.portfolio.positions[security].closeable_amount > 0:
        # 卖出所有股票,使这只股票的最终持有量为0
        order_target(security, 0)
        # 记录这次卖出
        log.info("Selling %s" % (security))
    # 画出上一时间点价格
    record(stock_price=current_price)
```

### [#](https://dict.thinktrader.net/strategy/JoinQuant2QMT.html#qmt代码) QMT代码

> 单股示例，当价格高于5日均线平均价格1.05时买入，当价格低于5日平均价格0.95时卖出。

```
# QMT是一个本地端界面化软件，回测基准，回测手续费，回测起止时间都可在界面右侧栏进行设置

# 初始化函数，设定要操作的股票，参数等
def init(C):
    # 定义一个全局变量，设定要操作的股票
    # C.stock_list = C.get_stock_list_in_sector("沪深300")  # 获取沪深300股票列表
    C.stock_list = ["000001.SZ"] 
    # 设定回测初始资金
    C.capital = 1000000
    # 设定回测账号，实盘中账号在交易设置截面选择
    C.account_id = "testaccID"
    # # 关于回测时间，既可以在编辑器右侧栏设置，也可通过代码设置
    C.start = '2017-06-06 00:00:00'
    C.end = '2020-06-06 10:00:00'

def handlebar(C):
    #当前k线日期
    bar_date = timetag_to_datetime(C.get_bar_timetag(C.barpos), '%Y%m%d%H%M%S')
    # 获取市场行情，具体参数释义见文档
    market_data = C.get_market_data_ex(["open", "high", "low", "close"],C.stock_list,period = "1d",end_time = bar_date)

    # 获取当前账户资金
    for i in get_trade_detail_data(C.account_id,"stock","account"):
        cash = i.m_dAvailable

    # 获取当前持仓信息，本示例中的holding_dict结构是{stock_code:lots}
    holding_dict = {obj.m_strInstrumentID+"."+obj.m_strExchangeID : obj.m_nVolume for obj in get_trade_detail_data(C.account_id,"stock","position")}
    
    # 遍历gmd返回的字典数据
    for i in market_data:
        # 获取K线数据
        kline = market_data[i]
        # 获取收盘价序列
        close_data = kline["close"]
        # 计算MA5
        MA5 = close_data.rolling(5).mean()
        # 如果上一时间点价格高出五天平均价5%, 且当前无持仓, 则全仓买入
        if close_data.iloc[-1] > 1.05 * MA5.iloc[-1] and i not in holding_dict.keys():
            # 全仓买入，交易记录会被客户端自动记录在回测结果，此处展示按金额交易的方法
            passorder(23, 1123, C.account_id, i, 5, -1, 1, C)
            print(f"{bar_date}——{i}触发买入")
        elif close_data.iloc[-1] < 0.95 * MA5.iloc[-1] and i in holding_dict.keys():
            # 获取当前持仓数量
            lots = holding_dict[i]
            # 全仓卖出，交易记录会被客户端自动记录在回测结果，此处展示按股数交易的方法
            passorder(24, 1101, C.account_id, i, 5, -1, lots, C)
            print(f"{bar_date}——{i}触发卖出")
        
```

## [#](https://dict.thinktrader.net/strategy/JoinQuant2QMT.html#_3-qmt特色函数) 3. QMT特色函数

### [#](https://dict.thinktrader.net/strategy/JoinQuant2QMT.html#获取数据) 获取数据

get_weight_in_index 获取某只股票在某指数中的绝对权重

get_divid_factors 获取除权除息日和复权因子

get_top10_share_holder 获取十大股东数据

get_etf_info 根据ETF基金代码获取ETF申赎清单及对应成分股数据

get_etf_iopv 根据ETF基金代码获取ETF的基金份额参考净值

subscribe_whole_quote 订阅市场全推(tick)

subscribe_quote 订阅单股行情数据

get_option_list 获取指定日期期权列表

上次更新:

邀请注册送VIP优惠券

分享下方的内容给好友、QQ群、微信群,好友注册您即可获得VIP优惠券

玩转qmt,上迅投qmt知识库

登录后获取
