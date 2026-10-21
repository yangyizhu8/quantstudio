---
title: "通达信量化平台"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hhobbn0nuedg"
crawled_at: 2026-10-12T02:42:24+08:00
platform: TDX
category: 06-Lambda平台
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

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

系统自动交易信号：

`get_trade_signals()` 查询的是交易执行层自动生成的标准信号行。策略只要调用 `order / order_value / order_target / order_percent / order_target_value / order_target_percent / order_target_weights`，平台会在订单提交、拒绝、成交、部分成交、撤单和过期时自动记录，不要求用户在 `handle_bar / on_order / on_trade` 中手工 `record()`。

需要注意：回测阶段和执行阶段含义不同。

| 阶段 | 云端行为 | 用户侧含义 |
| --- | --- | --- |
| 回测阶段 | 云端按回测撮合规则模拟订单、成交、持仓和资金，并同步输出交易信号 | `trade_fill` 表示回测模拟成交 |
| 执行阶段 | 云端只运行策略并生成交易信号，不连接券商、不连接模拟交易系统、不确认成交 | 信号是下单建议，用户在客户端自行确认和下单 |

执行阶段的交易信号适合客户端订阅、轮询或从结果文件读取。客户端收到信号后，应结合真实账户资金、持仓、可卖数量、交易权限和本地风控重新确认，不能把云端信号直接等同于真实成交。

执行阶段最小入口：

```
result = run_signal(
    strategy="my_strategy.py",
    symbols=["600000.SH"],
    run_dt="2026-07-24",
    trigger="close",
    output_dir="signal_output",
    strategy_id="strategy-001",
    job_id="job-001",
    account_snapshot={
        "snapshot_time": "2026-07-24 14:55:00",
        "trading_day": "20260724",
        "total_value": 100000,
        "available_cash": 80000,
        "positions": {
            "600000.SH": {"amount": 1000, "available_amount": 1000}
        },
    },
    signal_validity_policy={
        "enabled": True,
        "quote_same_trading_day": True,
        "require_all_symbols_quote": True,
        "snapshot_same_trading_day": True,
        "snapshot_max_age_seconds": 300,
    },
)

print(result.generated_count, result.blocked_count)
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
14
15
16
17
18
19
20
21
22
23
24
25
26
27

`run_signal()` 会输出 `trade_signals.csv / signal_job.json / signal_result.json / signal_manifest.json / run.log`。内部先写入不可消费的暂存清单，校验通过后才原子发布正式 `signal_manifest.json`；平台只能读取正式清单，不能消费暂存文件。正式 manifest 包含明细和描述文件的 SHA-256；执行阶段不输出 `trades.csv`，也不会把云端信号写成真实成交。

执行阶段如果策略调用 `order_target / order_target_percent / order_target_weights`，建议传入 `account_snapshot`。有快照时平台会换算目标仓位差额；缺快照或缺行情时输出 `blocked` 信号，不会用默认初始资金伪造数量。

执行阶段策略模板：

```
def init(context):
    context.stock = context.universe[0]
    set_log_level("INFO")
    run_daily(open_signal, time_rule="open")
    run_daily(close_signal, time_rule="close")

def before_trading(context):
    # trigger="open" 时会调用，用于开盘前准备参数或刷新股票池。
    pass

def open_signal(context, bar_dict):
    # 云端只生成信号，不连接券商，也不确认成交。
    order(context.stock, 1000)

def handle_bar(context, bar_dict):
    # trigger="every_bar" 或 "HH:MM" 时也会调用，可在这里按当前 bar 判断信号。
    if bar_dict[context.stock].close > 10:
        order_target_percent(context.stock, 0.2)

def close_signal(context, bar_dict):
    order(context.stock, -500)

def after_trading(context):
    # trigger="close" 时会调用，用于收盘后记录摘要。
    pass
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
14
15
16
17
18
19
20
21
22
23
24
25
26
27
28
29
30
31

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hhobbn0nuedg#当前成交和拒单规则) 当前成交和拒单规则

当前版本已经包含以下基础交易和撮合规则：

- 买入数量按 A 股 100 股整数倍向下取整。

- 卖出数量不能超过 `Position.available_amount`。

- 当天买入的股票当天不可卖出，下一交易日可卖。

- 当前 `Bar.paused` 或 `Bar.is_paused` 为 `True` 时，买卖订单会被拒绝。

- 买入成交价达到或超过 `Bar.high_limit` 时，订单会被拒绝。

- 卖出成交价达到或低于 `Bar.low_limit` 时，订单会被拒绝。

- 如果行情数据没有提供 `paused / high_limit / low_limit` 字段，平台不会伪造这些字段，也不会因为字段缺失而拒单。

- 限价单支持跨 bar 延续、部分成交、撤单和按 `limit_order_expire_bars` 过期。

- `get_match_logs()` 可查看订单提交、等待、部分成交、成交、撤单和过期日志。
