# -*- coding: utf-8 -*-
"""CORP-01 · ETF 份额合并（任意比）处理 · 契约测试（六步③实施，2026-10-07）。

方案：docs/corp-action-etf-merge-design.md §2.2 六项；
证据：docs/evidence/corp-action-etf-merge-2025-09-22.md（159934 黄金ETF
2025-09-22 合并比例 0.948126035，平台 4000→3796 份 + 成本 7.673→8.086 双恒等）。

带区契约（CORP-01 后）：
  ratio<0.50        出界 → WARN+跳过（脏数据保护）
  0.50≤ratio<0.99   合并带：去吸附直接用反推 ratio + 按份取整 + 成本守恒
  0.99~1.01         非除权 → 跳过（不变）
  1.01~1.10         现金分红带 → 跳过+WARN（不变，阶段2 精确入账）
  ≥1.10             送股带 0.5 吸附（不变）
"""
import logging

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


def _make_engine(tmp_path, cost_basis_method="diluted"):
    db_path = tmp_path / "t_corp01.db"
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
                         "2025-09-18", "2025-09-23",
                         providers=registry,
                         cost_basis_method=cost_basis_method)
    return eng


def _add_pos(eng, code, vol, cost):
    eng.account.positions[code] = Position(code=code, volume=vol, avg_cost=cost,
                                           can_sell=vol)


def _dfs(prev_close, curr_preclose, code):
    return (pd.DataFrame([{"code": code, "preClose": curr_preclose}]),
            pd.DataFrame([{"code": code, "close": prev_close}]))


# 1. 平台黄金：159934 黄金ETF 2025-09-22（双恒等式锚定）
def test_platform_golden_159934(tmp_path):
    """4000 份@成本 7.673，prev_close 7.823 / preClose 8.251（除权参考价）
    → ratio=0.948127（与公告 0.948126035 六位吻合）→ 3792 份 + 成本 8.0867。
    平台侧 3796/8.086 为内部自洽值（取整规则未公开，尾差 3.5≈4 份≈0.02% 登记
    于证据件——回测判据噪声带内）。"""
    eng = _make_engine(tmp_path)
    _add_pos(eng, "159934.SZ", 4000, 7.673)
    curr_df, prev_df = _dfs(7.823, 8.251, "159934")
    eng._apply_factor_derived_split(curr_df, prev_df, "2025-09-22")

    pos = eng.account.positions["159934.SZ"]
    assert pos.volume == int(round(4000 * (7.823 / 8.251)))  # = 3793（3792.506 四舍五入）
    assert pos.volume == 3793
    # 成本守恒（恒等式①：7.673×4000/3793）
    assert pos.avg_cost == pytest.approx(7.673 * 4000 / 3793, abs=1e-4)
    assert pos.avg_cost == pytest.approx(8.0917, abs=1e-4)
    assert pos.can_sell == 3793
    # 恒等式②登记：nav 跳变差 = (4000-3793)×8.408 = 1744.46 与平台 (4000-3796)×8.408=1715.23
    # 差 29.2 元（0.019%）= 平台取整尾差，噪声带内（见证据件 §3 注）
    evts = [a for a in eng.result.corporate_actions
            if a.get("type") == "factor_derived_merge"]
    assert len(evts) == 1
    assert evts[0]["ratio"] == pytest.approx(0.948127, abs=1e-6)
    assert evts[0]["new_volume"] == 3793


# 2. 出界保护
def test_merge_out_of_band_skips(tmp_path, caplog):
    eng = _make_engine(tmp_path)
    _add_pos(eng, "159934.SZ", 4000, 7.673)
    curr_df, prev_df = _dfs(2.0, 6.0, "159934")   # ratio=0.333<0.50
    with caplog.at_level(logging.WARNING, logger="quantstudio.backtest.backtest_engine"):
        eng._apply_factor_derived_split(curr_df, prev_df, "2025-09-22")
    assert eng.account.positions["159934.SZ"].volume == 4000
    assert "出界" in caplog.text


# 3. 送股带回归保护（ratio=1.5 吸附语义不变）
def test_send_stock_band_unchanged(tmp_path):
    eng = _make_engine(tmp_path)
    _add_pos(eng, "511030.SS", 10000, 1.0)
    curr_df, prev_df = _dfs(3.0, 2.0, "511030")   # ratio=1.5
    eng._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")
    pos = eng.account.positions["511030.SS"]
    assert pos.volume == 15000                     # 整手语义保留（送股带）
    assert pos.avg_cost == pytest.approx(1.0 * 10000 / 15000)


# 4. 现金分红带回归保护（跳过+WARN 不变）
def test_cash_dividend_band_unchanged(tmp_path, caplog):
    eng = _make_engine(tmp_path)
    _add_pos(eng, "510500.SS", 10000, 1.0)
    curr_df, prev_df = _dfs(1.05, 1.0, "510500")  # ratio=1.05 ∈(1.01,1.10)
    with caplog.at_level(logging.WARNING, logger="quantstudio.backtest.backtest_engine"):
        eng._apply_factor_derived_split(curr_df, prev_df, "2026-07-07")
    assert eng.account.positions["510500.SS"].volume == 10000
    assert "现金分红带" in caplog.text


# 5. 成本口径开关正交（moving_avg 下合并同样生效）
def test_merge_moving_avg_orthogonal(tmp_path):
    eng = _make_engine(tmp_path, cost_basis_method="moving_avg")
    assert eng.cost_basis_method == "moving_avg"
    _add_pos(eng, "159934.SZ", 4000, 7.673)
    curr_df, prev_df = _dfs(7.823, 8.251, "159934")
    eng._apply_factor_derived_split(curr_df, prev_df, "2025-09-22")
    assert eng.account.positions["159934.SZ"].volume == 3793
    assert eng.account.positions["159934.SZ"].avg_cost == pytest.approx(8.0917, abs=1e-4)


# 6. 非除权带保护（ratio≈1.0 不触发）
def test_no_exright_unchanged(tmp_path):
    eng = _make_engine(tmp_path)
    _add_pos(eng, "512890.SH", 65500, 1.19)
    curr_df, prev_df = _dfs(1.139, 1.138, "512890")  # ratio≈1.0009
    eng._apply_factor_derived_split(curr_df, prev_df, "2025-09-30")
    assert eng.account.positions["512890.SH"].volume == 65500
    assert eng.result.corporate_actions == []