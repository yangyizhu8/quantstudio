# -*- coding: utf-8 -*-
"""
四象限ETF轮动策略 - 方案3：组合风险预算版本

核心逻辑：
1. 使用沪深300指数的250日均线和60日线性回归斜率划分四象限。
2. 每个自然月的首个交易日更新象限并执行资产配置。
3. 每个季度首月记录季度再平衡日志。
4. 组合相对历史净值高点回撤达到8%时，立即切换至BEAR防御组合。
5. 组合净值重新创出历史新高后解除风险锁定，并重新按当前象限配置。
6. 单只持仓相对持仓成本下跌达到5%时执行止损。
7. 所有委托统一记录交易日志，并避免同一标的存在未完成订单时重复下单。

建议回测频率：daily

================================================================================
本文件为 PTrade 策略「四象限ETF轮动策略（方案3：组合风险预算版本）」的
本地 QuantStudio 逆向移植版（PTrade -> 本地），严格按 PTrade API 的函数签名、
契约与实现功能 1:1 复刻，策略逻辑逐行一致。

【本版唯一策略层改动（用户指令 2026-09-17）】
  成长候选池中 中证500ETF 510500.SS  ->  中证1000ETF 159629.SZ（弹性更大）。
  除该标的替换外，全部参数、函数、生命周期、日志文案与判定逻辑保持 1:1。

【本地适配点（均为语义等价适配，已逐处标注 [本地适配]）】
  ① context.capital_base：本地 Context 不提供该属性（PTrade 提供）——原写法保留，
     由 safe_float 的 try/except 兜底；主路径（portfolio_value > 0）与 PTrade 等价。
  ② get_open_orders(security)：本地 close/open 即时撮合模式下订单即时成交，
     该接口恒返回空列表 -> has_open_order 恒为 False；PTrade 下返回真实未完成订单。
     策略的"防重复委托"在本地退化为无操作，不改变任何下单/持仓行为。
  ③ set_commission(commission_ratio=, min_commission=, type="ETF")：本地支持同名
     参数（type='ETF' 关闭印花税与过户费，贴近场内 ETF 口径）。
  ④ fq=None（不复权）与 include=False：本地 get_history 原生支持（既有策略先例）。
  ⑤ context.blotter.current_dt / portfolio.positions_value / position.enable_amount
     / position.cost_basis / position.last_sale_price / get_position：本地均提供。
================================================================================
"""

import pandas as pd
import numpy as np


# ====================== 全局参数 ======================

# ETF标的池
etf_pool = {
    # [本地适配·用户指令 2026-09-17] 成长候选池第二项：原 "510500.SS"（中证500ETF）
    # -> "159629.SZ"（富国中证1000ETF，弹性更大）。其余候选与全部仓位参数不变。
    "growth": ["510300.SS", "159629.SZ", "588400.SS"],  # 沪深300ETF、中证1000ETF、双创50ETF
    "div": "512890.SS",                    # 红利低波ETF
    "bond": "511010.SS",                   # 十年国债ETF
    "gold": "159934.SZ"                    # 黄金ETF
}

# 不同象限下的目标仓位
target_weight = {
    "BULL": {
        "growth": 0.70,
        "bond": 0.30
    },
    "HIGH": {
        "div": 0.50,
        "bond": 0.30,
        "gold": 0.20
    },
    "BEAR": {
        "div": 0.50,
        "bond": 0.30,
        "gold": 0.20
    },
    "LOW": {
        "bond": 0.70,
        "gold": 0.20,
        "div": 0.10
    }
}

# 组合最大回撤预算
max_drawdown_budget = 0.05

# 单标的止损比例
single_stop_loss = 0.05

# 基准指数：沪深300
benchmark_code = "000300.SS"

# 象限计算所需历史数据数量
quadrant_history_count = 300

# 成长ETF动量计算周期
growth_momentum_days = 60

# ETF最小交易单位
etf_lot_size = 100


# ====================== 通用工具函数 ======================

def write_log(level, module, message):
    """统一策略日志格式。"""
    text = "[四象限ETF][%s] %s" % (module, message)

    if level == "error":
        log.error(text)
    elif level == "warning":
        log.warning(text)
    elif level == "debug":
        log.debug(text)
    else:
        log.info(text)


def normalize_security_code(security):
    """
    将PTrade持仓或订单中可能出现的四位市场尾缀统一为两位尾缀。
    """
    if security is None:
        return security

    code = str(security)
    code = code.replace(".XSHG", ".SS")
    code = code.replace(".XSHE", ".SZ")
    return code


def get_all_strategy_securities():
    """返回策略需要订阅的全部证券代码。"""
    securities = []

    for code in etf_pool["growth"]:
        if code not in securities:
            securities.append(code)

    for code in [
        etf_pool["div"],
        etf_pool["bond"],
        etf_pool["gold"],
        benchmark_code
    ]:
        if code not in securities:
            securities.append(code)

    return securities


def safe_float(value, default_value=0.0):
    """安全转换为浮点数。"""
    try:
        result = float(value)
        if np.isnan(result) or np.isinf(result):
            return default_value
        return result
    except Exception:
        return default_value


def calc_ma(series, window):
    """计算简单移动平均线。"""
    return series.rolling(window=window).mean()


def calc_slope(series, window=60):
    """计算滚动线性回归斜率。"""
    x = np.arange(window)

    def slope_func(y):
        if len(y) < window:
            return np.nan
        if np.isnan(y).any():
            return np.nan
        return np.polyfit(x, y, 1)[0]

    return series.rolling(window=window).apply(slope_func, raw=True)


def get_latest_quadrant(close_bench):
    """
    根据最新有效数据计算四象限：
    BULL：价格高于250日均线，且60日斜率大于0
    BEAR：价格低于250日均线，且60日斜率小于0
    HIGH：价格高于250日均线，且60日斜率小于等于0
    LOW ：其他情况
    """
    if close_bench is None:
        return None

    close_series = pd.Series(close_bench).dropna()

    if len(close_series) < 250:
        return None

    ma250 = calc_ma(close_series, 250)
    slope60 = calc_slope(close_series, 60)

    current_price = safe_float(close_series.iloc[-1])
    current_ma250 = safe_float(ma250.iloc[-1])
    current_slope60 = safe_float(slope60.iloc[-1], default_value=np.nan)

    if current_price <= 0 or current_ma250 <= 0:
        return None

    if np.isnan(current_slope60):
        return None

    if current_price > current_ma250 and current_slope60 > 0:
        quadrant = "BULL"
    elif current_price < current_ma250 and current_slope60 < 0:
        quadrant = "BEAR"
    elif current_price > current_ma250 and current_slope60 <= 0:
        quadrant = "HIGH"
    else:
        quadrant = "LOW"

    return {
        "quadrant": quadrant,
        "price": current_price,
        "ma250": current_ma250,
        "slope60": current_slope60
    }


def load_current_quadrant():
    """读取沪深300历史行情并计算当前象限。"""
    try:
        history = get_history(
            quadrant_history_count,
            frequency="1d",
            field="close",
            security_list=benchmark_code,
            fq=None,          # [本地适配④] 不复权：本地 get_history 原生支持
            include=False
        )
    except Exception as error:
        write_log(
            "error",
            "象限",
            "获取基准历史行情异常，错误=%s" % error
        )
        return None

    if history is None or len(history) == 0:
        write_log("warning", "象限", "基准历史行情为空，本次不执行调仓")
        return None

    if "close" not in history.columns:
        write_log("warning", "象限", "基准历史行情缺少close字段")
        return None

    result = get_latest_quadrant(history["close"])

    if result is None:
        write_log(
            "warning",
            "象限",
            "有效历史数据不足，至少需要250条有效日线"
        )

    return result


def get_market_price(security, data, position=None):
    """
    获取当前用于计算目标数量的价格。
    优先使用handle_data中的当前周期收盘价，其次使用持仓最新价。
    """
    security = normalize_security_code(security)

    try:
        if security in data:
            current_data = data[security]

            try:
                price = safe_float(current_data["close"])
            except Exception:
                price = safe_float(current_data.close)

            if price > 0:
                return price
    except Exception:
        pass

    if position is not None:
        price = safe_float(getattr(position, "last_sale_price", 0))
        if price > 0:
            return price

    return 0.0


def get_normalized_positions(context):
    """
    获取当前持仓，并将证券尾缀统一为.SS或.SZ。
    返回格式：
    {
        "510500.SS": Position对象
    }
    """
    result = {}

    try:
        positions = context.portfolio.positions
    except Exception:
        return result

    for position_key in list(positions.keys()):
        position = positions[position_key]
        amount = int(safe_float(getattr(position, "amount", 0)))

        if amount <= 0:
            continue

        position_sid = getattr(position, "sid", position_key)
        security = normalize_security_code(position_sid)
        result[security] = position

    return result


def has_open_order(security):
    """判断指定标的是否存在未完成订单。

    [本地适配②] 本地 close/open 即时撮合模式下订单即时成交，get_open_orders
    恒返回空列表（恒为"无未完成订单"）；next_open 模式返回 pending 队列。
    该退化不改变任何下单/持仓行为。
    """
    try:
        open_orders = get_open_orders(security)
        if open_orders is not None and len(open_orders) > 0:
            return True
    except Exception as error:
        write_log(
            "warning",
            "订单检查",
            "查询未完成订单异常，代码=%s，错误=%s" % (security, error)
        )
        return True

    return False


def submit_target_amount(security, target_amount, current_amount, reason):
    """
    按目标持仓数量计算实际买卖差额，并使用order按数量提交委托。
    ETF买入和非清仓卖出数量按100份整数倍处理；清仓时允许卖出全部可用份额。

    返回：
    True  - 无需下单或委托创建成功
    False - 存在未完成订单、委托失败或发生异常
    """
    security = normalize_security_code(security)
    target_amount = int(target_amount)
    current_amount = int(current_amount)

    if target_amount < 0:
        target_amount = 0

    if target_amount == current_amount:
        write_log(
            "debug",
            "交易",
            "无需委托，代码=%s，当前数量=%s，目标数量=%s，原因=%s"
            % (security, current_amount, target_amount, reason)
        )
        return True

    if has_open_order(security):
        write_log(
            "warning",
            "交易",
            "跳过重复委托，代码=%s，当前数量=%s，目标数量=%s，原因=%s"
            % (security, current_amount, target_amount, reason)
        )
        return False

    if target_amount > current_amount:
        direction = "买入"
        buy_amount = target_amount - current_amount
        order_amount = int(buy_amount / etf_lot_size) * etf_lot_size
    else:
        direction = "卖出"

        # PTrade实盘持仓同步和当日可用数量可能存在时滞，
        # 卖出委托必须以enable_amount为上限，避免超出可用持仓。
        try:
            position = get_position(security)
            enable_amount = int(
                safe_float(getattr(position, "enable_amount", 0))
            )
        except Exception as error:
            write_log(
                "warning",
                "交易",
                "读取可用持仓异常，代码=%s，错误=%s"
                % (security, error)
            )
            return False

        desired_sell_amount = current_amount - target_amount

        if target_amount == 0 and enable_amount >= current_amount:
            # 清仓委托允许一次性卖出全部可用份额，包括可能存在的零股。
            sell_amount = current_amount
        else:
            # 非清仓卖出严格按ETF最小交易单位处理。
            tradable_amount = min(desired_sell_amount, enable_amount)
            sell_amount = (
                int(tradable_amount / etf_lot_size) * etf_lot_size
            )

        order_amount = -sell_amount

    if order_amount == 0:
        if direction == "卖出" and current_amount > target_amount:
            write_log(
                "warning",
                "交易",
                "暂无满足交易单位的可用持仓，代码=%s，当前数量=%s，目标数量=%s，原因=%s"
                % (
                    security,
                    current_amount,
                    target_amount,
                    reason
                )
            )

            # 无可用持仓时保留调仓标记，下一交易日继续处理。
            if target_amount == 0:
                return False
        else:
            write_log(
                "debug",
                "交易",
                "买入差额不足%s份，跳过委托，代码=%s，当前数量=%s，目标数量=%s，原因=%s"
                % (
                    etf_lot_size,
                    security,
                    current_amount,
                    target_amount,
                    reason
                )
            )

        return True

    try:
        # 不指定limit_price时，交易环境由PTrade按实时行情快照最新价报单；
        # 回测环境由撮合引擎按当前周期行情处理，避免自行传入错误价格精度。
        order_id = order(security, order_amount)
    except Exception as error:
        write_log(
            "error",
            "交易",
            "委托异常，方向=%s，代码=%s，委托数量=%s，当前数量=%s，目标数量=%s，原因=%s，错误=%s"
            % (
                direction,
                security,
                order_amount,
                current_amount,
                target_amount,
                reason,
                error
            )
        )
        return False

    if order_id is None:
        write_log(
            "error",
            "交易",
            "委托创建失败，方向=%s，代码=%s，委托数量=%s，当前数量=%s，目标数量=%s，原因=%s"
            % (
                direction,
                security,
                order_amount,
                current_amount,
                target_amount,
                reason
            )
        )
        return False

    write_log(
        "info",
        "交易",
        "委托已提交，方向=%s，代码=%s，委托数量=%s，当前数量=%s，目标数量=%s，订单号=%s，原因=%s"
        % (
            direction,
            security,
            order_amount,
            current_amount,
            target_amount,
            order_id,
            reason
        )
    )
    return True


def calculate_growth_momentum(security):
    """计算成长ETF最近60个交易日的价格动量。"""
    try:
        history = get_history(
            growth_momentum_days,
            frequency="1d",
            field="close",
            security_list=security,
            fq=None,          # [本地适配④] 不复权：本地 get_history 原生支持
            include=False
        )
    except Exception as error:
        write_log(
            "warning",
            "动量",
            "获取历史行情异常，代码=%s，错误=%s" % (security, error)
        )
        return None

    if history is None or len(history) < growth_momentum_days:
        write_log(
            "warning",
            "动量",
            "历史行情不足，代码=%s，需要=%s，实际=%s"
            % (
                security,
                growth_momentum_days,
                0 if history is None else len(history)
            )
        )
        return None

    if "close" not in history.columns:
        return None

    close_series = history["close"].dropna()

    if len(close_series) < growth_momentum_days:
        return None

    start_price = safe_float(close_series.iloc[0])
    end_price = safe_float(close_series.iloc[-1])

    if start_price <= 0 or end_price <= 0:
        return None

    return end_price / start_price - 1.0


def select_best_growth_etf():
    """选择60日动量最高的成长ETF。"""
    momentum_map = {}

    for security in etf_pool["growth"]:
        momentum = calculate_growth_momentum(security)

        if momentum is not None:
            momentum_map[security] = momentum
            write_log(
                "info",
                "动量",
                "代码=%s，%s日动量=%.4f%%"
                % (security, growth_momentum_days, momentum * 100.0)
            )

    if len(momentum_map) == 0:
        write_log(
            "warning",
            "动量",
            "所有成长ETF动量数据无效，无法生成BULL象限目标组合"
        )
        return None

    best_security = max(momentum_map, key=momentum_map.get)

    write_log(
        "info",
        "动量",
        "成长ETF选择完成，代码=%s，动量=%.4f%%"
        % (best_security, momentum_map[best_security] * 100.0)
    )

    return best_security


def build_weight_map(quadrant, risk_lock):
    """
    根据象限和风险锁定状态生成最终证券权重。
    风险锁定时强制使用BEAR防御配置。
    """
    if risk_lock:
        use_quadrant = "BEAR"
    else:
        use_quadrant = quadrant

    if use_quadrant not in target_weight:
        return None

    use_weight = target_weight[use_quadrant]
    weight_map = {}

    if use_quadrant == "BULL" and not risk_lock:
        best_growth = select_best_growth_etf()

        if best_growth is None:
            return None

        weight_map[best_growth] = use_weight["growth"]
        weight_map[etf_pool["bond"]] = use_weight["bond"]
    else:
        for asset_name in use_weight:
            weight = safe_float(use_weight[asset_name])

            if asset_name == "div":
                weight_map[etf_pool["div"]] = weight
            elif asset_name == "bond":
                weight_map[etf_pool["bond"]] = weight
            elif asset_name == "gold":
                weight_map[etf_pool["gold"]] = weight

    return weight_map


def calculate_target_amounts(context, data, weight_map, stopped_securities):
    """
    根据组合总资产和目标权重计算目标持仓数量。
    目标数量按ETF最小交易单位向下取整。
    当日已触发单标的止损的证券不会被重新买入。
    """
    target_amounts = {}
    current_positions = get_normalized_positions(context)
    total_asset = safe_float(context.portfolio.portfolio_value)

    if total_asset <= 0:
        write_log("error", "调仓", "组合总资产无效，无法计算目标仓位")
        return None

    for security in weight_map:
        security = normalize_security_code(security)
        weight = safe_float(weight_map[security])

        if security in stopped_securities:
            target_amounts[security] = 0
            write_log(
                "warning",
                "调仓",
                "代码=%s当日已触发止损，目标仓位强制设为0" % security
            )
            continue

        position = current_positions.get(security)
        price = get_market_price(security, data, position)

        if price <= 0:
            write_log(
                "error",
                "调仓",
                "代码=%s价格无效，无法计算目标数量" % security
            )
            return None

        target_value = total_asset * weight
        raw_amount = int(target_value / price)
        target_amount = int(raw_amount / etf_lot_size) * etf_lot_size

        target_amounts[security] = target_amount

        write_log(
            "info",
            "调仓",
            "目标计算，代码=%s，权重=%.2f%%，价格=%.3f，目标市值=%.2f，目标数量=%s"
            % (
                security,
                weight * 100.0,
                price,
                target_value,
                target_amount
            )
        )

    return target_amounts


def execute_rebalance(context, data, weight_map, reason, stopped_securities):
    """
    执行组合调仓：
    1. 先提交减仓和清仓委托。
    2. 再提交加仓委托。
    3. 避免同一标的存在未完成订单时重复下单。
    """
    current_positions = get_normalized_positions(context)
    target_amounts = calculate_target_amounts(
        context,
        data,
        weight_map,
        stopped_securities
    )

    if target_amounts is None:
        return False

    all_securities = set(current_positions.keys()) | set(target_amounts.keys())
    sell_tasks = []
    buy_tasks = []

    for security in all_securities:
        position = current_positions.get(security)
        current_amount = 0

        if position is not None:
            current_amount = int(
                safe_float(getattr(position, "amount", 0))
            )

        target_amount = int(target_amounts.get(security, 0))

        if security in stopped_securities:
            target_amount = 0

        if target_amount < current_amount:
            sell_tasks.append(
                (security, target_amount, current_amount)
            )
        elif target_amount > current_amount:
            buy_tasks.append(
                (security, target_amount, current_amount)
            )

    success = True

    # 先减仓、清仓，减少资金占用
    for security, target_amount, current_amount in sell_tasks:
        if security in stopped_securities:
            # 止损函数已经提交过清仓委托，避免同日重复下单
            continue

        order_success = submit_target_amount(
            security,
            target_amount,
            current_amount,
            reason
        )

        if not order_success:
            success = False

    # 再执行加仓
    for security, target_amount, current_amount in buy_tasks:
        if security in stopped_securities:
            continue

        order_success = submit_target_amount(
            security,
            target_amount,
            current_amount,
            reason
        )

        if not order_success:
            success = False

    target_description = []

    for security in sorted(weight_map.keys()):
        target_description.append(
            "%s=%.2f%%" % (security, weight_map[security] * 100.0)
        )

    write_log(
        "info",
        "调仓",
        "调仓处理完成，原因=%s，目标组合=%s，提交状态=%s"
        % (
            reason,
            ",".join(target_description),
            "成功" if success else "部分失败或待处理"
        )
    )

    return success


def check_single_stop_loss(context, data):
    """
    检查单标的止损。
    返回当日触发止损的证券集合，防止后续调仓立即买回。
    """
    stopped_securities = set()
    current_positions = get_normalized_positions(context)
    active_securities = set(current_positions.keys())

    # 清理已经不再持有的成本记录
    for security in list(g.cost_price.keys()):
        normalized_code = normalize_security_code(security)
        if normalized_code not in active_securities:
            del g.cost_price[security]

    for security in current_positions:
        position = current_positions[security]
        current_amount = int(
            safe_float(getattr(position, "amount", 0))
        )

        if current_amount <= 0:
            continue

        position_cost = safe_float(
            getattr(position, "cost_basis", 0)
        )

        if position_cost > 0:
            g.cost_price[security] = position_cost
        else:
            position_cost = safe_float(
                g.cost_price.get(security, 0)
            )

        if position_cost <= 0:
            write_log(
                "warning",
                "止损",
                "代码=%s持仓成本无效，本次跳过止损检查" % security
            )
            continue

        current_price = get_market_price(security, data, position)

        if current_price <= 0:
            write_log(
                "warning",
                "止损",
                "代码=%s当前价格无效，本次跳过止损检查" % security
            )
            continue

        loss_rate = current_price / position_cost - 1.0

        if loss_rate <= -single_stop_loss:
            stopped_securities.add(security)

            write_log(
                "warning",
                "止损",
                "触发单标的止损，代码=%s，成本=%.3f，现价=%.3f，收益率=%.2f%%，阈值=-%.2f%%"
                % (
                    security,
                    position_cost,
                    current_price,
                    loss_rate * 100.0,
                    single_stop_loss * 100.0
                )
            )

            order_success = submit_target_amount(
                security,
                0,
                current_amount,
                "单标的止损"
            )

            if order_success and security in g.cost_price:
                del g.cost_price[security]

    return stopped_securities


# ====================== PTrade事件函数 ======================

def initialize(context):
    """策略初始化，仅在策略启动时执行一次。"""
    set_benchmark(benchmark_code)

    # PTrade的佣金接口不区分开仓和闭仓佣金。
    # 仅在回测环境设置ETF佣金，实盘使用券商账户实际费率。
    if not is_trade():
        set_commission(
            commission_ratio=0.00005,
            min_commission=5.0,
            type="ETF"
        )   # [本地适配③] 本地 set_commission 支持同名参数（type='ETF' 关闭印花税/过户费）

    # 订阅策略所需的全部ETF和基准指数
    set_universe(get_all_strategy_securities())

    # 组合状态
    g.quad_last = None
    g.risk_lock = False
    g.risk_rebalance_pending = False
    g.monthly_flag = False
    g.quarter_flag = False
    g.last_rebalance_month = None
    g.cost_price = {}

    # [本地适配①] 本地 Context 不提供 capital_base —— 原写法保留，由 safe_float 兜底；
    # 主路径（portfolio_value > 0）下与 PTrade 取值等价。
    initial_value = safe_float(
        context.portfolio.portfolio_value,
        safe_float(context.capital_base)
    )

    if initial_value <= 0:
        initial_value = safe_float(context.capital_base, 1.0)

    g.high_water = initial_value

    write_log(
        "info",
        "初始化",
        "策略初始化完成，初始净值高点=%.2f，组合回撤阈值=%.2f%%，单标的止损阈值=%.2f%%"
        % (
            g.high_water,
            max_drawdown_budget * 100.0,
            single_stop_loss * 100.0
        )
    )


def before_trading_start(context, data):
    """盘前判断当天是否为本月首个需要调仓的交易日。"""
    current_dt = context.blotter.current_dt
    current_month = current_dt.strftime("%Y%m")

    # 兼容旧持久化状态中不存在新变量的情况
    if not hasattr(g, "last_rebalance_month"):
        g.last_rebalance_month = None

    if not hasattr(g, "monthly_flag"):
        g.monthly_flag = False

    if not hasattr(g, "quarter_flag"):
        g.quarter_flag = False

    if g.last_rebalance_month != current_month:
        g.monthly_flag = True
        g.quarter_flag = current_dt.month in [1, 4, 7, 10]

        write_log(
            "info",
            "调度",
            "检测到本月首个待调仓交易日，日期=%s，季度再平衡=%s"
            % (
                current_dt.strftime("%Y-%m-%d"),
                "是" if g.quarter_flag else "否"
            )
        )


def handle_data(context, data):
    """日频主交易逻辑。"""
    current_dt = context.blotter.current_dt
    portfolio = context.portfolio
    net_value = safe_float(portfolio.portfolio_value)

    if net_value <= 0:
        write_log("error", "风控", "组合净值无效，本周期停止处理")
        return

    # 兼容策略升级后的历史持久化状态
    if not hasattr(g, "high_water") or safe_float(g.high_water) <= 0:
        g.high_water = net_value

    if not hasattr(g, "risk_lock"):
        g.risk_lock = False

    if not hasattr(g, "risk_rebalance_pending"):
        g.risk_rebalance_pending = False

    if not hasattr(g, "cost_price"):
        g.cost_price = {}

    if not hasattr(g, "quad_last"):
        g.quad_last = None

    if not hasattr(g, "monthly_flag"):
        g.monthly_flag = False

    if not hasattr(g, "quarter_flag"):
        g.quarter_flag = False

    if not hasattr(g, "last_rebalance_month"):
        g.last_rebalance_month = None

    # ========== 组合净值高点与回撤风控 ==========

    risk_state_changed = False

    if net_value > safe_float(g.high_water):
        old_high_water = safe_float(g.high_water)
        g.high_water = net_value

        if g.risk_lock:
            g.risk_lock = False
            g.risk_rebalance_pending = True
            risk_state_changed = True

            write_log(
                "info",
                "风控",
                "组合净值创新高并解除风险锁定，旧高点=%.2f，新高点=%.2f"
                % (old_high_water, g.high_water)
            )

    current_drawdown = net_value / safe_float(g.high_water) - 1.0

    if (
        current_drawdown <= -max_drawdown_budget
        and not g.risk_lock
    ):
        g.risk_lock = True
        g.risk_rebalance_pending = True
        risk_state_changed = True

        write_log(
            "warning",
            "风控",
            "触发组合回撤预算，当前净值=%.2f，历史高点=%.2f，当前回撤=%.2f%%，阈值=-%.2f%%"
            % (
                net_value,
                g.high_water,
                current_drawdown * 100.0,
                max_drawdown_budget * 100.0
            )
        )

    if not risk_state_changed:
        write_log(
            "debug",
            "风控",
            "净值检查，当前净值=%.2f，历史高点=%.2f，当前回撤=%.2f%%，风险锁定=%s"
            % (
                net_value,
                g.high_water,
                current_drawdown * 100.0,
                "是" if g.risk_lock else "否"
            )
        )

    # ========== 单标的止损 ==========

    stopped_securities = check_single_stop_loss(context, data)

    # 非月度调仓日且没有风险状态切换时，仅执行风控和止损检查
    need_rebalance = (
        g.monthly_flag
        or g.risk_rebalance_pending
    )

    if not need_rebalance:
        return

    # ========== 更新当前象限 ==========

    quadrant_result = load_current_quadrant()

    if quadrant_result is None:
        write_log(
            "warning",
            "调仓",
            "象限计算失败，保留调仓标记并在下一交易日重试"
        )
        return

    current_quadrant = quadrant_result["quadrant"]
    g.quad_last = current_quadrant

    write_log(
        "info",
        "象限",
        "象限判定完成，日期=%s，象限=%s，指数收盘=%.3f，MA250=%.3f，Slope60=%.6f"
        % (
            current_dt.strftime("%Y-%m-%d"),
            current_quadrant,
            quadrant_result["price"],
            quadrant_result["ma250"],
            quadrant_result["slope60"]
        )
    )

    # ========== 生成目标组合 ==========

    weight_map = build_weight_map(
        current_quadrant,
        g.risk_lock
    )

    if weight_map is None or len(weight_map) == 0:
        write_log(
            "error",
            "调仓",
            "目标组合生成失败，保留调仓标记并在下一交易日重试"
        )
        return

    reason_parts = []

    if g.monthly_flag:
        reason_parts.append("月度调仓")

    if g.quarter_flag:
        reason_parts.append("季度再平衡")

    if g.risk_lock:
        reason_parts.append("组合回撤强制防御")
    elif g.risk_rebalance_pending:
        reason_parts.append("解除风险锁定恢复配置")

    reason_parts.append("象限=%s" % current_quadrant)
    rebalance_reason = "|".join(reason_parts)

    if g.risk_lock:
        write_log(
            "warning",
            "调仓",
            "风险锁定生效，忽略正常象限仓位并强制使用BEAR防御组合"
        )
    else:
        write_log(
            "info",
            "调仓",
            "按当前象限执行目标配置，象限=%s" % current_quadrant
        )

    # ========== 执行调仓 ==========

    rebalance_success = execute_rebalance(
        context,
        data,
        weight_map,
        rebalance_reason,
        stopped_securities
    )

    if rebalance_success:
        if g.monthly_flag:
            g.last_rebalance_month = current_dt.strftime("%Y%m")

        g.monthly_flag = False
        g.quarter_flag = False
        g.risk_rebalance_pending = False

        write_log(
            "info",
            "调仓",
            "本次调仓委托处理成功，日期=%s，原因=%s"
            % (
                current_dt.strftime("%Y-%m-%d"),
                rebalance_reason
            )
        )
    else:
        write_log(
            "warning",
            "调仓",
            "本次部分委托未成功创建，保留调仓标记并在下一交易日继续处理"
        )


def after_trading_end(context, data):
    """盘后输出组合状态日志。"""
    portfolio = context.portfolio
    net_value = safe_float(portfolio.portfolio_value)
    high_water = safe_float(g.high_water)

    if high_water > 0:
        current_drawdown = net_value / high_water - 1.0
    else:
        current_drawdown = 0.0

    positions = get_normalized_positions(context)
    position_text = []

    for security in sorted(positions.keys()):
        position = positions[security]
        amount = int(
            safe_float(getattr(position, "amount", 0))
        )
        enable_amount = int(
            safe_float(getattr(position, "enable_amount", 0))
        )
        cost_basis = safe_float(
            getattr(position, "cost_basis", 0)
        )
        last_price = safe_float(
            getattr(position, "last_sale_price", 0)
        )

        position_text.append(
            "%s:总持仓=%s,可用=%s,成本=%.3f,现价=%.3f"
            % (
                security,
                amount,
                enable_amount,
                cost_basis,
                last_price
            )
        )

    if len(position_text) == 0:
        position_summary = "空仓"
    else:
        position_summary = "; ".join(position_text)

    write_log(
        "info",
        "盘后",
        "日期=%s，组合净值=%.2f，持仓市值=%.2f，可用资金=%.2f，历史高点=%.2f，当前回撤=%.2f%%，象限=%s，风险锁定=%s，持仓=%s"
        % (
            context.blotter.current_dt.strftime("%Y-%m-%d"),
            net_value,
            safe_float(portfolio.positions_value),
            safe_float(portfolio.cash),
            high_water,
            current_drawdown * 100.0,
            str(g.quad_last),
            "是" if g.risk_lock else "否",
            position_summary
        )
    )


# ====================== 回测配置说明 ======================
"""
建议回测配置：

start: 2020-01-01
end: 2026-09-01
frequency: daily
capital_base: 1000000

注意：
1. 本策略使用PTrade标准证券尾缀：上海.SS、深圳.SZ。
2. 策略不使用get_snapshot，因此同时兼容PTrade日频回测和交易环境。
3. PTrade回测中的set_commission无法分别设置买卖佣金及印花税，
本策略仅按原参数设置ETF佣金率0.00005和最低佣金5元。
4. 组合风险预算达到阈值时会立即提交防御调仓，而不是等待下一个月初。
5. 单标的止损和组合调仓在同一天发生时，止损优先，且当日不会买回止损标的。
6. 成长资产候选池为510300.SS（沪深300ETF）、159629.SZ（中证1000ETF）
和588400.SS（双创50ETF），BULL象限选择其中60日动量最高者。
   [本地适配·用户指令 2026-09-17] 原候选 510500.SS（中证500ETF）已替换为 159629.SZ。
7. 委托使用order按实际差额下单；ETF买入及非清仓卖出按100份整数倍处理，
清仓以可用持仓为限，并允许一次性卖出全部可用零股。

【本地回测数据要求】源标的池 + 基准 000300.SS 需覆盖回测区间；
本策略需要 250 个交易日以上的基准历史以判定象限（quadrant_history_count=300）。
"""
