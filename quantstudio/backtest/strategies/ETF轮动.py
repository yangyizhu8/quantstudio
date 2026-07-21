# 筛选市值介于20-30亿的股票，选取市值最小的三只
# 每天开盘检查；每持有5个交易日后调仓

def initialize(context):
    # 设定沪深300作为基准，PTrade通常使用 .SS / .SZ 后缀
    set_benchmark('000300.SS')

    # 持仓数量
    g.stocknum = 3

    # 交易日计数器
    g.days = 0

    # 调仓频率：5个交易日
    g.refresh_rate = 5

    # 每个交易日开盘后运行
    run_daily(context, trade, time='09:30')


def check_stocks(context):
    q = query(
        valuation.code,
        valuation.market_cap
    ).filter(
        valuation.market_cap >= 20,
        valuation.market_cap <= 30
    ).order_by(
        valuation.market_cap.asc()
    )

    df = get_fundamentals(q)

    if df is None or len(df) == 0:
        return []

    buylist = list(df['code'])

    # 过滤停牌股票
    buylist = filter_paused_stock(buylist)

    return buylist[:g.stocknum]


def trade(context):
    # 每5个交易日调仓一次
    if g.days % g.refresh_rate == 0:

        # 当前持仓全部卖出
        sell_list = list(context.portfolio.positions.keys())
        for stock in sell_list:
            order_target_value(stock, 0)

        # 重新选股
        stock_list = check_stocks(context)

        if len(stock_list) == 0:
            g.days = 1
            return

        # 等权分配资金
        cash_per_stock = context.portfolio.cash / len(stock_list)

        # 买入目标股票
        for stock in stock_list:
            order_value(stock, cash_per_stock)

        g.days = 1

    else:
        g.days += 1


def filter_paused_stock(stock_list):
    if len(stock_list) == 0:
        return []

    # PTrade常用停牌查询接口：get_stock_status(stock_list, 'HALT')
    # 返回值通常为 {stock: True/False}，True表示停牌
    halt_status = get_stock_status(stock_list, 'HALT')

    return [
        stock for stock in stock_list
        if not halt_status.get(stock, False)
    ]
