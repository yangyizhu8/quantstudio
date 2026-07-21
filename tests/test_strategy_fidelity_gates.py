"""Cross-stage strategy-fidelity gate contract tests."""
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from scripts.run_strategy_fidelity_gates import (
    load_gate_config,
    validate_fidelity_report,
    validate_local_result,
    validate_semantics,
)


def test_portfolio_exact_membership_semantic_gate_passes():
    config = load_gate_config()
    assert validate_semantics(config) == []


def test_etf_approved_fidelity_and_local_result_pass():
    import json

    config = load_gate_config()
    gate = config["gates"]["etf_momentum"]
    report = json.loads((
        ROOT / "output" / "strategy-fidelity-gates" /
        "etf_momentum_current.json").read_text(encoding="utf-8"))
    checks, failures = validate_fidelity_report(report, gate)
    assert failures == []
    assert any(check.startswith("L1=") for check in checks)

    result_dir = ROOT / "output" / "backtest_results" / "20260721_094926_ETF动量"
    checks, failures = validate_local_result(result_dir, gate)
    assert failures == []
    assert "trade_count=3" in checks
    assert "trade_sequence=exact" in checks


def test_etf_gate_rejects_the_known_31_trade_drift():
    config = load_gate_config()
    gate = config["gates"]["etf_momentum"]
    drifted_dir = ROOT / "output" / "backtest_results" / "20260721_024759_ETF动量"
    checks, failures = validate_local_result(drifted_dir, gate)
    assert failures
    assert any("trade_count=31" in failure for failure in failures)
    assert any("final_asset=" in failure for failure in failures)


def test_fidelity_gate_rejects_nav_drift_even_if_verdict_is_pass():
    import json

    config = load_gate_config()
    gate = config["gates"]["etf_momentum"]
    report = json.loads((
        ROOT / "output" / "strategy-fidelity-gates" /
        "etf_momentum_current.json").read_text(encoding="utf-8"))
    report = deepcopy(report)
    report["metrics"]["L2"]["value"]["nav_deviation"] = 0.10
    _, failures = validate_fidelity_report(report, gate)
    assert any("L2.nav" in failure for failure in failures)


def test_smallcap_close_baseline_is_accepted_without_relaxing_submetrics():
    import json

    config = load_gate_config()
    gate = config["gates"]["smallcap_guard"]
    report = json.loads((
        ROOT / "output" / "strategy-fidelity-gates" /
        "smallcap_current.json").read_text(encoding="utf-8"))
    checks, failures = validate_fidelity_report(report, gate)
    assert failures == []
    assert "verdict=CLOSE" in checks


def test_required_gate_sample_directories_exist():
    config = load_gate_config()
    for gate in config["gates"].values():
        samples = (ROOT / gate["ptrade_samples"]).resolve()
        assert samples.is_dir(), samples
        assert any(samples.glob("交易详情*.csv"))
        assert any(samples.glob("持仓明细*.csv"))
        assert (samples / "Log.txt").is_file()
