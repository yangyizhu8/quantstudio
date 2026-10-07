# -*- coding: utf-8 -*-
"""CORP-02 · 数量式卖出零股全清 · 契约测试（六步③，2026-10-07 ②审计 PASS）。

方案 docs/corp02-odd-lot-sell-design.md §2.2 六项；
证据链：docs/evidence/corp-action-etf-merge-fix-acceptance-20261007.md §3
（2026-01-29 四象限清仓单 平台 3296 原量成交 vs 本地旧 round_to_lot 截 3200）。

平台依据：Context7 /kay-ou/ptradeapi（order 数量式无整手声明）+ 平台实证 + A 股规则。
"""
import duckdb
import pandas as pd
import pytest

from quantstudio.backtest.backtest_engine import BacktestEngine, Position


class _Calendar:
    def get_trading_day(self, *a, **kw):
        return None


class _Reference:
    def get_corporate_actions(self, day_str):
        return pd.DataFrame(columns=["code", "cash_div", "stk_div"])

    def get_exrights(self, code, date):
        return None


def _make_engine(tmp_path):
    db_path = tmp_path / "t_corp02.db"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS stock_daily (code VARCHAR)")
    conn.close()
    registry = type("_R", (), {
        "market": type("_M", (), {})(),
        "fundamental": type("_F", (), {})(),
        "reference": _Reference(),
        "calendar": _Calendar(),
    })()
    eng = BacktestEngine(str(db_path), {"initialize": lambda ctx: None},
                         "2026-01-28", "2026-01-30", providers=registry)
    return eng


def _add_pos(eng, code, vol, cost):
    eng.account.positions[code] = Position(code=code, volume=vol, avg_cost=cost,
                                           can_sell=vol)


def _run_sell(eng, code, **kw):
    """以 curr_data（价格行情）直接调 _execute_sell，close/preClose 给中性值。"""
    curr = pd.DataFrame([{"code": code.split(".")[0], "preClose": 10.0}])
    prev = pd.DataFrame([{"code": code.split(".")[0], "close": 10.0}])
    return eng._execute_sell(code, 10.0, date="2026-01-29",
                             curr_data=curr, **kw) if "curr_data" in \
        eng._execute_sell.__code__.co_varnames else \
        eng._execute_sell(code, 10.0, date="2026-01-29", **kw)


# 1. 平台黄金：零股全清原量成交
def test_odd_lot_sell_full_fills(tmp_path):
    """持仓 3293（CORP-01 合并尾差形态）→ order(-3293) → 成交 3293
    （修复前 round_to_lot 截 3200，滞留 93 份=平台 2026-01-29 实证差异）。"""
    eng = _make_engine(tmp_path)
    _add_pos(eng, "159934.SZ", 3293, 8.09)
    vol, _ = eng._execute_sell("159934.SZ", 10.0, sell_shares=3293,
                               date="2026-01-29")
    assert vol == 3293
    assert "159934.SZ" not in eng.account.positions or \
        eng.account.positions["159934.SZ"].volume == 0


# 2. 整手量无变化（回归保护）
def test_round_lot_sell_unchanged(tmp_path):
    eng = _make_engine(tmp_path)
    _add_pos(eng, "512890.SH", 65500, 1.19)
    vol, _ = eng._execute_sell("512890.SH", 1.15, sell_shares=65500,
                               date="2026-01-29")
    assert vol == 65500


# 3. 超卖钳制（can_sell 上限不变）
def test_oversell_clamped(tmp_path):
    eng = _make_engine(tmp_path)
    _add_pos(eng, "159934.SZ", 3293, 8.09)
    vol, _ = eng._execute_sell("159934.SZ", 10.0, sell_shares=4000,
                               date="2026-01-29")
    assert vol == 3293


# 4. 买入方向整手保留（不变面断言）
def test_buy_round_lot_retained(tmp_path):
    eng = _make_engine(tmp_path)
    eng.account.cash = 1_000_000.0
    vol, _ = eng._execute_buy("159934.SZ", 10.0, buy_shares=3293,
                              date="2026-01-29")
    assert vol == 3200  # round_to_lot(3293)=3200：买入必须整手（A 股规则）


# 5. sell_value 分支整手保留（不变面断言）
def test_sell_value_round_lot_retained(tmp_path):
    eng = _make_engine(tmp_path)
    _add_pos(eng, "159934.SZ", 3293, 8.09)
    vol, _ = eng._execute_sell("159934.SZ", 10.0, sell_value=32930.0,
                               date="2026-01-29")  # 3293 股市值 → round 3200
    assert vol == 3200


# 6. sell_all 全量回归（既有语义）
def test_sell_all_full(tmp_path):
    eng = _make_engine(tmp_path)
    _add_pos(eng, "159934.SZ", 3293, 8.09)
    vol, _ = eng._execute_sell("159934.SZ", 10.0, sell_all=True,
                               date="2026-01-29")
    assert vol == 3293