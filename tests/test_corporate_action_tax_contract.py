"""
测试公司行为引擎——真实 BacktestEngine + Position 契约测试（W2-0.5）。

使用 BacktestEngine(...) 构造器（非 __new__()）。引擎 fixture：
1. 创建临时 DuckDB + stock_daily 表
2. 构造最小化 dummy strategy
3. 调用 BacktestEngine(db_path, strategy, start_date, end_date, ...) 正确构造
4. 测试中注入 fake reference provider
5. 调用 engine._apply_corporate_actions(date)
"""
import duckdb
import pandas as pd
import pytest
from pathlib import Path


# ---- Mock providers for DataProviderRegistry ----

class _Calendar:
    def get_trading_day(self, *a, **kw):
        return None


class _Market:
    pass


class _Fundamental:
    pass


class _Reference:
    """Default stub replaced per test."""

    def get_corporate_actions(self, day_str):
        return pd.DataFrame(columns=["code", "cash_div", "stk_div"])

    def get_exrights(self, code, date):
        return None


def _make_registry(ref=None):
    """Build a minimal DataProviderRegistry duck-type."""
    return type("_Registry", (), {
        "market": _Market(),
        "fundamental": _Fundamental(),
        "reference": ref if ref is not None else _Reference(),
        "calendar": _Calendar(),
    })()


@pytest.fixture
def engine(tmp_path):
    """真实 BacktestEngine(...) 构造器。

    1. 创建临时 DuckDB + stock_daily 表
    2. 最小化 dummy strategy
    3. 以真实构造器实例化 BacktestEngine（注入 mock providers）
    4. 返回 engine 供测试注入 fake reference provider
    """
    from quantstudio.backtest.backtest_engine import BacktestEngine

    # 1. 临时 DuckDB + stock_daily 表（满足契约：提供一个有 stock_daily 的 DB）
    db_path = tmp_path / "test_corp_actions.db"
    conn = duckdb.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_daily (
            code         VARCHAR,
            date_ms      BIGINT,
            open         DOUBLE,
            high         DOUBLE,
            low          DOUBLE,
            close        DOUBLE,
            volume       DOUBLE,
            amount       DOUBLE,
            preClose     DOUBLE,
            open_front   DOUBLE,
            high_front   DOUBLE,
            low_front    DOUBLE,
            close_front  DOUBLE
        )
    """)
    conn.close()

    # 2. 最小化 dummy strategy（仅需 initialize，引擎永远不跑 run()）
    strategy = {"initialize": lambda ctx: None}

    # 3. 真实构造器 —— 注入 mock provider registry，不触发 DuckDB provider 自动构建
    eng = BacktestEngine(
        str(db_path),
        strategy,
        "2024-01-01",
        "2024-12-31",
        providers=_make_registry(),
    )

    return eng


def _set_ref(eng, df):
    """替换 engine._providers.reference 为返回指定 DataFrame 的 fake provider。"""

    class FakeRef:
        def get_corporate_actions(self, day_str):
            return df

        def get_exrights(self, code, date):
            return None

    eng._providers.reference = FakeRef()


def _add_pos(eng, code, vol, cost):
    """使用真实 Position dataclass 添加持仓。"""
    from quantstudio.backtest.backtest_engine import Position

    pos = Position(code=code, volume=vol, avg_cost=cost, can_sell=vol)
    eng.account.positions[code] = pos


# ============================================================
# New schema tests
# ============================================================

def test_new_schema_cash_only(engine):
    """新 schema：cash_div_before_tax=1.0 -> 100股 x 1.0 x 0.80 = 80.0"""
    df = pd.DataFrame([{
        "code": "600000.SH", "cash_div_before_tax": 1.0,
        "cash_div_after_tax": 0.80, "cash_div": 1.0, "stk_div": 0.0,
    }])
    _set_ref(engine, df)
    _add_pos(engine, "600000.SH", 100, 10.0)
    engine._apply_corporate_actions("2024-06-20")

    assert engine.account.cash == pytest.approx(100080.0)
    pos = engine.account.positions["600000.SH"]
    assert pos.avg_cost == pytest.approx(9.0)
    assert pos.volume == 100
    evt = engine.result.corporate_actions[0]
    assert evt["cash_div_per_share"] == pytest.approx(1.0)
    assert evt["cash_div_before_tax"] == pytest.approx(1.0)
    assert evt["cash_div_after_tax"] == pytest.approx(0.80)
    assert evt["cash_credit_net"] == pytest.approx(80.0)
    assert evt["tax_policy"] == "pre_tax_x_0.80"


def test_new_schema_stock_and_cash(engine):
    """新 schema：分红+送股同时发生"""
    df = pd.DataFrame([{
        "code": "000001.SZ", "cash_div_before_tax": 0.5,
        "cash_div_after_tax": 0.40, "cash_div": 0.5, "stk_div": 0.3,
    }])
    _set_ref(engine, df)
    _add_pos(engine, "000001.SZ", 100, 15.0)
    engine._apply_corporate_actions("2024-07-15")

    pos = engine.account.positions["000001.SZ"]
    assert pos.volume == 130
    assert pos.can_sell == 130
    assert pos.avg_cost == pytest.approx((15.0 - 0.5) * 100 / 130)
    assert engine.account.cash == pytest.approx(100040.0)
    evt = engine.result.corporate_actions[0]
    assert evt["stock_div_ratio"] == pytest.approx(0.3)
    assert evt["added_shares"] == 30


def test_new_schema_pretax_vs_posttax(engine):
    """A/B: 税前=1.0, 税后=0.80 -> 入账 80.0（非 64.0）"""
    df = pd.DataFrame([{
        "code": "600000.SH", "cash_div_before_tax": 1.0,
        "cash_div_after_tax": 0.80, "cash_div": 1.0, "stk_div": 0.0,
    }])
    _set_ref(engine, df)
    _add_pos(engine, "600000.SH", 100, 10.0)
    engine._apply_corporate_actions("2024-06-20")

    evt = engine.result.corporate_actions[0]
    assert evt["cash_credit_net"] == pytest.approx(80.0)
    assert evt["cash_credit_net"] != pytest.approx(64.0)


# ============================================================
# Legacy schema tests
# ============================================================

def test_legacy_schema_only_old_keys(engine):
    """旧 schema：只有 cash_div 列（无 cash_div_before_tax/after_tax）"""
    df = pd.DataFrame([{"code": "600000.SH", "cash_div": 1.0, "stk_div": 0.0}])
    _set_ref(engine, df)
    _add_pos(engine, "600000.SH", 100, 10.0)
    engine._apply_corporate_actions("2024-08-01")

    # engine 通过 COALESCE 将 cash_div 作为 pre-tax 使用
    assert engine.account.cash == pytest.approx(100080.0)
    assert engine.account.positions["600000.SH"].avg_cost == pytest.approx(9.0)


def test_no_actions(engine):
    """空 DataFrame：不改变任何状态"""
    df = pd.DataFrame(columns=["code", "cash_div_before_tax", "stk_div"])
    _set_ref(engine, df)
    _add_pos(engine, "000001.SZ", 100, 10.0)
    engine._apply_corporate_actions("2024-09-01")
    assert engine.account.cash == 100000.0
    assert len(engine.result.corporate_actions) == 0


def test_zero_dividend(engine):
    """分红=0：不改变"""
    df = pd.DataFrame([{
        "code": "000001.SZ", "cash_div_before_tax": 0.0,
        "cash_div_after_tax": 0.0, "cash_div": 0.0, "stk_div": 0.0,
    }])
    _set_ref(engine, df)
    _add_pos(engine, "000001.SZ", 100, 10.0)
    engine._apply_corporate_actions("2024-10-01")
    assert engine.account.cash == 100000.0
    pos = engine.account.positions["000001.SZ"]
    assert pos.avg_cost == 10.0
    assert pos.volume == 100


# ============================================================
# Historical A/B test —— 调用真实 engine 方法
# ============================================================

def test_historical_ab_600000_2015_dividend(engine):
    """浦发银行 600000 2015年分红：税前 0.7191，税后 ~0.5753。

    构造 fake provider 返回该分红数据，持仓 1000 股，调用真实
    engine._apply_corporate_actions，然后断言 cash_credit_net、
    avg_cost 等输出字段 —— 这是真实引擎方法的端到端验证，非纯数学。
    """
    pre_tax = 0.7191
    post_tax = 0.5753  # 20% tax at source
    shares = 1000
    init_cost = 12.0
    init_cash = engine.account.cash

    # Fake provider returning the historical dividend for 600000
    df = pd.DataFrame([{
        "code": "600000.SH",
        "cash_div_before_tax": pre_tax,
        "cash_div_after_tax": post_tax,
        "cash_div": pre_tax,
        "stk_div": 0.0,
    }])
    _set_ref(engine, df)
    _add_pos(engine, "600000.SH", shares, init_cost)

    # --- 调用真实引擎方法 ---
    engine._apply_corporate_actions("2015-06-20")

    # Engine policy: pre_tax x 0.80
    expected_cash = shares * pre_tax * 0.80  # 575.28
    expected_avg = max(0.0, init_cost - pre_tax)  # 12.0 - 0.7191

    assert engine.account.cash == pytest.approx(init_cash + expected_cash)
    pos = engine.account.positions["600000.SH"]
    assert pos.avg_cost == pytest.approx(expected_avg)
    assert pos.volume == shares  # no stock dividend

    evt = engine.result.corporate_actions[0]
    assert evt["code"] == "600000.SH"
    assert evt["cash_div_before_tax"] == pytest.approx(pre_tax)
    assert evt["cash_div_after_tax"] == pytest.approx(post_tax)
    assert evt["cash_credit_net"] == pytest.approx(expected_cash)
    assert evt["tax_policy"] == "pre_tax_x_0.80"
    # Post-tax should NOT be used as base for taxation (no double-tax)
    assert evt["cash_credit_net"] != pytest.approx(shares * post_tax * 0.80)
    assert pre_tax != pytest.approx(post_tax)
