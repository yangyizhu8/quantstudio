---
title: "# HTTP方式调用"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hdhbmi50d038.html"
crawled_at: 2026-10-12T02:41:34+08:00
platform: TDX
category: 10-HTTP与Skills
depth: 1
tags: [平台/TDX, 状态/已抓取]
---

# [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hdhbmi50d038.html#http方式调用) HTTP方式调用

除了tqcenter的调用方式，本地客户端模式下，也可以直接通过以下方式调用。
POST http://127.0.0.1:17709/
使用时需要先开启支持TQ的通达信客户端。

method为TdxQuant中的函数方法，params为函数参数。

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hdhbmi50d038.html#例如) 例如：

```
{
    "id":1,
    "method": "get_market_data",
    "params": {
        "stock_list":["688318.SH"],
        "count":5,
        "dividend_type":"none",
        "period":"1d"
    }
}
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

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hdhbmi50d038.html#返回) 返回：

```
{
    "id": 1,
    "result": {
        "ErrorId": "0",
        "KlinePaged": true,
        "KlineTotal": {
            "688318.SH": 5
        },
        "Value": {
            "688318.SH": {
                "Amount": [
                    "61995.09",
                    "62455.38",
                    "59548.38",
                    "58073.54",
                    "38680.98"
                ],
                "Close": [
                    "122.45",
                    "120.32",
                    "117.84",
                    "112.00",
                    "82.70"
                ],
                "Date": [
                    "20260526",
                    "20260527",
                    "20260528",
                    "20260529",
                    "20260605"
                ],
                "ErrorId": "0",
                "ForwardFactor": [
                    "0.711862",
                    "0.711862",
                    "0.711862",
                    "0.711862",
                    "1.000000"
                ],
                "High": [
                    "124.58",
                    "125.61",
                    "120.32",
                    "120.20",
                    "85.24"
                ],
                "Low": [
                    "117.70",
                    "120.02",
                    "113.88",
                    "111.25",
                    "81.22"
                ],
                "Open": [
                    "119.71",
                    "121.28",
                    "120.30",
                    "118.19",
                    "84.00"
                ],
                "Time": [
                    "0",
                    "0",
                    "0",
                    "0",
                    "0"
                ],
                "VolInStock": [
                    "0",
                    "0",
                    "0",
                    "0",
                    "0"
                ],
                "Volume": [
                    "5086297.00",
                    "5081741.00",
                    "5104389.00",
                    "5017581.00",
                    "4627644.00"
                ]
            }
        },
        "has_more": false,
        "next_stock_page_index": 1,
        "stock_page_count": 1,
        "stock_page_index": 0,
        "stock_page_size": 100,
        "stock_total": 1
    }
}
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

← [撤单cancel_order_stock](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1h7k4iqb1grk4/mindoc-1h84elp5atr6o.html) [TdxAiData后台接口](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbgqpdhv114.html) →
