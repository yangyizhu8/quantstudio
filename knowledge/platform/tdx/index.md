---
doc: tdx-index
title: TDX（通达信 TdxQuant）· 文档分域索引（MOC）
version: v1.0
updated_at: 2026-10-12
---

# TDX（通达信 TdxQuant）· 文档分域索引（MOC）

> **来源站点**：`https://help.tdx.com.cn/quant/docs/markdown/`　|　**抓取时点**：2026-10-12T02:41:23+08:00　|　**页数**：**83**
> **★ 以实测为准**：本域为平台官方文档快照，**只作参考线索**；契约与判据一律以运行实测为准。

## 分域总览

| 分类 | 页数 | 入口 |
| --- | --- | --- |
| 01-入门与安装 | 6 | 见下 |
| 02-通用函数 | 15 | 见下 |
| 03-数据获取 | 6 | 见下 |
| 04-交易函数 | 8 | 见下 |
| 05-TdxAiData | 10 | 见下 |
| 06-Lambda平台 | 22 | 见下 |
| 07-常量与字典 | 1 | 见下 |
| 08-公式调用 | 2 | 见下 |
| 09-场景化示例 | 5 | 见下 |
| 10-HTTP与Skills | 2 | 见下 |
| 11-FAQ | 1 | 见下 |
| 12-公众号文章 | 5 | 见下 |

---

## 01-入门与安装

- [TdxQuant 简介](01-入门与安装/001-mindoc-1cfsjkbf8f3is.md)
- [版本更新说明](01-入门与安装/002-TdxQuantVersion.md)
- [安装Python及VSCode等开发环境](01-入门与安装/003-mindoc-1d00970eq1rtc.md)
- [1. 安装通达信终端](01-入门与安装/004-mindoc-1d00kk3jsibbc.md)
- [步骤分解](01-入门与安装/005-mindoc-1cv7o3nje2gu8.md)
- [什么是量化交易 | 通达信量化平台](01-入门与安装/020-mindoc-1h12t4q6fg29o.md)

## 02-通用函数

- [通用函数](02-通用函数/006-ctx.stock.md)
- [初始化initialize](02-通用函数/024-mindoc-1cv85e8u9nb0c.md)
- [订阅行情subscribe_hq](02-通用函数/030-mindoc-1h1104d65vr68.md)
- [取消订阅更新unsubscribe_hq](02-通用函数/031-mindoc-1h112vh7jtsms.md)
- [获得订阅列表get_subscribe_hq_stock_list](02-通用函数/032-mindoc-1h1137r4k2mas.md)
- [刷新行情缓存(最新snapshot和K线数据)refresh_cache](02-通用函数/033-mindoc-1h10f9145us1g.md)
- [刷新历史K线缓存refresh_kline](02-通用函数/034-mindoc-1h10fh9m6recg.md)
- [下载特定数据文件download_file](02-通用函数/035-mindoc-1h10pqrdlj71o.md)
- [发送消息到通达信客户端send_message](02-通用函数/036-mindoc-1h10rkbndkb0k.md)
- [发送预警信号send_warn](02-通用函数/037-mindoc-1h10u5k9qjh8o.md)
- [发送文件到客户端send_file](02-通用函数/038-mindoc-1h10u17ue9464.md)
- [发送回测数据send_bt_data](02-通用函数/039-mindoc-1h10vc2pot87c.md)
- [导出多组数据到通达信客户端 print_to_tdx](02-通用函数/040-mindoc-1h62l8kg2k4jc.md)
- [调用客户端功能](02-通用函数/041-mindoc-1h85iq443j44c.md)
- [检索证券信息get_match_stkinfo](02-通用函数/042-mindoc-1hdguaia2v4bo.md)

## 03-数据获取

- [通达信TQ可以获取如下数据](03-数据获取/007-mindoc-1ctuhthaq5qmg.md)
- [财务类数据](03-数据获取/008-TdxQuant.md)
- [分类/板块成份股](03-数据获取/009-mindoc-1ctuhttn72svo.md)
- [自选股/自定义板块](03-数据获取/010-mindoc-1h139a4ckchkk.md)
- [ETF/可转债/期货数据](03-数据获取/011-mindoc-1h13a594nhvb4.md)
- [获取K线行情get_market_data](03-数据获取/080-mindoc-1h10g60jt68sc.md)

## 04-交易函数

- [交易函数](04-交易函数/013-mindoc-1h7k4iqb1grk4.md)
- [撤单](04-交易函数/025-mindoc-1h84elp5atr6o.md)
- [获取资金账户句柄](04-交易函数/043-mindoc-1h7k4k5tk6q64.md)
- [查询账户资产信息](04-交易函数/044-mindoc-1h84fvcjulrnc.md)
- [查询账户委托信息](04-交易函数/045-mindoc-1h7k4rp481gt4.md)
- [查询账户持仓信息](04-交易函数/046-mindoc-1h7k5ar9kc508.md)
- [获取历史成交queryhistorytrade | 通达信量化平台](04-交易函数/047-mindoc-1hlnun1348n54.md)
- [交易执行函数](04-交易函数/048-mindoc-1h7k5j4drr928.md)

## 05-TdxAiData

- [TdxAiData简介 | 通达信量化平台](05-TdxAiData/015-mindoc-1hjbgqpdhv114.md)
- [TdxAiData特有函数](05-TdxAiData/016-mindoc-1hjbidnohpjn4.md)
- [获取分笔行情gettickdata | 通达信量化平台](05-TdxAiData/026-mindoc-1hlsetap441rs.md)
- [获取指定日期分时数据 getminutedata | 通达信量化平台](05-TdxAiData/049-mindoc-1hlseu05n86ek.md)
- [获取指数成分股getzzgzstocklist | 通达信量化平台](05-TdxAiData/050-mindoc-1hlseuoousuvo.md)
- [获取集合竞价getcallauction | 通达信量化平台](05-TdxAiData/051-mindoc-1hlsevg97ftug.md)
- [批量获取集合竞价getcallauction_batch | 通达信量化平台](05-TdxAiData/052-mindoc-1hlsf010md5gg.md)
- [获取集合排名行情getdomainsorthq | 通达信量化平台](05-TdxAiData/053-mindoc-1hmdnqahdoeo0.md)
- [订阅实时行情 subscribe | 通达信量化平台](05-TdxAiData/054-mindoc-1hlsf0u13l0d4.md)
- [取消实时行情订阅 unsubscribe | 通达信量化平台](05-TdxAiData/055-mindoc-1hlsf1ifqpt8k.md)

## 06-Lambda平台

- [通达信Lambda量化平台专属函数](06-Lambda平台/017-mindoc-1hho7blr2j340.md)
- [常见问题](06-Lambda平台/027-mindoc-1hhodgohoq4j0.md)
- [策略生命周期](06-Lambda平台/056-mindoc-1hho9d546pbhc.md)
- [初始化函数 | 通达信量化平台](06-Lambda平台/057-mindoc-1hho9hh3kshvo.md)
- [开盘前函数 | 通达信量化平台](06-Lambda平台/058-mindoc-1hho9irt0nu3s.md)
- [Bar 回调函数 | 通达信量化平台](06-Lambda平台/059-mindoc-1hho9jic0qbu8.md)
- [收盘后函数 | 通达信量化平台](06-Lambda平台/060-mindoc-1hho9kq57km1c.md)
- [策略结束函数 | 通达信量化平台](06-Lambda平台/061-mindoc-1hho9lb0bd8ck.md)
- [委托回调函数 on_order | 通达信量化平台](06-Lambda平台/062-mindoc-1hib55063ha7o.md)
- [委托回调函数 on_trade | 通达信量化平台](06-Lambda平台/063-mindoc-1hib60s1q4hbg.md)
- [context回测对象](06-Lambda平台/064-mindoc-1hho9u0cbfe60.md)
- [set_benchmark | 通达信量化平台](06-Lambda平台/065-mindoc-1hhoa58m7i93s.md)
- [定时任务函数](06-Lambda平台/066-mindoc-1hibf3lo5qk7g.md)
- [特有行情类函数](06-Lambda平台/067-mindoc-1hi3n0rqkakt4.md)
- [专用数据函数](06-Lambda平台/068-mindoc-1hhoaleti08f0.md)
- [通达信量化平台](06-Lambda平台/069-mindoc-1hhobbn0nuedg.md)
- [账户查询函数](06-Lambda平台/070-mindoc-1hib696p7b7gc.md)
- [记录和日志](06-Lambda平台/071-mindoc-1hhoc0a5b29v0.md)
- [统一证券代码格式 | 通达信量化平台](06-Lambda平台/072-mindoc-1hibfhi6jhlqs.md)
- [完整示例](06-Lambda平台/073-mindoc-1hhodeob36hi4.md)
- [context上下文变量context.stock、context.universe的核心区别 | 通达信量化平台](06-Lambda平台/082-huicecontext.md)
- [取当前账户快照 get_portfolio | 通达信量化平台](06-Lambda平台/083-mindoc-1hib6pevs6fck.md)

## 07-常量与字典

- [常量枚举 | 通达信量化平台](07-常量与字典/019-Dict.md)

## 08-公式调用

- [调用通达信公式](08-公式调用/012-mindoc-1h3hrvkp4sc0g.md)
- [获取指定公式信息formula_get_info](08-公式调用/081-mindoc-1hce356csmgmo.md)

## 09-场景化示例

- [场景化示例](09-场景化示例/021-mindoc-1h1525ci3mnkc.md)
- [执行选股策略并加入客户端自定义板块](09-场景化示例/028-mindoc-1h15262vnafcc.md)
- [订阅行情涨幅突破实时预计](09-场景化示例/074-mindoc-1h1526nmnk5n4.md)
- [计算调仓信号并快速买卖](09-场景化示例/075-mindoc-1h1ep1rl20jv8.md)
- [VBT简单回测并输出图形](09-场景化示例/076-mindoc-1h62qo3mceppc.md)

## 10-HTTP与Skills

- [HTTP方式调用](10-HTTP与Skills/014-mindoc-1hdhbmi50d038.md)
- [提供3个Skills包供各智能体平台安装 | 通达信量化平台](10-HTTP与Skills/018-mindoc-1he2c52nvvdkg.md)

## 11-FAQ

- [Q：运行的python文件可不可以随便放，不一定在PYPlugins\user目录下？ | 通达信量化平台](11-FAQ/023-mindoc-tdxpy.md)

## 12-公众号文章

- [公众号文章例子 | 通达信量化平台](12-公众号文章/022-gzh0122inweixinwenz.md)
- [通达信TdxAiData：用批量竞价数据做"上提一字"竞价形态选股](12-公众号文章/029-20261008gzhwz.md)
- [通达信TQ策略介绍和应用示例](12-公众号文章/077-gzh20260122wzlz.md)
- [打通通达信量化任督二脉：公式与Python双向数据互通闭环](12-公众号文章/078-gzh20260302wzlz.md)
- [通达信TdxAiData：Python独立数据接口，无需启动客户端](12-公众号文章/079-gzh20260302wzlz-tdxaidata.md)

---

## 元数据

- 逐页源 URL 对照：`_meta/sources.md`
- 抓取清单（含 sha256／分类）：`_meta/crawl-manifest.json`

## 沿革

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v1.0 | 2026-10-12 | 建域：批次 1（TDX）83 页按语义分类落库（12 分类） |
