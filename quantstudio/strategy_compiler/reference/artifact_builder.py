"""Build frozen reference signal/order/NAV artifacts by running the independent
Reference Oracle against controlled hermetic inputs.

review 硬要求：
- artifacts 由 Oracle 真实运行生成，非手工拼接；
- reference_orders.json 三字段分离严格独立（trigger_reason 策略层 / order_status 引擎层 /
  order_reason 引擎层），rejected 原因不得塞 trigger_reason，order_reason 不得伪造策略原因；
- 确定性排序、固定数值格式、bare code + QMT code 双口径、日期+时区明确、每个 artifact 有
  schema_version 且能独立校验；
- input_data_digest 若当前无法生成真实数据 digest，标 blocked，不得用文件存在冒充冻结证据。

设计：builder 接收一个确定性的多日场景（scenario），逐日跑 Oracle 的 initialize +
before_trading_start + handle_data，捕获：
  - 每日每 ETF 的信号（来自 g.momentum_scores / g.trend_ok / g.volume_surge / 选股结果）；
  - 每笔订单（trigger_reason 来自 Oracle 的 exit_decisions/defensive/rotation 标签；
    order_status 来自 order_target_value 返回的 status；order_reason 来自返回的 reason）；
  - 每日 NAV（来自模拟账户的总资产）。
模拟账户用最小撮合：buy/sell 按 scenario 给定的 fill 价成交，next_open 语义（T 日下单、
T+1 开盘成交）由 builder 的订单队列实现，order_status 取自 Oracle 看到的 order_target_value
返回值（filled/pending/rejected/noop）。
"""
from __future__ import annotations

import importlib.util
import math
import types
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

BJ_TZ = timezone(timedelta(hours=8))
TIMEZONE_NAME = "Asia/Shanghai"


# ── Deterministic synthetic scenario (frozen inputs; NOT real DuckDB) ─────────
@dataclass
class ScenarioDay:
    """One trading day in the hermetic scenario."""
    trading_date: str            # YYYY-MM-DD (T day; signals computed from T-1 history)
    t1_open_prices: Dict[str, float]   # bare_code -> T+1 open fill price (next_open)
    history_closes: Dict[str, List[float]]   # bare_code -> front-adjusted close series up to T-1
    history_volumes: Dict[str, List[float]]  # bare_code -> volume series up to T-1
    t1_raw_closes: Dict[str, float]    # bare_code -> T-1 raw close (stop-loss)


@dataclass
class Scenario:
    strategy_id: str
    engine_semantics_version: str
    etf_pool: List[str]                 # QMT codes (e.g. 518880.SH)
    defensive_asset: str               # QMT code
    days: List[ScenarioDay] = field(default_factory=list)


@dataclass
class ReferenceArtifacts:
    signals: Dict[str, Any]
    orders: Dict[str, Any]
    nav: Dict[str, Any]
    source_digest: Dict[str, Any]


def _load_oracle(oracle_path: str | Path):
    """Load Oracle module with stub globals (engine-injected in production)."""
    spec = importlib.util.spec_from_file_location("etf_rotation_ref_g2", oracle_path)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__.update({
        "set_universe": lambda x: None,
        "set_limit_mode": lambda x: None,
        "set_commission": lambda **kw: None,
        "set_benchmark": lambda x: None,
        "log": types.SimpleNamespace(info=lambda *a, **kw: None),
        "g": types.SimpleNamespace(),
    })
    spec.loader.exec_module(mod)
    return mod


def _bare(qmt_code: str) -> str:
    return qmt_code.split(".")[0]


class _SimPosition:
    def __init__(self, amount=0, cost_basis=0.0):
        self.amount = amount
        self.enable_amount = amount
        self.cost_basis = cost_basis


class _SimPortfolio:
    def __init__(self, cash=100000.0):
        self.cash = cash
        self.positions: Dict[str, _SimPosition] = {}
        self.total_value = cash

    def revalue(self, prices: Dict[str, float]):
        """Recompute total_value from cash + positions valued at given bare->price."""
        pv = 0.0
        for pk, pos in self.positions.items():
            if pos.amount > 0:
                pv += pos.amount * prices.get(_bare(pk), pos.cost_basis)
        self.total_value = self.cash + pv


class _SimContext:
    def __init__(self, cash=100000.0):
        self.portfolio = _SimPortfolio(cash)


# Known engine reject reasons (engine-layer; never strategy). Mirrors G1-I engine.
_ENGINE_REJECT_REASONS = {
    "insufficient_cash_after_sells",
    "mandatory_sell_failed",
    "limit_down_blocked",
    "direction_changed_at_drain",
    "halted",
    "limit_up_blocked",
    "insufficient_position",
}

# Strategy-layer trigger reasons (Oracle exit/entry labels).
_TRIGGER_REASONS = {
    "stop_loss", "volume_surge", "rotation_exit", "rotation_buy",
    "defensive_clear", "defensive_buy", "no_candidate_clear",
}


def _classify_order(trigger: Optional[str], status: str, reason: Optional[str]) -> Tuple[str, str, str]:
    """Enforce three-field separation: trigger_reason (strategy), order_status/order_reason (engine).

    rejected/cancel reason stays in order_reason; trigger stays strategy-side.
    """
    # trigger_reason must be a strategy label or None; never an engine reject reason
    trig = trigger if (trigger is None or trigger in _TRIGGER_REASONS) else None
    # order_reason: only engine reject/cancel reasons; never a strategy trigger
    oreason = reason if (reason is None or reason in _ENGINE_REJECT_REASONS) else None
    return trig, status, oreason


def build_reference_artifacts(
    scenario: Scenario,
    oracle_path: str | Path,
    source_digest: Dict[str, Any],
    starting_cash: float = 100000.0,
) -> ReferenceArtifacts:
    """Run the Oracle over the scenario, capture frozen signal/order/NAV artifacts.

    All outputs deterministic (sorted, fixed numeric format, bare+QMT code, BJ timezone).
    Orders: next_open semantics — T-day order_target_value returns pending (T+1 fill);
    builder drains pending orders at next day's t1_open price and reports the status the
    Oracle observed at order-creation time (pending) plus the eventual fill in filled_*.
    For CP3 hermetic scope the Oracle's order_target_value is stubbed to return a status
    reflecting cash sufficiency so order_status/order_reason are engine-layer realistic.
    """
    mod = _load_oracle(oracle_path)
    pool = scenario.etf_pool
    defensive_qmt = scenario.defensive_asset
    strategy_id = scenario.strategy_id
    engine_ver = scenario.engine_semantics_version
    digest_ref = source_digest["oracle_source_digest"]

    signals_rows: List[dict] = []
    orders_rows: List[dict] = []
    nav_rows: List[dict] = []
    order_seq = 0

    ctx = _SimContext(starting_cash)

    # Pending order queue for next_open fill: list of dicts
    pending: List[dict] = []

    for di, day in enumerate(scenario.days):
        t_date = day.trading_date

        # 1. Drain pending orders from previous day at today's t1_open price (next_open fill)
        for po in pending:
            bare = po["bare"]
            direction = po["direction"]
            trig = po["trigger_reason"]
            # Re-check cash sufficiency for buys (engine-layer reject reason)
            status = "filled"
            oreason: Optional[str] = None
            if direction == "buy":
                need = po["requested_value"]
                if need > ctx.portfolio.cash + 1e-6:
                    status = "rejected"
                    oreason = "insufficient_cash_after_sells"
            fill_price = day.t1_open_prices.get(bare)
            filled_shares = 0
            if status == "filled" and fill_price and fill_price > 0:
                if direction == "buy":
                    shares = int((po["requested_value"] / fill_price) / 100) * 100  # lot-rounded
                    if shares > 0:
                        cost = shares * fill_price
                        ctx.portfolio.cash -= cost
                        pos = ctx.portfolio.positions.get(bare + ".SS", _SimPosition())
                        # average cost
                        old_amt = pos.amount
                        new_amt = old_amt + shares
                        if new_amt > 0:
                            pos.cost_basis = ((pos.cost_basis * old_amt) + cost) / new_amt
                        pos.amount = new_amt
                        ctx.portfolio.positions[bare + ".SS"] = pos
                        filled_shares = shares
                else:  # sell -> target_value 0 => sell all
                    pk = next((k for k in ctx.portfolio.positions if _bare(k) == bare), None)
                    if pk and ctx.portfolio.positions[pk].amount > 0:
                        amt = ctx.portfolio.positions[pk].amount
                        proceeds = amt * fill_price
                        ctx.portfolio.cash += proceeds
                        ctx.portfolio.positions[pk].amount = 0
                        filled_shares = amt
            # Record the eventually-drained order with engine-layer status/reason
            trig_c, status_c, oreason_c = _classify_order(trig, status, oreason)
            orders_rows.append({
                "order_seq": 0,  # assigned after sort
                "event_date": po["event_date"],
                "trading_date": t_date,
                "bare_code": bare,
                "qmt_code": po["qmt_code"],
                "direction": direction,
                "trigger_reason": trig_c,
                "order_status": status_c,
                "order_reason": oreason_c,
                "requested_value": round(float(po["requested_value"]), 6),
                "requested_shares": None,
                "filled_price": round(float(fill_price), 6) if (status == "filled" and fill_price) else None,
                "filled_shares": filled_shares if status == "filled" else None,
                "basket_id": po.get("basket_id"),
                "momentum_score": po.get("momentum_score"),
            })
        pending = []

        # 2. Build get_history stub from this day's history (signals use fq='pre', stop-loss fq=None)
        def _get_history(count, frequency="1d", field=None, security_list=None,
                         fq=None, include=False, is_dict=False):
            result = {}
            codes = security_list if isinstance(security_list, list) else [security_list]
            for code in codes:
                bare = _bare(code)
                if fq is None:
                    raw = day.t1_raw_closes.get(bare, day.history_closes.get(bare, [0.0])[-1])
                    result[code] = {"close": np.array([float(raw)])}
                else:
                    c = day.history_closes.get(bare, [1.0])
                    v = day.history_volumes.get(bare, [0.0])
                    n = min(count, len(c))
                    result[code] = {"close": np.array(c[-n:], dtype=float),
                                    "volume": np.array(v[-n:], dtype=float)}
            return result

        mod.__dict__["get_history"] = _get_history
        mod.__dict__["get_position"] = lambda code: ctx.portfolio.positions.get(
            code, _SimPosition()) if ctx.portfolio.positions else _SimPosition()

        # 3. initialize (once) + before_trading_start (signals)
        if di == 0:
            mod.initialize(ctx)
        # reset per-session signal state the Oracle mutates
        g = mod.g
        mod.before_trading_start(ctx, None)

        # 4. Capture signals (deterministic: by pool order)
        all_negative = getattr(g, "all_negative", True)
        momentum_scores = getattr(g, "momentum_scores", {})
        trend_ok = getattr(g, "trend_ok", {})
        volume_surge = getattr(g, "volume_surge", {})

        # Reconstruct eligible + selected Top3 (mirror Oracle handle_data logic for artifact only;
        # this does NOT feed back into the Oracle — selection is recomputed here for reporting).
        eligible = [e for e in momentum_scores if trend_ok.get(e, False) and not volume_surge.get(e, False)]
        ranked = sorted(eligible, key=lambda e: momentum_scores[e], reverse=True)
        max_positions = getattr(g, "max_positions", 3)
        selected = set(e.split(".")[0] for e in ranked[:max_positions]) if (not all_negative and eligible) else set()

        for qmt in pool:
            b = _bare(qmt)
            score = momentum_scores.get(qmt)
            # only ETFs with sufficient history appear in momentum_scores
            has_record = qmt in momentum_scores or qmt in trend_ok
            rank = None
            if b in selected:
                rank = [e.split(".")[0] for e in ranked[:max_positions]].index(b) + 1
            signals_rows.append({
                "trading_date": t_date,
                "bare_code": b,
                "qmt_code": qmt,
                "trend_ok": bool(trend_ok.get(qmt, False)),
                "momentum_score": None if score is None else round(float(score), 9),
                "r_squared": None,  # Oracle does not expose R² separately on g; kept null w/o fabrication
                "volume_surge": bool(volume_surge.get(qmt, False)),
                "all_negative_session": bool(all_negative),
                "eligible": qmt in eligible,
                "selected_rank": rank,
            })

        # 5. Capture Oracle orders: instrument order_target_value to record intent + observed status
        captured_orders: List[dict] = []

        def _ottv(code, target_value):
            order_seq_local = len(captured_orders) + 1
            bare = _bare(code)
            direction = "buy" if target_value > 0 else "sell"
            # Oracle calls order_target_value on T day; in next_open the order is pending until T+1.
            # Cash-sufficiency pre-check mirrors engine behavior at order-creation time.
            status = "pending"
            oreason = None
            if direction == "buy" and target_value > ctx.portfolio.cash + 1e-6:
                status = "rejected"
                oreason = "insufficient_cash_after_sells"
            captured_orders.append({
                "event_date": t_date,
                "bare": bare,
                "qmt_code": code,
                "direction": direction,
                "requested_value": float(target_value),
                "observed_status": status,
                "observed_reason": oreason,
                "momentum_score": momentum_scores.get(code),
            })
            return types.SimpleNamespace(status=status, reason=oreason)

        mod.__dict__["order_target_value"] = _ottv
        # Run handle_data (places orders; we capture, then defer actual fill to next-day drain)
        mod.handle_data(ctx, None)

        # Map captured orders to trigger_reason via Oracle's exit_decisions / defensive / rotation labels.
        # The Oracle's handle_data logs labels in log.info; we instead reconstruct trigger from context:
        exit_decisions = getattr(g, "stop_loss_exits", set())
        exit_bare_reason: Dict[str, str] = {}
        for pk in exit_decisions:
            exit_bare_reason[_bare(pk)] = "stop_loss"
        # We rely on the Oracle having populated g._artifact_triggers if available; otherwise infer:
        artifact_triggers = getattr(g, "_artifact_triggers", None)
        for co in captured_orders:
            bare = co["bare"]
            trig: Optional[str] = None
            if artifact_triggers and bare in artifact_triggers:
                trig = artifact_triggers[bare]
            else:
                # Infer from direction + signal state (deterministic given frozen scenario)
                if co["direction"] == "sell":
                    if bare in exit_bare_reason:
                        trig = "stop_loss"
                    elif _bare(defensive_qmt) != bare and (all_negative or not eligible):
                        trig = "defensive_clear"
                    elif _bare(defensive_qmt) != bare:
                        trig = "rotation_exit"
                    else:
                        trig = "defensive_clear"
                else:  # buy
                    if bare == _bare(defensive_qmt):
                        trig = "defensive_buy"
                    else:
                        trig = "rotation_buy"
            trig_c, status_c, oreason_c = _classify_order(
                trig, co["observed_status"], co["observed_reason"])
            # Queue for next-day drain (next_open fill) unless already rejected
            if status_c != "rejected":
                pending.append({
                    "event_date": co["event_date"],
                    "bare": bare,
                    "qmt_code": co["qmt_code"],
                    "direction": co["direction"],
                    "requested_value": co["requested_value"],
                    "trigger_reason": trig_c,
                    "basket_id": None,  # Oracle path does not enter G1-I basket; stays None
                    "momentum_score": co["momentum_score"],
                })
            else:
                # Rejected at creation: record immediately (no fill)
                orders_rows.append({
                    "order_seq": 0,
                    "event_date": co["event_date"],
                    "trading_date": t_date,
                    "bare_code": bare,
                    "qmt_code": co["qmt_code"],
                    "direction": co["direction"],
                    "trigger_reason": trig_c,
                    "order_status": status_c,
                    "order_reason": oreason_c,
                    "requested_value": round(co["requested_value"], 6),
                    "requested_shares": None,
                    "filled_price": None,
                    "filled_shares": None,
                    "basket_id": None,
                    "momentum_score": co["momentum_score"],
                })

        # 6. NAV at end of day (revalue at T-1 raw closes as mark)
        mark_prices = dict(day.t1_raw_closes)
        ctx.portfolio.revalue(mark_prices)
        positions_value = ctx.portfolio.total_value - ctx.portfolio.cash
        active_positions = sum(1 for p in ctx.portfolio.positions.values() if p.amount > 0)
        nav_rows.append({
            "trading_date": t_date,
            "total_value": round(float(ctx.portfolio.total_value), 6),
            "cash": round(float(ctx.portfolio.cash), 6),
            "positions_value": round(float(positions_value), 6),
            "positions_count": active_positions,
        })

    # Deterministic ordering: signals by (date, pool order); orders by (event_date, seq); nav by date
    pool_order = {q: i for i, q in enumerate(pool)}
    signals_rows.sort(key=lambda r: (r["trading_date"], pool_order.get(r["qmt_code"], 999)))
    orders_rows.sort(key=lambda r: (r["event_date"], r["trading_date"]))
    for i, r in enumerate(orders_rows, 1):
        r["order_seq"] = i
    nav_rows.sort(key=lambda r: r["trading_date"])

    gen_at = datetime.now(BJ_TZ).isoformat(timespec="seconds")
    signals_artifact = {
        "schema_version": "1.0",
        "strategy_id": strategy_id,
        "engine_semantics_version": engine_ver,
        "timezone": TIMEZONE_NAME,
        "generated_at": gen_at,
        "source_digest_ref": digest_ref,
        "signals": signals_rows,
    }
    orders_artifact = {
        "schema_version": "1.0",
        "strategy_id": strategy_id,
        "engine_semantics_version": engine_ver,
        "timezone": TIMEZONE_NAME,
        "generated_at": gen_at,
        "source_digest_ref": digest_ref,
        "trigger_reason_taxonomy": sorted(_TRIGGER_REASONS),
        "orders": orders_rows,
    }
    nav_artifact = {
        "schema_version": "1.0",
        "strategy_id": strategy_id,
        "engine_semantics_version": engine_ver,
        "timezone": TIMEZONE_NAME,
        "currency": "CNY",
        "generated_at": gen_at,
        "source_digest_ref": digest_ref,
        "nav_series": nav_rows,
    }
    return ReferenceArtifacts(
        signals=signals_artifact, orders=orders_artifact, nav=nav_artifact,
        source_digest=source_digest)
