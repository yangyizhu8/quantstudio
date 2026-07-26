#!/usr/bin/env python3
"""Review hash-bound user PyQt backtest evidence and advance or route workflow."""
from __future__ import annotations

import argparse
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

from agent_skill_common import load_json, skill_root, write_json
from analyze_backtest_artifacts import (
    analyze_verified, artifact_path_binding_problems, verified_artifact_paths,
    verify_artifact_hashes,
)
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
    "capital_contract_mismatch": "R5",
    "deployment_invariant_failed": "R3",
    "execution_funding_failed": "R3",
    "artifact_missing": "R5",
    "artifact_hash_mismatch": "R5",
    "artifact_contract_mismatch": "R5",
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
        "report_version": "2.0",
        "strategy_id": strategy_id,
        "status": status,
        "issues": issues,
        "return_stage": return_stage,
        "formal_publish_allowed": status == "PASS",
    }


def _fail(state_path: Path, state: dict, strategy_path: Path, strategy_id: str,
          failure_class: str, issues: list[str]) -> dict:
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
    report = _review_report(strategy_id, "FAIL", issues, return_stage)
    report["failure_class"] = failure_class
    write_json(strategy_path.parent / "user_backtest_review.json", report)
    return report


def _deployment_failures(design: dict[str, Any], analysis: dict[str, Any]) -> list[tuple[str, str]]:
    """Evaluate r5_deployment_invariants per rebalance, using the standardized
    QS_REBALANCE_AUDIT / QS_PORTFOLIO_AUDIT lines as the authoritative
    rebalance events — never a single historical max or whole-period average.
    """
    invariants = design.get("r5_deployment_invariants")
    if not isinstance(invariants, dict):
        return []
    failures: list[tuple[str, str]] = []
    stats = analysis["daily_stats"]
    trades = analysis["trades"]
    counters = analysis["log"]["rejection_counters"]

    mode = invariants.get("holding_count_mode")
    target = int(invariants.get("target_holdings") or 0)
    fill_ratio = float(invariants.get("minimum_fill_ratio") or 0.0)
    min_exposure = float(invariants.get("minimum_gross_exposure") or 0.0)
    max_cash = invariants.get("maximum_cash_ratio_after_rebalance")
    max_cash = float(max_cash) if max_cash is not None else None

    rebalance_audits = analysis["log"]["rebalance_audits"]
    portfolio_audits = analysis["log"]["portfolio_audits"]

    if rebalance_audits:
        # rebalance_id is the only authoritative association: one rebalance
        # matches exactly one portfolio audit; a portfolio audit can never
        # prove two rebalances, must not predate its rebalance, and must not
        # reach past the next rebalance.
        seen_ids: set[str] = set()
        structural_problem = False
        for record in rebalance_audits:
            rid = record.get("rebalance_id")
            if not rid:
                failures.append((
                    "evidence_incomplete",
                    f"QS_REBALANCE_AUDIT line lacks a unique rebalance_id: "
                    f"{record.get('raw', '')[:80]}"))
                structural_problem = True
            elif rid in seen_ids:
                failures.append((
                    "evidence_incomplete",
                    f"duplicate rebalance_id {rid!r} in QS_REBALANCE_AUDIT lines"))
                structural_problem = True
            else:
                seen_ids.add(rid)

        portfolio_by_id: dict[str, dict[str, Any]] = {}
        for record in portfolio_audits:
            rid = record.get("rebalance_id")
            if not rid:
                failures.append((
                    "evidence_incomplete",
                    f"orphan QS_PORTFOLIO_AUDIT without rebalance_id: "
                    f"{record.get('raw', '')[:80]}"))
                structural_problem = True
            elif rid in portfolio_by_id:
                failures.append((
                    "evidence_incomplete",
                    f"duplicate QS_PORTFOLIO_AUDIT for rebalance_id {rid!r}"))
                structural_problem = True
            else:
                portfolio_by_id[rid] = record
        for rid in portfolio_by_id:
            if rid not in seen_ids:
                failures.append((
                    "evidence_incomplete",
                    f"orphan QS_PORTFOLIO_AUDIT rebalance_id {rid!r} matches no "
                    "QS_REBALANCE_AUDIT"))
                structural_problem = True

        rebalance_dates = sorted(
            record["date"] for record in rebalance_audits if record.get("date"))

        if not structural_problem:
            for index, record in enumerate(rebalance_audits):
                date = record.get("date")
                selected = record.get("selected")
                rid = record["rebalance_id"]
                if not date or selected is None:
                    failures.append((
                        "deployment_invariant_failed",
                        f"QS_REBALANCE_AUDIT {rid} lacks date/selected keys"))
                    continue
                available = record.get("tradable", selected)
                expected = min(target, int(available)) if mode == "strict_target_when_candidates_available" else int(selected)
                if mode == "strict_target_when_candidates_available" and int(selected) < expected:
                    failures.append((
                        "deployment_invariant_failed",
                        f"rebalance {date} ({rid}): selected={int(selected)} is below "
                        f"the achievable target {expected} (tradable={int(available)}); "
                        "candidates were sufficient but the strategy did not select "
                        "the designed count"))
                audit = portfolio_by_id.get(rid)
                if audit is None:
                    failures.append((
                        "deployment_invariant_failed",
                        f"rebalance {date} ({rid}): no matching QS_PORTFOLIO_AUDIT with "
                        "the same rebalance_id; actual post-rebalance deployment is "
                        "unverifiable"))
                    continue
                audit_date = audit.get("date")
                if audit_date and audit_date < date:
                    failures.append((
                        "evidence_incomplete",
                        f"QS_PORTFOLIO_AUDIT {rid} date {audit_date} predates its "
                        f"rebalance date {date}"))
                    continue
                later_rebalances = [d for d in rebalance_dates if d > date]
                if audit_date and later_rebalances and audit_date >= later_rebalances[0]:
                    failures.append((
                        "evidence_incomplete",
                        f"QS_PORTFOLIO_AUDIT {rid} date {audit_date} reaches past the "
                        f"next rebalance {later_rebalances[0]}; it cannot prove this "
                        "rebalance"))
                    continue
                required = math.ceil(expected * fill_ratio)
                positions = int(audit.get("positions", 0))
                if positions < required:
                    failures.append((
                        "deployment_invariant_failed",
                        f"rebalance {date} ({rid}): positions={positions} below "
                        f"ceil(expected x minimum_fill_ratio) = {required}; a "
                        "historical max elsewhere in the run is not accepted"))
                exposure = float(audit.get("gross_exposure", 0.0))
                if min_exposure and exposure < min_exposure:
                    failures.append((
                        "deployment_invariant_failed",
                        f"rebalance {date} ({rid}): gross_exposure={exposure:.4f} below "
                        f"minimum_gross_exposure={min_exposure}"))
                if max_cash is not None:
                    cash_ratio = float(audit.get("cash_ratio", 1.0))
                    if cash_ratio > max_cash:
                        failures.append((
                            "deployment_invariant_failed",
                            f"rebalance {date} ({rid}): cash_ratio={cash_ratio:.4f} "
                            f"exceeds maximum_cash_ratio_after_rebalance={max_cash}"))
    else:
        if invariants.get("require_at_least_one_rebalance") is True \
                and trades["rebalance_day_count"] < 1:
            failures.append((
                "deployment_invariant_failed",
                "no rebalance happened during the backtest; the strategy never traded"))
        if mode == "strict_target_when_candidates_available":
            failures.append((
                "evidence_incomplete",
                "r5_deployment_invariants require QS_REBALANCE_AUDIT/QS_PORTFOLIO_AUDIT "
                "log lines with rebalance_id for per-rebalance verification; aggregate "
                "stats are not accepted as a substitute"))
    if mode == "up_to_target_count" and target > 0 \
            and stats["max_concurrent_positions"] > target:
        failures.append((
            "deployment_invariant_failed",
            f"max_concurrent_positions={stats['max_concurrent_positions']} exceeds "
            f"target_holdings={target}"))

    max_rejections = int(invariants.get("maximum_insufficient_cash_rejections") or 0)
    if counters.get("insufficient_cash", 0) > max_rejections:
        failures.append((
            "execution_funding_failed",
            f"insufficient_cash rejections={counters['insufficient_cash']} exceed "
            f"maximum_insufficient_cash_rejections={max_rejections}; the execution "
            "funding mode cannot fund the designed rebalance"))

    coverage = design.get("history_coverage_contract", {})
    minimum_eligible = coverage.get("minimum_candidates_with_full_history")
    eligible = analysis["log"].get("audit_history_eligible_count") or []
    if minimum_eligible and eligible and eligible[0] < minimum_eligible:
        failures.append((
            "framework_data_api",
            f"history_eligible_count={eligible[0]:.0f} at the first rebalance is below "
            f"minimum_candidates_with_full_history={minimum_eligible}; classify as "
            "DATA_BLOCKED or follow the confirmed fail-soft rule instead of guessing"))
    return failures


def _artifact_bound_checks(design: dict[str, Any], evidence: dict[str, Any],
                           candidate: Path | None) -> tuple[list[tuple[str, str]], dict[str, Any] | None]:
    """Verify artifact hashes, then analyze exactly the hash-bound files.

    The analyzed bytes are guaranteed to be the verified bytes: paths come from
    the hash-checked evidence entries, are required to be absolute and
    traversal-free, and the CSVs must equal result_dir/<canonical name>.
    """
    failures: list[tuple[str, str]] = []

    hash_problems = verify_artifact_hashes(evidence)
    for problem in hash_problems:
        failure_class = "artifact_missing" if "missing" in problem or "does not exist" in problem \
            else "artifact_hash_mismatch"
        failures.append((failure_class, problem))
    for problem in artifact_path_binding_problems(evidence):
        failures.append(("artifact_contract_mismatch", problem))
    if failures:
        return failures, None

    paths = verified_artifact_paths(evidence)
    invariants = design.get("r5_deployment_invariants", {})
    if paths["trades_csv"] is None:
        # Legitimate no-trade runs are only possible for signal-dependent designs.
        if invariants.get("holding_count_mode") != "signal_dependent" \
                or invariants.get("require_at_least_one_rebalance") is True:
            failures.append((
                "artifact_missing",
                "trades_csv is null, which is only allowed for holding_count_mode="
                "signal_dependent without require_at_least_one_rebalance"))
            return failures, None

    analysis = analyze_verified(
        config_path=paths["config_csv"],
        daily_stats_path=paths["daily_stats_csv"],
        trades_path=paths["trades_csv"],
        log_path=paths["log_file"],
        result_dir=Path(str(evidence.get("result_dir", ""))).resolve(),
    )
    if analysis["status"] != "PASS":
        for name in analysis.get("missing_artifacts", []):
            failures.append(("artifact_missing", f"required run artifact is missing: {name}"))
        return failures, analysis

    if paths["trades_csv"] is None:
        counters = analysis["log"]["rejection_counters"]
        if counters.get("callback_exception", 0) > 0:
            failures.append((
                "artifact_contract_mismatch",
                "no-trade evidence must include a clean completion log; callback "
                "exceptions were found"))

    config = analysis["config"]
    declared_cash = float(evidence.get("initial_cash") or 0.0)
    if config["init_capital"] and abs(config["init_capital"] - declared_cash) > 1e-6:
        failures.append((
            "capital_contract_mismatch",
            f"config.csv init_capital={config['init_capital']} contradicts declared "
            f"initial_cash={declared_cash}"))
    contract = design.get("portfolio_contract", {})
    required_cash = contract.get("required_initial_cash")
    if contract.get("sizing_mode") == "fixed_notional" and isinstance(required_cash, (int, float)) \
            and abs(config["init_capital"] - required_cash) > 1e-6:
        failures.append((
            "capital_contract_mismatch",
            f"actual init_capital={config['init_capital']} does not satisfy "
            f"portfolio_contract.required_initial_cash={required_cash}; rerun R5 with the "
            "designed capital"))
    design_match_mode = design.get("engine_profile", {}).get("match_price_mode")
    if config.get("match_price_mode") and design_match_mode \
            and config["match_price_mode"] != design_match_mode:
        failures.append((
            "artifact_contract_mismatch",
            f"ARTIFACT-ENGINE-SEMANTICS-MISMATCH: config.csv match_price_mode="
            f"{config['match_price_mode']} does not match the confirmed design "
            f"{design_match_mode}"))

    # Window binding: the hash-bound config must describe the declared window.
    if config.get("start_time") and str(config["start_time"])[:10] != str(evidence.get("start_date")):
        failures.append((
            "artifact_contract_mismatch",
            f"ARTIFACT-WINDOW-MISMATCH: config.csv start_time={config['start_time']} "
            f"!= evidence.start_date={evidence.get('start_date')}"))
    if config.get("end_time") and str(config["end_time"])[:10] != str(evidence.get("end_date")):
        failures.append((
            "artifact_contract_mismatch",
            f"ARTIFACT-WINDOW-MISMATCH: config.csv end_time={config['end_time']} "
            f"!= evidence.end_date={evidence.get('end_date')}"))

    # Strategy identity binding: the run must have executed this candidate.
    strategy_id = design.get("strategy_id", "")
    config_stem = Path(str(config.get("strategy_file") or "")).stem
    candidate_stem = candidate.stem if candidate is not None else ""
    if config_stem and strategy_id:
        accepted = {strategy_id, candidate_stem}
        if config_stem not in accepted:
            failures.append((
                "artifact_contract_mismatch",
                f"ARTIFACT-STRATEGY-MISMATCH: config.csv strategy_file={config_stem!r} "
                f"matches neither the candidate {candidate_stem!r} nor strategy_id "
                f"{strategy_id!r}"))

    expected_semantics = design.get("engine_profile", {}).get("expected_engine_semantics_version")
    if expected_semantics and config.get("engine_semantics_version") != expected_semantics:
        failures.append((
            "artifact_contract_mismatch",
            f"ARTIFACT-ENGINE-SEMANTICS-MISMATCH: config.csv engine_semantics_version="
            f"{config.get('engine_semantics_version')} does not prove the designed "
            f"semantics {expected_semantics} (e.g. rebalance_mode=callback_basket was "
            "not actually active)"))

    failures.extend(_deployment_failures(design, analysis))
    return failures, analysis


def review_evidence(strategy_path: Path, design_path: Path, evidence_path: Path,
                    project_root: Path) -> dict:
    design = load_json(design_path)
    evidence = load_json(evidence_path)
    strategy_id = design["strategy_id"]
    state_path, state = load_workflow_state(strategy_path)
    issues = _schema_errors(evidence)
    if validation_mode(design) != USER_MODE:
        issues.append("user PyQt evidence is only valid for validation_execution.mode='user_pyqt'")
    if state.get("stage") not in {"AWAITING_USER_BACKTEST", "USER_BACKTEST_EVIDENCE_INCOMPLETE", "BACKTEST_FAIL_RETURN_R1", "BACKTEST_FAIL_RETURN_R3", "BACKTEST_FAIL_RETURN_R4", "BACKTEST_FAIL_RETURN_R5"}:
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

    # Evidence 2.0: PASS is authoritative only when the real run artifacts
    # (config.csv / daily_stats.csv / trades.csv / run log) verify hashes,
    # satisfy the portfolio capital contract, and meet r5_deployment_invariants.
    # Self-reported runtime_checks booleans are never accepted as proof.
    artifact_failures, analysis = _artifact_bound_checks(design, evidence, candidate)
    if artifact_failures:
        failure_class = artifact_failures[0][0]
        write_json(strategy_path.parent / "user_backtest_evidence.json", evidence)
        if analysis is not None:
            write_json(strategy_path.parent / "backtest_artifact_analysis.json", analysis)
        return _fail(state_path, state, strategy_path, strategy_id, failure_class,
                     [message for _, message in artifact_failures])

    state.update({
        "stage": "BACKTEST_PASS",
        "backtest_status": "PASS",
        "backtest_execution_owner": USER_MODE,
        "backtest_evidence_status": "PASS",
        "backtest_evidence_version": evidence.get("evidence_version"),
        "backtest_evidence_sha256": sha256_path(evidence_path),
        "backtest_data_source": evidence["backtest_data_source"],
        "backtest_db_path": str(db_path.resolve()),
        "backtest_start_date": evidence["start_date"],
        "backtest_end_date": evidence["end_date"],
        "backtest_initial_cash": analysis["config"]["init_capital"] or evidence["initial_cash"],
        "backtest_result_dir": str(Path(str(evidence.get("result_dir", ""))).resolve()),
        "deployment_metrics": {
            "max_concurrent_positions": analysis["daily_stats"]["max_concurrent_positions"],
            "positions_after_rebalance": analysis["daily_stats"]["positions_after_rebalance"],
            "gross_exposure": analysis["daily_stats"]["gross_exposure"],
            "cash_ratio": analysis["daily_stats"]["cash_ratio"],
            "buy_count": analysis["trades"]["buy_count"],
            "sell_count": analysis["trades"]["sell_count"],
            "unique_bought_symbols": analysis["trades"]["unique_bought_symbols"],
            "rebalance_day_count": analysis["trades"]["rebalance_day_count"],
            "rejection_counters": analysis["log"]["rejection_counters"],
        },
        "formal_publish_allowed": True,
        "candidate_status": "BACKTEST_PASS",
    })
    write_state(state_path, state)
    write_json(strategy_path.parent / "user_backtest_evidence.json", evidence)
    write_json(strategy_path.parent / "backtest_artifact_analysis.json", analysis)
    report = _review_report(strategy_id, "PASS", [])
    report.update({
        "candidate_path": str(candidate),
        "candidate_sha256": evidence["candidate_sha256"],
        "backtest_window": [evidence["start_date"], evidence["end_date"]],
        "evidence_source": USER_MODE,
        "artifact_analysis": "backtest_artifact_analysis.json",
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
