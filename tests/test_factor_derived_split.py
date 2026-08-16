"""ETF/股票回测除权补正（引擎方案 v2-final）测试：`_apply_factor_derived_split`。

依据：docs/mcp_migration/etf-split-factor-derived-fix-plan.v2-final.md §六（10 例）
修订索引（§十二）：P0-1（already_handled 裸码统一）、P0-2（现金分红带 1.01~1.10 跳过+WARN）、
P1-3（≥1.10 未吸附按原值送股+WARN）、P1-4（ratio<0.99 份额合并对称处理）、
P1-5（ETF-only 门控，股票零回归）。

覆盖：
  1. ETF 除权送股（159995 场景：30100→60200、avg_cost 3.321→1.6605）
  2. 非除权日不触发（ratio=1.0）
  3. 吸附逻辑（1.9993→2.0；1.5027→1.5）
  4. 【P0-1】already_handled QMT 格式（'159995.SZ'）不重复送股
  5. 【P0-2】现金分红带（ratio=1.0347）跳过 + WARN，无幽灵送股
  6. 【P1-3】非 0.5 倍数折算（ratio=2.0462）按原值送股
  7. 【P1-4】份额合并对称处理（ratio=0.5）；未吸附（ratio=0.9718）WARN+跳过
  8. 净值连续性：除权日 |净值变化率−pctChg| ≤ 0.1pp，非 -50%（真实 run()）
  9. avg_cost 调整：送股后 avg_cost×old/new
  10. 回归：ETF 动量式策略除权日净值不跳水（真实 run()）；股票零回归（ETF-only 门控）
"""
import logging
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from tests.conftest import daily_row, make_providers

from quantstudio.backtest.backtest_engine import BacktestEngine, Position
from quantstudio.backtest.ptrade_import import g, order, log  # noqa: F401


# =====================================================================
# 单元级 fixture：mock providers registry + 直接调用 _apply_factor_derived_split
# （仿 test_corporate_action_tax_contract.py 的引擎 fixture 模式）
# =====================================================================

class _Calendar:
    def get_trading_day(self, *a, **kw):
        return None


class _Market:
    pass


class _Fundamental:
    pass


class _Reference:
    def get_corporate_actions(self, day_str):
        return pd.DataFrame(columns=["code", "cash_div", "stk_div"])

    def get_exrights(self, code, date):
        return None


def _make_registry(ref=None):
    return type("_Registry", (), {
        "market": _Market(),
        "fundamental": _Fundamental(),
        "reference": ref if ref is not None else _Reference(),
        "calendar": _Calendar(),
    })()


@pytest.fixture
def engine(tmp_path):
    """真实 BacktestEngine(...) 构造器 + mock providers（不跑 run()）。"""
    db_path = tmp_path / "test_factor_derived.db"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS stock_daily (code VARCHAR)")
    conn.close()
    eng = BacktestEngine(
        str(db_path), {"initialize": lambda ctx: None},
        "2026-07-01", "2026-07-08",
        providers=_make_registry(),
    )
    return eng


def _add_pos(eng, code, vol, cost):
    pos = Position(code=code, volume=vol, avg_cost=cost, can_sell=vol)
    eng.account.positions[code] = pos


def _dfs(prev_close, curr_preclose, code="159995"):
    """构造 prev_data（列 code/close）与 curr_data（列 code/preClose）。"""
    prev_df = pd.DataFrame([{"code": code, "close": prev_close}])
    curr_df = pd.DataFrame([{"code": code, "preClose": curr_preclose}])
    return curr_df, prev_df


def _evt(eng, day_str="2026-07-07"):
    """当日 corporate_actions 记录列表。"""
    return [a for a in eng.result.corporate_actions if a.get("date") == day_str]


# =====================================================================
# 1. ETF 除权送股（159995 场景）
# =====================================================================

def test_etf_split_159995_scenario(engine):
    """07-07 除权：prev_close=3.009 / preClose=1.505 → ratio 1.9993 吸附 2.0 → 30100→60200。"""
    _add_pos(engine, "159995.SZ", 30100, 3.321)
    curr_df, prev_df = _dfs(3.009, 1.505)
    engine._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")

    pos = engine.account.positions["159995.SZ"]
    assert pos.volume == 60200
    assert pos.can_sell == 60200
    # avg_cost 3.321 → 3.321×30100/60200 = 1.6605
    assert pos.avg_cost == pytest.approx(1.6605, abs=1e-9)

    evts = _evt(engine)
    assert len(evts) == 1
    evt = evts[0]
    assert evt["type"] == "factor_derived_split"
    assert evt["code"] == "159995"          # 新记录 code 统一裸码（与 curr_data 比对口径一致）
    assert evt["ratio"] == pytest.approx(2.0)
    assert evt["old_volume"] == 30100
    assert evt["new_volume"] == 60200
    assert evt["added"] == 30100


# =====================================================================
# 2. 非除权日不触发
# =====================================================================

def test_no_action_on_normal_day(engine):
    """ratio=1.0（preClose=prev close）→ 无操作。"""
    _add_pos(engine, "159995.SZ", 30100, 3.321)
    curr_df, prev_df = _dfs(3.009, 3.009)
    engine._apply_factor_derived_split(curr_df, prev_df, "2026-07-06")

    pos = engine.account.positions["159995.SZ"]
    assert pos.volume == 30100
    assert pos.avg_cost == pytest.approx(3.321)
    assert _evt(engine, "2026-07-06") == []


# =====================================================================
# 3. 吸附逻辑（0.5 倍数，容差 0.5%）
# =====================================================================

def test_snap_to_half_multiple(engine):
    """1.9993→2.0（159995 型）；1.5027→1.5（510500 型）。"""
    _add_pos(engine, "159995.SZ", 10000, 10.0)
    curr_df, prev_df = _dfs(3.009, 1.505)  # ratio = 1.999335...
    engine._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")
    assert engine.account.positions["159995.SZ"].volume == 20000
    evt = _evt(engine)[0]
    assert evt["ratio"] == pytest.approx(2.0)

    engine.account.positions.clear()
    engine.result.corporate_actions.clear()
    _add_pos(engine, "510500.SS", 10000, 10.0)
    curr_df, prev_df = _dfs(3.0054, 2.0, code="510500")  # ratio = 1.5027 → snapped 1.5
    engine._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")
    assert engine.account.positions["510500.SS"].volume == 15000
    evt = _evt(engine)[0]
    assert evt["ratio"] == pytest.approx(1.5)


# =====================================================================
# 4. 【P0-1】already_handled 裸码统一（QMT 格式不重复送股）
# =====================================================================

def test_already_handled_qmt_code_skips(engine):
    """corporate_actions 已有 QMT 格式记录（'159995.SZ'，_apply_corporate_actions 落盘格式）
    + 当日 preClose 异常 → 反推路径必须跳过，不重复送股（v1 缺陷：裸码比对永远 False → 翻倍）。"""
    _add_pos(engine, "159995.SZ", 30100, 3.321)
    engine.result.corporate_actions.append({
        "date": "2026-07-07", "code": "159995.SZ",  # QMT 格式（真实落盘格式）
        "cash_div_per_share": 0.0, "stock_div_ratio": 1.0, "added_shares": 30100,
    })
    curr_df, prev_df = _dfs(3.009, 1.505)  # 即使 preClose 异常也应跳过
    engine._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")

    pos = engine.account.positions["159995.SZ"]
    assert pos.volume == 30100          # 不重复送股
    assert pos.avg_cost == pytest.approx(3.321)
    assert len(_evt(engine)) == 1       # 仅已有记录，无新增 factor_derived 记录


def test_already_handled_bare_code_skips(engine):
    """corporate_actions 已有裸码记录（本方法自身落盘格式）→ 同样跳过。"""
    _add_pos(engine, "159995.SZ", 30100, 3.321)
    engine.result.corporate_actions.append({
        "date": "2026-07-07", "code": "159995",
        "type": "factor_derived_split", "ratio": 2.0,
    })
    curr_df, prev_df = _dfs(3.009, 1.505)
    engine._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")
    assert engine.account.positions["159995.SZ"].volume == 30100
    assert len(_evt(engine)) == 1


# =====================================================================
# 5. 【P0-2】现金分红带（1.01 < ratio < 1.10）跳过 + WARN，无幽灵送股
# =====================================================================

def test_cash_dividend_band_skips(engine, caplog):
    """510880 型 ratio=1.0347 → 不送股、不改 avg_cost、不入账，仅 WARN（v1 缺陷：+3.47% 假股）。"""
    _add_pos(engine, "510880.SS", 10000, 1.0)
    with caplog.at_level(logging.WARNING, logger="quantstudio.backtest.backtest_engine"):
        curr_df, prev_df = _dfs(1.0347, 1.0, code="510880")
        engine._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")

    pos = engine.account.positions["510880.SS"]
    assert pos.volume == 10000          # 无幽灵送股
    assert pos.avg_cost == pytest.approx(1.0)   # 成本不摊薄
    assert _evt(engine) == []
    assert "现金分红带" in caplog.text


# =====================================================================
# 6. 【P1-3】非 0.5 倍数真实折算按原值送股
# =====================================================================

def test_non_half_multiple_ratio_uses_original(engine, caplog):
    """512890 型 ratio=2.0462 → snapped=2.0 未命中（偏差 2.3%>0.5%）→ 按原值送股 + WARN。"""
    _add_pos(engine, "512890.SS", 10000, 10.0)
    with caplog.at_level(logging.WARNING, logger="quantstudio.backtest.backtest_engine"):
        curr_df, prev_df = _dfs(2.0462, 1.0, code="512890")
        engine._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")

    pos = engine.account.positions["512890.SS"]
    # new_total = round(10000×2.0462) = 20462；added = round_to_lot(10462, 100) = 10400
    assert pos.volume == 20400
    assert pos.avg_cost == pytest.approx(10.0 * 10000 / 20400)
    evt = _evt(engine)[0]
    assert evt["type"] == "factor_derived_split"
    assert evt["ratio"] == pytest.approx(2.0462)   # 原值（非吸附值 2.0）
    assert "非 0.5 倍数折算" in caplog.text


# =====================================================================
# 7. 【P1-4】份额合并（ratio < 0.99）对称处理
# =====================================================================

def test_share_merge_symmetric(engine):
    """ratio=0.5（吸附命中）→ volume×0.5、avg_cost÷0.5、can_sell 同步、记录 factor_derived_merge。"""
    _add_pos(engine, "511030.SS", 10000, 10.0)
    curr_df, prev_df = _dfs(1.0, 2.0, code="511030")   # ratio = 0.5
    engine._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")

    pos = engine.account.positions["511030.SS"]
    assert pos.volume == 5000
    assert pos.can_sell == 5000
    assert pos.avg_cost == pytest.approx(20.0)  # 10.0×10000/5000
    evt = _evt(engine)[0]
    assert evt["type"] == "factor_derived_merge"
    assert evt["ratio"] == pytest.approx(0.5)
    assert evt["added"] == -5000


def test_share_merge_rounds_down_to_lot(engine):
    """非整手合并：10100×0.5=5050 → 整手向下取整 5000（与送股整手取整语义对称）。"""
    _add_pos(engine, "511030.SS", 10100, 10.0)
    curr_df, prev_df = _dfs(1.0, 2.0, code="511030")   # ratio = 0.5
    engine._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")

    pos = engine.account.positions["511030.SS"]
    assert pos.volume == 5000
    assert pos.avg_cost == pytest.approx(10.0 * 10100 / 5000)


def test_share_merge_to_zero_skips(engine):
    """合并到 0 股（100×0.5=50 → 整手 0）为数值异常 → 跳过，不除零。"""
    _add_pos(engine, "511030.SS", 100, 10.0)
    curr_df, prev_df = _dfs(1.0, 2.0, code="511030")   # ratio = 0.5
    engine._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")

    pos = engine.account.positions["511030.SS"]
    assert pos.volume == 100
    assert pos.avg_cost == pytest.approx(10.0)
    assert _evt(engine) == []


def test_share_merge_unmatched_ratio_skips(engine, caplog):
    """ratio=0.9718（未吸附，偏差 2.8%>0.5%）→ WARN + 跳过（保守，不误合并）。"""
    _add_pos(engine, "159870.SZ", 10000, 10.0)
    with caplog.at_level(logging.WARNING, logger="quantstudio.backtest.backtest_engine"):
        curr_df, prev_df = _dfs(0.9718, 1.0, code="159870")
        engine._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")

    pos = engine.account.positions["159870.SZ"]
    assert pos.volume == 10000
    assert pos.avg_cost == pytest.approx(10.0)
    assert _evt(engine) == []
    assert "疑似份额合并" in caplog.text


# =====================================================================
# 9. avg_cost 调整（送股后 avg_cost×old/new）
# =====================================================================

def test_avg_cost_adjustment(engine):
    """送股不创造 PnL：avg_cost = avg_cost × old_volume / new_volume。"""
    _add_pos(engine, "159995.SZ", 50000, 2.0)
    curr_df, prev_df = _dfs(3.009, 1.505)  # ratio 1.9993 → 2.0
    engine._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")

    pos = engine.account.positions["159995.SZ"]
    assert pos.volume == 100000
    assert pos.avg_cost == pytest.approx(2.0 * 50000 / 100000)  # 1.0
    # 市值守恒：送股前后市值不变（用同一 raw close 估值）
    assert pos.volume * pos.avg_cost == pytest.approx(50000 * 2.0)


# =====================================================================
# 8. 净值连续性：除权日 |净值变化率−pctChg| ≤ 0.1pp，非 -50%（真实 run()）
# =====================================================================

_ETF_DAYS = ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06", "2026-07-07", "2026-07-08"]

# 159995 真实行情（2026-07，data/quantstudio.db 实测）
_159995_BARS = [
    ("2026-07-01", 3.321, 3.391, -2.04),
    ("2026-07-02", 3.019, 3.321, -9.09),
    ("2026-07-03", 3.001, 3.019, -0.63),
    ("2026-07-06", 3.009, 3.001, 0.30),
    ("2026-07-07", 1.501, 1.505, -0.27),
    ("2026-07-08", 1.493, 1.501, -0.53),
]


def _make_fixed_cal(days):
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


def _etf_engine(build_db, days=_ETF_DAYS, bars=_159995_BARS, extra_bars=None):
    rows = [daily_row("159995", day, close, preclose=preclose, pctchg=pctchg)
            for day, close, preclose, pctchg in bars]
    if extra_bars:
        rows.extend(extra_bars)
    db = build_db(etf_daily=rows)
    cal = _make_fixed_cal(days)
    providers = make_providers(db, cal)
    return BacktestEngine(
        db_path=str(db), strategy={}, start=days[0], end=days[-1],
        match_price_mode="close", providers=providers,
    )


def _buy_and_hold_strategy(buy_code, buy_shares):
    def initialize(context):
        pass
    def handle_data(context, data):
        if not getattr(g, "bought", False):
            order(buy_code, buy_shares)
            g.bought = True
    return {"initialize": initialize, "handle_data": handle_data}


def test_nav_continuity_on_ex_date(build_db):
    """159995 场景端到端：07-01 买入 30100（close 3.321）→ 07-07 除权送股 60200 →
    除权日净值变化 ≈ -0.23%（非 -50%），|净值变化率−pctChg| ≤ 0.1pp。"""
    eng = _etf_engine(build_db)
    eng.strategy = _buy_and_hold_strategy("159995.SZ", 30100)
    eng.run()

    nav = {d["date"]: d["nav"] for d in eng.result.nav_history}
    assert "2026-07-06" in nav and "2026-07-07" in nav

    # 除权日已送股（引擎级证据）
    evts = [a for a in eng.result.corporate_actions
            if a.get("date") == "2026-07-07" and a.get("type") == "factor_derived_split"]
    assert len(evts) == 1
    assert evts[0]["code"] == "159995"
    assert evts[0]["added"] == 30100
    assert eng.account.positions["159995.SZ"].volume == 60200
    assert eng.account.positions["159995.SZ"].avg_cost == pytest.approx(3.321 / 2.0, abs=1e-9)

    nav_chg = nav["2026-07-07"] / nav["2026-07-06"] - 1.0
    pct_chg = -0.27 / 100.0  # 07-07 pctChg（close 1.501 vs preClose 1.505）
    # 验收口径（v2-final §四）：|净值变化率 − pctChg| ≤ 0.1pp
    assert abs(nav_chg - pct_chg) <= 0.001
    # 非 -50%（原始缺陷：未送股 → -50.1% 虚假腰斩）
    assert nav_chg > -0.30
    # 精确值 ≈ -0.23%
    assert nav_chg == pytest.approx(-0.00233, abs=0.001)


def _run_profile_nav_test(build_db, engine_profile):
    """159995 折算日（07-07）在指定 profile 下送股生效、净值不跳水（v2-final 分钟挂钩）。"""
    from tests.conftest import minute_row

    rows = [daily_row("159995", day, close, preclose=preclose, pctchg=pctchg)
            for day, close, preclose, pctchg in _159995_BARS]
    # 分钟数据仅 07-01（买入日）两根 bar；中间日/除息日无分钟 bar → 策略无操作但挂钩仍执行
    mrows = [minute_row("159995", "2026-07-01", 9, 31, 3.321, preclose=3.391),
             minute_row("159995", "2026-07-01", 15, 0, 3.321, preclose=3.391)]
    db = build_db(etf_daily=rows, etf_minutes=mrows)
    cal = _make_fixed_cal(_ETF_DAYS)
    providers = make_providers(db, cal)

    def initialize(context):
        pass
    def handle_data(context, data):
        if not getattr(g, "bought", False):
            order("159995.SZ", 30100)
            g.bought = True
    eng = BacktestEngine(str(db), {"initialize": initialize, "handle_data": handle_data},
                         start="2026-07-01", end="2026-07-08",
                         match_price_mode="close", engine_profile=engine_profile,
                         providers=providers)
    eng.run()

    nav = {d["date"]: d["nav"] for d in eng.result.nav_history}
    assert "2026-07-06" in nav and "2026-07-07" in nav
    # 挂钩证据：07-07 送股（原缺口：分钟 profile 不执行反推 → 无事件、净值腰斩）
    evts = [a for a in eng.result.corporate_actions
            if a.get("date") == "2026-07-07" and a.get("type") == "factor_derived_split"]
    assert len(evts) == 1, f"{engine_profile} 未挂钩除权补正"
    assert evts[0]["added"] == 30100
    assert eng.account.positions["159995.SZ"].volume == 60200
    # 净值连续（非 -50% 腰斩）
    chg = nav["2026-07-07"] / nav["2026-07-06"] - 1.0
    assert chg > -0.30, f"{engine_profile} 除权日净值跳变: {chg:.2%}"
    assert abs(chg - (-0.27 / 100.0)) <= 0.001
    return eng


def test_minute_profile_hooks_split(build_db):
    """minute-bar-v1 挂钩：159995 07-07 折算日送股生效、净值不跳水。"""
    _run_profile_nav_test(build_db, "minute-bar-v1")


def test_daily_open_close_proxy_profile_hooks_split(build_db):
    """daily-open-close-proxy-v1 挂钩：同场景送股生效、净值不跳水。"""
    _run_profile_nav_test(build_db, "daily-open-close-proxy-v1")


# =====================================================================
# 10. 回归
# =====================================================================

def test_etf_momentum_no_nav_crash_on_ex_date(build_db):
    """ETF 动量式策略（13 只 ETF 单持仓轮动的迷你版）：07-07 除权日净值不跳水（非 -50%）。"""
    # 迷你 universe：159995（07-07 折算）+ 510500（无除权事件，现金分红带 07-06 型）
    extra = [
        daily_row("510500", "2026-07-06", 6.0, preclose=6.0, pctchg=0.0),
        daily_row("510500", "2026-07-07", 6.0, preclose=6.0, pctchg=0.0),
        daily_row("510500", "2026-07-08", 6.0, preclose=6.0, pctchg=0.0),
    ]
    eng = _etf_engine(build_db, extra_bars=extra)

    def initialize(context):
        pass
    def handle_data(context, data):
        # 动量打分恒选 159995（简化回归：单持仓轮动不换仓）
        if not getattr(g, "bought", False):
            order("159995.SZ", 30100)
            g.bought = True
    eng.strategy = {"initialize": initialize, "handle_data": handle_data}
    eng.run()

    nav = [d["nav"] for d in eng.result.nav_history]
    assert len(nav) == len(_ETF_DAYS)
    # 全序列无 -50% 级跳变（修复前 07-07 会出现 nav 腰斩）
    for prev, curr in zip(nav, nav[1:]):
        chg = curr / prev - 1.0
        assert chg > -0.30, f"净值跳变异常: {chg:.2%}"
    # 除权日净值连续性（与用例 8 同口径）
    by_date = {d["date"]: d["nav"] for d in eng.result.nav_history}
    chg = by_date["2026-07-07"] / by_date["2026-07-06"] - 1.0
    assert abs(chg - (-0.27 / 100.0)) <= 0.001
    # 现金分红带（510500 若持仓）也应无幽灵送股——本场景 510500 未持仓，断言无 factor_derived_merge
    merges = [a for a in eng.result.corporate_actions if a.get("type") == "factor_derived_merge"]
    assert merges == []


def test_stock_zero_regression(engine):
    """股票零回归：反推路径仅 ETF（P1-5 门控），股票（600000）即使 ratio 2.0 也零操作。"""
    _add_pos(engine, "600000.SH", 10000, 10.0)
    curr_df, prev_df = _dfs(2.0, 1.0, code="600000")  # ratio = 2.0（若 ETF 会送股）
    engine._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")

    pos = engine.account.positions["600000.SH"]
    assert pos.volume == 10000
    assert pos.avg_cost == pytest.approx(10.0)
    assert _evt(engine) == []


def test_no_positions_noop(engine):
    """无持仓 → 方法立即返回（零操作）。"""
    curr_df, prev_df = _dfs(3.009, 1.505)
    engine._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")
    assert engine.result.corporate_actions == []
