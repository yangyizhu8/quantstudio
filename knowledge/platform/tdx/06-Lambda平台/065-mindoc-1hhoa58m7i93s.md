---
title: "set_benchmark | 通达信量化平台"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hhoa58m7i93s"
crawled_at: 2026-10-12T02:42:20+08:00
platform: TDX
category: 06-Lambda平台
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hhoa58m7i93s#set-benchmark) set_benchmark

```
set_benchmark(symbol)
```

1

设置回测基准。

回测结束后，`run_backtest` 会返回 `result.benchmark`、`result.strategy_returns`、`result.benchmark_returns`、`result.return_comparison` 和 `result.drawdowns`。其中 `benchmark_returns` 是基准代码在本次回测区间内的逐 bar 收益率序列；策略收益率仍以 `result.funds[*].returns` 为准，`result.strategy_returns` 是面向前端画图的策略逐 bar 收益率序列，`result.return_comparison` 会把策略收益率和基准收益率按时间对齐，`result.drawdowns` 是策略资金曲线的逐 bar 回撤序列。

示例：

```
set_benchmark("000300.SH")
```

1

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hhoa58m7i93s#set-commission) set_commission

```
set_commission(
    open_tax=0.0,
    close_tax=0.0005,
    open_commission=0.0003,
    close_commission=0.0003,
    min_commission=5.0,
)
```

1
2
3
4
5
6
7

设置交易费用。未调用本函数时，普通 A 股默认按买卖双边佣金万分之 3（单笔最低 5 元）、卖出印花税万分之 5、过户费万分之 0.1 双边收取计算。

参数：

| 参数 | 说明 |
| --- | --- |
| `open_tax` | 买入印花税 |
| `close_tax` | 卖出印花税 |
| `open_commission` | 买入佣金 |
| `close_commission` | 卖出佣金 |
| `min_commission` | 单笔最低佣金 |

`transferfee` 不作为 Python 参数暴露，普通 A 股固定使用成交金额的 `0.00001` 双边收取；ETF、基金、债券等非 A 股标的不收取该费用。

【迁移兼容】对象：

```
set_commission(PerShare(type="stock", cost=0.0003, min_trade_cost=5.0))
set_commission(PerTrade(type="stock", cost=5.0))
```

1
2

| 对象 | 说明 |
| --- | --- |
| `PerShare(type="stock", cost=0.0003, min_trade_cost=5.0)` | 按成交金额比例收取佣金。`type="stock"` 时卖出侧默认保留万分之 5 的 A 股印花税。 |
| `PerTrade(type="stock", cost=5.0)` | 按笔固定费用对象。当前底层成本模型按最低佣金近似表达固定每笔费用。 |

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hhoa58m7i93s#set-slippage) set_slippage

```
set_slippage(value)
```

1

【迁移兼容】对象：

| 写法 | 说明 |
| --- | --- |
| `set_slippage(0.001)` | 原有写法，表示单边比例滑点 0.1%。 |
| `set_slippage(PriceSlippage(0.02))` | 总买卖价差 2%，内部折算为买入上浮 1%、卖出下浮 1%。 |
| `set_slippage(FixedSlippage(2.0))` | 总固定价差 2 元，内部折算为买入加 1 元、卖出减 1 元。 |

设置滑点。

当前同步成交模式下已生效：

- 买入成交价 = 当前价格 * `(1 + value)`。

- 卖出成交价 = 当前价格 * `(1 - value)`。

- `value` 不能为负数。

示例：

```
set_slippage(0.001)
```

1

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hhoa58m7i93s#set-option) set_option

```
set_option(name, value)
```

1

设置通用回测选项。

当前正式支持的 `name` 如下：

| `name` | 可选值 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `limit_order_expire_bars` | `None` 或正整数 | `None` | 设置限价单未成交时最多保留的撮合 bar 数；`None` 表示不自动过期 |
| `future_data_policy` | `"truncate"` 或 `"raise"` | `"truncate"` | 策略查询时间超过当前策略时点时，`truncate` 截断到当前时点，`raise` 直接抛出异常；同时适用于行情和历史财务数据查询 |

示例：

```
set_option("limit_order_expire_bars", 3)
set_option("future_data_policy", "raise")
```

1
2

说明：

- `set_option` 当前不会校验 `name` 是否在白名单中，传入未支持的名称虽然可能不会立即报错，但不会产生对应功能效果。

- 市价单成交方式应使用 `set_execution("close")` 或 `set_execution("next_open")`；交易延迟应使用 `set_trade_delay(value)`，不要直接修改内部选项名。

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hhoa58m7i93s#set-execution) set_execution

```
set_execution(mode)
```

1

设置市价单回测成交机制。该函数只影响本地回测撮合，不调用 C++/行情主站 RPC 协议，因此无 ReqId。

参数：

| 参数 | 说明 |
| --- | --- |
| `mode` | 只能传字符串 `"close"` 或 `"next_open"` |

可选值：

| 值 | 说明 |
| --- | --- |
| `"close"` | 当前 bar 收盘价撮合 |
| `"next_open"` | 下一根 bar 开盘价撮合 |

使用要求：

- 建议在 `init(context)` 中调用。

- 当前 Lambda 默认成交机制为 `"next_open"`；用户不调用 `set_execution()` 时，市价单默认下一根 bar 开盘价成交。

- `set_execution("next_open")` 会同时设置市价单延迟 1 根 bar，并在下一根 bar 使用 `open` 撮合。

- `set_trade_delay(value)` 是低层延迟参数，只控制延迟 bar 数，不改变当前 execution 使用的成交价格字段。

示例：

```
def init(context):
    context.stock = "000001.SZ"
    set_execution("next_open")
```

1
2
3

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hhoa58m7i93s#其他设置函数) 其他设置函数

```
set_volume_limit(value)
set_volume_limit(daily=0.25, minute=0.5)
set_trade_delay(value)
set_holding_stocks(holdings)
enable_open_bar()
set_log_level(level, is_limit=True, filename=None)
```

1
2
3
4
5
6

【迁移兼容】补充：

- `set_volume_limit(daily=..., minute=...)` 表示日线和分钟线使用不同的单 bar 最大成交比例；日线回测使用 `daily`，分钟回测使用 `minute`。

- `set_holding_stocks({"600000.SH": 1000})` 设置初始持仓数量；也可写成 `{"600000.SH": {"amount": 1000, "price": 9.8}}` 指定初始成本价。当前初始持仓代码必须属于本次最终回测股票池，否则 `run_backtest` 会报错。

- `enable_open_bar()` 只调整日线回测中 `handle_bar`、定时任务和下单看到的事件时间为 09:30，不额外生成开盘 K 线。

说明：

- `set_volume_limit` 设置单个 bar 最大成交比例，取值范围为 `0` 到 `1`，`None` 表示关闭限制。

- `set_trade_delay` 设置下单延迟成交规则；该函数不改变当前 execution 使用的成交价格字段。

- `set_log_level` 是【迁移兼容】日志设置入口；`filename` 非空时额外输出文件日志；`is_limit` 为兼容参数，当前只保存设置值。

当前同步成交模式下：

- `set_volume_limit` 已按当前 bar 成交量限制最大成交数量。

- 成交量限制后的买卖数量仍按证券规则处理：主板和创业板为 100 股整数倍；科创板（`688`、`689`）为 200 股起且超过部分按 1 股递增；北交所股票为 100 股起且超过部分按 1 股递增；指数为 1 股递增；可转债为 10 张递增。`BJ899xxx` 属于北证指数，不按北交所股票处理。

- `set_trade_delay` 已对市价单生效，`value > 0` 时订单会进入延迟撮合队列。

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hhoa58m7i93s#set-run-params-和-get-run-params) set_run_params 和 get_run_params

```
set_run_params(params=None, **kwargs)
get_run_params(clear=False)
```

1
2

云平台运行参数入口。平台可在用户策略代码前注入 `set_run_params(...)`，runner 会在本次回测或执行阶段信号任务启动时同步到 `context.params` 和 `g.params`。该参数是一次性 pending 状态；运行中再次调用只更新当前上下文，入口校验失败或任务结束后都会清理 pending 状态。

参数：

| API | 参数 | 说明 |
| --- | --- | --- |
| `set_run_params` | `params` | 参数字典，例如 `{"fast": 5, "slow": 20}` |
| `set_run_params` | `**kwargs` | 额外参数，会覆盖 `params` 中的同名 key |
| `get_run_params` | `clear` | 是否读取后清空待运行参数；普通用户策略不需要传 |

示例：

```
# 平台注入
set_run_params(fast=5, slow=20)

def init(context):
    context.fast = context.params.fast
    context.slow = get_run_params()["slow"]
```

1
2
3
4
5
6
7

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hhoa58m7i93s#enable-profile-和-get-profile-stats) enable_profile 和 get_profile_stats

```
enable_profile(func_list=None)
get_profile_stats()
```

1
2

开启轻量函数耗时统计。该能力只统计 runner 调用的策略生命周期函数和调度函数，不做 C++ 内部性能分析。

参数和返回：

| API | 说明 |
| --- | --- |
| `enable_profile()` | 统计全部被 runner 调用的用户函数 |
| `enable_profile(["handle_bar", "trade"])` | 只统计指定函数名 |
| `get_profile_stats()` | 返回当前进程内统计快照 |
| `BacktestResult.profile_stats` | 回测结束后的统计结果 |
| `profile_stats.csv` | 指定 `output_dir` 时额外导出的统计文件 |

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hhoa58m7i93s#策略股票池声明) 策略股票池声明

股票池是策略定义的一部分，只能在 `init(context)` 中通过 `context.stock` 声明。`run_backtest(...)`、`run_signal(...)`、`SignalRunRequest` 和平台注入代码均不接受 `symbols / blocks / block_type / list_type`。

```
def init(context):
    context.stock = ["600000.SH", "000001.SZ"]
```

1
2

`context.stock` 接受一个证券代码字符串或代码序列。`init` 返回后，Lambda 会做代码规范化、去重和有效性校验，并把最终不可变的代码列表写入 `context.universe`。多股票策略必须遍历 `context.universe`，并以 `bar_dict.get(symbol)` 读取当前行情。

```
def handle_bar(context, bar_dict):
    for symbol in context.universe:
        bar = bar_dict.get(symbol)
        if bar is None:
            continue
        # 按 symbol 处理多股票策略逻辑。
```

1
2
3
4
5
6

板块、指数和全市场策略同样在 `init` 内解析为实际代码列表：

```
def init(context):
    # 前提：数据源已初始化；block_type=0 表示系统板块。
    context.stock = get_stock_list_in_sector("000300.SH", block_type=0)

    # 全市场策略需要显式取得实际代码，不能使用 context.stock = ["all"]。
    # context.stock = get_all_securities(types="stock").index.tolist()
```

1
2
3
4
5
6

边界如下：

1. `context.stock` 必须在 `init` 中设置为非空代码或代码序列；未设置、为空或包含无效代码时，本次回测或信号任务失败。

2. `init` 返回后的 `context.stock` 修改不会改变已定稿的 `context.universe`；需要动态选股时应维护策略自定义候选列表，而不是修改运行股票池。

3. 信号执行也遵循相同顺序：先执行 `init`，再依据 `context.stock` 定稿股票池，随后才读取最终股票池的 K 线并执行行情预检。

4. 单次信号请求首次执行后，Worker 把已定稿的代码保存到内部 `resolved_signal_job.json` 和 `SignalJob.symbols`；重试直接复用该列表，不会重新查询板块成员或全市场列表。

5. 不支持`set_run_universe`、`set_stock_pool`、`get_run_universe` 和 `clear_run_universe`。
