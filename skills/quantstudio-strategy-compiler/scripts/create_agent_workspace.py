#!/usr/bin/env python3
"""Create an agent-editable strategy workspace from a confirmed design contract."""
from __future__ import annotations

import argparse
from pathlib import Path

from agent_skill_common import confirmation_errors, load_json, validate_design, write_json


def _render_strategy(design: dict) -> str:
    strategy_id = design["strategy_id"]
    hooks = list(dict.fromkeys(["initialize", *design["components"].get("lifecycle_hooks", [])]))
    events = design["timing"].get("decision_events", [])
    scheduled = [event for event in events if event.get("lifecycle") == "run_daily"]
    dual_target = "ptrade" in design.get("targets", [])
    scope_label = "canonical QuantStudio/PTrade" if dual_target else "QuantStudio-only"
    injection_label = (
        "Public PTrade-style APIs, numpy/pandas, g and log are injected locally."
        if dual_target else
        "QuantStudio APIs, registered local extensions, numpy/pandas, g and log are injected locally."
    )
    publish_label = (
        "The validated file is published unchanged to both QuantStudio and PTrade."
        if dual_target else
        "The validated file is published only to the QuantStudio PyQt strategy directory."
    )

    lines = [
        '"""',
        f'{strategy_id}.py - agent-authored {scope_label} strategy.',
        '',
        'This file is intentionally a lifecycle/API scaffold, not a strategy template.',
        'Implement strategy-specific universe, indicators, signals, state and risk below.',
        injection_label,
        publish_label,
        '"""',
        '',
        f"STRATEGY_ID = {strategy_id!r}",
        f"DESIGN_VERSION = {design['design_version']!r}",
        '',
        '',
        'def _ensure_runtime_state():',
        '    """Idempotently create every g field used by any callback."""',
        '    # AGENT_IMPLEMENTATION_REQUIRED: use hasattr checks; never reset existing state.',
        '    pass',
        '',
        '',
        'def initialize(context):',
        '    """Configure parameters, costs, universe and scheduled callbacks."""',
        '    _ensure_runtime_state()',
    ]
    for event in scheduled:
        event_time = event.get("time")
        if event_time:
            lines.append(f"    run_daily(context, {event['name']}, time={event_time!r})")
        else:
            lines.append(f"    # Schedule {event['name']} after choosing an explicit supported time.")
    lines.extend([
        '    # AGENT_IMPLEMENTATION_REQUIRED: initialize strategy parameters and state.',
        '    pass',
        '',
    ])

    signatures = {
        "before_trading_start": "context, data",
        "handle_data": "context, data",
        "after_trading_end": "context, data",
    }
    purposes = {
        "before_trading_start": "Build the PIT universe and prepare factors without same-day future data.",
        "handle_data": "Evaluate bar-dependent logic for the declared engine profile.",
        "after_trading_end": "Record diagnostics and reconcile persistent state after the close.",
    }
    for hook in hooks:
        if hook == "initialize":
            continue
        lines.extend([
            '',
            f'def {hook}({signatures[hook]}):',
            f'    """{purposes[hook]}"""',
            '    _ensure_runtime_state()',
            f'    # AGENT_IMPLEMENTATION_REQUIRED: implement {hook} from the confirmed design.',
            '    pass',
        ])

    for event in scheduled:
        lines.extend([
            '',
            '',
            f"def {event['name']}(context):",
            f"    \"\"\"Scheduled component: {event.get('purpose', event['name'])}.\"\"\"",
            '    _ensure_runtime_state()',
            '    # AGENT_IMPLEMENTATION_REQUIRED: implement this scheduled decision component.',
            '    pass',
        ])

    lines.extend([
        '',
        '',
        '# Add strategy-specific helper functions below. Keep all data/order access behind',
        '# the injected public APIs listed in COMPONENT_PLAN.md.',
        "# Signal-price history calls must use literal fq='pre'; raw prices are execution-only.",
        '',
    ])
    return "\n".join(lines)


def _render_plan(design: dict) -> str:
    sem = design["strategy_semantics"]
    lines = [
        f"# Component Plan: {design['strategy_name']}",
        '',
        f"- Strategy ID: `{design['strategy_id']}`",
        f"- Targets: {', '.join(design['targets'])}",
        f"- Universe mode: `{design.get('universe_contract', {}).get('mode', 'legacy_dual')}`",
        f"- Backtest validation owner: `{design.get('validation_execution', {}).get('mode', 'agent_managed')}`",
        f"- Engine profile: `{design['engine_profile']['profile_id']}` / `{design['engine_profile']['bar_frequency']}`",
        f"- Match price: `{design['engine_profile']['match_price_mode']}`",
        f"- Signal cutoff: `{design['timing']['signal_data_cutoff']}`",
        f"- Signal price adjustment: `{design['market_data_contract']['signal_price_adjustment']}` (`fq='pre'` required)",
        f"- Execution price basis: `{design['market_data_contract']['execution_price_basis']}`",
        '',
        '## Confirmed semantics',
        '',
        f"- Universe: {sem['universe']}",
        f"- Holding: {design['timing']['holding_semantics']}",
        '- Entry rules:',
        *[f"  - {item}" for item in sem.get('entry_rules', [])],
        '- Exit rules:',
        *[f"  - {item}" for item in sem.get('exit_rules', [])],
        '- Portfolio rules:',
        *[f"  - {item}" for item in sem.get('portfolio_rules', [])],
        '- Risk rules:',
        *[f"  - {item}" for item in sem.get('risk_rules', [])],
        '',
        '## Selected components',
        '',
        f"- Lifecycle hooks: {', '.join(design['components'].get('lifecycle_hooks', []))}",
        f"- API groups: {', '.join(design['components'].get('api_groups', []))}",
        f"- Required APIs: {', '.join(design['components'].get('required_apis', []))}",
        '',
        '## Hard constraints',
        '',
        *[f"- {item}" for item in design['constraints'].get('hard_filters', [])],
        '- No direct DuckDB/provider/file access.',
        '- Backtest dates are never hardcoded in strategy source; use the confirmed window contract.',
        ('- User-PyQt mode: publish only a __candidate file after R4; formal targets remain blocked until hash-bound R5 evidence PASS.'
         if design.get("validation_execution", {}).get("mode") == "user_pyqt" else
         '- Agent-managed mode: R5 is executed by the agent only with the customer-confirmed backtest window.'),
        ('- Dual target: no local-only API; ETF universes use the confirmed static whitelist.'
         if "ptrade" in design.get("targets", []) else
         '- QuantStudio-only target: registered local APIs are allowed; PTrade portability must not be claimed.'),
        "- Every signal-price get_history/get_history_batch/get_price call uses literal fq='pre'.",
        '- attribute_history is forbidden because its price adjustment cannot be proven.',
        '- Raw bar/snapshot OHLC is execution-only and must not enter indicator or signal series.',
        '- No strategy-pattern branch may be added to Compiler or templates.',
        '',
        '## Calling-agent implementation notes',
        '',
        *[f"- {item}" for item in design['components'].get('implementation_notes', [])],
        '',
    ]
    return "\n".join(lines)


def create_workspace(design_path: Path, out_dir: Path, overwrite: bool = False) -> Path:
    design = load_json(design_path)
    problems = validate_design(design) + confirmation_errors(design)
    if problems:
        raise ValueError("design gate failed: " + "; ".join(problems))
    if out_dir.exists() and any(out_dir.iterdir()) and not overwrite:
        raise FileExistsError(f"workspace is not empty: {out_dir}; use --overwrite explicitly")
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "agent_strategy_design.json", design)
    (out_dir / "strategy.py").write_text(_render_strategy(design), encoding="utf-8")
    (out_dir / "COMPONENT_PLAN.md").write_text(_render_plan(design), encoding="utf-8")
    write_json(out_dir / "workspace_state.json", {
        "strategy_id": design["strategy_id"],
        "stage": "SCAFFOLDED",
        "targets": design.get("targets", []),
        "universe_mode": design.get("universe_contract", {}).get("mode", "legacy_dual"),
        "validation_execution_mode": design.get("validation_execution", {}).get("mode", "agent_managed"),
        "candidate_status": "NOT_GENERATED",
        "backtest_evidence_status": "NOT_APPLICABLE" if design.get("validation_execution", {}).get("mode") != "user_pyqt" else "PENDING",
        "formal_publish_allowed": False,
        "backtest_window_contract": design.get("backtest_window_contract", {}),
        "agent_implementation_required": True,
        "canonical_source": "strategy.py",
        "quantstudio_output": (
            f"quantstudio/backtest/strategies/{design['strategy_id']}_quantstudio.py"
            if "quantstudio" in design.get("targets", []) else "NOT_GENERATED"
        ),
        "ptrade_output": (
            f"ptrade/{design['strategy_id']}_ptrade.py"
            if "ptrade" in design.get("targets", []) else "NOT_GENERATED"
        ),
    })
    return out_dir


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create an agent-first QuantStudio strategy workspace")
    parser.add_argument("design", help="Confirmed agent_strategy_design.json")
    parser.add_argument("--out", required=True, help="Workspace output directory")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        path = create_workspace(Path(args.design), Path(args.out), args.overwrite)
    except Exception as exc:
        print(f"BLOCKED: {exc}")
        return 2
    print(f"CREATED: {path}")
    print(f"Agent must now implement: {path / 'strategy.py'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
