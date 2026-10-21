---
title: "# 获取K线行情get_market_data"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1ctuhthaq5qmg/mindoc-1h10g60jt68sc.html"
crawled_at: 2026-10-12T02:42:33+08:00
platform: TDX
category: 03-数据获取
depth: 4
tags: [平台/TDX, 状态/已抓取]
---

# [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1ctuhthaq5qmg/mindoc-1h10g60jt68sc.html#获取k线行情get-market-data) 获取K线行情get_market_data

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1ctuhthaq5qmg/mindoc-1h10g60jt68sc.html#根据股票-获取历史行情) 根据股票，获取历史行情

注：如果是tqserver/tdxaidata从服务端取数据，如果取较长时间段的分钟周期数据，可能会消耗较多的积分。

```
get_market_data(field_list: List[str] = [],
				stock_list: List[str] = [],
				period: str = '',
				start_time: str = '',
				end_time: str = '',
				count: int = -1,
				dividend_type: Optional[str] = None,
				fill_data: bool = True) -> Dict:
```

1
2
3
4
5
6
7
8

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1ctuhthaq5qmg/mindoc-1h10g60jt68sc.html#输入参数) 输入参数

| 参数 | 是否必选 | 参数类型 | 参数说明 |
| --- | --- | --- | --- |
| field_list | N | List[str] | 字段筛选，传空则返回全部 |
| stock_list | Y | List[str] | 证券代码列表 |
| period | Y | str | 周期 |
| start_time | N | str | 起始时间 |
| end_time | N | str | 结束时间 |
| count | N | int | 返回数据个数（每只股票） |
| dividend_type | N | str | [复权类型 (opens new window)](https://help.tdx.com.cn/quant/docs/markdown/Dict.html#%E5%A4%8D%E6%9D%83%E7%B1%BB%E5%9E%8B)：none不复权、front前复权、back后复权 |
| fill_data | N | bool | 是否向后填充空缺数据，多只股票一起取k线时使用 |

count小于等于0或者count为空：
1、开始时间与结束时间之间的数据；
2、开始时间无值，从第一根k取到结束时间
3、结束时间无值，从开始时间取到最后一根
4、都没值取全部本地数据
count大于0时：
1、结束时间往前count个数据，起始时间无效
2、结束时间无时从最后一根k线往前取count个数据

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1ctuhthaq5qmg/mindoc-1h10g60jt68sc.html#返回数据) 返回数据

- 返回dict { field1 : value1, field2 : value2, ... }

- field1, field2, ... ：数据字段

- value1, value2, ... ：pd.DataFrame 数据集，index为stock_list，columns为time_list

- 各字段对应的DataFrame维度相同、索引相同

- 只有dividend_type传入为none时，会返回有效的前复权因子ForwardFactor

- 一次最多返回24000条数据，要获取完整分钟线需要多次分批获取

- 返回的复权因子为权息变动当天的复权因子，一直沿用到下次权息变动

- 开高低收单位为元，成交量单位为量的最小单位(股或张)，成交额单位为万元

| 数据 | 默认返回 | 数据类型 | 数据说明 |
| --- | --- | --- | --- |
| Date | Y | str | 日期 |
| Time | Y | str | 时间 |
| Open | Y | str | 开盘价 |
| High | Y | str | 最高价 |
| Low | Y | str | 最低价 |
| Close | Y | str | 收盘价 |
| Volume | Y | str | 成交量 |
| Amount | Y | str | 成交额 |
| ForwardFactor | Y | str | 前复权因子，当dividend_type=none时候返回有效值 |
| VolInStock | N | str | 持仓量 |

- 期货数据时Amount为0，非期货数据时VolInStock为0

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1ctuhthaq5qmg/mindoc-1h10g60jt68sc.html#接口使用) 接口使用

> 获取688318.SH从2025-12-20到今为止最新一条日K线的不复权数据

```
from tqcenter import tq
tq.initialize(__file__)
df = tq.get_market_data(
        field_list=[],
        stock_list=['688318.SH'],
        start_time='20251220',
        end_time='',
        count=1,
        dividend_type='none',
        period='1d',
        fill_data=True
    )
print(df)
```

1
2
3
4
5
6
7
8
9
10
11
12
13

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1ctuhthaq5qmg/mindoc-1h10g60jt68sc.html#数据样本) 数据样本

```
{'Close':             688318.SH
2026-09-18      80.52, 'Amount':             688318.SH
2026-09-18   47346.57, 'Volume':             688318.SH
2026-09-18  5962835.0, 'High':             688318.SH
2026-09-18      81.27, 'Open':             688318.SH
2026-09-18       78.6, 'Low':             688318.SH
2026-09-18       77.5, 'VolInStock':             688318.SH
2026-09-18        0.0, 'ForwardFactor':             688318.SH
2026-09-18        1.0}
```

1
2
3
4
5
6
7
8
9

← [检索证券信息get_match_stkinfo](https://help.tdx.com.cn/quant/docs/markdown/ctx.stock.md/mindoc-1hdguaia2v4bo.html) [获取快照数据get_market_snapshot*](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1ctuhthaq5qmg/mindoc-1h10iig4pb6e0.html) →
