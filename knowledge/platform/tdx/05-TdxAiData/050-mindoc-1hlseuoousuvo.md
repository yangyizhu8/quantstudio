---
title: "获取指数成分股getzzgzstocklist | 通达信量化平台"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlseuoousuvo.html"
crawled_at: 2026-10-12T02:42:07+08:00
platform: TDX
category: 05-TdxAiData
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlseuoousuvo.html#获取指数成分股get-zzgz-stocklist) 获取指数成分股get_zzgz_stocklist

根据指数代码，获取指定指数的成分股列表

```
get_zzgz_stocklist(
    index_code: str = "",
    list_type: int = 0
) -> List
```

1
2
3
4

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlseuoousuvo.html#输入参数) 输入参数

| 参数 | 是否必选 | 参数类型 | 参数说明 |
| --- | --- | --- | --- |
| index_code | Y | str | 指数代码，例如 `000300.SH` |
| list_type | N | int | 成分股返回类型，默认值为 `0` |

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlseuoousuvo.html#参数说明) 参数说明

- `index_code` 必须传入指数代码，不能为空。支持沪(SH)，深(SZ)，京(BJ)，中证(CSI)和国证(CNI)等类型的指数。

- 指数代码建议带市场后缀，例如：

```
399001.SZ
000905.SH
000510.SH
000852.SH
899050.BJ
932000.CSI
980112.CNI
```

1
2
3
4
5
6
7

- `list_type` 默认值为0，如果为1则表示返回证券名称。

```
get_zzgz_stocklist(
    index_code="000300.SH",
    list_type=0,
)
```

1
2
3
4

可以返回沪深 300 成分股代码列表。

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlseuoousuvo.html#返回数据) 返回数据

返回列表中的股票代码带市场后缀，例如：

```
000001.SZ
000002.SZ
000063.SZ
600000.SH
```

1
2
3
4

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlseuoousuvo.html#接口使用) 接口使用

> 获取沪深 300 指数 `000300.SH` 的成分股列表。

```
from tdxaidata import tqs

stock_list = tqs.get_zzgz_stocklist(
    index_code="000300.SH",
    list_type=0,
)

print(stock_list)
```

1
2
3
4
5
6
7
8

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlseuoousuvo.html#数据样本) 数据样本

```
[
    "000001.SZ",
    "000002.SZ",
    "000063.SZ",
    "000100.SZ",
    "000157.SZ",
    "000166.SZ",
    "000301.SZ",
    "000333.SZ",
    "000338.SZ",
    "000408.SZ"
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
12

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlseuoousuvo.html#实际返回说明) 实际返回说明

使用以下参数进行实际验证：

```
tqs.get_zzgz_stocklist(
    index_code="000300.SH",
    list_type=0,
)
```

1
2
3
4

实际返回：

```
返回类型：list
返回数量：300
```

1
2

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlseuoousuvo.html#注意事项) 注意事项

- `index_code` 不能为空。

- `list_type` 必须为整数。

- 指数代码无效、指数市场不支持或服务端没有数据时，可能返回空列表或空对象。

← [获取指定日期分时数据get_minute_data](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlseu05n86ek.html) [获取集合竞价get_call_auction](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsevg97ftug.html) →
