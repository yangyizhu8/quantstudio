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
#   P-D10（2026-08-22）：shim 返回形状必须与本地 B1 契约逐字段一致——
#   get_fundamentals_batch 本地契约为合并 DataFrame（index=code, columns=fields，
#   ptrade_api.py:1421-1442，平台原生 list 单调用 + 列筛选 + end_date/publ_date 数值归一）；
#   get_history_batch 本地契约为 dict[code→DataFrame]（ptrade_api.py:1444-1476）。
#   契约唯一真相见 SHIM_CONTRACT_REGISTRY（下方），校验器据此对产物中未登记
#   的注入 shim/wrapper def 施 BLOCK（PORTABILITY-UNREGISTERED-SHIM）。
# ============================================================================
DENY_SHIM: frozenset[str] = frozenset({
    "get_fundamentals_batch",  # shim 形状：DataFrame（index=code, columns=fields，本地 B1 契约）
    "get_history_batch",       # shim 形状：dict[code→DataFrame]（与 get_history is_dict=True 一致）
})

# 注入同名 wrapper 的平台登记 API 名（策略代码零改动，转换侧包装平台行为）
# 与 DENY_SHIM 并集 = SHIM_CONTRACT_REGISTRY 的键集合（测试断言集合相等，防双边漂移）
INJECTED_WRAPPER_NAMES: frozenset[str] = frozenset({
    "get_history",              # 方向B：structured array → DataFrame + 字段双向映射
    "get_fundamentals",         # P-D10：list 单调用 + 列筛选 + end_date/publ_date 数值归一
    "filter_stock_by_status",   # P-D9：'ST' 语义补退市风险兜底
    "get_trade_days",           # A3：日历格式归一 + 未来过滤
    "get_stock_info",           # A3：listed_date 归一
})

from dataclasses import dataclass


@dataclass(frozen=True)
class ShimContractSpec:
    """注入 shim/wrapper 必须满足的本地契约（四要素：type/index/columns/空行为）。

    P-D10 三道防线之①（机器门禁）：校验器对产物中出现的注入 def，
    若不在 SHIM_CONTRACT_REGISTRY 内 → BLOCK（PORTABILITY-UNREGISTERED-SHIM）。
    新增任何注入模板必须先在本注册表登记 + 补四要素同构测试，否则校验失败。
    """
    api_name: str
    contract_type: str       # DataFrame / dict[code→DataFrame] / list / ...
    contract_index: str      # index 契约（ptrade_code / code→DataFrame / n/a）
    contract_columns: str    # columns 契约（fields 请求字段 / 字段映射 / n/a）
    contract_empty: str      # 空行为契约
    contract_source: str     # 契约出处（ptrade_api.py 等行号）
    template_location: str   # source_import 模板常量名
    homology_test: str       # 四要素同构测试名（tests/test_ptrade_contract_compliance.py）


SHIM_CONTRACT_REGISTRY: dict[str, ShimContractSpec] = {
    "get_fundamentals": ShimContractSpec(
        api_name="get_fundamentals",
        contract_type="DataFrame",
        contract_index="ptrade_code",
        contract_columns="fields（按请求字段筛选；end_date/publ_date 归一为数值 YYYYMMDD；or_yoy→operating_revenue_grow_rate 字段名映射）",
        contract_empty="空 DataFrame(columns=fields)，不抛错；请求字段缺失 → QS_SHIM_FIELD_MISSING 显性警报",
        contract_source="ptrade_api.py:698-772 / 1421-1442",
        template_location="_QS_FUNDAMENTALS_EXT",
        homology_test="test_p10_wrapper_native_list_index_preserved",
    ),
    "get_fundamentals_batch": ShimContractSpec(
        api_name="get_fundamentals_batch",
        contract_type="DataFrame",
        contract_index="ptrade_code",
        contract_columns="fields（按请求字段筛选）",
        contract_empty="空 DataFrame(columns=fields)，不抛错",
        contract_source="ptrade_api.py:1421-1442",
        template_location="_shim_source('get_fundamentals_batch')",
        homology_test="test_p10_batch_shim_returns_dataframe_index_code",
    ),
    "get_history": ShimContractSpec(
        api_name="get_history",
        contract_type="DataFrame（单标的）/ dict[code→DataFrame]（is_dict）",
        contract_index="平台原生（日期可能入 index）",
        contract_columns="字段双向映射（amount↔money/preClose↔preclose）+ trade_date 合成",
        contract_empty="空 DataFrame / 空 dict，不抛错",
        contract_source="ptrade_api.py:1516-1520 附近 get_history + 方向B 实证 2026-08-13",
        template_location="_QS_HISTORY_WRAPPER / _QS_HISTORY_TRADE_DATE_EXT",
        homology_test="test_history_wrapper_passes_dict",
    ),
    "get_history_batch": ShimContractSpec(
        api_name="get_history_batch",
        contract_type="dict[code→DataFrame]",
        contract_index="code→DataFrame",
        contract_columns="fields（请求字段映射）",
        contract_empty="空 dict（无数据不报错）",
        contract_source="ptrade_api.py:1444-1476",
        template_location="_shim_source('get_history_batch')",
        homology_test="test_history_wrapper_idempotent",
    ),
    "filter_stock_by_status": ShimContractSpec(
        api_name="filter_stock_by_status",
        contract_type="list[str]",
        contract_index="n/a",
        contract_columns="n/a",
        contract_empty="空 list（无幸存）",
        contract_source="ptrade_api.py:746-780 附近 + P-D9 方案 v3",
        template_location="_QS_FILTER_STATUS_EXT",
        homology_test="test_pd9_st_filters_delisting_risk_penny",
    ),
    "get_trade_days": ShimContractSpec(
        api_name="get_trade_days",
        contract_type="list[str]",
        contract_index="n/a",
        contract_columns="n/a",
        contract_empty="空 list",
        contract_source="ptrade_api.py:1486-1497",
        template_location="_QS_DATE_NORM_EXT",
        homology_test="test_trade_date_ext_removes_synthetic_field",
    ),
    "get_stock_info": ShimContractSpec(
        api_name="get_stock_info",
        contract_type="dict[code→dict]",
        contract_index="code→dict",
        contract_columns="n/a（listed_date 归一 'YYYY-MM-DD'）",
        contract_empty="空 dict / None 值",
        contract_source="ptrade_api.py:get_stock_info（A3 归一契约）",
        template_location="_QS_DATE_NORM_EXT",
        homology_test="test_a3_listed_date_normalized",
    ),
}

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
