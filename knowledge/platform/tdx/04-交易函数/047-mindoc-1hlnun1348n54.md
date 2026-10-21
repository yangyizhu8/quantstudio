---
title: "获取历史成交queryhistorytrade | 通达信量化平台"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1hlnun1348n54.html"
crawled_at: 2026-10-12T02:42:04+08:00
platform: TDX
category: 04-交易函数
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1hlnun1348n54.html#获取历史成交query-history-trade) 获取历史成交query_history_trade

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1hlnun1348n54.html#根据账号与证券代码获取该证券在通达信客户端交易账号下的历史成交记录) 根据账号与证券代码获取该证券在通达信客户端交易账号下的历史成交记录

```
query_history_trade(
account_id: int = 0,
stock_code: str = ''
) -> list
```

1
2
3
4

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1hlnun1348n54.html#输入参数) 输入参数

| 参数 | 是否必选 | 参数类型 | 参数说明 |
| --- | --- | --- | --- |
| account_id | Y | int | 账户句柄，未指定账号时默认 0 |
| stock_code | Y | str | 证券代码，须带市场后缀，例如 688318.SH；为空时返回 [] |

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1hlnun1348n54.html#返回数据) 返回数据

| 数据字段 | 默认返回 | 数据类型 | 数据说明 |
| --- | --- | --- | --- |
| Date | Y | str | 成交日期，格式 YYYYMMDD |
| Time | Y | str | 成交时间，格式 HHMMSS |
| Code | Y | str | 证券代码，带市场后缀 |
| Name | Y | str | 证券名称 |
| BSFlag | Y | int | 买卖标志，0 为买入 |
| CjPrice | Y | str | 成交价格 |
| CjVol | Y | str | 成交量 |

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1hlnun1348n54.html#接口使用) 接口使用

获取账号下 688318.SH 的历史成交记录。

```
from tdxaidata import tqs
trade = tqs.query_history_trade(
account_id=0,
stock_code="688318.SH",
)
print(trade)
```

1
2
3
4
5
6

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1hlnun1348n54.html#数据样本) 数据样本

```
[
{
"Date": "20260318",
"Time": "150008",
"Code": "688318.SH",
"Name": "财富趋势",
"BSFlag": 0,
"CjPrice": "117.390",
"CjVol": "2000"
}
]
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

> 返回为成交记录列表，无成交记录时返回空列表 []；使用前需先在通达信客户端登录交易账号。

← [查询账户持仓信息query_stock_positions](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h7k5ar9kc508.html) [交易执行函数order_stock](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h7k5j4drr928.html) →
