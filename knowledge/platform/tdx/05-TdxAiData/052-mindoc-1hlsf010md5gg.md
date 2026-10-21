---
title: "批量获取集合竞价getcallauction_batch | 通达信量化平台"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsf010md5gg.html"
crawled_at: 2026-10-12T02:42:08+08:00
platform: TDX
category: 05-TdxAiData
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsf010md5gg.html#批量获取集合竞价get-call-auction-batch) 批量获取集合竞价get_call_auction_batch

根据证券代码列表批量获取当日集合竞价逐笔虚拟撮合数据

```
get_call_auction_batch(
stock_list: list[str] = [],
field_list: list[str] = [],
type: int = 1,
return_df: bool = False
) -> Dict
```

1
2
3
4
5
6

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsf010md5gg.html#输入参数) 输入参数

| 参数 | 是否必选 | 参数类型 | 参数说明 |
| --- | --- | --- | --- |
| stock_list | Y | list[str] | 证券代码列表，须带市场后缀，例如 ["688318.SH", "600000.SH"] |
| field_list | N | list[str] | 指定返回字段，为空 [] 返回全部字段，未知字段静默忽略 |
| type | N | int | 竞价范围，0 早盘和尾盘、1 仅早盘，默认 1 |
| return_df | N | bool | 为 True 且已安装 pandas 时返回 DataFrame（行索引为证券代码），否则返回 dict |

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsf010md5gg.html#返回数据) 返回数据

| 数据字段 | 默认返回 | 数据类型 | 数据说明 |
| --- | --- | --- | --- |
| LeaveQty | Y | List[str] | 未匹配量，可为负 |
| Price | Y | List[str] | 虚拟匹配价 |
| Time | Y | List[str] | 撮合时间，格式 HHMMSS |
| Volume | Y | List[str] | 虚拟匹配量 |

> 各代码下的字段与单只接口 get_call_auction 相同。

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsf010md5gg.html#接口使用) 接口使用

批量获取 600000.SH 与 688318.SH 当日集合竞价数据。

```
from tdxaidata import tqs
auction = tqs.get_call_auction_batch(
stock_list=["600000.SH", "688318.SH"],
return_df=True,
)
print(auction)
```

1
2
3
4
5
6

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsf010md5gg.html#数据样本) 数据样本

```
                                                              LeaveQty                                              Price                                               Time                                              Volume
600000.SH  [-19, -3480, -3484, -3481, -3457, -3481, 81, 12, 8, ...  [9.16, 9.17, 9.17, 9.17, 9.17, 9.17, 9.16, 9.1...  [091500, 091503, 091506, 091509, 091512, 09151...  [1.00, 81.00, 81.00, 84.00, 108.00, 84.00, 84....
688318.SH  [-22, 12, 22, 13, 11, 20, 2, -24, -21, -17, -15, -...  [75.72, 75.70, 75.00, 73.00, 73.00, 75.00, 74....  [091502, 091547, 091602, 091632, 091635, 09164...  [13.00, 17.00, 40.00, 102.00, 104.00, 42.00, 6...
```

1
2
3

```
{
"600000.SH": {
"LeaveQty": ["-19", "-3480", "-3484", "-3481", "-3457", "-3481", "81", "12"],
"Price": ["9.16", "9.17", "9.17", "9.17", "9.17", "9.17", "9.16", "9.16"],
"Time": ["091500", "091503", "091506", "091509", "091512", "091515", "091524", "091527"],
"Volume": ["1.00", "81.00", "81.00", "84.00", "108.00", "84.00", "84.00", "153.00"]
},
"688318.SH": {
"LeaveQty": ["-22", "12", "22", "13", "11", "20", "2", "-24"],
"Price": ["75.72", "75.70", "75.00", "73.00", "73.00", "75.00", "74.80", "75.72"],
"Time": ["091502", "091547", "091602", "091632", "091635", "091641", "091717", "091841"],
"Volume": ["13.00", "17.00", "40.00", "102.00", "104.00", "42.00", "64.00", "13.00"]
}
}
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

← [获取集合竞价get_call_auction](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsevg97ftug.html) [获取集合排名行情get_domain_sorthq](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hmdnqahdoeo0.html) →
