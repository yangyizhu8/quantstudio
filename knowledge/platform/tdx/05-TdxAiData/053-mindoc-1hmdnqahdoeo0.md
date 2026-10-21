---
title: "获取集合排名行情getdomainsorthq | 通达信量化平台"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hmdnqahdoeo0.html"
crawled_at: 2026-10-12T02:42:09+08:00
platform: TDX
category: 05-TdxAiData
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hmdnqahdoeo0.html#获取集合排名行情get-domain-sorthq) 获取集合排名行情get_domain_sorthq

按指定排序栏目获取某一市场、板块或指数成分域内证券的排名行情，可附加返回最多5个其他栏目

```
get_domain_sorthq(
domain: int = 6,
colname: str = 'ZAF',
sorttype: int = 1,
wantcol: list[str] = [],
startxh: int = 0,
wantnum: int = 100,
data_source: int = 0
) -> DataFrame
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

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hmdnqahdoeo0.html#输入参数) 输入参数

| 参数 | 是否必选 | 参数类型 | 参数说明 |
| --- | --- | --- | --- |
| domain | Y | int | 排序域，可选值见下方「domain 有效值」 |
| colname | Y | str | 排序栏目，可选值见下方「colname / wantcol 有效值」 |
| sorttype | N | int | 排序方式，0 不排序、1 降序、2 升序，默认 1 |
| wantcol | N | list[str] | 附加返回的栏目，可为空 []，最多有效 5 个；可选值同colname |
| startxh | N | int | 起始序号，从 0 开始，默认 0 |
| wantnum | N | int | 返回条数，取值 1~200，默认 100 |
| data_source | N | int | 数据源，0 沪深京（默认）、1 扩展行情 |

**沪深京市场域**

| domain | 分类 | domain | 分类 |
| --- | --- | --- | --- |
| 6 | 沪深京A股 | 8 | 科创板 |
| 0 | 上证A股 | 12 | 北交所 |
| 2 | 深证A股 | 14 | 创业板 |

**沪深京板块域**

| domain | 分类 |
| --- | --- |
| 10000 | 综合板块 (缺省行业分类+概念板块+地区板块+风格板块) |
| 10001 | 板块数据 (缺省行业分类) |
| 10002 | 板块数据 (缺省细分行业分类) |
| 10004 | 概念板块 |
| 10005 | 风格板块 |
| 10006 | 地区板块 |
| 10007 | 缺省行业分类+概念板块 |
| 10008 | 研究行业一级 |
| 10009 | 研究行业二级 |
| 10010 | 研究行业三级 |
| 20000 | 前缀"880"板块成份股 20000+880代码的后3位 |
| 21000 | 前缀"881"板块成份股 20000+881代码的后3位 |
| 22000 | 前缀"882"板块成份股 20000+882代码的后3位 |
| 30000 | 指数板块成份股的起始序号,深圳为30XXX,上海为31XXX,北京为32XXX,XXX为代码的后三位 |

例如：30000+规则

| domain | 指数 |
| --- | --- |
| 30001 | 深证成指 399001 |
| 30006 | 创业板指 399006 |
| 31016 | 上证50 000016 |
| 32050 | 北证50 899050 |

**沪深京分类域**

| domain | 分类 |
| --- | --- |
| 11000 | 沪深主板 |
| 11001 | 沪股通 |
| 11009 | 深股通 |
| 11010 | CDR |
| 11002 | 沪深300成份股 |
| 11003 | 融资融券标的 |
| 11050 | 可转债标的 |
| 11053 | 中证500成份股 |
| 11054 | 中证1000成份股 |
| 11055 | 富时A50成份股 |
| 11057 | 中证2000成份股 |
| 11058 | 中证A500成份股 |
|  |  |
| 11004 | 沪深重点指数 |
| 11052 | 沪深主要指数 |
|  |  |
| 11080 | 科创成长层 |
| 11006 | 新股次新股 |
| 11007 | 风险警示板 |
| 11008 | 退市整理板 |
|  |  |
| 11023 | 可转换债券 |
| 11005 | 沪深ETF基金 |
| 11012 | 沪深LOF基金 |
| 11027 | 债券类ETF |
| 11028 | 货币类ETF |
| 11029 | 黄金类ETF |
| 11030 | T+0货币基金 |
| 11031 | 跨境ETF和QDII |
| 11033 | 期货ETF |
| 11034 | 跨市场ETF |
| 11043 | 跟踪指数ETF(每种指数列一个) |
| 11044 | 科技ETF |
| 11045 | 商品LOF |
| 11046 | 双创基金 |
| 11047 | REITs基金 |
| 11048 | T+0基金 |
| 11051 | 沪深股通ETF |
| 11059 | 贵金属主题基金 |

**扩展行情--期货期权等板块域**

| domain | 分类 |
| --- | --- |
| 8 | 上海个股期权 |
| 9 | 深圳个股期权 |
| 47 | 中金所期货 |
| 28 | 郑州期货 |
| 29 | 大连期货 |
| 30 | 上海期货 |
| 66 | 广州期货 |
| 60 | 主力期货合约 |

**扩展行情--港股美股板块域**

| domain | 分类 |
| --- | --- |
| 10000 | 港股综合板块 (行业分类+概念板块) |
| 10001 | 港股板块（行业分类） |
| 10002 | 港股板块（概念板块） |
| 10003 | 美股综合板块 (行业分类+概念板块) |
| 10004 | 美股板块（行业分类） |
| 10005 | 美股板块（概念板块） |
| 20000 | 前缀"HK"板块成份股 20000+HK代码的后4位 |
| 30000 | 前缀"US"板块成份股 30000+US代码的后4位 |
|  |  |
| 12001 | 恒指成份股 |
| 12002 | 红筹股 |
| 12004 | 国企股 |
| 12005 | 国企A+H股 |
| 12006 | 港股通标的 |
| 12007 | 国指成份股 |
| 12008 | 红筹成份股 |
| 12009 | 港股-次新股 |
| 12012 | 恒生科技成份股 |
| 12027 | 港股通ETF |
| 12030 | 港交所REITs |
| 12035 | 蓝筹股 |
|  |  |
| 13001 | 美股-中概股 |
| 13002 | 美股-明星股 |
| 13003 | 道琼斯成份股 |
| 13004 | 标普成份股 |
| 13005 | 纳斯达克100 |
| 13006 | 美股-热门ETF |
| 13007 | 美股-次新股 |
| 13008 | 美股-REITs |
| 13009 | 美股-费城半导体 |

> 期货期权、港股美股板块域需在函数入参中指定扩展数据源（data_source=1）

**colname / wantcol 有效值**

| 栏目 | 入参值 | 适用范围 |
| --- | --- | --- |
| 代码（固定栏目） | Code | 扩展也支持 |
| 昨收 （固定栏目） | LastClose | 扩展也支持 |
| 现价 （固定栏目） | Now | 扩展也支持 |
| 总手（固定栏目） | Volume | 扩展也支持 |
| 总金额 | Amount | 万元，扩展也支持 |
| 涨幅 （固定栏目） | ZAF | 扩展也支持 |
| 振幅 | ZEF | 扩展也支持 |
| 市盈率/期货期权持仓量 | Syl | 扩展也支持 |
| 委比 | Wtb | 扩展也支持 |
| 外盘（板块指数为涨停家数） | ZTGPNum |  |
| 封单额 | FCAmo | 万元 |
| 开盘成交额 | OpenAmo | 万元 |
| 开盘换手率 | OpenHSL |  |
| 量比 | fLianB | 扩展也支持 |
| 换手率 | fHSL | 扩展也支持 |
| 流通市值 | Ltsz | 亿元，扩展也支持 |
| 总市值 | Zsz | 亿元，扩展也支持 |
| 涨速 | Zangsu | 扩展也支持 |
| 活跃度 | ItemNum | 扩展也支持 |
| 量涨速 | vzangsu |  |
| 主买净额 | Zjl | 万元，扩展也支持 |
| 主力净额 | Zjl_HB | 万元 |
| 竞价涨停委买额 | OpenZTBuy | 万元 |
| 开盘昨比% | OpenLastB |  |
| 2分钟金额 | FzAmo | 万元 |
| 连涨天数 | ConZAFDateNum | 扩展也支持 |
| 连续涨停天数 | ZTDateNum |  |
| 连续跌停天数 | DTDateNum |  |
| 昨日涨幅% | ZAFYesterday | 扩展也支持 |
| 3日涨幅% | ZAFPre3 | 扩展也支持 |
| 最近5日的涨幅% | ZAFPre5 |  |
| 最近10日的涨幅% | ZAFPre10 |  |
| 盘后成交额 | PHAmo | 万元 |
| 20日涨幅% | ZAFPre20 | 扩展也支持 |
| 60日涨幅% | ZAFPre60 | 扩展也支持 |
| 年初至今% | ZAFYear | 扩展也支持 |

> 上表右列是 colname 与 wantcol 唯一合法的入参写法，须按原样书写。colname 填其他内容会直接报错并返回空，wantcol 填其他内容会被忽略。

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hmdnqahdoeo0.html#返回数据) 返回数据

| 数据字段 | 默认返回 | 数据类型 | 数据说明 |
| --- | --- | --- | --- |
| Code | Y | List[str] | 证券代码，带市场后缀 |
| Name | Y | List[str] | 证券名称 |
| Now | Y | List[str] | 现价 |
| LastClose | Y | List[str] | 昨收价 |
| ZAF | Y | List[str] | 涨幅，单位 % |
| Volume | Y | List[str] | 成交量 |
| CurCol | N | List[str] | 当前排序栏目的取值，仅当 colname 不属于固定栏目时返回 |
| wantcol 指定栏目 | N | List[str] | wantcol 中指定的栏目，按栏目追加对应列 |

> 未安装 pandas 时返回同结构的 dict。

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hmdnqahdoeo0.html#接口使用) 接口使用

获取沪深300成分股按涨幅降序排名前 5，并附加返回成交额与换手率。

```
from tdxaidata import tqs
rank = tqs.get_domain_sorthq(
domain=11002,
colname="ZAF",
sorttype=1,
wantcol=["Amount", "fHSL"],
startxh=0,
wantnum=5,
)
print(rank)
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

#### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hmdnqahdoeo0.html#数据样本) 数据样本

```
        Code  Name     Now   LastClose    ZAF      Volume         Amount  fHSL
0  601238.SH  广汽集团    5.60    5.09  10.02    78010.00    43685772.00  0.11
1  002074.SZ  国轩高科   28.68   26.07  10.01   455830.00  1285766144.00  2.62
2  000002.SZ  万 科Ａ    4.08    3.71   9.97  7974463.00  3157589504.00  8.21
3  300450.SZ  先导智能   33.58   31.20   7.63   888958.00  2928257024.00  5.70
4  688082.SH  盛美上海  301.68  286.01   5.48    67551.00  2019369472.00  1.40
```

1
2
3
4
5
6

← [批量获取集合竞价get_call_auction_batch](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsf010md5gg.html) [订阅实时行情subscribe](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsf0u13l0d4.html) →
