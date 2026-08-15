"""ETF 现金分红入账（引擎方案 v2-final 阶段 2，§3.6）测试。

覆盖：
  1. **0.8 入账口径（2026-08-16 PTrade 实测修正）**：div_cash × volume × 0.80——
     PTrade 平台对 ETF 现金分红与股票统一扣 20%（600000 11500×0.42×0.8=3864.00、
     510500 10800×0.149×0.8=1287.36 与平台现金增量逐分吻合）；公募免税全额假设被证伪。
  2. reference 无 get_etf_dividends 方法 → no-op
  3. 空记录 → no-op
  4. 510500 除息日端到端：现金入账 = volume × div_cash × 0.8，净值缺口 = 0.2×div×vol
  5. 同日分红+送股不互斥（already_handled 不阻止两路径并行）
  6. etf_dividend 表缺失 → no-op（query 返回空）
"""
import logging

import duckdb
import pandas as pd
import pytest

from tests.conftest import daily_row, make_providers

from quantstudio.backtest.backtest_engine import BacktestEngine, Position
from quantstudio.backtest.ptrade_import import g, order  # noqa: F401


# =====================================================================
# 单元级 fixture（仿 test_corporate_action_tax_contract 模式）
# =====================================================================

class _Calendar:
    def get_trading_day(self, *a, **kw):
        return None


class _Market:
    pass


class _Fundamental:
    pass


class _Reference:
    """Default stub；各用例替换 get_etf_dividends。"""

    def get_corporate_actions(self, day_str):
        return pd.DataFrame(columns=["code", "cash_div", "stk_div"])

    def get_etf_dividends(self, day_str):
        return pd.DataFrame(columns=["code", "div_cash"])

    def get_exrights(self, code, date):
        return None


@pytest.fixture
def engine(tmp_path):
    from quantstudio.backtest.backtest_engine import BacktestEngine

    db_path = tmp_path / "test_etf_div.db"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS stock_daily (code VARCHAR)")
    conn.close()
    registry = type("_Registry", (), {
        "market": _Market(), "fundamental": _Fundamental(),
        "reference": _Reference(), "calendar": _Calendar(),
    })()
    return BacktestEngine(
        str(db_path), {"initialize": lambda ctx: None},
        "2026-07-01", "2026-07-31",
        providers=registry,
    )


def _set_div(eng, rows):
    """替换 reference.get_etf_dividends 返回指定 DataFrame。"""
    class FakeRef(_Reference):
        def get_etf_dividends(self, day_str):
            return pd.DataFrame(rows)
    eng._providers.reference = FakeRef()


def _add_pos(eng, code, vol, cost):
    pos = Position(code=code, volume=vol, avg_cost=cost, can_sell=vol)
    eng.account.positions[code] = pos


def _div_evts(eng, day_str="2026-07-15"):
    return [a for a in eng.result.corporate_actions
            if a.get("date") == day_str and a.get("type") == "etf_cash_dividend"]


# =====================================================================
# 1. ETF 现金分红 0.8 入账（PTrade 实测口径，2026-08-16）
# =====================================================================

def test_etf_cash_dividend_credit_x080(engine):
    """510500 div_cash=0.149、持仓 10000 份 → cash += 1192.0（=1490×0.80）。

    PTrade 实测：510500 10800×0.149×0.8=1287.36 与平台现金增量逐分吻合——
    平台对 ETF 分红同样扣 20%（与股票 pre_tax×0.80 同口径），非公募免税全额。
    """
    _add_pos(engine, "510500.SH", 10000, 5.9)
    init_cash = engine.account.cash
    _set_div(engine, [{"code": "510500", "div_cash": 0.149}])
    engine._apply_etf_cash_dividends("2026-07-15")

    assert engine.account.cash == pytest.approx(init_cash + 1192.0)
    evts = _div_evts(engine)
    assert len(evts) == 1
    evt = evts[0]
    assert evt["code"] == "510500.SH"
    assert evt["div_cash"] == pytest.approx(0.149)
    assert evt["cash_credit_net"] == pytest.approx(1192.0)
    assert evt["tax_policy"] == "etf_pre_tax_x_0.80"   # 与股票 pre_tax_x_0.80 命名一致
    # 反证：不得按 1.0 全额（公募免税假设已被 PTrade 实测证伪）
    assert evt["cash_credit_net"] != pytest.approx(1490.0)


def test_ptrade_measured_stock_600000(engine):
    """PTrade 实测对照（股票路径，验证证据链基准）：600000 11500×0.42×0.8=3864.00。"""
    _add_pos(engine, "600000.SH", 11500, 8.8)
    init_cash = engine.account.cash
    engine._providers.reference.get_corporate_actions = lambda day: _pd([
        {"code": "600000.SH", "cash_div_before_tax": 0.42, "cash_div_after_tax": 0.42,
         "cash_div": 0.42, "stk_div": 0.0}])
    engine._apply_corporate_actions("2026-07-16")
    assert engine.account.cash == pytest.approx(init_cash + 11500 * 0.42 * 0.80)


def test_ptrade_measured_etf_510500(engine):
    """PTrade 实测对照（ETF 路径）：510500 10800×0.149×0.8=1287.36（平台现金增量精确值）。"""
    _add_pos(engine, "510500.SH", 10800, 8.4)
    init_cash = engine.account.cash
    _set_div(engine, [{"code": "510500", "div_cash": 0.149}])
    engine._apply_etf_cash_dividends("2026-07-15")
    assert engine.account.cash == pytest.approx(init_cash + 10800 * 0.149 * 0.80)


def _pd(rows):
    import pandas as _pd
    return _pd.DataFrame(rows)


def test_etf_cash_dividend_does_not_touch_stock_positions(engine):
    """股票持仓不因 etf_dividend 入账（表为 ETF 专属；股票代码不在表中）。"""
    _add_pos(engine, "600000.SH", 1000, 10.0)
    init_cash = engine.account.cash
    _set_div(engine, [{"code": "159915", "div_cash": 1.0}])  # ETF 记录，非持仓 → 不匹配
    engine._apply_etf_cash_dividends("2026-07-15")
    assert engine.account.cash == init_cash
    assert _div_evts(engine) == []


# =====================================================================
# 2/3/6. no-op 路径
# =====================================================================

def test_no_provider_method_noop(engine):
    """reference 无 get_etf_dividends（旧 provider）→ no-op。"""
    _add_pos(engine, "510500.SH", 10000, 5.9)
    init_cash = engine.account.cash
    engine._providers.reference = _Reference()  # 默认实现仍返回空
    engine._providers.reference.get_etf_dividends = None  # 模拟方法缺失
    # 防御：hasattr 检查 → 直接返回
    engine._apply_etf_cash_dividends("2026-07-15")
    assert engine.account.cash == init_cash


def test_empty_records_noop(engine):
    """空 DataFrame → no-op。"""
    _add_pos(engine, "510500.SH", 10000, 5.9)
    init_cash = engine.account.cash
    _set_div(engine, [])
    engine._apply_etf_cash_dividends("2026-07-15")
    assert engine.account.cash == init_cash
    assert _div_evts(engine) == []


def test_missing_table_noop(build_db):
    """etf_dividend 表缺失 → query 返回空 → no-op（阶段 2 前回测不受影响）。"""
    from quantstudio.backtest.providers.duckdb_provider import DuckDBReferenceDataProvider
    rows = [daily_row("510500", "2026-07-14", 6.0, preclose=6.0, pctchg=0.0),
            daily_row("510500", "2026-07-15", 5.851, preclose=5.851, pctchg=0.0)]
    db = build_db(etf_daily=rows)  # 不建 etf_dividend 表
    ref = DuckDBReferenceDataProvider(db)
    df = ref.get_etf_dividends("2026-07-15")
    assert len(df) == 0   # 表缺失 → 空（no-op 设计）
    assert list(df.columns) == ["code", "div_cash"]


# =====================================================================
# 4. 510500 除息日端到端：净值连续（现金入账补齐价格缺口）
# =====================================================================

def _make_cal(days):
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


def test_510500_ex_date_nav_continuity(build_db):
    """510500 除息日（07-15，div_cash=0.149）：现金入账 = volume×0.149×0.80，
    净值缺口 ≈ 0.2×div×vol（0.8 口径，PTrade 实测），非 0%（1.0 免税）也非 -1.49%（不入账）。"""
    from quantstudio.pipeline.writers import DDL_DUCKDB
    import duckdb as _dd

    rows = [daily_row("510500", "2026-07-14", 6.000, preclose=6.000, pctchg=0.0),
            daily_row("510500", "2026-07-15", 5.851, preclose=5.851, pctchg=0.0)]
    db = build_db(etf_daily=rows)
    # 建 etf_dividend 表 + 插入 07-15 分红行
    con = _dd.connect(str(db))
    con.execute(DDL_DUCKDB["etf_dividend"])
    con.execute("INSERT INTO etf_dividend (code, ex_date, div_cash, div_proc) VALUES (?, ?, ?, ?)",
                ["510500", pd.Timestamp("2026-07-15", tz="Asia/Shanghai").value // 10**6, 0.149, "实施"])
    con.close()

    cal = _make_cal(["2026-07-14", "2026-07-15"])
    # make_providers 的 reference=None；阶段 2 需真实 reference provider（读 etf_dividend 表）
    from quantstudio.backtest.providers.duckdb_provider import (
        DuckDBMarketDataProvider, DuckDBReferenceDataProvider)
    market = DuckDBMarketDataProvider(db, calendar_provider=cal)
    reference = DuckDBReferenceDataProvider(db)
    providers = type("P", (), {
        "market": market, "fundamental": None,
        "reference": reference, "calendar": cal,
    })()

    def initialize(context):
        pass
    def handle_data(context, data):
        if not getattr(g, "bought", False):
            order("510500.SH", 10000)
            g.bought = True
    eng = BacktestEngine(str(db), {"initialize": initialize, "handle_data": handle_data},
                         start="2026-07-14", end="2026-07-15",
                         match_price_mode="close", providers=providers)
    eng.run()

    nav = {d["date"]: d["nav"] for d in eng.result.nav_history}
    assert "2026-07-14" in nav and "2026-07-15" in nav
    # 入账证据（0.8 口径：10000×0.149×0.8 = 1192.0）
    evts = _div_evts(eng, "2026-07-15")
    assert len(evts) == 1
    assert evts[0]["cash_credit_net"] == pytest.approx(10000 * 0.149 * 0.80)
    assert evts[0]["tax_policy"] == "etf_pre_tax_x_0.80"
    # 净值缺口 = 0.2×div×vol/总资产 ≈ 0.298%（PTrade 实测口径）
    chg = nav["2026-07-15"] / nav["2026-07-14"] - 1.0
    assert chg == pytest.approx(-0.00298, abs=0.0003), f"除息日净值缺口: {chg:.4%}"
    # 反证：1.0 全额口径缺口≈0%；不入账口径缺口 ≈ -1.49%
    assert chg < -0.001 and chg > -0.01


# =====================================================================
# 5. 同日分红+送股不互斥（阶段 1 already_handled 协调）
# =====================================================================

def test_same_day_dividend_and_split_not_mutually_exclusive(engine):
    """同日 ETF 现金分红 + 送股折算：两路径都执行（already_handled 排除 etf_cash_dividend）。"""
    _add_pos(engine, "159995.SZ", 30100, 3.321)
    # 阶段 2 先入账（现金，0.8 口径）
    _set_div(engine, [{"code": "159995", "div_cash": 0.05}])
    init_cash = engine.account.cash
    engine._apply_etf_cash_dividends("2026-07-07")
    assert engine.account.cash == pytest.approx(init_cash + 30100 * 0.05 * 0.80)

    # 阶段 1 送股反推（同日，prev 3.009 / preClose 1.505 → ratio 2.0）
    prev_df = pd.DataFrame([{"code": "159995", "close": 3.009}])
    curr_df = pd.DataFrame([{"code": "159995", "preClose": 1.505}])
    engine._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")

    pos = engine.account.positions["159995.SZ"]
    assert pos.volume == 60200          # 送股仍执行（不被现金记录阻止）
    assert pos.avg_cost == pytest.approx(3.321 / 2.0)
    types = {a.get("type") for a in engine.result.corporate_actions
             if a.get("date") == "2026-07-07"}
    assert types == {"etf_cash_dividend", "factor_derived_split"}  # 两路径并行


def test_stock_dividend_record_still_blocks_split(engine):
    """已有精确送股记录（非现金类型）仍阻止反推（精确路径优先语义不变）。"""
    _add_pos(engine, "159995.SZ", 30100, 3.321)
    engine.result.corporate_actions.append({
        "date": "2026-07-07", "code": "159995.SZ",
        "stock_div_ratio": 1.0, "added_shares": 30100,
    })
    prev_df = pd.DataFrame([{"code": "159995", "close": 3.009}])
    curr_df = pd.DataFrame([{"code": "159995", "preClose": 1.505}])
    engine._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")
    assert engine.account.positions["159995.SZ"].volume == 30100  # 不重复送股
