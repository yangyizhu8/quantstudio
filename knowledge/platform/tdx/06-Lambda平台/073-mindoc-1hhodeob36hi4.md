---
title: "# 完整示例"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hhodeob36hi4.html"
crawled_at: 2026-10-12T02:42:27+08:00
platform: TDX
category: 06-Lambda平台
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

# [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hhodeob36hi4.html#完整示例) 完整示例

双均线策略：

```
def init(context):
    context.stock = "000001.SZ"
    set_benchmark("000300.SH")
    set_commission(open_commission=0.0003, close_commission=0.0003, close_tax=0.001)
    run_daily(trade, time_rule="every_bar")

def trade(context, bar_dict):
    hist = attribute_history(context.stock, 20, "1d", ["close"])
    if len(hist) < 20:
        return

    close = hist["close"]
    ma5 = close.tail(5).mean()
    ma20 = close.tail(20).mean()
    price = bar_dict[context.stock].close

    has_position = context.stock in context.portfolio.positions

    if ma5 > ma20 and not has_position:
        order_value(context.stock, context.portfolio.available_cash)
        log.info("buy %s price=%s", context.stock, price)
    elif ma5 < ma20 and has_position:
        order_target(context.stock, 0)
        log.info("sell %s price=%s", context.stock, price)

    record(price=price, ma5=ma5, ma20=ma20)
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

因子轮动策略：

```
FACTOR_IDS = ["momentum_20d", "volume_ratio"]
DEFAULT_INDEXES = ["000300.SH"]
TOP_N = 10
LOOKBACK = 320
REBALANCE_DAYS = 20
BENCHMARK = "000300.SH"

def _n(x):
    try:
        y = float(x)
        if y == y and abs(y) < 1e100:
            return y
    except Exception:
        pass
    return None

def _v(f, c):
    try:
        raw = f[c].tolist()
    except Exception:
        try:
            raw = list(f[c])
        except Exception:
            raw = []
    return [_n(x) for x in raw if _n(x) is not None]

def _ma(a, n):
    return sum(a[-n:]) / float(n) if len(a) >= n else None

def _ret(c, n):
    return c[-1] / c[-n - 1] - 1.0 if len(c) > n and c[-n - 1] else None

def _div(a, b):
    return None if a is None or b in (None, 0) else a / b

def _universe():
    r, s = [], set()
    for idx in DEFAULT_INDEXES:
        try:
            items = get_stock_list_in_sector(idx, block_type=0)
        except Exception:
            items = []
        for x in items or []:
            y = str(x)
            if y and y not in s:
                s.add(y)
                r.append(y)
    return r or ["000300.SH"]

def _calc(fid, c, v):
    if not c:
        return None
    if fid.startswith("momentum_") and fid.endswith("d"):
        return _ret(c, int(fid.split("_")[1][:-1]))
    if fid == "volume_ratio":
        return _div(v[-1] if v else None, _ma(v, 20))
    return None

def _rank(rows):
    for fid in FACTOR_IDS:
        vv = [x for x in rows if x.get(fid) is not None]
        vv.sort(key=lambda x: x.get(fid))
        n = len(vv)
        for i, x in enumerate(vv, 1):
            x[fid + "_rank"] = i / float(n)
    for x in rows:
        vals = [x.get(fid + "_rank") for fid in FACTOR_IDS if x.get(fid + "_rank") is not None]
        x["factor_score"] = sum(vals) / float(len(vals)) if vals else None

def init(context):
    context.rebalance_counter = 0
    context.current_targets = []
    context.stock = _universe()
    run_daily(check, time_rule="every_bar")   
    set_benchmark(BENCHMARK)

def check(context, bar_dict):
    context.rebalance_counter += 1
    if context.rebalance_counter % REBALANCE_DAYS != 1:
        return

    universe = list(context.stock) if context.stock else _universe()
    if not universe:
        return

    data = get_bars_batch(universe, count=LOOKBACK, fields=["close", "volume"], frequency="1d")
    rows = []
    for sym, frame in data.items():
        c = _v(frame, "close")
        v = _v(frame, "volume")
        row = {"symbol": sym, "close": c[-1] if c else None}
        for fid in FACTOR_IDS:
            row[fid] = _calc(fid, c, v)
        rows.append(row)

    _rank(rows)
    rows.sort(key=lambda x: x.get("factor_score") if x.get("factor_score") is not None else -1e100, reverse=True)
    selected = [x.get("symbol") for x in rows[:TOP_N] if x.get("symbol")]
    w = 1.0 / float(len(selected)) if selected else 0.0

    # ── 先卖：不在新列表里的旧持仓 ──
    for old in context.current_targets:
        if old not in selected:
            order_target_percent(old, 0)
            log.info(f"【卖出】{old}")

    # ── 再买：新列表 ──
    for sym in selected:
        order_target_percent(sym, w)
        log.info(f"【买入】{sym} 目标权重={w:.2%}")

    context.current_targets = selected

def after_trading(context):
    log.info("盘后运行结束")
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
71
72
73
74
75
76
77
78
79
80
81
82
83
84
85
86
87
88
89
90
91
92
93
94
95
96
97
98
99
100
101
102
103
104
105
106
107
108
109
110
111
112
113
114
115
116
117
118
119
120
121
122
123
124
125

← [代码转换函数](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hibfhi6jhlqs.html) [常见问题](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hhodgohoq4j0.html) →
