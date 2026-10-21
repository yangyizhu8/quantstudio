---
title: "收盘后函数 | 通达信量化平台"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9kq57km1c.html"
crawled_at: 2026-10-12T02:42:15+08:00
platform: TDX
category: 06-Lambda平台
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9kq57km1c.html#收盘后函数) 收盘后函数

```
def after_trading(context):
    pass
```

1
2

用途：

- 每个交易日收盘后汇总日志。

- 检查持仓。

- 输出诊断信息。

兼容`after_trading_end(context, data)`函数名

← [Bar回调函数handle_bar](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9jic0qbu8.html) [策略结束函数on_strategy_end](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9lb0bd8ck.html) →
