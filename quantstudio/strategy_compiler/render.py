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
    }

    # Walk nodes by type and extract concrete params.
    for node in ir.nodes:
        nt = node.node_type
        p = node.parameters
        if nt == "UniverseNode":
            ctx["universe_kind"] = p.get("kind")
            ctx["security_code"] = p.get("code")  # single_stock
            ctx["universe_index"] = p.get("index")  # index_constituents
            ctx["universe_codes"] = p.get("codes")  # manual_list/etf_list
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
        elif nt == "RankingNode":
            ctx["ranking_op"] = p.get("operation")
            ctx["ranking_source"] = p.get("source")
            ctx["ranking_ascending"] = p.get("ascending", False)
            ctx["ranking_top_n"] = p.get("top_n")
            ctx["ranking_output"] = node.output
        elif nt == "SignalNode" and p.get("operation") == "cross":
            ctx["signal_op"] = "cross"
            ctx["signal_sources"] = p.get("sources", [])
            ctx["signal_direction"] = p.get("direction", "golden")
        elif nt == "PortfolioNode":
            ctx["portfolio_kind"] = p.get("kind")
            ctx["portfolio_max_positions"] = p.get("max_positions", 1)
            ctx["portfolio_rebalance"] = p.get("rebalance", "signal_triggered")
            ctx["portfolio_target_weight"] = p.get("target_weight", 1.0)
        elif nt == "RiskNode":
            ctx["risk_kind"] = p.get("kind")
            ctx["risk_max_single_weight"] = p.get("max_single_weight", 1.0)
            ctx["risk_cash_buffer"] = p.get("cash_buffer", 0.0)
        elif nt == "ExecutionNode":
            ctx["exec_order_api"] = p.get("order_api", "order_value")
            ctx["exec_match_price_mode"] = p.get("match_price_mode", "close")
            ctx["exec_order_type"] = p.get("order_type", "market")

    # Determine the "fast" and "slow" ma for cross signals (golden/death).
    ma_list = ctx.get("ma_indicators", [])
    if len(ma_list) >= 2 and ctx.get("signal_op") == "cross":
        # Sort by lookback ascending: fast = shortest, slow = longest.
        sorted_ma = sorted(ma_list, key=lambda m: m["lookback"])
        ctx["ma_fast"] = sorted_ma[0]
        ctx["ma_slow"] = sorted_ma[-1]

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
