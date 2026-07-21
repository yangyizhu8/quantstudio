"""
Ptrade 统一导入层 — 策略文件无需任何 import 语句

策略文件头部不需要写 from xxx import *，
引擎加载策略时自动注入这里的全部名称。

包含：
  - Ptrade API 函数（50+ 个）
  - MyTT 技术指标库（50+ 个）
  - shared_ashare_rules A股规则函数
  - pandas / numpy（策略常用）
  - g / log 全局对象
"""

# ===== Ptrade API =====
from .ptrade_api import (
    set_benchmark, set_limit_mode, set_universe,
    set_commission, set_slippage, set_fixed_slippage, set_backtest,
    set_volume_ratio, set_yesterday_position,
    get_index_stocks, get_fundamentals, filter_stock_by_status,
    check_limit, get_positions, get_position,
    get_history, get_price, attribute_history,
    current_price, get_current_data, get_snapshot,
    get_trading_day, get_trade_days, get_all_trades_days, get_trading_day_by_date,
    run_daily, get_Ashares,
    get_stock_name, get_stock_info, get_stock_status,
    get_security_info, get_industry,
    get_orders, get_trades, get_open_orders, get_order, cancel_order,
    get_frequency, get_business_type,
    get_MACD, get_KDJ, get_RSI, get_CCI,
    order_target_value, order, order_value, order_target,
    is_trade, g, log,
    # 第2批新增：财务/除权/板块/行业/ETF/可转债
    get_stock_exrights, get_stock_blocks, get_industry_stocks, get_reits_list,
    get_etf_list, get_etf_info, get_etf_stock_list, get_etf_stock_info, get_ipo_stocks,
    get_cb_list, get_cb_info,
    # 第3批新增：市场/文件/持仓/参数/期货降级
    get_market_list, get_market_detail, get_trend_data,
    create_dir, get_trades_file, get_all_positions, convert_position_from_csv,
    set_parameters, get_instruments, get_dominant_contract, get_margin_rate,
    get_underlying_code, get_user_name, get_research_path, get_current_kline_count,
    # 第4批新增：ORM 查询（query/valuation）
    query, valuation,
    # 第5批新增：B1 批量取数 API（性能优化，策略可选）
    get_fundamentals_batch, get_history_batch,
)

# ===== MyTT 技术指标库 =====
from .libs.MyTT import (
    RD, RET, LAST, REF, DIFF, STD, SUM, IF, MAX, MIN,
    ABS, LN, POW, SQRT, SIN, COS, TAN, CONST,
    HHV, LLV, HHVBARS, LLVBARS, AVEDEV, SLOPE, FORCAST,
    COUNT, EVERY, EXIST, FILTER, BARSLAST, BARSLASTCOUNT,
    CROSS, LONGCROSS, VALUEWHEN, BETWEEN, TOPRANGE, LOWRANGE,
    MA, SMA, EMA, WMA, DMA, MACD, KDJ, RSI, WR, BIAS,
    BOLL, PSY, CCI, ATR, BBI, DMI, TRIX, CR, EMV, DPO,
    BRAR, MTM, MASS, ROC, EXPMA, OBV, MFI, ASI, SAR,
)

# ===== A股交易规则 =====
from .libs.shared_ashare_rules import (
    is_price_limit_blocked, is_t1_blocked, round_to_lot,
    get_price_limit_pct, is_star_market, is_chinext_market,
    is_bse_market, is_st_stock,
)

# ===== 成本模型 =====
from .libs.shared_cost_model import SharedCostModel

# ===== 标准库 =====
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)
