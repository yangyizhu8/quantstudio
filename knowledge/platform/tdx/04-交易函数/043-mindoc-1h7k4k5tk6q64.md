---
title: "# 获取资金账户句柄"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h7k4k5tk6q64.html"
crawled_at: 2026-10-12T02:42:00+08:00
platform: TDX
category: 04-交易函数
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

# [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h7k4k5tk6q64.html#获取资金账户句柄) 获取资金账户句柄

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h7k4k5tk6q64.html#获取指定资金账户的句柄) 获取指定资金账户的句柄

```
    def stock_account(account:str = '',
                    account_type: str = 'stock') -> int:
```

1
2

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h7k4k5tk6q64.html#输入参数) 输入参数

| 参数 | 是否必选 | 参数类型 | 参数说明 |
| --- | --- | --- | --- |
| account | Y | str | 资金账号 |
| account_type | Y | str | 账号类型 |

- 获取句柄须先在客户端登陆指定账号

- 返回值大于0时为有效句柄，小于0时为无效句柄

- 涉及到交易函数的调用前，必须要先使用stock_account函数得到account句柄

- account为空时，默认为当前登陆账户

- account_type当前可选：'STOCK' 股票交易, 'CREDIT' 信用交易 'FUTURE' 期货交易 'OPTION' 期权交易。如果不设参数，就是'STOCK'。如果是期货通版本的期货账号，则必须要指定为'FUTURE'。

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h7k4k5tk6q64.html#接口使用) 接口使用

```
from tqcenter import tq
tq.initialize(__file__)
myAccount = tq.stock_account(account="1190008847", account_type="STOCK")
print(myAccount)
```

1
2
3
4

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h7k4k5tk6q64.html#数据样本) 数据样本

```
0
```

1

← [获取指定公式信息formula_get_info](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h3hrvkp4sc0g/mindoc-1hce356csmgmo.html) [查询账户资产query_stock_asset](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h84fvcjulrnc.html) →
