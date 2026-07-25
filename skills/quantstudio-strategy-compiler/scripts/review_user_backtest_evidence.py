#!/usr/bin/env python3
"""Review hash-bound user PyQt backtest evidence and advance or route workflow."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from agent_skill_common import load_json, skill_root, write_json
from user_backtest_flow import (
    USER_MODE, ensure_candidate_path_is_safe, load_workflow_state, sha256_path,
    validation_mode, write_state,
)

RETURN_STAGE = {
    "strategy_logic": "R3",
    "framework_data_api": "R1",
    "ptrade_profile_validator": "R4",
    "evidence_incomplete": "R5",
    "source_drift": "R4",
}


def _schema_errors(evidence: dict[str, Any]) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema is required to validate user backtest evidence"]
    schema = load_json(skill_root() / "schemas" / "user_backtest_evidence.schema.json")
    validator = jsonschema.Draft7Validator(schema)
    errors = []
    for error in sorted(validator.iter_errors(evidence), key=lambda e: list(e.absolute_path)):
        loc = ".".join(str(item) for item in error.absolute_path) or "$"
        errors.append(f"{loc}: {error.message}")
    return errors


def _review_report(strategy_id: str, status: str, issues: list[str],
                   return_stage: str | None = None) -> dict:
    return {
        "report_version": "1.0",
        "strategy_id": strategy_id,
        "status": status,
        "issues": issues,
        "return_stage": return_stage,
        "formal_publish_allowed": status == "PASS",
    }


def review_evidence(strategy_path: Path, design_path: Path, evidence_path: Path,
                    project_root: Path) -> dict:
    design = load_json(design_path)
    evidence = load_json(evidence_path)
    strategy_id = design["strategy_id"]
    state_path, state = load_workflow_state(strategy_path)
    issues = _schema_errors(evidence)
    if validation_mode(design) != USER_MODE:
        issues.append("user PyQt evidence is only valid for validation_execution.mode='user_pyqt'")
    if state.get("stage") not in {"AWAITING_USER_BACKTEST", "USER_BACKTEST_EVIDENCE_INCOMPLETE", "BACKTEST_FAIL_RETURN_R1", "BACKTEST_FAIL_RETURN_R3", "BACKTEST_FAIL_RETURN_R4"}:
        issues.append(f"workflow stage {state.get('stage')!r} is not awaiting user backtest evidence")
    if evidence.get("strategy_id") != strategy_id:
        issues.append("evidence strategy_id does not match design")

    candidate = None
    source_drift = False
    try:
        candidate = ensure_candidate_path_is_safe(
            evidence.get("candidate_path", ""), project_root, strategy_id)
        if not candidate.exists():
            issues.append(f"candidate file does not exist: {candidate}")
        else:
            actual_hash = sha256_path(candidate)
            if actual_hash != state.get("candidate_sha256"):
                source_drift = True
                issues.append("candidate file hash differs from the R4-generated workspace state")
            if actual_hash != evidence.get("candidate_sha256"):
                source_drift = True
                issues.append("candidate file hash differs from submitted evidence")
    except Exception as exc:
        issues.append(str(exc))

    try:
        start = date.fromisoformat(str(evidence.get("start_date")))
        end = date.fromisoformat(str(evidence.get("end_date")))
        if start > end:
            issues.append("backtest start_date must not be after end_date")
        hard_start = design.get("backtest_window_contract", {}).get("hard_earliest_start_date")
        if hard_start and start < date.fromisoformat(hard_start):
            issues.append(
                f"backtest start_date {start} is earlier than hard ETF-pool lower bound {hard_start}")
    except Exception:
        issues.append("backtest start_date/end_date must be valid ISO dates")

    db_path = Path(str(evidence.get("backtest_db_path", "")))
    if not db_path.is_absolute() or not db_path.exists():
        issues.append(f"backtest_db_path must be an existing absolute path: {db_path}")
    else:
        project_db = (project_root / "data" / "quantstudio.db").resolve()
        if project_db.exists() and db_path.resolve() != project_db \
                and evidence.get("external_db_override_confirmed") is not True:
            issues.append("external database requires explicit override confirmation")

    if evidence.get("engine_profile") != design.get("engine_profile", {}).get("profile_id"):
        issues.append("evidence engine_profile does not match confirmed design")
    if evidence.get("match_price_mode") != design.get("engine_profile", {}).get("match_price_mode"):
        issues.append("evidence match_price_mode does not match confirmed design")

    if issues and source_drift:
        state.update({
            "stage": "BACKTEST_FAIL_RETURN_R4",
            "backtest_status": "FAIL",
            "backtest_evidence_status": "FAIL",
            "backtest_failure_class": "source_drift",
            "return_stage": "R4",
            "formal_publish_allowed": False,
        })
        write_state(state_path, state)
        report = _review_report(strategy_id, "FAIL", issues, "R4")
        report["failure_class"] = "source_drift"
        write_json(strategy_path.parent / "user_backtest_review.json", report)
        return report

    if issues:
        state.update({
            "stage": "USER_BACKTEST_EVIDENCE_INCOMPLETE",
            "backtest_status": "NOT_VERIFIED",
            "backtest_evidence_status": "INCOMPLETE",
            "formal_publish_allowed": False,
        })
        write_state(state_path, state)
        report = _review_report(strategy_id, "EVIDENCE_INCOMPLETE", issues, "R5")
        write_json(strategy_path.parent / "user_backtest_review.json", report)
        return report

    if evidence["backtest_status"] == "FAIL":
        failure_class = evidence["failure_class"]
        return_stage = RETURN_STAGE[failure_class]
        state.update({
            "stage": f"BACKTEST_FAIL_RETURN_{return_stage}",
            "backtest_status": "FAIL",
            "backtest_evidence_status": "FAIL",
            "backtest_failure_class": failure_class,
            "return_stage": return_stage,
            "formal_publish_allowed": False,
        })
        write_state(state_path, state)
        write_json(strategy_path.parent / "user_backtest_evidence.json", evidence)
        report = _review_report(
            strategy_id, "FAIL", [f"backtest failure routed to {return_stage}"], return_stage)
        report["failure_class"] = failure_class
        write_json(strategy_path.parent / "user_backtest_review.json", report)
        return report

    state.update({
        "stage": "BACKTEST_PASS",
        "backtest_status": "PASS",
        "backtest_execution_owner": USER_MODE,
        "backtest_evidence_status": "PASS",
        "backtest_evidence_sha256": sha256_path(evidence_path),
        "backtest_data_source": evidence["backtest_data_source"],
        "backtest_db_path": str(db_path.resolve()),
        "backtest_start_date": evidence["start_date"],
        "backtest_end_date": evidence["end_date"],
        "backtest_initial_cash": evidence["initial_cash"],
        "formal_publish_allowed": True,
        "candidate_status": "BACKTEST_PASS",
    })
    write_state(state_path, state)
    write_json(strategy_path.parent / "user_backtest_evidence.json", evidence)
    report = _review_report(strategy_id, "PASS", [])
    report.update({
        "candidate_path": str(candidate),
        "candidate_sha256": evidence["candidate_sha256"],
        "backtest_window": [evidence["start_date"], evidence["end_date"]],
        "evidence_source": USER_MODE,
    })
    write_json(strategy_path.parent / "user_backtest_review.json", report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Review user PyQt backtest evidence")
    parser.add_argument("strategy")
    parser.add_argument("--design", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args(argv)
    report = review_evidence(
        Path(args.strategy), Path(args.design), Path(args.evidence), Path(args.project_root))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
