---
title: "委托回调函数 on_order | 通达信量化平台"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hib55063ha7o.html"
crawled_at: 2026-10-12T02:42:17+08:00
platform: TDX
category: 06-Lambda平台
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hib55063ha7o.html#委托回调函数-on-order) 委托回调函数 on_order

```
def on_order(context, order):
    pass
```

1
2

用途：

- 用于在委托状态更新后进行回调。

参数说明：
`order: Order对象，包含订单详情。`

← [策略结束函数on_strategy_end](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9lb0bd8ck.html) [成交回调on_trade](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hib60s1q4hbg.html) →
