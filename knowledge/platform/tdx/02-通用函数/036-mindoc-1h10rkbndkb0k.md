---
title: "# 发送消息到通达信客户端send_message"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h10rkbndkb0k.html"
crawled_at: 2026-10-12T02:41:54+08:00
platform: TDX
category: 02-通用函数
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

# [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h10rkbndkb0k.html#发送消息到通达信客户端send-message) 发送消息到通达信客户端send_message

### [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h10rkbndkb0k.html#发送消息给通达信客户端的tq策略界面) 发送消息给通达信客户端的TQ策略界面

```
send_message(msg_str: str) -> Dict:
```

1

### [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h10rkbndkb0k.html#输入参数) 输入参数

| 参数 | 是否必选 | 参数类型 | 参数说明 |
| --- | --- | --- | --- |
| msg_str | Y | str | 消息字符串 |

- 传入的字符串使用 | 可以让客户端将其分为两条（插入 \n 也可以分行显示）

### [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h10rkbndkb0k.html#接口使用) 接口使用

```
from tqcenter import tq
tq.initialize(__file__)
msg_str = "这是第一行. | 这是第二行. "
tq.send_message(msg_str)
```

1
2
3
4

← [下载特定数据文件download_file](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h10pqrdlj71o.html) [发送预警信号到客户端send_warn](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h10u5k9qjh8o.html) →
