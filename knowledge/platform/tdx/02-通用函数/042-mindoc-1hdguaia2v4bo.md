---
title: "# 检索证券信息get_match_stkinfo"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1hdguaia2v4bo.html"
crawled_at: 2026-10-12T02:41:59+08:00
platform: TDX
category: 02-通用函数
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

# [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1hdguaia2v4bo.html#检索证券信息get-match-stkinfo) 检索证券信息get_match_stkinfo

### [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1hdguaia2v4bo.html#检索证券信息) 检索证券信息

```
    def get_match_stkinfo(key_word:str = ''):
```

1

### [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1hdguaia2v4bo.html#输入参数) 输入参数

| 参数 | 是否必选 | 参数类型 | 参数说明 |
| --- | --- | --- | --- |
| key_word | Y | str | 关键词 |

### [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1hdguaia2v4bo.html#输出数据) 输出数据

| 名称 | 类型 | 数值 | 说明 |
| --- | --- | --- | --- |
| Code | Y | str | 证券代码 |
| Name | Y | str | 证券名称 |

### [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1hdguaia2v4bo.html#接口使用) 接口使用

```
from tqcenter import tq
tq.initialize(__file__)
match_stkinfo = tq.get_match_stkinfo(key_word='通达信')
print(match_stkinfo)
```

1
2
3
4

### [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1hdguaia2v4bo.html#数据样本) 数据样本

```
[{'Code': '880515.SH', 'Name': '通达信88'},
{'Code': '880818.SH', 'Name': '通达信热股'},
{'Code': '688318.SH', 'Name': '财富趋势'}]
```

1
2
3

← [调用客户端功能exec_to_tdx](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h85iq443j44c.html) [获取K线数据get_market_data*](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1ctuhthaq5qmg/mindoc-1h10g60jt68sc.html) →
