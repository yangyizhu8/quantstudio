---
title: "Bar 回调函数 | 通达信量化平台"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9jic0qbu8.html"
crawled_at: 2026-10-12T02:42:14+08:00
platform: TDX
category: 06-Lambda平台
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9jic0qbu8.html#bar-回调函数) Bar 回调函数

```
def handle_bar(context, bar_dict):
    pass
```

1
2

用途：

- 每个回测时间点执行策略逻辑。

- 读取当前 bar 行情。

- 查询历史行情。

- 发出下单指令。

- 记录自定义指标。

`bar_dict` 是当前时间点的行情字典，key 是证券代码，value 是 `Bar` 对象。

示例：

```
def handle_bar(context, bar_dict):
    price = bar_dict["000001.SZ"].close
    record(price=price)
```

1
2
3

兼容`handle_data(context, data)`函数名

← [开盘前函数before_trading](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9irt0nu3s.html) [收盘后函数after_trading](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9kq57km1c.html) →
