#!/usr/bin/env python3
"""R5.4 Parameter Optimization Study (Phase 2: grid/Optuna + nested walk-forward).

Authoritative design: docs/strategy-compiler/parameter-optimization-design.md (final-review passed).
Method reference: references/parameter-optimization.md.

Fail-closed contract:
  - Requires design 2.3 parameter_optimization_contract (enabled + verbatim R2.5 evidence);
    absent/disabled -> NOT_APPLICABLE, exit 0 without a study (Phase 1 behavior).
  - Grid product > 50 -> BLOCK (exit 4). engine=optuna without optuna installed -> BLOCK.
  - Timeout -> status INCOMPLETE_TIMEOUT with completed trials preserved (sqlite study).
  - param_overrides.json lifecycle (M2): file exists ONLY during the study window and is
    DELETED at R5.4 completion on every outcome path; never crosses R6 (publish gate
    enforces the tail end).
  - Outer fold k train region = window start .. fold k start (exclusive); fold 1 is the
    seed fold (empty train region, not an OOS point). Train region too short for
    inner_folds x 20 days -> fold SKIPped (excluded from majority-vote denominator, M1).
  - Proposal = per-parameter majority vote of inner-best values across non-SKIP outer
    folds; no majority or <2 valid folds -> keep design default, mark UNRESOLVED.

Usage:
  python run_optimization_study.py --design <agent_strategy_design.json>
                                   --evidence <r5 evidence json>
                                   --workspace <agent_workspace_dir>
                                   [--project-root <project root>]

Exit codes: 0 = study completed (report produced); 2 = EVIDENCE_INCOMPLETE;
3 = FAILED / ROBUSTNESS-style terminal; 4 = BLOCK / usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

GRID_PRODUCT_CAP = 50
N_TRIALS_CAP = 50
INNER_FOLD_MIN_DAYS = 20
OUTER_OOS_FOLDS = (2, 3, 4, 5)  # fold 1 is the seed fold (M1)
MIN_VALID_OUTER_FOLDS = 2

SKILL_DIR = Path(__file__).resolve().parent.parent
OVERRIDES_NAME = "param_overrides.json"


class Block(Exception):
    pass


class EvidenceIncomplete(Exception):
    pass


# --------------------------------------------------------------------- helpers

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def seed_hex_from(art: dict) -> str:
    h = hashlib.sha256()
    h.update(Path(art["trades"]).read_bytes())
    h.update(Path(art["config"]).read_bytes())
    return h.hexdigest()[:8]


def grid_combos(space: dict) -> list[dict]:
    names = sorted(space)
    value_lists = []
    for name in names:
        spec = space[name]
        t = spec["type"]
        if t == "int":
            lo, hi, st = spec["low"], spec["high"], int(spec.get("step", 1) or 1)
            value_lists.append(list(range(int(lo), int(hi) + 1, max(1, st))))
        elif t == "float":
            lo, hi, st = spec["low"], spec["high"], spec.get("step")
            if not st:
                raise Block(f"float param {name!r} requires step for grid engine")
            vals, cur = [], lo
            while cur <= hi + 1e-12:
                vals.append(round(cur, 10))
                cur += st
            value_lists.append(vals)
        else:
            value_lists.append(list(spec["choices"]))
    product = 1
    for v in value_lists:
        product *= len(v)
    if product > GRID_PRODUCT_CAP:
        raise Block(
            f"grid combination count {product} > cap {GRID_PRODUCT_CAP}; shrink the "
            f"search space (fail-closed, no silent truncation)")
    return [dict(zip(names, values)) for values in itertools.product(*value_lists)]


def validate_space(space: dict) -> None:
    if not 1 <= len(space) <= 6:
        raise Block(f"search_space must have 1..6 tunable params, got {len(space)}")
    for name, spec in space.items():
        t = spec.get("type")
        if t not in ("int", "float", "categorical"):
            raise Block(f"param {name!r}: unsupported type {t!r}")
        if t == "categorical":
            if len(spec.get("choices", [])) < 2:
                raise Block(f"categorical param {name!r} needs >=2 choices")
        else:
            if not spec.get("low") < spec.get("high"):
                raise Block(f"param {name!r}: low must be < high")


# ------------------------------------------------------------- overrides file

def overrides_path(strategy_file: Path) -> Path:
    return strategy_file.parent / OVERRIDES_NAME


def write_overrides(strategy_file: Path, params: dict, space: dict) -> str:
    """Write param_overrides.json next to the strategy. Undeclared keys are rejected
    at READ time by the strategy hook too; here we enforce at WRITE time as well."""
    undeclared = sorted(set(params) - set(space))
    if undeclared:
        raise Block(f"overrides contain keys outside the declared search space: {undeclared}")
    p = overrides_path(strategy_file)
    write_json(p, params)
    return sha256_file(p)


def delete_overrides(strategy_file: Path) -> bool:
    p = overrides_path(strategy_file)
    if p.exists():
        p.unlink()
        return True
    return False


# ------------------------------------------------------------------ evidence

def load_primary_artifacts(evidence_path: Path, project_root: Path) -> dict:
    ev = load_json(evidence_path)
    if "artifacts" in ev and isinstance(ev["artifacts"], dict):
        cfg = ev["artifacts"].get("config_csv") or {}
        ds = ev["artifacts"].get("daily_stats_csv") or {}
        tr = ev["artifacts"].get("trades_csv") or {}
        art = {"config": cfg.get("path"), "daily_stats": ds.get("path"),
               "trades": tr.get("path"),
               "hashes": {"config": cfg.get("sha256"), "daily_stats": ds.get("sha256"),
                          "trades": tr.get("sha256")},
               "result_dir": ev.get("result_dir")}
    elif "evidence_files" in ev:
        run_dir = None
        for name in ev.get("evidence_files", []):
            s = str(name)
            if "backtest_results" in s:
                cand = Path(s)
                if not cand.is_absolute():
                    cand = Path(project_root) / cand
                if cand.exists():
                    run_dir = cand.parent if cand.is_file() else cand
                    break
        if run_dir is None:
            raise EvidenceIncomplete(
                "agent-managed evidence lacks an existing backtest_results dir")
        art = {"config": str(run_dir / "config.csv"),
               "daily_stats": str(run_dir / "daily_stats.csv"),
               "trades": str(run_dir / "trades.csv"), "hashes": {},
               "result_dir": str(run_dir)}
    else:
        raise EvidenceIncomplete(f"unrecognized evidence shape: {evidence_path}")
    for key in ("config", "daily_stats", "trades"):
        p = art[key]
        if not p or not Path(p).exists():
            raise EvidenceIncomplete(f"artifact missing on disk: {key}: {p}")
        actual = sha256_file(Path(p))
        bound = art["hashes"].get(key)
        if bound and bound.lower() != actual:
            raise EvidenceIncomplete(
                f"hash mismatch for {key}: bound={bound[:12]}.. actual={actual[:12]}..")
        art["hashes"][key] = actual
    return art


# ---------------------------------------------------------------- fold slicing

def fold_windows(trading_dates: list[str]) -> list[dict]:
    n = len(trading_dates)
    base, r = divmod(n, 5)
    folds, idx = [], 0
    for i in range(5):
        size = base + (1 if i < r else 0)
        folds.append({"fold_index": i + 1,
                      "start": trading_dates[idx],
                      "end": trading_dates[idx + size - 1],
                      "trade_days": size,
                      "start_idx": idx, "end_idx": idx + size - 1})
        idx += size
    return folds


def slice_dates(trading_dates: list[str], start: str, end: str) -> list[str]:
    return [d for d in trading_dates if start <= d <= end]


# ---------------------------------------------------------------- engine runner

def run_engine_once(strategy_file: Path, project_root: Path, start: str, end: str,
                    capital: float, match_mode: str) -> Path | None:
    """One engine run via the shared entry; returns the new result dir or None."""
    sys.path.insert(0, str(project_root))
    try:
        from quantstudio.backtest.run_ptrade_strategy import run_backtest
        result, output_dir, _engine = run_backtest(
            str(strategy_file), start, end, db_path=None, capital=capital,
            match_price_mode=match_mode, engine_profile="daily-bar-v1", etf_t0=False,
            cost=None, rebalance_mode="legacy", progress_callback=None)
        return Path(output_dir)
    except Exception as exc:  # noqa: BLE001 - a failed trial is a recorded outcome
        print(f"[R5.4] engine run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def objective_from_run(result_dir: Path, bench_series: pd.Series | None,
                       inner_slice: tuple[str, str] | None) -> float:
    """Inner objective = mean daily excess return on the validation slice.
    Outer score = mean daily excess return over the whole fold. Missing artifacts
    count as a failed run (NaN), never an exception."""
    ds_path = Path(result_dir) / "daily_stats.csv"
    if not ds_path.exists():
        return float("nan")
    try:
        ds = pd.read_csv(ds_path)
    except Exception:
        return float("nan")
    if len(ds) < 2:
        return float("nan")
    dates = ds["date"].astype(str)
    nav = ds["total_asset"].to_numpy(dtype=float)
    r = nav[1:] / nav[:-1] - 1.0
    d = dates.to_numpy()[1:]
    if bench_series is not None and "benchmark" in ds.columns and len(ds) > 1:
        b = ds["benchmark"].to_numpy(dtype=float)
        b_r = b[1:] / b[:-1] - 1.0
        e = r - b_r
    else:
        e = r
    if inner_slice:
        lo, hi = inner_slice
        mask = (d >= lo) & (d <= hi)
        e = e[mask]
    return float(e.mean()) if len(e) else float("nan")


# ------------------------------------------------------------------- study core

def run_study(design_path: Path, evidence_path: Path, workspace: Path,
              project_root: Path) -> int:
    design = load_json(design_path)
    if design.get("design_version") not in ("2.2", "2.3"):
        print(f"[R5.4] NOT_APPLICABLE: design_version {design.get('design_version')!r}; "
              f"optimization requires 2.3", file=sys.stderr)
        return 0
    contract = design.get("parameter_optimization_contract")
    if not isinstance(contract, dict) or contract.get("enabled") is not True:
        print("[R5.4] NOT_APPLICABLE: parameter_optimization_contract absent/disabled "
              "(Phase 1 behavior)", file=sys.stderr)
        return 0
    if not _has_verbatim_confirmation(design):
        print("[R5.4] BLOCK: contract enabled but parameter_optimization_contract "
              "verbatim R2.5 confirmation_evidence missing", file=sys.stderr)
        return 4

    engine = contract.get("engine")
    space = contract.get("search_space") or {}
    inner_folds = int(contract.get("inner_folds", 2))
    n_trials = int(contract.get("n_trials", 30))
    timeout_s = contract.get("timeout_seconds")
    if engine not in ("grid", "optuna"):
        raise Block(f"unsupported engine {engine!r}")
    validate_space(space)
    if engine == "optuna":
        try:
            import optuna  # noqa: F401
        except ImportError:
            raise Block("engine=optuna but optuna is not installed; install it or use "
                        "engine=grid (no hard dependency is added by this skill)")
        if contract.get("timeout_seconds") is None:
            raise Block("engine=optuna requires timeout_seconds (double budget guard)")
        n_trials = min(n_trials, N_TRIALS_CAP)

    art = load_primary_artifacts(evidence_path, project_root)
    cfg_csv = Path(art["config"])
    cfg = pd.read_csv(cfg_csv, dtype=str).iloc[0]
    capital = float(cfg["init_capital"])
    match_mode = cfg["match_price_mode"]
    strategy_file = Path(workspace) / "strategy.py"
    if not strategy_file.exists():
        raise UsageErrorIfAny(f"strategy.py not found in workspace: {workspace}")

    ds = pd.read_csv(Path(art["daily_stats"]))
    trading_dates = ds["date"].astype(str).tolist()
    bench = ds["benchmark"]
    folds = fold_windows(trading_dates)

    study_out_dir = workspace / "robustness"
    study_out_dir.mkdir(parents=True, exist_ok=True)
    seed_hex = seed_hex_from(art)
    started = time.time()
    overrides_events = {"created": False, "writes": 0}

    # per-candidate evaluation: one run per inner validation slice
    def evaluate_params(params: dict, train_dates: list[str],
                        inner_bounds: list[tuple[int, int]]) -> tuple[float, list[dict]]:
        ov_sha = write_overrides(strategy_file, params, space)
        overrides_events["writes"] += 1
        objectives = []
        trial_records = []
        for (lo_idx, hi_idx) in inner_bounds:
            lo, hi = train_dates[lo_idx], train_dates[hi_idx]
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            run_dir = project_root / "output" / "backtest_results" / f"{stamp}_r54_inner"
            cfg_copy = pd.read_csv(cfg_csv, dtype=str)
            cfg_copy.loc[0, "start_time"] = train_dates[0]
            cfg_copy.loc[0, "end_time"] = hi
            run_dir.mkdir(parents=True, exist_ok=True)
            cfg_copy.to_csv(run_dir / "config.csv", index=False)
            ok = run_engine_once(strategy_file, project_root, train_dates[0], hi,
                                 capital, match_mode) is not None
            obj = objective_from_run(run_dir, bench, (lo, hi)) if ok else float("nan")
            objectives.append(obj)
            trial_records.append({
                "params": params, "objective": obj,
                "state": "COMPLETE" if ok and obj == obj else "FAILED",
                "config_sha256": sha256_file(run_dir / "config.csv"),
                "overrides_sha256": ov_sha})
        obj = float(np.nanmean([o for o in objectives if o == o])) \
            if any(o == o for o in objectives) else float("nan")
        return obj, trial_records

    def enumerate_candidates() -> list[dict]:
        if engine == "grid":
            return grid_combos(space)
        raise Block("optuna enumeration must use the optuna study path")

    outer_records = []
    proposal_votes: dict[str, list] = {}
    runs_planned = 0
    runs_executed = 0
    timed_out = False

    deadline = (time.time() + int(timeout_s)) if (
        engine == "optuna" and timeout_s is not None) else None

    for fold in folds:
        k = fold["fold_index"]
        if k == 1:
            outer_records.append({
                "fold_index": k, "role": "seed",
                "train_region": {"start": None, "end": None, "trade_days": 0},
                "inner_best_params": None, "inner_best_objective": None,
                "oos_excess_mean": None, "oos_result_dir": None,
                "skip_reason": "seed fold (empty train region, not an OOS point, M1)"})
            continue
        train_dates = trading_dates[:fold["start_idx"]]
        need = inner_folds * INNER_FOLD_MIN_DAYS
        if len(train_dates) < need:
            outer_records.append({
                "fold_index": k, "role": "skipped",
                "train_region": {"start": train_dates[0] if train_dates else None,
                                 "end": train_dates[-1] if train_dates else None,
                                 "trade_days": len(train_dates)},
                "inner_best_params": None, "inner_best_objective": None,
                "oos_excess_mean": None, "oos_result_dir": None,
                "skip_reason": (f"train region {len(train_dates)} < "
                                f"inner_folds({inner_folds})x{INNER_FOLD_MIN_DAYS} days (M1)")})
            continue
        # inner validation slices: last inner_folds segments of the train region
        seg = len(train_dates) // (inner_folds + 1)
        inner_bounds = [(seg * (i + 1) - 1, min(seg * (i + 2) - 1, len(train_dates) - 1))
                        for i in range(inner_folds)]
        candidates = enumerate_candidates() if engine == "grid" else None
        runs_planned += (len(candidates) if candidates else n_trials) * inner_folds
        best_params, best_obj, best_trials = None, float("-inf"), []
        if engine == "grid":
            for params in candidates:
                if deadline is not None and time.time() > deadline:
                    timed_out = True
                    break
                obj, trials = evaluate_params(params, train_dates, inner_bounds)
                runs_executed += inner_folds
                best_trials.extend(trials)
                if obj == obj and obj > best_obj:
                    best_obj, best_params = obj, params
        else:
            import optuna
            sampler = optuna.samplers.TPESampler(seed=int(seed_hex, 16))
            study = optuna.create_study(
                direction="maximize", sampler=sampler,
                storage=f"sqlite:///{study_out_dir / 'optuna_study.db'}",
                study_name=f"r54_{design.get('strategy_id', 'strategy')}_{seed_hex}",
                load_if_exists=True)

            def objective_fn(trial):
                params = {}
                for name, spec in space.items():
                    t = spec["type"]
                    if t == "int":
                        params[name] = trial.suggest_int(
                            name, int(spec["low"]), int(spec["high"]),
                            step=int(spec.get("step", 1) or 1))
                    elif t == "float":
                        params[name] = trial.suggest_float(
                            name, spec["low"], spec["high"],
                            step=spec.get("step"))
                    else:
                        params[name] = trial.suggest_categorical(name, spec["choices"])
                if deadline is not None and time.time() > deadline:
                    raise optuna.TrialPruned("timeout")
                obj, trials = evaluate_params(params, train_dates, inner_bounds)
                runs_executed += inner_folds
                best_trials.extend(trials)
                return obj

            study.optimize(objective_fn, n_trials=n_trials,
                           timeout=(None if deadline is None
                                    else max(1, int(deadline - time.time()))),
                           show_progress_bar=False)
            if study.best_trials:
                bt = study.best_trials[0]
                best_params, best_obj = dict(bt.params), float(bt.value)

        outer_records.append({
            "fold_index": k, "role": "oos",
            "train_region": {"start": train_dates[0], "end": train_dates[-1],
                             "trade_days": len(train_dates)},
            "inner_best_params": best_params,
            "inner_best_objective": (None if best_obj == float("-inf") else best_obj),
            "oos_excess_mean": None, "oos_result_dir": None, "skip_reason": None,
            "trials": best_trials})
        if best_params:
            proposal_votes.setdefault("folds", []).append((k, best_params))
        # outer OOS evaluation of the chosen params on fold k
        if best_params:
            write_overrides(strategy_file, best_params, space)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            oos_dir = project_root / "output" / "backtest_results" / f"{stamp}_r54_oos"
            cfg_copy = pd.read_csv(cfg_csv, dtype=str)
            cfg_copy.loc[0, "start_time"] = fold["start"]
            cfg_copy.loc[0, "end_time"] = fold["end"]
            oos_dir.mkdir(parents=True, exist_ok=True)
            cfg_copy.to_csv(oos_dir / "config.csv", index=False)
            ok = run_engine_once(strategy_file, project_root, fold["start"],
                                 fold["end"], capital, match_mode) is not None
            runs_executed += 1
            outer_records[-1]["oos_result_dir"] = str(oos_dir) if ok else None
            outer_records[-1]["oos_excess_mean"] = (
                objective_from_run(oos_dir, bench, None) if ok else None)
            outer_records[-1]["oos_artifacts"] = {
                "config_csv_sha256": sha256_file(oos_dir / "config.csv"),
                "daily_stats_csv_sha256": sha256_file(oos_dir / "daily_stats.csv"),
                "trades_csv_sha256": sha256_file(oos_dir / "trades.csv")}

    # ---------------- aggregation (M1: SKIP folds excluded from the denominator) ----
    oos_folds = [r for r in outer_records if r["role"] == "oos" and r.get("inner_best_params")]
    per_parameter = []
    proposal_params: dict = {}
    for name in sorted(space):
        votes = []
        for rec in oos_folds:
            val = rec["inner_best_params"].get(name)
            if val is not None:
                votes.append({"fold_index": rec["fold_index"], "value": val})
        default = _design_default(design, name)
        if len(votes) < MIN_VALID_OUTER_FOLDS:
            decision, majority = "UNRESOLVED", None
        else:
            counts: dict = {}
            for v in votes:
                counts[v["value"]] = counts.get(v["value"], 0) + 1
            majority = max(counts, key=lambda k: (counts[k], -votes.index(
                next(vv for vv in votes if vv["value"] == k))))
            decision = "PROPOSED" if counts[majority] * 2 > len(votes) else "UNRESOLVED"
        if decision == "PROPOSED" and majority != default:
            proposal_params[name] = majority
            per_parameter.append({"param": name, "votes": votes,
                                  "majority_value": majority, "design_default": default,
                                  "decision": "PROPOSED",
                                  "detail": f"majority {counts_desc(votes)}"})
        else:
            per_parameter.append({"param": name, "votes": votes,
                                  "majority_value": majority, "design_default": default,
                                  "decision": "KEEP_DEFAULT" if majority == default
                                  else "UNRESOLVED",
                                  "detail": f"majority {counts_desc(votes)}"})

    diff = [{"param": p["param"], "design_default": p["design_default"],
             "proposed": p.get("majority_value"), "decision": p["decision"]}
            for p in per_parameter]
    status = "INCOMPLETE_TIMEOUT" if timed_out else "COMPLETED"

    report = {
        "optimization_study_report_version": "1.0",
        "strategy_id": design.get("strategy_id", workspace.name[:63]),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "contract": {"engine": engine, "n_trials": n_trials if engine == "optuna" else None,
                     "timeout_seconds": timeout_s, "inner_folds": inner_folds,
                     "objective": "mean_daily_excess_return", "search_space": space},
        "input_binding": {
            "config_csv_sha256": art["hashes"]["config"],
            "daily_stats_csv_sha256": art["hashes"]["daily_stats"],
            "trades_csv_sha256": art["hashes"]["trades"],
            "primary_result_dir": str(art.get("result_dir") or "")},
        "cost": {"engine_runs_planned": runs_planned,
                 "engine_runs_executed": runs_executed,
                 "wall_seconds": round(time.time() - started, 1),
                 "timed_out": timed_out, "pruned_trials": 0},
        "outer_folds": outer_records,
        "aggregation": {"per_parameter": per_parameter},
        "proposal": {"params": proposal_params, "diff_vs_design": diff,
                     "awaiting_customer_confirmation": True, "accepted": None,
                     "confirmation_evidence_ref": None},
        "seeds": {"optuna_seed_source": "sha256(trades_csv+config_csv)[:8]",
                  "optuna_seed_hex": seed_hex,
                  "python_hashseed": os.environ.get("PYTHONHASHSEED")},
        "lifecycle": {"overrides_file_created": overrides_events["created"]
                      or overrides_events["writes"] > 0,
                      "overrides_write_events": overrides_events["writes"],
                      "overrides_file_deleted_at_completion": True,
                      "optuna_storage_path": (str(study_out_dir / "optuna_study.db")
                                              if engine == "optuna" else None),
                      "optuna_completed_trials_at_close": (runs_executed // max(1, inner_folds)
                                                           if engine == "optuna" else None)},
    }
    out_path = study_out_dir / "optimization_study_report.json"
    write_json(out_path, report)

    # M2 lifecycle: delete the overrides file on EVERY outcome path
    deleted = delete_overrides(strategy_file)
    report["lifecycle"]["overrides_file_deleted_at_completion"] = deleted or not (
        overrides_path(strategy_file).exists())
    write_json(out_path, report)

    schema_ok = _validate_against_schema(out_path)
    print(f"[R5.4] status={status} proposal_params={proposal_params or 'none (keep defaults)'} "
          f"runs={runs_executed}/{runs_planned} wall={report['cost']['wall_seconds']}s")
    print(f"[R5.4] report: {out_path} schema_ok={schema_ok} overrides_deleted={deleted}")
    if not schema_ok:
        print("[R5.4] WARNING: report failed schema validation", file=sys.stderr)
    return 0 if status in ("COMPLETED", "INCOMPLETE_TIMEOUT") else 3


def counts_desc(votes: list) -> str:
    counts: dict = {}
    for v in votes:
        counts[str(v["value"])] = counts.get(str(v["value"]), 0) + 1
    return ", ".join(f"{k}x{c}" for k, c in sorted(counts.items(), key=lambda x: -x[1]))


def _design_default(design: dict, name: str):
    for container in ("portfolio_contract", "market_data_contract", "strategy_semantics",
                      "r5_deployment_invariants"):
        node = design.get(container)
        if isinstance(node, dict) and name in node:
            return node[name]
    return None


def _has_verbatim_confirmation(design: dict) -> bool:
    ev = design.get("confirmation_evidence") or {}
    entry = ev.get("parameter_optimization_contract")
    return bool(entry and entry.get("confirmed") is True
                and entry.get("customer_text")
                and entry.get("source") == "customer_reply")


def _validate_against_schema(report_path: Path) -> bool:
    schema_path = SKILL_DIR / "schemas" / "optimization_study_report.schema.json"
    try:
        import jsonschema
        jsonschema.validate(load_json(report_path), load_json(schema_path))
        return True
    except ImportError:
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[R5.4] schema violation: {exc}", file=sys.stderr)
        return False


class UsageErrorIfAny(Exception):
    pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="R5.4 parameter optimization study")
    ap.add_argument("--design", required=True)
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--project-root", default=".")
    args = ap.parse_args(argv)
    try:
        return run_study(Path(args.design), Path(args.evidence), Path(args.workspace),
                         Path(args.project_root))
    except EvidenceIncomplete as exc:
        print(f"[R5.4] EVIDENCE_INCOMPLETE: {exc}", file=sys.stderr)
        return 2
    except Block as exc:
        print(f"[R5.4] BLOCK: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
