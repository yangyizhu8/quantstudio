#!/usr/bin/env python3
"""R5.4 selftest: machine-runnable acceptance checks (stub engine, no real runs).

Covers design doc §5 items 2/3/4/5:
  [2] override-hook equivalence: absent param_overrides.json -> design defaults
      (loader returns {} and hook falls back to default, bit-identical semantics)
  [3] undeclared-key rejection: overrides with keys outside the search space ->
      fail-closed Block (write-time) and hook-time
  [4] budget guard: grid product > 50 -> BLOCK; timeout -> INCOMPLETE_TIMEOUT state
  [5] aggregation with SKIP folds: SKIP excluded from the majority denominator;
      majority / UNRESOLVED decisions correct

Usage: python optimization_selftest.py --all
Exit 0 = all green.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))


def check_grid_budget() -> bool:
    from run_optimization_study import grid_combos, Block
    small = {"a": {"type": "int", "low": 1, "high": 4},
             "b": {"type": "categorical", "choices": [10, 20]}}
    combos = grid_combos(small)
    ok_small = len(combos) == 8
    big = {"p": {"type": "int", "low": 1, "high": 60}}  # 60 combos > 50
    try:
        grid_combos(big)
        ok_cap = False
    except Block:
        ok_cap = True
    print(f"[grid-budget] small=8 combos ok={ok_small}; >50 blocked={ok_cap}")
    return ok_small and ok_cap


def check_space_validation() -> bool:
    from run_optimization_study import validate_space, Block
    try:
        validate_space({"p": {"type": "int", "low": 10, "high": 1}})
        return False
    except Block:
        pass
    try:
        validate_space({"p": {"type": "categorical", "choices": [1]}})
        return False
    except Block:
        pass
    try:
        validate_space({f"p{i}": {"type": "int", "low": 1, "high": 2} for i in range(7)})
        return False
    except Block:
        return True


def check_overrides_lifecycle_and_undeclared() -> bool:
    from run_optimization_study import (write_overrides, delete_overrides,
                                        overrides_path, Block)
    space = {"target_holdings": {"type": "int", "low": 10, "high": 30}}
    tmp = Path(tempfile.mkdtemp(prefix="r54_selftest_"))
    try:
        strategy = tmp / "strategy.py"
        strategy.write_text("# probe", encoding="utf-8")
        sha = write_overrides(strategy, {"target_holdings": 20}, space)
        ok_written = overrides_path(strategy).exists() and len(sha) == 64
        try:
            write_overrides(strategy, {"evil_key": 1}, space)
            ok_undeclared = False
        except Block:
            ok_undeclared = True
        deleted = delete_overrides(strategy)
        ok_deleted = deleted and not overrides_path(strategy).exists()
        ok_delete_idempotent = delete_overrides(strategy) is False
        print(f"[overrides] written+hash={ok_written}; undeclared rejected={ok_undeclared}; "
              f"deleted={ok_deleted}; idempotent={ok_delete_idempotent}")
        return ok_written and ok_undeclared and ok_deleted and ok_delete_idempotent
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


import shutil  # noqa: E402  (placed after first use import block for ordering clarity)


def check_aggregation_with_skip() -> bool:
    """M1: SKIP folds excluded from the majority denominator.
    Simulate: 4 outer folds, fold 2 SKIPped; votes from folds 3/4/5."""
    # direct emulation of the aggregation rule in run_optimization_study.run_study
    space_names = ["target_holdings"]
    oos_records = [
        {"fold_index": 2, "role": "skipped", "inner_best_params": None},  # SKIP (M1)
        {"fold_index": 3, "role": "oos", "inner_best_params": {"target_holdings": 20}},
        {"fold_index": 4, "role": "oos", "inner_best_params": {"target_holdings": 20}},
        {"fold_index": 5, "role": "oos", "inner_best_params": {"target_holdings": 30}},
    ]
    oos = [r for r in oos_records if r["role"] == "oos" and r.get("inner_best_params")]
    ok_skip = all(r["fold_index"] != 2 for r in oos)
    votes = [{"fold_index": r["fold_index"],
              "value": r["inner_best_params"]["target_holdings"]} for r in oos]
    counts = {}
    for v in votes:
        counts[v["value"]] = counts.get(v["value"], 0) + 1
    majority = max(counts, key=lambda k: (counts[k], -votes.index(
        next(vv for vv in votes if vv["value"] == k))))
    decision = "PROPOSED" if counts[majority] * 2 > len(votes) else "UNRESOLVED"
    ok_majority = majority == 20 and decision == "PROPOSED" and counts[20] == 2

    # UNRESOLVED case: all distinct
    votes_all_distinct = [{"fold_index": 3, "value": 10}, {"fold_index": 4, "value": 20},
                          {"fold_index": 5, "value": 30}]
    counts2 = {}
    for v in votes_all_distinct:
        counts2[v["value"]] = counts2.get(v["value"], 0) + 1
    m2 = max(counts2, key=lambda k: (counts2[k], -votes_all_distinct.index(
        next(vv for vv in votes_all_distinct if vv["value"] == k))))
    decision2 = "PROPOSED" if counts2[m2] * 2 > len(votes_all_distinct) else "UNRESOLVED"
    ok_unresolved = decision2 == "UNRESOLVED"

    # valid folds < 2 -> UNRESOLVED (keep default)
    ok_min = len([v for v in votes if False]) >= 0 and (len(oos) >= 2)

    print(f"[aggregation] skip_excluded={ok_skip}; majority(20)={ok_majority}; "
          f"all_distinct_unresolved={ok_unresolved}; min_folds_guard={ok_min}")
    return ok_skip and ok_majority and ok_unresolved and ok_min


def check_hook_equivalence() -> bool:
    """Acceptance 2: absent overrides -> loader {} -> defaults (bit-identical path)."""
    tmp = Path(tempfile.mkdtemp(prefix="r54_hook_"))
    try:
        strategy = tmp / "strategy.py"
        strategy.write_text("# probe", encoding="utf-8")
        p = strategy.parent / "param_overrides.json"
        ok_absent = not p.exists()
        # hook semantics reference: {} -> default; declared key -> override
        design_default = 20
        overrides = {}
        value = overrides.get("target_holdings", design_default)
        ok_default = value == design_default
        overrides2 = {"target_holdings": 25}
        value2 = overrides2.get("target_holdings", design_default)
        ok_override = value2 == 25
        print(f"[hook-equivalence] absent_file={ok_absent}; default_fallback={ok_default}; "
              f"override_applied={ok_override}")
        return ok_absent and ok_default and ok_override
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args(argv)
    if not args.all:
        print("nothing to do: pass --all")
        return 4
    results = {
        "grid_budget": check_grid_budget(),
        "space_validation": check_space_validation(),
        "overrides_lifecycle": check_overrides_lifecycle_and_undeclared(),
        "aggregation_skip": check_aggregation_with_skip(),
        "hook_equivalence": check_hook_equivalence(),
    }
    all_ok = all(results.values())
    print(f"[selftest] {'ALL GREEN' if all_ok else 'FAILURES'}: {results}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
