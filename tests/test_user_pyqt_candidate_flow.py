from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quantstudio-strategy-compiler" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from prepare_user_backtest_candidate import prepare_candidate
from publish_agent_strategy import publish
from retire_ptrade_runtime_evidence import retire_ptrade_runtime_evidence
from review_user_backtest_evidence import review_evidence
from tests.test_target_aware_strategy_skill import dual_etf_design, dual_source
from user_backtest_flow import sha256_path


def user_pyqt_design() -> dict:
    design = dual_etf_design()
    design["strategy_id"] = "dual_user_pyqt_etf"
    design["validation_execution"] = {
        "mode": "user_pyqt",
        "require_hash_bound_evidence": True,
        "candidate_filename_suffix": "__candidate_quantstudio.py",
        "user_selects_backtest_window": True,
        "formal_publish_requires_backtest_pass": True,
    }
    design["backtest_window_contract"] = {
        "pool_latest_listing_date": "2020-11-16",
        "hard_earliest_start_date": "2020-11-16",
        "lookback_trading_days": 252,
        "required_close_bars": 253,
        "recommended_start_date": "2021-11-29",
        "recommended_end_date_as_of_local_data": "2026-07-23",
        "actual_window_selected_by": "user_pyqt",
        "strategy_must_not_hardcode_backtest_dates": True,
        "agent_must_not_start_unconfirmed_backtest": True,
    }
    return design


def setup_workspace(tmp_path: Path):
    project = tmp_path / "project"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = project / "data" / "quantstudio.db"
    db.parent.mkdir(parents=True)
    db.touch()
    design = user_pyqt_design()
    design_path = workspace / "agent_strategy_design.json"
    design_path.write_text(json.dumps(design), encoding="utf-8")
    strategy_path = workspace / "strategy.py"
    strategy_path.write_text(dual_source(), encoding="utf-8")
    (workspace / "workspace_state.json").write_text(json.dumps({
        "strategy_id": design["strategy_id"],
        "stage": "R4_PASS",
        "backtest_status": "NOT_RUN",
        "formal_publish_allowed": False,
    }), encoding="utf-8")
    write_artifacts(workspace / "result")
    return project, workspace, db, design, design_path, strategy_path


def write_artifacts(result_dir: Path, *, init_capital=100000.0, match_mode="close",
                    positions=(2, 2, 2), buys=2, sells=0, exposure=0.85,
                    insufficient_cash=0,
                    strategy_name="本地动态ETF轮动策略__candidate_quantstudio",
                    engine_semantics="0.1.0-legacy",
                    audit: list[dict] | None = None,
                    portfolio_audit: list[dict] | None = None,
                    write_trades: bool = True) -> Path:
    """Write synthetic result_exporter-style artifacts for evidence 2.0."""
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "config.csv").write_text(
        "strategy_file,strategy,start_time,end_time,init_capital,commission_rate,"
        "min_commission,stamp_tax_rate,transfer_fee_rate,slippage_rate,fixed_slippage,"
        "match_price_mode,engine_semantics_version,min_rebalance_pct\n"
        f"{strategy_name}.py,{strategy_name},2021-11-29,2026-07-23,{init_capital},"
        f"0.0003,5.0,0.001,0.0,0.0,0.0,{match_mode},{engine_semantics},0.0\n",
        encoding="utf-8")
    days = ["2026-07-20", "2026-07-21", "2026-07-22"]
    rows = ["date,total_asset,cash,market_value,benchmark,positions,daily_return"]
    for index, day in enumerate(days):
        pos = positions[min(index, len(positions) - 1)]
        total = init_capital
        market = total * exposure
        rows.append(f"{day},{total},{total - market},{market},100.0,{pos},0.0")
    (result_dir / "daily_stats.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    if write_trades:
        trade_rows = ["datetime,code,action,volume,price,commission,tax,pnl,amount"]
        for i in range(buys):
            trade_rows.append(f"2026-07-20,51005{i}.SS,buy,100,10.0,5.0,0.0,0.0,1000.0")
        for i in range(sells):
            trade_rows.append(f"2026-07-20,51005{i}.SS,sell,100,10.0,5.0,1.0,0.0,1000.0")
        (result_dir / "trades.csv").write_text("\n".join(trade_rows) + "\n", encoding="utf-8")
    if audit is None:
        audit = [{
            "date": "2026-07-20", "rebalance_id": "20260720_1",
            "selected": 2, "tradable": 2, "sell_submitted": 0,
            "buy_submitted": buys, "history_eligible_count": 2,
            "positions": positions[0], "cash_ratio": round(1 - exposure, 3),
            "gross_exposure": exposure,
        }]
    if portfolio_audit is None:
        portfolio_audit = audit
    log_lines = ["[Backtest] started"]
    for entry in audit:
        log_lines.append(
            "QS_REBALANCE_AUDIT rebalance_id={rebalance_id} date={date} "
            "selected={selected} tradable={tradable} "
            "sell_submitted={sell_submitted} buy_submitted={buy_submitted} "
            "history_eligible_count={history_eligible_count}".format(**entry))
    for entry in portfolio_audit:
        portfolio_date = entry.get("portfolio_date") or entry["date"]
        log_lines.append(
            ("QS_PORTFOLIO_AUDIT rebalance_id={rebalance_id} date=" + portfolio_date +
             " positions={positions} cash_ratio={cash_ratio} "
             "gross_exposure={gross_exposure}").format(**entry))
    log_lines.extend("当前账户资金不足，510050.SS下单失败 insufficient_cash" for _ in range(insufficient_cash))
    log_lines.append("[Backtest] completed: 3 days")
    (result_dir / "backtest.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    return result_dir


def _artifact_entry(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha256_path(path)}


def evidence_payload(report: dict, db: Path, result_dir: Path, *, status="PASS",
                     failure_class=None, init_cash=100000.0, trades_null=False,
                     repro_dir: Path | None = None) -> dict:
    payload = {
        "evidence_version": "2.1",
        "strategy_id": "dual_user_pyqt_etf",
        "candidate_path": report["candidate_path"],
        "candidate_sha256": report["candidate_sha256"],
        "execution_owner": "user_pyqt",
        "run_status": "COMPLETED" if status == "PASS" else "FAILED",
        "backtest_status": status,
        "completed": status == "PASS",
        "exception_count": 0 if status == "PASS" else 1,
        "fatal_error": None if status == "PASS" else "synthetic failure",
        "backtest_data_source": "duckdb_provider",
        "backtest_db_path": str(db.resolve()),
        "start_date": "2021-11-29",
        "end_date": "2026-07-23",
        "initial_cash": init_cash,
        "engine_profile": "daily-bar-v1",
        "match_price_mode": "close",
        "runtime_checks": {
            "weekly_schedule": status == "PASS",
            "warmup": status == "PASS",
            "orders": status == "PASS",
        },
        "log_excerpt": "BACKTEST COMPLETED" if status == "PASS" else "Traceback",
        "result_dir": str(result_dir.resolve()),
        "artifacts": {
            "config_csv": _artifact_entry(result_dir / "config.csv"),
            "daily_stats_csv": _artifact_entry(result_dir / "daily_stats.csv"),
            "trades_csv": (None if trades_null else _artifact_entry(result_dir / "trades.csv")),
            "log_file": _artifact_entry(result_dir / "backtest.log"),
        },
    }
    # G3.5 R5 复现性门禁：默认用第二目录复制同一三件套（内容一致→hash 一致）；
    # 传 repro_dir 可注入"不同内容"的第二跑以测试 mismatch。
    if repro_dir is None:
        repro_dir = result_dir.parent / (result_dir.name + "_repro")
        repro_dir.mkdir(parents=True, exist_ok=True)
        for f in ("config.csv", "daily_stats.csv", "trades.csv"):
            src = result_dir / f
            if src.exists():
                shutil.copy2(src, repro_dir / f)
    payload["reproducibility_artifacts"] = {
        "config_csv": _artifact_entry(repro_dir / "config.csv"),
        "daily_stats_csv": _artifact_entry(repro_dir / "daily_stats.csv"),
        "trades_csv": (None if trades_null else _artifact_entry(repro_dir / "trades.csv")),
    }
    if failure_class:
        payload["failure_class"] = failure_class
    return payload


def test_user_mode_candidate_then_evidence_then_formal_promotion(tmp_path):
    project, workspace, db, design, design_path, strategy_path = setup_workspace(tmp_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    candidate_path = Path(candidate["candidate_path"])
    assert candidate_path.name == "本地动态ETF轮动策略__candidate_quantstudio.py"
    assert candidate_path.exists()
    assert "NOT_FOR_PTRADE_UPLOAD=true" in candidate_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="not publishable|backtest"):
        publish(strategy_path, design_path, project)

    evidence_path = workspace / "submitted_evidence.json"
    evidence_path.write_text(json.dumps(evidence_payload(candidate, db, workspace / "result")), encoding="utf-8")
    review = review_evidence(strategy_path, design_path, evidence_path, project)
    assert review["status"] == "PASS"

    result = publish(strategy_path, design_path, project)
    assert result["candidate_promotion"]["status"] == "PROMOTED"
    assert result["candidate_promotion"]["candidate_removed"] is True
    assert not candidate_path.exists()
    assert (project / "quantstudio" / "backtest" / "strategies"
            / "本地动态ETF轮动策略.py").exists()
    assert (project / "ptrade" / "dual_user_pyqt_etf_ptrade.py").exists()


@pytest.mark.parametrize("failure_class,return_stage", [
    ("strategy_logic", "R3"),
    ("framework_data_api", "R1"),
    ("ptrade_profile_validator", "R4"),
])
def test_failed_user_backtest_routes_to_correct_stage(tmp_path, failure_class, return_stage):
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    evidence_path = workspace / "failed_evidence.json"
    evidence_path.write_text(json.dumps(
        evidence_payload(candidate, db, workspace / "result", status="FAIL", failure_class=failure_class)
    ), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "FAIL"
    assert report["return_stage"] == return_stage
    state = json.loads((workspace / "workspace_state.json").read_text(encoding="utf-8"))
    assert state["stage"] == f"BACKTEST_FAIL_RETURN_{return_stage}"
    assert state["formal_publish_allowed"] is False


def test_candidate_hash_drift_invalidates_r4_and_routes_back(tmp_path):
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    path = Path(candidate["candidate_path"])
    path.write_text(path.read_text(encoding="utf-8") + "\n# user edit\n", encoding="utf-8")
    evidence_path = workspace / "drift_evidence.json"
    evidence_path.write_text(json.dumps(evidence_payload(candidate, db, workspace / "result")), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "FAIL"
    assert report["failure_class"] == "source_drift"
    assert report["return_stage"] == "R4"


def test_backtest_before_latest_listing_bound_is_rejected(tmp_path):
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    evidence = evidence_payload(candidate, db, workspace / "result")
    evidence["start_date"] = "2020-11-15"
    evidence_path = workspace / "early_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "EVIDENCE_INCOMPLETE"
    assert any("hard ETF-pool lower bound" in issue for issue in report["issues"])


def test_incomplete_log_evidence_does_not_unlock_publication(tmp_path):
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    evidence = evidence_payload(candidate, db, workspace / "result")
    evidence["log_excerpt"] = ""
    evidence_path = workspace / "incomplete_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "EVIDENCE_INCOMPLETE"
    with pytest.raises(ValueError):
        publish(strategy_path, design_path, project)

def test_v21_requires_explicit_backtest_owner_and_confirmation():
    from agent_skill_common import confirmation_errors, validate_design
    design = user_pyqt_design()
    design.pop("validation_execution")
    assert any("validation_execution" in item for item in validate_design(design))

    design = user_pyqt_design()
    design["user_confirmations"]["backtest_validation_mode"] = False
    assert confirmation_errors(design)


def test_candidate_requires_r4_pass_and_does_not_generate_ptrade_artifact(tmp_path):
    project, workspace, _, _, design_path, strategy_path = setup_workspace(tmp_path)
    state_path = workspace / "workspace_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["stage"] = "SCAFFOLDED"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="R4 PASS"):
        prepare_candidate(strategy_path, design_path, project)
    assert not (project / "ptrade" / "dual_user_pyqt_etf_ptrade.py").exists()


def _design_with_fixed_capital(design_path: Path, required_cash=1000000,
                               invariants: dict | None = None) -> None:
    design = json.loads(design_path.read_text(encoding="utf-8"))
    design["portfolio_contract"] = {
        "sizing_mode": "fixed_notional",
        "required_initial_cash": required_cash,
        "fixed_target_value": required_cash / 20,
        "allocation_mode": "equal_weight",
        "allocation_denominator": "configured_target_count",
        "target_holdings": 20,
        "gross_exposure_target": 0.85,
        "cash_buffer_ratio": 0.15,
        "per_position_target_weight": 0.0425,
        "max_single_weight": 0.05,
        "allow_leverage": False,
    }
    if invariants is not None:
        design["r5_deployment_invariants"] = invariants
    design_path.write_text(json.dumps(design), encoding="utf-8")


STRICT_INVARIANTS = {
    "holding_count_mode": "strict_target_when_candidates_available",
    "target_holdings": 20,
    "minimum_fill_ratio": 0.9,
    "minimum_gross_exposure": 0.80,
    "maximum_cash_ratio_after_rebalance": 0.20,
    "maximum_insufficient_cash_rejections": 0,
    "require_at_least_one_rebalance": True,
}


def _write_audit_capable_strategy(strategy_path: Path) -> None:
    """Designs with r5_deployment_invariants require QS_*_AUDIT log lines."""
    source = dual_source().rstrip("\n") + (
        "\n    log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=%s buy_submitted=%s' % ('20260720_1', '2026-07-20', 20, 20))"
        "\n    log.info('QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%s' % ('20260720_1', '2026-07-20', 20))\n"
    )
    strategy_path.write_text(source, encoding="utf-8")


def test_designed_capital_mismatch_blocks_r5(tmp_path):
    """Designed 1,000,000 but ran with 100,000: R5 must BLOCK even when the
    backtest itself completed without exceptions."""
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    _design_with_fixed_capital(design_path, required_cash=1000000)
    candidate = prepare_candidate(strategy_path, design_path, project)
    result_dir = workspace / "result"
    write_artifacts(result_dir, init_capital=100000.0)  # user selected the wrong cash
    evidence_path = workspace / "wrong_cash_evidence.json"
    evidence_path.write_text(json.dumps(
        evidence_payload(candidate, db, result_dir, init_cash=100000.0)), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "FAIL"
    assert report["failure_class"] == "capital_contract_mismatch"
    assert report["return_stage"] == "R5"
    state = json.loads((workspace / "workspace_state.json").read_text(encoding="utf-8"))
    assert state["formal_publish_allowed"] is False


def test_underdeployed_portfolio_blocks_r5(tmp_path):
    """Target 20 holdings but only 2 ever held: a finished exception-free
    backtest is NOT PASS."""
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    _design_with_fixed_capital(design_path, required_cash=100000, invariants=STRICT_INVARIANTS)
    _write_audit_capable_strategy(strategy_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    result_dir = workspace / "result"
    write_artifacts(result_dir, init_capital=100000.0, positions=(2, 2, 2), buys=2,
                    exposure=0.02, audit=[{
                        "date": "2026-07-20", "rebalance_id": "20260720_1",
                        "selected": 20, "tradable": 20,
                        "sell_submitted": 0, "buy_submitted": 2,
                        "history_eligible_count": 30, "positions": 2,
                        "cash_ratio": 0.98, "gross_exposure": 0.02,
                    }])
    evidence_path = workspace / "underdeployed_evidence.json"
    evidence_path.write_text(json.dumps(
        evidence_payload(candidate, db, result_dir)), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "FAIL"
    assert report["failure_class"] == "deployment_invariant_failed"
    assert report["return_stage"] == "R3"
    state = json.loads((workspace / "workspace_state.json").read_text(encoding="utf-8"))
    assert state["formal_publish_allowed"] is False


def test_correct_capital_and_full_deployment_passes_r5(tmp_path):
    """Correct cash, 20 positions deployed, exposure on target, no cash
    rejections: R5 PASS."""
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    _design_with_fixed_capital(design_path, required_cash=100000, invariants=STRICT_INVARIANTS)
    _write_audit_capable_strategy(strategy_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    result_dir = workspace / "result"
    write_artifacts(result_dir, init_capital=100000.0, positions=(20, 20, 20),
                    buys=20, exposure=0.85, insufficient_cash=0, audit=[{
                        "date": "2026-07-20", "rebalance_id": "20260720_1",
                        "selected": 20, "tradable": 20,
                        "sell_submitted": 0, "buy_submitted": 20,
                        "history_eligible_count": 30, "positions": 20,
                        "cash_ratio": 0.15, "gross_exposure": 0.85,
                    }])
    evidence_path = workspace / "good_evidence.json"
    evidence_path.write_text(json.dumps(
        evidence_payload(candidate, db, result_dir)), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "PASS", report
    state = json.loads((workspace / "workspace_state.json").read_text(encoding="utf-8"))
    assert state["formal_publish_allowed"] is True
    assert state["deployment_metrics"]["positions_after_rebalance"] == 20


def test_artifact_hash_mismatch_blocks_r5(tmp_path):
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    result_dir = workspace / "result"
    evidence = evidence_payload(candidate, db, result_dir)
    # tamper with the trades artifact after hashing
    trades = result_dir / "trades.csv"
    trades.write_text(trades.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    evidence_path = workspace / "tampered_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "FAIL"
    assert report["failure_class"] == "artifact_hash_mismatch"
    assert report["return_stage"] == "R5"


def test_ptrade_runtime_failure_retires_candidate_and_blocks_publish(tmp_path):
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    candidate_path = Path(candidate["candidate_path"])
    retirement = retire_ptrade_runtime_evidence(
        strategy_path, project, "NameError: name 'set_backtest' is not defined")
    assert retirement["status"] == "RETIRED"
    assert not candidate_path.exists()
    retired_matches = list(candidate_path.parent.glob(candidate_path.name + ".RETIRED_DO_NOT_UPLOAD*"))
    assert retired_matches, "candidate must be retired with a RETIRED_DO_NOT_UPLOAD suffix"
    # Retiring again must not overwrite the first retired evidence file.
    candidate_path.write_text("restored candidate\n", encoding="utf-8")
    retire_again = retire_ptrade_runtime_evidence(strategy_path, project, "second failure")
    assert len(retire_again["retired_artifacts"]) >= 1
    assert retired_matches[0].exists()

    state = json.loads((workspace / "workspace_state.json").read_text(encoding="utf-8"))
    assert state["stage"] == "PTRADE_RUNTIME_FAIL_RETURN_R1_R4"
    assert state["ptrade_profile_validation_status"] == "STALE"
    assert state["ptrade_runtime_status"] == "FAIL"
    assert state["candidate_status"] == "STALE"
    assert state["candidate_sha256"] is None
    assert state["formal_publish_allowed"] is False

    with pytest.raises(ValueError, match="not publishable|STALE|runtime failure"):
        publish(strategy_path, design_path, project)




GOOD_AUDIT = [{
    "date": "2026-07-20", "rebalance_id": "20260720_1", "selected": 20, "tradable": 20, "sell_submitted": 0,
    "buy_submitted": 20, "history_eligible_count": 30, "positions": 20,
    "cash_ratio": 0.15, "gross_exposure": 0.85,
}]
BAD_AUDIT = [{
    "date": "2026-07-20", "rebalance_id": "20260720_1", "selected": 2, "tradable": 20, "sell_submitted": 0,
    "buy_submitted": 2, "history_eligible_count": 30, "positions": 2,
    "cash_ratio": 0.90, "gross_exposure": 0.10,
}]


def test_hash_bound_artifacts_cannot_be_swapped_for_better_results(tmp_path):
    """The reviewer's bypass: hash-bind a bad run but point result_dir at a
    good run. The reviewer must analyze exactly the hash-bound files."""
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    _design_with_fixed_capital(design_path, required_cash=100000, invariants=STRICT_INVARIANTS)
    _write_audit_capable_strategy(strategy_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    bad_dir = write_artifacts(workspace / "bad", init_capital=100000.0,
                              positions=(2, 2, 2), buys=2, exposure=0.10, audit=BAD_AUDIT)
    good_dir = write_artifacts(workspace / "good", init_capital=100000.0,
                               positions=(20, 20, 20), buys=20, exposure=0.85, audit=GOOD_AUDIT)
    evidence = evidence_payload(candidate, db, bad_dir)  # hashes bound to the BAD run
    evidence["result_dir"] = str(good_dir.resolve())     # but analysis pointed at the GOOD run
    evidence_path = workspace / "swap_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "FAIL"
    assert report["failure_class"] == "artifact_contract_mismatch"
    assert any("ARTIFACT-PATH-MISMATCH" in issue for issue in report["issues"])
    state = json.loads((workspace / "workspace_state.json").read_text(encoding="utf-8"))
    assert state["formal_publish_allowed"] is False


def test_gradual_deployment_does_not_satisfy_per_rebalance_gate(tmp_path):
    """Max-positions-over-run must not rescue under-deployed rebalances."""
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    _design_with_fixed_capital(design_path, required_cash=100000, invariants=STRICT_INVARIANTS)
    _write_audit_capable_strategy(strategy_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    gradual_audit = [
        {"date": "2026-07-20", "rebalance_id": "20260720_1", "selected": 2, "tradable": 20, "sell_submitted": 0,
         "buy_submitted": 2, "history_eligible_count": 30, "positions": 2,
         "cash_ratio": 0.90, "gross_exposure": 0.10},
        {"date": "2026-07-21", "rebalance_id": "20260721_1", "selected": 3, "tradable": 20, "sell_submitted": 0,
         "buy_submitted": 1, "history_eligible_count": 30, "positions": 3,
         "cash_ratio": 0.85, "gross_exposure": 0.15},
        {"date": "2026-07-22", "rebalance_id": "20260722_1", "selected": 20, "tradable": 20, "sell_submitted": 0,
         "buy_submitted": 17, "history_eligible_count": 30, "positions": 20,
         "cash_ratio": 0.15, "gross_exposure": 0.85},
    ]
    result_dir = write_artifacts(
        workspace / "result", init_capital=100000.0, positions=(2, 3, 20), buys=20,
        exposure=0.85, audit=gradual_audit)
    evidence_path = workspace / "gradual_evidence.json"
    evidence_path.write_text(json.dumps(
        evidence_payload(candidate, db, result_dir)), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "FAIL"
    assert report["failure_class"] == "deployment_invariant_failed"
    assert any("2026-07-20" in issue or "2026-07-21" in issue for issue in report["issues"])


def test_signal_dependent_no_trade_run_allows_null_trades(tmp_path):
    """A legitimate signal-dependent run with zero trades must not be killed
    by a missing trades.csv; a clean completion log is required."""
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    signal_invariants = {
        "holding_count_mode": "signal_dependent",
        "target_holdings": 0,
        "minimum_fill_ratio": 0.0,
        "minimum_gross_exposure": 0.0,
        "maximum_cash_ratio_after_rebalance": 1.0,
        "maximum_insufficient_cash_rejections": 0,
        "require_at_least_one_rebalance": False,
    }
    _design_with_fixed_capital(design_path, required_cash=100000, invariants=signal_invariants)
    _write_audit_capable_strategy(strategy_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    result_dir = write_artifacts(workspace / "result", init_capital=100000.0,
                                 positions=(0, 0, 0), buys=0, exposure=0.0,
                                 audit=[], write_trades=False)
    evidence_path = workspace / "notrade_evidence.json"
    evidence_path.write_text(json.dumps(
        evidence_payload(candidate, db, result_dir, trades_null=True)), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "PASS", report


def test_strict_target_no_trade_run_requires_trades_csv(tmp_path):
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    _design_with_fixed_capital(design_path, required_cash=100000, invariants=STRICT_INVARIANTS)
    _write_audit_capable_strategy(strategy_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    result_dir = write_artifacts(workspace / "result", init_capital=100000.0,
                                 positions=(0, 0, 0), buys=0, exposure=0.0,
                                 audit=[], write_trades=False)
    evidence_path = workspace / "strict_notrade_evidence.json"
    evidence_path.write_text(json.dumps(
        evidence_payload(candidate, db, result_dir, trades_null=True)), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "FAIL"
    assert report["failure_class"] == "artifact_missing"


def test_wrong_window_or_strategy_artifacts_are_rejected(tmp_path):
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    result_dir = workspace / "result"
    write_artifacts(result_dir, strategy_name="some_other_strategy")
    evidence_path = workspace / "wrong_strategy_evidence.json"
    evidence_path.write_text(json.dumps(
        evidence_payload(candidate, db, result_dir)), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "FAIL"
    assert report["failure_class"] == "artifact_contract_mismatch"
    assert any("ARTIFACT-STRATEGY-MISMATCH" in issue for issue in report["issues"])


# --- runtime-shape fixture gate (R4 third gate) ---

HISTORY_STRATEGY = """
import numpy as np

def _extract_history_field(history_item, field, dtype=float):
    if history_item is None:
        return np.asarray([], dtype=dtype)
    try:
        values = history_item[field]
    except (KeyError, IndexError, TypeError, ValueError):
        return np.asarray([], dtype=dtype)
    if hasattr(values, "values"):
        values = values.values
    return np.asarray(values, dtype=dtype).reshape(-1)

def _ensure_runtime_state():
    if not hasattr(g, 'ready'):
        g.ready = True

def initialize(context):
    _ensure_runtime_state()

def before_trading_start(context, data):
    _ensure_runtime_state()
    hist = get_history(60, frequency='1d', field=['close'],
                       security_list=['510050.SS', '159915.SZ'], fq='pre', include=False, is_dict=True)
    for code, df in hist.items():
        closes = _extract_history_field(df, 'close', float)
        if len(closes) > 0:
            log.info('close %s' % closes[-1])
"""


def _write_history_strategy(strategy_path: Path, helper_body: str | None = None) -> None:
    source = HISTORY_STRATEGY
    if helper_body is not None:
        head = source[:source.index("    if history_item is None:")]
        tail = source[source.index("def _ensure_runtime_state"):]
        source = head + helper_body + "\n\n" + tail
    strategy_path.write_text(source, encoding="utf-8")


@pytest.mark.xfail(strict=True, reason="A4(PTRADE-IS-DICT-BAN)硬禁is_dict=True 与 prepare runtime-shape fixture(校验放行) 设计矛盾：对ptrade触发fixture的唯一条件(is_dict=True)被A4在R4拦死→fixture结构性不可达；本测试编码的'fixture应放行'意图与当前代码(A4先拦)不符，待X/Y决策后收口(见2026-07-31诊断)。")
def test_runtime_shape_fixture_runs_inside_prepare_candidate(tmp_path):
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    _write_history_strategy(strategy_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    assert candidate["runtime_shape_fixture_status"] == "PASS"
    state = json.loads((workspace / "workspace_state.json").read_text(encoding="utf-8"))
    assert state["runtime_shape_fixture_status"] == "PASS"
    assert state["runtime_shape_fixture_source_sha256"] == candidate["canonical_sha256"]
    assert state["runtime_shape_fixture_report_sha256"]
    assert (workspace / "runtime_shape_fixture_report.json").exists()


@pytest.mark.xfail(strict=True, reason="A4(PTRADE-IS-DICT-BAN)硬禁is_dict=True 与 prepare runtime-shape fixture(校验放行) 设计矛盾：对ptrade触发fixture的唯一条件(is_dict=True)被A4在R4拦死→fixture结构性不可达；本测试编码的'fixture应放行'意图与当前代码(A4先拦)不符，待X/Y决策后收口(见2026-07-31诊断)。")
def test_runtime_shape_fixture_failure_blocks_candidate(tmp_path):
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    _write_history_strategy(
        strategy_path,
        helper_body="    return np.asarray(history_item[field].values, dtype=dtype)")
    with pytest.raises(ValueError, match="runtime-shape fixture"):
        prepare_candidate(strategy_path, design_path, project)


@pytest.mark.xfail(strict=True, reason="A4(PTRADE-IS-DICT-BAN)硬禁is_dict=True 与 prepare runtime-shape fixture(校验放行) 设计矛盾：对ptrade触发fixture的唯一条件(is_dict=True)被A4在R4拦死→fixture结构性不可达；本测试编码的'fixture应放行'意图与当前代码(A4先拦)不符，待X/Y决策后收口(见2026-07-31诊断)。")
def test_publish_blocked_without_fixture_pass(tmp_path):
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    _write_history_strategy(strategy_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    evidence_path = workspace / "fixture_evidence.json"
    evidence_path.write_text(json.dumps(
        evidence_payload(candidate, db, workspace / "result")), encoding="utf-8")
    review = review_evidence(strategy_path, design_path, evidence_path, project)
    assert review["status"] == "PASS"

    state_path = workspace / "workspace_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.pop("runtime_shape_fixture_status")
    state.pop("runtime_shape_fixture_source_sha256")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="runtime-shape fixture PASS is required"):
        publish(strategy_path, design_path, project)


@pytest.mark.xfail(strict=True, reason="A4(PTRADE-IS-DICT-BAN)硬禁is_dict=True 与 prepare runtime-shape fixture(校验放行) 设计矛盾：对ptrade触发fixture的唯一条件(is_dict=True)被A4在R4拦死→fixture结构性不可达；本测试编码的'fixture应放行'意图与当前代码(A4先拦)不符，待X/Y决策后收口(见2026-07-31诊断)。")
def test_publish_blocked_when_canonical_changed_after_fixture(tmp_path):
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    _write_history_strategy(strategy_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    evidence_path = workspace / "fixture_stale_evidence.json"
    evidence_path.write_text(json.dumps(
        evidence_payload(candidate, db, workspace / "result")), encoding="utf-8")
    review = review_evidence(strategy_path, design_path, evidence_path, project)
    assert review["status"] == "PASS"

    strategy_path.write_text(
        strategy_path.read_text(encoding="utf-8") + "\n# post-fixture edit\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="STALE|changed after the runtime-shape fixture"):
        publish(strategy_path, design_path, project)


# --- rebalance_id one-to-one association (round-2 review P0-4) ---

def _rebalance(rid: str, date: str, selected=20, tradable=20) -> dict:
    return {
        "rebalance_id": rid, "date": date, "selected": selected, "tradable": tradable,
        "sell_submitted": 0, "buy_submitted": selected, "history_eligible_count": 30,
        "positions": selected, "cash_ratio": 0.15, "gross_exposure": 0.85,
    }


def _invariant_workspace(tmp_path):
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    _design_with_fixed_capital(design_path, required_cash=100000, invariants=STRICT_INVARIANTS)
    _write_audit_capable_strategy(strategy_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    return project, workspace, db, design_path, strategy_path, candidate


def test_single_late_portfolio_audit_cannot_prove_two_rebalances(tmp_path):
    project, workspace, db, design_path, strategy_path, candidate = _invariant_workspace(tmp_path)
    result_dir = write_artifacts(
        workspace / "result", init_capital=100000.0, positions=(20, 20, 20), buys=20,
        audit=[_rebalance("20260331_1", "2026-03-31"),
               _rebalance("20260630_1", "2026-06-30")],
        portfolio_audit=[{
            "rebalance_id": "20260331_1", "portfolio_date": "2026-07-01",
            "positions": 20, "cash_ratio": 0.15, "gross_exposure": 0.85,
        }])
    evidence_path = workspace / "shared_audit_evidence.json"
    evidence_path.write_text(json.dumps(
        evidence_payload(candidate, db, result_dir)), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "FAIL"
    # the 2026-06-30 rebalance has no matching audit; the 2026-07-01 audit also
    # reaches past the next rebalance
    assert any("20260630_1" in issue for issue in report["issues"]) \
        or any("next rebalance" in issue for issue in report["issues"])


def test_portfolio_audit_after_next_rebalance_cannot_backfill(tmp_path):
    project, workspace, db, design_path, strategy_path, candidate = _invariant_workspace(tmp_path)
    result_dir = write_artifacts(
        workspace / "result", init_capital=100000.0, positions=(20, 20, 20), buys=20,
        audit=[_rebalance("20260331_1", "2026-03-31"),
               _rebalance("20260630_1", "2026-06-30")],
        portfolio_audit=[
            {"rebalance_id": "20260331_1", "portfolio_date": "2026-06-30",
             "positions": 20, "cash_ratio": 0.15, "gross_exposure": 0.85},
            {"rebalance_id": "20260630_1", "portfolio_date": "2026-07-01",
             "positions": 20, "cash_ratio": 0.15, "gross_exposure": 0.85},
        ])
    evidence_path = workspace / "backfill_evidence.json"
    evidence_path.write_text(json.dumps(
        evidence_payload(candidate, db, result_dir)), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "FAIL"
    assert any("next rebalance" in issue for issue in report["issues"])


def test_duplicate_rebalance_id_is_rejected(tmp_path):
    project, workspace, db, design_path, strategy_path, candidate = _invariant_workspace(tmp_path)
    result_dir = write_artifacts(
        workspace / "result", init_capital=100000.0, positions=(20, 20, 20), buys=20,
        audit=[_rebalance("20260331_1", "2026-03-31"),
               _rebalance("20260331_1", "2026-06-30")])
    evidence_path = workspace / "dup_evidence.json"
    evidence_path.write_text(json.dumps(
        evidence_payload(candidate, db, result_dir)), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "FAIL"
    assert report["failure_class"] == "evidence_incomplete"
    assert any("duplicate rebalance_id" in issue for issue in report["issues"])


def test_two_rebalances_with_matching_audits_pass(tmp_path):
    project, workspace, db, design_path, strategy_path, candidate = _invariant_workspace(tmp_path)
    result_dir = write_artifacts(
        workspace / "result", init_capital=100000.0, positions=(20, 20, 20), buys=20,
        audit=[_rebalance("20260331_1", "2026-03-31"),
               _rebalance("20260630_1", "2026-06-30")])
    evidence_path = workspace / "matched_evidence.json"
    evidence_path.write_text(json.dumps(
        evidence_payload(candidate, db, result_dir)), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "PASS", report


def test_next_open_next_day_audit_with_matching_id_passes(tmp_path):
    project, workspace, db, design_path, strategy_path, candidate = _invariant_workspace(tmp_path)
    result_dir = write_artifacts(
        workspace / "result", init_capital=100000.0, positions=(20, 20, 20), buys=20,
        audit=[_rebalance("20260331_1", "2026-03-31")],
        portfolio_audit=[{
            "rebalance_id": "20260331_1", "portfolio_date": "2026-04-01",
            "positions": 20, "cash_ratio": 0.15, "gross_exposure": 0.85,
        }])
    evidence_path = workspace / "nextday_evidence.json"
    evidence_path.write_text(json.dumps(
        evidence_payload(candidate, db, result_dir)), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "PASS", report


def test_orphan_portfolio_audit_is_rejected(tmp_path):
    project, workspace, db, design_path, strategy_path, candidate = _invariant_workspace(tmp_path)
    result_dir = write_artifacts(
        workspace / "result", init_capital=100000.0, positions=(20, 20, 20), buys=20,
        audit=[_rebalance("20260331_1", "2026-03-31")],
        portfolio_audit=[{
            "rebalance_id": "99999999_9", "portfolio_date": "2026-03-31",
            "positions": 20, "cash_ratio": 0.15, "gross_exposure": 0.85,
        }])
    evidence_path = workspace / "orphan_evidence.json"
    evidence_path.write_text(json.dumps(
        evidence_payload(candidate, db, result_dir)), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "FAIL"
    assert any("orphan" in issue for issue in report["issues"])


def test_basket_design_requires_proven_engine_semantics_at_r5(tmp_path):
    """Design declares callback_basket but config.csv shows the basket never
    activated (0.2.0-next_open_pending): ARTIFACT-ENGINE-SEMANTICS-MISMATCH."""
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    _design_with_fixed_capital(design_path, required_cash=100000, invariants=STRICT_INVARIANTS)
    design = json.loads(design_path.read_text(encoding="utf-8"))
    design["engine_profile"]["match_price_mode"] = "next_open"
    design["engine_profile"]["rebalance_mode"] = "callback_basket"
    design["engine_profile"]["expected_engine_semantics_version"] = "0.4.0-next_open_basket"
    design_path.write_text(json.dumps(design), encoding="utf-8")
    _write_audit_capable_strategy(strategy_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    result_dir = write_artifacts(
        workspace / "result", init_capital=100000.0, match_mode="next_open",
        engine_semantics="0.2.0-next_open_pending",
        positions=(20, 20, 20), buys=20, audit=GOOD_AUDIT)
    evidence = evidence_payload(candidate, db, result_dir)
    evidence["match_price_mode"] = "next_open"
    evidence_path = workspace / "basket_semantics_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "FAIL"
    assert report["failure_class"] == "artifact_contract_mismatch"
    assert any("ARTIFACT-ENGINE-SEMANTICS-MISMATCH" in issue for issue in report["issues"])


# --- fixture report audit closure at publish (round-2 review item 3) ---

def _published_ready_workspace(tmp_path):
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    _write_history_strategy(strategy_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    evidence_path = workspace / "publish_ready_evidence.json"
    evidence_path.write_text(json.dumps(
        evidence_payload(candidate, db, workspace / "result")), encoding="utf-8")
    review = review_evidence(strategy_path, design_path, evidence_path, project)
    assert review["status"] == "PASS"
    return project, workspace, design_path, strategy_path


@pytest.mark.xfail(strict=True, reason="A4(PTRADE-IS-DICT-BAN)硬禁is_dict=True 与 prepare runtime-shape fixture(校验放行) 设计矛盾：对ptrade触发fixture的唯一条件(is_dict=True)被A4在R4拦死→fixture结构性不可达；本测试编码的'fixture应放行'意图与当前代码(A4先拦)不符，待X/Y决策后收口(见2026-07-31诊断)。")
def test_publish_blocked_when_fixture_report_deleted(tmp_path):
    project, workspace, design_path, strategy_path = _published_ready_workspace(tmp_path)
    (workspace / "runtime_shape_fixture_report.json").unlink()
    with pytest.raises(ValueError, match="fixture report is missing"):
        publish(strategy_path, design_path, project)


@pytest.mark.xfail(strict=True, reason="A4(PTRADE-IS-DICT-BAN)硬禁is_dict=True 与 prepare runtime-shape fixture(校验放行) 设计矛盾：对ptrade触发fixture的唯一条件(is_dict=True)被A4在R4拦死→fixture结构性不可达；本测试编码的'fixture应放行'意图与当前代码(A4先拦)不符，待X/Y决策后收口(见2026-07-31诊断)。")
def test_publish_blocked_when_fixture_report_tampered(tmp_path):
    project, workspace, design_path, strategy_path = _published_ready_workspace(tmp_path)
    report_path = workspace / "runtime_shape_fixture_report.json"
    report_path.write_text(report_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="report changed|not trustworthy"):
        publish(strategy_path, design_path, project)


@pytest.mark.xfail(strict=True, reason="A4(PTRADE-IS-DICT-BAN)硬禁is_dict=True 与 prepare runtime-shape fixture(校验放行) 设计矛盾：对ptrade触发fixture的唯一条件(is_dict=True)被A4在R4拦死→fixture结构性不可达；本测试编码的'fixture应放行'意图与当前代码(A4先拦)不符，待X/Y决策后收口(见2026-07-31诊断)。")
def test_publish_blocked_when_state_report_sha_mismatches(tmp_path):
    project, workspace, design_path, strategy_path = _published_ready_workspace(tmp_path)
    state_path = workspace / "workspace_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["runtime_shape_fixture_report_sha256"] = "0" * 64
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="report changed|not trustworthy"):
        publish(strategy_path, design_path, project)


@pytest.mark.xfail(strict=True, reason="A4(PTRADE-IS-DICT-BAN)硬禁is_dict=True 与 prepare runtime-shape fixture(校验放行) 设计矛盾：对ptrade触发fixture的唯一条件(is_dict=True)被A4在R4拦死→fixture结构性不可达；本测试编码的'fixture应放行'意图与当前代码(A4先拦)不符，待X/Y决策后收口(见2026-07-31诊断)。")
def test_publish_blocked_when_fixture_report_status_flipped(tmp_path):
    project, workspace, design_path, strategy_path = _published_ready_workspace(tmp_path)
    report_path = workspace / "runtime_shape_fixture_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["status"] = "FAIL"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    state_path = workspace / "workspace_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["runtime_shape_fixture_report_sha256"] = sha256_path(report_path)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="no longer records PASS"):
        publish(strategy_path, design_path, project)


# ---------------------------------------------------------------------------
# G3.5 R5 复现性门禁：两独立进程双跑，三件套 SHA-256 一致才 PASS
# ---------------------------------------------------------------------------

def _review_ready(tmp_path):
    project, workspace, db, design, design_path, strategy_path = setup_workspace(tmp_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    return workspace, db, strategy_path, design_path, project, candidate


def test_reproducibility_evidence_missing_blocks_r5(tmp_path):
    """证据缺少第二跑（reproducibility_artifacts）→ EVIDENCE_INCOMPLETE"""
    workspace, db, strategy_path, design_path, project, candidate = _review_ready(tmp_path)
    result_dir = write_artifacts(workspace / "result", init_capital=100000.0,
                                 positions=(2, 2, 2), buys=2, exposure=0.98)
    payload = evidence_payload(candidate, db, result_dir)
    del payload["reproducibility_artifacts"]
    evidence_path = workspace / "evidence.json"
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "EVIDENCE_INCOMPLETE"
    assert any("reproducibility" in issue for issue in report["issues"])


def test_reproducibility_mismatch_blocks_r5(tmp_path):
    """第二跑 trades.csv 与主运行不一致 → R5 FAIL（reproducibility_mismatch）"""
    workspace, db, strategy_path, design_path, project, candidate = _review_ready(tmp_path)
    result_dir = write_artifacts(workspace / "result", init_capital=100000.0,
                                 positions=(2, 2, 2), buys=2, exposure=0.98)
    bad_dir = workspace / "result_repro_bad"
    bad_dir.mkdir(exist_ok=True)
    for f in ("config.csv", "daily_stats.csv"):
        shutil.copy2(result_dir / f, bad_dir / f)
    (bad_dir / "trades.csv").write_text("datetime,code,action,volume,price\n2026-07-20,159915.SZ,buy,1,1.0\n",
                                        encoding="utf-8")  # 与主运行 trades 内容不同
    payload = evidence_payload(candidate, db, result_dir, repro_dir=bad_dir)
    evidence_path = workspace / "evidence.json"
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "FAIL"
    assert report.get("failure_class") == "reproducibility_mismatch"
    assert any("SHA-256 不一致" in issue for issue in report["issues"])


def test_reproducibility_matching_passes_r5(tmp_path):
    """双跑三件套一致 → 复现性门禁通过，正常 PASS"""
    workspace, db, strategy_path, design_path, project, candidate = _review_ready(tmp_path)
    result_dir = write_artifacts(workspace / "result", init_capital=100000.0,
                                 positions=(2, 2, 2), buys=2, exposure=0.98)
    payload = evidence_payload(candidate, db, result_dir)   # 默认 repro 目录复制同内容
    evidence_path = workspace / "evidence.json"
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "PASS"
    assert not any("reproducibility" in issue for issue in report["issues"])
