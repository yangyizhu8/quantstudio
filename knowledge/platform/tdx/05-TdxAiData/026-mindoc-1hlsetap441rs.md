---
title: "获取分笔行情gettickdata | 通达信量化平台"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsetap441rs.html"
crawled_at: 2026-10-12T02:41:46+08:00
platform: TDX
category: 05-TdxAiData
depth: 2
tags: [平台/TDX, 状态/已抓取]
---

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsetap441rs.html#获取分笔行情get-tick-data) 获取分笔行情get_tick_data

根据股票代码和日期，获取指定日期的分笔成交数据

```
get_tick_data(
    stock_code: str,
    date: str,
    startxh: int = 0,
    wantnum: int = 0
) -> Dict
```

1
2
3
4
5
6

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsetap441rs.html#输入参数) 输入参数

| 参数 | 是否必选 | 参数类型 | 参数说明 |
| --- | --- | --- | --- |
| stock_code | Y | str | 单只证券代码，例如 688318.SH |
| date | Y | str | 分笔数据日期，支持 YYYYMMDD、YYYY-MM-DD |
| startxh | N | int | 起始分笔序号 |
| wantnum | N | int | 请求返回的分笔数据条数 |

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsetap441rs.html#返回数据) 返回数据

| 数据字段 | 默认返回 | 数据类型 | 数据说明 |
| --- | --- | --- | --- |
| BSFlag | N | List[str] | 买卖方向 0买 1卖 2中性 5盘后交易 8集合竞价 |
| Price | Y | List[str] | 成交价格 |
| Time | Y | List[str] | 成交时间 |
| Volume | Y | List[str] | 成交量 |

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsetap441rs.html#接口使用) 接口使用

获取 688318.SH 在 2026-08-20 的前 10 条分笔成交数据。

```
from tdxaidata import tqs

ticks = tqs.get_tick_data(
    stock_code="688318.SH",
    date="2026-08-20",
    startxh=0,
    wantnum=10,
)

print(ticks)
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

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsetap441rs.html#数据样本) 数据样本

```
{
    "BSFlag": ["2", "0", "0", "1", "1", "1", "1", "1", "0", "1"],
    "Price": ["76.26", "76.61", "76.62", "76.61", "76.61", "76.61", "76.61", "76.71", "76.97", "76.62"],
    "Time": ["092504", "093000", "093003", "093006", "093009", "093014", "093018", "093022", "093026", "093030"],
    "Volume": ["43", "2", "9", "29", "7", "18", "6", "19", "16", "53"]
}
```

1
2
3
4
5
6

← [TdxAiData后台接口](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbgqpdhv114.html) [获取指定日期分时数据get_minute_data](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlseu05n86ek.html) →
