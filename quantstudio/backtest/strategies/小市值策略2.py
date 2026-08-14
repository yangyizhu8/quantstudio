"""
策略名称: 小市值日线多因子策略 (三项优化版)
优化内容: 
  1. 市场强势时提高仓位 (牛市1.2倍)
  2. 7月和9月空仓
  3. 止盈比例放宽至22%
备注：代码仅作学习使用，不做投资参考，注意风险
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


def initialize(context):
    """初始化策略参数"""
    set_benchmark('000300.XSHG')
    g.index = '399101.XSHE'               # 中小板综

    # --- 核心参数 ---
    g.buy_stock_count = 5                 # 最终持仓数量
    g.initial_pool_size = 50              # 初始候选池大小

    # --- 打分权重 ---
    g.market_cap_weight = 0.6             # 市值因子权重
    g.reversal_weight = 0.4               # 反转因子权重

    # --- 仓位与风控（基准参数，运行中会动态调整） ---
    g.base_max_single_position = 0.10     # 基础单只最大仓位：10%
    g.max_industry_exposure = 0.30        # 单行业最大暴露

    # 止盈止损参数
    g.stop_loss_percent = 0.05            # 止损比例：5%
    g.take_profit_percent = 0.22          # 止盈比例：22%

    # 记录上次调仓日期以及每只股票的买入成本
    g.last_rebalance_date = None
    g.stock_cost = {}                     # 记录每只股票的成本价

    # 记录历史最高净值（用于计算当前回撤）
    g.high_water_mark = None

    set_limit_mode("UNLIMITED")
    log.info("【动态仓位管理版】小市值策略初始化完成")

def before_trading_start(context, data):
    """
    盘前：构建股票池
    步骤：获取指数成分股 -> 剔除ST/停牌/退市/次新股 -> 按流通市值取前N只
    """
    # 1. 获取指数成分股
    all_stocks = get_index_stocks(g.index)

    # 2. 剔除ST、停牌、退市
    valid_stocks = filter_stock_by_status(
        all_stocks,
        filter_type=["ST", "HALT", "DELISTING"],
        query_date=None
    )

    # 3. 剔除上市不足60天的新股
    sixty_days_ago = context.current_dt - timedelta(days=60)
    filtered_stocks = []
    for stock in valid_stocks:
        try:
            listing_date = get_security_info(stock).start_date
            if listing_date and listing_date <= sixty_days_ago:
                filtered_stocks.append(stock)
        except:
            filtered_stocks.append(stock)  # 获取失败则保留

    # 4. 获取流通市值，按升序取前N只
    try:
        fundamentals_df = get_fundamentals(
            filtered_stocks,
            "valuation",
            fields=["float_value"],
            date=context.previous_date
        )
        fundamentals_df = fundamentals_df[fundamentals_df['float_value'] > 0]
        fundamentals_df = fundamentals_df.sort_values(by='float_value')
        g.stock_pool_df = fundamentals_df.head(g.initial_pool_size)
        g.stock_pool_list = g.stock_pool_df.index.tolist()
    except Exception as e:
        log.error(f"盘前获取财务数据失败: {e}")
        g.stock_pool_df = None
        g.stock_pool_list = []

    log.info(f"盘前股票池构建完成，候选池数量: {len(g.stock_pool_list)}")


def handle_data(context, data):
    """盘中：止盈止损检查 + 每周调仓"""
    # 止盈止损检查
    check_stop_loss_and_take_profit(context, data)
    # 每周调仓（仅在调仓日执行）
    weekly_rebalance(context, data)


def check_stop_loss_and_take_profit(context, data):
    """
    统一的止盈止损检查函数
    先检查是否触发止盈（盈利>=22%），再检查是否触发止损（亏损>=5%）
    """
    positions = context.portfolio.positions
    for stock in list(positions.keys()):
        try:
            # 获取当前价格
            current_price = data[stock].close

            # 获取该股票的记录成本价
            cost_price = g.stock_cost.get(stock)
            if cost_price is None or cost_price <= 0:
                continue

            # ---- 1. 先检查止盈（优化3：改为22%） ----
            profit_percent = (current_price - cost_price) / cost_price
            if profit_percent >= g.take_profit_percent:
                log.info(f"22%止盈触发: {stock}, 成本价 {cost_price:.4f}, 当前价 {current_price:.4f}, 盈利 {profit_percent:.2%}")
                order_target_value(stock, 0)
                # 卖出后清除成本记录
                if stock in g.stock_cost:
                    del g.stock_cost[stock]
                continue  # 已经卖出，无需再检查止损

            # ---- 2. 再检查止损 ----
            loss_percent = (cost_price - current_price) / cost_price
            if loss_percent >= g.stop_loss_percent:
                log.info(f"5%止损触发: {stock}, 成本价 {cost_price:.4f}, 当前价 {current_price:.4f}, 跌幅 {loss_percent:.2%}")
                order_target_value(stock, 0)
                # 卖出后清除成本记录
                if stock in g.stock_cost:
                    del g.stock_cost[stock]

        except Exception as e:
            log.warning(f"止盈止损检查 {stock} 出错: {e}")


def weekly_rebalance(context, data):
    """每周调仓"""
    # 判断是否为调仓日
    if g.last_rebalance_date is None:
        should_rebalance = True
    else:
        should_rebalance = (context.current_dt - g.last_rebalance_date).days >= 7

    if not should_rebalance:
        return

    # 【优化2】判断是否为空仓月份（7月和9月）
    if is_avoid_month(context):
        log.info(f"当前处于空仓月份（7月或9月），强制卖出所有持仓，不进行买入操作")
        # 卖出所有持仓
        for stock in list(context.portfolio.positions.keys()):
            order_target_value(stock, 0)
            if stock in g.stock_cost:
                del g.stock_cost[stock]
        g.last_rebalance_date = context.current_dt
        return

    # 使用盘前构建的股票池
    if not g.stock_pool_list:
        log.warning("盘前股票池为空，跳过调仓")
        return

    # 打分选股
    final_target_stocks = score_stocks(g.stock_pool_list, context, data)

    if not final_target_stocks:
        log.warning("打分后无目标股票，跳过调仓")
        return

    log.info(f"本周目标股票: {final_target_stocks}")

    # 【优化1】判断市场状态，强势时提高仓位
    market_strength = get_market_strength(context)
    if market_strength == 'strong':
        # 强势市场：仓位提高到120%（可适当使用杠杆或提高单票仓位比例）
        position_ratio = 1.2
        log.info("市场强势，仓位提高至120%")
    elif market_strength == 'bull':
        position_ratio = 1.0
        log.info("市场多头趋势，仓位100%")
    else:
        position_ratio = 0.5
        log.info("市场空头趋势，仓位减半至50%")

    # 执行交易
    execute_trades(context, final_target_stocks, position_ratio)
    g.last_rebalance_date = context.current_dt


def is_avoid_month(context):
    """
    【优化2】判断当前是否为空仓月份
    在7月和9月强制空仓
    """
    month = context.current_dt.month
    if month == 7 or month == 9:
        return True
    return False


def get_market_strength(context):
    """
    【优化1】判断市场强势程度
    使用MA20判断：价格远高于MA20为强势，高于MA20为多头，低于为空头
    """
    try:
        # 使用MA20更敏感地捕捉短期强势
        close_data = get_history(21, '1d', 'close', '000300.XSHG', fq='pre', include=False)
        if close_data is None or close_data.empty or len(close_data) < 21:
            return 'bull'
        
        ma20 = close_data['close'].mean()
        current_price = close_data['close'].iloc[-1]
        
        # 价格超过MA20的5%以上，视为强势
        if current_price > ma20 * 1.05:
            return 'strong'
        elif current_price > ma20:
            return 'bull'
        else:
            return 'bear'
    except:
        return 'bull'


def score_stocks(stock_list, context, data):
    """
    简化打分函数（含PE筛选）
    剔除PE为负或极端高(>200)的股票，然后按市值+反转因子打分
    """
    scores = {}
    for stock in stock_list:
        try:
            # --- PE筛选 ---
            pe_ttm_df = get_fundamentals(
                [stock], "valuation", fields=["pe_ttm"], date=context.current_dt
            )
            if pe_ttm_df is None or pe_ttm_df.empty:
                continue
            pe_ttm = pe_ttm_df['pe_ttm'].values
            # 剔除PE为负或极端高（>200）的股票
            if pd.isna(pe_ttm) or pe_ttm <= 0 or pe_ttm >= 200:
                continue

            # --- 流通市值因子 ---
            float_value_df = get_fundamentals(
                [stock], "valuation", fields=["float_value"], date=context.current_dt
            )
            if float_value_df is None or float_value_df.empty:
                continue
            float_value = float_value_df['float_value'].values
            if pd.isna(float_value) or float_value <= 0:
                continue

            # --- 反转因子（过去20日收益率） ---
            close_data = get_history(21, '1d', 'close', [stock], fq='pre', include=False)
            if close_data is None or close_data.empty or len(close_data) < 21:
                continue
            past_20d_return = (close_data['close'].iloc[-1] / close_data['close'].iloc[0] - 1)

            # 记录该股票的原始数据
            scores[stock] = {
                'float_value': float_value,
                'past_20d_return': past_20d_return
            }
        except Exception as e:
            continue

    if not scores:
        log.warning("所有股票打分失败，无目标股票")
        return []

    # 转换为DataFrame进行打分排序
    scores_df = pd.DataFrame(scores).T

    # 排序打分（市值越小、过去20日跌幅越大，得分越高）
    scores_df['float_value_rank'] = scores_df['float_value'].rank(ascending=True)
    scores_df['return_rank'] = scores_df['past_20d_return'].rank(ascending=True)

    # 归一化得分 (0~1之间)
    max_float_rank = scores_df['float_value_rank'].max()
    max_return_rank = scores_df['return_rank'].max()
    scores_df['market_cap_score'] = scores_df['float_value_rank'] / max_float_rank if max_float_rank > 0 else 0
    scores_df['reversal_score'] = 1 - (scores_df['return_rank'] / max_return_rank) if max_return_rank > 0 else 0

    # 综合打分（权重与原策略一致）
    scores_df['total_score'] = (
        scores_df['market_cap_score'] * g.market_cap_weight +
        scores_df['reversal_score'] * g.reversal_weight
    )

    # 按综合得分降序排列，选出前N只
    final_stocks = scores_df.sort_values(by='total_score', ascending=False).head(g.buy_stock_count).index.tolist()
    log.info(f"打分完成，选出 {len(final_stocks)} 只目标股票")
    return final_stocks


def execute_trades(context, target_stocks, position_ratio):
    """执行交易（卖出不在目标池的持仓，等权重买入目标股，并记录成本）"""
    current_positions = {pos.sid: pos for pos in context.portfolio.positions.values() if pos.amount > 0}
    current_holdings = set(current_positions.keys())
    target_set = set(target_stocks)
    total_assets = context.portfolio.total_value
    
    # 更新历史最高净值
    if g.high_water_mark is None or total_assets > g.high_water_mark:
        g.high_water_mark = total_assets
    
    # ===== 【新增】根据当前回撤动态调整单票仓位 =====
    current_drawdown = 1 - total_assets / g.high_water_mark if g.high_water_mark > 0 else 0
    
    if current_drawdown < 0.10:
        # 回撤小于10%，使用正常仓位
        g.max_single_position = g.base_max_single_position
        log.info(f"当前回撤 {current_drawdown:.2%} < 10%，单票仓位保持 {g.max_single_position:.0%}")
    elif current_drawdown < 0.15:
        # 回撤在10%~15%之间，降低仓位至7%
        g.max_single_position = 0.07
        log.info(f"当前回撤 {current_drawdown:.2%} (10%~15%)，单票仓位降至 {g.max_single_position:.0%}")
    else:
        # 回撤超过15%，降低仓位至5%
        g.max_single_position = 0.05
        log.info(f"当前回撤 {current_drawdown:.2%} >= 15%，单票仓位降至 {g.max_single_position:.0%}")
    
    # ===== 以下代码与之前完全一致 =====
    target_total_value = total_assets * position_ratio

    # 卖出不在目标池的持仓，并清除成本记录
    stocks_to_sell = current_holdings - target_set
    for stock in list(stocks_to_sell):
        log.info(f"卖出: {stock}")
        order_target_value(stock, 0)
        if stock in g.stock_cost:
            del g.stock_cost[stock]

    # 买入
    if len(target_set) > 0:
        per_stock_value_base = target_total_value / len(target_set)
        per_stock_value_base = min(per_stock_value_base, total_assets * g.max_single_position)

        # 简化行业限制检查
        final_buy_list = []
        for stock in target_set:
            try:
                industry = get_industry(stock)
                if industry and industry.get('sw_l1'):
                    industry_code = industry['sw_l1']['industry_code']
                else:
                    industry_code = 'uncategorized'

                current_industry_value = sum(
                    current_positions[s].market_value for s in current_holdings if
                    get_industry(s) and get_industry(s).get('sw_l1') and get_industry(s)['sw_l1']['industry_code'] == industry_code
                )
                if (current_industry_value + per_stock_value_base) > (total_assets * g.max_industry_exposure):
                    log.info(f"行业暴露限制: {stock}({industry_code}), 跳过买入")
                    continue
                final_buy_list.append(stock)
            except:
                final_buy_list.append(stock)

        if len(final_buy_list) > 0:
            per_stock_value = min(target_total_value / len(final_buy_list), total_assets * g.max_single_position)
            for stock in final_buy_list:
                if stock not in current_positions:
                    order_target_value(stock, per_stock_value)
                    log.info(f"买入: {stock}, 目标价值: {per_stock_value:.2f}")
                    
                    # 记录买入成本价（以收盘价近似作为成本）
                    try:
                        g.stock_cost[stock] = data[stock].close
                    except:
                        g.stock_cost[stock] = 0
