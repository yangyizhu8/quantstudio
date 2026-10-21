---
title: "# 刷新历史K线缓存refresh_kline"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h10fh9m6recg.html"
crawled_at: 2026-10-12T02:41:53+08:00
platform: TDX
category: 02-通用函数
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

# [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h10fh9m6recg.html#刷新历史k线缓存refresh-kline) 刷新历史K线缓存refresh_kline

### [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h10fh9m6recg.html#根据股票和周期刷新历史k线缓存-如果本地没有下载完整的日线等数据-则可以调用这个函数定向下载某些品种某些周期的历史k线数据) 根据股票和周期刷新历史K线缓存，如果本地没有下载完整的日线等数据，则可以调用这个函数定向下载某些品种某些周期的历史K线数据

```
refresh_kline(stock_list: List[str] = [], period: str = '')
```

1

### [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h10fh9m6recg.html#输入参数) 输入参数

| 参数 | 是否必选 | 参数类型 | 参数说明 |
| --- | --- | --- | --- |
| stock_list | Y | List[str] | 证券代码列表，证券代码格式为6位数+市场后缀（.SH/.SZ/.BJ等） |
| period | Y | str | 周期 1d为日线、1m为一分钟线、5m为五分钟线，只支持这三种，其它周期的数据均由这三种数据生成 |

### [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h10fh9m6recg.html#接口使用) 接口使用

```
from tqcenter import tq
tq.initialize(__file__)
refresh_kline = tq.refresh_kline(stock_list=['688318.SH'],period='1d')
print(refresh_kline)
```

1
2
3
4

### [#](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h10fh9m6recg.html#数据样本) 数据样本

注：如果在盘中交易时间段下载1m和5m分钟线，只能下载到截止上个交易日的数据
使用后会在客户端弹出刷新数据的加载界面，加载完成后才会有返回

```
{
   "Error" : "refresh kline cache success.",
   "ErrorId" : "0",
   "run_id" : "1"
}
```

1
2
3
4
5

← [刷新行情缓存refresh_cache](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h10f9145us1g.html) [下载特定数据文件download_file](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1h10pqrdlj71o.html) →
