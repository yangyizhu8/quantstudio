---
title: "开盘前函数 | 通达信量化平台"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9irt0nu3s.html"
crawled_at: 2026-10-12T02:42:14+08:00
platform: TDX
category: 06-Lambda平台
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9irt0nu3s.html#开盘前函数) 开盘前函数

```
def before_trading(context):
    pass
```

1
2

用途：

- 每个交易日开盘前准备数据。

- 重置当日临时变量。

- 生成当日候选股票池。

兼容`before_trading_start(context, data)`函数名

← [初始化函数init](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9hh3kshvo.html) [Bar回调函数handle_bar](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9jic0qbu8.html) →
