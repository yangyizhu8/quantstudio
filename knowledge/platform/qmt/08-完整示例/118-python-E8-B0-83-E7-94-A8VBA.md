---
title: "Python调用VBA指标 | 迅投知识库"
source_url: "https://dict.thinktrader.net/codeExample/python%E8%B0%83%E7%94%A8VBA.html?id=e2M5nZ"
crawled_at: 2026-10-12T03:03:45+08:00
platform: QMT
category: 08-完整示例
depth: 3
tags: [平台/QMT, 状态/已抓取]
---

## [#](https://dict.thinktrader.net/codeExample/python%E8%B0%83%E7%94%A8VBA.html?id=e2M5nZ#调用vba指标计算历史数据) 调用VBA指标计算历史数据

内置python原生pythonVBA指标相互引用

# coding:gbk##&&stocks = ['${stock_code}.${market}']##&&##&&formula_name = 'MA' # VBA指标名称##&&period = '1d' # 计算的数据周期，可选"1m"：1分钟线；"5m"：5分钟线；"15m"：15分钟线；"30m"：30分钟线；"1h"小时线；"1d"：日线；"1w"：周线；"1mon"：月线；"1q"：季线；"1hy"：半年线；"1y"：年线##&&start_time = '' # %Y%m%d 或%Y%m%d%H%M%S##&&end_time = '' # %Y%m%d 或%Y%m%d%H%M%S##&&count = -1 # 数据条数，-1为 时间区间内全部数据##&÷nd_type = 'front' # 复权方式，参考get_market_data_ex的复权种类##&&extend_param = {'MA:N1':5,'MA:N2':20,'MA:N3':40,'MA:N4':60,} # MA指标中的参数，等同VBA编辑界面的参数设置中指定参数值##&&##&&# 注意！VBA指标中的变量和值必须以 `:` 符号赋值，才能被python取到##&&# ##&&# 例如 MA 指标的VBA写法##&&# ma1:MA(C,N1);ma2:MA(C,N2);......##&&# 其中，N1等VBA指标使用的参数可用通过extend_param字典进行指定##&&##&&def after_init(C):##&& # for s in stocks:##&& # 订阅后，call_formula可以获取到盘中动态结果##&& # id = C.subscribe_quote(s, period, '', '', -1)##&& ##&& for s in stocks:##&& # 通过 call_formula 去调用客户端中的 `MA` 指标##&& formula_results = call_formula(##&& formula_name,##&& s,##&& period=period,##&& start_time=start_time,##&& end_time=end_time,##&& count=count,##&& dividend_type=dividend_type,##&& extend_param=extend_param##&& )##&& # 返回值是一个多层嵌套的对象##&& print(formula_results.keys()) # 外层key##&& print(formula_results['outputs'].keys()) # 输出MA指标输出值名称##&& for k in formula_results['outputs']:##&& # 依次输出 标的代码, 指标值名称, 时间戳, 指标值列表##&& print(s, k, formula_results['timelist'][-1],formula_results['outputs'][k][-5:])##&&##&&

`1`

##&&from xtquant import xtdata##&&# xtdata.connect(port=58612)##&&stocks = ['${stock_code}.${market}']##&&##&&formula_name = 'MA' # VBA指标名称##&&period = '1d' # 计算的数据周期，可选"1m"：1分钟线；"5m"：5分钟线；"15m"：15分钟线；"30m"：30分钟线；"1h"小时线；"1d"：日线；"1w"：周线；"1mon"：月线；"1q"：季线；"1hy"：半年线；"1y"：年线##&&start_time = '' # %Y%m%d 或%Y%m%d%H%M%S##&&end_time = '' # %Y%m%d 或%Y%m%d%H%M%S##&&count = -1 # 数据条数，-1为 时间区间内全部数据##&÷nd_type = 'front' # 复权方式，参考get_market_data_ex的复权种类##&&extend_param = {'MA:N1':5,'MA:N2':20,'MA:N3':40,'MA:N4':60,} # MA指标中的参数，等同VBA编辑界面的参数设置中指定参数值##&&##&&# 注意！VBA指标中的变量和值必须以 `:` 符号赋值，才能被python取到##&&# ##&&# 例如 MA 指标的VBA写法##&&# ma1:MA(C,N1);ma2:MA(C,N2);......##&&# 其中，N1等VBA指标使用的参数可用通过extend_param字典进行指定##&&##&&import time##&&for s in stocks:##&& # 订阅后，call_formula可以获取到盘中动态结果##&& id = xtdata.subscribe_quote(s, period, '', '', -1)##&&while 1:##&& for s in stocks:##&& formula_results = xtdata.call_formula(##&& formula_name,##&& s,##&& period=period,##&& start_time=start_time,##&& end_time=end_time,##&& count=count,##&& dividend_type=dividend_type,##&& extend_param=extend_param##&& )##&& # 返回值是一个多层嵌套的对象##&& print(formula_results.keys()) # 外层key##&& print(formula_results['outputs'].keys()) # 输出MA指标输出值名称##&& for k in formula_results['outputs']:##&& # 依次输出 标的代码, 指标值名称, 时间戳, 指标值列表##&& print(s, k, formula_results['timelist'][-1],formula_results['outputs'][k][-5:])##&& ##&& time.sleep(3)##&&##&&

`1`

```
code:= ''; // 标的代码，传空为主图标的

CO := 0; // CO为坐标轴类型0交易日坐标1自然日2交易交易时间

p := -1; // 周期，传-1为当前主图周期，具体值见STKINDI函数说明

ma1:STKINDI(code,'ma.ma1',CO,p);  // 引用其他的指标，格式为 '指标名称.输出值名称'
kdj_k:STKINDI(code,'kdj.k',CO,p);  // 引用其他的指标，格式为 '指标名称.输出值名称'
```

### [#](https://dict.thinktrader.net/codeExample/python%E8%B0%83%E7%94%A8VBA.html?id=e2M5nZ#订阅vba指标实时计算结果) 订阅VBA指标实时计算结果

内置python原生pythonVBA指标相互引用

# coding:gbk##&&##&&import time##&&from functools import partial##&&import pandas as pd##&&##&&##&&def callback(s, data):##&& '''##&& 回调函数，展示结果##&& data格式示例：##&& {'dbt': 0, 'timelist': [1712592000000, 1712678400000], 'outputs': {'ma1': [nan, nan], 'ma2': [nan, nan], 'ma3': [nan, nan], 'ma4': [nan, nan]}}##&& '''##&& ##&& # 转换时间戳##&& timelist = data['timelist'][-5:]##&& timelist_fmt = [timetag_to_datetime(t,'%Y%m%d') for t in timelist]##&& for index_name in data['outputs']:##&& ##&& for k, t, v in zip(timelist,timelist_fmt,data['outputs'][index_name][-5:]):##&& # 依次输出 : 标的代码, 时间戳, 转换后的格式化时间, 指标值名称, 指标值值##&& print(s, k, t, index_name, v)##&&##&&# 初始化参数变量##&&stock_list = ['${stock_code}'] # 股票代码列表##&&formula_name = 'MA' # VBA指标名称##&&period = '1d' # 计算的数据周期，可选"1m"：1分钟线；"5m"：5分钟线；"15m"：15分钟线；"30m"：30分钟线；"1h"小时线；"1d"：日线；"1w"：周线；"1mon"：月线；"1q"：季线；"1hy"：半年线；"1y"：年线##&&start_time = '' # %Y%m%d 或%Y%m%d%H%M%S##&&end_time = '' # %Y%m%d 或%Y%m%d%H%M%S##&&count = -1 # 数据条数，-1为 时间区间内全部数据##&÷nd_type = 'front' # 复权方式，参考get_market_data_ex的复权种类##&&extend_param = {'MA:N1':5,'MA:N2':20,'MA:N3':40,'MA:N4':60,} # MA指标中的参数，等同VBA编辑界面的参数设置中指定参数值##&&##&&##&&def after_init(C):##&& ##&& for s in stock_list:##&& # 订阅该指标实时计算结果，并把结果传给回调函数##&& id = subscribe_formula(formula_name,s,period,start_time,end_time,count=count,extend_param=extend_param,callback = partial(callback, s))##&& ##&& ##&&##&&

`1`

import time##&&from xtquant import xtdata##&&from functools import partial##&&import pandas as pd##&&##&&xtdata.connect(port=58612)##&&##&&def callback(s, data):##&& '''##&& 回调函数，展示结果##&& data格式示例：##&& {'dbt': 0, 'timelist': [1712592000000, 1712678400000], 'outputs': {'ma1': [nan, nan], 'ma2': [nan, nan], 'ma3': [nan, nan], 'ma4': [nan, nan]}}##&& '''##&& ##&& # 转换时间戳##&& timelist = data['timelist'][-5:]##&& timelist_fmt = [xtdata.timetag_to_datetime(t,'%Y%m%d') for t in timelist]##&& for index_name in data['outputs']:##&& for k, t, v in zip(timelist,timelist_fmt,data['outputs'][index_name][-5:]):##&& # 依次输出 : 标的代码, 时间戳, 转换后的格式化时间, 指标值名称, 指标值值##&& print(s, k, t, index_name, v)##&&##&&# 初始化参数变量##&&stock_list = ['${stock_code}.${market}'] # 股票代码列表##&&formula_name = 'MA' # VBA指标名称##&&period = '1d' # 计算的数据周期，可选"1m"：1分钟线；"5m"：5分钟线；"15m"：15分钟线；"30m"：30分钟线；"1h"小时线；"1d"：日线；"1w"：周线；"1mon"：月线；"1q"：季线；"1hy"：半年线；"1y"：年线##&&start_time = '' # %Y%m%d 或%Y%m%d%H%M%S##&&end_time = '' # %Y%m%d 或%Y%m%d%H%M%S##&&count = -1 # 数据条数，-1为 时间区间内全部数据##&÷nd_type = 'front' # 复权方式，参考get_market_data_ex的复权种类##&&extend_param = {'MA:N1':5,'MA:N2':20,'MA:N3':40,'MA:N4':60,} # MA指标中的参数，等同VBA编辑界面的参数设置中指定参数值##&&##&&for s in stock_list:##&& # 订阅该指标实时计算结果，并把结果传给回调函数##&& id = xtdata.subscribe_formula(formula_name,s,period,start_time,end_time,count=count,extend_param=extend_param,callback = partial(callback, s))##&&##&&

`1`

```
code:= ''; // 标的代码，传空为主图标的

CO := 0; // CO为坐标轴类型0交易日坐标1自然日2交易交易时间

p := -1; // 周期，传-1为当前主图周期，具体值见STKINDI函数说明

ma1:STKINDI(code,'ma.ma1',CO,p);  // 引用其他的指标，格式为 '指标名称.输出值名称'
kdj_k:STKINDI(code,'kdj.k',CO,p);  // 引用其他的指标，格式为 '指标名称.输出值名称'
```

上次更新:

邀请注册送VIP优惠券

分享下方的内容给好友、QQ群、微信群,好友注册您即可获得VIP优惠券

玩转qmt,上迅投qmt知识库

登录后获取
