"""QS_FILL_AUDIT 拒单采集与日末审计行测试（设计文档 backtest-align-diagnosability-design §2.1/2.2/5）。

覆盖（方案 §5 验收标准 1）：
- 四种拒单场景采集：no_price（含 buy/sell 方向归类）、涨跌停阻断、halted（分钟 Profile）、
  insufficient_cash_or_rounding（资金不足 + 整手取整不足 100 股兜底）
- below_rebalance_threshold 不采集（集中采集排除规则）
- _emit_fill_audit 行内容（计数/positions_total/rejected_detail）与截断 ...(+N more)
- 跨日重置（端到端：第一天有拒单、第二天无拒单 → 第二天 rejected_detail=[]）
"""
import logging

import pandas as pd
import pytest

from tests.conftest import daily_row, etf_basic_row

from quantstudio.backtest.ptrade_import import g, order  # noqa: F401  (策略函数命名空间)


def _engine(cash=1_000_000, profile="daily-bar-v1"):
    """构造带资金和价格的引擎（不连真实库，用 mock 价格；同 test_order_rejection 风格）。"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={}, start="2026-01-01", end="2026-01-02",
        engine_profile=profile,
    )
    engine.account.cash = cash
    return engine


def _pos(engine, code, volume, avg_cost=10.0):
    from quantstudio.backtest.backtest_engine import Position
    engine.account.positions[code] = Position(
        code=code, volume=volume, avg_cost=avg_cost, can_sell=volume)


# ========== 拒单采集（_finalize_immediate / _day_rejections）==========

def test_no_price_rejected_collected_with_direction():
    """no_price 拒单按 target_value/shares 符号归类 buy/sell（修订①），并写入 _day_rejections。"""
    engine = _engine()
    order = engine._immediate_execute("600000.SH", target_value=10000,
                                      prices={}, date="2026-01-05", curr_data=None)
    assert order.status == "rejected"
    assert order.reason == "no_price"
    assert order.direction == "buy"  # 修订①：不再 unknown
    assert ("600000.SH", "buy", "no_price") in engine._day_rejections

    engine._day_rejections.clear()
    order2 = engine._immediate_execute("600000.SH", target_value=0,
                                       prices={}, date="2026-01-05", curr_data=None)
    assert order2.reason == "no_price"
    assert order2.direction == "sell"
    assert ("600000.SH", "sell", "no_price") in engine._day_rejections


def test_limit_blocks_collected():
    """涨停/跌停阻断均纳入采集。"""
    engine = _engine()
    curr = pd.DataFrame({'code': ['600000'], 'close': [11.0], 'preClose': [10.0]})
    order = engine._immediate_execute("600000.SH", target_value=10000,
                                      prices={"600000.SH": 11.0},
                                      date="2026-01-05", curr_data=curr)
    assert order.reason == "limit_up_blocked"
    assert ("600000.SH", "buy", "limit_up_blocked") in engine._day_rejections

    engine._day_rejections.clear()
    _pos(engine, "600000.SH", 1000)
    curr2 = pd.DataFrame({'code': ['600000'], 'close': [9.0], 'preClose': [10.0]})
    order2 = engine._immediate_execute("600000.SH", target_value=0,
                                       prices={"600000.SH": 9.0},
                                       date="2026-01-05", curr_data=curr2)
    assert order2.reason == "limit_down_blocked"
    assert ("600000.SH", "sell", "limit_down_blocked") in engine._day_rejections


def test_halted_collected_minute_profile():
    """分钟 Profile 停牌拒单（halted，原零日志）纳入采集。"""
    engine = _engine(profile="minute-bar-v1")
    curr = pd.DataFrame({'code': ['600000'], 'close': [10.0], 'volume': [0.0],
                         'suspendFlag': [1]})
    order = engine._immediate_execute("600000.SH", target_value=10000,
                                      prices={"600000.SH": 10.0},
                                      date="2026-01-05", curr_data=curr)
    assert order.status == "rejected"
    assert order.reason == "halted"
    assert ("600000.SH", "buy", "halted") in engine._day_rejections


def test_insufficient_cash_and_rounding_zero_collected():
    """资金不足与整手取整不足 100 股兜底拒单均纳入采集。"""
    engine = _engine(cash=100)
    curr = pd.DataFrame({'code': ['600000'], 'close': [10.0], 'preClose': [10.0]})
    order = engine._immediate_execute("600000.SH", target_value=100000,
                                      prices={"600000.SH": 10.0},
                                      date="2026-01-05", curr_data=curr)
    assert order.status == "rejected"
    assert order.reason == "insufficient_cash_or_rounding"
    assert ("600000.SH", "buy", "insufficient_cash_or_rounding") in engine._day_rejections

    # 整手取整：buy_value=50 不足一手（100 股）成本 → target_vol=0 → 兜底拒单
    engine2 = _engine(cash=1_000_000)
    curr2 = pd.DataFrame({'code': ['600000'], 'close': [10.0], 'preClose': [10.0]})
    order2 = engine2._immediate_execute("600000.SH", target_value=50,
                                        prices={"600000.SH": 10.0},
                                        date="2026-01-05", curr_data=curr2)
    assert order2.status == "rejected"
    assert order2.reason == "insufficient_cash_or_rounding"
    assert ("600000.SH", "buy", "insufficient_cash_or_rounding") in engine2._day_rejections


def test_below_rebalance_threshold_not_collected():
    """微调跳过（below_rebalance_threshold）属正常语义，不采集（集中采集排除规则）。"""
    engine = _engine()
    _pos(engine, "600000.SH", 1000, avg_cost=10.0)
    curr = pd.DataFrame({'code': ['600000'], 'close': [10.0], 'preClose': [10.0]})
    order = engine._immediate_execute("600000.SH", target_value=10000,
                                      prices={"600000.SH": 10.0},
                                      date="2026-01-05", curr_data=curr)
    assert order.status == "rejected"
    assert order.reason == "below_rebalance_threshold"
    assert engine._day_rejections == []


# ========== 日末审计行（_emit_fill_audit）==========

def test_emit_fill_audit_content(caplog):
    """QS_FILL_AUDIT 行内容：计数、positions_total、rejected_detail。"""
    engine = _engine()
    engine.result.trade_records.append({'date': '2026-01-05', 'action': 'buy'})
    engine.result.trade_records.append({'date': '2026-01-05', 'action': 'buy'})
    engine.result.trade_records.append({'date': '2026-01-05', 'action': 'sell'})
    engine.result.trade_records.append({'date': '2026-01-06', 'action': 'buy'})  # 非当日不计
    _pos(engine, "600000.SH", 1000)
    _pos(engine, "000001.SZ", 2000)
    engine._day_rejections = [("600000.SH", "buy", "no_price"),
                              ("000001.SZ", "sell", "limit_down_blocked")]

    with caplog.at_level(logging.WARNING, logger="quantstudio.backtest.backtest_engine"):
        engine._emit_fill_audit("2026-01-05")
    assert "QS_FILL_AUDIT date=2026-01-05 sell_filled=1 buy_filled=2 " \
           "sell_rejected=1 buy_rejected=1 positions_total=2 " \
           "rejected_detail=[600000.SH:no_price,000001.SZ:limit_down_blocked]" in caplog.text


def test_emit_fill_audit_info_level_when_no_rejection(caplog):
    """无拒单 → INFO 级输出（正常日对照行）。"""
    engine = _engine()
    engine.result.trade_records.append({'date': '2026-01-05', 'action': 'buy'})
    _pos(engine, "600000.SH", 1000)
    with caplog.at_level(logging.INFO, logger="quantstudio.backtest.backtest_engine"):
        engine._emit_fill_audit("2026-01-05")
    assert "QS_FILL_AUDIT date=2026-01-05 sell_filled=0 buy_filled=1 " \
           "sell_rejected=0 buy_rejected=0 positions_total=1 " \
           "rejected_detail=[]" in caplog.text


def test_emit_fill_audit_detail_truncated(caplog):
    """rejected_detail 超过 10 条时截断并输出省略计数 ...(+N more)。"""
    engine = _engine()
    engine._day_rejections = [(f"60000{i}.SH", "buy", "no_price") for i in range(12)]
    with caplog.at_level(logging.WARNING, logger="quantstudio.backtest.backtest_engine"):
        engine._emit_fill_audit("2026-01-05")
    line = next(l for l in caplog.messages if l.startswith("QS_FILL_AUDIT"))
    assert "buy_rejected=12" in line
    assert line.count(":no_price") == 10
    assert line.endswith("...(+2 more)]")


# ========== 跨日重置（端到端）==========

def _fixed_cal(days):
    class Cal:
        def __init__(self, d):
            self._days = list(d)

        def get_trade_days(self, start, end):
            return [pd.Timestamp(d, tz="Asia/Shanghai").to_pydatetime() for d in self._days]

        def get_trading_day(self, date, offset=0):
            s = str(date)[:10]
            if s not in self._days:
                return None
            idx = max(0, min(len(self._days) - 1, self._days.index(s) + offset))
            return pd.Timestamp(self._days[idx], tz="Asia/Shanghai").date()
    return Cal(days)


def test_cross_day_reset_end_to_end(build_db, caplog):
    """跨日重置：第一天有拒单、第二天无拒单 → 第二天审计 rejected_detail=[] 且计数归零。"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    from tests.conftest import make_providers

    days = ("2026-01-05", "2026-01-06")
    db = build_db(
        etf_daily=[
            daily_row("600000", "2026-01-05", 10.0),
            daily_row("600000", "2026-01-06", 10.0),
            daily_row("159999", "2026-01-06", 5.0),  # 第一天无 bar → no_price 拒单
        ],
        etf_basic=[etf_basic_row("600000", "equity"), etf_basic_row("159999", "equity")],
    )
    providers = make_providers(db, _fixed_cal(days))

    def initialize(context):
        pass

    def handle_data(context, data):
        order("600000.SH", 100)   # 两天都有 bar → 两天都成交
        order("159999.SZ", 100)   # day1 无 bar → no_price 拒单；day2 成交

    engine = BacktestEngine(
        db_path=str(db), strategy={"initialize": initialize, "handle_data": handle_data},
        start=days[0], end=days[-1], match_price_mode="close", providers=providers,
        capital=100_000,
    )
    with caplog.at_level(logging.INFO, logger="quantstudio.backtest.backtest_engine"):
        engine.run()

    audits = [l for l in caplog.messages if l.startswith("QS_FILL_AUDIT")]
    assert len(audits) == 2
    # 第一天：159999 无 bar → no_price 拒单（buy_rejected=1，600000 成交 buy_filled=1）
    assert "date=2026-01-05" in audits[0]
    assert "buy_filled=1" in audits[0]
    assert "buy_rejected=1" in audits[0]
    assert "rejected_detail=[159999.SZ:no_price]" in audits[0]
    # 第二天：无拒单 → 计数归零、明细为空（跨日重置断言）
    assert "date=2026-01-06" in audits[1]
    assert "sell_rejected=0 buy_rejected=0" in audits[1]
    assert "rejected_detail=[]" in audits[1]
