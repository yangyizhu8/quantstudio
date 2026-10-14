# -*- coding: utf-8 -*-
"""qs-deploy 管线测试固件：run_daily 周频挂载形态（PD-0006 源形态）。

模拟 convert-pipeline 周频/定时类产物（如 tech_etf_mvo_rotation_ptrade.py）：
- 无模块级 handle_data——调仓逻辑挂自定义函数 weekly_rebalance；
- initialize 内 run_daily 挂载 weekly_rebalance（盘后收盘时点注册；具体时点
  字面量不入 docstring，避免反向断言误命中）；
- 周内去重（g.state 记 ISO 周边界，仅每周第一个交易日调仓）；
- get_history 日线信号（E1 铁律：include=False 取 D-1）；
- order_target 订单家族（护栏注入对象）。
"""


def initialize(context):
    g.inited = False
    g.week_key = None
    set_universe(['510300.SS'])
    run_daily(context, weekly_rebalance, time='15:00')


def weekly_rebalance(context):
    hist = get_history(20, '1d', 'close', ['510300.SS'], fq='pre', include=False)
    closes = hist['510300.SS']
    ma_short = closes[-5:].mean()
    ma_long = closes[-20:].mean()
    today = get_history(2, '1d', 'close', ['510300.SS'], fq='pre', include=False)
    week_key = str(today['510300.SS'].index[-1])[:10]
    if g.week_key == week_key:
        return
    g.week_key = week_key
    if ma_short > ma_long:
        order_target('510300.SS', 10000)
        g.state['last_action'] = 'hold_etf'
    else:
        order_target('510300.SS', 0)
        g.state['last_action'] = 'cash'
