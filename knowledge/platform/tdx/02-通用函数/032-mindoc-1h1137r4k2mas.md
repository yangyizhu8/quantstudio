---
title: "# 获得订阅列表get_subscribe_hq_stock_list"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h1137r4k2mas.html"
crawled_at: 2026-10-12T02:41:51+08:00
platform: TDX
category: 02-通用函数
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

# [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h1137r4k2mas.html#获得订阅列表get-subscribe-hq-stock-list) 获得订阅列表get_subscribe_hq_stock_list

### [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h1137r4k2mas.html#获得当前策略订阅的股票列表) 获得当前策略订阅的股票列表

```
get_subscribe_hq_stock_list():
```

1

### [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h1137r4k2mas.html#接口使用) 接口使用

```
from tqcenter import tq

tq.initialize(__file__)

sub_list = tq.get_subscribe_hq_stock_list()
print(sub_list)
```

1
2
3
4
5
6

### [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h1137r4k2mas.html#数据样本) 数据样本

```
['600519.SH']
```

1

← [取消订阅更新unsubscribe_hq](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h112vh7jtsms.html) [刷新行情缓存refresh_cache](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h10f9145us1g.html) →
