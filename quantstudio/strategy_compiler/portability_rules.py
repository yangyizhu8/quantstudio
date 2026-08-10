"""PTrade 公共 Profile 可移植性规则（单一来源）。

来源：docs/strategy-compiler/ptrade-profile-contract.md 各版本登记 +
      T1 盘点（私募工作文件/QuantStudio本地策略转ptrade模块开发/T1-DENYLIST盘点.md）
      quantstudio/backtest/ptrade_import.py 注入清单逐项分类。

本文件由 source_import（转换器）与 validate_ptrade_portability（校验器）共用，
防止两边清单漂移。修改本文件必须同步 T1 盘点文档与 ptrade-profile-contract.md。
"""

from __future__ import annotations

# ============================================================================
# 分类 1：本地自创/回测辅助 API（真实 PTrade 不存在）→ REMOVE
#   档位由 source_import 按 AST 上下文判定（02 规格 §2 步骤 2）：
#     档 1 裸语句删整行；档 2 表达式内嵌改写为等价字面量；档 3 语义不明 → BLOCK
# ============================================================================
DENY_REMOVE: frozenset[str] = frozenset({
    "set_backtest",          # 本地引擎自创（ptrade_api.py:2144 lambda），档 2 等价字面量 None
    "is_trade",              # 本地回测模式标记（ptrade_api.py:2194），档 2 等价字面量 False
    "get_etf_list_local",    # 本地 ETF 列表扩展（profile ETF split：QS-only）
    "get_strategy_events",   # 本地事件扩展（未在 profile 登记）
    "create_dir",            # 本地文件目录创建（profile 禁止本地文件访问；删除不影响策略逻辑）
    "get_research_path",     # 本地研究目录（同 create_dir）
})

# ============================================================================
# 分类 2：本地批量性能 API（B1，本地优化）→ SHIM
#   注入同名 shim 函数：循环单调用 + 原返回形状拼接（02 规格 §2 步骤 7）
# ============================================================================
DENY_SHIM: frozenset[str] = frozenset({
    "get_fundamentals_batch",  # shim 形状：dict[code→DataFrame]（与 get_fundamentals is_dataframe=True 一致）
    "get_history_batch",       # shim 形状：dict[code→DataFrame]（与 get_history is_dict=True 一致）
})

# ============================================================================
# 分类 3：无法自动处理、必须人工 → BLOCK（fail-closed；不在 1:1 承诺内）
#   T1 复核：get_trades_file/convert_position_from_csv 策略可能依赖返回值，
#   从 REMOVE 升为 BLOCK（N3）。
# ============================================================================
DENY_BLOCK: frozenset[str] = frozenset({
    "load_research_signals",      # 框架侧 CSV 研报 I/O（ptrade_api.py 标注 LOCAL_ONLY），需人工改用 PTrade 数据源
    "get_trades_file",            # 本地成交对账 CSV 导出，策略可能消费返回值
    "convert_position_from_csv",  # 本地底仓 CSV 导入，策略可能消费返回值
    "SharedCostModel",            # 本地成本模型类（依赖本地配置与引擎语义）
})

# ============================================================================
# 分类 4：已登记 PTrade API 但本地语义/平台行为有差异 → 保留 + WARN
#   （PTRADE_RUNTIME_UNVERIFIED：真实券商平台行为按部署核实）
# ============================================================================
PTRADE_REGISTERED_WARN: frozenset[str] = frozenset({
    # profile 1.7.0/1.8.0/1.9.0/1.10.0 已登记（语义差异已入契约）
    "set_benchmark", "run_daily", "get_Ashares", "get_index_stocks",
    "get_stock_status", "get_positions", "get_position", "get_trade_days",
    "get_fundamentals", "get_history", "get_industry", "get_stock_info",
    "get_stock_exrights",
    # PTrade 平台同名公共 API（本地同名实现，行为差异待真实平台核实）
    "get_price", "attribute_history", "current_price", "get_current_data",
    "get_snapshot", "order", "order_value", "order_target", "order_target_value",
    "order_at_price", "cancel_order", "get_orders", "get_trades",
    "get_open_orders", "get_order", "get_trading_day", "get_all_trades_days",
    "get_trading_day_by_date", "set_universe", "set_limit_mode", "set_slippage",
    "set_fixed_slippage", "set_commission", "set_volume_ratio",
    "set_yesterday_position", "get_stock_name", "get_security_info",
    "get_MACD", "get_KDJ", "get_RSI", "get_CCI", "get_frequency",
    "get_business_type", "filter_stock_by_status", "check_limit",
    "get_stock_blocks", "get_industry_stocks", "get_reits_list", "get_ipo_stocks",
    "get_etf_list", "get_etf_info", "get_etf_stock_list", "get_etf_stock_info",
    "get_cb_list", "get_cb_info", "get_market_list", "get_market_detail",
    "get_trend_data", "get_instruments", "get_dominant_contract",
    "get_margin_rate", "get_underlying_code", "set_parameters", "get_user_name",
    "get_current_kline_count", "get_all_positions",
    "query", "valuation", "g", "log",
})

# ============================================================================
# 参数归一化规则表（H1：有序 list，禁止 dict 嵌套——同参名键覆盖会吞规则）
# 元组结构：(api_name, param, old_value, new_value, rule_id, default_grade)
#   default_grade:
#     "NORMALIZE" = 直接改写（仅限有代码级证据、确认语义等价的规则）
#     "WARN_KEEP" = 保留原值 + WARN + approximation（语义等价性未证实）
#
# G1 证据（T1 §3）：duckdb_provider.py:61,83 use_qfq = fq.lower() in ("pre","dypre")
#   同一分支 → dypre≡pre 等价成立 → NORMALIZE；
#   'dypost' 不在 data_access 任何分支（610/615/705/710）→ 本地实际=不复权，
#   与 'post' 不等价 → 维持 WARN_KEEP（改写会破坏 round-trip 1:1）。
# ============================================================================
NORMALIZE_RULES: list[tuple[str, str, object, object, str, str]] = [
    ("get_history", "fq", "dypre",  "pre",  "NORM-FQ-DYPRE",  "NORMALIZE"),
    ("get_history", "fq", "dypost", "post", "NORM-FQ-DYPOST", "WARN_KEEP"),
    ("get_price",   "fq", "dypre",  "pre",  "NORM-FQ-DYPRE",  "NORMALIZE"),
    ("get_price",   "fq", "dypost", "post", "NORM-FQ-DYPOST", "WARN_KEEP"),
]

# PTrade 不支持的 get_price 参数名 → REMOVE 该 keyword（INFO 级，无漂移风险）
GET_PRICE_DROP_PARAMS: frozenset[str] = frozenset({
    "panel", "fill_paused", "skip_paused",
})

# ============================================================================
# 本地纯计算库（真实 PTrade 无，但无平台依赖 → 检测使用后注入源码 INJECT）
#   注入后该策略不在 1:1 承诺内（源码在 PTrade 平台行为需验证）。
#   注意：MyTT.MACD/RSI 与 PTrade 内置 get_MACD/get_RSI 同名不同实现，
#   注入时函数名加 "_mytt_" 前缀（N4）。
# ============================================================================
MYTT_FUNCTIONS: frozenset[str] = frozenset({
    "RD", "RET", "LAST", "REF", "DIFF", "STD", "SUM", "IF", "MAX", "MIN",
    "ABS", "LN", "POW", "SQRT", "SIN", "COS", "TAN", "CONST",
    "HHV", "LLV", "HHVBARS", "LLVBARS", "AVEDEV", "SLOPE", "FORCAST",
    "COUNT", "EVERY", "EXIST", "FILTER", "BARSLAST", "BARSLASTCOUNT",
    "CROSS", "LONGCROSS", "VALUEWHEN", "BETWEEN", "TOPRANGE", "LOWRANGE",
    "MA", "SMA", "EMA", "WMA", "DMA", "MACD", "KDJ", "RSI", "WR", "BIAS",
    "BOLL", "PSY", "CCI", "ATR", "BBI", "DMI", "TRIX", "CR", "EMV", "DPO",
    "BRAR", "MTM", "MASS", "ROC", "EXPMA", "OBV", "MFI", "ASI", "SAR",
})

ASHARE_RULES_FUNCTIONS: frozenset[str] = frozenset({
    "is_price_limit_blocked", "is_t1_blocked", "round_to_lot",
    "get_price_limit_pct", "is_star_market", "is_chinext_market",
    "is_bse_market", "is_st_stock",
})

# ============================================================================
# 转换产物头部必须包含的 profile 标记
# （validate_ptrade_portability 检查前 500 字符；转换器自检兜底，02 规格 §2 步骤 8）
# ============================================================================
PTRADE_PROFILE_MARKER = "ptrade-default"

# 转换产物注入函数的标记头（幂等性依据：再次转换时识别已注入代码）
INJECTED_MARKER = "# [qs-import-generated]"


def denylist() -> frozenset[str]:
    """全部禁止/需处理 API 并集（校验器用）。"""
    return DENY_REMOVE | DENY_SHIM | DENY_BLOCK
