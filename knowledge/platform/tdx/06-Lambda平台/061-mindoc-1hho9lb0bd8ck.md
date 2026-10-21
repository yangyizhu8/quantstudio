---
title: "策略结束函数 | 通达信量化平台"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9lb0bd8ck.html"
crawled_at: 2026-10-12T02:42:16+08:00
platform: TDX
category: 06-Lambda平台
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9lb0bd8ck.html#策略结束函数) 策略结束函数

```
def on_strategy_end(context):
    pass
```

1
2

用途：

- 回测结束后执行收尾逻辑。

- 汇总自定义统计结果。

← [收盘后函数after_trading](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9kq57km1c.html) [订单状态变化时函数on_order](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hib55063ha7o.html) →
