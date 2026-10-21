---
title: "初始化函数 | 通达信量化平台"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9hh3kshvo.html"
crawled_at: 2026-10-12T02:42:13+08:00
platform: TDX
category: 06-Lambda平台
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9hh3kshvo.html#初始化函数) 初始化函数

```
def init(context):
    pass
```

1
2

用途：

- 设置股票池。 `目前是init内必须设置context.stock否则策略运行报错`

- 设置基准。

- 设置手续费、滑点等回测参数。

- 注册定时运行函数。

- 初始化用户自定义变量。

示例：

```
def init(context):
    context.stock = ["000001.SZ", "600000.SH"]
    set_benchmark("000300.SH")
    set_commission(open_commission=0.0003, close_commission=0.0003, close_tax=0.001)
    run_daily(rebalance, time_rule="every_bar")
```

1
2
3
4
5

兼容 `initialize(context)`函数名

**延伸（用CALLED输出策略运行函数路径，判断函数执行没执行的情况）**

```
TEST_CASE = "T01"
STRATEGY_NAME = "smoke_strategy.py"
STOCKS = ["000001.SZ", "600000.SH"]
START_DATE = "20260701"
END_DATE = "20260710"
CALLED = []

def _emit(api, ok, value):
    preview = str(value)
    try:
        rows = len(value)
    except Exception:
        rows = 1 if value is not None else 0
    log.info("[API_COVERAGE] %s ok=%s preview=%s" % (api, ok, preview))
    record(test_case=TEST_CASE, strategy_name=STRATEGY_NAME, api=api, ok=ok, rows=rows, preview=str(value)[:300])

def init(context):
    CALLED.append("init")
    context.stock = STOCKS
    context.did_trade = False
    set_benchmark("000300.SH")
    log.info("T01 smoke init")
    _emit("init", 1, context.stock)

def handle_bar(context, bar_dict):
    CALLED.append("handle_bar")
    if context.did_trade:
        return
    try:
        now = get_datetime()
        current = get_current(context.stock)
        _emit("get_datetime", 1, now)
        _emit("get_current", 1, current)
        order_target_percent(STOCKS[0], 0.5)
        _emit("order_target_percent", 1, STOCKS[0])
    except Exception as exc:
        _emit("T01_exception", 0, exc)
    context.did_trade = True

def on_strategy_end(context):
    _emit("on_strategy_end", 1, "done")

# ========== 新版【别名】全部同时定义 ==========
def initialize(context):
    CALLED.append("initialize")
    context.stock = "000001.SZ"

def before_trading_start(context):
    CALLED.append("before_trading_start")

def handle_data(context, data):
    CALLED.append("handle_data")

def after_trading_end(context):
    CALLED.append("after_trading_end")

def on_strategy_end(context):
    # ✅旧主函数必须执行
    assert "init" in CALLED, "旧主函数init必须被调用"
    assert "handle_bar" in CALLED, "旧主函数handle_bar必须被调用"

    # ❗只有存在一一对应的别名才应当被屏蔽
    assert "initialize" not in CALLED, "共存：init存在，initialize别名不应执行"
    assert "handle_data" not in CALLED, "共存：handle_bar存在，handle_data别名不应执行"

    # before_trading_start / after_trading_end 没有旧主函数，允许执行，不做not in断言
    _emit("T03_priority_check", 1, CALLED)
    log.info("T03 priority PASS, CALLED=%s", CALLED)
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
32
33
34
35
36
37
38
39
40
41
42
43
44
45
46
47
48
49
50
51
52
53
54
55
56
57
58
59
60
61
62
63
64
65
66
67
68
69
70

← [取消实时行情订阅unsubscribe](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsf1ifqpt8k.html) [开盘前函数before_trading](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hho9irt0nu3s.html) →
