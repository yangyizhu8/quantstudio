# -*- coding: utf-8 -*-
"""qs-deploy 管线测试固件：分钟级简单动量示例（PTrade 回测形态）。

特征覆盖：
- 分钟频率信号（get_history '1m'）→ P1 频率画像 minute → minute_keep 档；
- set_universe(get_Ashares()) 动态股票池；
- g.helper 类实例（P1 可序列性判定 exempt_needed → P3 改 g.__helper → P4 还原）；
- order 订单家族（护栏注入对象）。
"""


class _MinuteCounter(object):

    def __init__(self):
        self.count = 0

    def bump(self):
        self.count = self.count + 1
        return self.count


def initialize(context):
    g.signal_count = 0
    g.helper = _MinuteCounter()
    set_universe(get_Ashares())


def handle_data(context, data):
    hist = get_history(10, '1m', 'close', ['600000.SS'], fq='pre', include=False)
    closes = hist['600000.SS']
    if (closes[-1] > closes[-6]) and (g.signal_count == 0):
        order('600000.SS', 100)
        g.signal_count = 1
        g.state['last_signal'] = 'minute_break'
