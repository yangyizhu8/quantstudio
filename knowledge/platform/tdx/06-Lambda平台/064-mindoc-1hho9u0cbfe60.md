---
title: "# context回测对象"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9u0cbfe60"
crawled_at: 2026-10-12T02:42:19+08:00
platform: TDX
category: 06-Lambda平台
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

# [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9u0cbfe60#context回测对象) context回测对象

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9u0cbfe60#上下文对象) 上下文对象

策略函数中的 `context` 表示当前回测上下文。

常用字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `context.current_dt` | `datetime` | 当前回测时间 |
| `context.previous_dt` | `datetime` | 上一个 bar 时间 |
| `context.frequency` | `str` | 当前回测频率 |
| `context.run_info` | `RunInfo` | 本次回测任务信息 |
| `context.portfolio` | `Portfolio` | 当前账户和持仓视图 |
| `context.universe` | `list[str]` | 当前股票池 |
| `context.stock` | `str` | 单股策略当前工作标的；`run_backtest` 未传 `symbols` 时，可在 `init(context)` 中写入作为股票池来源 |

用户可以在 `context` 上保存自定义变量：

```
def init(context):
    context.max_hold_count = 5
```

1
2

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9u0cbfe60#账户对象) 账户对象

`context.portfolio` 表示当前组合账户。

常用字段：

| 字段 | 说明 |
| --- | --- |
| `available_cash` | 可用现金 |
| `frozen_cash` | 冻结资金 |
| `market_value` | 持仓市值 |
| `total_value` | 账户总资产 |
| `portfolio_value` | `total_value` 的兼容别名 |
| `returns` | 当前收益率 |
| `pnl` | 累计盈亏 |
| `positions` | 当前持仓字典 |

示例：

```
def handle_bar(context, bar_dict):
    cash = context.portfolio.available_cash
    total_value = context.portfolio.total_value
    log.info("cash=%s total=%s", cash, total_value)
```

1
2
3
4

账户查询函数：

```
portfolio = get_portfolio()
cash = get_cash()
positions = get_positions()
position = get_position("000001.SZ")
```

1
2
3
4

说明：

- `get_portfolio(refresh=True)` 返回当前 `Portfolio` 快照；默认会先刷新资金和持仓。

- `get_cash(refresh=True)` 返回当前可用资金，等同于 `get_portfolio().available_cash`。

- `get_positions(symbols=None, order_book_id=None, refresh=True)` 返回 `dict[str, Position]`；不传过滤条件时返回全部持仓。

- `get_position(symbol=None, order_book_id=None, refresh=True)` 返回单只证券 `Position`；无持仓返回 `None`；未传代码会报错。

- 这些函数只读取当前 Python 运行期账户视图，不直接调用行情主站 RPC；回测阶段来自模拟账户，执行阶段来自 `account_snapshot`。

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9u0cbfe60#持仓对象) 持仓对象

`context.portfolio.positions` 是持仓字典。

示例：

```
pos = context.portfolio.positions.get("000001.SZ")
if pos and pos.amount > 0:
    log.info("持仓数量: %s", pos.amount)
```

1
2
3

`Position` 常用字段：

| 字段 | 说明 |
| --- | --- |
| `symbol` | 证券代码 |
| `amount` | 持仓总数量 |
| `available_amount` | 可卖数量，股票回测中会扣除当前交易日买入数量 |
| `closeable_amount` | `available_amount` 的兼容别名 |
| `cost_basis` | 持仓成本 |
| `avg_cost` | `cost_basis` 的兼容别名 |
| `last_price` | 最新价 |
| `market_value` | 持仓市值 |
| `pnl` | 持仓盈亏 |

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9u0cbfe60#order对象) Order对象

`Order` 常用字段：

| 字段 | 说明 |
| --- | --- |
| `order_id` | 订单编号 |
| `symbol` | 证券代码 |
| `amount` | 委托数量，买入为正，卖出为负 |
| `price` | 委托价格 |
| `filled` | 已成交数量 |
| `status` | 订单状态，例如 `filled`、`rejected`、`cancelled` |
| `side` | `buy` 或 `sell` |
| `datetime` | 下单时间 |
| `filled_dt` | 成交时间 |
| `avg_price` | 平均成交价 |
| `message` | 失败或状态说明 |
| `cancelable` | 是否可撤 |

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9u0cbfe60#trade对象) Trade对象

`Trade` 常用字段：

| 字段 | 说明 |
| --- | --- |
| `trade_id` | 成交编号 |
| `order_id` | 对应订单编号 |
| `symbol` | 证券代码 |
| `amount` | 成交数量 |
| `price` | 成交价格 |
| `side` | `buy` 或 `sell` |
| `datetime` | 成交时间 |
| `cost` | 交易成本 |

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9u0cbfe60#bar对象) Bar对象

`bar_dict` 保存当前时间点行情。

示例：

```
def handle_bar(context, bar_dict):
    bar = bar_dict["000001.SZ"]
    log.info("open=%s close=%s volume=%s", bar.open, bar.close, bar.volume)
```

1
2
3

`Bar` 常用字段：

| 字段 | 说明 |
| --- | --- |
| `symbol` | 证券代码 |
| `datetime` | bar 时间 |
| `open` | 开盘价 |
| `high` | 最高价 |
| `low` | 最低价 |
| `close` | 收盘价 |
| `volume` | 成交量 |
| `turnover` | 成交额 |
| `money` | `turnover` 的兼容别名 |
| `high_limit` | 涨停价，字段存在且大于 0 时会参与买入拒单判断 |
| `low_limit` | 跌停价，字段存在且大于 0 时会参与卖出拒单判断 |
| `prev_close` | 前收盘价 |
| `avg_price` | 均价 |
| `paused` | 是否停牌 |
| `is_paused` | `paused` 的兼容别名 |
| `is_st` | 是否 ST |
