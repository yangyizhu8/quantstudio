"""Render Strategy IR to dual-platform .py (PR6a).

Derived from `docs/strategy-compiler/strategy-ir-contract.md` §5 (platform_mapping)
+ `ptrade-profile-contract.md` (差异层: batch API / header / suffix).

Single render core: QuantStudio and PTrade share the same injected API
(ptrade_import.py), so lifecycle function bodies are identical across profiles.
The only differences (contract §5) are 3 points handled by the Profile diff layer:
  1. Batch APIs (get_fundamentals_batch / get_history_batch): QS template may emit;
     PTrade template MUST NOT (ptrade-profile-contract.md).
  2. Header: PTrade output top declares Profile version.
  3. Suffix: QS -> <id>_quantstudio.py; PTrade -> <id>_ptrade.py.

Golden protection (contract §6 PORTFOLIO-POSITIONS-EXACT-MATCH + handoff §11):
render() raises if strategy_id matches a protected ID. IDs come from TWO sources
(plan §3.6): dynamic (config/strategy_fidelity_gates.json gates keys) + hardcoded
fallback. Either source hitting is enough to block.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jinja2

from quantstudio.backtest.libs.security_code_rules import normalize_to_ptrade, normalize_to_qmt

from .ir_nodes import StrategyIR

# Contract §3.6: hardcoded fallback (config 不可用时兜底)
_HARDCODED_PROTECTED: set[str] = {
    "etf_momentum",
    "smallcap_guard",
    "dual_ma_sample",
}

# Templates: prefer package-bundled (installed wheel works without source tree);
# fall back to the source-tree skills/ location for dev.
_PKG_TEMPLATES = Path(__file__).resolve().parent / "templates"
_SKILL_TEMPLATES = (
    Path(__file__).resolve().parents[2]
    / "skills" / "quantstudio-strategy-compiler" / "templates"
)
_TEMPLATES_DIR = _PKG_TEMPLATES if _PKG_TEMPLATES.is_dir() else _SKILL_TEMPLATES

# Profile -> (template filename prefix, output suffix)
_PROFILE_TEMPLATE_MAP: dict[str, dict[str, str]] = {
    "quantstudio": {"prefix": "quantstudio", "suffix": "_quantstudio.py"},
    "ptrade-default": {"prefix": "ptrade", "suffix": "_ptrade.py"},
}


class GoldenProtectionError(ValueError):
    """Raised when render() is called on a golden protected strategy_id.

    Protected strategies (etf_momentum / smallcap_guard / dual-MA sample) carry
    frozen Fidelity baselines; silent regeneration would break them. Any change
    requires the golden-baseline change protocol (handoff §11), not auto-render.
    """


def _load_protected_ids(config_path: str | Path | None = None) -> set[str]:
    """Load protected strategy IDs from two sources (contract §3.6 双源).

    1. Dynamic: config/strategy_fidelity_gates.json gates keys (preferred when
       available — stays in sync as gates are added).
    2. Hardcoded fallback: _HARDCODED_PROTECTED (used when config missing/corrupt).

    Union of both — either source hitting blocks render.
    """
    ids = set(_HARDCODED_PROTECTED)
    path = Path(config_path) if config_path else Path("config/strategy_fidelity_gates.json")
    try:
        cfg = json.loads(path.read_text(encoding="utf-8-sig"))
        gates = cfg.get("gates", {})
        if isinstance(gates, dict):
            ids.update(gates.keys())
        # else: malformed config; fall through to hardcoded only
    except (FileNotFoundError, json.JSONDecodeError):
        # Config unavailable — hardcoded fallback covers the known golden set
        pass
    return ids


def _assert_not_protected(strategy_id: str, config_path: str | Path | None = None) -> None:
    """Raise GoldenProtectionError if strategy_id is protected (双源)."""
    protected = _load_protected_ids(config_path)
    if strategy_id in protected:
        raise GoldenProtectionError(
            f"strategy_id {strategy_id!r} is golden-protected (source: "
            f"config/strategy_fidelity_gates.json gates + hardcoded fallback). "
            f"Silent regeneration forbidden — golden-baseline change protocol "
            f"required (handoff §11). To re-render, rename the strategy_id."
        )


def _bar_frequency(ir: StrategyIR) -> str:
    """Extract bar_frequency from IR engine_profile (1d for daily, 1m/5m/... for minute)."""
    freq = ir.engine_profile.get("bar_frequency", "1d")
    return freq or "1d"


def _select_template_name(profile: str, bar_frequency: str) -> str:
    """Pick template: {prefix}_{daily|minute}.py.j2 based on profile + frequency."""
    if profile not in _PROFILE_TEMPLATE_MAP:
        raise ValueError(
            f"Unknown profile {profile!r}; expected one of {list(_PROFILE_TEMPLATE_MAP)}"
        )
    prefix = _PROFILE_TEMPLATE_MAP[profile]["prefix"]
    mode = "minute" if bar_frequency in ("1m", "5m", "15m", "30m", "60m") else "daily"
    return f"{prefix}_{mode}.py.j2"


def _build_template_context(ir: StrategyIR, profile: str) -> dict[str, Any]:
    """Flatten IR into a Jinja2-friendly context dict.

    The renderer walks the IR nodes and extracts the concrete values templates
    need (security code, ma lookbacks, signal direction, execution params, etc.).
    This is the Spec->IR->render load-bearing step: if templates ignored IR and
    emitted a fixed skeleton, the IR承重 assertions in tests would catch it.
    """
    ctx: dict[str, Any] = {
        "strategy_id": ir.strategy_id,
        "profile": profile,
        "is_ptrade": profile == "ptrade-default",
        "bar_frequency": _bar_frequency(ir),
        "is_minute": _bar_frequency(ir) in ("1m", "5m", "15m", "30m", "60m"),
        "contract_versions": ir.contract_versions,
        "engine_profile": ir.engine_profile,
        "time_model": ir.time_model,
        "metadata": ir.metadata,
        "benchmark": ir.metadata.get("benchmark") or "000300.XSHG",
        "initial_capital": ir.metadata.get("initial_capital") or 100000.0,
    }

    normalize_code = normalize_to_ptrade if profile == "ptrade-default" else normalize_to_qmt
    if ctx.get("benchmark"):
        ctx["benchmark"] = normalize_code(ctx["benchmark"])

    cross_count = 0

    # Walk nodes by type and extract concrete params.
    for node in ir.nodes:
        nt = node.node_type
        p = node.parameters
        if nt == "UniverseNode":
            ctx["universe_kind"] = p.get("kind")
            code = p.get("code")
            index = p.get("index")
            codes = p.get("codes")
            ctx["security_code"] = normalize_code(code) if code else None
            ctx["universe_index"] = normalize_code(index) if index else None
            ctx["universe_codes"] = [normalize_code(item) for item in codes] if codes else None
            ctx["universe_parameters"] = dict(p)
            if p.get("kind") == "smallest_market_cap":
                ctx["market_cap_pool_size"] = p.get("pool_size", 500)
                ctx["market_cap_field"] = p.get("field", "circulating_market_cap")
                ctx["min_listing_trade_days"] = p.get("min_listing_trade_days", 252)
                ctx["recent_suspension_days"] = p.get("recent_suspension_days", 5)
                ctx["exclude_chinext"] = p.get("exclude_chinext", True)
        elif nt == "DataLoadNode":
            ctx["dataload_dataset"] = p.get("dataset")
            ctx["dataload_frequency"] = p.get("frequency")
            ctx["dataload_fields"] = p.get("fields", ["close"])
            ctx["dataload_lookback"] = p.get("lookback", 20)
            ctx["dataload_pit_anchor"] = p.get("pit_anchor", "previous_date")
        elif nt == "IndicatorNode":
            op = p.get("operation")
            if op == "ma":
                # Collect all ma indicators (multiple allowed: ma5, ma10, ...).
                ctx.setdefault("ma_indicators", []).append({
                    "id": node.output,
                    "field": p.get("field", "close"),
                    "lookback": p.get("lookback"),
                })
            elif op == "pct_change":
                ctx.setdefault("pct_change_indicators", []).append({
                    "id": node.output,
                    "field": p.get("field", "close"),
                    "lookback": p.get("lookback"),
                })
            elif op == "ema":
                ctx.setdefault("ema_indicators", []).append({
                    "id": node.output,
                    "field": p.get("field", "close"),
                    "lookback": p.get("lookback"),
                })
            elif op == "rolling_amplitude":
                ctx.setdefault("amplitude_indicators", []).append({
                    "id": node.output,
                    "lookback": p.get("lookback"),
                    "high_field": p.get("high_field", "high"),
                    "low_field": p.get("low_field", "low"),
                })
        elif nt == "RankingNode":
            ctx["ranking_op"] = p.get("operation")
            ctx["ranking_source"] = p.get("source")
            ctx["ranking_ascending"] = p.get("ascending", False)
            ctx["ranking_top_n"] = p.get("top_n")
            ctx["ranking_output"] = node.output
        elif nt == "SignalNode" and p.get("operation") == "cross":
            cross_count += 1
            if cross_count > 1:
                raise ValueError("multiple cross SignalNodes are not supported")
            ctx["signal_op"] = "cross"
            ctx["signal_sources"] = p.get("sources", [])
            ctx["signal_direction"] = p.get("direction", "golden")
        elif nt == "SignalNode":
            ctx.setdefault("signal_steps", []).append({"id": node.output, **dict(p)})
        elif nt == "PortfolioNode":
            ctx["portfolio_kind"] = p.get("kind")
            ctx["portfolio_max_positions"] = p.get("max_positions", 1)
            ctx["portfolio_rebalance"] = p.get("rebalance", "signal_triggered")
            ctx["portfolio_target_weight"] = p.get("target_weight", 1.0)
            ctx["portfolio_parameters"] = dict(p)
        elif nt == "RiskNode":
            ctx["risk_kind"] = p.get("kind")
            ctx["risk_max_single_weight"] = p.get("max_single_weight", 1.0)
            ctx["risk_cash_buffer"] = p.get("cash_buffer", 0.0)
            ctx["risk_parameters"] = dict(p)
        elif nt == "ExecutionNode":
            ctx["exec_order_api"] = p.get("order_api", "order_value")
            ctx["exec_match_price_mode"] = p.get("match_price_mode", "close")
            ctx["exec_order_type"] = p.get("order_type", "market")

    match_price_mode = ctx.get("exec_match_price_mode", "close")
    ctx["ptrade_rebalance_time"] = (
        "9:31" if match_price_mode in ("next_open", "open") else "15:00"
    )
    ctx["ptrade_requires_minute_schedule"] = match_price_mode in ("next_open", "open")

    universe_kind = ctx.get("universe_kind")
    if universe_kind == "smallest_market_cap":
        ema_items = ctx.get("ema_indicators", [])
        amp_items = ctx.get("amplitude_indicators", [])
        signal_ops = {item.get("operation") for item in ctx.get("signal_steps", [])}
        if len(ema_items) != 1 or len(amp_items) != 1 or not {
            "compare", "open_below_previous_low", "and"
        }.issubset(signal_ops):
            raise ValueError(
                "smallest_market_cap renderer requires one EMA, one rolling_amplitude, "
                "compare + open_below_previous_low + and signals"
            )
        ctx["strategy_pattern"] = "smallcap_overnight_scalp"
        ctx["ema_indicator"] = ema_items[0]
        ctx["amplitude_indicator"] = amp_items[0]
        ctx["entry_time"] = ir.time_model.get("entry_clock", "9:31")
        ctx["exit_time"] = ir.time_model.get("exit_clock", "10:30")
        ctx["exit_day_offset"] = ir.time_model.get("exit_day_offset", 1)
        ctx["max_concurrent_positions"] = ir.time_model.get("max_concurrent_positions", 14)
        ctx["new_buy_cash_policy"] = ir.time_model.get("new_buy_cash_policy", "available_cash_only")
        ctx["buy_count"] = ctx.get("portfolio_parameters", {}).get("max_positions", 7)
        ctx["cash_buffer"] = ctx.get("risk_parameters", {}).get("cash_buffer", 0.02)
        ctx["amplitude_threshold"] = ctx.get("risk_parameters", {}).get("amplitude_threshold", 0.10)
    if universe_kind == "single_stock" and ctx.get("signal_op") != "cross":
        raise ValueError("single_stock rendering requires a supported dual-MA cross signal")

    pct_by_id = {item["id"]: item for item in ctx.get("pct_change_indicators", [])}
    if universe_kind == "manual_list":
        ranking_source = ctx.get("ranking_source")
        if ranking_source not in pct_by_id:
            raise ValueError(
                f"unsupported manual_list ranking source {ranking_source!r}; expected pct_change indicator"
            )
        ctx["ranking_indicator"] = pct_by_id[ranking_source]

    # Resolve cross operands strictly from the IR-declared sources. The currently
    # supported renderer semantics are ma_fast > ma_slow (golden direction).
    if ctx.get("signal_op") == "cross":
        ma_by_id = {item["id"]: item for item in ctx.get("ma_indicators", [])}
        sources = ctx.get("signal_sources", [])
        if (
            ctx.get("signal_direction") != "golden"
            or len(sources) != 2
            or any(source not in ma_by_id for source in sources)
        ):
            raise ValueError(
                "unsupported cross combination: expected two MA sources with direction='golden'"
            )
        first, second = (ma_by_id[source] for source in sources)
        if first["lookback"] >= second["lookback"]:
            raise ValueError(
                "unsupported cross combination: first MA source must be faster than second"
            )
        ctx["ma_fast"] = first
        ctx["ma_slow"] = second

    return ctx


# Jinja2 environment (single, shared). autoescape=False (we emit Python source).
_jinja_env: jinja2.Environment | None = None


def _get_jinja_env() -> jinja2.Environment:
    global _jinja_env
    if _jinja_env is None:
        if not _TEMPLATES_DIR.is_dir():
            raise FileNotFoundError(
                f"Templates directory not found: {_TEMPLATES_DIR}. "
                f"PR6a requires skills/quantstudio-strategy-compiler/templates/."
            )
        _jinja_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=False,
            keep_trailing_newline=True,
            trim_blocks=False,
            lstrip_blocks=False,
        )
    return _jinja_env


def render_strategy(
    ir: StrategyIR,
    profile: str,
    config_path: str | Path | None = None,
) -> str:
    """Render IR to a .py source string for the given profile.

    profile ∈ {"quantstudio", "ptrade-default"}. Raises GoldenProtectionError
    if ir.strategy_id is golden-protected (双源: config + hardcoded).
    """
    _assert_not_protected(ir.strategy_id, config_path)
    template_name = _select_template_name(profile, _bar_frequency(ir))
    ctx = _build_template_context(ir, profile)
    template = _get_jinja_env().get_template(template_name)
    return template.render(**ctx)


def render_quantstudio(
    ir: StrategyIR, config_path: str | Path | None = None
) -> str:
    """Render IR to QuantStudio .py (profile=quantstudio)."""
    return render_strategy(ir, "quantstudio", config_path)


def render_ptrade(
    ir: StrategyIR, config_path: str | Path | None = None
) -> str:
    """Render IR to PTrade .py (profile=ptrade-default).

    PTrade output MUST NOT contain batch APIs (get_fundamentals_batch /
    get_history_batch) — enforced by template (ptrade-profile-contract.md).
    """
    return render_strategy(ir, "ptrade-default", config_path)


def output_filename(strategy_id: str, profile: str) -> str:
    """Return the canonical output filename for a rendered strategy."""
    if profile not in _PROFILE_TEMPLATE_MAP:
        raise ValueError(f"Unknown profile {profile!r}")
    return f"{strategy_id}{_PROFILE_TEMPLATE_MAP[profile]['suffix']}"
