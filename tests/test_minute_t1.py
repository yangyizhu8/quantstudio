"""PR4 契约测试：分钟 T+1 与 ETF per-code T+0（docs/etf-t0-per-code-design.md §4/§9.1）。

验证目标：
1. 黄金 3：T 日分钟买入股票，当日禁止卖出（T+1）
2. 决策 4 隔离：日线 Profile 下 ETF 仍 T+1（守护黄金基线）
3. per-code T+0（etf_t0=True + fund_type ∈ {qdii,gold,commodity,bond,money}）：当日买入当日可卖
4. per-code T+1：fund_type=equity / 未知代码（fail-closed，含 LOF 类）当日卖被拒
5. etf_t0=False：即便 T+0 分类也按 T+1（默认路径零变化）
6. 装载失败 fail-closed：etf_basic 缺失 → 全 T+1 + warning
7. T+1 次日盘前解锁：昨日买入 equity 今日可卖
"""
import logging

import pandas as pd
import pytest

from tests.conftest import minute_row, daily_row, etf_basic_row, make_providers

DAY = "2026-01-05"
DAY2 = "2026-01-06"

T0_FUND_TYPES = ["qdii", "gold", "commodity", "bond", "money"]


def _etf_bars(code="159870", day=DAY):
    return [minute_row(code, day, h, m, p) for h, m, p in
            [(9, 35, 1.0), (10, 0, 1.1), (14, 55, 1.5), (15, 0, 1.6)]]


def _etf_daily(code="159870", day=DAY):
    return [daily_row(code, day, 1.0, preclose=0.95, pctchg=0.0526)]   # 5.26% 非涨停


def _stock_bars(day=DAY):
    return [minute_row("600000", day, h, m, 10.0) for h, m in
            [(9, 35), (10, 0), (14, 0), (14, 55), (15, 0)]]


def _stock_daily(day=DAY, pctchg=0.0):
    return [daily_row("600000", day, 10.0, pctchg=pctchg)]


def _run_engine(db, strategy, cal, start=DAY, end=DAY, etf_t0=False):
    """构造 minute-bar-v1 引擎并运行，返回引擎（供断言内部状态）。"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path=str(db), strategy=strategy, start=start, end=end,
        engine_profile="minute-bar-v1", match_price_mode="close",
        etf_t0=etf_t0, providers=make_providers(db, cal),
    )
    engine.run()
    return engine


def _buy_sell_strategy(code, state, buy_time=935, sell_time=1455, buy_day=DAY, sell_day=DAY):
    """09:35 买入 → 14:55 全卖；记录 order 返回值与成交状态。"""
    def handle_data(context, data):
        from quantstudio.backtest.ptrade_api import order_target_value
        hhmm = context.current_dt.hour * 100 + context.current_dt.minute
        d = str(context.current_dt.date())
        if d == buy_day and hhmm == buy_time:
            order_target_value(code, 10000)
        elif d == sell_day and hhmm == sell_time:
            o = order_target_value(code, 0)
            state['status'] = o.status if o is not None else None
            if o is not None and o.status == "filled":
                state['sold'] = True
    return {'initialize': lambda c: None, 'handle_data': handle_data}


# ---------------------------------------------------------------------------
# 1. 股票 T+1（黄金 3，现状守护）
# ---------------------------------------------------------------------------

def test_stock_minute_buy_then_same_day_sell_blocked(build_db, cal):
    """T 日 09:35 分钟买入股票，14:55 卖出被 T+1 阻断（can_sell=0）"""
    db = build_db(stock_minutes=_stock_bars(), stock_daily=_stock_daily())
    state = {'sell_status': None, 'sold': False}
    _run_engine(db, _buy_sell_strategy("600000.SH", state), cal)
    assert state['sold'] is False
    assert state['status'] in (None, "rejected")


def test_daily_profile_etf_remains_t1():
    """日线 Profile（默认）下 etf_t0 强制为 False（守护黄金基线）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={}, start=DAY, end=DAY,
        engine_profile="daily-bar-v1", etf_t0=True,
    )
    assert engine.etf_t0 is False


def test_minute_profile_etf_t0_default_false():
    """分钟 Profile 默认 etf_t0=False（默认路径零变化）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={}, start=DAY, end=DAY,
        engine_profile="minute-bar-v1", match_price_mode="close",
    )
    assert engine.etf_t0 is False


# ---------------------------------------------------------------------------
# 3. per-code T+0：fund_type ∈ T0 集合 → 当日买当日卖成交
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ftype", T0_FUND_TYPES)
def test_etf_t0_per_code_t0_types_allow_same_day_sell(build_db, cal, ftype):
    """etf_t0=True + fund_type∈{qdii,gold,commodity,bond,money}：当日卖出成交"""
    db = build_db(etf_minutes=_etf_bars(), etf_daily=_etf_daily(),
                  etf_basic=[etf_basic_row("159870", ftype)])
    state = {'status': None, 'sold': False}
    _run_engine(db, _buy_sell_strategy("159870.SZ", state), cal, etf_t0=True)
    assert state['sold'] is True, f"fund_type={ftype} 当日卖应成交"


def test_etf_t0_per_code_520830_qdii_same_day_sell(build_db, cal):
    """平台偏差标的（520830 沙特，qdii）：本地按真实规则 T+0 放行（design §8.4）"""
    db = build_db(etf_minutes=_etf_bars("520830"), etf_daily=_etf_daily("520830"),
                  etf_basic=[etf_basic_row("520830", "qdii")])
    state = {'status': None, 'sold': False}
    _run_engine(db, _buy_sell_strategy("520830.SS", state), cal, etf_t0=True)
    assert state['sold'] is True


# ---------------------------------------------------------------------------
# 4. per-code T+1：equity / 未知代码 fail-closed
# ---------------------------------------------------------------------------

def test_etf_t0_per_code_equity_same_day_sell_blocked(build_db, cal):
    """fund_type=equity：当日买入当日卖被拒（T+1）"""
    db = build_db(etf_minutes=_etf_bars(), etf_daily=_etf_daily(),
                  etf_basic=[etf_basic_row("159870", "equity")])
    state = {'status': None, 'sold': False}
    _run_engine(db, _buy_sell_strategy("159870.SZ", state), cal, etf_t0=True)
    assert state['sold'] is False
    assert state['status'] in (None, "rejected")


def test_etf_t0_per_code_unknown_code_fail_closed(build_db, cal):
    """代码不在 etf_basic 分类映射（如 LOF 类）→ fail-closed T+1（design §4）"""
    db = build_db(etf_minutes=_etf_bars(), etf_daily=_etf_daily(),
                  etf_basic=[etf_basic_row("518880", "gold")])  # 159870 不在映射中
    state = {'status': None, 'sold': False}
    _run_engine(db, _buy_sell_strategy("159870.SZ", state), cal, etf_t0=True)
    assert state['sold'] is False
    assert state['status'] in (None, "rejected")


# ---------------------------------------------------------------------------
# 5. etf_t0=False：T+0 分类也按 T+1（默认路径）
# ---------------------------------------------------------------------------

def test_etf_t0_false_blocks_even_t0_classified(build_db, cal):
    """etf_t0=False（默认）：gold 分类也按 T+1，当日卖被拒；且不触达分类装载（零查询零 warning）"""
    db = build_db(etf_minutes=_etf_bars(), etf_daily=_etf_daily(),
                  etf_basic=[etf_basic_row("159870", "gold")])
    state = {'status': None, 'sold': False}
    engine = _run_engine(db, _buy_sell_strategy("159870.SZ", state), cal, etf_t0=False)
    assert state['sold'] is False
    assert state['status'] in (None, "rejected")
    assert engine._t0_cache is None   # 短路：etf_t0=False 路径不装载分类（design §5.1）


# ---------------------------------------------------------------------------
# 6. 装载失败 fail-closed：etf_basic 缺失 → 全 T+1 + warning（不崩）
# ---------------------------------------------------------------------------

def test_etf_t0_load_failure_fail_closed_all_t1(build_db, cal, caplog):
    """etf_basic 表缺失 → 装载失败 warning + 全 T+1（fail-closed，不崩溃）"""
    db = build_db(etf_minutes=_etf_bars(), etf_daily=_etf_daily())  # 无 etf_basic 表
    state = {'status': None, 'sold': False}
    with caplog.at_level(logging.WARNING, logger="quantstudio.backtest.backtest_engine"):
        _run_engine(db, _buy_sell_strategy("159870.SZ", state), cal, etf_t0=True)
    assert state['sold'] is False
    # 表缺失（装载失败）或空表（装载结果为空）都属 fail-closed，两分支文案不同
    assert any("ETF T+0 分类装载" in r.getMessage() and "fail-closed" in r.getMessage()
               for r in caplog.records)


# ---------------------------------------------------------------------------
# 7. T+1 次日盘前解锁：昨日买入 equity 今日可卖
# ---------------------------------------------------------------------------

def _cal_2days():
    """两日日历：get_trade_days 必须按 [start,end] 过滤（数据访问层会按日查询窗口）。"""
    class Cal:
        DAYS = (DAY, DAY2)

        def get_trade_days(self, start, end):
            s, e = str(start)[:10], str(end)[:10]
            return [pd.Timestamp(d, tz="Asia/Shanghai").to_pydatetime()
                    for d in self.DAYS if s <= d <= e]

        def get_trading_day(self, date, offset=0):
            ds = str(date)[:10]
            if ds in self.DAYS:
                idx = self.DAYS.index(ds) + int(offset)
            else:
                idx = 0
            idx = max(0, min(idx, len(self.DAYS) - 1))
            return pd.Timestamp(self.DAYS[idx], tz="Asia/Shanghai").date()
    return Cal()


def test_etf_t0_equity_prev_day_sellable_after_unlock(build_db):
    """Day1 买入 equity：当日卖被拒；Day2 盘前解锁后卖出成交"""
    db = build_db(etf_minutes=_etf_bars(day=DAY) + _etf_bars(day=DAY2),
                  etf_daily=_etf_daily(day=DAY) + _etf_daily(day=DAY2),
                  etf_basic=[etf_basic_row("159870", "equity")])
    state = {'day1_status': None, 'sold_day2': False}

    def handle_data(context, data):
        from quantstudio.backtest.ptrade_api import order_target_value
        hhmm = context.current_dt.hour * 100 + context.current_dt.minute
        d = str(context.current_dt.date())
        if d == DAY and hhmm == 935:
            order_target_value("159870.SZ", 10000)
        elif d == DAY and hhmm == 1455:
            o = order_target_value("159870.SZ", 0)
            state['day1_status'] = o.status if o is not None else None
        elif d == DAY2 and hhmm == 1455:
            o = order_target_value("159870.SZ", 0)
            if o is not None and o.status == "filled":
                state['sold_day2'] = True

    strategy = {'initialize': lambda c: None, 'handle_data': handle_data}
    _run_engine(db, strategy, _cal_2days(), start=DAY, end=DAY2, etf_t0=True)
    assert state['day1_status'] in (None, "rejected")   # 当日卖被拒（T+1）
    assert state['sold_day2'] is True                   # 次日解锁后可卖
