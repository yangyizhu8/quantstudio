---
title: "# 交易执行函数"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h7k5j4drr928.html"
crawled_at: 2026-10-12T02:42:05+08:00
platform: TDX
category: 04-交易函数
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

# [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h7k5j4drr928.html#交易执行函数) 交易执行函数

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h7k5j4drr928.html#对指定品种执行买卖下单操作) 对指定品种执行买卖下单操作

```
    def order_stock(cls,
                    account_id:int = 0,
                    stock_code:str = '', 
                    order_type:int = 0, 
                    order_volume:int = 0, 
                    price_type:int = 0, 
                    price:float = 0.0,
                    notify:int = 0):
```

1
2
3
4
5
6
7
8

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h7k5j4drr928.html#输入参数) 输入参数

| 参数 | 是否必选 | 参数类型 | 参数说明 |
| --- | --- | --- | --- |
| account_id | Y | int | 资金账号句柄 |
| stock_code | Y | str | 证券代码 |
| order_type | Y | int | 委托类型 |
| order_volume | Y | int | 委托数量 |
| price_type | Y | int | 报价类型 |
| price | Y | float | 委托价格 |
| notify | Y | int | 是否手动确认 |

- order_type可选类型： STOCK_BUY买(0)，STOCK_SELL卖(1)，CREDIT_BUY担保品买入(0)，CREDIT_SELL担保品卖出(1) ，CREDIT_FIN_BUY融资买入(69)，CREDIT_SLO_SELL融券卖出(70)，CREDIT_COV_BUY(71)，CREDIT_STK_REPAY(76)，ETF相关，FUNTURE相关，OPTION相关

- price_type可选类型：PRICE_MY自填价格(0)，PRICE_SJ市价(1)，PRICE_ZTJ涨停价(2)，PRICE_DTJ跌停价(3)

- 如果price_type为市价类型，具体是哪种市价，请在客户端->系统设置->参数中 进行设置

- 注：对于实盘交易账户是提示下单让用户确认。

- 注：交易需满足交易所规则。对于沪深交易所可转债品种，不支持市价交易方式。

- 实盘交易账户的自动下单请联系你的开户券商，开通并使用对应的支持TQ的版本。

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h7k5j4drr928.html#返回数据) 返回数据

| 数据 | 默认返回 | 数据类型 | 数据说明 |
| --- | --- | --- | --- |
| Value | Y | int | 成功标志，0失败，1待用户确认，2成功 |
| Wtbh | Y | str | 委托编号，只有成功时才会返回 |
| Msg | Y | str | 返回提示信息 |

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h7k5j4drr928.html#接口使用) 接口使用

```
from tqcenter import tq
from tqcenter import tqconst
tq.initialize(__file__)

myAccount = tq.stock_account(account="1190008847", account_type="STOCK")
print(myAccount)
order_res = tq.order_stock(account_id=myAccount,
                            stock_code="688318.SH",
                            order_type=tqconst.STOCK_BUY,
                            order_volume=200,
                            price_type=tqconst.PRICE_MY,
                            price=160.0)
print(order_res)
```

1
2
3
4
5
6
7
8
9
10
11
12
13

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h7k5j4drr928.html#数据样本) 数据样本

```
{'ErrorId': '0', 'Msg': '已发送信号至客户端，待用户确认！', 'Value': 1}
```

1

← [查询历史成交信息query_history_trade](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1hlnun1348n54.html) [撤单cancel_order_stock](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h84elp5atr6o.html) →
