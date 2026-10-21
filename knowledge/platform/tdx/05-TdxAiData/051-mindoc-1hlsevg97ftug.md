---
title: "获取集合竞价getcallauction | 通达信量化平台"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsevg97ftug.html"
crawled_at: 2026-10-12T02:42:08+08:00
platform: TDX
category: 05-TdxAiData
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsevg97ftug.html#获取集合竞价get-call-auction) 获取集合竞价get_call_auction

根据证券代码获取当日集合竞价逐笔虚拟撮合数据，可选择仅早盘或含早盘与尾盘两段

```
get_call_auction(
stock_code: str,
type: int = 1
) -> DataFrame
```

1
2
3
4

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsevg97ftug.html#输入参数) 输入参数

| 参数 | 是否必选 | 参数类型 | 参数说明 |
| --- | --- | --- | --- |
| stock_code | Y | str | 单只证券代码，须带市场后缀，例如 688318.SH |
| type | N | int | 竞价范围，0 早盘和尾盘、1 仅早盘，默认 1 |

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsevg97ftug.html#返回数据) 返回数据

| 数据字段 | 默认返回 | 数据类型 | 数据说明 |
| --- | --- | --- | --- |
| LeaveQty | Y | List[str] | 未匹配量，可为负 |
| Price | Y | List[str] | 虚拟匹配价 |
| Time | Y | List[str] | 撮合时间，格式 HHMMSS |
| Volume | Y | List[str] | 虚拟匹配量 |

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsevg97ftug.html#接口使用) 接口使用

获取 688318.SH 当日集合竞价数据。

```
from tdxaidata import tqs
auction = tqs.get_call_auction(
stock_code="688318.SH",
)
print(auction)
```

1
2
3
4
5

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsevg97ftug.html#数据样本) 数据样本

```
   LeaveQty  Price    Time  Volume
0       -22  75.72  091502   13.00
1        12  75.70  091547   17.00
2        22  75.00  091602   40.00
3        13  73.00  091632  102.00
4        11  73.00  091635  104.00
5        20  75.00  091641   42.00
6         2  74.80  091717   64.00
7       -24  75.72  091841   13.00
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

```
{
"LeaveQty": ["-22", "12", "22", "13", "11", "20", "2", "-24"],
"Price": ["75.72", "75.70", "75.00", "73.00", "73.00", "75.00", "74.80", "75.72"],
"Time": ["091502", "091547", "091602", "091632", "091635", "091641", "091717", "091841"],
"Volume": ["13.00", "17.00", "40.00", "102.00", "104.00", "42.00", "64.00", "13.00"]
}
```

1
2
3
4
5
6

← [获取指数成分股get_zzgz_stocklist](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlseuoousuvo.html) [批量获取集合竞价get_call_auction_batch](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsf010md5gg.html) →
