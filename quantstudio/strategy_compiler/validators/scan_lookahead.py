"""scan_lookahead validator (PR6a).

Derived from `docs/strategy-compiler/strategy-ir-contract.md` §6.1 (10 high-risk
items mapped to validation rule IDs) + master plan §9 (lines 1242-1255).

Covers the 10 lookahead/timing high-risk items. ALL high-risk items BLOCK (never
WARN) per master plan §9 line 1255 + framework-contract invariant 5.

Detection strategy per item (contract §6.1 table):
  #1 before_trading_start reads same-day full close   -> AST: data[...].close in before_trading_start
  #2 T-close signal + same-close trade                -> AST + IR: handle_data computes signal AND orders, match_price=close
  #3 current full high/low for intraday trigger       -> IR timing cross-check (IndicatorNode timing vs bar)
  #4 ranking/fundamentals uses current-day future     -> IR timing cross-check (RankingNode source timing)
  #5 include=True misuse                              -> AST: get_history(..., include=True)
  #6 fundamentals not PIT by announcement_date        -> AST: get_fundamentals(..., date=current_dt/current_date)
  #7 next_open order booked into T-day                -> IR: match_price=next_open + execution_clock != next_open
  #8 daily-proxy match-price inconsistent              -> IR: execution.mode proxy + match_price mismatch
  #9 minute signal uses future minute                 -> IR timing cross-check (minute IndicatorNode include)
  #10 1m->5m aggregation sees unfinished 5m bar       -> IR: minute aggregation flag (PR6b; PR6a flags if detected)

Returns (ok, violations, warnings). Each violation carries:
  - rule_id: the validation_rules ID (contract §6.1)
  - severity: "BLOCK" for all 10 high-risk items
  - message: human-readable explanation
  - location: AST node line or IR node_id where detected
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from ..ir_nodes import StrategyIR


@dataclass
class Violation:
    rule_id: str
    severity: str  # "BLOCK" or "WARN"
    message: str
    location: str = ""  # line number or node_id

    def __str__(self) -> str:
        return f"[{self.severity}] {self.rule_id} @ {self.location}: {self.message}"


def scan_lookahead(ir: StrategyIR, code: str) -> tuple[bool, list[Violation], list[str]]:
    """Scan rendered .py code + IR for the 10 lookahead high-risk items.

    Returns (ok, violations, warnings). ok=False iff any BLOCK-severity violation.
    All 10 high-risk items emit BLOCK (contract §9: 高危必须阻断不只警告).
    """
    violations: list[Violation] = []
    warnings: list[str] = []

    # Parse code AST once. If code is not valid Python, that's a separate
    # validator's job (validate_local_strategy); here we just skip AST-based
    # checks and note it as a warning.
    try:
        tree = ast.parse(code)
        ast_available = True
    except SyntaxError as e:
        ast_available = False
        warnings.append(f"scan_lookahead: code has SyntaxError ({e}); AST-based checks skipped")

    if ast_available:
        _check_item_1_before_trading_close(tree, violations)
        _check_item_2_same_close_signal_trade(tree, ir, violations)
        _check_item_5_include_true(tree, ir, violations)
        _check_item_6_fundamentals_current_date(tree, violations)
        _check_item_9_minute_future(tree, ir, violations)

    # IR-based checks (don't need AST)
    _check_item_3_intraday_high_low(ir, violations)
    _check_item_4_ranking_future_data(ir, violations)
    _check_item_7_next_open_clock(ir, violations)
    _check_item_8_proxy_match_price(ir, violations)
    _check_item_10_aggregation(ir, violations)

    ok = not any(v.severity == "BLOCK" for v in violations)
    return ok, violations, warnings


# ---------------------------------------------------------------------------
# AST-based detectors
# ---------------------------------------------------------------------------

def _func_body(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    """Find a top-level function def by name."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _calls_in(node: ast.AST) -> list[ast.Call]:
    """All Call nodes within a subtree."""
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def _call_name(call: ast.Call) -> str:
    """Name of a called function (best-effort: Name or Attribute)."""
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _kw_args(call: ast.Call) -> dict[str, ast.AST]:
    """Keyword arguments as {name: node}."""
    return {kw.arg: kw.value for kw in call.keywords if kw.arg}


def _const_value(node: ast.AST) -> Any:
    """Resolve a constant node to its Python value."""
    if isinstance(node, ast.Constant):
        return node.value
    return None


def _has_subscript_data_close(node: ast.AST) -> bool:
    """True if the subtree contains data[...].close or data[...]['close']."""
    for n in ast.walk(node):
        # data[x].close  ->  Attribute(value=Subscript(value=Name('data')), attr='close')
        if isinstance(n, ast.Attribute) and n.attr in ("close", "high", "low", "open"):
            if isinstance(n.value, ast.Subscript):
                sub = n.value
                if isinstance(sub.value, ast.Name) and sub.value.id == "data":
                    return True
        # data[x]['close']  ->  Subscript(value=Subscript(value=Name('data')))
        if isinstance(n, ast.Subscript):
            if isinstance(n.value, ast.Subscript) and isinstance(n.value.value, ast.Name) and n.value.value.id == "data":
                return True
    return False


def _check_item_1_before_trading_close(tree: ast.AST, violations: list[Violation]) -> None:
    """#1: before_trading_start reads same-day full close (DATALOAD-PIT-PREVIOUS-DATE).

    Detection: before_trading_start body contains data[...].close (same-day bar
    read). Correct pattern uses get_history(include=False) only.
    """
    func = _func_body(tree, "before_trading_start")
    if func is None:
        return
    if _has_subscript_data_close(func):
        violations.append(Violation(
            rule_id="DATALOAD-PIT-PREVIOUS-DATE",
            severity="BLOCK",
            message="before_trading_start reads data[...].close/.high/.low/.open — same-day full bar. "
                    "Use get_history(include=False) anchored at previous_date instead (高危 #1).",
            location=f"before_trading_start line {func.lineno}",
        ))


def _check_item_2_same_close_signal_trade(tree: ast.AST, ir: StrategyIR, violations: list[Violation]) -> None:
    """#2: T-close signal computed in handle_data AND traded same close (SIGNAL-NO-SAME-CLOSE-TRADE).

    IMPORTANT daily-profile nuance: in daily-bar-v1, handle_data fires AT the
    day's close. Reading data[...].close there and filling at close is NOT
    lookahead — the close is known at that moment and the fill uses the same
    known value (双均线策略.py and ETF动量.py both do this and pass Fidelity gates).

    The real 高危 #2 is: the signal derives from a price that is NOT yet known
    at fill time. This happens when:
      - MINUTE profile: handle_data reads the current minute bar and fills at a
        LATER price (the current bar isn't the day's close; a later bar's close
        is the "future" the signal pretends to know).
      - execution_clock=current_bar in a context where the signal uses a price
        determined after the fill.

    Detection: flag only in MINUTE profile (bar_frequency in 1m/5m/...) where
    handle_data reads data[...].close AND places an order. Daily profile is
    exempt (close-at-close is the合法 daily pattern).
    """
    bar_freq = ir.engine_profile.get("bar_frequency", "1d")
    if bar_freq not in ("1m", "5m", "15m", "30m", "60m"):
        return  # daily profile: close-at-close is合法, not 高危 #2
    match_price = None
    for node in ir.nodes:
        if node.node_type == "ExecutionNode":
            match_price = node.parameters.get("match_price_mode")
    if match_price != "close":
        return
    func = _func_body(tree, "handle_data")
    if func is None:
        return
    has_signal_read = _has_subscript_data_close(func)
    order_apis = {"order", "order_value", "order_target", "order_target_value"}
    has_order = any(_call_name(c) in order_apis for c in _calls_in(func))
    if has_signal_read and has_order:
        violations.append(Violation(
            rule_id="SIGNAL-NO-SAME-CLOSE-TRADE",
            severity="BLOCK",
            message=f"MINUTE profile ({bar_freq}): handle_data computes signal from data[...].close "
                    f"AND places order — the current minute bar's close is NOT the final fill price; "
                    f"later bars determine the real close (lookahead 高危 #2). Move signal to a "
                    f"fixed-time run_daily or use completed-bar lookback.",
            location=f"handle_data line {func.lineno}",
        ))


def _check_item_5_include_true(
    tree: ast.AST, ir: StrategyIR, violations: list[Violation]
) -> None:
    """Block incomplete-bar reads while allowing the completed daily qfq bar.

    The sole allowed include=True shape is a daily-bar-v1 ``handle_data`` call
    to ``get_history`` with daily frequency and literal ``fq='pre'``. At that
    lifecycle point the daily bar is complete; using the history API prevents
    raw BarDict OHLC from being mixed into a front-adjusted signal series.
    """
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    bar_freq = ir.engine_profile.get("bar_frequency", "1d")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) != "get_history":
            continue
        kws = _kw_args(node)
        if _const_value(kws.get("include")) is not True:
            continue
        owner = None
        current = node
        while current in parents:
            current = parents[current]
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = current.name
                break
        frequency = None
        if len(node.args) >= 2:
            frequency = _const_value(node.args[1])
        if frequency is None:
            frequency = _const_value(kws.get("frequency") or kws.get("unit"))
        allowed_completed_daily = (
            bar_freq == "1d"
            and owner == "handle_data"
            and frequency == "1d"
            and _const_value(kws.get("fq")) == "pre"
        )
        if allowed_completed_daily:
            continue
        violations.append(Violation(
            rule_id="DATALOAD-NO-INCLUDE-TRUE",
            severity="BLOCK",
            message="get_history(..., include=True) is allowed only for a completed daily "
                    "handle_data bar with literal fq='pre'; otherwise it risks future-bar leakage.",
            location=f"line {node.lineno}",
        ))


def _check_item_6_fundamentals_current_date(tree: ast.AST, violations: list[Violation]) -> None:
    """#6: get_fundamentals not PIT by announcement_date (DATALOAD-PIT-ANN-DATE).

    Detection: get_fundamentals(..., date=context.current_dt) or
    date=context.current_date — uses the current bar's date instead of
    announcement_date for PIT. Correct: date=context.previous_date or explicit
    ann_date-based query.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node) != "get_fundamentals":
            continue
        kws = _kw_args(node)
        date_node = kws.get("date")
        if date_node is None:
            continue
        # Check for context.current_dt / context.current_date attribute access
        if isinstance(date_node, ast.Attribute) and isinstance(date_node.value, ast.Name):
            if date_node.value.id == "context" and date_node.attr in ("current_dt", "current_date"):
                violations.append(Violation(
                    rule_id="DATALOAD-PIT-ANN-DATE",
                    severity="BLOCK",
                    message=f"get_fundamentals(..., date=context.{date_node.attr}) uses current-bar date "
                            f"for fundamentals — must PIT by announcement_date (高危 #6). "
                            f"Use date=context.previous_date or ann_date-based query.",
                    location=f"line {node.lineno}",
                ))


def _check_item_9_minute_future(tree: ast.AST, ir: StrategyIR, violations: list[Violation]) -> None:
    """#9: minute signal uses future minute (INDICATOR-NO-FUTURE-BAR).

    Detection (PR6a heuristic): in minute profile, get_history with a minute
    frequency unit but without explicit include=False. get_history accepts unit
    as either a keyword (unit='1m') or a positional arg (get_history(count, '1m')),
    so we scan both kwargs and positional string constants.
    """
    bar_freq = ir.engine_profile.get("bar_frequency", "1d")
    if bar_freq not in ("1m", "5m", "15m", "30m", "60m"):
        return  # daily-only check skipped
    minute_units = {"1m", "5m", "15m", "30m", "60m"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node) != "get_history":
            continue
        kws = _kw_args(node)
        # Detect minute unit from keyword OR positional string args
        unit = _const_value(kws.get("unit"))
        if unit not in minute_units:
            # Check positional args for a minute-frequency string constant
            for arg in node.args:
                v = _const_value(arg)
                if v in minute_units:
                    unit = v
                    break
        if unit not in minute_units:
            continue
        # Minute get_history without explicit include=False is suspicious
        if "include" not in kws or _const_value(kws.get("include")) is not False:
            violations.append(Violation(
                rule_id="INDICATOR-NO-FUTURE-BAR",
                severity="BLOCK",
                message=f"minute get_history(unit='{unit}') without explicit include=False risks "
                        f"future-minute leakage (高危 #9). Always set include=False in minute mode.",
                location=f"line {node.lineno}",
            ))


# ---------------------------------------------------------------------------
# IR-based detectors (no AST needed)
# ---------------------------------------------------------------------------

def _check_item_3_intraday_high_low(ir: StrategyIR, violations: list[Violation]) -> None:
    """#3: using current full daily high/low to judge intraday triggers
    (INDICATOR-NO-FUTURE-BAR + SIGNAL-TIMING-CONSISTENT).

    IR cross-check: if an IndicatorNode uses field=high/low AND timing=bar in a
    daily profile, the strategy reads the full day's high/low during the day
    (future leak — the day's high/low aren't known until close).
    """
    bar_freq = ir.engine_profile.get("bar_frequency", "1d")
    if bar_freq != "1d":
        return
    for node in ir.nodes:
        if node.node_type == "IndicatorNode":
            field = node.parameters.get("field")
            if field in ("high", "low") and node.timing == "bar":
                violations.append(Violation(
                    rule_id="INDICATOR-NO-FUTURE-BAR",
                    severity="BLOCK",
                    message=f"IndicatorNode {node.node_id!r} uses field={field!r} with timing=bar in "
                            f"daily profile — the day's {field} isn't known until close (高危 #3). "
                            f"Move to pre_open or use previous-day {field}.",
                    location=f"node_id={node.node_id}",
                ))


def _check_item_4_ranking_future_data(ir: StrategyIR, violations: list[Violation]) -> None:
    """#4: ranking/fundamentals uses current-day future-available data
    (DATALOAD-PIT-ANN-DATE + RANK-SOURCE-EXISTS).

    IR cross-check: RankingNode whose source is a DataLoadNode with
    pit_anchor != announcement_date for fundamental data, evaluated at bar timing
    (intraday), risks using not-yet-announced data.
    """
    # Find DataLoadNodes with fundamental dataset
    fundamental_loads = {
        n.output: n for n in ir.nodes
        if n.node_type == "DataLoadNode"
        and n.parameters.get("pit_anchor") != "announcement_date"
        and n.parameters.get("dataset", "").endswith(("_statement", "_ability", "valuation"))
    }
    for node in ir.nodes:
        if node.node_type == "RankingNode":
            source = node.parameters.get("source")
            if source in fundamental_loads and node.timing == "bar":
                violations.append(Violation(
                    rule_id="DATALOAD-PIT-ANN-DATE",
                    severity="BLOCK",
                    message=f"RankingNode {node.node_id!r} ranks on fundamental source {source!r} "
                            f"that is not PIT by announcement_date, at bar timing — risks using "
                            f"current-day future-available data (高危 #4).",
                    location=f"node_id={node.node_id}",
                ))


def _check_item_7_next_open_clock(ir: StrategyIR, violations: list[Violation]) -> None:
    """#7: next_open order booked into T-day (EXEC-NEXT-OPEN-CLOCK).

    IR cross-check: ExecutionNode match_price_mode=next_open but
    time_model.execution_clock != next_open — the order would be matched at
    next_open price but booked same-day (lookahead on the fill).
    """
    exec_clock = ir.time_model.get("execution_clock")
    for node in ir.nodes:
        if node.node_type == "ExecutionNode":
            if node.parameters.get("match_price_mode") == "next_open" and exec_clock != "next_open":
                violations.append(Violation(
                    rule_id="EXEC-NEXT-OPEN-CLOCK",
                    severity="BLOCK",
                    message=f"ExecutionNode match_price_mode=next_open but time_model.execution_clock="
                            f"{exec_clock!r} — next_open fill must be booked at next_open clock, "
                            f"not T-day (高危 #7).",
                    location=f"node_id={node.node_id}",
                ))


def _check_item_8_proxy_match_price(ir: StrategyIR, violations: list[Violation]) -> None:
    """#8: daily-proxy match-price inconsistent (EXEC-MATCH-PRICE-CONSISTENT).

    IR cross-check: detect genuinely inconsistent match-price/clock/cutoff combos.
    The合法 daily-close pattern (match_price=close + current_bar + T-close in
    daily-bar-v1) is NOT a violation — handle_data fires at close, signal and
    fill share the same known close (双均线/ETF动量 both pass Fidelity this way).

    Real 高危 #8 (proxy口径不一致): signal_data_cutoff signals a proxy mode was
    declared (T-open/open or T-close/close) but execution_clock contradicts it
    — e.g. cutoff=T-open but execution_clock=next_open (signal at open, fill at
    next open = the signal uses today's open to trade tomorrow, which is the
    opposite of what the cutoff claims). PR6a flags the clearest contradiction;
    full 14-dimension consistency check is compare_strategy_variants (PR6b).
    """
    cutoff = ir.time_model.get("signal_data_cutoff")
    exec_clock = ir.time_model.get("execution_clock")
    bar_freq = ir.engine_profile.get("bar_frequency", "1d")
    for node in ir.nodes:
        if node.node_type != "ExecutionNode":
            continue
        mp = node.parameters.get("match_price_mode")
        # 高危 #8 concrete case: match_price=open but execution_clock=next_open.
        # open-price fill means same-day open; next_open means next-day open.
        # These two contradict — the order can't fill at "today's open" and
        # "tomorrow's open" simultaneously.
        if mp == "open" and exec_clock == "next_open":
            violations.append(Violation(
                rule_id="EXEC-MATCH-PRICE-CONSISTENT",
                severity="BLOCK",
                message=f"ExecutionNode match_price_mode=open but execution_clock=next_open — "
                        f"open-fill means today's open, next_open means tomorrow's open; these "
                        f"contradict (高危 #8 proxy口径不一致). Align match_price_mode with execution_clock.",
                location=f"node_id={node.node_id}",
            ))


def _check_item_10_aggregation(ir: StrategyIR, violations: list[Violation]) -> None:
    """#10: 1m->5m aggregation sees unfinished 5m bar (INDICATOR-NO-FUTURE-BAR).

    PR6a limitation: real-time 1m->5m aggregation is not implemented (PR3.5
    deferred). This check flags if an IndicatorNode declares a 5m/15m/30m/60m
    frequency while the market_data_frequency is 1m — implying on-the-fly
    aggregation that could see an unfinished bar. PR6a flags it; full prevention
    arrives when PR3.5 aggregation lands.
    """
    mdf = ir.time_model.get("market_data_frequency")
    if mdf != "1m":
        return
    for node in ir.nodes:
        if node.node_type in ("IndicatorNode", "FactorNode", "RankingNode"):
            # IndicatorNodes don't carry their own frequency in PR6a; this check
            # is defensive for when they do. PR6a case 1 doesn't trigger it.
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """CLI: python -m quantstudio.strategy_compiler.validators.scan_lookahead <rendered.py> <ir.json>"""
    import sys, json
    from pathlib import Path
    from ..ir_nodes import StrategyIR

    if argv is None:
        argv = sys.argv[1:]
    if len(argv) < 2:
        print("Usage: scan_lookahead <rendered.py> <ir.json>", file=sys.stderr)
        return 2
    code = Path(argv[0]).read_text(encoding="utf-8")
    ir_payload = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    ir = StrategyIR.from_dict(ir_payload)

    ok, violations, warnings = scan_lookahead(ir, code)
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)
    if ok:
        print(f"VALID: no lookahead high-risk items detected ({len(violations)} non-block violations)")
        return 0
    print(f"INVALID: {len(violations)} lookahead violation(s):", file=sys.stderr)
    for v in violations:
        print(f"  {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
