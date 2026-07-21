"""
策略名称：
二八轮动策略
运行周期:
日线
策略流程：
策略通过计算沪深300、中证500的阶段动量数据，来决定持有沪深300ETF还是中证500ETF还是货币基金
持有至少10天
注意事项：
策略中调用的order_target接口的使用有场景限制，回测可以正常使用，交易谨慎使用。
回测场景下撮合是引擎计算的，因此成交之后持仓信息的更新是瞬时的，但交易场景下信息的更新依赖于柜台数据
的返回，无法做到瞬时同步，可能造成重复下单。详细原因请看帮助文档。
"""


# 初始化
def initialize(context):
    set_params()
    g.signal = 0
    g.open_date = get_trading_day(-40)
    # 基金池: 沪深300，中证500，货币基金
    g.fund_list = ['000300.SS', '510300.SS',
                   '000905.SS', '510500.SS',
                   '511880.SS', '511880.SS']

    if not is_trade():
        set_backtest()  # 设置回测条件


# 设置策略参数
def set_params():
    g.N = 20  # N日涨幅
    g.holding_days = 10  # 至少持有天数（交易日）
    g.rise_threshold = 0  # 涨幅阈值


# 设置回测条件
def set_backtest():
    set_limit_mode('UNLIMITED')
    set_commission(commission_ratio=0.00015, min_commission=5.0)


def before_trading_start(context, data):
    current_date = context.blotter.current_dt.strftime('%Y%m%d')
    # 2005-05-01前回测由于数据不足，不执行。
    if current_date < '20050501':
        g.trade_flag = False
    else:
        g.trade_flag = True


# 盘中处理
def handle_data(context, data):
    if not g.trade_flag:
        return
    # 产生信号并交易
    g.signal = create_signal(g.fund_list, g.N, g.rise_threshold)
    trade(context, g.signal, g.fund_list, g.holding_days)
    return


# 交易函数
def trade(context, signal, security_round_list, holding_days):
    security_round_num = int(len(security_round_list) / 2)  # 轮动组数
    pre_trading_date = get_trading_day(-holding_days - 1)
    days = (g.open_date - pre_trading_date).days
    if days > 0:
        return
    hold = set(context.portfolio.positions.keys())
    if signal == 0:  # 买货币基金
        to_buy = {security_round_list[(security_round_num - 1) * 2 + 1]}
    else:
        to_buy = {security_round_list[(signal - 1) * 2 + 1]}
    sell = hold - to_buy
    buy = to_buy - hold
    if sell:
        order_target(list(sell)[0], 0)
    if buy:
        target_value = context.portfolio.cash
        order_value(list(buy)[0], target_value)
        g.open_date = context.current_dt.date()
    return


# 产生信号，返回signal
def create_signal(fund_list, num, rise_threshold):
    price_rise = [0, 0, 0, 0]
    max_rise_index = 0  # 涨幅最大的指数
    price_rise_max = -999999  # 价格涨幅
    security_round_num = int(len(fund_list) / 2)  # 轮动组数
    # 货币基金不参与计算信号
    for i in range(security_round_num - 1):
        stock = fund_list[i * 2]
        his_data = get_history(num + 1, frequency='1d', field='close', security_list=stock, fq=None,
                               include=False, is_dict=True)
        price_rise[i] = his_data[stock]['close'][-1] / his_data[stock]['close'][-num - 1] - 1  # N日涨幅
        if price_rise[i] > price_rise_max:
            max_rise_index = i
            price_rise_max = price_rise[i]
    if price_rise[max_rise_index] > rise_threshold:
        signal = max_rise_index + 1
    else:
        signal = security_round_num
    return signal
