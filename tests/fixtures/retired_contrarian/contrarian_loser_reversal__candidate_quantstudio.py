# QUANTSTUDIO USER-PYQT BACKTEST CANDIDATE
# strategy_id=contrarian_loser_reversal
# canonical_sha256=46fcec7d8f5a34b78c87d4c46cb79fedc19f78c5f3f5a385b8fa781b49d5f80d
# STATUS=UNVALIDATED_BY_BACKTEST
# NOT_FOR_PTRADE_UPLOAD=true
# Formal publication requires hash-bound R5 evidence PASS.

"""
contrarian_loser_reversal.py - agent-authored canonical QuantStudio/PTrade strategy.

反转策略（输家组合均值回归 / De Bondt & Thaler 1985 过度反应假说）：
  按过去 252 个交易日（前复权）累计跌幅降序排名，剔除 ST/停牌/退市/科创板/北交所/
  上市不足 252 日后，取跌幅最大的 20 只等权买入，季度末交易日收盘重排。
对标沪深300，资金 100 万元，单只目标市值 5 万元（上限 5%）。

This file is intentionally a lifecycle/API scaffold, not a strategy template.
Public PTrade-style APIs, numpy/pandas, g and log are injected locally.
The validated file is published unchanged to both QuantStudio and PTrade.
"""
import numpy as np

STRATEGY_ID = "contrarian_loser_reversal"
DESIGN_VERSION = "2.1"


def _ensure_runtime_state():
    """Idempotently create every g field used by any callback.

    Real PTrade may continue later lifecycle calls after initialize raises, so
    state construction must never reset existing fields.
    """
    if not hasattr(g, "universe"):
        g.universe = []
    if not hasattr(g, "capital"):
        g.capital = 1_000_000.0
    if not hasattr(g, "top_n"):
        g.top_n = 20
    if not hasattr(g, "lookback"):
        g.lookback = 252
    if not hasattr(g, "per_target"):
        g.per_target = 50_000.0
    if not hasattr(g, "last_rebalance_ymd"):
        g.last_rebalance_ymd = 0
    if not hasattr(g, "targets"):
        g.targets = []


def _ymd(value):
    """Normalize a date-like value to an int YYYYmmdd."""
    if value is None:
        return 0
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str):
        digits = "".join(ch for ch in value if ch.isdigit())
        return int(digits) if digits else 0
    if hasattr(value, "year"):
        return value.year * 10000 + value.month * 100 + value.day
    text = str(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else 0


def _is_quarter_end_last_trading_day(context):
    """True if context.current_dt is the last trading day of its quarter."""
    dt = context.current_dt
    y, m = dt.year, dt.month
    q_start_month = ((m - 1) // 3) * 3 + 1
    q_end_month = q_start_month + 2
    last_day = {3: 31, 6: 30, 9: 30, 12: 31}[q_end_month]
    start_ymd = "%04d%02d01" % (y, q_start_month)
    end_ymd = "%04d%02d%02d" % (y, q_end_month, last_day)
    trade_days = get_trade_days(start_date=start_ymd, end_date=end_ymd)
    if trade_days is None or len(trade_days) == 0:
        return False
    return _ymd(trade_days[-1]) == _ymd(dt)


def initialize(context):
    """Configure parameters, costs, universe and scheduled callbacks."""
    _ensure_runtime_state()
    set_benchmark("000300.SS")
    set_commission(commission_ratio=0.0003, min_commission=5.0)
    set_slippage(slippage=0.0)
    run_daily(context, rebalance, time="15:00")
    g.per_target = min(g.capital / g.top_n, 0.05 * g.capital)


def before_trading_start(context, data):
    """Build the PIT universe without same-day future data.

    filter_stock_by_status is a before_trading_start-only API; the scheduled
    rebalance callback must use get_stock_status for current status checks.
    """
    _ensure_runtime_state()
    universe = []
    try:
        all_codes = get_Ashares()
    except Exception as exc:
        log.warning("get_Ashares failed: %s" % exc)
        all_codes = []
    for code in all_codes:
        if code.startswith("688"):
            continue
        if code.endswith(".BJ"):
            continue
        universe.append(code)
    try:
        clean = filter_stock_by_status(
            stocks=universe, filter_type=["ST", "HALT", "DELISTING"])
    except Exception as exc:
        log.warning("filter_stock_by_status failed: %s" % exc)
        clean = universe
    g.universe = list(clean) if clean is not None else list(universe)


def handle_data(context, data):
    """Evaluate bar-dependent logic for the declared engine profile."""
    _ensure_runtime_state()
    log.debug("handle_data %s" % context.current_dt.date())


def after_trading_end(context, data):
    """Record diagnostics and reconcile persistent state after the close."""
    _ensure_runtime_state()
    log.info("after_trading_end %s targets=%d" % (context.current_dt.date(), len(g.targets)))


def rebalance(context):
    """Scheduled component: 季度末交易日收盘执行输家组合重排与等权调仓."""
    _ensure_runtime_state()
    if not _is_quarter_end_last_trading_day(context):
        return
    today_ymd = _ymd(context.current_dt)
    if today_ymd == g.last_rebalance_ymd:
        return

    universe = g.universe
    if not universe:
        log.warning("rebalance skipped: empty universe")
        return

    # 过去 lookback 交易日的前复权累计跌幅 = (old - new) / new
    ranked = []
    try:
        hist = get_history(
            count=g.lookback, frequency="1d", field=["close"],
            security_list=universe, fq="pre", include=False, is_dict=True,
        )
    except Exception as exc:
        log.warning("get_history failed: %s" % exc)
        return
    for code, df in hist.items():
        if df is None or len(df) < g.lookback:
            continue
        closes = np.asarray(df["close"].values, dtype=float)
        if len(closes) < 2 or closes[-1] <= 0:
            continue
        drop = (closes[0] - closes[-1]) / closes[-1]
        ranked.append((drop, code))
    if not ranked:
        log.warning("rebalance skipped: no ranked candidates")
        return
    ranked.sort(reverse=True)
    selected = [code for _, code in ranked[: g.top_n]]

    # 盘中状态复核（仅 get_stock_status，避免 scheduled 中调用 filter_stock_by_status）
    halt_map = get_stock_status(stocks=selected, query_type="HALT", query_date=None) or {}
    delist_map = get_stock_status(stocks=selected, query_type="DELISTING", query_date=None) or {}
    tradable = []
    for code in selected:
        if halt_map.get(code):
            continue
        if delist_map.get(code):
            continue
        tradable.append(code)

    g.targets = tradable
    target_set = set(tradable)

    # 清仓不在目标内的持仓
    try:
        positions = get_positions()
    except Exception as exc:
        log.warning("get_positions failed: %s" % exc)
        positions = {}
    for code in list(positions.keys()):
        if code not in target_set:
            order_target_value(security=code, value=0.0)

    # 等权买入目标（单只上限 5% 由 per_target 保证）
    for code in tradable:
        order_target_value(security=code, value=g.per_target)

    g.last_rebalance_ymd = today_ymd
    log.info("rebalance %s selected=%d" % (context.current_dt.date(), len(tradable)))
