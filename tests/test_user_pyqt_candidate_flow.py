from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quantstudio-strategy-compiler" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from prepare_user_backtest_candidate import prepare_candidate
from publish_agent_strategy import publish
from review_user_backtest_evidence import review_evidence
from tests.test_target_aware_strategy_skill import dual_etf_design, dual_source


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
    return project, workspace, db, design, design_path, strategy_path


def evidence_payload(report: dict, db: Path, *, status="PASS", failure_class=None) -> dict:
    payload = {
        "evidence_version": "1.0",
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
        "initial_cash": 100000.0,
        "engine_profile": "daily-bar-v1",
        "match_price_mode": "close",
        "runtime_checks": {
            "weekly_schedule": status == "PASS",
            "warmup": status == "PASS",
            "orders": status == "PASS",
            "weights": status == "PASS",
            "risk_controls": status == "PASS",
            "cash_positions": status == "PASS",
            "completed_output": status == "PASS",
        },
        "log_excerpt": "BACKTEST COMPLETED" if status == "PASS" else "Traceback",
    }
    if failure_class:
        payload["failure_class"] = failure_class
    return payload


def test_user_mode_candidate_then_evidence_then_formal_promotion(tmp_path):
    project, workspace, db, design, design_path, strategy_path = setup_workspace(tmp_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    candidate_path = Path(candidate["candidate_path"])
    assert candidate_path.name == "dual_user_pyqt_etf__candidate_quantstudio.py"
    assert candidate_path.exists()
    assert "NOT_FOR_PTRADE_UPLOAD=true" in candidate_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="not publishable|backtest"):
        publish(strategy_path, design_path, project)

    evidence_path = workspace / "submitted_evidence.json"
    evidence_path.write_text(json.dumps(evidence_payload(candidate, db)), encoding="utf-8")
    review = review_evidence(strategy_path, design_path, evidence_path, project)
    assert review["status"] == "PASS"

    result = publish(strategy_path, design_path, project)
    assert result["candidate_promotion"]["status"] == "PROMOTED"
    assert result["candidate_promotion"]["candidate_removed"] is True
    assert not candidate_path.exists()
    assert (project / "quantstudio" / "backtest" / "strategies"
            / "dual_user_pyqt_etf_quantstudio.py").exists()
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
        evidence_payload(candidate, db, status="FAIL", failure_class=failure_class)
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
    evidence_path.write_text(json.dumps(evidence_payload(candidate, db)), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "FAIL"
    assert report["failure_class"] == "source_drift"
    assert report["return_stage"] == "R4"


def test_backtest_before_latest_listing_bound_is_rejected(tmp_path):
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    evidence = evidence_payload(candidate, db)
    evidence["start_date"] = "2020-11-15"
    evidence_path = workspace / "early_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "EVIDENCE_INCOMPLETE"
    assert any("hard ETF-pool lower bound" in issue for issue in report["issues"])


def test_incomplete_log_evidence_does_not_unlock_publication(tmp_path):
    project, workspace, db, _, design_path, strategy_path = setup_workspace(tmp_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    evidence = evidence_payload(candidate, db)
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
