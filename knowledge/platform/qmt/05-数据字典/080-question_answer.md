---
title: "常见问题 | 迅投知识库"
source_url: "https://dict.thinktrader.net/dictionary/question_answer.html"
crawled_at: 2026-10-12T03:03:12+08:00
platform: QMT
category: 05-数据字典
depth: 3
tags: [平台/QMT, 状态/已抓取]
---

## [#](https://dict.thinktrader.net/dictionary/question_answer.html#token-使用相关) Token 使用相关

### [#](https://dict.thinktrader.net/dictionary/question_answer.html#无法连接-xtquant-服务) 无法连接 xtquant 服务

**描述：**

`Exception: 无法连接xtquant服务，请检查QMT-投研版或QMT-极简版是否开启`

提示

您未成功启动数据服务或请检查端口是否正确填写`port=XXXXX`。

### [#](https://dict.thinktrader.net/dictionary/question_answer.html#token-失效后是否再次订阅) Token 失效后是否再次订阅

**描述：**

如果token刷新失效后，换成新的token，是否需要再次进行订阅操作？

提示

需要让`xtdatacenter`调整token后重新连接行情，这个可以直接重启`xtdatacenter`的进程，这会导致下游的`xtdata`连接断开，断开后重连上需要重新订阅

## [#](https://dict.thinktrader.net/dictionary/question_answer.html#获取行情相关) 获取行情相关

### [#](https://dict.thinktrader.net/dictionary/question_answer.html#k-线数据缺失) k 线数据缺失

**描述：**

获取到的数据 1m 的 K 线数据缺失。

提示

如果是`get_market_data_ex`获取K线可以用fill_data=True来填充。

这个填充现在只对 k 线这种标准的数据做了，其他的特色数据没有处理填充的操作。

因此其他函数如果在获取k线数据的时候，如果存在数据缺失，用`fill_data=True`来填充，并不适用。

### [#](https://dict.thinktrader.net/dictionary/question_answer.html#订阅数据相关) 订阅数据相关

**描述：**

盘中订阅完行情全推后，切换行情站点需要重新订阅吗？

提示

1. 对于内置python策略来说，需要重新订阅

2. 对于xtquant.xtdata来说，不需要重新订阅，除非xtdata断开

### [#](https://dict.thinktrader.net/dictionary/question_answer.html#数据更新时间相关) 数据更新时间相关

**描述：**

收盘后什么时候能够下载到完整的当日行情数据

提示

股票收盘有两个时间点

大部分股票是 15:00，在交易所撮合尾盘竞价成交后下发行情，一般会延后几秒，大约是15:00:03左右

创业板、科创板有盘后定价交易，如果需要获取这部分数据的话，在15:30收盘之后获取，不需要的话也在15:00获取

期货一般在15:00收盘后有最新的收盘价，在16:30左右有结算价

上次更新:

邀请注册送VIP优惠券

分享下方的内容给好友、QQ群、微信群,好友注册您即可获得VIP优惠券

玩转qmt,上迅投qmt知识库

登录后获取

[债券数据](https://dict.thinktrader.net/dictionary/bond.html)[场景化示例](https://dict.thinktrader.net/dictionary/scenario_based_example.html)
