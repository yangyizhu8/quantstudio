"""
策略名称：全球资产轮动（Global Asset Rotation）

运行周期：日线（每日 handle_data 触发一次）

来源：由 PTrade 策略源码**逆向移植**为 QuantStudio 本地回测策略
      （即 PTrade→本地 转换，与"本地→PTrade"转换管线互为逆向）。
      本次移植严格对齐原 PTrade 实现的行为与语义，未改动任何策略逻辑。

================================================================================
一、策略逻辑（与 PTrade 原实现逐条一致）
================================================================================
标的池（9 只，顺序即权重顺序）：
  1 国债 511260.SS  2 标普500 513500.SS  3 纳指100 159941.SZ  4 德国DAX30 513030.SS
  5 亚太低碳 159687.SZ  6 原油 501018.SS  7 有色金属 159980.SZ  8 豆粕 159985.SZ
  9 黄金 518880.SS

权重结构：
  基础权重合计 60%（国债 20%，其余 8 只各 5%）；
  动态资金 35%，每半年（126 个策略日）在剔除国债后的 8 只候选中，
  选**年化波动率最低的 2 只**，各加仓 35%/2 = 17.5%；
  目标总仓位 100%，未配置部分（首次建仓 40%、动态调仓后 5%）全部由
  国债 ETF 511260.SS 承接。

时间轴（严格保留原行为）：
  全部 9 只标的均具备历史行情后**首个交易日**首次建仓（策略日 t=0）；
  首次动态调仓发生在策略日 t = 125 + 126 = **251**；
  其后每隔 126 个策略日调仓一次。

信号口径：所有历史行情均使用 include=False 的前复权日线（fq='pre'），
  即只使用当前交易日之前**已完成**的 K 线，无未来函数；
  波动率 = 125 个日收益率的样本标准差（ddof=1）× sqrt(250)。

执行顺序：先卖出超配资产 → 再买入非国债资产 → 最后调整国债 ETF
  （国债作为现金承接资产放在最后，降低下单过程中资金不足的概率）。

================================================================================
二、本地适配点（保证语义等价，共 6 处，均已在代码中标注【本地适配】）
================================================================================
【本地适配 1】订单状态码映射
  本地框架 Order.status 为字符串（filled/rejected/partial/pending/expired/
  cancelled），PTrade 为 "0".."9" 数字码。本文件把本地状态映射为 PTrade 码后
  再走原有裁决分支，保证 PTrade 的 status 语义（5=部撤/6=已撤/8=已成/9=废单）
  在本地逐字生效。

【本地适配 2】成交核验路径（两种撮合模式自适应）
  本地 match_price_mode='close'（默认，与 PTrade 日线/分钟一致）为**即时成交**：
  order_target_value 返回的 Order 对象当即带终态（filled/rejected），且框架的
  get_order() 在该模式下恒返回 None（框架既定契约，见 ptrade_api.get_order）。
  因此本文件在下单当日即用返回对象核验终态并记账；仅当订单未终结
  （pending/partial，即 match_price_mode='next_open' 路径）时才入队，
  由次日 get_order() 走原 PTrade 核验路径。两种模式下"核验实际成交"的策略
  意图完全一致，不改变任何下单/持仓行为。

【本地适配 3】委托数量/价格日志字段
  本地 Order 无 amount/limit 属性（对应字段为 target_amount/price）。
  为保证"委托数量、委托价格"日志真实可读，读取时做等价字段回退（仅影响日志，
  不参与任何裁决）。

【本地适配 4】运行周期自检
  本地 Context 不提供 sim_params.data_frequency（原 try/except 已兜底），
  09:31 触发时间兜底判断保留；本地日线回测不会误报。

【本地适配 5】行情字典访问
  本地 data 为惰性构建的 PTrade BarData 字典，data[security]['close'] 语义一致；
  保留 position.last_sale_price / cost_basis 三级兜底（本地 Position 均具备）。

【本地适配 6】下单接口语义
  本地 order_target_value 为框架层 P-D12 补差语义实现（与 PTrade 目标市值语义
  一致），含 <0.5% 微调跳过与大额拆单；微调跳过返回 status='rejected' 的
  no-op Order（reason='below_rebalance_threshold'），本文件按"未成交"如实记账。

================================================================================
三、本地回测数据窗口提示（主库实测 2026-09-12）
================================================================================
  9 只标的**全部就绪**的公共区间 = 2022-12-29 ~ 2026-09-04（890 个交易日）。
  瓶颈为 159687（亚太低碳精选，2022-12-29 起有数据）；其余 8 只历史更长
  （511260/513500/159941/513030/501018/518880 自 2018-01、159980 自 2019-12、
  159985 自 2019-12）。回测起点早于 2022-12-29 时，策略将按原逻辑等待数据
  （全部就绪后才首次建仓），不会提前建仓。
"""

import numpy as np


# ====================== 策略参数设置 ======================
# 标的顺序：
# 1. 国债
# 2. 标普500
# 3. 纳指100
# 4. 德国DAX30
# 5. 亚太低碳精选
# 6. 原油
# 7. 有色金属
# 8. 豆粕
# 9. 黄金

ASSET_LIST = [
    "511260.SS",  # 国泰十年国债ETF
    "513500.SS",  # 博时标普500ETF(QDII)
    "159941.SZ",  # 广发纳指100ETF(QDII)
    "513030.SS",  # 华安德国DAX30ETF(QDII)
    "159687.SZ",  # 南方富时亚太低碳精选ETF(QDII)
    "501018.SS",  # 南方原油(QDII-LOF)
    "159980.SZ",  # 国泰中证有色金属ETF
    "159985.SZ",  # 大成豆粕ETF
    "518880.SS"   # 华安黄金ETF
]

ASSET_NAME_MAP = {
    "511260.SS": "国泰十年国债ETF",
    "513500.SS": "博时标普500ETF",
    "159941.SZ": "广发纳指100ETF",
    "513030.SS": "华安德国DAX30ETF",
    "159687.SZ": "南方富时亚太低碳精选ETF",
    "501018.SS": "南方原油",
    "159980.SZ": "国泰中证有色金属ETF",
    "159985.SZ": "大成豆粕ETF",
    "518880.SS": "华安黄金ETF"
}

# 回看125个日收益率，需要读取126根日K线
LOOKBACK_WINDOW = 125

# 半年再平衡周期
REBALANCE_PERIOD = 126

# 原始基础权重，合计为60%
BASE_WEIGHT = np.array([
    0.20,
    0.05,
    0.05,
    0.05,
    0.05,
    0.05,
    0.05,
    0.05,
    0.05
], dtype=float)

# 动态资金权重，保持原策略的35%不变
DYNAMIC_POOL_SIZE = 0.35

# 动态候选池剔除国债，仅使用索引1至8的资产
DYNAMIC_CANDIDATE_INDEX = list(range(1, 9))

# 每次选择波动率最低的2只资产
NUM_SELECT = 2

# 使用国债ETF承接原策略中的空闲资金
# 首次建仓：基础权重60%，剩余40%配置到国债ETF
# 动态调仓：基础权重60% + 动态权重35%，剩余5%配置到国债ETF
CASH_PROXY_SECURITY = "511260.SS"

# 组合目标仓位为100%
TARGET_TOTAL_WEIGHT = 1.0

# 原代码的首次动态调仓条件为：
# t >= lookback，并且 t - lookback >= rebalance_period
# 因此首次动态调仓发生在第251个策略日，严格保留该行为。
FIRST_DYNAMIC_REBALANCE_DAY = LOOKBACK_WINDOW + REBALANCE_PERIOD


# 【本地适配 1】本地 Order.status → PTrade 状态码映射。
# 本地框架 status 取值："filled"/"rejected"/"partial"/"pending"（另有
# "expired"/"cancelled" 用于 next_open 撤单/过期路径）。
# PTrade 状态码：5=部撤, 6=已撤, 7=部成, 8=已成, 9=废单, 2=已报。
_LOCAL_STATUS_TO_PTRADE = {
    "filled": "8",
    "partial": "7",
    "rejected": "9",
    "pending": "2",
    "expired": "6",
    "cancelled": "6",
}

# 【本地适配 3】本地 Order 字段 → PTrade Order 字段等价回退。
# 本地 Order 为双口径：target/filled 是**金额**，target_amount/filled_amount
# 是**股数**；PTrade Order 的 amount/filled 均为**股数**。因此：
#   amount → target_amount（委托股数）
#   filled → filled_amount（成交股数，优先于金额口径的 filled）
#   limit  → price（成交价）
# 该映射仅用于日志可读性与成交确认判定（成交股数>0 才视为已成交），
# 不改变任何下单/持仓行为。
_LOCAL_ORDER_FIELD_FALLBACK = {
    "amount": ("amount", "target_amount", "filled_amount"),
    "filled": ("filled_amount", "filled"),
    "limit": ("limit", "price"),
    "status": ("status",),
}


def initialize(context):
    """
    QuantStudio 本地策略初始化。

    本地回测由 PyQt 回测面板或 CLI 触发，运行周期为日线；
    策略代码不含初始资金硬编码，资金由回测配置决定。

    回测环境关闭成交量限制，避免目标配置因单根K线成交量不足而
    出现部分成交。该设置只作用于回测，不作用于实盘交易。
    """
    g.asset_list = list(ASSET_LIST)
    g.asset_name_map = dict(ASSET_NAME_MAP)

    g.lookback_window = LOOKBACK_WINDOW
    g.rebalance_period = REBALANCE_PERIOD
    g.base_weight = BASE_WEIGHT.copy()
    g.dynamic_pool_size = DYNAMIC_POOL_SIZE
    g.dynamic_candidate_index = list(DYNAMIC_CANDIDATE_INDEX)
    g.num_select = NUM_SELECT
    g.cash_proxy_security = CASH_PROXY_SECURITY
    g.target_total_weight = TARGET_TOTAL_WEIGHT
    g.first_dynamic_rebalance_day = FIRST_DYNAMIC_REBALANCE_DAY

    # 策略状态。
    g.strategy_started = False
    g.strategy_day = 0
    g.last_dynamic_rebalance_day = None
    g.last_handle_date = None

    # 保存已创建但尚未核验最终状态的订单。
    # 订单将在后续交易日通过get_order查询实际成交数量和最终状态。
    g.pending_order_audit = {}

    # 避免分钟周期误配告警在同一交易日重复输出。
    g.last_frequency_warning_date = None

    set_universe(g.asset_list)

    # 回测中取消单周期成交量限制，解决“当前bar交易量不足”问题。
    if not is_trade():
        set_limit_mode("UNLIMITED")

    initial_weights = build_fully_invested_weights(g.base_weight)
    initial_proxy_weight = get_weight(
        initial_weights,
        g.cash_proxy_security
    )

    log.info(
        "[资产配置][初始化] 标的数量=%d, 回看收益率=%d日, 调仓周期=%d日, "
        "基础权重=%.2f%%, 动态权重=%.2f%%, 目标总仓位=%.2f%%, "
        "空闲资金承接标的=%s, 首次国债目标权重=%.2f%%"
        % (
            len(g.asset_list),
            g.lookback_window,
            g.rebalance_period,
            float(np.sum(g.base_weight)) * 100.0,
            g.dynamic_pool_size * 100.0,
            g.target_total_weight * 100.0,
            g.asset_name_map.get(
                g.cash_proxy_security,
                g.cash_proxy_security
            ),
            initial_proxy_weight * 100.0
        )
    )


def handle_data(context, data):
    """
    每个交易日执行一次。

    所有波动率信号均使用include=False的前复权日线，
    即只使用当前交易日之前已经完成的K线，避免未来函数。
    """
    current_date = context.blotter.current_dt.strftime("%Y%m%d")

    # 防止策略被误设为分钟周期后，同一交易日重复计数和重复下单。
    if g.last_handle_date == current_date:
        return
    g.last_handle_date = current_date

    check_running_frequency(context, current_date)

    # 核验之前调仓产生的订单。PTrade订单通常在handle_data返回后撮合，
    # 因此不能把order_target_value返回订单号直接视为已经成交。
    audit_pending_orders(
        context=context,
        data=data,
        current_date=current_date
    )

    # 原离线代码对所有资产价格执行dropna，因此只有全部资产均有数据后
    # 才正式进入组合回测。这里使用上一根已完成日线进行同等保护。
    ready, unavailable_assets = check_all_assets_ready()

    if not ready:
        log.warning(
            "[资产配置][等待数据] 日期=%s, 尚无有效历史行情=%s, 本日不交易"
            % (current_date, ",".join(unavailable_assets))
        )
        return

    # 全部标的具备历史数据后首次建仓。
    # 原基础权重合计60%，其余40%由国债ETF承接，使目标仓位达到100%。
    if not g.strategy_started:
        g.strategy_started = True
        g.strategy_day = 0
        g.last_dynamic_rebalance_day = None

        initial_weights = build_fully_invested_weights(
            g.base_weight.copy()
        )
        proxy_weight = get_weight(
            initial_weights,
            g.cash_proxy_security
        )

        log.info(
            "[资产配置][首次建仓] 日期=%s, 策略日=%d, "
            "基础权重=%.2f%%, 国债承接后权重=%.2f%%, "
            "目标总仓位=%.2f%%"
            % (
                current_date,
                g.strategy_day,
                float(np.sum(g.base_weight)) * 100.0,
                proxy_weight * 100.0,
                float(np.sum(initial_weights)) * 100.0
            )
        )

        rebalance_portfolio(
            context=context,
            data=data,
            target_weights=initial_weights,
            stage="首次建仓",
            current_date=current_date
        )

    # 严格保留原代码的首次动态调仓时间：
    # 第251个策略日首次动态调仓，之后每隔126个策略日调仓一次。
    if should_dynamic_rebalance():
        volatility_result = calculate_candidate_volatility()

        if volatility_result is None:
            log.warning(
                "[资产配置][动态调仓跳过] 日期=%s, 策略日=%d, "
                "125日收益率数据不足或异常"
                % (current_date, g.strategy_day)
            )
        else:
            target_weights, selected_assets, volatility_map = (
                volatility_result
            )

            selected_text = []
            for security in selected_assets:
                selected_text.append(
                    "%s(%s,年化波动率=%.4f)"
                    % (
                        g.asset_name_map.get(security, security),
                        security,
                        volatility_map[security]
                    )
                )

            proxy_weight = get_weight(
                target_weights,
                g.cash_proxy_security
            )

            log.info(
                "[资产配置][动态调仓] 日期=%s, 策略日=%d, 入选资产=%s, "
                "每只动态加仓权重=%.2f%%, 国债目标权重=%.2f%%, "
                "目标总仓位=%.2f%%"
                % (
                    current_date,
                    g.strategy_day,
                    ";".join(selected_text),
                    g.dynamic_pool_size / g.num_select * 100.0,
                    proxy_weight * 100.0,
                    float(np.sum(target_weights)) * 100.0
                )
            )

            rebalance_portfolio(
                context=context,
                data=data,
                target_weights=target_weights,
                stage="动态调仓",
                current_date=current_date
            )

            g.last_dynamic_rebalance_day = g.strategy_day

    # t=0为首次建仓日，处理完成后进入下一个策略日。
    g.strategy_day += 1


def build_fully_invested_weights(source_weights):
    """
    将未配置权重全部加入国债ETF，使目标权重合计严格等于100%。

    不改变原始基础权重和35%的动态资金参数，只将原本的现金部分
    转换成国债ETF仓位。
    """
    target_weights = np.asarray(
        source_weights,
        dtype=float
    ).copy()

    if len(target_weights) != len(g.asset_list):
        raise ValueError(
            "目标权重数量与资产数量不一致"
        )

    if not np.all(np.isfinite(target_weights)):
        raise ValueError(
            "目标权重中存在无效值"
        )

    if np.any(target_weights < 0):
        raise ValueError(
            "目标权重中存在负数"
        )

    current_total = float(np.sum(target_weights))
    remaining_weight = g.target_total_weight - current_total

    if remaining_weight < -0.00000001:
        raise ValueError(
            "目标权重合计超过100%%，当前合计为%.8f"
            % current_total
        )

    proxy_index = g.asset_list.index(g.cash_proxy_security)
    target_weights[proxy_index] += max(remaining_weight, 0.0)

    # 修正浮点数累计误差，确保权重和严格等于1。
    final_difference = (
        g.target_total_weight - float(np.sum(target_weights))
    )
    target_weights[proxy_index] += final_difference

    return target_weights


def get_weight(target_weights, security):
    """
    获取指定标的的目标权重。
    """
    security_index = g.asset_list.index(security)
    return float(target_weights[security_index])


def check_all_assets_ready():
    """
    检查全部资产是否至少存在一根已完成的前复权日K线。

    include=False明确排除当前交易日数据，防止使用尚未完成的日K线。
    """
    unavailable_assets = []

    for security in g.asset_list:
        try:
            history = get_history(
                1,
                frequency="1d",
                field="close",
                security_list=security,
                fq="pre",
                include=False
            )

            if history is None or len(history) < 1:
                unavailable_assets.append(security)
                continue

            close_values = np.asarray(
                history["close"].values,
                dtype=float
            )

            if (len(close_values) < 1 or
                    not np.isfinite(close_values[-1]) or
                    close_values[-1] <= 0):
                unavailable_assets.append(security)

        except Exception as error:
            unavailable_assets.append(security)
            log.warning(
                "[资产配置][行情异常] 标的=%s, 阶段=就绪检查, 错误=%s"
                % (security, str(error))
            )

    return len(unavailable_assets) == 0, unavailable_assets


def should_dynamic_rebalance():
    """
    判断当前是否触发动态调仓。
    """
    if g.strategy_day < g.first_dynamic_rebalance_day:
        return False

    if g.last_dynamic_rebalance_day is None:
        return True

    return (
        g.strategy_day - g.last_dynamic_rebalance_day
        >= g.rebalance_period
    )


def calculate_candidate_volatility():
    """
    使用截至上一交易日的126根前复权收盘价，计算125个日收益率。

    年化波动率计算方式与原策略一致：
        日收益率标准差 * sqrt(250)

    为对齐原代码中pandas.Series.std()的默认行为，
    标准差使用ddof=1。
    """
    volatility_map = {}

    for asset_index in g.dynamic_candidate_index:
        security = g.asset_list[asset_index]

        try:
            history = get_history(
                g.lookback_window + 1,
                frequency="1d",
                field="close",
                security_list=security,
                fq="pre",
                include=False
            )

            if history is None or len(history) < g.lookback_window + 1:
                log.warning(
                    "[资产配置][波动率异常] 标的=%s, 原因=历史K线不足, "
                    "需要=%d, 实际=%d"
                    % (
                        security,
                        g.lookback_window + 1,
                        0 if history is None else len(history)
                    )
                )
                return None

            close_values = np.asarray(
                history["close"].values,
                dtype=float
            )
            close_values = close_values[
                -(g.lookback_window + 1):
            ]

            if (len(close_values) != g.lookback_window + 1 or
                    not np.all(np.isfinite(close_values)) or
                    np.any(close_values <= 0)):
                log.warning(
                    "[资产配置][波动率异常] 标的=%s, "
                    "原因=前复权收盘价存在无效值"
                    % security
                )
                return None

            daily_returns = (
                close_values[1:] / close_values[:-1] - 1.0
            )

            if (len(daily_returns) != g.lookback_window or
                    not np.all(np.isfinite(daily_returns))):
                log.warning(
                    "[资产配置][波动率异常] 标的=%s, "
                    "原因=收益率存在无效值"
                    % security
                )
                return None

            annualized_volatility = float(
                np.std(daily_returns, ddof=1) * np.sqrt(250.0)
            )

            if not np.isfinite(annualized_volatility):
                log.warning(
                    "[资产配置][波动率异常] 标的=%s, "
                    "原因=年化波动率无效"
                    % security
                )
                return None

            volatility_map[security] = annualized_volatility

        except Exception as error:
            log.warning(
                "[资产配置][波动率异常] 标的=%s, 错误=%s"
                % (security, str(error))
            )
            return None

    # 按波动率从低到高排序。
    # 波动率相同时按候选池原始顺序排序，以保证结果稳定。
    sorted_candidates = sorted(
        volatility_map.keys(),
        key=lambda code: (
            volatility_map[code],
            g.asset_list.index(code)
        )
    )

    selected_assets = sorted_candidates[:g.num_select]

    if len(selected_assets) != g.num_select:
        log.warning(
            "[资产配置][波动率异常] 原因=可选资产数量不足, "
            "需要=%d, 实际=%d"
            % (g.num_select, len(selected_assets))
        )
        return None

    # 重置为基础权重，再将35%动态资金平均加到两只入选资产上。
    target_weights = g.base_weight.copy()
    dynamic_allocation = (
        g.dynamic_pool_size / float(g.num_select)
    )

    for security in selected_assets:
        asset_index = g.asset_list.index(security)
        target_weights[asset_index] += dynamic_allocation

    # 原本剩余的5%现金由国债ETF承接，使组合达到100%仓位。
    target_weights = build_fully_invested_weights(
        target_weights
    )

    return target_weights, selected_assets, volatility_map


def get_current_asset_value(context, data, security):
    """
    估算指定标的当前持仓市值，用于判断先卖还是后买。

    优先使用当前日线价格，获取失败时使用Position中的最新价格。
    """
    try:
        position = get_position(security)
    except Exception:
        return 0.0

    if position is None:
        return 0.0

    try:
        amount = float(position.amount)
    except Exception:
        amount = 0.0

    if amount <= 0:
        return 0.0

    current_price = 0.0

    try:
        if security in data:
            current_price = float(data[security]["close"])
    except Exception:
        current_price = 0.0

    # 【本地适配 5】本地 Position 同样提供 last_sale_price / cost_basis，
    # 三级兜底语义与 PTrade 一致。
    if (not np.isfinite(current_price) or
            current_price <= 0):
        try:
            current_price = float(position.last_sale_price)
        except Exception:
            current_price = 0.0

    if (not np.isfinite(current_price) or
            current_price <= 0):
        try:
            current_price = float(position.cost_basis)
        except Exception:
            current_price = 0.0

    if (not np.isfinite(current_price) or
            current_price <= 0):
        return 0.0

    return amount * current_price


def extract_order_id(order_result):
    """
    【本地适配 2】从下单返回值中提取订单编号字符串。

    本地框架的 order_target_value 返回 Order 对象（close/open 即时成交）或
    订单编号字符串（next_open 排队路径），也可能返回 None（未创建）。
    PTrade 原实现直接把返回值当订单号使用；此处统一归一为字符串编号，
    以便后续 get_order() 精确核验（避免把 Order 对象repr 当编号查询）。
    """
    if order_result is None:
        return None

    if isinstance(order_result, str):
        return order_result

    try:
        order_id = getattr(order_result, "order_id", None)
    except Exception:
        order_id = None

    if order_id is None:
        return None

    return str(order_id)


def extract_local_terminal_state(order_result):
    """
    【本地适配 1】读取本地 Order 对象的终态（若返回值本身已带终态）。

    返回 (ptrade_status_code, filled_amount, target_amount, price)；
    若返回值不是本地 Order 对象（例如 next_open 路径返回编号字符串），
    则返回 (None, 0.0, 0.0, 0.0)，由 get_order 走原核验路径。
    """
    if order_result is None or isinstance(order_result, str):
        return None, 0.0, 0.0, 0.0

    raw_status = get_order_attribute(
        order_result,
        "status",
        None
    )

    if raw_status is None:
        return None, 0.0, 0.0, 0.0

    ptrade_status = _LOCAL_STATUS_TO_PTRADE.get(
        str(raw_status).lower(),
        str(raw_status)
    )

    filled_amount = read_order_attribute(
        order_result,
        "filled",
        0.0
    )
    target_amount = read_order_attribute(
        order_result,
        "amount",
        0.0
    )
    price = read_order_attribute(
        order_result,
        "limit",
        0.0
    )

    try:
        filled_number = float(filled_amount)
    except Exception:
        filled_number = 0.0

    try:
        target_number = float(target_amount)
    except Exception:
        target_number = 0.0

    try:
        price_number = float(price)
    except Exception:
        price_number = 0.0

    return ptrade_status, filled_number, target_number, price_number


def submit_target_value_order(
        security,
        target_weight,
        target_value,
        stage,
        current_date,
        action):
    """
    提交单只标的目标市值委托并输出统一日志。
    """
    asset_name = g.asset_name_map.get(
        security,
        security
    )

    try:
        order_result = order_target_value(
            security,
            target_value
        )

        order_id = extract_order_id(order_result)

        if order_id is None:
            log.warning(
                "[资产配置][委托未创建] 日期=%s, 阶段=%s, "
                "方向=%s, 标的=%s, 名称=%s, "
                "目标权重=%.2f%%, 目标市值=%.2f"
                % (
                    current_date,
                    stage,
                    action,
                    security,
                    asset_name,
                    target_weight * 100.0,
                    target_value
                )
            )
        else:
            # 【本地适配 2】记录下单返回值本身的终态（close/open 即时成交模式下
            # 该终态即为最终成交结果）；next_open 排队路径下为 None，走原核验路径。
            (
                local_status,
                local_filled,
                local_amount,
                local_price
            ) = extract_local_terminal_state(order_result)

            g.pending_order_audit[str(order_id)] = {
                "security": security,
                "asset_name": asset_name,
                "stage": stage,
                "action": action,
                "submit_date": current_date,
                "target_weight": float(target_weight),
                "target_value": float(target_value),
                "audit_count": 0,
                "local_status": local_status,
                "local_filled": local_filled,
                "local_amount": local_amount,
                "local_price": local_price,
                "local_confirmed": False
            }

            log.info(
                "[资产配置][委托已创建待核验] 日期=%s, 阶段=%s, "
                "方向=%s, 标的=%s, 名称=%s, "
                "目标权重=%.2f%%, 目标市值=%.2f, "
                "订单编号=%s"
                % (
                    current_date,
                    stage,
                    action,
                    security,
                    asset_name,
                    target_weight * 100.0,
                    target_value,
                    str(order_id)
                )
            )

    except Exception as error:
        log.error(
            "[资产配置][委托异常] 日期=%s, 阶段=%s, "
            "方向=%s, 标的=%s, 名称=%s, "
            "目标权重=%.2f%%, 目标市值=%.2f, 错误=%s"
            % (
                current_date,
                stage,
                action,
                security,
                asset_name,
                target_weight * 100.0,
                target_value,
                str(error)
            )
        )


def check_running_frequency(context, current_date):
    """
    检查回测运行周期。

    PTrade日线回测通常在15:00触发handle_data；分钟回测则可能从
    09:31开始触发。代码无法修改回测界面的周期设置，只能明确告警。
    """
    data_frequency = None

    # 【本地适配 4】本地 Context 不提供 sim_params（原 try/except 已兜底）。
    try:
        data_frequency = context.sim_params.data_frequency
    except Exception:
        data_frequency = None

    current_hour = context.blotter.current_dt.hour
    current_minute = context.blotter.current_dt.minute

    is_minute_frequency = (
        str(data_frequency).lower() in [
            "minute",
            "1m",
            "min"
        ]
    )

    # 兼容部分环境未提供data_frequency的情况。
    # 日线回测正常应在15:00运行，09:31明显属于分钟周期。
    suspicious_time = (
        current_hour == 9 and current_minute == 31
    )

    if is_minute_frequency or suspicious_time:
        if g.last_frequency_warning_date != current_date:
            log.warning(
                "[资产配置][运行周期错误] 日期=%s, 当前触发时间=%02d:%02d, "
                "检测到当前回测可能仍为分钟周期。请在PTrade回测界面将"
                "运行周期改为“每日”；策略代码无法代替前端修改周期。"
                % (
                    current_date,
                    current_hour,
                    current_minute
                )
            )
            g.last_frequency_warning_date = current_date


def get_order_attribute(order_obj, attribute_name, default_value):
    """
    安全读取订单对象属性（PTrade Order 与本框架 Order 同构使用）。
    """
    try:
        return getattr(
            order_obj,
            attribute_name,
            default_value
        )
    except Exception:
        return default_value


def read_order_attribute(order_obj, attribute_name, default_value):
    """
    【本地适配 3】按等价字段回退读取订单属性，仅用于日志可读性。

    本地 Order 的 amount/limit 对应 target_amount/price，
    filled 对应 filled_amount；此处按回退链取值，不参与裁决逻辑。
    """
    fallback_names = _LOCAL_ORDER_FIELD_FALLBACK.get(
        attribute_name
    )

    if fallback_names is None:
        return get_order_attribute(
            order_obj,
            attribute_name,
            default_value
        )

    for candidate_name in fallback_names:
        candidate_value = get_order_attribute(
            order_obj,
            candidate_name,
            None
        )

        if candidate_value is not None:
            return candidate_value

    return default_value


def audit_pending_orders(context, data, current_date):
    """
    查询此前创建订单的实际状态和成交数量。

    状态说明：
    5=部撤，6=已撤，8=已成，9=废单。
    只有status=8并且filled非零，才能确认订单已经实际成交。

    【本地适配 2】本地 close/open 即时成交模式下框架 get_order() 恒返回 None，
    因此优先使用下单时记录的本地终态（下单当日即确认成交/失败）；
    仅当本地未提供终态（next_open 排队路径）时才依赖 get_order 次日核验。
    """
    if not hasattr(g, "pending_order_audit"):
        g.pending_order_audit = {}

    if not g.pending_order_audit:
        return

    status_name_map = {
        "0": "未报",
        "1": "待报",
        "2": "已报",
        "3": "已报待撤",
        "4": "部成待撤",
        "5": "部撤",
        "6": "已撤",
        "7": "部成",
        "8": "已成",
        "9": "废单",
        "+": "已受理",
        "-": "已确认",
        "C": "正报",
        "V": "已确认"
    }

    terminal_status = ["5", "6", "8", "9"]
    completed_order_ids = []

    for order_id in list(g.pending_order_audit.keys()):
        order_info = g.pending_order_audit[order_id]
        order_info["audit_count"] = (
            int(order_info.get("audit_count", 0)) + 1
        )

        try:
            order_result = get_order(order_id)

            order_obj = None
            status = ""
            order_amount = 0
            filled_amount = 0
            limit_price = 0

            if order_result is not None and len(order_result) > 0:
                order_obj = order_result[0]
                status = str(
                    read_order_attribute(
                        order_obj,
                        "status",
                        ""
                    )
                )
                order_amount = read_order_attribute(
                    order_obj,
                    "amount",
                    0
                )
                filled_amount = read_order_attribute(
                    order_obj,
                    "filled",
                    0
                )
                limit_price = read_order_attribute(
                    order_obj,
                    "limit",
                    0
                )
            elif order_info.get("local_status") is not None:
                # 【本地适配 2】即时成交模式下由下单返回值提供终态。
                status = str(order_info.get("local_status"))
                order_amount = order_info.get("local_amount", 0)
                filled_amount = order_info.get("local_filled", 0)
                limit_price = order_info.get("local_price", 0)

            if not status:
                log.warning(
                    "[资产配置][订单核验失败] 核验日期=%s, 下单日期=%s, "
                    "标的=%s, 名称=%s, 订单编号=%s, 原因=未查询到订单"
                    % (
                        current_date,
                        order_info.get("submit_date", ""),
                        order_info.get("security", ""),
                        order_info.get("asset_name", ""),
                        order_id
                    )
                )

                # 防止异常订单永久保留在待核验列表。
                if order_info["audit_count"] >= 5:
                    completed_order_ids.append(order_id)
                continue

            status_name = status_name_map.get(
                status,
                "未知状态"
            )

            try:
                filled_number = float(filled_amount)
            except Exception:
                filled_number = 0.0

            if status == "8" and abs(filled_number) > 0:
                order_info["local_confirmed"] = True
                log.info(
                    "[资产配置][实际成交确认] 核验日期=%s, 下单日期=%s, "
                    "阶段=%s, 方向=%s, 标的=%s, 名称=%s, "
                    "委托数量=%s, 实际成交数量=%s, 委托价格=%s, "
                    "状态=%s(%s), 订单编号=%s"
                    % (
                        current_date,
                        order_info.get("submit_date", ""),
                        order_info.get("stage", ""),
                        order_info.get("action", ""),
                        order_info.get("security", ""),
                        order_info.get("asset_name", ""),
                        str(order_amount),
                        str(filled_amount),
                        str(limit_price),
                        status,
                        status_name,
                        order_id
                    )
                )
            elif status == "5":
                log.warning(
                    "[资产配置][部分成交后撤单] 核验日期=%s, 下单日期=%s, "
                    "阶段=%s, 方向=%s, 标的=%s, 名称=%s, "
                    "委托数量=%s, 实际成交数量=%s, 状态=%s(%s), "
                    "订单编号=%s"
                    % (
                        current_date,
                        order_info.get("submit_date", ""),
                        order_info.get("stage", ""),
                        order_info.get("action", ""),
                        order_info.get("security", ""),
                        order_info.get("asset_name", ""),
                        str(order_amount),
                        str(filled_amount),
                        status,
                        status_name,
                        order_id
                    )
                )
            elif status in ["6", "9"]:
                log.warning(
                    "[资产配置][订单未成交] 核验日期=%s, 下单日期=%s, "
                    "阶段=%s, 方向=%s, 标的=%s, 名称=%s, "
                    "委托数量=%s, 实际成交数量=%s, 状态=%s(%s), "
                    "订单编号=%s"
                    % (
                        current_date,
                        order_info.get("submit_date", ""),
                        order_info.get("stage", ""),
                        order_info.get("action", ""),
                        order_info.get("security", ""),
                        order_info.get("asset_name", ""),
                        str(order_amount),
                        str(filled_amount),
                        status,
                        status_name,
                        order_id
                    )
                )
            else:
                log.info(
                    "[资产配置][订单仍待完成] 核验日期=%s, 下单日期=%s, "
                    "标的=%s, 名称=%s, 委托数量=%s, "
                    "实际成交数量=%s, 状态=%s(%s), 订单编号=%s"
                    % (
                        current_date,
                        order_info.get("submit_date", ""),
                        order_info.get("security", ""),
                        order_info.get("asset_name", ""),
                        str(order_amount),
                        str(filled_amount),
                        status,
                        status_name,
                        order_id
                    )
                )

            if status in terminal_status:
                completed_order_ids.append(order_id)
            elif order_info["audit_count"] >= 5:
                log.warning(
                    "[资产配置][订单核验终止] 日期=%s, 标的=%s, "
                    "订单编号=%s, 连续核验%d次仍未进入终态"
                    % (
                        current_date,
                        order_info.get("security", ""),
                        order_id,
                        order_info["audit_count"]
                    )
                )
                completed_order_ids.append(order_id)

        except Exception as error:
            log.warning(
                "[资产配置][订单核验异常] 日期=%s, 标的=%s, "
                "订单编号=%s, 错误=%s"
                % (
                    current_date,
                    order_info.get("security", ""),
                    order_id,
                    str(error)
                )
            )

            if order_info["audit_count"] >= 5:
                completed_order_ids.append(order_id)

    for order_id in completed_order_ids:
        if order_id in g.pending_order_audit:
            del g.pending_order_audit[order_id]

    log_actual_portfolio(
        context=context,
        data=data,
        current_date=current_date
    )


def log_actual_portfolio(context, data, current_date):
    """
    输出核验时点的实际持仓权重。

    该日志基于实际Position数量，不使用目标权重，可用于判断订单成交后
    组合是否真正接近调仓目标。
    """
    try:
        portfolio_value = float(
            context.portfolio.portfolio_value
        )
    except Exception:
        portfolio_value = 0.0

    if (not np.isfinite(portfolio_value) or
            portfolio_value <= 0):
        return

    position_text = []
    total_position_value = 0.0

    for security in g.asset_list:
        current_value = get_current_asset_value(
            context,
            data,
            security
        )
        total_position_value += current_value
        actual_weight = current_value / portfolio_value

        position_text.append(
            "%s(%s,市值=%.2f,实际权重=%.2f%%)"
            % (
                g.asset_name_map.get(security, security),
                security,
                current_value,
                actual_weight * 100.0
            )
        )

    cash_value = float(context.portfolio.cash)
    cash_weight = cash_value / portfolio_value

    log.info(
        "[资产配置][实际仓位快照] 日期=%s, 账户总资产=%.2f, "
        "持仓估算市值=%.2f, 可用现金=%.2f, 现金权重=%.2f%%, "
        "各资产=%s"
        % (
            current_date,
            portfolio_value,
            total_position_value,
            cash_value,
            cash_weight * 100.0,
            ";".join(position_text)
        )
    )


def rebalance_portfolio(
        context,
        data,
        target_weights,
        stage,
        current_date):
    """
    根据目标权重调整全部策略标的的目标市值。

    调仓执行顺序：
    1. 先卖出高于目标仓位的资产，释放可用资金；
    2. 再买入低于目标仓位的非国债资产；
    3. 最后调整国债ETF，由国债ETF承接剩余配置资金。

    目标权重合计严格为100%，避免策略主动保留现金。
    """
    portfolio_value = float(
        context.portfolio.portfolio_value
    )

    if (not np.isfinite(portfolio_value) or
            portfolio_value <= 0):
        log.error(
            "[资产配置][调仓失败] 日期=%s, 阶段=%s, "
            "原因=账户总资产无效, 账户总资产=%s"
            % (
                current_date,
                stage,
                str(portfolio_value)
            )
        )
        return

    target_weights = np.asarray(
        target_weights,
        dtype=float
    ).copy()

    if len(target_weights) != len(g.asset_list):
        log.error(
            "[资产配置][调仓失败] 日期=%s, 阶段=%s, "
            "原因=目标权重数量与资产数量不一致"
            % (current_date, stage)
        )
        return

    target_weight_sum = float(
        np.sum(target_weights)
    )

    if abs(
            target_weight_sum -
            g.target_total_weight) > 0.00000001:
        log.error(
            "[资产配置][调仓失败] 日期=%s, 阶段=%s, "
            "原因=目标权重合计不是100%%, 当前合计=%.8f%%"
            % (
                current_date,
                stage,
                target_weight_sum * 100.0
            )
        )
        return

    target_value_map = {}
    current_value_map = {}

    for index in range(len(g.asset_list)):
        security = g.asset_list[index]
        target_value_map[security] = (
            portfolio_value * float(target_weights[index])
        )
        current_value_map[security] = (
            get_current_asset_value(
                context,
                data,
                security
            )
        )

    log.info(
        "[资产配置][调仓开始] 日期=%s, 阶段=%s, "
        "账户总资产=%.2f, 可用现金=%.2f, "
        "目标总仓位=%.2f%%, 执行顺序=先卖后买"
        % (
            current_date,
            stage,
            portfolio_value,
            float(context.portfolio.cash),
            target_weight_sum * 100.0
        )
    )

    sell_assets = []
    buy_assets = []

    for index in range(len(g.asset_list)):
        security = g.asset_list[index]
        current_value = current_value_map[security]
        target_value = target_value_map[security]

        if current_value > target_value + 0.01:
            sell_assets.append(security)
        elif current_value < target_value - 0.01:
            buy_assets.append(security)

    # 第一阶段：先减仓，释放资金。
    for security in sell_assets:
        index = g.asset_list.index(security)
        submit_target_value_order(
            security=security,
            target_weight=float(target_weights[index]),
            target_value=target_value_map[security],
            stage=stage,
            current_date=current_date,
            action="减仓"
        )

    # 第二阶段：先买入非国债资产。
    for security in buy_assets:
        if security == g.cash_proxy_security:
            continue

        index = g.asset_list.index(security)
        submit_target_value_order(
            security=security,
            target_weight=float(target_weights[index]),
            target_value=target_value_map[security],
            stage=stage,
            current_date=current_date,
            action="加仓"
        )

    # 第三阶段：最后调整国债ETF。
    # 国债ETF作为现金承接资产，放在最后可以降低买入过程中
    # 因下单顺序造成资金不足的概率。
    if g.cash_proxy_security in buy_assets:
        proxy_index = g.asset_list.index(
            g.cash_proxy_security
        )
        submit_target_value_order(
            security=g.cash_proxy_security,
            target_weight=float(
                target_weights[proxy_index]
            ),
            target_value=target_value_map[
                g.cash_proxy_security
            ],
            stage=stage,
            current_date=current_date,
            action="现金承接"
        )

    log.info(
        "[资产配置][调仓结束] 日期=%s, 阶段=%s, "
        "目标总仓位=%.2f%%, 目标现金权重=0.00%%"
        % (
            current_date,
            stage,
            target_weight_sum * 100.0
        )
    )
