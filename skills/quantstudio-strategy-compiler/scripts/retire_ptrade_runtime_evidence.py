#!/usr/bin/env python3
"""Invalidate stale evidence after a real PTrade broker runtime failure.

When the customer supplies a real PTrade exception/traceback for a published
or candidate strategy, every prior PASS becomes stale in one atomic step:

- stage -> PTRADE_RUNTIME_FAIL_RETURN_R1_R4
- ptrade_profile_validation_status -> STALE (old R4 PASS is no longer valid)
- local backtest evidence -> STALE
- formal_publish_allowed -> false
- candidate -> STALE, its hash cleared, the file renamed
  ``*.RETIRED_DO_NOT_UPLOAD`` (kept for audit, never uploadable again)
- published PTrade upload artifact -> retired the same way

After retirement the reusable profile/adapter/Skill rule must be repaired and
both targets regenerated under fresh hashes (see SKILL.md "Runtime failure
repair protocol").

Usage:
    python retire_ptrade_runtime_evidence.py strategy.py --project-root <root> \
        --reason "NameError: name 'set_backtest' is not defined"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_skill_common import load_json, write_json
from user_backtest_flow import (
    ensure_candidate_path_is_safe, load_workflow_state, mark_ptrade_runtime_failure,
    retire_artifact, write_state,
)


def retire_ptrade_runtime_evidence(strategy_path: Path, project_root: Path,
                                   reason: str) -> dict:
    state_path, state = load_workflow_state(strategy_path)
    strategy_id = state.get("strategy_id") or strategy_path.parent.name
    # Chinese naming contract (2026-08-22): candidate filenames carry the
    # Chinese strategy_name. Resolve it from the workspace state first, then
    # the co-located design JSON; fall back to the ASCII strategy_id.
    strategy_name = state.get("strategy_name")
    if not strategy_name:
        design_path = strategy_path.parent / "agent_strategy_design.json"
        if design_path.exists():
            try:
                strategy_name = load_json(design_path).get("strategy_name")
            except Exception:
                strategy_name = None

    retired: list[str] = []
    raw_candidate = state.get("candidate_path")
    if raw_candidate:
        # The candidate path comes from workspace state; re-validate it against
        # the expected project candidate location before touching any file.
        safe_candidate = ensure_candidate_path_is_safe(
            raw_candidate, project_root, strategy_id, strategy_name)
        result = retire_artifact(safe_candidate, must_be_under=safe_candidate.parent)
        if result is not None:
            retired.append(str(result))
    published_ptrade = project_root / "ptrade" / f"{strategy_id}_ptrade.py"
    result = retire_artifact(published_ptrade, must_be_under=project_root / "ptrade")
    if result is not None:
        retired.append(str(result))

    mark_ptrade_runtime_failure(state, reason, retired)
    write_state(state_path, state)

    report = {
        "report_version": "1.0",
        "strategy_id": strategy_id,
        "status": "RETIRED",
        "stage": state["stage"],
        "reason": reason,
        "retired_artifacts": retired,
        "formal_publish_allowed": False,
        "next_step": "Return to R1/R4: repair the reusable profile/adapter/Skill rule, "
                     "regenerate both targets, and rerun all gates with fresh hashes.",
    }
    write_json(strategy_path.parent / "ptrade_runtime_retirement.json", report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Retire stale PTrade evidence after a broker runtime failure")
    parser.add_argument("strategy", help="Canonical strategy.py (workspace parent holds workspace_state.json)")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--reason", required=True, help="Verbatim customer-supplied runtime failure")
    args = parser.parse_args(argv)
    try:
        report = retire_ptrade_runtime_evidence(
            Path(args.strategy), Path(args.project_root), args.reason)
    except Exception as exc:
        print(f"BLOCKED: {exc}")
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
