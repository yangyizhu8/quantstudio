---
title: "# 通达信Lambda量化平台专属函数"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340"
crawled_at: 2026-10-12T02:41:37+08:00
platform: TDX
category: 06-Lambda平台
depth: 1
tags: [平台/TDX, 状态/已抓取]
---

# [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340#通达信lambda量化平台专属函数) 通达信Lambda量化平台专属函数

`注意是这个界面才能使用的云端策略`目前只放开了内测版入口，属于付费增值模块，试用需要购买算力包。
![](https://help.tdx.com.cn/quant/uploads/mindoc/images/m_b00f99cb546c4084664b2cdf3779a773_r.png)
![](https://help.tdx.com.cn/quant/uploads/mindoc/images/m_81b33b2866a9662077c4e2581c952d02_r.png)

# [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340#lambda-策略支持能力说明) Lambda 策略支持能力说明

本文档面向策略编写用户，说明当前 Python 回测策略中可以直接使用的库、函数和不支持的能力。

用户提交的 `req` 只需要包含策略代码，例如 `init`、`handle_bar`、`after_trading` 等函数。不需要也不应该写平台启动代码。

## [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340#基本写法) 基本写法

策略代码中不要写 `import`，平台会自动提供常用库和回测 API。

推荐写法：

```
def init(context):
    context.stock = "000001.SZ"
    set_benchmark("000300.SH")

def handle_bar(context, bar_dict):
    hist = attribute_history(context.stock, 5, "1d", ["close"])
    if len(hist) < 5:
        return

    ma5 = hist["close"].mean()
    current = bar_dict[context.stock].close
    order_target_percent(context.stock, 1.0 if current > ma5 else 0)
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

## [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340#支持的策略回调函数) 支持的策略回调函数

当前支持以下策略函数：

| 函数名 | 说明 |
| --- | --- |
| `init(context)` | 回测开始时执行一次，也可写作 `initialize(context)` |
| `before_trading(context)` | 每个交易日前或每个交易日首根 bar 前执行，也可写作 `before_trading_start(context)` |
| `handle_bar(context, bar_dict)` | 每根 bar 执行，是默认主策略入口，也可写作 `handle_data(context, bar_dict)` |
| `after_trading(context)` | 每个交易日结束后执行，也可写作 `after_trading_end(context)` |
| `on_strategy_end(context)` | 回测结束时执行一次 |
| `on_order(context, order)` | 订单事件回调 |
| `on_trade(context, trade)` | 成交事件回调 |

关于 `trade`：

- `trade` 只是普通函数名，不是 `run_backtest` 自动识别的生命周期回调。

- `run_backtest` 直接识别的每根 bar 主入口是 `handle_bar(context, bar_dict)` 或 `handle_data(context, bar_dict)`。

- 如果希望沿用 `trade` 风格，需要在 `init(context)` 中调用 `run_daily(trade, time_rule="every_bar")` 注册。

- 被 `run_daily` 注册的函数支持 `func(context)` 或 `func(context, bar_dict)` 两种签名。

- 调度函数会在每根 bar 的 `handle_bar` 之前执行。

- 如果没有调用 `run_daily(...)`，只定义顶层 `trade` 函数不会被自动调用。

示例：

```
def init(context):
    context.stock = context.universe[0]
    run_daily(trade, time_rule="every_bar")

def trade(context, bar_dict):
    pass
```

1
2
3
4
5
6
7

## [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340#可直接使用的常用库) 可直接使用的常用库

以下名字可以在策略中直接使用，无需 `import`。

| 名字 | 说明 | 示例 |
| --- | --- | --- |
| `datetime` | 日期时间类 | `datetime.now()` |
| `date` | 日期类 | `date(2026, 1, 1)` |
| `timedelta` | 时间差 | `timedelta(days=1)` |
| `timezone` | 时区 | `timezone.utc` |
| `dt` | `datetime` 模块 | `dt.datetime.now()` |
| `math` | 数学函数 | `math.sqrt(9)` |
| `statistics` | 统计函数 | `statistics.mean(values)` |
| `decimal` | 高精度小数模块 | `decimal.Decimal("1.23")` |
| `Decimal` | 高精度小数类 | `Decimal("1.23")` |
| `json` | JSON 编解码 | `json.dumps(data)` |
| `re` | 正则表达式 | `re.match("a", text)` |
| `collections` | 容器工具 | `collections.Counter(values)` |
| `itertools` | 迭代工具 | `itertools.chain(a, b)` |
| `functools` | 函数工具 | `functools.reduce(func, values)` |
| `operator` | 操作符函数 | `operator.add(a, b)` |
| `bisect` | 二分插入和查找 | `bisect.bisect_left(values, x)` |
| `heapq` | 堆队列 | `heapq.heappush(values, x)` |
| `copy` | 对象复制 | `copy.copy(obj)` |
| `dataclasses` | 数据类模块 | `dataclasses.asdict(obj)` |
| `dataclass` | 数据类装饰器 | `@dataclass` |
| `enum` | 枚举模块 | `enum.Enum` |
| `Enum` | 枚举基类 | `class Side(Enum): ...` |
| `typing` | 类型标注工具 | `typing.Any` |
| `random` | 随机数工具 | `random.choice(values)` |
| `SimpleNamespace` | 简单对象容器 | `SimpleNamespace(value=1)` |
| `np` | NumPy | `np.array([1, 2, 3])` |
| `numpy` | NumPy，同 `np` | `numpy.mean(values)` |
| `pd` | pandas | `pd.DataFrame(data)` |
| `pandas` | pandas，同 `pd` | `pandas.Series(values)` |

`np` 和 `numpy` 是同一个模块；`pd` 和 `pandas` 是同一个模块。

## [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340#可直接使用的回测-api) 可直接使用的回测 API

平台会提供 `pylambda.api` 中的回测 API，策略中可以直接调用。

常见 API 示例：

| 类型 | 示例 API |
| --- | --- |
| 基准和参数 | `set_benchmark`、`set_commission`、`set_slippage` |
| 行情查询 | `get_price`、`attribute_history`、`history`、`get_current_data`、`get_datetime` |
| 下单交易 | `order`、`order_target`、`order_value`、`order_percent`、`order_target_value`、`order_target_percent` |
| 记录输出 | `record` |
| 定时任务 | `run_daily` |
| 股票池和板块查询 | `get_stock_list`、`get_stock_list_in_sector` |

平台当前会注入 `pylambda.api.__all__` 中的名字；但本文档明确标注为“不支持”的接口，即使运行环境中存在，也不能作为当前可用策略能力使用。

## [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340#支持的-python-语法) 支持的 Python 语法

当前支持常规 Python 策略写法，包括：

- 函数定义

- 全局变量

- 类定义

- `@dataclass`

- `Enum`

- 列表、字典、集合、元组

- 推导式

- `for` / `while`

- `if` / `elif` / `else`

- `try` / `except`

- 类型标注

示例：

```
@dataclass
class Params:
    window: int = 5

class Signal(Enum):
    BUY = 1
    SELL = 2
```

1
2
3
4
5
6
7
8

## [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340#不支持的写法) 不支持的写法

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340#不支持用户自己导入包) 不支持用户自己导入包

策略中不要写任何 `import`。

不支持：

```
import os
import pandas as pd
from datetime import datetime
from pylambda.api import *
```

1
2
3
4

请直接使用平台提供的名字：

```
started_at = datetime.now()
frame = pd.DataFrame(data)
```

1
2

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340#不支持完整脚本模式) 不支持完整脚本模式

策略中不要调用：

```
load_lambda(...)
run_backtest(...)
types.ModuleType(...)
```

1
2
3

这些由平台包装层自动处理。

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340#不支持的调度入口) 不支持的调度入口

当前支持 `run_daily(...)`。以下调度或 tick 入口当前不作为可用策略能力：

```
run_weekly
run_monthly
handle_tick
```

1
2
3

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340#不支持文件、网络、进程和系统操作) 不支持文件、网络、进程和系统操作

以下包和能力不面向策略开放：

```
os
sys
pathlib
shutil
subprocess
socket
requests
urllib
http
ftplib
smtplib
multiprocessing
threading
asyncio
pickle
marshal
importlib
inspect
builtins
ctypes
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

不要在策略中读写本地文件、访问网络、启动进程或动态加载代码。

## [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340#不支持的函数和操作) 不支持的函数和操作

以下函数不支持：

```
open
input
eval
exec
compile
__import__
globals
locals
vars
dir
getattr
setattr
delattr
breakpoint
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

示例：

```
open("data.csv")
eval("1 + 1")
getattr(context, "stock")
```

1
2
3

这些写法都不支持。

## [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340#pandas-numpy-使用限制) pandas / NumPy 使用限制

pandas 和 NumPy 支持用于内存中的指标计算、表格处理和数组计算。

支持示例：

```
values = np.array([1, 2, 3])
mean_value = np.mean(values)

frame = pd.DataFrame({"close": values})
ma = frame["close"].rolling(2).mean()
```

1
2
3
4
5

不支持 pandas 读文件、读数据库、读 HTML、读 pickle 等 IO 操作，例如：

```
pd.read_csv
pd.read_excel
pd.read_sql
pd.read_json
pd.read_parquet
pd.read_pickle
pd.read_html
pd.read_table
pd.ExcelFile
pd.ExcelWriter
pd.HDFStore
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

不支持 pandas 写文件、写数据库等操作，例如：

```
to_csv
to_excel
to_json
to_sql
to_parquet
to_pickle
to_hdf
to_html
to_xml
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

不支持 NumPy 文件 IO，例如：

```
np.load
np.save
np.savez
np.loadtxt
np.savetxt
np.genfromtxt
np.fromfile
np.memmap
array.tofile
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

## [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340#请求参数要求) 请求参数要求

策略源码放在请求的 `req` 字段中，回测参数放在结构化字段中。

示例：

```
{
  "reqId": 202400,
  "req": "def init(context):\n    context.stock = \"000001.SZ\"\n\ndef handle_bar(context, bar_dict):\n    pass\n",
  "req_len": 94,
  "total_len": 94,
  "task_userid": "USER_ID",
  "task_uuid": "",
  "symbols": ["000001.SZ"],
  "blocks": [],
  "start_date": "2025-01-01",
  "end_date": "2026-06-30",
  "frequency": "1d",
  "initial_cash": 500000.0,
  "modname": "mod_backtest.dll"
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

说明：

- `symbols` 可以为空，但如果策略在 `init` 中立即查询某只股票行情，建议把该股票放入 `symbols`，便于平台提前初始化数据。

- `blocks` 可以为空。

- `start_date`、`end_date`、`frequency` 必填。

- `initial_cash` 可不填，默认 `0`；不能小于 `0`。

## [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340#防未来函数规则) 防未来函数规则

平台会尽量保证策略只能看到当前回测时间之前或当前已完成 bar 的数据。

基本规则：

- `history` 默认不包含当前未完成 bar。

- `attribute_history` 默认不包含当前未完成 bar。

- `get_price` 的 `end_date` 不应超过 `context.current_dt`。

- 不建议跨日期缓存复权行情结果。

- 用户自行读取外部文件或外部数据时，需要自行保证没有未来数据。

示例：

```
def handle_bar(context, bar_dict):
    hist = attribute_history("000001.SZ", 20, "1d", ["close"])
    # hist 中只包含当前时点可见的数据
```

1
2
3

## [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340#常见错误原因) 常见错误原因

| 错误现象 | 常见原因 | 处理方式 |
| --- | --- | --- |
| `imports are not allowed` | 策略里写了 `import` | 删除 import，直接使用平台提供的名字 |
| `forbidden builtin` | 使用了不支持的内置函数 | 改成普通策略逻辑，不使用文件/反射/动态执行能力 |
| `forbidden pandas IO attribute` | 使用了 pandas 读写文件/数据库 API | 改为使用平台行情 API 或内存数据 |
| `forbidden numpy IO attribute` | 使用了 NumPy 文件 IO API | 改为内存计算 |
| `证券不存在或未加载` | 策略查询的证券未被平台提前初始化 | 在请求 `symbols` 中传入策略需要交易或立即查询的证券 |
| `回测股票池为空` | `symbols` 和 `blocks` 都为空，且策略未能提供有效股票池 | 在请求中提供 `symbols` 或 `blocks` |
