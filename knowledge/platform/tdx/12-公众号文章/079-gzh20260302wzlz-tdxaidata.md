---
title: "# 通达信TdxAiData：Python独立数据接口，无需启动客户端"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/gzh0122inweixinwenz/gzh20260302wzlz-tdxaidata.html"
crawled_at: 2026-10-12T02:42:33+08:00
platform: TDX
category: 12-公众号文章
depth: 3
tags: [平台/TDX, 状态/已抓取]
---

# [#](https://help.tdx.com.cn/quant/docs/markdown/gzh0122inweixinwenz/gzh20260302wzlz-tdxaidata.html#通达信tdxaidata-python独立数据接口-无需启动客户端) 通达信TdxAiData：Python独立数据接口，无需启动客户端

**此功能为付费功能，详细使用过程请查看今天的公众号文章** [通达信TdxAiData (opens new window)](https://mp.weixin.qq.com/s/DXChrQ79yGB1AnPLjywK8A)

> 📄 **获取Tick 分笔成交行情**

```
from tdxaidata import tqs

RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"

# 价格涨跌配色
def price_style(price, prev_price=None):
    if prev_price is None:
        return f"{price:.2f}"
    if price > prev_price:
        return f"{RED}{price:.2f}↑{RESET}"
    if price < prev_price:
        return f"{GREEN}{price:.2f}↓{RESET}"
    return f"{price:.2f}"

# 买卖方向标识：B外盘 S内盘
def bs_text(flag):
    return {"0": "B", "1": "S", "2": ""}.get(str(flag), str(flag))

# 打印分笔面板
def print_tick_panel(stock_code, date, startxh=0, wantnum=10):
    data = tqs.get_tick_data(
        stock_code=stock_code,
        date=date,
        startxh=startxh,
        wantnum=wantnum,
    )

    times = data.get("Time", [])
    prices = data.get("Price", [])
    vols = data.get("Volume", [])
    flags = data.get("BSFlag", [])

    prev_time = None
    prev_price = None
    seq_in_second = 0

    for t, p, v, f in zip(times, prices, vols, flags):
        hhmmss = f"{t[:2]}:{t[2:4]}:{t[4:6]}"
        price = float(p)
        side = bs_text(f)
        vol_side = f"{v}{side}"

        if t != prev_time:
            seq_in_second = 1
            print(f"{hhmmss:<9} {price_style(price, prev_price):<12} {vol_side:>4}")
        else:
            seq_in_second += 1
            print(f"{seq_in_second:>2}        {price_style(price, prev_price):<12} {vol_side:>4}")

        prev_time = t
        prev_price = price

# 直接跑：打印 688318 最近10笔
print_tick_panel("688318.SH", "2026-09-08", startxh=0, wantnum=10)
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

> 📄 **取指数成份股前10获取指定日期的分时数据**

```
from tdxaidata import tqs

# 获取沪深300成份股前101只
codes = tqs.get_zzgz_stocklist("000300.SH", 0)[:10]

# 逐只获取分时数据
for code in codes:
    data = tqs.get_minute_data(
        stock_code=code,
        date="2026-08-27",
        field_list=["Time", "Price", "Average", "Volume"],
    )
    print(f"{code}: {data}")
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

> 📄 **取指数成份股前10订阅实时行情数据**

```
from tdxaidata import tqs
import time

N = 10            # 订阅数量
RUN_SECONDS = 300   # 持续接收秒数
INTERVAL_SECONDS = 60 # 每次订阅间隔

# 获取订阅标的（沪深300前100只）
codes = tqs.get_zzgz_stocklist("000300.SH", 0)[:N]

# 行情回调：收到数据自动触发
def on_quote(data: str) -> None:
    print("收到实时行情：")
    print(data)

started = time.monotonic()
try:
    while time.monotonic() - started < RUN_SECONDS:
        print("开始订阅：", time.strftime("%H:%M:%S"))
        tqs.subscribe(codes, callback=on_quote)
        time.sleep(INTERVAL_SECONDS)
        tqs.unsubscribe(codes)
        print("取消订阅：", time.strftime("%H:%M:%S"))
finally:
    tqs.unsubscribe(codes)
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

> 📄 **开盘前集合竞价期间获取快照模拟单股集合竞价全过程**

```
"""单票集合竞价动态展示、原始数据保存和离线回放。"""
from __future__ import annotations

import argparse

import json

import os

import time

from datetime import datetime

from pathlib import Path

from typing import Any

  

from tdxaidata import tqs

  
  

DATA_DIR = Path(__file__).with_name("auction_data_single")

RED = "\033[31m"

GREEN = "\033[32m"

YELLOW = "\033[33m"

RESET = "\033[0m"

  
  

def number(value: Any) -> float:

    try:

        return float(value)

    except (TypeError, ValueError):

        return 0.0

  
  

def integer(value: Any) -> int:

    try:

        return int(float(value))

    except (TypeError, ValueError):

        return 0

  
  

def at(values: list[Any], index: int, convert) -> Any:

    return convert(values[index]) if index < len(values) else convert(0)

  
  

def format_time(value: Any) -> str:

    text = str(value or "")

    return f"{text[:2]}:{text[2:4]}:{text[4:6]}" if len(text) >= 6 else "--:--:--"

  
  

def in_auction_window(value: Any) -> bool:

    text = str(value or "")

    if len(text) < 6 or not text[:6].isdigit():

        return False

    hhmmss = int(text[:6])

    return 91500 <= hhmmss <= 92500

  
  

def matched_price_from_first_level(

    refresh_time: Any,

    buy_prices: list[float],

    sell_prices: list[float],

) -> float:

    """只在 09:15-09:25 且买一价等于卖一价时认定匹配价。"""

    if not in_auction_window(refresh_time):

        return 0.0

    if not buy_prices or not sell_prices:

        return 0.0

    buy1, sell1 = buy_prices[0], sell_prices[0]

    if buy1 > 0 and sell1 > 0 and abs(buy1 - sell1) < 0.000001:

        return buy1

    return 0.0

  
  

def normalize(raw: dict[str, Any]) -> dict[str, Any]:

    buy_prices = [number(x) for x in raw.get("Buyp", [])]

    buy_volumes = [integer(x) for x in raw.get("Buyv", [])]

    sell_prices = [number(x) for x in raw.get("Sellp", [])]

    sell_volumes = [integer(x) for x in raw.get("Sellv", [])]

    refresh_time = raw.get("RefreshTime")

    last_close = number(raw.get("LastClose"))

    matched_price = matched_price_from_first_level(

        refresh_time,

        buy_prices,

        sell_prices,

    )

  

    return {

        "time": format_time(refresh_time),

        "last_close": last_close,

        "matched_price": matched_price,

        "change_pct": (

            (matched_price / last_close - 1) * 100

            if matched_price > 0 and last_close

            else 0.0

        ),

        "matched_volume": integer(raw.get("NowVol")),

        "buy_prices": buy_prices,

        "buy_volumes": buy_volumes,

        "sell_prices": sell_prices,

        "sell_volumes": sell_volumes,

        "buy_total": sum(buy_volumes),

        "sell_total": sum(sell_volumes),

        "volume": integer(raw.get("Volume")),

        "amount": number(raw.get("Amount")),

        "average": number(raw.get("Average")),

        "open": number(raw.get("Open")),

        "high": number(raw.get("Max")),

        "low": number(raw.get("Min")),

    }

  
  

def price_text(price: float, last_close: float) -> str:

    if price <= 0:

        return "--"

    text = f"{price:.2f}"

    if price > last_close:

        return f"{RED}{text}{RESET}"

    if price < last_close:

        return f"{GREEN}{text}{RESET}"

    return f"{YELLOW}{text}{RESET}"

  
  

def shape(history: list[dict[str, Any]]) -> str:

    prices = [x["matched_price"] for x in history if x["matched_price"] > 0]

    if len(prices) < 3:

        return "数据不足"

    first, middle, last = prices[0], prices[len(prices) // 2], prices[-1]

    threshold = max(first * 0.0005, 0.01)

    if max(prices) - min(prices) <= threshold:

        return "横盘"

    if first < middle and last < middle:

        return "冲高回落"

    if first > middle and last > middle:

        return "探底回升"

    if last - first > threshold:

        return "逐步走强"

    if last - first < -threshold:

        return "逐步走弱"

    return "震荡"

  
  

def render(code: str, values: dict[str, Any], history: list[dict[str, Any]]) -> None:

    os.system("cls" if os.name == "nt" else "clear")

    bp, bv = values["buy_prices"], values["buy_volumes"]

    sp, sv = values["sell_prices"], values["sell_volumes"]

    imbalance = values["buy_total"] - values["sell_total"]

  

    print(f"{code}  集合竞价动态展示  本机时间 {datetime.now():%H:%M:%S}")

    print("-" * 78)

    print(

        f"数据时间 {values['time']}  昨收 {values['last_close']:.2f}  "

        f"竞价匹配价 {price_text(values['matched_price'], values['last_close'])}  "

        f"涨跌幅 "

        f"{values['change_pct']:+.2f}%"

        if values["matched_price"] > 0

        else f"涨跌幅 --"

    )

    print(

        f"匹配量 {values['matched_volume']}  成交量 {values['volume']}  "

        f"成交额 {values['amount'] / 10000:.2f}万  委托差 {imbalance:+d}"

    )

    print("-" * 78)

    print("档位       卖价       卖量       买价       买量")

    for index in range(5):

        print(

            f"{index + 1:<4} "

            f"{at(sp, index, number):>10.2f} {at(sv, index, integer):>10} "

            f"{at(bp, index, number):>10.2f} {at(bv, index, integer):>10}"

        )

    print("-" * 78)

    print(

        f"匹配价={values['matched_price']:.2f}  "

        f"买一={at(bp, 0, number):.2f}/{at(bv, 0, integer)}  "

        f"卖一={at(sp, 0, number):.2f}/{at(sv, 0, integer)}"

    )

    print(

        f"未匹配参考：买二量={at(bv, 1, integer)}  "

        f"卖二量={at(sv, 1, integer)}  当前形态={shape(history)}"

    )

    print("原始快照正在持续保存，按 Ctrl+C 停止。")

  
  

def save(path: Path, code: str, raw: dict[str, Any], values: dict[str, Any]) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)

    record = {

        "fetched_at": datetime.now().isoformat(timespec="milliseconds"),

        "stock_code": code,

        "raw": raw,

        "normalized": values,

    }

    with path.open("a", encoding="utf-8") as file:

        file.write(json.dumps(record, ensure_ascii=False) + "\n")

  
  

def run_live(code: str, interval: float) -> None:

    path = DATA_DIR / f"{code.replace('.', '_')}_{datetime.now():%Y%m%d}.jsonl"

    history: list[dict[str, Any]] = []

    last_key = None

  

    while True:

        try:

            raw = tqs.get_market_snapshot(stock_code=code, field_list=[])

            raw = raw if isinstance(raw, dict) else {}

            values = normalize(raw)

            save(path, code, raw, values)

            key = (

                values["time"],

                values["matched_price"],

                values["matched_volume"],

                values["buy_total"],

                values["sell_total"],

            )

            if values["matched_price"] > 0 and key != last_key:

                history.append(values)

                history = history[-600:]

                last_key = key

            render(code, values, history)

        except KeyboardInterrupt:

            print(f"\n已停止。数据文件：{path}")

            return

        except Exception as exc:

            print(f"获取失败：{exc}，下一轮继续重试。")

        time.sleep(interval)

  
  

def run_replay(path: Path, interval: float) -> None:

    history: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as file:

        for line in file:

            try:

                record = json.loads(line)

                values = record["normalized"]

            except (json.JSONDecodeError, KeyError):

                continue

            history.append(values)

            render(record.get("stock_code", "UNKNOWN"), values, history)

            time.sleep(interval)

  
  

def main() -> None:

    parser = argparse.ArgumentParser()

    parser.add_argument("--stock", default="688318.SH")

    parser.add_argument("--interval", type=float, default=1.0)

    parser.add_argument("--replay", type=Path)

    args = parser.parse_args()

    if args.replay:

        run_replay(args.replay, args.interval)

    else:

        run_live(args.stock, args.interval)

  
  

if __name__ == "__main__":

    main()
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
126
127
128
129
130
131
132
133
134
135
136
137
138
139
140
141
142
143
144
145
146
147
148
149
150
151
152
153
154
155
156
157
158
159
160
161
162
163
164
165
166
167
168
169
170
171
172
173
174
175
176
177
178
179
180
181
182
183
184
185
186
187
188
189
190
191
192
193
194
195
196
197
198
199
200
201
202
203
204
205
206
207
208
209
210
211
212
213
214
215
216
217
218
219
220
221
222
223
224
225
226
227
228
229
230
231
232
233
234
235
236
237
238
239
240
241
242
243
244
245
246
247
248
249
250
251
252
253
254
255
256
257
258
259
260
261
262
263
264
265
266
267
268
269
270
271
272
273
274
275
276
277
278
279
280
281
282
283
284
285
286
287
288
289
290
291
292
293
294
295
296
297
298
299
300
301
302
303
304
305
306
307
308
309
310
311
312
313
314
315
316
317
318
319
320
321
322
323
324
325
326
327
328
329
330
331
332
333
334
335
336
337
338
339
340
341
342
343
344
345
346
347
348
349
350
351
352
353
354
355
356
357
358
359
360
361
362
363
364
365
366
367
368
369
370
371
372
373
374
375
376
377
378
379
380
381
382
383
384
385
386
387
388
389
390
391
392
393
394
395
396
397
398
399
400
401
402
403
404
405
406
407
408
409
410
411
412
413
414
415
416
417
418
419
420
421
422
423
424
425
426
427
428
429
430
431
432
433
434
435
436
437
438
439
440
441
442
443
444
445
446
447
448
449
450
451
452
453
454
455
456
457
458
459
460
461
462
463
464
465
466
467
468
469
470
471
472
473
474
475
476

> 📄 **分时形态选股策略---一次运行选股模式**

```
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""早盘拉高回调布尔形态纯分时一次性选股。

仅依赖：

    from tdxaidata import tqs

程序启动后只扫描一次。历史日期直接扫描；查询当天时，必须已经到达
设置的选股开始时间。策略统一使用 09:30-15:00 的分钟数据。

本文件只调用 tqs.get_minute_data，不调用 get_tick_data，也不使用逐笔
成交重建或拼接历史分时。接口返回到哪一分钟，策略就评估到哪一分钟。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from tdxaidata import tqs

ANALYSIS_START = "093000"
ANALYSIS_END = "150000"
MINUTE_FIELDS = ["Time", "Price", "Average", "Volume"]

# ============================ 用户可编辑参数 ============================
# 直接运行本文件时，可以修改下面的默认值。
# 如果命令行明确传入同名参数，则以命令行参数为准。

# 查询日期：支持单个或多个日期，多个日期使用英文逗号分隔。
# "today" 表示当天；历史日期可填写 "YYYY-MM-DD" 或 "YYYYMMDD"。
# 示例：DEFAULT_QUERY_DATE = "2024-09-20,2024-09-23,2024-09-24"
# DEFAULT_QUERY_DATE = "today"
DEFAULT_QUERY_DATE = "20260909,20260908,20260907"

# 手动股票代码：多个代码使用英文逗号分隔；留空时按 DEFAULT_MARKET 获取股票池。
# DEFAULT_STOCKS = "300925.SZ,000965.SZ"
DEFAULT_STOCKS =""
# 如果全市场轮询 把这个DEFAULT_STOCKS =""留空就可

# 通达信市场代码：仅在 DEFAULT_STOCKS 为空时使用；"5" 表示所有 A 股。
DEFAULT_MARKET = "5"

# 最大扫描股票数：0 表示不限制，扫描股票池中的全部股票。
DEFAULT_MAX_STOCKS = 0

# 早盘拉高回调形态参数：
# PRE_END：早盘统计截止时间，用于计算早盘阶段是否持续位于均价线上方。
# SELECT_START：开始检查选股信号的时间，首次满足全部条件的分钟记为 B 点。
# PRE_MAX_ABOVE_PCT：早盘价格相对均价线的最大上偏离阈值，实际值必须严格大于它。
# MAX_BELOW_PCT：跌破均价线后允许的最大下偏离百分比。
# ALLOW_ABOVE_COUNT：首次跌破均价线后，最多允许重新回到均价线上方的分钟数量。
DEFAULT_PRE_END = "1400"
DEFAULT_SELECT_START = "1445"
DEFAULT_PRE_MAX_ABOVE_PCT = 3.5
DEFAULT_MAX_BELOW_PCT = 3.0
DEFAULT_ALLOW_ABOVE_COUNT = 10

# 输出控制：
# SHOW_REJECTED：是否在扫描完成后把全部未命中股票及原因打印到控制台。
# OUTPUT：JSON 文件路径；留空时自动保存到当前项目的 reports 目录。
# LOG：完整日志路径；留空时自动保存到当前项目的 reports 目录。
# PROGRESS_EVERY：控制台每扫描多少只股票显示一次进度。
DEFAULT_SHOW_REJECTED = False
DEFAULT_OUTPUT = ""
DEFAULT_LOG = ""
DEFAULT_PROGRESS_EVERY = 10

# ========================================================================

@dataclass
class MinuteRow:
    time: str
    price: float
    average: float
    volume: float

@dataclass
class StrategyParams:
    query_date: str
    pre_end: str = "1400"
    select_start: str = "1445"
    pre_max_above_pct: float = 3.5
    max_below_pct: float = 3.0
    allow_above_count: int = 10

@dataclass
class MatchResult:
    code: str
    query_date: str
    matched: bool
    signal_time: str = ""
    signal_price: float = 0.0
    reason: str = ""
    rows_count: int = 0
    first_time: str = ""
    last_time: str = ""
    pre_max_above_pct: float = 0.0
    first_break_time: str = ""
    above_after_break: int = 0
    min_below_pct: float = 0.0
    signal_deviation_pct: float = 0.0

def setup_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("tdx_bool_shape_selector_minute_only")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger

def flush_logger(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        handler.flush()

def close_logger(logger: logging.Logger) -> None:
    for handler in logger.handlers[:]:
        handler.flush()
        handler.close()
        logger.removeHandler(handler)

def write_json_snapshot(
    output_path: Path,
    params: StrategyParams,
    query_dates: list[str],
    pool_count: int,
    processed: int,
    matches: list[MatchResult],
    rejected: list[MatchResult],
    errors: list[dict[str, str]],
    status: str,
    started_at: str,
    completed_at: str = "",
) -> None:
    params_payload = asdict(params)
    params_payload.pop("query_date", None)
    date_summaries = []
    for query_date in query_dates:
        date_matches = sum(item.query_date == query_date for item in matches)
        date_rejected = sum(item.query_date == query_date for item in rejected)
        date_errors = sum(
            item.get("query_date") == query_date for item in errors
        )
        date_summaries.append(
            {
                "query_date": query_date,
                "pool_count": pool_count,
                "processed_count": date_matches + date_rejected + date_errors,
                "match_count": date_matches,
                "rejected_count": date_rejected,
                "error_count": date_errors,
            }
        )
    payload = {
        "strategy": "早盘拉高回调布尔形态",
        "run_mode": "一次性扫描",
        "data_source": "tqs.get_minute_data（纯分时，不使用逐笔重建）",
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "params": params_payload,
        "query_dates": query_dates,
        "date_count": len(query_dates),
        "pool_count": pool_count,
        "total_tasks": pool_count * len(query_dates),
        "processed_count": processed,
        "match_count": len(matches),
        "rejected_count": len(rejected),
        "error_count": len(errors),
        "matches": [asdict(item) for item in matches],
        "rejected": [asdict(item) for item in rejected],
        "errors": errors,
        "date_summaries": date_summaries,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def number(value: Any, default: float | None = None) -> float | None:
    try:
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

def time_text(value: Any) -> str:
    return str(value or "").strip().replace(":", "").zfill(6)[-6:]

def parameter_time(value: str) -> str:
    text = str(value or "").strip().replace(":", "")
    if not text.isdigit() or len(text) not in (4, 6):
        raise ValueError(f"时间参数格式错误：{value}，应为 HHMM 或 HHMMSS")
    result = text + "00" if len(text) == 4 else text
    datetime.strptime(result, "%H%M%S")
    return result

def parse_date(value: str) -> date:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"日期格式错误：{value}，应为 YYYY-MM-DD 或 YYYYMMDD")

def split_codes(value: str | Iterable[str]) -> list[str]:
    if isinstance(value, str):
        values = (
            value.replace("，", ",")
            .replace("；", ",")
            .replace(";", ",")
            .replace("\n", ",")
            .split(",")
        )
    else:
        values = list(value or [])
    result: list[str] = []
    for item in values:
        code = str(item or "").strip().upper()
        if code and code not in result:
            result.append(code)
    return result

def split_dates(value: str | Iterable[str]) -> list[str]:
    if isinstance(value, str):
        values = (
            value.replace("，", ",")
            .replace("；", ",")
            .replace(";", ",")
            .replace("\n", ",")
            .split(",")
        )
    else:
        values = list(value or [])

    result: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if not text:
            continue
        query_date = (
            date.today().isoformat()
            if text.lower() == "today"
            else parse_date(text).isoformat()
        )
        if query_date not in result:
            result.append(query_date)
    if not result:
        raise ValueError("查询日期不能为空")
    return result

def normalize_minute_rows(raw: Any) -> list[MinuteRow]:
    """转换分时数据，并按最近一次真实成交价填充零成交分钟。"""
    if not isinstance(raw, dict):
        return []
    fields = {name: raw.get(name, []) or [] for name in MINUTE_FIELDS}
    lengths = [len(values) for values in fields.values() if hasattr(values, "__len__")]
    if not lengths:
        return []

    rows: list[MinuteRow] = []
    previous_trade_price: float | None = None
    previous_average: float | None = None
    for index in range(min(lengths)):
        current_time = time_text(fields["Time"][index])
        if current_time < ANALYSIS_START or current_time > ANALYSIS_END:
            continue

        raw_price = number(fields["Price"][index])
        average = number(fields["Average"][index])
        volume = number(fields["Volume"][index], 0.0) or 0.0

        price = previous_trade_price if volume <= 0 and previous_trade_price is not None else raw_price
        if price is None or price <= 0:
            price = previous_trade_price or (
                average if average is not None and average > 0 else None
            )
        if price is None or price <= 0:
            continue
        if average is None or average <= 0:
            average = previous_average or price

        rows.append(MinuteRow(current_time, price, average, volume))
        if volume > 0:
            previous_trade_price = price
        previous_average = average
    return rows

def fetch_minute_rows(code: str, query_date: str) -> list[MinuteRow]:
    """只通过 tqs.get_minute_data 获取策略所需分钟数据。"""
    raw = tqs.get_minute_data(code, query_date, MINUTE_FIELDS)
    return normalize_minute_rows(raw)

def evaluate_shape(
    code: str,
    rows: list[MinuteRow],
    params: StrategyParams,
) -> MatchResult:
    """判断早盘拉高回调布尔形态，返回首次满足条件的 B 点。"""
    pre_end = parameter_time(params.pre_end)
    select_start = parameter_time(params.select_start)
    result = MatchResult(
        code=code,
        query_date=params.query_date,
        matched=False,
        rows_count=len(rows),
        first_time=rows[0].time if rows else "",
        last_time=rows[-1].time if rows else "",
    )
    if not rows:
        result.reason = "没有可用分时数据"
        return result
    if rows[-1].time < select_start:
        result.reason = f"分时只到 {rows[-1].time}，尚未到选股开始时间 {select_start}"
        return result

    pre_indices = [index for index, row in enumerate(rows) if row.time <= pre_end]
    if not pre_indices:
        result.reason = "早盘截止时间之前没有分时数据"
        return result
    if not all(rows[index].price >= rows[index].average for index in pre_indices):
        result.reason = "早盘并非全程位于均价线上"
        return result

    pre_deviations = [
        (rows[index].price / rows[index].average - 1) * 100
        for index in pre_indices
        if rows[index].average
    ]
    pre_max_above = max(pre_deviations, default=0.0)
    result.pre_max_above_pct = round(pre_max_above, 3)
    if pre_max_above <= params.pre_max_above_pct:
        result.reason = (
            f"早盘最大上偏离 {pre_max_above:.3f}% "
            f"未超过要求 {params.pre_max_above_pct:.3f}%"
        )
        return result

    pre_end_index = pre_indices[-1]
    break_index = next(
        (
            index
            for index in range(pre_end_index + 1, len(rows))
            if rows[index].time > pre_end
            and rows[index].price <= rows[index].average
        ),
        None,
    )
    if break_index is None:
        result.reason = "早盘截止后没有触及或跌破均价线"
        return result
    result.first_break_time = rows[break_index].time

    start_candidates = [
        index for index, row in enumerate(rows) if row.time <= select_start
    ]
    if not start_candidates:
        result.reason = "选股开始时间之前没有分时数据"
        return result
    start_index = start_candidates[-1]
    if start_index < break_index:
        result.reason = "到选股开始时间尚未触及或跌破均价线"
        return result

    allowed_above = max(0, int(params.allow_above_count))
    for signal_index in range(start_index, len(rows)):
        row = rows[signal_index]
        if row.time < select_start or row.price > row.average:
            continue

        above_after_break = sum(
            1
            for index in range(break_index, signal_index + 1)
            if rows[index].price > rows[index].average
        )
        if above_after_break > allowed_above:
            continue

        deviations = [
            (rows[index].price / rows[index].average - 1) * 100
            for index in range(break_index, signal_index + 1)
            if rows[index].average
        ]
        min_below = min(deviations, default=0.0)
        current_deviation = (
            (row.price / row.average - 1) * 100 if row.average else 0.0
        )
        if (
            min_below < -abs(params.max_below_pct)
            or current_deviation < -abs(params.max_below_pct)
        ):
            continue

        result.matched = True
        result.signal_time = row.time
        result.signal_price = row.price
        result.reason = "首次满足固定布尔形态"
        result.above_after_break = above_after_break
        result.min_below_pct = round(min_below, 3)
        result.signal_deviation_pct = round(current_deviation, 3)
        return result

    result.reason = "选股开始时间之后没有出现满足全部条件的分钟"
    return result

def stock_pool(stocks: str, market: str, max_stocks: int) -> list[str]:
    codes = split_codes(stocks)
    if not codes:
        value = tqs.get_stock_list(str(market or "5"), 0)
        codes = split_codes(value) if isinstance(value, (str, list, tuple, set)) else []
    if not codes:
        raise RuntimeError("股票池为空，请检查 --stocks 或 --market 参数")
    limit = max(0, int(max_stocks))
    return codes if limit == 0 else codes[:limit]

def ensure_run_time(params: StrategyParams) -> None:
    query_day = parse_date(params.query_date)
    today = date.today()
    if query_day > today:
        raise RuntimeError(f"查询日期 {query_day} 晚于今天 {today}")
    if query_day == today:
        select_start = parameter_time(params.select_start)
        now = datetime.now().strftime("%H%M%S")
        if now < select_start:
            raise RuntimeError(
                f"当前时间 {now[:2]}:{now[2:4]} 尚未到选股开始时间 "
                f"{select_start[:2]}:{select_start[2:4]}，本次未扫描"
            )

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只用 tqs.get_minute_data 一次性运行早盘拉高回调布尔形态选股"
    )
    parser.add_argument(
        "--date",
        default=(
            date.today().isoformat()
            if DEFAULT_QUERY_DATE.strip().lower() == "today"
            else DEFAULT_QUERY_DATE
        ),
        help="查询日期；多个日期用逗号分隔，支持 YYYY-MM-DD、YYYYMMDD 或 today",
    )
    parser.add_argument(
        "--stocks",
        default=DEFAULT_STOCKS,
        help="手动股票代码，多个用逗号分隔；非空时优先于 --market",
    )
    parser.add_argument(
        "--market",
        default=DEFAULT_MARKET,
        help="tqs.get_stock_list 市场代码，默认 5（所有 A 股）",
    )
    parser.add_argument(
        "--max-stocks",
        type=int,
        default=DEFAULT_MAX_STOCKS,
        help="最大扫描数量，0 表示不截断",
    )
    parser.add_argument("--pre-end", default=DEFAULT_PRE_END, help="早盘截止时间，默认 1400")
    parser.add_argument(
        "--select-start",
        default=DEFAULT_SELECT_START,
        help="选股开始时间，默认 1445",
    )
    parser.add_argument(
        "--pre-max-above",
        type=float,
        default=DEFAULT_PRE_MAX_ABOVE_PCT,
        help="早盘最大上偏离阈值百分比，要求实际值严格大于它",
    )
    parser.add_argument(
        "--max-below",
        type=float,
        default=DEFAULT_MAX_BELOW_PCT,
        help="触及均价线后的最大允许下偏离百分比",
    )
    parser.add_argument(
        "--allow-above",
        type=int,
        default=DEFAULT_ALLOW_ABOVE_COUNT,
        help="触及均价线后最多允许回到均价线上方的分钟根数",
    )
    parser.add_argument(
        "--show-rejected",
        action="store_true",
        default=DEFAULT_SHOW_REJECTED,
        help="同时打印未命中股票及原因",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help="JSON 输出文件路径；不传时自动保存到 reports",
    )
    parser.add_argument(
        "--log",
        default=DEFAULT_LOG,
        help="完整日志文件路径；不传时自动保存到 reports",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        help="控制台进度输出间隔，默认每 10 只",
    )
    return parser

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = datetime.now()
    started_at = started.isoformat(timespec="seconds")
    run_stamp = started.strftime("%Y%m%d_%H%M%S")
    raw_date_tag = str(args.date or "unknown")
    safe_date = "".join(
        character if character.isalnum() else "_"
        for character in raw_date_tag
    ).strip("_") or "unknown"
    reports_dir = Path(__file__).resolve().parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else reports_dir / f"bool_shape_minute_only_{safe_date}_{run_stamp}.json"
    )
    log_path = (
        Path(args.log).expanduser().resolve()
        if args.log
        else reports_dir / f"bool_shape_minute_only_{safe_date}_{run_stamp}.log"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(log_path)
    params = StrategyParams(
        query_date=args.date,
        pre_end=args.pre_end,
        select_start=args.select_start,
        pre_max_above_pct=args.pre_max_above,
        max_below_pct=args.max_below,
        allow_above_count=args.allow_above,
    )
    matches: list[MatchResult] = []
    rejected: list[MatchResult] = []
    errors: list[dict[str, str]] = []
    query_dates: list[str] = []
    pool_count = 0

    try:
        query_dates = split_dates(args.date)
        for query_date in query_dates:
            params.query_date = query_date
            ensure_run_time(params)
        codes = stock_pool(args.stocks, args.market, args.max_stocks)
    except Exception as exc:
        errors.append({"query_date": "", "code": "", "error": str(exc)})
        logger.error("[错误] %s", exc)
        write_json_snapshot(
            output_path,
            params,
            query_dates=query_dates,
            pool_count=0,
            processed=0,
            matches=matches,
            rejected=rejected,
            errors=errors,
            status="failed",
            started_at=started_at,
            completed_at=datetime.now().isoformat(timespec="seconds"),
        )
        logger.info("完整日志：%s", log_path)
        logger.info("JSON 检查点：%s", output_path)
        close_logger(logger)
        return 2

    pool_count = len(codes)
    total_tasks = pool_count * len(query_dates)
    progress_every = max(1, int(args.progress_every))
    logger.info(
        "开始纯分时一次性扫描：日期数=%s，每个日期股票数=%s，总任务数=%s，选股开始=%s",
        len(query_dates),
        pool_count,
        total_tasks,
        parameter_time(params.select_start)[:4],
    )
    logger.info("查询日期：%s", "，".join(query_dates))
    logger.info("数据源：仅 tqs.get_minute_data，不使用 get_tick_data 或逐笔重建")
    logger.info("完整日志：%s", log_path)
    logger.info("JSON 检查点：%s", output_path)
    write_json_snapshot(
        output_path,
        params,
        query_dates,
        pool_count,
        processed=0,
        matches=matches,
        rejected=rejected,
        errors=errors,
        status="running",
        started_at=started_at,
    )

    processed = 0
    for date_index, query_date in enumerate(query_dates, start=1):
        params.query_date = query_date
        date_match_start = len(matches)
        date_rejected_start = len(rejected)
        date_error_start = len(errors)
        logger.info(
            "\n[日期开始] %s（%s/%s）",
            query_date,
            date_index,
            len(query_dates),
        )

        for stock_index, code in enumerate(codes, start=1):
            processed += 1
            try:
                rows = fetch_minute_rows(code, query_date)
                result = evaluate_shape(code, rows, params)
                (matches if result.matched else rejected).append(result)
                if result.matched:
                    logger.info(
                        "[命中] %s %s B点=%s:%s 价格=%g",
                        query_date,
                        result.code,
                        result.signal_time[:2],
                        result.signal_time[2:4],
                        result.signal_price,
                    )
                else:
                    logger.debug(
                        "[未命中] %s %s %s 数据=%s-%s 共%s根",
                        query_date,
                        result.code,
                        result.reason,
                        result.first_time,
                        result.last_time,
                        result.rows_count,
                    )
            except Exception as exc:
                error = {
                    "query_date": query_date,
                    "code": code,
                    "error": str(exc),
                }
                errors.append(error)
                logger.error("[数据错误] %s %s %s", query_date, code, exc)
                write_json_snapshot(
                    output_path,
                    params,
                    query_dates,
                    pool_count,
                    processed=processed,
                    matches=matches,
                    rejected=rejected,
                    errors=errors,
                    status="running",
                    started_at=started_at,
                )
                flush_logger(logger)

            if stock_index % progress_every == 0 or stock_index == pool_count:
                logger.info(
                    "日期进度：%s %s/%s；总进度=%s/%s，累计命中=%s，错误=%s",
                    query_date,
                    stock_index,
                    pool_count,
                    processed,
                    total_tasks,
                    len(matches),
                    len(errors),
                )
            if processed % 100 == 0:
                write_json_snapshot(
                    output_path,
                    params,
                    query_dates,
                    pool_count,
                    processed=processed,
                    matches=matches,
                    rejected=rejected,
                    errors=errors,
                    status="running",
                    started_at=started_at,
                )

        logger.info(
            "[日期完成] %s：命中=%s，未命中=%s，错误=%s",
            query_date,
            len(matches) - date_match_start,
            len(rejected) - date_rejected_start,
            len(errors) - date_error_start,
        )

    logger.info(
        "\n全部日期扫描完成：日期数=%s，总命中=%s / %s，总错误=%s",
        len(query_dates),
        len(matches),
        total_tasks,
        len(errors),
    )
    if args.show_rejected:
        for item in rejected:
            logger.info("[未命中] %s %s %s", item.query_date, item.code, item.reason)
    completed_at = datetime.now().isoformat(timespec="seconds")
    write_json_snapshot(
        output_path,
        params,
        query_dates,
        pool_count,
        processed=total_tasks,
        matches=matches,
        rejected=rejected,
        errors=errors,
        status="completed",
        started_at=started_at,
        completed_at=completed_at,
    )
    logger.info("JSON 已保存：%s", output_path)
    logger.info("完整日志已保存：%s", log_path)
    close_logger(logger)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
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
126
127
128
129
130
131
132
133
134
135
136
137
138
139
140
141
142
143
144
145
146
147
148
149
150
151
152
153
154
155
156
157
158
159
160
161
162
163
164
165
166
167
168
169
170
171
172
173
174
175
176
177
178
179
180
181
182
183
184
185
186
187
188
189
190
191
192
193
194
195
196
197
198
199
200
201
202
203
204
205
206
207
208
209
210
211
212
213
214
215
216
217
218
219
220
221
222
223
224
225
226
227
228
229
230
231
232
233
234
235
236
237
238
239
240
241
242
243
244
245
246
247
248
249
250
251
252
253
254
255
256
257
258
259
260
261
262
263
264
265
266
267
268
269
270
271
272
273
274
275
276
277
278
279
280
281
282
283
284
285
286
287
288
289
290
291
292
293
294
295
296
297
298
299
300
301
302
303
304
305
306
307
308
309
310
311
312
313
314
315
316
317
318
319
320
321
322
323
324
325
326
327
328
329
330
331
332
333
334
335
336
337
338
339
340
341
342
343
344
345
346
347
348
349
350
351
352
353
354
355
356
357
358
359
360
361
362
363
364
365
366
367
368
369
370
371
372
373
374
375
376
377
378
379
380
381
382
383
384
385
386
387
388
389
390
391
392
393
394
395
396
397
398
399
400
401
402
403
404
405
406
407
408
409
410
411
412
413
414
415
416
417
418
419
420
421
422
423
424
425
426
427
428
429
430
431
432
433
434
435
436
437
438
439
440
441
442
443
444
445
446
447
448
449
450
451
452
453
454
455
456
457
458
459
460
461
462
463
464
465
466
467
468
469
470
471
472
473
474
475
476
477
478
479
480
481
482
483
484
485
486
487
488
489
490
491
492
493
494
495
496
497
498
499
500
501
502
503
504
505
506
507
508
509
510
511
512
513
514
515
516
517
518
519
520
521
522
523
524
525
526
527
528
529
530
531
532
533
534
535
536
537
538
539
540
541
542
543
544
545
546
547
548
549
550
551
552
553
554
555
556
557
558
559
560
561
562
563
564
565
566
567
568
569
570
571
572
573
574
575
576
577
578
579
580
581
582
583
584
585
586
587
588
589
590
591
592
593
594
595
596
597
598
599
600
601
602
603
604
605
606
607
608
609
610
611
612
613
614
615
616
617
618
619
620
621
622
623
624
625
626
627
628
629
630
631
632
633
634
635
636
637
638
639
640
641
642
643
644
645
646
647
648
649
650
651
652
653
654
655
656
657
658
659
660
661
662
663
664
665
666
667
668
669
670
671
672
673
674
675
676
677
678
679
680
681
682
683
684
685
686
687
688
689
690
691
692
693
694
695
696
697
698
699
700
701
702
703
704
705
706
707
708
709
710
711
712
713
714
715
716
717
718
719
720
721
722
723
724
725
726
727
728
729
730
731
732
733
734
735
736
737
738
739
740
741
742
743
744
745
746
747
748
749
750
751
752
753
754
755
756
757
758
759
760
761
762
763
764
765
766
767

← [20260302公众号文章](https://help.tdx.com.cn/quant/docs/markdown/gzh0122inweixinwenz/gzh20260302wzlz.html) [20261008公众号文章](https://help.tdx.com.cn/quant/docs/markdown/gzh0122inweixinwenz/20261008gzhwz.html) →
