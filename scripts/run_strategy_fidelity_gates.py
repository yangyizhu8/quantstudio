"""Run and validate cross-stage QuantStudio strategy-fidelity gates.

This command is intentionally separate from pytest: ordinary unit tests cannot
catch every strategy control-flow drift. The gate reruns approved strategies
against real PTrade exports and validates both Fidelity L1-L4 and local golden
results.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_CONFIG = ROOT / "config" / "strategy_fidelity_gates.json"
DEFAULT_OUTPUT = ROOT / "output" / "strategy-fidelity-gates"


class FidelityGateError(RuntimeError):
    """Raised when an approved strategy baseline drifts."""


@dataclass
class GateOutcome:
    name: str
    passed: bool
    verdict: str
    compare_exit_code: int
    report_path: str
    result_dir: str
    checks: list[str]
    failures: list[str]


def load_gate_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("gate_version") != "1.0.0":
        raise FidelityGateError("unsupported fidelity gate version")
    if not data.get("gates"):
        raise FidelityGateError("no strategy fidelity gates configured")
    return data


def _metric_value(report: Mapping[str, Any], layer: str) -> Any:
    try:
        return report["metrics"][layer]["value"]
    except (KeyError, TypeError) as exc:
        raise FidelityGateError(f"missing {layer} value in fidelity report") from exc


def validate_fidelity_report(
    report: Mapping[str, Any], gate: Mapping[str, Any]
) -> tuple[list[str], list[str]]:
    """Validate verdict and L1-L4 against a frozen gate profile."""
    checks: list[str] = []
    failures: list[str] = []
    verdict = str(report.get("verdict", ""))
    if verdict not in gate["accepted_verdicts"]:
        failures.append(
            f"verdict {verdict!r} not in {gate['accepted_verdicts']}"
        )
    else:
        checks.append(f"verdict={verdict}")

    limits = gate["fidelity"]
    l1 = float(_metric_value(report, "L1"))
    l2 = _metric_value(report, "L2")
    l3 = float(_metric_value(report, "L3"))
    l4 = float(_metric_value(report, "L4"))

    comparisons = [
        ("L1", l1 >= limits["min_l1"], l1, f">={limits['min_l1']}"),
        ("L2.nav", float(l2["nav_deviation"]) <= limits["max_nav_deviation"],
         float(l2["nav_deviation"]), f"<={limits['max_nav_deviation']}"),
        ("L2.drawdown", float(l2["drawdown_deviation"]) <= limits["max_drawdown_deviation"],
         float(l2["drawdown_deviation"]), f"<={limits['max_drawdown_deviation']}"),
        ("L2.sharpe", float(l2["sharpe_deviation"]) <= limits["max_sharpe_deviation"],
         float(l2["sharpe_deviation"]), f"<={limits['max_sharpe_deviation']}"),
        ("L3", l3 >= limits["min_l3"], l3, f">={limits['min_l3']}"),
        ("L4", l4 <= limits["max_l4"], l4, f"<={limits['max_l4']}"),
    ]
    for label, ok, value, expected in comparisons:
        message = f"{label}={value} expected {expected}"
        (checks if ok else failures).append(message)
    return checks, failures


def _bare(code: Any) -> str:
    return str(code).split(".", 1)[0]


def validate_local_result(
    result_dir: Path, gate: Mapping[str, Any]
) -> tuple[list[str], list[str]]:
    """Validate local final asset and transaction sequence/count."""
    checks: list[str] = []
    failures: list[str] = []
    expected = gate["local_result"]
    daily_path = result_dir / "daily_stats.csv"
    trades_path = result_dir / "trades.csv"
    if not daily_path.is_file():
        return checks, [f"missing {daily_path}"]
    daily = pd.read_csv(daily_path)
    final_asset = float(daily.iloc[-1]["total_asset"])
    target = float(expected["expected_final_asset"])
    tolerance = float(expected["final_asset_tolerance"])
    if abs(final_asset - target) <= tolerance:
        checks.append(f"final_asset={final_asset:.6f}")
    else:
        failures.append(
            f"final_asset={final_asset:.6f}, expected {target:.6f} ± {tolerance}"
        )

    trades = pd.read_csv(trades_path) if trades_path.is_file() else pd.DataFrame()
    trade_count = len(trades)
    expected_count = int(expected["expected_trade_count"])
    if trade_count == expected_count:
        checks.append(f"trade_count={trade_count}")
    else:
        failures.append(f"trade_count={trade_count}, expected {expected_count}")

    expected_sequence = expected.get("expected_trade_sequence")
    if expected_sequence is not None:
        actual = [
            [str(row["datetime"])[:10], _bare(row["code"]), str(row["action"])]
            for _, row in trades.iterrows()
        ]
        if actual == expected_sequence:
            checks.append("trade_sequence=exact")
        else:
            failures.append(
                f"trade_sequence drifted: actual={actual}, expected={expected_sequence}"
            )

    expected_last = expected.get("expected_last_bought_code")
    if expected_last is not None:
        buys = trades[trades["action"] == "buy"] if not trades.empty else trades
        actual_last = _bare(buys.iloc[-1]["code"]) if len(buys) else None
        if actual_last == expected_last:
            checks.append(f"last_bought_code={actual_last}")
        else:
            failures.append(
                f"last_bought_code={actual_last}, expected {expected_last}"
            )
    return checks, failures


def _newest_result_dir(strategy_stem: str, started_at: float) -> Path:
    candidates = [
        path for path in (ROOT / "output" / "backtest_results").glob(
            f"*_{strategy_stem}"
        )
        if path.is_dir() and path.stat().st_mtime >= started_at - 2
    ]
    if not candidates:
        raise FidelityGateError(f"no new result directory for {strategy_stem}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def run_gate(
    name: str,
    gate: Mapping[str, Any],
    output_dir: Path,
) -> GateOutcome:
    output_dir.mkdir(parents=True, exist_ok=True)
    strategy = ROOT / gate["strategy"]
    samples = (ROOT / gate["ptrade_samples"]).resolve()
    report_path = output_dir / f"{name}.json"
    log_path = output_dir / f"{name}.log"
    if not strategy.is_file():
        raise FidelityGateError(f"missing strategy: {strategy}")
    if not samples.is_dir():
        raise FidelityGateError(f"missing PTrade samples: {samples}")

    command = [
        sys.executable, "-m", "quantstudio.backtest.run_ptrade_strategy",
        str(strategy), gate["start"], gate["end"],
        "--match-price", gate["match_price_mode"],
        "--slippage", str(gate["slippage"]),
        "--compare", "--ptrade-dir", str(samples),
        "--output", str(report_path),
    ]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    started_at = datetime.now().timestamp()
    with log_path.open("w", encoding="utf-8", newline="\n") as log_file:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            check=False,
        )
    accepted_exit_codes = set(gate["expected_compare_exit_codes"])
    command_failure = completed.returncode not in accepted_exit_codes

    if not report_path.is_file():
        raise FidelityGateError(
            f"gate {name} produced no report; see {log_path}"
        )
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    result_dir = _newest_result_dir(strategy.stem, started_at)
    checks, failures = validate_fidelity_report(report, gate)
    local_checks, local_failures = validate_local_result(result_dir, gate)
    checks.extend(local_checks)
    failures.extend(local_failures)
    if command_failure:
        failures.append(
            f"compare exit code {completed.returncode}, accepted {sorted(accepted_exit_codes)}"
        )
    else:
        checks.append(f"compare_exit_code={completed.returncode}")

    return GateOutcome(
        name=name,
        passed=not failures,
        verdict=str(report.get("verdict", "")),
        compare_exit_code=completed.returncode,
        report_path=str(report_path),
        result_dir=str(result_dir),
        checks=checks,
        failures=failures,
    )


def validate_semantics(config: Mapping[str, Any]) -> list[str]:
    """Fail immediately if the public portfolio container contract drifted."""
    from quantstudio.backtest.backtest_engine import (
        Account, BacktestEngine, Position as EnginePosition,
    )
    from quantstudio.backtest.ptrade_api import Portfolio, PtradeAPI

    engine = object.__new__(BacktestEngine)
    engine.account = Account(cash=10.0, positions={
        "159870.SZ": EnginePosition(
            "159870.SZ", volume=100, avg_cost=0.86, can_sell=100
        )
    })
    positions = engine._get_ptrade_positions({"159870.SZ": 0.85})
    portfolio = Portfolio(10.0, positions)
    failures = []
    expected_type = config["semantics"]["portfolio_positions_container"]
    actual_type = f"{type(portfolio.positions).__module__}.{type(portfolio.positions).__name__}"
    if actual_type != expected_type:
        failures.append(f"portfolio container {actual_type}, expected {expected_type}")
    if "159870.XSHE" in portfolio.positions:
        failures.append("portfolio membership became alias-aware")
    if "159870.SZ" not in portfolio.positions:
        failures.append("canonical .SZ portfolio key missing")
    if PtradeAPI._lookup_position(portfolio.positions, "159870.XSHE") is None:
        failures.append("get_position-style alias lookup stopped working")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--gate", action="append", dest="gates")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--validate-existing",
        action="store_true",
        help="validate report/result paths supplied by --report/--result-dir instead of rerunning",
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--result-dir", type=Path)
    args = parser.parse_args()

    config = load_gate_config(args.config)
    semantic_failures = validate_semantics(config)
    if semantic_failures:
        print("SEMANTIC GATE FAILED")
        for failure in semantic_failures:
            print(f"- {failure}")
        return 2

    selected = args.gates or [
        name for name, gate in config["gates"].items() if gate.get("required")
    ]
    if args.validate_existing:
        if len(selected) != 1 or not args.report or not args.result_dir:
            parser.error(
                "--validate-existing requires one --gate plus --report and --result-dir"
            )
        gate = config["gates"][selected[0]]
        report = json.loads(args.report.read_text(encoding="utf-8-sig"))
        checks, failures = validate_fidelity_report(report, gate)
        local_checks, local_failures = validate_local_result(args.result_dir, gate)
        outcome = GateOutcome(
            selected[0], not failures and not local_failures,
            str(report.get("verdict", "")), int(report.get("exit_code", -1)),
            str(args.report), str(args.result_dir),
            checks + local_checks, failures + local_failures,
        )
        outcomes = [outcome]
    else:
        outcomes = [
            run_gate(name, config["gates"][name], args.output_dir)
            for name in selected
        ]

    summary = {
        "gate_version": config["gate_version"],
        "passed": all(outcome.passed for outcome in outcomes),
        "outcomes": [asdict(outcome) for outcome in outcomes],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for outcome in outcomes:
        state = "PASS" if outcome.passed else "FAIL"
        print(f"[{state}] {outcome.name}: verdict={outcome.verdict}")
        for failure in outcome.failures:
            print(f"  - {failure}")
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
