# -*- coding: utf-8 -*-
"""qs-deploy 管线测试固件：日线双均线买入持有（PTrade 回测形态）。

特征覆盖：
- g 变量状态（g.inited/g.buy_count/g.day_count + g.state 容器内写）；
- set_commission 回测专用调用（P3 门控对象）；
- set_universe 静态字面量股票池；
- get_history 日线信号（E1 铁律：include=False 取 D-1）；
- order_target 订单家族（护栏注入对象）；
- after_trading_end 存在（落盘块注入既有函数场景）。
"""


def initialize(context):
    g.inited = False
    g.buy_count = 0
    g.day_count = 0
    set_commission(commission_ratio=0.0003, min_commission=5.0)
    set_universe(['600000.SS'])


def handle_data(context, data):
    hist = get_history(20, '1d', 'close', ['600000.SS'], fq='pre', include=False)
    closes = hist['600000.SS']
    ma_short = closes[-5:].mean()
    ma_long = closes[-20:].mean()
    if (ma_short > ma_long) and (g.buy_count == 0):
        order_target('600000.SS', 1000)
        g.buy_count = 1
        g.state['last_signal'] = 'golden_cross'
    elif (ma_short < ma_long) and (g.buy_count > 0):
        order_target('600000.SS', 0)
        g.buy_count = 0
        g.state['last_signal'] = 'death_cross'


def after_trading_end(context):
    g.day_count = g.day_count + 1
