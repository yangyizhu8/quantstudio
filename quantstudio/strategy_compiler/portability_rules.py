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
    # 2026-09-09：get_index_day_bar 重写解锁（D4 两轮平台探针 PRECLOSE_PATH_UNLOCK）——
    # shim 形状：DataFrame（index=trade_date 升序，8 字段契约，pctChg 由平台 close/preclose 合成）；
    # 仅 daily-bar-v1 + handle_data/收盘 run_daily 可达路径放行（engine_profile 机器门禁），
    # 其余生命周期/profile → 转换 BLOCK（source_import 调用图门禁）。
    "get_index_day_bar",
})

# ============================================================================
# 分类 0.5：QuantStudio 本地专用 API（PTrade 平台不存在且无重写映射）→ 转换 fail-closed BLOCK
#   权威登记源：skills/quantstudio-strategy-compiler/references/ptrade-api-signatures.json
#   local_only_symbols。任何引用形态（调用/别名赋值/传参）一律拦截——平台不存在的名字
#   任何引用都通向 NameError。此集合为人工 curated（无误杀风险），故采用引用级拦截（非仅 Call func）。
#   2026-09-09：get_index_day_bar 已迁移 DENY_SHIM（重写解锁），本集合当前为空——
#   保留集合与分支作为未来本地 API 的门禁通道（新本地 API 未登记重写映射前在此拦截）。
# ============================================================================
LOCAL_ONLY_PASSTHROUGH_BLOCK: frozenset[str] = frozenset()

# 注入同名 wrapper 的平台登记 API 名（策略代码零改动，转换侧包装平台行为）
# 与 DENY_SHIM 并集 = SHIM_CONTRACT_REGISTRY 的键集合（测试断言集合相等，防双边漂移）
INJECTED_WRAPPER_NAMES: frozenset[str] = frozenset({
    "get_history",              # 方向B：structured array → DataFrame + 字段双向映射
    "get_fundamentals",         # P-D10：list 单调用 + 列筛选 + end_date/publ_date 数值归一 + B6 双查询分派 + B9 批量预取
    "filter_stock_by_status",   # P-D9：'ST' 语义补退市风险兜底
    "get_trade_days",           # A3：日历格式归一 + 未来过滤
    "get_stock_info",           # A3：listed_date 归一
    "get_industry",             # B1：平台无 get_industry → 反向金融池（get_industry_stocks 双码）替代
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
        contract_empty="P-D10 v1.2（2026-08-31 B8/B2）：请求字段全缺 → 1 行 NaN 契约 DataFrame(columns=fields)，不抛错（策略无数据加分分支不再误加分，NaN 比较恒 False 降级不判）；字段部分缺失 → 存留列 + 缺失列 NaN；请求字段缺失 → QS_SHIM_FIELD_MISSING 显性警报；date+start_year 并存 → date-only 双查询多期（B6）",
        contract_source="ptrade_api.py:698-772 / 1421-1442 + 探针 P1/P2（2026-08-31）",
        template_location="_QS_FUNDAMENTALS_EXT",
        homology_test="test_p10_wrapper_native_list_index_preserved",
    ),
    "get_fundamentals_batch": ShimContractSpec(
        api_name="get_fundamentals_batch",
        contract_type="DataFrame",
        contract_index="ptrade_code",
        contract_columns="fields（按请求字段筛选）",
        contract_empty="空契约与 get_fundamentals 一致（全缺 → 1 行 NaN）；平台无 get_fundamentals_batch → shim 委托 get_fundamentals list 模式批量（P-D10 实证 500 码 0.05s）",
        contract_source="ptrade_api.py:1421-1442 + B10（2026-08-31 平台 NameError 实证）",
        template_location="_shim_source('get_fundamentals_batch')",
        homology_test="test_p10_batch_shim_returns_dataframe_index_code",
    ),
    "get_industry": ShimContractSpec(
        api_name="get_industry",
        contract_type="dict",
        contract_index="n/a（单码入参）",
        contract_columns="{'sw_l1': {'industry_code': <6位行业码>}}（本地 get_industry 契约子集）",
        contract_empty="平台无 get_industry → 反向金融池（get_industry_stocks 双码：裸/.XBHS/.XBKS 并集，行业码集转换期从策略源码烘焙）：池内命中 → 首个策略行业码；池外/池无效 → 非金融哨兵 '999999'（fail-open 不剔，RD-3 登记，防本地 fail-closed 全剔空仓）；绝不返回空 dict/抛错",
        contract_source="本地 get_industry（ptrade_api）+ B1/B7（2026-08-28/31，探针 P4：480000.XBHS 银行 42 只实证）",
        template_location="_QS_INDUSTRY_EXT",
        homology_test="test_b1_industry_wrapper_pool_and_failopen",
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
    "get_index_day_bar": ShimContractSpec(
        api_name="get_index_day_bar",
        contract_type="DataFrame",
        contract_index="trade_date（YYYY-MM-DD 升序，index.name='trade_date'）",
        contract_columns="8 字段 canonical 序（open/high/low/close/pctChg/volume/amount + trade_date 索引）；"
                         "pctChg 由平台 close/preclose 合成（D4 探针 P2/P3 实证）；money→amount 映射；preClose 中间列不泄漏",
        contract_empty="无数据 → 空 DataFrame；count 数据不足 → 返回实际行数",
        contract_source="ptrade/probe_get_index_day_bar_ptrade.py + probe_index_preclose_v2_ptrade.py"
                        "（D4 两轮平台探针 2026-09-08/09，PRECLOSE_PATH_UNLOCK）",
        template_location="_shim_source('get_index_day_bar')",
        homology_test="test_get_index_day_bar_shim_homology",
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


# ============================================================================
# M2a：QMT（迅投大QMT 内置 Python）产物最小白名单 + validate_qmt_portability
#   依据：docs/qmt/inner-api/ 转写册（05-枚举常量/06-系统函数/07-行情函数/
#   08-交易函数）+ docs/qmt-pipeline-architecture-m1.md（M1-rev2）。
#   作用域：spec 路径 QMT 渲染产物（qmt/<id>_qmt.py）静态校验——
#   不与上方 PTrade 分类共用清单（平台域独立，防双边漂移同理各自单一来源）。
# ============================================================================
import ast
import builtins as _py_builtins
import re as _re
from typing import Any

from .validators.scan_lookahead import Violation

# 产物内可裸调用的平台函数/模板 wrapper（QMT innerApi 转写册登记面）。
# 准入红线（M2b 块3）：每条登记必须真实对应实际注入的平台 API / 模板 wrapper，
# 逐一附行级依据（转写册行号或 source_import_qmt.py 定义行号）；严禁为让校验
# 通过而放宽、严禁新增 fail-open 分支。source 注入面的 wrapper 同时被
# validate_qmt_portability 的 local_calls_ok（文件内定义即可调）覆盖——此处
# 显式登记为注入面单一来源登记册（产物恒全量注入，见 QmtSourceConverter._assemble）。
_QMT_API_WHITELIST: frozenset[str] = frozenset({
    # ---- 平台行情/交易函数（QMT innerApi 全局，产物内裸调用；转写册行级依据）----
    "get_market_data_ex",       # 07-行情函数.md:81/:96（source 路径经 C. 属性形态消费）
    "passorder",                # 08-交易函数.md:8/:27/:84/:85（_qs_passorder 下单底座）
    "get_trade_detail_data",    # 08-交易函数.md:583（持仓/账户明细；_qs_get_trade_detail 底座）
    # ---- M2a spec 模板 wrapper（render_qmt/qmt_daily.py.j2 注入区，不在 source 注入面）----
    "_qs_account_total_value",
    "_qs_get_ma",
    # ---- M2a+M2b 双面 wrapper（spec 模板与 source 模板均有定义）----
    "_qs_get_history",          # M2b source_import_qmt.py:538（E1：count+1 剔当日 bar）
    "_qs_order_target_value",   # M2b source_import_qmt.py:679（passorder 底座）
    "_qs_get_positions",        # M2b source_import_qmt.py:605（08:583/:648/:649）
    "_qs_should_run_daily",     # M2b source_import_qmt.py:1212（handlebar 前段门控）
    "_qs_native_otv",           # D-18（2026-10-11）：_QS_QMT_POSITION_EXT 模块加载期捕获的
                                # QMT 原生 order_target_value 引用（赋值型 helper，非 def——
                                # 探针 v3 实证模块 globals 预注入，回测语境转发底座，08:1517）
    # ---- M2b source 注入面：机制②同名遮蔽（14 件，_WIRED_WRAPPER_NAMES :1263-1268）----
    "get_history",              # source_import_qmt.py:1079（双签名识别 ptrade_api.py:1407-1423 同款）
    "get_history_batch",        # :1119（B1 契约 {code: DataFrame}）
    "order",                    # :1126（本地签名 ptrade_api.py:1258）
    "order_target_value",       # :1131（本地签名 ptrade_api.py:1240）
    "get_positions",            # :1141（本地签名 ptrade_api.py:1211）
    "get_position",             # :625（定义于 _QS_QMT_POSITION_EXT；空仓返回零持仓视图）
    "get_fundamentals",         # :1149（本地签名 ptrade_api.py:938-940）
    "get_fundamentals_batch",   # :1166（本地签名 ptrade_api.py:1734-1735）
    "filter_stock_by_status",   # :1177（本地签名 ptrade_api.py:1078）
    "get_stock_status",         # :848（定义于 _QS_QMT_STOCK_INFO_EXT；ptrade_api.py:2552-2566）
    "get_Ashares",              # :1184（本地签名 ptrade_api.py:1859；07:3288/:3297）
    "get_trade_days",           # :1190（本地签名 ptrade_api.py:1801；07:3341/:3350）
    "get_stock_info",           # :1200（本地签名 ptrade_api.py:2472；07:2446/:2459）
    "current_price",            # :874（定义于 _QS_QMT_STOCK_INFO_EXT；07:81/:96/:122）
    # ---- M2b source 注入面：机制①视图/内部 helper（EXT/ZONE 实际定义，逐条登记）----
    "_qs_log_view",             # :243（log 视图成品）
    "_qs_pick_ctx",             # :257（机制② C 捕获源）
    "_qs_context_view",         # :359（context 视图）
    "_qs_data_view",            # :466（data 视图）
    "_qs_norm_history_fields",  # :495（字段别名映射 ptrade_api.py:1441 同款）
    "_qs_fetch_bars",           # :507（E1 取数底座）
    "_qs_get_history_batch",    # :556
    "_qs_get_trade_detail",     # :579（08:583）
    "_qs_passorder",            # :654（08:8/:27/:84/:85；05:43-44/:132）
    "_qs_order",                # :660
    "_qs_get_ashares",          # :715（07:3288/:3297）
    "_qs_get_trade_days",       # :743（07:3341/:3350；init 内不可用 06:12）
    "_qs_get_stock_info",       # :784（07:2446/:2459；字段 :2479-2511）
    "_qs_status_flags",         # :803（ST/HALT/DELISTING 三判）
    "_qs_filter_stock_by_status",  # :888
    "_qs_fin_ms_to_date",       # :949（07:1837 毫秒时间戳归一）
    "_qs_last_close",           # :957
    "_qs_get_fundamentals",     # :1010（07:1839/:1852/:1863/:1867）
    "_qs_get_fundamentals_batch",  # :1052
    "_qs_should_run_after",     # :1221（handlebar 尾段门控）
})

# ContextInfo（约定形参名 C）上允许调用的方法（06-系统函数.md / 07-行情函数.md）。
# 准入红线同上：source_import_qmt.py 注入面实际消费的 C.<method>() 全集，
# 逐一对应 + 行级依据（转写册行号 + source_import_qmt.py 消费点行号）。
_QMT_CONTEXT_METHODS: frozenset[str] = frozenset({
    # 07-行情函数.md:81/:96 —— 行情取数（消费点 :279/:388/:516/:878/:959）
    "get_market_data_ex",
    # 07-行情函数.md:1839/:1852（签名 :1863/:1867）—— 财务取数（消费点 :1023）
    "get_financial_data",
    # 07-行情函数.md:3288/:3297 —— 板块成分（消费点 :729，'沪深A股' 动态池）
    "get_stock_list_in_sector",
    # 07-行情函数.md:2446/:2459（字段 :2479-2511）—— 证券详情快照（消费点 :399/:787/:815）
    "get_instrument_detail",
    # 07-行情函数.md:3341/:3350 —— 交易日历（消费点 :754；init 内不可用 06:12）
    "get_trading_dates",
    # 06-系统函数.md ContextInfo 节 + M5-13 实测（毫秒 int，北京午夜锚定）——
    # bar 时间戳（消费点 _qs_fetch_bars/_qs_snap_kwargs，D-1 窗口锚定依据）
    "get_bar_timetag",
})

# 显式 DENY：QMT 已废弃/错误形态 API（命中即 BLOCK，rule_id 见 value）
_QMT_API_DENY: dict[str, str] = {
    # 07-行情函数.md：get_history_data 为旧版废弃接口，新版 get_market_data_ex
    "get_history_data": "QMT-DEPRECATED-API",
    # get_market_data（无 _ex）非 QMT innerApi 登记形态（聚宽/本地风格名）
    "get_market_data": "QMT-NONEX-MARKET-DATA",
    # 06-系统函数.md schedule_run 节：run_time 为旧版定时器，新版 schedule_run；
    # M2a 产物面不注入定时器（每日 bar 驱动 = handlebar 天然节拍）
    "run_time": "QMT-RUNTIME-INVALID",
}

_QMT_LIFECYCLE_REQUIRED = ("init", "handlebar")

# PEP 263 coding 声明（首行）——产物必须显式 gbk（QMT 客户端读取约定）
_QMT_CODING_RE = _re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*gbk\b", _re.IGNORECASE)


def validate_qmt_portability(
    source_text: str,
) -> tuple[bool, list[Violation], list[str]]:
    """Statically validate a rendered QMT product (unicode text, pre-gbk write).

    Checks (M2a 最小面，BLOCK 语义)：
      1. QMT-GBK-HEADER   —— 首行 PEP 263 coding:gbk 声明在位
      2. QMT-SYNTAX       —— ast.parse 语法合法
      3. QMT-LIFECYCLE-SHAPE —— 顶层 init(C)/handlebar(C) 生命周期在位
      4. QMT-DENY-API     —— 废弃/错误形态 API（Name 调用或属性调用名命中）
      5. QMT-API-WHITELIST —— 裸名调用必须属 白名单/本地函数/builtin；
                              C.<method>() 必须属 _QMT_CONTEXT_METHODS；
                              导入模块属性调用（np./pd.）与运行时对象方法放行
                             （与 validate_local_strategy 同款判定形态）。

    Returns (ok, violations, warnings)——与 scan_lookahead / validate_local_strategy
    同形，供 orchestrator 统一消费。
    """
    violations: list[Violation] = []
    warnings: list[str] = []

    # 1. gbk header（首行）
    first_line = source_text.split("\n", 1)[0]
    if not _QMT_CODING_RE.match(first_line):
        violations.append(Violation(
            rule_id="QMT-GBK-HEADER",
            severity="BLOCK",
            message="first line must declare `#coding:gbk` (PEP 263; QMT 客户端"
                    " 读取约定) — 产物缺 gbk 编码声明",
            location="line 1",
        ))

    # 2. syntax
    try:
        tree = ast.parse(source_text)
    except SyntaxError as e:
        violations.append(Violation(
            rule_id="QMT-SYNTAX",
            severity="BLOCK",
            message=f"Python SyntaxError: {e}",
            location=f"line {e.lineno}",
        ))
        return False, violations, warnings

    # 3. lifecycle shape（QMT innerApi：init/handlebar 顶层）
    top_level_funcs = {
        n.name for n in ast.iter_child_nodes(tree)
        if isinstance(n, ast.FunctionDef)
    }
    for req in _QMT_LIFECYCLE_REQUIRED:
        if req not in top_level_funcs:
            violations.append(Violation(
                rule_id="QMT-LIFECYCLE-SHAPE",
                severity="BLOCK",
                message=f"required QMT lifecycle function {req!r} missing"
                        " (06-系统函数.md: init/handlebar)",
            ))

    # 本地函数（含嵌套 def）与本地类——文件内定义即可调
    local_calls_ok: set[str] = {
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_modules.add(alias.asname or alias.name)

    builtin_names = frozenset(dir(_py_builtins))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func

        # 属性调用（X.method(...)）
        if isinstance(func, ast.Attribute):
            attr = func.attr
            # 4. deny 按属性名拦（如 C.get_market_data(...)）
            if attr in _QMT_API_DENY:
                violations.append(Violation(
                    rule_id=_QMT_API_DENY[attr],
                    severity="BLOCK",
                    message=f"call {attr!r} on an object — QMT DENY 名命中"
                            "（废弃/错误形态；见 docs/qmt/inner-api/ 转写册）",
                    location=f"line {node.lineno}",
                ))
                continue
            # C.<method>()：ContextInfo 方法面收口（约定形参名 C）
            if isinstance(func.value, ast.Name) and func.value.id == "C":
                if attr not in _QMT_CONTEXT_METHODS:
                    violations.append(Violation(
                        rule_id="QMT-CONTEXT-METHOD",
                        severity="BLOCK",
                        message=f"C.{attr}(...) — ContextInfo 方法不在"
                                " _QMT_CONTEXT_METHODS 登记面（06-系统函数.md）",
                        location=f"line {node.lineno}",
                    ))
                continue
            # 导入模块属性调用（np./pd.）放行；其余运行时对象方法放行
            # （判定形态与 validate_local_strategy 一致）
            continue

        # 裸名调用
        if isinstance(func, ast.Name):
            name = func.id
            if name in _QMT_API_DENY:
                violations.append(Violation(
                    rule_id=_QMT_API_DENY[name],
                    severity="BLOCK",
                    message=f"call {name!r} — QMT DENY 名命中（废弃/错误形态；"
                            "见 docs/qmt/inner-api/ 转写册）",
                    location=f"line {node.lineno}",
                ))
            elif name not in _QMT_API_WHITELIST and name not in local_calls_ok \
                    and name not in builtin_names:
                violations.append(Violation(
                    rule_id="QMT-API-WHITELIST",
                    severity="BLOCK",
                    message=f"calls unknown API {name!r} — not in QMT whitelist,"
                            " not a local helper, not a builtin, not an imported-"
                            "module/context method",
                    location=f"line {node.lineno}",
                ))

    ok = not any(v.severity == "BLOCK" for v in violations)
    return ok, violations, warnings


# ============================================================================
# QMT 行为差异 WARN 层（纯 WARN · 零 BLOCK）—— scan_behavior_warns + 表驱动
#   依据：docs/deal07-trade-surface-design.md §4.1（五条 WARN 原始定义，2026-10-22
#         总调度令）＋ QD-2026-0010 feedback.gate_rule 候选（跨切换日 WARN）＋
#         QD-2026-0008（空串≠None / 码形态 SH↔SS）、QD-2026-0009（零视图恒真 /
#         硬编码 .SS 静默 miss）＋ registry：POS 7 缺口（POS-04/05/10/11/13/15/17，
#         已裁接受）、FEE-02（佣金三项分设）、FEE-03（印花买卖双字段、本地仅卖出）、
#         FEE-04（过户费无专名、QMT 回测恒 0）、QMCH-02（QMT 回测不拦封板）。
#   定位：独立于 validate_qmt_portability 的 BLOCK 面——本层**只提示双端行为差异，
#   绝不阻断**任何转换/校验；呈现方式由调用方（转换器 / CI linter / 人工复核）决定。
#   位置纪律：追加于 NORMALIZE_RULES 之后（文件尾）；DENY_BLOCK / LOCAL_ONLY_
#   PASSTHROUGH_BLOCK / PTRADE_REGISTERED_WARN / NORMALIZE_RULES 既有内容零改动
#  （冻结基线 HEAD=4ddaf55，portability_rules.py 527 行）。
#   先例：QD-2026-0001（fix_layer=portability_rule——落点为契约执行门，非 wrapper 模板）。
# ============================================================================

# POS 7 缺口字段（registry POS-04/05/10/11/13/15/17：PTrade 有、本地 Position 6 字段
# 契约 ptrade_api.py:174-175 无；2026-10-22 消费方普查全仓零命中、已裁接受缺口）。
# 字段集唯一来源＝knowledge/registry.md，禁止凭猜测增删。
QMT_POS_GAP_FIELDS: frozenset[str] = frozenset({
    "today_long_amount",     # POS-04 今买量（QMT 侧可派生 m_nVolume−m_nYesterdayVolume）
    "today_short_amount",    # POS-05 今卖量
    "business_type",         # POS-10 业务类型（get_business_type() 为 API，非本字段，不在此列）
    "contract_multiplier",   # POS-11 合约乘数
    "long_pnl",              # POS-13 多头浮盈
    "long_cost_basis",       # POS-15 多头成本
    "delivery_date",         # POS-17 交割日（QMT 侧空串而非 None）
})

# 字符串型字段（无值时返回**空串而非 None**——QD-0008 ②条实测 + POS-17）：
# 判空禁用 is None，须用 not v。
QMT_STRING_VALUED_FIELDS: frozenset[str] = frozenset({
    "delivery_date",         # POS-17：m_strExpireDate=''（现货无到期日）
    "m_strOpenDate",         # QD-0008：实测 ''（现货无开仓日）
    "m_strExpireDate",       # QD-0008：实测 ''
})

# 过户费字段/别名（FEE-04：本地有公式双向计提；QMT 回测恒 0/未实现、面板无专名）
QMT_TRANSFER_FEE_ALIASES: frozenset[str] = frozenset({
    "transfer_fee", "transfer_fee_rate", "m_dTransferFee",
})

# 下单族 API（本地 order 系公开面，PTRADE_REGISTERED_WARN 登记子集；规则 8 判据之一）
_QMT_ORDER_FAMILY: frozenset[str] = frozenset({
    "order", "order_value", "order_target", "order_target_value", "order_at_price",
})

# 印花税分期间切换日（QD-2026-0010：本地 _stamp_tax_rate 封顶 0.0005 vs QMT 回测单一费率）
_QMT_STAMP_SWITCH_DATE = "2023-08-28"

# 行为差异 WARN 规则表：(rule_id, 触发摘要, 建议) —— 与 scan_behavior_warns 实现同步
BEHAVIOR_WARN_RULES: list[tuple[str, str, str]] = [
    ("QMT-POS-FIELD-UNIMPL",
     "Attribute 访问命中 POS 7 缺口字段（today_long_amount 等）",
     "本地 Position 未实现该字段；改用替代口径并登记已知差异"),
    ("QMT-FEE-COMMISSION-TRI",
     "调用 set_commission（QMT 面板佣金三项分设）",
     "本地单一 commission_ratio 须三项同值映射；QMT 面板逐一核对"),
    ("QMT-FEE-STAMP-BUY",
     "set_commission 传 stamp 相关关键字（涉印花语义）",
     "本地印花仅卖出；QMT 侧买入印花税必须置 0"),
    ("QMT-FEE-TRANSFER-ALIAS",
     "源码出现过户费字段/别名（transfer_fee 系）",
     "QMT 回测不收过户费；对照前并入手续费口径并写明"),
    ("QMT-DAT-NULL-TRIPLE",
     "判空形态错误：字符串字段用 is None / 持仓对象用 truthiness",
     "字符串字段判空用 not v；持仓判命中用 .amount>0 / ==0"),
    ("QMT-CODE-FORM-MASK",
     "硬编码 .SS 码形态或同文件混用 .SH/.SZ/裸码",
     "统一 universe 码形态；.SS 在 QMT 端静默 miss"),
    ("QMT-FEE-STAMP-PERIOD",
     "回测区间跨 2023-08-28 印花切换日（须调用方传入区间）",
     "本地分期间封顶 0.0005 vs QMT 单一费率；分段回测或声明差异"),
    ("QMT-LIMIT-BOARD-FIDELITY",
     "既调用 check_limit 又有下单分支",
     "本地涨跌停拒单 vs QMT 回测不拦封板；双端成交会分歧"),
]


def _stamp_date_key(date_str: str) -> str:
    """日期归一为可比较的 YYYYMMDD 键（兼容 '2023-08-28' / '20230828' 两种形态）。"""
    return str(date_str).replace("-", "").replace("/", "")


def _bool_operands(expr: ast.expr) -> list[ast.expr]:
    """递归展开布尔上下文叶子操作数（只穿透 BoolOp / not，不穿透属性/比较/调用包裹）。

    用于精确判定 truthiness：`get_position(x)` 作为布尔叶子（恒真零视图）才命中；
    `get_position(x).amount > 0` 这类正确写法的叶子是 Compare，不命中。
    """
    if isinstance(expr, ast.BoolOp):
        out: list[ast.expr] = []
        for v in expr.values:
            out.extend(_bool_operands(v))
        return out
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        return _bool_operands(expr.operand)
    return [expr]


def scan_behavior_warns(
    tree: ast.AST,
    source: str,
    backtest_range: tuple[str, str] | None = None,
) -> list[dict]:
    """扫描策略源码中的 QMT↔本地行为差异（**纯 WARN，零 BLOCK**）。

    参数：
      tree：已解析的 AST（调用方 ast.parse 产物）。
      source：源码原文（当前规则族全部基于 AST 判定；该参数保留供未来
        文本级规则扩展与诊断用途，签名契约固定不变）。
      backtest_range：(start, end) 回测区间，'YYYY-MM-DD' 或 'YYYYMMDD'。
        **仅 QMT-FEE-STAMP-PERIOD 依赖**——是否跨 2023-08-28 印花切换日
        **静态不可判定，须调用方传入区间**；None 时该规则恒不命中（如实
        静默，不猜测）。

    返回：``[{"rule_id": str, "line": int, "message": str, "grade": "WARN"}]``
    （按 line 升序、同 line 按 rule_id 排序；line=0 表示文件/区间级提示）。
    每条 message 固定含「双端差异」＋「建议」两句。

    判定形态与已知局限（防误读）：
      - QMT-POS-FIELD-UNIMPL 按 Attribute 字段名匹配（不区分宿主对象类型），
        命中后由人工确认是否真为 Position 消费；
      - QMT-DAT-NULL-TRIPLE 的 truthiness 判定覆盖 if/while/assert/三元/布尔
        链中**直接作为布尔叶子**的 get_position(...) 调用；先赋值后判真
        （p = get_position(x); if p:）属数据流形态，静态层不追、不误报；
      - QMT-CODE-FORM-MASK 的裸码判定为启发式（6 位数字且非 2 开头，规避
        8 位日期；时间字面量与个股码同形时可能多报，WARN 级人工甄别）；
      - 本层不产生任何 BLOCK——与 validate_qmt_portability 的 BLOCK 面正交。
    """
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()

    def _warn(rule_id: str, line: int, message: str) -> None:
        key = (rule_id, int(line))
        if key in seen:
            return
        seen.add(key)
        out.append({"rule_id": rule_id, "line": int(line),
                    "message": message, "grade": "WARN"})

    # 预收集：Call 的 func 节点集合（区分方法调用与纯字段访问，防规则 1 误报）
    call_func_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            call_func_ids.add(id(node.func))

    # 预收集：布尔判定上下文（if/while/assert/三元），规则 5b 用
    bool_tests: list[ast.expr] = []
    for node in ast.walk(tree):
        for attr in ("test",):
            t = getattr(node, attr, None)
            if isinstance(t, ast.expr) and isinstance(
                    node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
                bool_tests.append(t)

    suffixed_lines: list[int] = []          # .SH/.SZ/.SS 后缀码字面量行号
    bare_lines: list[int] = []              # 疑似裸码字面量行号
    has_check_limit = False
    first_order_line: int | None = None

    for node in ast.walk(tree):
        # ---- 规则 1：POS 7 缺口字段 Attribute 访问 ----
        if isinstance(node, ast.Attribute) and id(node) not in call_func_ids \
                and node.attr in QMT_POS_GAP_FIELDS:
            _warn("QMT-POS-FIELD-UNIMPL", node.lineno,
                  "双端差异：字段 %r 本地 Position 未实现（PTrade 有、QMT 侧可派生，"
                  "registry POS 7 缺口·已裁接受）。建议：改用替代口径"
                  "（如 amount/enable_amount 派生）并在双端对照报告登记该差异。"
                  % node.attr)

        # ---- 规则 4：过户费字段/别名（Name/Attribute/keyword/字典键字符串）----
        if isinstance(node, ast.Name) and node.id in QMT_TRANSFER_FEE_ALIASES:
            _warn("QMT-FEE-TRANSFER-ALIAS", node.lineno,
                  "双端差异：本地过户费双向计提（标识符 %s），QMT 回测不收过户费"
                  "且面板无专名（FEE-04 端测恒 0）。建议：对照前把过户费并入"
                  "手续费比例口径并在文档写明，勿按本地公式对账。" % node.id)
        elif isinstance(node, ast.Attribute) and node.attr in QMT_TRANSFER_FEE_ALIASES:
            _warn("QMT-FEE-TRANSFER-ALIAS", node.lineno,
                  "双端差异：本地过户费双向计提（字段 %s），QMT 回测不收过户费"
                  "且面板无专名（FEE-04 端测恒 0）。建议：对照前把过户费并入"
                  "手续费比例口径并在文档写明，勿按本地公式对账。" % node.attr)
        elif isinstance(node, ast.keyword) and node.arg in QMT_TRANSFER_FEE_ALIASES:
            _warn("QMT-FEE-TRANSFER-ALIAS", node.lineno,
                  "双端差异：本地过户费双向计提（关键字 %s），QMT 回测不收过户费"
                  "且面板无专名（FEE-04 端测恒 0）。建议：对照前把过户费并入"
                  "手续费比例口径并在文档写明，勿按本地公式对账。" % node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value in QMT_TRANSFER_FEE_ALIASES:
            _warn("QMT-FEE-TRANSFER-ALIAS", node.lineno,
                  "双端差异：本地过户费双向计提（字典键 %r），QMT 回测不收过户费"
                  "且面板无专名（FEE-04 端测恒 0）。建议：对照前把过户费并入"
                  "手续费比例口径并在文档写明，勿按本地公式对账。" % node.value)

        # ---- 规则 2/3：set_commission 调用（佣金三项 / 买入印花）----
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "set_commission":
            _warn("QMT-FEE-COMMISSION-TRI", node.lineno,
                  "双端差异：QMT 回测面板佣金三项分设（买入/平昨/平今），本地为"
                  "单一 commission_ratio（FEE-02）。建议：转换/对照时三项同值映射，"
                  "并在 QMT 面板逐一核对，防把结构差异误读为费率差异。")
            stamp_kws = [kw.arg for kw in node.keywords
                         if kw.arg and "stamp" in kw.arg.lower()]
            if stamp_kws:
                _warn("QMT-FEE-STAMP-BUY", node.lineno,
                      "双端差异：本地印花税仅卖出计提（set_commission 关键字 %s 涉"
                      "印花语义），QMT 面板为买卖双字段（FEE-03）。建议：QMT 侧"
                      "买入印花税必须置 0，仅卖出按本地费率映射。"
                      % "/".join(stamp_kws))

        # ---- 规则 5a：字符串型字段 is None 判空 ----
        if isinstance(node, ast.Compare) \
                and any(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops):
            operands = [node.left] + list(node.comparators)
            for operand in operands:
                if isinstance(operand, ast.Attribute) \
                        and operand.attr in QMT_STRING_VALUED_FIELDS:
                    _warn("QMT-DAT-NULL-TRIPLE", node.lineno,
                          "双端差异：字段 %r 在 QMT 侧无值时返回**空串而非 None**"
                          "（QD-0008 实测 / POS-17）。建议：判空改用 not v——"
                          "is None 对空串恒 False，会把无值误判为有值。"
                          % operand.attr)
                    break

        # ---- 规则 8 素材：check_limit 与下单族调用 ----
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fname = node.func.id
            if fname == "check_limit":
                has_check_limit = True
            elif fname in _QMT_ORDER_FAMILY and first_order_line is None:
                first_order_line = node.lineno

        # ---- 规则 6：码形态字面量 ----
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if _re.fullmatch(r"\d{6}\.SS", val):
                _warn("QMT-CODE-FORM-MASK", node.lineno,
                      "双端差异：硬编码 %r 为本地 .SS 形态，QMT 持仓键为 .SH/.SZ，"
                      "跨端精确匹配会静默 miss（QD-2026-0009 端测）。建议：改用 "
                      "universe/动态池码形态，勿硬编码跨端字面量。" % val)
                suffixed_lines.append(node.lineno)
            elif _re.fullmatch(r"\d{6}\.(SH|SZ)", val):
                suffixed_lines.append(node.lineno)
            elif _re.fullmatch(r"[0135689]\d{5}", val):
                # 启发式裸码：6 位数字、非 2 开头（规避 8 位日期的 6 位残片与
                # 2 开头日期串）；时间字面量与码同形时可能多报，WARN 级人工甄别
                bare_lines.append(node.lineno)

    # ---- 规则 5b：布尔叶子上的 get_position(...) truthiness（零视图恒真）----
    for test in bool_tests:
        for leaf in _bool_operands(test):
            if isinstance(leaf, ast.Call) and isinstance(leaf.func, ast.Name) \
                    and leaf.func.id == "get_position":
                _warn("QMT-DAT-NULL-TRIPLE", leaf.lineno,
                      "双端差异：get_position 未命中返回零视图对象（恒真），"
                      "truthiness 判命中恒成立（QD-2026-0009 端测）。建议：判命中"
                      "改用 .amount > 0，判空仓改用 .amount == 0。")

    # ---- 规则 6b：同文件混用后缀码与裸码（一次性汇总）----
    if suffixed_lines and bare_lines:
        _warn("QMT-CODE-FORM-MASK", min(bare_lines),
              "双端差异：同文件混用后缀码（%d 处 .SH/.SZ/.SS）与疑似裸码"
              "（%d 处）——QMT 持仓键为精确匹配语义，形态不一致会静默 miss"
              "（QD-2026-0009）。建议：统一码形态（转换器仅归一字面量、不做"
              "成员语义归一），人工核对混用清单。" % (len(suffixed_lines),
                                                      len(bare_lines)))

    # ---- 规则 8：check_limit × 下单分支（本地拒单 vs QMT 不拦封板）----
    if has_check_limit and first_order_line is not None:
        _warn("QMT-LIMIT-BOARD-FIDELITY", first_order_line,
              "双端差异：本地涨跌停封板拒单（limit_up_blocked），QMT 回测不拦"
              "封板、涨跌停两侧均可成交（QMCH-02 端测定谳）。建议：依赖 "
              "check_limit 拒单语义的策略在 QMT 侧逐笔核对成交，并显式登记该分歧。")

    # ---- 规则 7：回测区间跨 2023-08-28 印花切换日（须调用方传入区间）----
    if backtest_range is not None:
        start_key = _stamp_date_key(backtest_range[0])
        end_key = _stamp_date_key(backtest_range[1])
        switch_key = _stamp_date_key(_QMT_STAMP_SWITCH_DATE)
        if start_key <= switch_key <= end_key:
            _warn("QMT-FEE-STAMP-PERIOD", 0,
                  "双端差异：回测区间 %s~%s 跨 2023-08-28 印花切换日——本地 "
                  "_stamp_tax_rate 分期间封顶 0.0005，QMT 回测为单一面板费率"
                  "（QD-2026-0010 端测定谳）。建议：分段回测拼接，或在对照报告"
                  "显式声明该期间差异。" % (backtest_range[0], backtest_range[1]))

    out.sort(key=lambda w: (w["line"], w["rule_id"]))
    return out
