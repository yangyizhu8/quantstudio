#!/usr/bin/env python3
"""Generate a hash-bound QuantStudio candidate for user-run PyQt backtesting."""
from __future__ import annotations

import argparse
from pathlib import Path

from agent_skill_common import confirmation_errors, load_json, validate_design, write_json
from validate_agent_strategy import validate_strategy
from user_backtest_flow import (
    R4_PASS_STAGES, USER_MODE, atomic_write, candidate_path, candidate_payload,
    load_workflow_state, sha256_bytes, sha256_path, validation_mode, write_state,
)


def prepare_candidate(strategy_path: Path, design_path: Path, project_root: Path,
                      overwrite: bool = False) -> dict:
    design = load_json(design_path)
    problems = validate_design(design) + confirmation_errors(design)
    if problems:
        raise ValueError("design gate failed: " + "; ".join(problems))
    if validation_mode(design) != USER_MODE:
        raise ValueError("candidate publication is only valid for validation_execution.mode='user_pyqt'")

    state_path, state = load_workflow_state(strategy_path)
    if state.get("stage") not in R4_PASS_STAGES:
        raise ValueError(
            f"candidate requires an R4 PASS stage, got {state.get('stage')!r}; "
            f"expected one of {sorted(R4_PASS_STAGES)}"
        )

    canonical_payload = strategy_path.read_bytes()
    canonical_source = canonical_payload.decode("utf-8-sig")
    strategy_id = design["strategy_id"]
    local_validation = validate_strategy(
        design, canonical_source, str(strategy_path), "quantstudio")
    ptrade_validation = validate_strategy(
        design, canonical_source, str(strategy_path), "ptrade")
    write_json(strategy_path.parent / "local_validation_report.json", local_validation)
    write_json(strategy_path.parent / "ptrade_validation_report.json", ptrade_validation)
    if local_validation["status"] != "PASS":
        raise ValueError("QuantStudio R4 validation must PASS before candidate generation")
    if "ptrade" in design.get("targets", []) and ptrade_validation["status"] != "PASS":
        raise ValueError("PTrade R4 validation must PASS before candidate generation")

    canonical_hash = sha256_bytes(canonical_payload)
    output = candidate_path(project_root, strategy_id)
    if output.exists() and not overwrite:
        raise FileExistsError(f"candidate exists; use --overwrite after a new R4 PASS: {output}")
    payload = candidate_payload(canonical_payload, strategy_id, canonical_hash)
    atomic_write(output, payload)
    candidate_hash = sha256_path(output)

    state.update({
        "stage": "AWAITING_USER_BACKTEST",
        "validation_execution_mode": USER_MODE,
        "candidate_status": "AWAITING_USER_BACKTEST",
        "candidate_path": str(output.resolve()),
        "candidate_sha256": candidate_hash,
        "canonical_sha256": canonical_hash,
        "backtest_status": "NOT_RUN",
        "backtest_evidence_status": "PENDING",
        "formal_publish_allowed": False,
        "local_validation_status": local_validation["status"],
        "ptrade_validation_status": ptrade_validation["status"],
        "recommended_backtest_start": design.get("backtest_window_contract", {}).get("recommended_start_date"),
        "hard_earliest_start_date": design.get("backtest_window_contract", {}).get("hard_earliest_start_date"),
        "actual_backtest_window_selected_by_user": True,
    })
    write_state(state_path, state)
    report = {
        "report_version": "1.0",
        "strategy_id": strategy_id,
        "status": "PASS",
        "candidate_status": "AWAITING_USER_BACKTEST",
        "candidate_path": str(output.resolve()),
        "candidate_sha256": candidate_hash,
        "canonical_sha256": canonical_hash,
        "not_for_ptrade_upload": True,
        "formal_publish_allowed": False,
        "recommended_backtest_window": design.get("backtest_window_contract", {}),
        "local_validation": local_validation,
        "ptrade_validation": ptrade_validation,
    }
    write_json(strategy_path.parent / "candidate_report.json", report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a user-PyQt backtest candidate")
    parser.add_argument("strategy")
    parser.add_argument("--design", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = prepare_candidate(
            Path(args.strategy), Path(args.design), Path(args.project_root), args.overwrite)
    except Exception as exc:
        print(f"BLOCKED: {exc}")
        return 2
    print(f"CANDIDATE: {report['candidate_path']}")
    print(f"sha256={report['candidate_sha256']}")
    print("Run it in PyQt, then submit hash-bound evidence for R5 review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
