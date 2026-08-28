#!/usr/bin/env python3
"""Gate, validate, and atomically publish target-aware strategy code."""
from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

from agent_skill_common import (
    load_json, published_quantstudio_filename, strategy_name_conflict_errors,
    write_json,
)
from validate_agent_strategy import validate_strategy
from validate_dual_consistency import compare_sources
from validate_runtime_shapes import requires_runtime_shape_fixture
from user_backtest_flow import (
    USER_MODE, ensure_candidate_path_is_safe, sha256_path, validation_mode,
)


_ALLOWED_PREPUBLISH_STAGES = {"BACKTEST_PASS", "LOCAL_VALIDATION_PASS", "DUAL_VALIDATION_PASS", "PUBLISHED"}


def _load_workflow_state(strategy_path: Path) -> tuple[Path, dict]:
    state_path = strategy_path.parent / "workspace_state.json"
    if not state_path.exists():
        raise ValueError("workspace_state.json is required; R0-R5 workflow evidence cannot be skipped")
    state = load_json(state_path)
    stage = state.get("stage")
    if stage not in _ALLOWED_PREPUBLISH_STAGES:
        raise ValueError(
            f"workflow stage {stage!r} is not publishable; required one of "
            f"{sorted(_ALLOWED_PREPUBLISH_STAGES)} after local backtest")
    if state.get("backtest_status") != "PASS":
        raise ValueError("local backtest evidence must be PASS before dual publication")
    if state.get("ptrade_runtime_status") in {"FAIL", "STALE"} \
            or state.get("ptrade_profile_validation_status") == "STALE":
        raise ValueError(
            "a real PTrade runtime failure retired the old evidence; repair the reusable "
            "profile/adapter/Skill rule and regenerate fresh hashes before publishing")
    # R5.5 statistical robustness gate (design doc robustness-gates-design.md §3, M4 compat):
    # legacy ledgers without a "robustness" field are treated as "not yet entered R5.5"
    # (read-compatible, no error) — but publishing still requires the gate to be satisfied,
    # otherwise the stage could be silently skipped.
    rob = state.get("robustness")
    if not isinstance(rob, dict) or not rob:
        raise ValueError(
            "R5.5 robustness evidence missing from the ledger (stage never entered): run "
            "scripts/run_robustness_suite.py, or record a verbatim-confirmed exemption "
            "(design.validation_contract.robustness_gates.enabled=false) before publishing")
    rob_stage = rob.get("stage")
    if rob.get("exempted") is True:
        pass  # verbatim-confirmed exemption recorded — R6 releases
    elif rob_stage == "ROBUSTNESS_FAILED" or rob.get("terminal") is True:
        raise ValueError(
            "R5.5 robustness terminated (ROBUSTNESS_FAILED): iteration cap reached with "
            "failing gates; formal publication is permanently blocked for this attempt")
    elif rob_stage != "PASS":
        raise ValueError(
            f"R5.5 robustness stage {rob_stage!r} is not publishable; required PASS or "
            "EXEMPTED (exemption needs verbatim customer confirmation in the design)")
    # R5.4 parameter-optimization gates (design doc parameter-optimization-design.md
    # §3.4/§3.5; M2 lifecycle tail-end enforcement). Legacy ledgers without an
    # "optimization" field: a design 2.2/neutral contract means the study was never
    # required (no error); an ENABLED contract demands an explicit study outcome.
    design = None
    design_path = strategy_path.parent / "agent_strategy_design.json"
    if design_path.exists():
        try:
            design = load_json(design_path)
        except Exception:
            design = None
    contract = (design or {}).get("parameter_optimization_contract") or {}
    optimization_required = bool(contract.get("enabled") is True
                                 and contract.get("search_space"))
    opt = state.get("optimization")
    if optimization_required:
        if not isinstance(opt, dict) or not opt:
            raise ValueError(
                "R5.4 optimization study was authorized by the design contract but no "
                "study outcome is recorded in the ledger: run "
                "scripts/run_optimization_study.py, or record a verbatim customer "
                "decline before publishing")
        opt_status = opt.get("status")
        if opt_status == "PROPOSAL_ACCEPTED":
            accepted = opt.get("accepted_params") or {}
            strategy_source = strategy_path.read_text(encoding="utf-8")
            for name, value in accepted.items():
                marker_variants = (f"{name!r}, {value!r}", f'"{name}", {value!r}',
                                   f"{name!r},{value!r}", f'"{name}",{value!r}')
                if not any(v in strategy_source for v in marker_variants):
                    raise ValueError(
                        f"accepted optimization proposal param {name!r}={value!r} is not "
                        f"reflected in the published source; regenerate the strategy from "
                        f"the accepted design defaults before publishing")
    overrides = strategy_path.parent / "param_overrides.json"
    if overrides.exists():
        raise ValueError(
            "param_overrides.json found next to the strategy (M2 lifecycle invariant): "
            "override files exist only inside the R5.4 study window and must never "
            "cross R6; delete it and regenerate before publishing")
    return state_path, state


def _not_applicable_report(strategy_id: str, source: str, target_profile: str,
                           reason: str) -> dict:
    return {
        "report_version": "2.1",
        "strategy_id": strategy_id,
        "source": source,
        "target_profile": target_profile,
        "status": "NOT_APPLICABLE",
        "block_count": 0,
        "warning_count": 0,
        "reason": reason,
        "issues": [],
    }


def _validate_user_managed_publish(design: dict, state: dict,
                                   project_root: Path) -> tuple[Path | None, str | None]:
    if validation_mode(design) != USER_MODE:
        return None, None
    if state.get("backtest_execution_owner") != USER_MODE:
        raise ValueError("user_pyqt mode requires R5 evidence reviewed as user_pyqt")
    if state.get("backtest_evidence_status") != "PASS":
        raise ValueError("user_pyqt mode requires backtest_evidence_status='PASS'")
    if state.get("formal_publish_allowed") is not True:
        raise ValueError("user_pyqt evidence has not unlocked formal publication")
    raw_candidate = state.get("candidate_path")
    expected_hash = state.get("candidate_sha256")
    if not raw_candidate or not expected_hash:
        raise ValueError("candidate path/hash evidence is missing from workspace state")
    candidate = ensure_candidate_path_is_safe(
        raw_candidate, project_root, design["strategy_id"],
        design.get("strategy_name"))
    if not candidate.exists():
        raise ValueError(f"validated candidate no longer exists: {candidate}")
    actual_hash = sha256_path(candidate)
    if actual_hash != expected_hash:
        raise ValueError(
            "validated candidate changed after R5 evidence; return to R4 and rerun user backtest")
    return candidate, actual_hash


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temp.write_bytes(payload)
    os.replace(temp, path)


def _validate_backtest_data_source(state: dict, project_root: Path) -> None:
    if state.get("backtest_data_source") != "duckdb_provider":
        raise ValueError(
            "workspace_state.json must record backtest_data_source='duckdb_provider'; "
            "strategy code may not select storage directly")
    raw_path = state.get("backtest_db_path")
    if not raw_path:
        raise ValueError("workspace_state.json must record the absolute backtest_db_path")
    selected = Path(raw_path)
    if not selected.is_absolute() or not selected.exists():
        raise ValueError(f"recorded backtest_db_path must be an existing absolute path: {selected}")

    project_db = (Path(project_root) / "data" / "quantstudio.db").resolve()
    selected_resolved = selected.resolve()
    override_confirmed = state.get("external_db_override_confirmed") is True
    if project_db.exists() and selected_resolved != project_db and not override_confirmed:
        raise ValueError(
            f"project-local DuckDB exists at {project_db}; selecting {selected_resolved} "
            "requires external_db_override_confirmed=true with customer evidence")


def publish(strategy_path: Path, design_path: Path, project_root: Path,
            overwrite: bool = False) -> dict:
    design = load_json(design_path)
    state_path, state = _load_workflow_state(strategy_path)
    _validate_backtest_data_source(state, project_root)
    candidate_to_retire, candidate_hash = _validate_user_managed_publish(
        design, state, project_root)
    canonical_payload = strategy_path.read_bytes()
    canonical_source = canonical_payload.decode("utf-8-sig")
    strategy_id = design["strategy_id"]

    # R4 third gate, re-verified at publication time: a dual-target source
    # consuming get_history(is_dict=True) must hold a runtime-shape fixture
    # PASS bound to the exact canonical hash being published.
    if requires_runtime_shape_fixture(design, canonical_source):
        if state.get("runtime_shape_fixture_status") != "PASS":
            raise ValueError(
                "agent-first runtime-shape fixture PASS is required before "
                "publication; run prepare_user_backtest_candidate.py or the "
                "fixture gate for this canonical source")
        canonical_digest = hashlib.sha256(canonical_payload).hexdigest()
        if state.get("runtime_shape_fixture_source_sha256") != canonical_digest:
            raise ValueError(
                "canonical source changed after the runtime-shape fixture PASS; "
                "the old fixture evidence is STALE, rerun the fixture gate")
        # Audit closure: the recorded fixture report must still exist, match
        # its recorded SHA-256, still say PASS, and point at this canonical
        # source file.
        report_path = strategy_path.parent / "runtime_shape_fixture_report.json"
        if not report_path.exists():
            raise ValueError(
                f"runtime-shape fixture report is missing: {report_path}; "
                "rerun the fixture gate")
        actual_report_sha = sha256_path(report_path)
        if actual_report_sha != state.get("runtime_shape_fixture_report_sha256"):
            raise ValueError(
                "runtime-shape fixture report changed after it was recorded; "
                "the fixture evidence is not trustworthy, rerun the fixture gate")
        fixture_report = load_json(report_path)
        if fixture_report.get("status") != "PASS":
            raise ValueError(
                "runtime-shape fixture report no longer records PASS; "
                "rerun the fixture gate")
        recorded_source = fixture_report.get("source")
        if recorded_source and Path(recorded_source).resolve() != strategy_path.resolve():
            raise ValueError(
                f"runtime-shape fixture report was produced for {recorded_source}, "
                f"not the canonical source {strategy_path}")

    final_targets: dict[str, Path] = {}
    strategies_dir = project_root / "quantstudio" / "backtest" / "strategies"
    if "quantstudio" in design["targets"]:
        # Chinese naming contract (2026-08-22): the formal local file is
        # <strategy_name>.py (Chinese, no ASCII suffix); strategy_id remains
        # the ASCII machine identifier.
        final_targets["quantstudio"] = (
            strategies_dir / published_quantstudio_filename(design)
        )
        conflicts = strategy_name_conflict_errors(design, strategies_dir)
        if conflicts:
            raise ValueError(
                "design gate failed: " + "; ".join(item["message"] for item in conflicts))
    if "ptrade" in design["targets"]:
        final_targets["ptrade"] = project_root / "ptrade" / f"{strategy_id}_ptrade.py"

    allow_overwrite = overwrite or design.get("output", {}).get("overwrite") is True
    existing = [path for path in final_targets.values() if path.exists()]
    if existing and not allow_overwrite:
        raise FileExistsError(f"target exists and overwrite is false: {existing}")

    # R6 is deliberately file-based: generate every selected target in staging first,
    # then read and validate those generated artifacts. Dual consistency is evaluated
    # only when both selected target files physically exist.
    with tempfile.TemporaryDirectory(
        prefix=".dual_target_stage_", dir=str(strategy_path.parent)
    ) as stage_dir_text:
        stage_dir = Path(stage_dir_text)
        staged: dict[str, Path] = {}
        for target_name in final_targets:
            staged_path = stage_dir / f"{strategy_id}_{target_name}.py"
            staged_path.write_bytes(canonical_payload)
            staged[target_name] = staged_path

        local_path = staged.get("quantstudio")
        ptrade_path = staged.get("ptrade")
        if local_path is None:
            raise ValueError("QuantStudio target is required for agent-first publication")
        local_source = local_path.read_text(encoding="utf-8-sig")

        local_validation = validate_strategy(
            design, local_source, str(local_path), "quantstudio",
            strategies_dir=strategies_dir)
        write_json(strategy_path.parent / "local_validation_report.json", local_validation)
        if local_validation["status"] != "PASS":
            raise ValueError(
                "QuantStudio generated-target validation blocked publication with "
                f"{local_validation['block_count']} BLOCK issue(s)")

        if ptrade_path is not None:
            ptrade_source = ptrade_path.read_text(encoding="utf-8-sig")
            ptrade_validation = validate_strategy(
                design, ptrade_source, str(ptrade_path), "ptrade",
                strategies_dir=strategies_dir)
            if ptrade_validation["status"] != "PASS":
                raise ValueError(
                    "PTrade generated-target validation blocked publication with "
                    f"{ptrade_validation['block_count']} BLOCK issue(s)")
            consistency = compare_sources(local_source, ptrade_source, design)
            consistency["comparison_phase"] = "post_generation_staging"
            consistency["staged_targets_exist_before_comparison"] = all(
                path.exists() for path in staged.values())
            if consistency["status"] != "PASS":
                raise ValueError("dual-platform semantic consistency validation failed")
        else:
            ptrade_validation = _not_applicable_report(
                strategy_id, str(strategy_path), "ptrade",
                "design targets exclude ptrade; no PTrade artifact was generated",
            )
            consistency = {
                "report_version": "1.1",
                "strategy_id": strategy_id,
                "status": "NOT_APPLICABLE",
                "comparison_phase": "NOT_APPLICABLE",
                "staged_targets_exist_before_comparison": True,
                "reason": "single-target QuantStudio publication",
                "issues": [],
            }

        write_json(strategy_path.parent / "ptrade_validation_report.json", ptrade_validation)
        write_json(strategy_path.parent / "dual_consistency_report.json", consistency)
        for target_name, final_path in final_targets.items():
            _atomic_write(final_path, staged[target_name].read_bytes())

    candidate_removed = False
    if candidate_to_retire is not None:
        if sha256_path(candidate_to_retire) != candidate_hash:
            raise ValueError("candidate changed during R6 publication; cleanup blocked")
        candidate_to_retire.unlink()
        candidate_removed = True

    digest = hashlib.sha256(canonical_payload).hexdigest()
    state.update({
        "stage": "PUBLISHED",
        "publish_status": "PASS",
        "local_validation_status": local_validation["status"],
        "ptrade_validation_status": ptrade_validation["status"],
        "dual_consistency_status": consistency["status"],
        "dual_consistency_phase": consistency["comparison_phase"],
        "quantstudio_output_status": "GENERATED",
        "ptrade_output_status": ("GENERATED" if "ptrade" in final_targets else "NOT_GENERATED"),
        "canonical_sha256": digest,
        "candidate_status": ("PROMOTED" if candidate_to_retire is not None else state.get("candidate_status")),
        "candidate_removed": candidate_removed,
    })
    write_json(state_path, state)

    report = {
        "report_version": "2.1",
        "strategy_id": strategy_id,
        "status": "PASS",
        # Static profile PASS is portability evidence only. Broker runtime
        # verification requires customer-supplied real-platform evidence and
        # must never be implied by this report.
        "ptrade_profile_validation_status": (
            "PTRADE_PROFILE_PASS" if ptrade_validation["status"] == "PASS"
            else ptrade_validation["status"]),
        "ptrade_runtime_validation_status": (
            "PTRADE_BROKER_RUNTIME_NOT_VERIFIED" if "ptrade" in final_targets
            else "NOT_APPLICABLE"),
        "deployment_status": "NOT_DEPLOYABLE",
        "canonical_sha256": digest,
        "identical_source": consistency.get("exact_source_match"),
        "validated_after_target_generation": True,
        "quantstudio_output_status": "GENERATED",
        "ptrade_output_status": ("GENERATED" if "ptrade" in final_targets else "NOT_GENERATED"),
        "candidate_promotion": {
            "mode": validation_mode(design),
            "candidate_sha256": candidate_hash,
            "candidate_removed": candidate_removed,
            "status": ("PROMOTED" if candidate_to_retire is not None else "NOT_APPLICABLE"),
        },
        "targets": [
            {"platform": name, "path": str(path),
             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for name, path in final_targets.items()
        ],
        "local_validation": local_validation,
        "ptrade_validation": ptrade_validation,
        "dual_consistency": consistency,
        "workflow_stage": state["stage"],
    }
    write_json(strategy_path.parent / "publish_report.json", report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Publish an agent-authored strategy to its validated target mode")
    parser.add_argument("strategy", help="Canonical strategy.py")
    parser.add_argument("--design", required=True, help="agent_strategy_design.json")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = publish(Path(args.strategy), Path(args.design), Path(args.project_root), args.overwrite)
    except Exception as exc:
        print(f"BLOCKED: {exc}")
        return 2
    print(f"PUBLISHED: {report['strategy_id']} sha256={report['canonical_sha256']}")
    print(f"  local={report['local_validation']['status']} ptrade={report['ptrade_validation']['status']} consistency={report['dual_consistency']['status']}")
    for target in report["targets"]:
        print(f"  - {target['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
