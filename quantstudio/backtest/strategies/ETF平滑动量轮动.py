"""
ETF平滑动量轮动.py - ETF Smooth Momentum Rotation

基于编译器生成的策略，修复了 ETF 数据访问问题：
1. 使用 .SS/.XSHE (Ptrade风格) 后缀
2. 使用 get_history 逐只查询（支持 etf_daily 表）
3. 补充了趋势过滤(close > MA20 > MA60)
4. 补充了防御模式（全仓货币基金）

策略逻辑：
- 静态ETF池（37只宽基+行业+货币基金）
- 第一轮：close > MA20 > MA60 多头排列趋势过滤
- 第二轮：25日涨幅排名（近似 年化收益率×R²）
- Top 3 等权持仓
- 无标的通过趋势过滤时 → 全仓防御资产(511880.SS 银华日利)
"""

import numpy as np


def initialize(context):
    g.stock_list = [
        '510050.SS', '510300.SS', '510500.SS', '510310.SS', '510330.SS', '510880.SS',
        '159901.XSHE', '159905.XSHE', '159915.XSHE', '159919.XSHE', '159949.XSHE',
        '588000.SS', '588050.SS', '588080.SS', '159952.XSHE', '560010.SS', '560030.SS',
        '512010.SS', '159938.XSHE', '513050.SS', '159992.XSHE',
        '159928.XSHE', '510150.SS',
        '512480.SS', '512760.SS', '159995.XSHE', '515050.SS',
        '516160.SS', '515790.SS',
        '512660.SS', '512710.SS',
        '512800.SS', '512000.SS', '515020.SS',
        '511880.SS', '511990.SS', '511620.SS',
    ]
    g.max_positions = 3
    g.top_n = 3
    g.lookback = 120
    g.defensive = '511880.SS'

    set_universe(g.stock_list)
    set_limit_mode('UNLIMITED')
    set_commission(commission_ratio=0.00035, min_commission=5.0, type="ETF")


def before_trading_start(context, data):
    g.scores = {}
    g.filtered_list = []

    for code in g.stock_list:
        try:
            hist = get_history(60, '1d', field='close', security_list=code, fq='pre', include=False)
            if hist is None or len(hist) < 60:
                continue

            closes = np.array(hist, dtype=float)
            curr_close = closes[-1]
            ma20 = np.mean(closes[-20:])
            ma60 = np.mean(closes[-60:])

            if curr_close > ma20 and ma20 > ma60:
                ret_25d = (closes[-1] - closes[-25]) / closes[-25]
                g.scores[code] = ret_25d
                g.filtered_list.append(code)
            else:
                log.info(f"{code} 未通过趋势过滤: close={curr_close:.3f} MA20={ma20:.3f} MA60={ma60:.3f}")
        except Exception as e:
            log.info(f"{code} 数据获取失败: {e}")
            continue

    g.sorted_codes = sorted(g.scores.keys(), key=lambda c: g.scores[c], reverse=True)
    log.info(f"通过趋势过滤: {len(g.filtered_list)} 只, Top3: {g.sorted_codes[:3]}")


def handle_data(context, data):
    selected = g.sorted_codes[:g.top_n]

    if not selected:
        log.info("无标的通过趋势过滤，进入防御模式")
        for code in list(context.portfolio.positions.keys()):
            if code != g.defensive:
                order_target_value(code, 0)
        if g.defensive not in context.portfolio.positions or \
           context.portfolio.positions[g.defensive].value < context.portfolio.total_value * 0.9:
            order_target_value(g.defensive, context.portfolio.total_value)
        return

    for code in list(context.portfolio.positions.keys()):
        if code not in selected:
            order_target_value(code, 0)
            log.info(f"卖出 {code}")

    position_count = sum(1 for c in context.portfolio.positions if c in selected)
    if position_count < g.max_positions:
        value = context.portfolio.cash / (g.max_positions - position_count)
        for code in selected:
            if code not in context.portfolio.positions:
                order_value(code, value)
                log.info(f"买入 {code}, 动量分={g.scores.get(code, 0):.4f}")
