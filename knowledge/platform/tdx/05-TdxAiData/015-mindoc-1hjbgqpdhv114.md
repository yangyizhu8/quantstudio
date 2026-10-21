---
title: "TdxAiData简介 | 通达信量化平台"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbgqpdhv114.html"
crawled_at: 2026-10-12T02:41:35+08:00
platform: TDX
category: 05-TdxAiData
depth: 1
tags: [平台/TDX, 状态/已抓取]
---

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbgqpdhv114.html#tdxaidata简介) TdxAiData简介

通达信 TdxAiData Python 数据接口（跨平台：Windows / macOS / Linux）。用tqs类从TdxAiData动态库（Windows 用 TdxAiData.dll、macOS 用 libTdxAiData.dylib、Linux 用 libTdxAiData.so）获取行情、基础信息、专业数据、分笔，订阅实时行情。
如果在其他非Windows通达信客户端体系下需要行情数据，需要在python环境下单独安装TdxAiData库。
**开始使用前可以先看下这个文章**：[通达信TdxAiData (opens new window)](https://mp.weixin.qq.com/s/DXChrQ79yGB1AnPLjywK8A)

pip库如下：

```
  pip install tdxaidata -i https://pypi.tuna.tsinghua.edu.cn/simple
```

1

使用时，需要import

```
from tdxaidata import tqs
```

1

后台数据模式需要在TdxAiData.ini中设置Key
token请登录通达信个人版商城的会员中心->积分和Key管理->创建数据服务Key（数据服务类型）
支持的函数见以下章节的tqserver中介绍。

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbgqpdhv114.html#tqserver介绍) tqserver介绍

`tqserver(即tdxaidata)` 是一个基于 `TdxAiData.dll` 的Python数据接口封装，用于从通达信数据后台获取行情和证券相关数据。此模式下，不需要开启通达信客户端。

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbgqpdhv114.html#主要功能) 主要功能

- 获取 K 线、分钟线和历史行情

- 获取实时行情快照

- 获取股票基础信息和扩展信息

- 获取股票列表、板块列表和板块成分股

- 获取交易日历

- 获取除权除息数据

- 获取财务数据、股票交易数据、板块交易数据和市场交易数据

- 获取可转债、ETF、涨跌停和股本数据

- 获取分笔数据

- 订阅和取消订阅实时行情

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbgqpdhv114.html#基本用法) 基本用法

`tqserver(即tdxaidata)` 对外提供的主要类是 `tqs`，调用示例：

```
from tdxaidata import tqs

data = tqs.get_market_data(
    field_list=["Open", "High", "Low", "Close"],
    stock_list=["600000.SH"],
    period="1d",
    start_time="2025-01-01",
    end_time="2025-01-31",
)

print(data)
# 一分钟周期云获取方式后期不做限制 不受限100天

data_divid_factors=tqs.get_divid_factors("600000.SH", "", "")
print(data_divid_factors)
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
15
16
17

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbgqpdhv114.html#与tqcenter的区别) 与tqcenter的区别

tqserver 与 tqcenter 的主要区别如下：

- 直接通过调用 TdxAiData.dll 从服务器获取数据

- 不依赖本地数据

- 不依赖通达信策略运行环境

- 主要面向数据查询和行情订阅

- 不提供交易功能

- 不提供公式执行功能

- 不提供通达信客户端交互功能

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbgqpdhv114.html#tqserver支持的tqcenter函数) tqserver支持的tqcenter函数

文档中带有`*`的接口为tq和tqs通用接口
以下接口使用方法于tqcenter相同

- `get_market_data`

- `get_market_snapshot`

- `get_stock_info`

- `get_more_info`

- `get_pricevol`

- `get_zdt_data`

- `get_exday_data`

- `get_divid_factors`

- `get_relation`

- `get_trading_dates`

- `get_ipo_info`

- `get_gb_info`

- `get_gb_info_by_date`

- `get_market_snapshot_batch`

- `get_stock_info_batch`

- `get_more_info_batch`

- `get_financial_data`

- `get_financial_data_by_date`

- `get_gpjy_value`

- `get_gpjy_value_by_date`

- `get_bkjy_value`

- `get_bkjy_value_by_date`

- `get_scjy_value`

- `get_scjy_value_by_date`

- `get_gp_one_data`

- `get_trackzs_etf_info`

- `get_kzz_info`

- `get_kzz_info_batch`

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbgqpdhv114.html#tqserver特有函数) tqserver特有函数

- `get_tick_data`

- `get_minute_data`

- `get_zzgz_stocklist`

- `get_call_auction`

- `get_call_auction_batch`

- `get_domain_sorthq`

- `subscribe`

- `unsubscribe`

← [HTTP方式调用](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hdhbmi50d038.html) [获取分笔行情get_tick_data](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsetap441rs.html) →
