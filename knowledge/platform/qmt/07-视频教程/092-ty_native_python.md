---
title: "# 原生Python_策略回测"
source_url: "https://dict.thinktrader.net/videos/touyan/ty_native_python.html"
crawled_at: 2026-10-12T03:03:22+08:00
platform: QMT
category: 07-视频教程
depth: 3
tags: [平台/QMT, 状态/已抓取]
---

# [#](https://dict.thinktrader.net/videos/touyan/ty_native_python.html#原生python-策略回测) 原生Python_策略回测

视频源码

```
# 根据情况指定xtquant的路径
import sys
import numpy as np
import pandas as pd
from xtquant import xtdata
import xtquant
print(xtquant)
print(xtdata.data_dir)
# 指定获取投研端数据(可不指定，默认优先连接投研)
# xtdata.reconnect(port=58613)
# xtdata.download_sector_data()

class G():
    pass

g = G()

def init(C):
    # ------------------------参数设定-----------------------------
    g.his_st = {}

    # g.s = C.get_stock_list_in_sector("沪深A股")  # 获取沪深A股股票列表
    g.s = C.get_stock_list_in_sector("沪深300")  # 获取沪深300股票列表
    # g.s = ['000001.SZ']
    g.day = 0
    g.holdings = {i: 0 for i in g.s}
    g.weight = [0.1] * 10
    g.buypoint = {}
    g.money = 1000000  # C.capital
    g.accid = 'test'
    g.profit = 0
    # 因子权重
    g.buy_num = 10  # 买排名前5的股票，在过滤中会用到
    g.per_money = g.money / g.buy_num * 0.95
    

def after_init(C):
    # ------------------------量价数据获取-----------------------------
    data = xtdata.get_market_data_ex([], g.s, period='1d', dividend_type='front_ratio',
                                           fill_data=True)
    close_df = get_df_ex(data,"close")
    open_df = get_df_ex(data,"open")
    low_df = get_df_ex(data,"low")
    high_df = get_df_ex(data,"high")
    volume_df = get_df_ex(data,"volume")
    amount_df = get_df_ex(data,"amount")
    preclose_df = get_df_ex(data,"preClose")

    # ------------------------基础数据获取-----------------------------
    # 将 g.s 中的全部股票的 TotalVolume 都获取出来，组合成一个 DataFrame
    # 例如 g.s 中有 10 个股票，那么下面的代码就会返回一个 1 行 10 列的 DataFrame
    # 该 DataFrame 的 index 是股票代码，columns 是 TotalVolume
    # 该 DataFrame 的数据是每个股票的 TotalVolume，请给出代码：
    # C.get_instrumentdetail('600000.SH')['TotalVolume']
    # 使用字典推导来获取每个股票的TotalVolume
    total_volumes = {stock: C.get_instrumentdetail(stock)['TotalVolume'] for stock in g.s}
    # 将字典转换为DataFrame，但先转化为一个嵌套字典
    df_total_volume = pd.DataFrame({k: v for k, v in total_volumes.items()}, index=['TotalVolume'])

    # ------------------------财务数据获取-----------------------------

    # ------------------------因子1计算及处理--------------------------------
    # 1. 市值因子: 用市值因子 = 股票收盘价 * 股票总股本，要利用好，要求对应列名相乘
    factor = close_df * df_total_volume.loc['TotalVolume']

    # ------------------------因子2计算及处理--------------------------------
    # 判断 close_df 中的 code 上市时间大于120天
    stock_opendate_filter = filter_opendate_qmt(C, close_df, 120)

    # ------------------------上市日期过滤处理--------------------------------
    # 两个布尔值的 DataFrame 对应相乘过滤掉每个交易日上市不足120天的
    factor *= stock_opendate_filter.astype(int).replace(0, np.nan)

    # ------------------------排序处理-----------------------------------
    # 对 factor 每行在一行内进行排序
    factor_sorted = rank_filter(factor, 10, ascending=True, method='min', na_option='keep')

    # ------------------------因子组合得到布尔值信号--------------------------------

    # 确保没有未来数据的影响，将因子数据向后移动一天
    g.factor_df = factor_sorted.shift(1)  #
    g.close_df = close_df.shift(1)  # 为了计算收益率，将收盘价向后移动一天
    g.open_df = open_df
    g.stock_opendate_filter = stock_opendate_filter

def handlebar(C):
    # 获取当前 K 线位置
    d = C.barpos
    # 获取当前 K 线时间
    backtest_time = timetag_to_datetime(C.get_bar_timetag(C.barpos), "%Y%m%d")
    factor_series = g.factor_df.loc[backtest_time]
    buy_list = daily_filter(factor_series, backtest_time)
    print(backtest_time, buy_list)

    # 获取持仓
    hold = get_holdings(g.accid, 'stock')
    need_sell = [s for s in hold if s not in buy_list]
    print('\t\t\t\t\t\t\t', backtest_time, 'sell list', need_sell)

    # 卖出
    for s in need_sell:
        price = g.open_df.loc[backtest_time, s]
        vol = hold[s]['持仓数量']
        passorder(24, 1101, g.accid, s, 11, price, vol, 1,"backtest","小市值",C)

    # 获取持仓
    hold = get_holdings(g.accid, 'stock')
    asset = get_trade_detail_data(g.accid, 'stock', 'account')
    cash = asset[0].m_dAvailable
    buy_num = g.buy_num - len(hold)
    buy_list = [s for s in buy_list if s not in hold]

    # 买入
    if buy_num > 0 and buy_list:
        buy_list = buy_list[:buy_num]
        # money = cash/buy_num
        print(backtest_time, 'buy list', buy_list)
        for s in buy_list:
            price = g.open_df.loc[backtest_time, s]
            if price > 0:
                passorder(23, 1102, g.accid, s, 11, float(price), g.per_money,1,"backtest","小市值",C)

def daily_filter(factor_series, backtest_time):
    # 将 factor_series 中值 True 的index，转化成列表
    print(len(factor_series))
    sl = factor_series[factor_series].index.tolist()
    print(len(sl))
    # exit()
    # st过滤
    sl = [s for s in sl if not is_st(s, backtest_time)]
    sl = sorted(sl, key=lambda k: factor_series.loc[k])
    return sl[:g.buy_num]

def is_st(s, date):
    # 判断某日在历史上是不是st *st
    st_dict = g.his_st.get(s, {})
    if not st_dict:
        return False
    else:
        st = st_dict.get('ST', []) + st_dict.get('*ST', [])
        for start, end in st:
            if start <= date <= end:
                return True

def get_df(dt: dict, df: pd.DataFrame, values_name: str) -> pd.DataFrame:
    '''
    循环从字典里赋值矩阵
    values_name可选字段: ['time', 'stime', 'open', 'high', 'low', 'close', 'volume','amount', 'settelementPrice', 'openInterest', 'preClose', 'suspendFlag']
    '''
    df1 = df.copy()
    df1 = df1.apply(lambda x: dt[x.name][values_name])

    return df1

def get_df_ex(data:dict,field:str) -> pd.DataFrame:

    '''
    ToDo:用于在使用get_market_data_ex的情况下，取到标准df
    
    Args:
        data: get_market_data_ex返回的dict
        field: ['time', 'open', 'high', 'low', 'close', 'volume','amount', 'settelementPrice', 'openInterest', 'preClose', 'suspendFlag']
        
    Return:
        一个以时间为index，标的为columns的df
    '''
    
    _index = data[list(data.keys())[0]].index.tolist()
    _columns = list(data.keys())
    df = pd.DataFrame(index=_index,columns=_columns)
    for i in _columns:
        df[i] = data[i][field]
    return df
        

def rank_filter(df: pd.DataFrame, N: int, axis=1, ascending=False, method="max", na_option="keep") -> pd.DataFrame:
    """
    Args:
        df: 标准数据的df
        N: 判断是否是前N名
        axis: 默认是横向排序
        ascending : 默认是降序排序
        na_option : 默认保留nan值,但不参与排名
    Return:
        pd.DataFrame:一个全是bool值的df
    """
    _df = df.copy()

    _df = _df.rank(axis=axis, ascending=ascending, method=method, na_option=na_option)

    return _df <= N

def filter_opendate_qmt(C, df: pd.DataFrame, n: int) -> pd.DataFrame:
    '''

    ToDo: 判断传入的df.columns中，上市天数是否大于N日，返回的值是一个全是bool值的df

    Args:
        C:contextinfo类
        df:index为时间，columns为stock_code的df,目的是为了和策略中的其他df对齐
        n:用于判断上市天数的参数，如要判断是否上市120天,则填写
    Return:pd.DataFrame

    '''
    local_df = pd.DataFrame(index=df.index, columns=df.columns)
    stock_list = df.columns
    stock_opendate = {i: C.get_instrument_detail(i)["OpenDate"] for i in stock_list}
    # print(type(stock_opendate["000001.SZ"]), stock_opendate["000001.SZ"])
    for stock, date in stock_opendate.items():
        local_df.at[date, stock] = 1
    df_fill = local_df.fillna(method="ffill")

    result = df_fill.expanding().sum() >= n

    return result

def filter_opendate_xt(df: pd.DataFrame, n: int) -> pd.DataFrame:
    '''

    ToDo: 判断传入的df.columns中，上市天数是否大于N日，返回的值是一个全是bool值的df

    Args:
        C:contextinfo类
        df:index为时间，columns为stock_code的df,目的是为了和策略中的其他df对齐
        n:用于判断上市天数的参数，如要判断是否上市120天,则填写
    Return:pd.DataFrame

    '''
    local_df = pd.DataFrame(index=df.index, columns=df.columns)
    stock_list = df.columns
    stock_opendate = {i: xtdata.get_instrument_detail(i)["OpenDate"] for i in stock_list}
    for stock, date in stock_opendate.items():
        local_df.at[date, stock] = 1
    df_fill = local_df.fillna(method="ffill")

    result = df_fill.expanding().sum() >= n

    return result

def get_holdings(accid, datatype):
    '''
    Arg:
        accondid:账户id
        datatype:
            'FUTURE'：期货
            'STOCK'：股票
            ......
    return:
        {股票名:{'手数':int,"持仓成本":float,'浮动盈亏':float,"可用余额":int}}
    '''
    PositionInfo_dict = {}
    resultlist = get_trade_detail_data(accid, datatype, 'POSITION')
    for obj in resultlist:
        PositionInfo_dict[obj.m_strInstrumentID + "." + obj.m_strExchangeID] = {
            "持仓数量": obj.m_nVolume,
            "持仓成本": obj.m_dOpenPrice,
            "浮动盈亏": obj.m_dFloatProfit,
            "可用数量": obj.m_nCanUseVolume
        }
    return PositionInfo_dict

if __name__ == '__main__':
    import sys
    from xtquant.qmttools import run_strategy_file

    # 参数定义方法一，如果使用方法二定义参数，run_strategy_file的param参数可不传
    param = {
        'stock_code': '000300.SH',  # 驱动handlebar的代码,
        'period': '1d',  # 策略执行周期 即主图周期
        'start_time': '2022-01-01 00:00:00',  # 注意格式，不要写错
        'end_time': '2024-03-01 00:00:00',  # 注意格式，不要写错
        'trade_mode': 'backtest',  # 'backtest':回测
        'quote_mode': 'history',
        # handlebar模式，'realtime':仅实时行情（不调用历史行情的handlebar）,'history':仅历史行情, 'all'：所有，即history+realtime
    }
    # user_script = os.path.basename(__file__)  # 当前脚本路径，相对路径，绝对路径均可,此处为相对路径的方法
    user_script = sys.argv[0]  # 当前脚本路径，相对路径，绝对路径均可，此处为绝对路径的方法

    print(user_script)
    result = run_strategy_file(user_script, param=param)
    if result:
        print(result.get_backtest_index())
        print(result.get_group_result())

    xtdata.run()
```

上次更新:

邀请注册送VIP优惠券

分享下方的内容给好友、QQ群、微信群,好友注册您即可获得VIP优惠券

玩转qmt,上迅投qmt知识库

登录后获取

[代码解读_策略回测_投研端](https://dict.thinktrader.net/videos/touyan/ty_code_review_backtest.html)[因子数据_投研端](https://dict.thinktrader.net/videos/touyan/ty_factor_data.html)
