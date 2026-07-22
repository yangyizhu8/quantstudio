"""Strategy Compiler orchestrator (PR6b-1, CP7).

End-to-end pipeline entry point: strategy_spec.json → build_strategy_ir →
render (QS + PTrade) → 7 validators → run_card.json.

Design principle (handoff §1 关键决策): the orchestrator is the SINGLE writer
of run_card.json + variant_consistency_report.json. Validators are pure
functions that return result tuples; the orchestrator collects them and maps
each to the run_card schema fields in one place (single source of truth for
field mapping, documented in handoff §2 CP7).

Stage machine (run_card.schema.json `stage` enum):
    SPEC_ONLY → STATIC_VALIDATED → SMOKE_EXECUTED
The `stage` field records which pipeline STEP was reached (not pass/fail — that
is the `status` field). Advancement rules:
  - SPEC_ONLY: schema/contracts validation failed → IR never built, stop here.
  - STATIC_VALIDATED: IR + render + static validators all RAN. Reached whether
    the static validators PASS or BLOCK — the static step executed, so the
    stage reflects that. (A static BLOCK is recorded in `validation.*` and
    `status`; the stage is not rolled back to SPEC_ONLY, which would falsely
    imply IR/render never happened.)
  - SMOKE_EXECUTED: the smoke step RAN. Reached only when static validators
    PASS (a static-failing strategy is not engine-worthy, so smoke is NOT
    attempted). If the capability gate blocks, stage is SMOKE_EXECUTED with
    smoke status BLOCKED (R6: code generated + static passed + execution
    blocked honestly recorded).

Golden protection (contract §6): render() raises on protected IDs; the
orchestrator surfaces this as a top-level error (not a silent run_card).

CLI:
    python -m quantstudio.strategy_compiler.orchestrator <spec.json> [--start <date>] [--end <date>] [--out-dir <dir>]
"""

from __future__ import annotations

import datetime
import hashlib
import json
import platform
import sys
from pathlib import Path
from typing import Any

from .build_strategy_ir import build_strategy_ir
from .contracts import ContractValidationError, validate_strategy_spec
from .ir_nodes import StrategyIR
from .render import GoldenProtectionError, render_ptrade, render_quantstudio
from .validators.check_hard_filters import check_hard_filters
from .validators.compare_strategy_variants import compare_strategy_variants
from .validators.run_smoke_backtest import run_smoke_backtest
from .validators.scan_lookahead import scan_lookahead
from .validators.validate_local_strategy import validate_local_strategy
from .validators.validate_ptrade_portability import validate_ptrade_portability

# Stages (run_card.schema.json `stage` enum subset for PR6b-1; FIDELITY_COMPARED
# is PR6b-2/PR7 scope — never produced here).
_STAGE_SPEC_ONLY = "SPEC_ONLY"
_STAGE_STATIC_VALIDATED = "STATIC_VALIDATED"
_STAGE_SMOKE_EXECUTED = "SMOKE_EXECUTED"

# checkStatus values (run_card.schema.json definitions.checkStatus enum).
_CHECK_PASS = "PASS"
_CHECK_BLOCKED = "BLOCKED"
_CHECK_NOT_RUN = "NOT_RUN"
_CHECK_FAILED = "FAILED"

# Skill version for run_card contract_versions.skill_version.
_SKILL_VERSION = "0.2.0-pr6b1"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _check_status_from_ok(ok: bool, ran: bool = True) -> str:
    """Map a validator ok flag to run_card checkStatus."""
    if not ran:
        return _CHECK_NOT_RUN
    return _CHECK_PASS if ok else _CHECK_BLOCKED


def _collect_violation_strs(violations: list) -> list[str]:
    """Flatten Violation objects to readable strings for run_card evidence."""
    return [str(v) for v in violations]


def orchestrate(
    spec: dict[str, Any],
    *,
    start: str | None = None,
    end: str | None = None,
    out_dir: Path | None = None,
    run_smoke: bool = True,
) -> dict[str, Any]:
    """Run the full pipeline for a strategy spec and write run_card.json.

    Args:
        spec: parsed strategy_spec.json dict.
        start, end: backtest window passed to the smoke engine.
        out_dir: output directory (default: output/generated_strategies/<id>).
        run_smoke: if False, skip the smoke step (stage stays STATIC_VALIDATED).

    Returns:
        The run_card dict that was written.

    Raises:
        GoldenProtectionError: if strategy_id is golden-protected (render aborts).
        ContractValidationError: if the spec fails schema validation (stage
            stays SPEC_ONLY; still writes a run_card recording the failure).
    """
    strategy_id = spec["strategy_id"]
    profile_id = spec.get("engine_profile", {}).get("profile_id", "daily-bar-v1")
    ptrade_profile_id = spec.get("ptrade_profile", {}).get("profile_id", "ptrade-default")

    # Output dir
    if out_dir is None:
        out_dir = Path("output/generated_strategies") / strategy_id
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    created_at = datetime.datetime.now().astimezone().isoformat()
    run_id = f"{strategy_id}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    build_id = hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]

    artifacts: list[dict[str, Any]] = []
    known_limitations: list[str] = []
    warnings_all: list[str] = []

    # ------------------------------------------------------------------
    # Stage 1: SPEC_ONLY — schema + contracts timing validation
    # ------------------------------------------------------------------
    validation: dict[str, str] = {
        "schema": _CHECK_NOT_RUN, "timing": _CHECK_NOT_RUN,
        "hard_filters": _CHECK_NOT_RUN, "api_portability": _CHECK_NOT_RUN,
        "variant_consistency": _CHECK_NOT_RUN,
    }
    stage = _STAGE_SPEC_ONLY
    schema_ok = True

    try:
        validate_strategy_spec(spec)
        validation["schema"] = _CHECK_PASS
    except ContractValidationError as e:
        validation["schema"] = _CHECK_BLOCKED
        schema_ok = False
        known_limitations.append(f"schema/timing validation BLOCKED: {e}")

    # If schema fails, we cannot trust the spec to build IR — write a minimal
    # run_card and stop. (build_strategy_ir on an invalid spec is undefined.)
    if not schema_ok:
        run_card = _build_run_card(
            run_id=run_id, strategy_id=strategy_id, build_id=build_id,
            created_at=created_at, stage=stage, status="BLOCKED",
            spec=spec, ir=None, profile_id=profile_id,
            ptrade_profile_id=ptrade_profile_id, execution_status="BLOCKED",
            artifacts=[], validation=validation, smoke_result=None,
            known_limitations=known_limitations, warnings=warnings_all,
            start=start, end=end,
        )
        _write_run_card(run_card, out_dir)
        return run_card

    # ------------------------------------------------------------------
    # Stage 2: build IR + render (SPEC_ONLY → advancing)
    # ------------------------------------------------------------------
    ir = build_strategy_ir(spec)

    # Persist spec + IR artifacts
    spec_path = out_dir / "strategy_spec.json"
    spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    ir_path = out_dir / "strategy_ir.json"
    ir_path.write_text(json.dumps(ir.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    artifacts.append({"name": "strategy_spec.json", "path": str(spec_path), "sha256": _sha256_file(spec_path)})
    artifacts.append({"name": "strategy_ir.json", "path": str(ir_path), "sha256": _sha256_file(ir_path)})

    # Render both platforms (may raise GoldenProtectionError — surfaced to caller)
    qs_code = render_quantstudio(ir)
    pt_code = render_ptrade(ir)
    qs_path = out_dir / f"{strategy_id}_quantstudio.py"
    pt_path = out_dir / f"{strategy_id}_ptrade.py"
    qs_path.write_text(qs_code, encoding="utf-8")
    pt_path.write_text(pt_code, encoding="utf-8")
    artifacts.append({"name": f"{strategy_id}_quantstudio.py", "path": str(qs_path), "sha256": _sha256_file(qs_path)})
    artifacts.append({"name": f"{strategy_id}_ptrade.py", "path": str(pt_path), "sha256": _sha256_file(pt_path)})

    validation["schema"] = _CHECK_PASS  # contracts.validate_strategy_spec ran above

    # ------------------------------------------------------------------
    # Stage 3: static validators (→ STATIC_VALIDATED)
    # ------------------------------------------------------------------
    # timing = scan_lookahead (lookahead/timing high-risk items)
    ok_look, v_look, w_look = scan_lookahead(ir, qs_code)
    validation["timing"] = _check_status_from_ok(ok_look)
    warnings_all.extend(w_look)
    known_limitations.extend(_collect_violation_strs(v_look))

    # hard_filters
    ok_hf, v_hf, w_hf = check_hard_filters(ir, spec)
    validation["hard_filters"] = _check_status_from_ok(ok_hf)
    warnings_all.extend(w_hf)
    known_limitations.extend(_collect_violation_strs(v_hf))

    # api_portability (validate_local_strategy QS+PTrade + ptrade portability)
    ok_qs, v_qs, w_qs = validate_local_strategy(spec, ir, qs_code, "quantstudio")
    ok_pt, v_pt, w_pt = validate_local_strategy(spec, ir, pt_code, "ptrade-default")
    ok_port, v_port, w_port = validate_ptrade_portability(pt_code, ir, spec)
    port_ok = ok_qs and ok_pt and ok_port
    validation["api_portability"] = _check_status_from_ok(port_ok)
    warnings_all.extend([*w_qs, *w_pt, *w_port])
    known_limitations.extend(_collect_violation_strs([*v_qs, *v_pt, *v_port]))

    # variant_consistency (14-dimension)
    ok_var, v_var, w_var, variant_report = compare_strategy_variants(spec, ir, qs_code, pt_code)
    validation["variant_consistency"] = _check_status_from_ok(ok_var)
    warnings_all.extend(w_var)
    known_limitations.extend(_collect_violation_strs(v_var))

    static_ok = ok_look and ok_hf and port_ok and ok_var
    # Stage advances to STATIC_VALIDATED once the static validators have RUN,
    # regardless of pass/block — the step executed. (status reflects pass/block.)
    stage = _STAGE_STATIC_VALIDATED

    # Write variant_consistency_report.json (single writer rule)
    vc_path = out_dir / "variant_consistency_report.json"
    vc_path.write_text(json.dumps(variant_report, indent=2, ensure_ascii=False), encoding="utf-8")
    artifacts.append({"name": "variant_consistency_report.json", "path": str(vc_path), "sha256": _sha256_file(vc_path)})

    # ------------------------------------------------------------------
    # Stage 4: capability inspection + smoke (→ SMOKE_EXECUTED)
    # ------------------------------------------------------------------
    smoke_result: dict[str, Any] | None = None
    overall_status = "BLOCKED"
    execution_status_for_card = "BLOCKED"

    if static_ok and run_smoke:
        try:
            capability_report = _inspect_capabilities(strategy_id, profile_id)
        except Exception as e:  # pragma: no cover — env-dependent
            capability_report = {"overall_execution_status": "BLOCKED",
                                 "blockers": [f"capability inspection failed: {e}"]}
            warnings_all.append(f"capability inspection error: {e}")

        exec_status = capability_report.get("overall_execution_status", "BLOCKED")
        execution_status_for_card = exec_status

        cap_path = out_dir / "capability_report.json"
        cap_path.write_text(json.dumps(capability_report, indent=2, ensure_ascii=False), encoding="utf-8")
        artifacts.append({"name": "capability_report.json", "path": str(cap_path), "sha256": _sha256_file(cap_path)})

        smoke_status, smoke_result, w_smoke = run_smoke_backtest(
            qs_path, capability_report, start=start, end=end, profile_id=profile_id,
        )
        warnings_all.extend(w_smoke)
        # Stage advances to SMOKE_EXECUTED regardless of PASS/BLOCKED/FAILED —
        # the smoke step RAN (or was honestly blocked by capability), which is
        # the SMOKE_EXECUTED contract.
        stage = _STAGE_SMOKE_EXECUTED
    elif not static_ok:
        # Static failure: smoke intentionally NOT attempted (no point hitting
        # the engine for a strategy that already failed static checks).
        known_limitations.append(
            "smoke backtest skipped: static validation BLOCKED (strategy not engine-worthy)"
        )

    # ------------------------------------------------------------------
    # Assemble run_card.json
    # ------------------------------------------------------------------
    # Overall status: PASS only if all checks PASS AND smoke PASS.
    all_checks_pass = all(v == _CHECK_PASS for v in validation.values())
    if not all_checks_pass:
        overall_status = "BLOCKED"
    elif smoke_result is None:
        overall_status = "PARTIAL"  # static passed, smoke not run
    elif smoke_result.get("status") == "PASS":
        overall_status = "PASS"
    elif smoke_result.get("status") == "BLOCKED":
        overall_status = "BLOCKED"
    else:
        overall_status = "FAILED"

    run_card = _build_run_card(
        run_id=run_id, strategy_id=strategy_id, build_id=build_id,
        created_at=created_at, stage=stage, status=overall_status,
        spec=spec, ir=ir, profile_id=profile_id,
        ptrade_profile_id=ptrade_profile_id, execution_status=execution_status_for_card,
        artifacts=artifacts, validation=validation, smoke_result=smoke_result,
        known_limitations=known_limitations, warnings=warnings_all,
        start=start, end=end,
    )
    _write_run_card(run_card, out_dir)
    return run_card


def _inspect_capabilities(strategy_id: str, profile_id: str) -> dict[str, Any]:
    """Run inspect_capabilities (Skill script) in-process to get capability_report.

    The inspector lives under skills/.../scripts/ (not in the quantstudio
    package). We import it by path to keep it the single capability source.
    """
    import importlib.util
    # The inspector lives under skills/.../scripts/ (not in the quantstudio
    # package). Resolve relative to this file: package is under quantstudio/,
    # skills is a sibling at the repo root.
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "skills" / "quantstudio-strategy-compiler" / "scripts" / "inspect_capabilities.py"
    if not script.exists():
        raise FileNotFoundError(f"inspect_capabilities.py not found at {script}")
    spec = importlib.util.spec_from_file_location("inspect_capabilities", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    from quantstudio._paths import db_path
    return mod.inspect(db_path(), profile_id, strategy_id)


def _build_run_card(
    *, run_id, strategy_id, build_id, created_at, stage, status,
    spec, ir, profile_id, ptrade_profile_id, execution_status,
    artifacts, validation, smoke_result, known_limitations, warnings,
    start, end,
) -> dict[str, Any]:
    """Assemble the run_card.json dict (run_card.schema.json-conformant)."""
    cv = spec.get("contract_versions", {})
    # data_window: use spec-declared window if present, else the passed start/end.
    dw_start = start or spec.get("data_window", {}).get("start")
    dw_end = end or spec.get("data_window", {}).get("end")
    as_of = spec.get("data_window", {}).get("as_of")
    # Fold warnings into approximations as human-readable notes (schema has no
    # top-level warnings array; approximations captures soft notices).
    approximations = list(dict.fromkeys(warnings))  # dedupe preserving order

    import quantstudio
    return {
        "run_card_version": "1.0",
        "run_id": run_id,
        "strategy_id": strategy_id,
        "build_id": build_id,
        "created_at": created_at,
        "stage": stage,
        "status": status,
        "contract_versions": {
            "strategy_spec_version": cv.get("strategy_spec_version", "1.0.0"),
            "engine_semantics_version": cv.get("engine_semantics_version", "1.0.0"),
            "provider_contract_version": cv.get("provider_contract_version", "1.0.0"),
            "security_code_rules_version": cv.get("security_code_rules_version", "1.0.0"),
            "ptrade_profile_version": cv.get("ptrade_profile_version", "1.0.0"),
            "renderer_version": cv.get("renderer_version", "1.0.0"),
            "skill_version": cv.get("skill_version", _SKILL_VERSION),
        },
        "profile": {
            "engine_profile_id": profile_id,
            "ptrade_profile_id": ptrade_profile_id,
            "execution_status": execution_status,
        },
        "data_window": {"start": dw_start, "end": dw_end, "as_of": as_of},
        "artifacts": artifacts,
        "validation": validation,
        "smoke_backtest": smoke_result,
        "fidelity": None,  # PR7 scope (R7 PTrade fidelity compare)
        "approximations": approximations,
        "known_limitations": known_limitations,
        "reproducibility": {
            "python_version": platform.python_version(),
            "quantstudio_version": getattr(quantstudio, "__version__", "0.0.0"),
            "random_seed": None,
            "data_fingerprint": None,
        },
    }


def _write_run_card(run_card: dict, out_dir: Path) -> None:
    path = out_dir / "run_card.json"
    path.write_text(json.dumps(run_card, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Strategy Compiler orchestrator: spec → IR → render → validate → run_card"
    )
    parser.add_argument("spec", help="Path to strategy_spec.json")
    parser.add_argument("--start", default=None, help="Smoke backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="Smoke backtest end date (YYYY-MM-DD)")
    parser.add_argument("--out-dir", default=None, help="Output directory (default: output/generated_strategies/<id>)")
    parser.add_argument("--no-smoke", action="store_true", help="Skip the smoke backtest step")
    args = parser.parse_args(argv)

    spec_path = Path(args.spec)
    if not spec_path.exists():
        print(f"ERROR: spec file not found: {spec_path}", file=sys.stderr)
        return 1
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON in {spec_path}: {e}", file=sys.stderr)
        return 1

    try:
        run_card = orchestrate(
            spec,
            start=args.start, end=args.end,
            out_dir=Path(args.out_dir) if args.out_dir else None,
            run_smoke=not args.no_smoke,
        )
    except GoldenProtectionError as e:
        print(f"ERROR: golden protection — {e}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir) if args.out_dir else Path("output/generated_strategies") / spec["strategy_id"]
    print(f"run_card written: {out_dir / 'run_card.json'}")
    print(f"  stage={run_card['stage']} status={run_card['status']}")
    print(f"  validation={run_card['validation']}")
    if run_card.get("smoke_backtest"):
        print(f"  smoke={run_card['smoke_backtest']['status']}")
    return 0 if run_card["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
