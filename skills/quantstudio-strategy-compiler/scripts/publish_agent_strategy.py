#!/usr/bin/env python3
"""Gate, validate, and atomically publish target-aware strategy code."""
from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

from agent_skill_common import load_json, write_json
from validate_agent_strategy import validate_strategy
from validate_dual_consistency import compare_sources


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
    canonical_payload = strategy_path.read_bytes()
    canonical_source = canonical_payload.decode("utf-8-sig")
    strategy_id = design["strategy_id"]

    final_targets: dict[str, Path] = {}
    if "quantstudio" in design["targets"]:
        final_targets["quantstudio"] = (
            project_root / "quantstudio" / "backtest" / "strategies"
            / f"{strategy_id}_quantstudio.py"
        )
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
            design, local_source, str(local_path), "quantstudio")
        write_json(strategy_path.parent / "local_validation_report.json", local_validation)
        if local_validation["status"] != "PASS":
            raise ValueError(
                "QuantStudio generated-target validation blocked publication with "
                f"{local_validation['block_count']} BLOCK issue(s)")

        if ptrade_path is not None:
            ptrade_source = ptrade_path.read_text(encoding="utf-8-sig")
            ptrade_validation = validate_strategy(
                design, ptrade_source, str(ptrade_path), "ptrade")
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
    })
    write_json(state_path, state)

    report = {
        "report_version": "2.1",
        "strategy_id": strategy_id,
        "status": "PASS",
        "canonical_sha256": digest,
        "identical_source": consistency.get("exact_source_match"),
        "validated_after_target_generation": True,
        "quantstudio_output_status": "GENERATED",
        "ptrade_output_status": ("GENERATED" if "ptrade" in final_targets else "NOT_GENERATED"),
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
