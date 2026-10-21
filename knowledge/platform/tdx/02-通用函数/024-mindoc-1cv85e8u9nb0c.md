---
title: "# 初始化initialize"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1cv85e8u9nb0c.html"
crawled_at: 2026-10-12T02:41:44+08:00
platform: TDX
category: 02-通用函数
depth: 2
tags: [平台/TDX, 状态/已抓取]
---

# [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1cv85e8u9nb0c.html#初始化initialize) 初始化initialize

```
initialize(__file__) #所有策略连接通达信客户端都必须调用此函数进行初始化
```

1

### [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1cv85e8u9nb0c.html#调用方法) 调用方法:

```
from tqcenter import tq

tq.initialize(__file__)
```

1
2
3

### [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1cv85e8u9nb0c.html#注意事项) 注意事项:

1.tqcenter本地客户端模式下，使用tq前需要调用这个函数进行初始化。
2."initialize"不可修改。
3.该函数用于初始化，任何一个策略都必须有该函数。

← [快速开始第一个策略](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1cfsjkbf8f3is/mindoc-1cv7o3nje2gu8.html) [订阅行情subscribe_hq](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h1104d65vr68.html) →
