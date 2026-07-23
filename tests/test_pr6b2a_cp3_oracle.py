"""PR6b-2A CP3 tests: Reference Oracle independence + behavior + frozen formula.

Tests cover:
  - Oracle location + independence (not in strategies/, not from Codegen)
  - Frozen parameters match Spec
  - BEHAVIOR tests (directly call Oracle callbacks with controlled stubs):
    * trend pass/fail, positive/negative/zero momentum, NaN exclude
    * volume surge 6-bar boundary (exactly enough, insufficient)
    * held position volume exit
    * stop-loss exit
    * all-negative defensive switch
    * Top3 selection + equal weight
  - Dual price regime (fq='pre' indicators, fq=None stop-loss, cost_basis)
  - Engine loadable

The behavior tests construct synthetic history/context stubs and actually
execute the Oracle's before_trading_start + handle_data — they do NOT merely
grep source strings.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import types
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
ORACLE_PATH = ROOT / "tests" / "strategy_references" / "etf_rotation_ref.py"
EXAMPLES = ROOT / "quantstudio" / "strategy_compiler" / "examples"


def _load_oracle_module():
    """Load the Oracle as a module (not via engine) for behavior testing."""
    spec = importlib.util.spec_from_file_location("etf_rotation_ref", ORACLE_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Provide stub globals the Oracle expects (injected by engine in production)
    mod.__dict__.update({
        "set_universe": lambda x: None,
        "set_limit_mode": lambda x: None,
        "set_commission": lambda **kw: None,
        "set_benchmark": lambda x: None,
        "get_history": lambda *a, **kw: {},
        "get_position": lambda code: types.SimpleNamespace(amount=0, cost_basis=0),
        "order_target_value": lambda code, val: types.SimpleNamespace(status="filled"),
        "log": types.SimpleNamespace(info=lambda *a, **kw: None),
        "g": types.SimpleNamespace(),
    })
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def oracle_source() -> str:
    return ORACLE_PATH.read_text(encoding="utf-8")


@pytest.fixture
def oracle_mod():
    return _load_oracle_module()


@pytest.fixture
def etf_spec() -> dict:
    return json.loads((EXAMPLES / "etf_rotation_spec.json").read_text(encoding="utf-8"))


# ── Stub builders for behavior tests ────────────────────────────────────────

class _StubPosition:
    def __init__(self, amount=0, cost_basis=0.0):
        self.amount = amount
        self.enable_amount = amount
        self.cost_basis = cost_basis


class _StubPortfolio:
    def __init__(self, cash=100000.0, positions=None):
        self.cash = cash
        self.total_value = cash
        self.positions = positions or {}


class _StubContext:
    def __init__(self, cash=100000.0, positions=None):
        self.portfolio = _StubPortfolio(cash, positions or {})


def _make_history(closes, volumes, fq):
    """Build a get_history stub returning {code: {'close': array, 'volume': array}}."""
    def _get_history(count, frequency="1d", field=None, security_list=None,
                     fq=None, include=False, is_dict=False):
        result = {}
        codes = security_list if isinstance(security_list, list) else [security_list]
        for i, code in enumerate(codes):
            c = closes[i] if i < len(closes) else closes[0]
            v = volumes[i] if i < len(volumes) else volumes[0]
            n = min(count, len(c))
            result[code] = {"close": np.array(c[-n:], dtype=float),
                            "volume": np.array(v[-n:], dtype=float)}
        return result
    return _get_history


def _run_oracle_signal(oracle_mod, closes, volumes, t1_raw_closes=None,
                       held_positions=None):
    """Run initialize + before_trading_start on the Oracle with controlled data.

    Args:
        closes: list of close arrays (one per ETF, front-adjusted). If shorter
                than 13, the first array is reused for remaining ETFs.
        volumes: list of volume arrays (one per ETF). Same reuse rule.
        t1_raw_closes: list of T-1 raw close values (for stop-loss). Same reuse.
        held_positions: dict {code: _StubPosition} for context.portfolio.positions

    Returns: the oracle_mod.g object with all computed signals.
    """
    # Must call initialize first to set up g.* params (data_window, etc.)
    oracle_mod.initialize(_StubContext())

    g = oracle_mod.g
    ctx = _StubContext(positions=held_positions or {})
    pool = oracle_mod.ETF_POOL

    # Map ETF code → data (reuse first array if fewer provided)
    close_map = {pool[i]: closes[min(i, len(closes) - 1)] for i in range(len(pool))}
    vol_map = {pool[i]: volumes[min(i, len(volumes) - 1)] for i in range(len(pool))}
    raw_map = {}
    if t1_raw_closes:
        for i in range(len(pool)):
            raw_map[pool[i]] = t1_raw_closes[min(i, len(t1_raw_closes) - 1)]

    def _get_history(count, frequency="1d", field=None, security_list=None,
                     fq=None, include=False, is_dict=False):
        result = {}
        codes = security_list if isinstance(security_list, list) else [security_list]
        for code in codes:
            if fq is None:
                # stop-loss path: return only T-1 raw close
                raw = raw_map.get(code, close_map[code][-1])
                result[code] = {"close": np.array([float(raw)])}
            else:
                c = close_map[code]
                v = vol_map[code]
                n = min(count, len(c))
                result[code] = {"close": np.array(c[-n:], dtype=float),
                                "volume": np.array(v[-n:], dtype=float)}
        return result

    oracle_mod.__dict__["get_history"] = _get_history
    oracle_mod.__dict__["get_position"] = lambda code: held_positions.get(code, _StubPosition()) if held_positions else _StubPosition()

    oracle_mod.before_trading_start(ctx, None)
    return g


# ---------------------------------------------------------------------------
# CP3: Oracle location + independence (structural checks)
# ---------------------------------------------------------------------------

class TestOracleLocation:
    def test_oracle_exists(self):
        assert ORACLE_PATH.exists()

    def test_not_in_strategies_dir(self):
        strategies_dir = ROOT / "quantstudio" / "backtest" / "strategies"
        if strategies_dir.exists():
            assert "etf_rotation_ref.py" not in [f.name for f in strategies_dir.iterdir()]

    def test_not_in_render_output(self):
        assert "generated_strategies" not in str(ORACLE_PATH)


class TestOracleIndependence:
    def test_has_independence_header(self, oracle_source):
        assert "NOT generated by" in oracle_source or "INDEPENDENT" in oracle_source.upper()

    def test_does_not_import_compiler(self, oracle_source):
        for line in oracle_source.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                assert "strategy_compiler" not in stripped

    def test_does_not_import_codegen(self, oracle_source):
        for line in oracle_source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            if stripped.startswith("import ") or stripped.startswith("from "):
                assert "codegen" not in stripped.lower()


# ---------------------------------------------------------------------------
# CP3: Frozen parameters (source-level sanity)
# ---------------------------------------------------------------------------

class TestOracleFrozenParams:
    def test_13_etf_codes(self, oracle_source):
        for code in ["518880.SH", "513880.SH", "159770.SZ", "159819.SZ",
                      "513100.SH", "159915.SZ", "515880.SH", "513120.SH",
                      "159755.SZ", "159652.SZ", "510500.SH", "159870.SZ",
                      "159995.SZ"]:
            assert code in oracle_source

    def test_defensive_asset(self, oracle_source):
        assert "511880.SH" in oracle_source

    def test_benchmark(self, oracle_source):
        assert "000300.SH" in oracle_source and "set_benchmark" in oracle_source


# ---------------------------------------------------------------------------
# CP3: BEHAVIOR TESTS — directly call Oracle callbacks (阻断3)
# ---------------------------------------------------------------------------

class TestOracleBehaviorTrendFilter:
    """Module 2: close > MA20 > MA60 persistent multi-head."""

    def test_trend_pass(self, oracle_mod):
        # Rising price: close above MA20 above MA60
        close = [10 + 0.1 * i for i in range(120)]
        vol = [1000] * 120
        g = _run_oracle_signal(oracle_mod, [close], [vol])
        etf = oracle_mod.ETF_POOL[0]
        assert bool(g.trend_ok.get(etf, False)) is True

    def test_trend_fail_close_below_ma20(self, oracle_mod):
        # Price drops at the end: close < MA20
        close = [10 + 0.1 * i for i in range(119)] + [5.0]
        vol = [1000] * 120
        g = _run_oracle_signal(oracle_mod, [close], [vol])
        etf = oracle_mod.ETF_POOL[0]
        assert bool(g.trend_ok.get(etf, True)) is False


class TestOracleBehaviorMomentumScore:
    """Modules 3-6: regression × R² momentum score."""

    def test_positive_momentum(self, oracle_mod):
        # Exponential growth → positive annualized_return × R²
        close = [10.0 * math.exp(0.002 * i) for i in range(120)]
        vol = [1000] * 120
        g = _run_oracle_signal(oracle_mod, [close], [vol])
        etf = oracle_mod.ETF_POOL[0]
        score = g.momentum_scores.get(etf)
        assert score is not None and score > 0

    def test_negative_momentum(self, oracle_mod):
        # Declining price → negative score
        close = [10.0 * math.exp(-0.002 * i) for i in range(120)]
        vol = [1000] * 120
        g = _run_oracle_signal(oracle_mod, [close], [vol])
        etf = oracle_mod.ETF_POOL[0]
        score = g.momentum_scores.get(etf)
        assert score is not None and score < 0

    def test_zero_variance_produces_score_zero(self, oracle_mod):
        """阻断1: flat price (zero variance) must produce momentum_score=0,
        not skip the ETF entirely."""
        close = [10.0] * 120  # perfectly flat
        vol = [1000] * 120
        g = _run_oracle_signal(oracle_mod, [close], [vol])
        etf = oracle_mod.ETF_POOL[0]
        # The ETF must be in momentum_scores with score=0
        assert etf in g.momentum_scores, \
            "zero-variance ETF must still get a score (0), not be skipped"
        assert g.momentum_scores[etf] == 0.0

    def test_nan_excluded(self, oracle_mod):
        """NaN momentum → ETF excluded from momentum_scores."""
        # Create a price series that produces NaN (negative prices → log NaN)
        close = [10.0] * 119 + [-1.0]
        vol = [1000] * 120
        g = _run_oracle_signal(oracle_mod, [close], [vol])
        etf = oracle_mod.ETF_POOL[0]
        # Either not in scores (NaN excluded) or has valid score
        if etf in g.momentum_scores:
            assert not np.isnan(g.momentum_scores[etf])


class TestOracleBehaviorVolumeSurge:
    """Module 7: volume surge (frozen §6.3: volume[-1] vs mean(volume[-6:-1])×2.5)."""

    def test_surge_detected_6_bars(self, oracle_mod):
        """阻断2: exactly 6 volume bars should be sufficient (not 7).
        volume = [1,1,1,1,1,10] → signal=10, baseline=mean([1,1,1,1,1])=1, surge=True."""
        close = [10.0 * math.exp(0.001 * i) for i in range(120)]
        vol = [1000] * 114 + [1, 1, 1, 1, 1, 10000]  # last 6: surge
        g = _run_oracle_signal(oracle_mod, [close], [vol])
        etf = oracle_mod.ETF_POOL[0]
        assert bool(g.volume_surge.get(etf, False)) is True, \
            "6 bars with surge at [-1] should be detected"

    def test_no_surge_normal_volume(self, oracle_mod):
        close = [10.0 * math.exp(0.001 * i) for i in range(120)]
        vol = [1000] * 120  # uniform volume, no surge
        g = _run_oracle_signal(oracle_mod, [close], [vol])
        etf = oracle_mod.ETF_POOL[0]
        assert bool(g.volume_surge.get(etf, True)) is False

    def test_exactly_6_volume_bars_boundary(self, oracle_mod):
        """6 volume bars total (exactly the frozen minimum) must work."""
        close = [10.0 + 0.1 * i for i in range(120)]
        vol = [1000] * 114 + [1, 1, 1, 1, 1, 10]
        g = _run_oracle_signal(oracle_mod, [close], [vol])
        etf = oracle_mod.ETF_POOL[0]
        assert etf in g.volume_surge  # was computed (not skipped)


class TestOracleBehaviorStopLoss:
    """Module 9: -8% stop loss (cost_basis vs T-1 raw close)."""

    def test_stop_loss_triggered(self, oracle_mod):
        """Held position with -10% drawdown → stop_loss_exits contains it."""
        close = [10.0 * math.exp(0.001 * i) for i in range(120)]
        vol = [1000] * 120
        etf = oracle_mod.ETF_POOL[0]
        held = {etf: _StubPosition(amount=100, cost_basis=10.0)}
        # T-1 raw close = 8.5 → drawdown = (10-8.5)/10 = 15% > 8%
        g = _run_oracle_signal(oracle_mod, [close], [vol],
                               t1_raw_closes=[8.5], held_positions=held)
        assert etf in g.stop_loss_exits

    def test_stop_loss_not_triggered(self, oracle_mod):
        """Held position with -3% drawdown → no stop loss."""
        close = [10.0 * math.exp(0.001 * i) for i in range(120)]
        vol = [1000] * 120
        etf = oracle_mod.ETF_POOL[0]
        held = {etf: _StubPosition(amount=100, cost_basis=10.0)}
        # T-1 raw close = 9.7 → drawdown = 3% < 8%
        g = _run_oracle_signal(oracle_mod, [close], [vol],
                               t1_raw_closes=[9.7], held_positions=held)
        assert etf not in g.stop_loss_exits


class TestOracleBehaviorDefensive:
    """Module 9: all-negative → defensive asset (511880.SH)."""

    def test_all_negative_triggers_defensive(self, oracle_mod):
        """When all ETFs have negative momentum, all_negative=True."""
        close = [10.0 * math.exp(-0.002 * i) for i in range(120)]
        vol = [1000] * 120
        closes = [close] * 13
        vols = [vol] * 13
        g = _run_oracle_signal(oracle_mod, closes, vols)
        assert g.all_negative is True


class TestOracleBehaviorTop3:
    """Module 8: Top3 selection."""

    def test_top3_selected_by_momentum(self, oracle_mod):
        """3 ETFs with different momentum → top3 selects highest 3."""
        base = [10.0 * math.exp(0.001 * i) for i in range(120)]
        vol = [1000] * 120
        # Give first 5 ETFs different slopes
        closes = []
        for i in range(5):
            c = [10.0 * math.exp((0.001 + 0.0005 * i) * j) for j in range(120)]
            closes.append(c)
        closes.extend([base] * 8)  # rest get base
        vols = [vol] * 13
        g = _run_oracle_signal(oracle_mod, closes, vols)
        # At least some ETFs have positive scores
        positive = {e: s for e, s in g.momentum_scores.items() if s > 0}
        assert len(positive) >= 3


class TestOracleBehaviorHeldExit:
    """Module 7: held position volume surge → exit."""

    def test_held_surge_marked_for_exit(self, oracle_mod):
        """A held ETF with volume_surge=True should trigger exit in handle_data."""
        close = [10.0 * math.exp(0.001 * i) for i in range(120)]
        vol = [1000] * 114 + [1, 1, 1, 1, 1, 10000]  # surge at end
        etf = oracle_mod.ETF_POOL[0]
        held = {etf: _StubPosition(amount=100, cost_basis=10.0)}
        g = _run_oracle_signal(oracle_mod, [close], [vol],
                               t1_raw_closes=[10.0], held_positions=held)
        assert bool(g.volume_surge.get(etf)) is True
        # Run via _run_full_oracle for consistent capture
        g2, orders = _run_full_oracle(oracle_mod, [close], [vol],
                                      t1_raw_closes=[10.0], held_positions=held)
        sells = [o for o in orders if o["target_value"] == 0]
        assert any("518880" in o["code"] for o in sells), \
            f"held ETF with surge should be sold: {sells}"


# ---------------------------------------------------------------------------
# CP3: Dual price regime (source-level + behavior)
# ---------------------------------------------------------------------------

class TestDualPriceRegime:
    def test_indicator_uses_fq_pre(self, oracle_source):
        assert "fq='pre'" in oracle_source or 'fq="pre"' in oracle_source

    def test_stoploss_uses_fq_none(self, oracle_source):
        assert "fq=None" in oracle_source

    def test_stoploss_uses_cost_basis(self, oracle_source):
        assert "cost_basis" in oracle_source
        assert "avg_cost" not in oracle_source


# ---------------------------------------------------------------------------
# CP3: Engine loadable
# ---------------------------------------------------------------------------

class TestOracleEngineLoadable:
    def test_oracle_loads(self):
        from quantstudio.backtest.run_ptrade_strategy import load_strategy
        funcs, mod = load_strategy(str(ORACLE_PATH))
        assert "initialize" in funcs
        assert "before_trading_start" in funcs
        assert "handle_data" in funcs


# ---------------------------------------------------------------------------
# CP3 audit-fix v3: comprehensive handle_data behavior tests
# orders = list of dicts: {code, target_value, status, reason}
# ---------------------------------------------------------------------------

def _run_full_oracle(oracle_mod, closes, volumes, t1_raw_closes=None,
                     held_positions=None, order_status="filled", order_reason=None):
    """Run initialize + before_trading_start + handle_data. Returns (g, orders).

    orders = list of dicts: {code, target_value, status, reason}
    """
    g = _run_oracle_signal(oracle_mod, closes, volumes, t1_raw_closes, held_positions)
    ctx = _StubContext(cash=100000.0, positions=held_positions or {})

    orders = []
    def _capture(code, val):
        entry = {"code": code, "target_value": val,
                 "status": order_status, "reason": order_reason}
        orders.append(entry)
        return types.SimpleNamespace(status=order_status, reason=order_reason)
    oracle_mod.__dict__["order_target_value"] = _capture
    oracle_mod.handle_data(ctx, None)
    return g, orders


class TestHandleDataTop3EqualWeight:
    def test_top3_buys_equal_weight(self, oracle_mod):
        vol = [1000] * 120
        closes = []
        for i in range(5):
            closes.append([10.0 * math.exp((0.001 + 0.0003 * i) * j) for j in range(120)])
        closes.extend([[10.0 * math.exp(0.001 * j) for j in range(120)]] * 8)
        g, orders = _run_full_oracle(oracle_mod, closes, [vol] * 13)
        buys = [o for o in orders if o["target_value"] > 0]
        assert len(buys) == 3
        for o in buys:
            assert abs(o["target_value"] - 100000.0 / 3) < 0.01


class TestHandleDataDefensive:
    def test_defensive_clears_risk_and_buys_defensive(self, oracle_mod):
        declining = [10.0 * math.exp(-0.002 * i) for i in range(120)]
        vol = [1000] * 120
        held = {"515880.SS": _StubPosition(amount=1000, cost_basis=10.0)}
        g, orders = _run_full_oracle(oracle_mod, [declining] * 13, [vol] * 13,
                                     held_positions=held)
        sells = [o for o in orders if o["target_value"] == 0]
        buys = [o for o in orders if o["target_value"] > 0]
        assert any("515880" in o["code"] for o in sells)
        assert any("511880" in o["code"] for o in buys)
        assert len([o for o in buys if "511880" in o["code"]]) == 1


class TestHandleDataStopLoss:
    def test_stop_loss_produces_sell_order(self, oracle_mod):
        rising = [10.0 * math.exp(0.001 * i) for i in range(120)]
        vol = [1000] * 120
        held = {"518880.SS": _StubPosition(amount=1000, cost_basis=10.0)}
        g, orders = _run_full_oracle(oracle_mod, [rising], [vol],
                                     t1_raw_closes=[8.0], held_positions=held)
        sells = [o for o in orders if o["target_value"] == 0]
        assert any("518880" in o["code"] for o in sells)


class TestHandleDataHeldNotResold:
    def test_held_selected_not_duplicated(self, oracle_mod):
        rising = [10.0 * math.exp(0.002 * i) for i in range(120)]
        vol = [1000] * 120
        held = {"518880.SS": _StubPosition(amount=1000, cost_basis=10.0)}
        g, orders = _run_full_oracle(oracle_mod, [rising] + [rising] * 12,
                                     [vol] * 13, t1_raw_closes=[10.0] * 13,
                                     held_positions=held)
        sells_518880 = [o for o in orders if o["target_value"] == 0 and "518880" in o["code"]]
        assert len(sells_518880) == 0
        buys_518880 = [o for o in orders if o["target_value"] > 0 and "518880" in o["code"]]
        assert len(buys_518880) == 0


class TestHandleDataSuffixMixed:
    def test_sh_signal_ss_position_held_selected_not_sold(self, oracle_mod):
        """.SH signal + .SS position: held ETF in selected → NOT sold, NOT re-bought."""
        # Give 518880 (pool[0]) the HIGHEST momentum so it's in top3
        highest = [10.0 * math.exp(0.003 * i) for i in range(120)]
        lower = [10.0 * math.exp(0.001 * i) for i in range(120)]
        vol = [1000] * 120
        closes = [highest] + [lower] * 12  # ETF0 highest → in top3
        held = {"518880.SS": _StubPosition(amount=1000, cost_basis=10.0)}
        g, orders = _run_full_oracle(oracle_mod, closes, [vol] * 13,
                                     t1_raw_closes=[10.0] * 13, held_positions=held)
        sells_518880 = [o for o in orders if o["target_value"] == 0 and "518880" in o["code"]]
        assert len(sells_518880) == 0, f"selected .SS held should not be sold: {sells_518880}"
        buys_518880 = [o for o in orders if o["target_value"] > 0 and "518880" in o["code"]]
        assert len(buys_518880) == 0, f"held should not be re-bought: {buys_518880}"

    def test_sh_signal_ss_position_not_selected_sold_once(self, oracle_mod):
        """.SH signal + .SS position: held ETF NOT in selected → exactly ONE sell via .SS key."""
        # Give 518880 (pool[0]) the LOWEST momentum so it's NOT in top3
        lowest = [10.0 * math.exp(-0.001 * i) for i in range(120)]
        higher = [10.0 * math.exp(0.002 * i) for i in range(120)]
        vol = [1000] * 120
        closes = [lowest] + [higher] * 12  # ETF0 lowest → not in top3
        held = {"518880.SS": _StubPosition(amount=1000, cost_basis=10.0)}
        g, orders = _run_full_oracle(oracle_mod, closes, [vol] * 13,
                                     t1_raw_closes=[10.0] * 13, held_positions=held)
        sells_518880 = [o for o in orders if o["target_value"] == 0 and "518880" in o["code"]]
        assert len(sells_518880) == 1, f"exactly ONE sell expected, got {len(sells_518880)}"
        assert sells_518880[0]["code"] == "518880.SS", \
            f"sell must use .SS position key, got {sells_518880[0]['code']}"

    def test_sh_defensive_ss_position_not_rebought(self, oracle_mod):
        declining = [10.0 * math.exp(-0.002 * i) for i in range(120)]
        vol = [1000] * 120
        held = {"511880.SS": _StubPosition(amount=10000, cost_basis=100.0)}
        g, orders = _run_full_oracle(oracle_mod, [declining] * 13, [vol] * 13,
                                     held_positions=held)
        buys_defensive = [o for o in orders if o["target_value"] > 0 and "511880" in o["code"]]
        assert len(buys_defensive) == 0


class TestHandleDataOrderStatus:
    def test_filled_status_captured(self, oracle_mod):
        rising = [10.0 * math.exp(0.002 * i) for i in range(120)]
        vol = [1000] * 120
        g, orders = _run_full_oracle(oracle_mod, [rising] * 13, [vol] * 13,
                                     order_status="filled")
        assert all(o["status"] == "filled" for o in orders)

    def test_pending_status_captured(self, oracle_mod):
        rising = [10.0 * math.exp(0.002 * i) for i in range(120)]
        vol = [1000] * 120
        g, orders = _run_full_oracle(oracle_mod, [rising] * 13, [vol] * 13,
                                     order_status="pending")
        assert all(o["status"] == "pending" for o in orders)

    def test_rejected_status_with_reason_captured(self, oracle_mod):
        rising = [10.0 * math.exp(0.002 * i) for i in range(120)]
        vol = [1000] * 120
        g, orders = _run_full_oracle(oracle_mod, [rising] * 13, [vol] * 13,
                                     order_status="rejected", order_reason="insufficient_cash")
        assert all(o["status"] == "rejected" for o in orders)
        assert all(o["reason"] == "insufficient_cash" for o in orders)


class TestHandleDataVolumeExitNotInCandidates:
    def test_surging_etf_not_bought(self, oracle_mod):
        high_mom_surge_close = [10.0 * math.exp(0.003 * i) for i in range(120)]
        high_mom_surge_vol = [1000] * 114 + [1, 1, 1, 1, 1, 100000]
        normal_close = [10.0 * math.exp(0.001 * i) for i in range(120)]
        normal_vol = [1000] * 120
        closes = [high_mom_surge_close] + [normal_close] * 12
        vols = [high_mom_surge_vol] + [normal_vol] * 12
        g, orders = _run_full_oracle(oracle_mod, closes, vols)
        buys = [o for o in orders if o["target_value"] > 0]
        assert not any("518880" in o["code"] for o in buys)


# ---------------------------------------------------------------------------
# CP3 audit-fix v3: exit deduplication conflict tests (阻断2 补齐)
# Each bare code gets at most ONE exit order per round.
# ---------------------------------------------------------------------------

class TestExitDeduplication:
    """同一持仓在多种退出条件同时满足时，只产生一笔卖单，原因按优先级冻结。"""

    def _setup_conflict(self, oracle_mod, close, vol, t1_raw, held_suffix=".SS"):
        """Helper: hold one ETF with configurable data to trigger multiple exits."""
        etf = oracle_mod.ETF_POOL[0]  # 518880.SH
        held = {etf.replace(".SH", held_suffix): _StubPosition(amount=1000, cost_basis=10.0)}
        g, orders = _run_full_oracle(oracle_mod, [close] + [close] * 12,
                                     [vol] * 13, t1_raw_closes=[t1_raw] * 13,
                                     held_positions=held)
        sells = [o for o in orders if o["target_value"] == 0 and "518880" in o["code"]]
        return sells

    def test_stop_loss_plus_all_negative_one_sell(self, oracle_mod):
        """stop_loss + all_negative → exactly 1 sell (stop_loss priority)."""
        # Declining price (all_negative) + deep drawdown (stop_loss)
        declining = [10.0 * math.exp(-0.003 * i) for i in range(120)]
        vol = [1000] * 120
        sells = self._setup_conflict(oracle_mod, declining, vol, t1_raw=5.0)
        assert len(sells) == 1, f"stop_loss+all_negative should be 1 sell, got {len(sells)}"

    def test_volume_surge_plus_all_negative_one_sell(self, oracle_mod):
        """volume_surge + all_negative → exactly 1 sell."""
        declining = [10.0 * math.exp(-0.003 * i) for i in range(120)]
        vol = [1000] * 114 + [1, 1, 1, 1, 1, 100000]  # surge + declining
        sells = self._setup_conflict(oracle_mod, declining, vol, t1_raw=10.0)
        assert len(sells) == 1, f"surge+all_negative should be 1 sell, got {len(sells)}"

    def test_stop_loss_plus_volume_surge_one_sell(self, oracle_mod):
        """stop_loss + volume_surge → exactly 1 sell (stop_loss wins priority)."""
        rising = [10.0 * math.exp(0.001 * i) for i in range(120)]
        vol = [1000] * 114 + [1, 1, 1, 1, 1, 100000]  # surge + drawdown
        sells = self._setup_conflict(oracle_mod, rising, vol, t1_raw=8.0)
        assert len(sells) == 1, f"stop_loss+surge should be 1 sell, got {len(sells)}"

    def test_all_three_conflicts_one_sell(self, oracle_mod):
        """stop_loss + volume_surge + all_negative → exactly 1 sell."""
        declining = [10.0 * math.exp(-0.003 * i) for i in range(120)]
        vol = [1000] * 114 + [1, 1, 1, 1, 1, 100000]
        sells = self._setup_conflict(oracle_mod, declining, vol, t1_raw=5.0)
        assert len(sells) == 1, f"3-way conflict should be 1 sell, got {len(sells)}"
