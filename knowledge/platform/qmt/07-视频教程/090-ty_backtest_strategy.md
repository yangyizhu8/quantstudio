---
title: "# 策略回测"
source_url: "https://dict.thinktrader.net/videos/touyan/ty_backtest_strategy.html"
crawled_at: 2026-10-12T03:03:21+08:00
platform: QMT
category: 07-视频教程
depth: 3
tags: [平台/QMT, 状态/已抓取]
---

# [#](https://dict.thinktrader.net/videos/touyan/ty_backtest_strategy.html#策略回测) 策略回测

策略源码

```
# coding:gbk

import pandas as pd
import numpy as np

class G():
	pass

g = G()

def init(C):
	# ------------------------参数设定-----------------------------
	g.his_st = {}
	g.s = get_stock_list_in_sector("上证50")  # 获取沪深A股股票列表
	# g.s = get_stock_list_in_sector("沪深300")  # 获取沪深300股票列表
	# print(g.s)
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
	data = C.get_market_data_ex([], g.s, period='1d', dividend_type='front_ratio',
										   fill_data=True)
	close_df = get_df_ex(data,"close")
	# print(close_df)
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

	# 使用字典推导来获取每个股票的TotalVolume，注意在内置 Python 环境的拼写
	total_volumes = {stock: C.get_instrumentdetail(stock)['TotalVolumn'] for stock in g.s}
	# print(total_volumes)

	# 将字典转换为DataFrame，但先转化为一个嵌套字典
	df_total_volume = pd.DataFrame({k: v for k, v in total_volumes.items()}, index=['TotalVolumn'])
	# print(df_total_volume)
	# exit()

	# ------------------------财务数据获取-----------------------------

	# ------------------------因子1计算及处理--------------------------------
	# 1. 市值因子: 用市值因子 = 股票收盘价 * 股票总股本，要利用好，要求对应列名相乘
	factor = close_df * df_total_volume.loc['TotalVolumn']
	# print(factor)
	# exit()

	# ------------------------因子2计算及处理--------------------------------
	# 判断 close_df 中的 code 上市时间大于120天
	stock_opendate_filter = filter_opendate_qmt(C, close_df, 120)
	# print(stock_opendate_filter)

	# ------------------------上市日期过滤处理--------------------------------
	# 两个布尔值的 DataFrame 对应相乘过滤掉每个交易日上市不足120天的
	factor *= stock_opendate_filter.astype(int).replace(0, np.nan)
	# print(factor)
	# ------------------------排序处理-----------------------------------
	# 对 factor 每行在一行内进行排序
	factor_sorted = rank_filter(factor, 10, ascending=True, method='min', na_option='keep')
	# print(factor_sorted)
	# exit()

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
	# print(g.factor_df)
	# print(backtest_time)
	factor_series = g.factor_df.loc[backtest_time]
	# factor_series.sort_values(ascending=True,inplace=True)
	# sl = factor_series.index.tolist()
	buy_list = daily_filter(factor_series, backtest_time)
	print(backtest_time, buy_list)
	# exit()

	# 获取持仓
	hold = get_holdings(g.accid, 'stock')
	need_sell = [s for s in hold if s not in buy_list]
	print('\t\t\t\t\t\t\t', backtest_time, 'sell list', need_sell)

	# 卖出
	for s in need_sell:
		price = g.open_df.loc[backtest_time, s]
		vol = hold[s]['持仓数量']
		passorder(24, 1101, g.accid, s, 11, price, vol, C)

	# 获取持仓
	hold = get_holdings(g.accid, 'stock')
	asset = get_trade_detail_data(g.accid, 'stock', 'account')
	# cash = asset[0].m_dAvailable
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
				passorder(23, 1102, g.accid, s, 11, float(price), g.per_money, C)

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

def filter_opendate_qmt(C, df: pd.DataFrame, n: int) -> pd.DataFrame:
    '''

    ToDo: 判断传入的df.columns中，上市天数是否大于N日，返回的值是一个全是bool值的df

    Args:
        C:contextinfo类
        df:index为时间，columns为stock_code的df,目的是为了和策略中的其他df对齐
        n:用于判断上市天数的参数，如要判断是否上市120天,则填写
    Return:pd.DataFrame

    '''
    # print(df.index)
    local_df = pd.DataFrame(index=df.index, columns=df.columns)
    # print(local_df)
    # print(type(list(local_df.index)[0]))
    stock_list = df.columns
    # 这里的索引数据类型不一样
    stock_opendate = {i: str(C.get_instrumentdetail(i)["OpenDate"]) for i in stock_list}
    # stock_opendate = {i: C.get_instrumentdetail(i)["OpenDate"] for i in stock_list}
    # print(type(stock_opendate["000001.SZ"]), stock_opendate["000001.SZ"])
    # print("+================================+\n")
    
    for stock, date in stock_opendate.items():
        local_df.at[date, stock] = 1
    
    df_fill = local_df.fillna(method="ffill")

    result = df_fill.expanding().sum() >= n
    # print(result)
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
```

上次更新:

邀请注册送VIP优惠券

分享下方的内容给好友、QQ群、微信群,好友注册您即可获得VIP优惠券

玩转qmt,上迅投qmt知识库

登录后获取

[添加指标_投研端](https://dict.thinktrader.net/videos/touyan/ty_add_indicator.html)[代码解读_策略回测_投研端](https://dict.thinktrader.net/videos/touyan/ty_code_review_backtest.html)
