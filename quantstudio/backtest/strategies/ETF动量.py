import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from datetime import datetime, timedelta
import math

def initialize(context):
    # 加入各个ETF代码
    g.security = [
        '518880.SS',   # 黄金ETF
        '513880.SS',    #日经225ETF
        '159770.XSHE',    #机器人ETF
        '159819.XSHE',    #人工智能ETF
        '513100.SS',  # 纳指100（海外资产）
        '159915.XSHE',  # 创业板100（成长股，科技股，中小盘）     
        '515880.SS',  # 通信        
        '513120.SS',  # 港股创新药
        '159755.XSHE',   # 电池
        '159652.XSHE',   #有色        
        '510500.SS',    #500ETF
        '159870.XSHE',    #化工ETF
        '159995.XSHE'     #芯片ETF
    ]

    set_universe(g.security)
    set_limit_mode('UNLIMITED')
    set_commission(commission_ratio=0.00005, min_commission=0.5, type="ETF")
    g.lookback_days = 25 # 设置计算动量因子的时间窗口
    g.last_traded = ''  # 用于记录上一次交易的ETF
    g.annual_trading_days = 250  # 假设一年有250个交易日

def before_trading_start(context, data):
    # 获取历史数据
    history = get_history(g.lookback_days, frequency='1d', field=["close"], security_list=g.security, fq='pre', include=False, is_dict=True)
    g.momentum_scores = {}  # 存储每个ETF的动量因子得分
    g.annualized_returns = {}  # 存储每个ETF的年化收益率
    g.r_squared = {}  # 存储每个ETF的判定系数

    for etf in g.security:
        # 确保历史数据存在
        if etf not in history or len(history[etf]['close']) < g.lookback_days:
            log.info("历史数据不足，跳过 {}".format(etf))
            continue
        close_data = history[etf]['close']

        # 计算对数收益率
        log_returns = np.diff(np.log(close_data))

        # 创建x值（时间序列）
        x = np.arange(len(close_data))
        y = np.log(close_data)

        # 线性回归
        slope, intercept = np.polyfit(x, y, 1)

        # 计算年化收益率
        annualized_return = math.pow(math.exp(slope), g.annual_trading_days) - 1

        # 计算判定系数 (R-squared)
        residuals = y - (slope * x + intercept)
        ss_res = np.sum(residuals ** 2)
        ss_tot = (len(y) - 1) * np.var(y, ddof=1)
        r_squared_value = 1 - (ss_res / ss_tot)

        # 计算动量因子得分
        momentum_score = annualized_return * r_squared_value

        # 存储结果
        g.momentum_scores[etf] = momentum_score
        g.annualized_returns[etf] = annualized_return
        g.r_squared[etf] = r_squared_value

def handle_data(context, data):
    # 按动量因子得分从高到低排序ETF
    sorted_etfs = sorted(g.momentum_scores.keys(), key=lambda x: g.momentum_scores[x], reverse=True)

    if not sorted_etfs:
        log.info("没有有效的ETF数据，跳过交易")
        return

    # 检查所有动量因子是否都小于0
    all_negative = all(score < 0 for score in g.momentum_scores.values())

    if all_negative:
        # 如果所有动量因子都小于0，清仓
        for etf in context.portfolio.positions:
            order_target_value(etf, 0)
            log.info("清仓 {}".format(etf))
        log.info("所有ETF的动量因子都小于0，空仓")
        return

    best_etf = sorted_etfs[0]  # 选择得分最高的ETF

    # 如果当前持仓的ETF是得分最高的，继续持有
    if g.last_traded and g.last_traded in context.portfolio.positions and g.last_traded == best_etf:
        log.info("继续持有 {}".format(best_etf))
        return

    # 卖出之前持有的ETF
    if g.last_traded is not None and g.last_traded in context.portfolio.positions:
        order_target_value(g.last_traded, 0)
        log.info("卖出 {}".format(g.last_traded))

    # 买入得分最高的ETF
    if best_etf is not None:
        order_target_value(best_etf, context.portfolio.total_value)
        log.info("买入 {}".format(best_etf))
        g.last_traded = best_etf
    else:
        log.error("best_etf 为空，无法进行交易")

    # 记录每个ETF的动量因子得分
    log.info("ETF动量因子得分: {}".format(g.momentum_scores))