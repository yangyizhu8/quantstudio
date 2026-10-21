---
title: "# 撤单"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h84elp5atr6o.html"
crawled_at: 2026-10-12T02:41:45+08:00
platform: TDX
category: 04-交易函数
depth: 2
tags: [平台/TDX, 状态/已抓取]
---

# [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h84elp5atr6o.html#撤单) 撤单

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h84elp5atr6o.html#根据委托编号撤单) 根据委托编号撤单

```
    def cancel_order_stock(account_id:int = 0,
                            stock_code:str = '',
                            order_id:str = ''):
```

1
2
3

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h84elp5atr6o.html#输入参数) 输入参数

| 参数 | 是否必选 | 参数类型 | 参数说明 |
| --- | --- | --- | --- |
| account_id | Y | int | 资金账号句柄 |
| stock_code | Y | str | 证券代码 |
| order_id | Y | int | 委托编号 |

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h84elp5atr6o.html#返回数据) 返回数据

| 数据 | 默认返回 | 数据类型 | 数据说明 |
| --- | --- | --- | --- |
| Value | Y | int | 成功标志，0失败，1成功 |
| Msg | Y | str | 返回提示信息 |

- 撤单成功后Status状态会变成WTSTATUS_NULL无效单(0)、WTSTATUS_BCBC部分成交部分撤单(4)或WTSTATUS_ALLCD全部撤单(5)

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h84elp5atr6o.html#接口使用) 接口使用

```
from tqcenter import tq
from tqcenter import tqconst
tq.initialize(__file__)

myAccount = tq.stock_account(account="1190008847", account_type="STOCK")
print(myAccount)

stock_orders = tq.query_stock_orders(account_id=myAccount, stock_code="")
print(stock_orders)

cancel_res = tq.cancel_order_stock(account_id=myAccount,
                            stock_code=stock_orders[0]['Code'],
                            order_id=stock_orders[0]['Wtbh'])
print(cancel_res)
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
14

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h84elp5atr6o.html#数据样本) 数据样本

```
{'Value': 1, 'ErrorId': '0', 'Msg': '提交撤单成功！'}
```

1

← [交易执行函数order_stock](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h7k5j4drr928.html) [HTTP方式调用](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hdhbmi50d038.html) →
