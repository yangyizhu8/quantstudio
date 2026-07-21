"""
策略名称：
双均线策略
注意事项：
策略中调用的order_target接口的使用有场景限制，回测可以正常使用，交易谨慎使用。
回测场景下撮合是引擎计算的，因此成交之后持仓信息的更新是瞬时的，但交易场景下信息的更新依赖于柜台数据
的返回，无法做到瞬时同步，可能造成重复下单。详细原因请看帮助文档。
"""
import numpy as np


def initialize(context):
    # 初始化此策略
    g.security = '600570.SS'


def before_trading_start(context, data):
    h = get_history(20, '1d', field=['close', 'volume'], security_list=g.security,
                    fq='dypre', include=False, is_dict=True)
    g.close_data = h[g.security]['close']


# 当五日均线高于十日均线时买入，当五日均线低于十日均线时卖出
def handle_data(context, data):
    # 获取历史日K线数据
    current_price = data[g.security].close
    # 合成最新K线序列
    close_data = np.concatenate((g.close_data, np.array(list([current_price]))), axis=0)
    # 获取5日、10日均线
    ma5 = get_ma(close_data, 5)
    ma10 = get_ma(close_data, 10)
    # 得到当前资金余额
    cash = context.portfolio.cash
    # 如果当前有余额，并且五日均线大于十日均线
    if ma5 > ma10 and get_position(g.security).amount == 0:
        # 用所有 cash 买入股票
        order_value(g.security, cash)
        # 记录这次买入
        log.info("Buying %s" % g.security)

    # 如果五日均线小于十日均线，并且目前有头寸
    elif ma5 < ma10 and get_position(g.security).enable_amount > 0:
        # 全部卖出
        order_target(g.security, 0)
        # 记录这次卖出
        log.info("Selling %s" % g.security)


# 获取MA函数
def get_ma(close_array, num):
    ma = close_array[-num:].mean()
    return round(ma, 2)