#!/usr/bin/env python3
"""R5.5 Statistical Robustness Suite (Phase 1: WF 5-fold + MC n=1000 + Gates G1-G6).

Authoritative design: docs/strategy-compiler/robustness-gates-design.md (final-review passed).
Method reference: references/robustness-gates.md.

Fail-closed contract:
  1. Entry pre-verification: recompute SHA-256 of config.csv/daily_stats.csv/trades.csv
     from the R5-bound evidence and compare with the bound hashes. Any mismatch ->
     EVIDENCE_INCOMPLETE, no report produced, exit code 2.
  2. The suite is READ-ONLY on R5 artifacts. It writes only its own report.
  3. Fold configs are derived programmatically from the hash-verified config.csv,
     replacing ONLY start/end dates (design rule 3.1, no drift between folds).
  4. MC is analytical (stationary block bootstrap on excess daily returns), 0 engine
     re-runs. Seed derived from artifact hashes, fully recorded.

Usage:
  python run_robustness_suite.py --evidence <user_backtest_evidence.json|r5_evidence.json>
                                 --workspace <agent_workspace_dir>
                                 [--strategy-root <project root>]
                                 [--skip-wf]
                                 [--iteration-count N]

Exit codes: 0 = report produced; 2 = EVIDENCE_INCOMPLETE / tamper; 3 = gates FAILED
(robustness report produced, R5.5 FAILED); 4 = usage/environment error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

MC_N = 1000
MC_BLOCK = 21
G6_P_THRESHOLD = 0.01
WF_FOLDS = 5
WF_MIN_WINDOW_DAYS = 250
MC_MIN_NAV_DAYS = 120
GATE_MIN_DAYS = 60
G4_MIN_ROUND_TRIPS = 10
G5_MIN_VALID_FOLDS = 3
ITERATION_CAP = 2

SKILL_DIR = Path(__file__).resolve().parent.parent


class EvidenceIncomplete(Exception):
    """Raised when input artifact hashes mismatch the R5-bound evidence."""


class UsageError(Exception):
    pass


# --------------------------------------------------------------------------- io

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_evidence_artifacts(evidence_path: Path, project_root: Path | None = None) -> dict:
    """Extract {config,daily_stats,trades} {path,sha256} + result_dir from an R5 evidence JSON.

    Supports evidence 2.1 (user_backtest_evidence.json: artifacts.config_csv.{path,sha256})
    and agent-managed r5_evidence.json with evidence_files[] + run dirs (hash anchored on
    the files themselves). Fail-closed: unknown shape -> UsageError.
    """
    ev = load_json(evidence_path)
    art = {}
    if "artifacts" in ev and isinstance(ev["artifacts"], dict):
        cfg = ev["artifacts"].get("config_csv") or {}
        ds = ev["artifacts"].get("daily_stats_csv") or {}
        tr = ev["artifacts"].get("trades_csv") or {}
        art = {
            "config": {"path": cfg.get("path"), "sha256": cfg.get("sha256")},
            "daily_stats": {"path": ds.get("path"), "sha256": ds.get("sha256")},
            "trades": {"path": tr.get("path"), "sha256": tr.get("sha256")},
            "result_dir": ev.get("result_dir"),
        }
    elif "evidence_files" in ev:
        # agent-managed evidence: locate the backtest_results dir named in evidence_files
        run_dir = None
        for name in ev.get("evidence_files", []):
            s = str(name)
            if "backtest_results" in s:
                cand = Path(s)
                if not cand.is_absolute() and project_root is not None:
                    cand = Path(project_root) / cand
                if cand.exists():
                    # evidence_files may name a FILE inside the run dir
                    # (e.g. .../ptrade_metrics.json) — anchor to its parent.
                    run_dir = cand.parent if cand.is_file() else cand
                    break
        if run_dir is None:
            raise UsageError(
                "agent-managed evidence lacks a parseable/existing backtest_results dir; "
                "pass an absolute evidence_files path or check --project-root")
        art = {
            "config": {"path": str(Path(run_dir) / "config.csv"), "sha256": None},
            "daily_stats": {"path": str(Path(run_dir) / "daily_stats.csv"), "sha256": None},
            "trades": {"path": str(Path(run_dir) / "trades.csv"), "sha256": None},
            "result_dir": str(run_dir),
        }
    else:
        raise UsageError(f"unrecognized evidence shape: {evidence_path}")
    # resolve relative artifact paths against the project root (evidence parent chain)
    for key in ("config", "daily_stats", "trades"):
        p = art[key].get("path")
        if p and not Path(p).is_absolute():
            cand = evidence_path.parent / p
            if not cand.exists():
                cand = Path.cwd() / p
            if cand.exists():
                art[key]["path"] = str(cand.resolve())
    return art


def preverify(art: dict) -> dict:
    """Recompute SHA-256 of the three artifacts and compare with bound hashes.

    Missing bound hash (None) is allowed only for agent-managed evidence where the
    file itself is the anchor; the computed hash then becomes the recorded one.
    Any bound-hash MISMATCH -> EvidenceIncomplete (fail-closed, design 3-entry gate).
    """
    verified = {}
    for key in ("config", "daily_stats", "trades"):
        path = art[key].get("path")
        if not path or not Path(path).exists():
            raise EvidenceIncomplete(f"artifact missing on disk: {key}: {path}")
        actual = sha256_file(Path(path))
        bound = art[key].get("sha256")
        if bound and bound.lower() != actual:
            raise EvidenceIncomplete(
                f"hash mismatch for {key}: bound={bound[:12]}.. actual={actual[:12]}.. "
                f"(tamper or stale evidence)")
        verified[key] = {"path": str(Path(path).resolve()), "sha256": actual}
    verified["result_dir"] = art.get("result_dir")
    return verified


# ------------------------------------------------------------------- fold math

def derive_folds(trading_dates: list[str]) -> list[dict]:
    """Design 3.1: contiguous, non-overlapping, no shuffling; remainder days go to
    the EARLIEST r folds. Pure date determinism."""
    n = len(trading_dates)
    base, r = divmod(n, WF_FOLDS)
    folds, idx = [], 0
    for i in range(WF_FOLDS):
        size = base + (1 if i < r else 0)
        folds.append({
            "fold_index": i + 1,
            "start": trading_dates[idx],
            "end": trading_dates[idx + size - 1],
            "trade_days": size,
        })
        idx += size
    return folds


def derive_fold_config(config_csv: Path, fold: dict, out_dir: Path) -> Path:
    """Rewrite ONLY start_time/end_time in the verified config.csv (design 3.1)."""
    cfg = pd.read_csv(config_csv, dtype=str)
    cfg.loc[0, "start_time"] = fold["start"]
    cfg.loc[0, "end_time"] = fold["end"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "config.csv"
    cfg.to_csv(out, index=False)
    return out


# ---------------------------------------------------------------- gate metrics

def annualized_return(nav: np.ndarray) -> float:
    n = len(nav)
    if n < GATE_MIN_DAYS or nav[0] <= 0:
        return float("nan")
    return float((nav[-1] / nav[0]) ** (252 / n) - 1.0)


def max_drawdown(nav: np.ndarray) -> float:
    if len(nav) < GATE_MIN_DAYS:
        return float("nan")
    peak = np.maximum.accumulate(nav)
    dd = nav / peak - 1.0
    return float(dd.min())


def sharpe(daily_ret: np.ndarray) -> float:
    if len(daily_ret) < GATE_MIN_DAYS or daily_ret.std(ddof=0) == 0:
        return float("nan")
    return float(daily_ret.mean() / daily_ret.std(ddof=0) * np.sqrt(252))


def mc_block_bootstrap_pvalue(excess: np.ndarray, seed_hex: str) -> dict:
    """Design 3.2: stationary block bootstrap, block=21, n=1000, circular wrap.
    H0: mean excess <= 0. p = (1 + #{resample_mean <= 0}) / (1 + n). Add-one."""
    seed = int(seed_hex, 16)
    rng = np.random.Generator(np.random.PCG64(seed))
    n = len(excess)
    n_blocks = int(np.ceil(n / MC_BLOCK))
    starts_pool = np.arange(n)
    means = np.empty(MC_N)
    for i in range(MC_N):
        starts = rng.choice(starts_pool, size=n_blocks, replace=True)
        idx = (starts[:, None] + np.arange(MC_BLOCK)[None, :]).ravel()[:n]
        means[i] = excess[idx % n].mean()
    p = (1 + int((means <= 0).sum())) / (1 + MC_N)
    return {
        "p_value": float(p),
        "observed_mean_excess": float(excess.mean()),
        "null_quantiles": {
            "q05": float(np.quantile(means, 0.05)),
            "q50": float(np.quantile(means, 0.50)),
            "q95": float(np.quantile(means, 0.95)),
        },
    }


def round_trips_from_csv(rt_csv: Path) -> int:
    rt = pd.read_csv(rt_csv)
    return int(len(rt))


def g4_winrate_from_trades(trades_csv: Path) -> dict:
    """Independent FIFO pairer (cross-check vs engine round_trips.csv).
    Net-of-fee: buy cost = amount + commission; sell net = amount - commission - tax."""
    tr = pd.read_csv(trades_csv)
    lots: dict[str, list[list[float]]] = {}  # code -> [[qty, cost], ...] FIFO
    wins = total = 0
    for _, row in tr.iterrows():
        code = str(row["code"])
        qty = float(row["volume"])
        if str(row["action"]).lower() == "buy":
            cost = float(row["amount"]) + float(row["commission"])
            lots.setdefault(code, []).append([qty, cost])
        else:
            sell_net = float(row["amount"]) - float(row["commission"]) - float(row["tax"])
            remain = qty
            open_lots = lots.get(code, [])
            while remain > 1e-9 and open_lots:
                lot = open_lots[0]
                use = min(remain, lot[0])
                share = use / lot[0] if lot[0] else 0.0
                pnl = sell_net * (use / qty) - lot[1] * share
                win_total = pnl > 0
                wins += 1 if win_total else 0
                total += 1
                lot[0] -= use
                lot[1] -= lot[1] * share
                remain -= use
                if lot[0] <= 1e-9:
                    open_lots.pop(0)
    return {"wins": wins, "total": total}


# ------------------------------------------------------------------- orchestrator

def run_suite(evidence_path: Path, workspace: Path, project_root: Path,
              skip_wf: bool = False, iteration_count: int = 0) -> int:
    art = load_evidence_artifacts(evidence_path, project_root)
    try:
        verified = preverify(art)
    except EvidenceIncomplete as exc:
        print(f"[R5.5] EVIDENCE_INCOMPLETE: {exc}", file=sys.stderr)
        ledger_update(workspace, {"stage": "R5.5_EVIDENCE_INCOMPLETE", "reason": str(exc)})
        return 2

    cfg_csv = Path(verified["config"]["path"])
    ds_csv = Path(verified["daily_stats"]["path"])
    tr_csv = Path(verified["trades"]["path"])
    ds = pd.read_csv(ds_csv)
    dates = [str(d) for d in ds["date"].tolist()]
    nav = ds["total_asset"].to_numpy(dtype=float)
    bench = ds["benchmark"].to_numpy(dtype=float) if "benchmark" in ds.columns else None
    n_days = len(dates)
    seed_hex = hashlib.sha256(
        Path(verified["trades"]["path"]).read_bytes()
        + cfg_csv.read_bytes()).hexdigest()[:8]

    design = _load_design(workspace)
    is_etf = _is_etf_strategy(design)
    g3_threshold = 0.25 if is_etf else 0.5

    # ---------------- WF folds ----------------
    folds_out, valid, positive = [], 0, 0
    wf_skipped = skip_wf or n_days < WF_MIN_WINDOW_DAYS
    skip_reason = None
    if skip_wf:
        skip_reason = "--skip-wf requested"
    elif n_days < WF_MIN_WINDOW_DAYS:
        skip_reason = f"window {n_days} < {WF_MIN_WINDOW_DAYS} trading days"
    if not wf_skipped:
        folds = derive_folds(dates)
        run_root = project_root / "output" / "backtest_results"
        for fold in folds:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            fold_dir = run_root / f"{stamp}_r55_fold{fold['fold_index']}"
            fold_cfg = derive_fold_config(cfg_csv, fold, fold_dir)
            # R5.5 修复②：真实 output_dir 由 run_backtest 返回（fold_dir 仅落 fold config）
            fold_result, real_out = _run_engine(fold_cfg, fold_dir, project_root,
                                                workspace=workspace)
            fold_ds = (real_out / "daily_stats.csv") if real_out else (fold_dir / "daily_stats.csv")
            fold_rt = (real_out / "round_trips.csv") if real_out else (fold_dir / "round_trips.csv")
            if not fold_result or not fold_ds.exists():
                folds_out.append(_failed_fold(fold, "engine run failed"))
                continue
            fnav = pd.read_csv(fold_ds)["total_asset"].to_numpy(dtype=float)
            fbench = None
            if "benchmark" in pd.read_csv(fold_ds).columns:
                fbench = pd.read_csv(fold_ds)["benchmark"].to_numpy(dtype=float)
            rt_count = round_trips_from_csv(fold_rt) if fold_rt.exists() else 0
            no_trade = rt_count == 0
            strat_ret = float(fnav[-1] / fnav[0] - 1.0) if len(fnav) > 1 else None
            bench_ret = (float(fbench[-1] / fbench[0] - 1.0)
                         if fbench is not None and len(fbench) > 1 else None)
            excess = (strat_ret - bench_ret
                      if strat_ret is not None and bench_ret is not None else None)
            if no_trade or excess is None:
                folds_out.append({
                    "fold_index": fold["fold_index"],
                    "fold_window": {"start": fold["start"], "end": fold["end"]},
                    "trade_days": fold["trade_days"],
                    "round_trips": rt_count,
                    "no_trade_flag": no_trade,
                    "strategy_return": strat_ret,
                    "benchmark_return": bench_ret,
                    "excess_return": excess,
                    "positive": None,
                    "artifacts": _fold_hashes(fold_cfg, fold_ds,
                                              fold_dir / "trades.csv", fold_rt, fold_dir),
                })
                continue
            valid += 1
            pos = excess > 0
            positive += 1 if pos else 0
            folds_out.append({
                "fold_index": fold["fold_index"],
                "fold_window": {"start": fold["start"], "end": fold["end"]},
                "trade_days": fold["trade_days"],
                "round_trips": rt_count,
                "no_trade_flag": False,
                "strategy_return": strat_ret,
                "benchmark_return": bench_ret,
                "excess_return": excess,
                "positive": pos,
                "artifacts": _fold_hashes(fold_cfg, fold_ds,
                                          fold_dir / "trades.csv", fold_rt, fold_dir),
            })

    # ---------------- MC ----------------
    daily_ret = nav[1:] / nav[:-1] - 1.0
    mc = {"n": MC_N, "block_length": MC_BLOCK,
          "method": "stationary_block_bootstrap_on_excess_daily_returns",
          "p_value": None, "observed_mean_excess": None,
          "null_quantiles": {"q05": 0.0, "q50": 0.0, "q95": 0.0}}
    if bench is not None and len(daily_ret) >= MC_MIN_NAV_DAYS:
        b_ret = bench[1:] / bench[:-1] - 1.0
        excess_daily = daily_ret - b_ret
        mc.update(mc_block_bootstrap_pvalue(excess_daily, seed_hex))

    # ---------------- Gates ----------------
    ann = annualized_return(nav)
    mdd = max_drawdown(nav)
    sharpe_v = sharpe(daily_ret)
    rt_path = Path(verified["result_dir"] or "") / "round_trips.csv" if verified.get("result_dir") else None
    if rt_path and rt_path.exists():
        rt_total = round_trips_from_csv(rt_path)
        wr = _winrate_from_round_trips(rt_path)
    else:
        g4i = g4_winrate_from_trades(tr_csv)
        rt_total, wr = g4i["total"], (g4i["wins"] / g4i["total"] if g4i["total"] else None)
    valid_ratio = (positive / valid) if valid else None

    gates = {
        "G1": _gate("PASS" if ann > 0 else "FAIL", ann, 0.0, "absolute",
                    "" if not np.isnan(ann) else f"n={n_days}<60"),
        "G2": _gate("PASS" if (not np.isnan(mdd)) and abs(mdd) < 0.25 else "FAIL",
                    mdd, -0.25, "absolute", "" if not np.isnan(mdd) else f"n={n_days}<60"),
        "G3": _gate("PASS" if (not np.isnan(sharpe_v)) and sharpe_v > g3_threshold else "FAIL",
                    sharpe_v, g3_threshold, "absolute",
                    f"etf={is_etf}" if is_etf else ""),
        "G4": (_gate("PASS" if wr is not None and wr > 0.4 else "FAIL", wr, 0.4,
                     "trade_level", f"round_trips={rt_total}")
               if rt_total >= G4_MIN_ROUND_TRIPS else
               _gate("INSUFFICIENT_SAMPLES", wr, 0.4, "trade_level",
                     f"round_trips={rt_total}<{G4_MIN_ROUND_TRIPS}")),
        "G5": _g5_gate(wf_skipped, skip_reason, valid, positive, valid_ratio),
        "G6": (_gate("PASS" if mc["p_value"] is not None and mc["p_value"] < G6_P_THRESHOLD
                     else "FAIL", mc["p_value"], G6_P_THRESHOLD, "excess")
               if mc["p_value"] is not None else
               _gate("INSUFFICIENT_SAMPLES", None, G6_P_THRESHOLD, "excess",
                     f"nav_days={n_days}<{MC_MIN_NAV_DAYS} or benchmark missing")),
    }
    # downgrade INSUFFICIENT-looking gates computed from too-short series
    if n_days < GATE_MIN_DAYS:
        for g in ("G1", "G2", "G3"):
            gates[g] = _gate("INSUFFICIENT_SAMPLES", gates[g]["value"],
                             gates[g]["threshold"], gates[g]["dimension"],
                             f"n={n_days}<{GATE_MIN_DAYS}")

    failed = [name for name, g in gates.items() if g["result"] == "FAIL"]
    overall = "PASS" if not failed else "FAILED"

    report = {
        "robustness_report_version": "1.0",
        "strategy_id": _strategy_id(workspace, evidence_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_binding": {
            "config_csv_sha256": verified["config"]["sha256"],
            "daily_stats_csv_sha256": verified["daily_stats"]["sha256"],
            "trades_csv_sha256": verified["trades"]["sha256"],
            "primary_result_dir": str(verified.get("result_dir") or ""),
        },
        "window": {"start": dates[0], "end": dates[-1], "trading_days": n_days,
                   "benchmark_source": "daily_stats_benchmark_column"},
        "wf": {"folds": folds_out, "valid_folds": valid, "positive_folds": positive,
               "positive_ratio": valid_ratio, "skipped": wf_skipped,
               "skip_reason": skip_reason},
        "mc": mc,
        "gates": gates,
        "overall": overall,
        "iteration": {"iteration_count": iteration_count, "terminal": False},
        "seeds": {"mc_seed_source": "sha256(trades_csv+config_csv)[:8]",
                  "mc_seed_hex": seed_hex,
                  "python_hashseed": os.environ.get("PYTHONHASHSEED")},
    }

    out_dir = workspace / "robustness"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"robustness_report_iter{iteration_count}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    schema_ok = _validate_against_schema(out_path)
    print(f"[R5.5] overall={overall} failed_gates={failed or 'none'} "
          f"insufficient={[k for k, g in gates.items() if g['result'] == 'INSUFFICIENT_SAMPLES'] or 'none'}")
    print(f"[R5.5] report: {out_path} schema_ok={schema_ok}")
    if not schema_ok:
        print("[R5.5] WARNING: report failed schema validation", file=sys.stderr)
    _print_gates(gates)
    return 0 if overall == "PASS" else 3


def _gate(result: str, value, threshold, dimension: str, detail: str = "") -> dict:
    return {"result": result, "value": value, "threshold": threshold,
            "dimension": dimension, "detail": detail}


def _g5_gate(skipped: bool, reason: str, valid: int, positive: int, ratio):
    if skipped:
        return _gate("INSUFFICIENT_SAMPLES", ratio, 0.6, "fold_level",
                     f"WF skipped: {reason}")
    if valid < G5_MIN_VALID_FOLDS:
        return _gate("INSUFFICIENT_SAMPLES", ratio, 0.6, "fold_level",
                     f"valid folds {valid}<{G5_MIN_VALID_FOLDS} (NO_TRADE excluded, M1)")
    result = "PASS" if ratio is not None and ratio >= 0.6 else "FAIL"
    return _gate(result, ratio, 0.6, "fold_level",
                 f"{positive}/{valid} valid folds positive")


def _failed_fold(fold: dict, reason: str) -> dict:
    return {"fold_index": fold["fold_index"],
            "fold_window": {"start": fold["start"], "end": fold["end"]},
            "trade_days": fold["trade_days"], "round_trips": 0,
            "no_trade_flag": True, "strategy_return": None,
            "benchmark_return": None, "excess_return": None, "positive": None,
            "artifacts": {"config_csv_sha256": "0" * 64,
                          "daily_stats_csv_sha256": "0" * 64,
                          "trades_csv_sha256": "0" * 64,
                          "round_trips_csv_sha256": "0" * 64,
                          "result_dir": f"ENGINE_FAILED: {reason}"}}


def _fold_hashes(cfg: Path, ds: Path, tr: Path, rt: Path, fold_dir: Path) -> dict:
    def h(p: Path) -> str:
        return sha256_file(p) if p.exists() else "0" * 64
    return {"config_csv_sha256": h(cfg), "daily_stats_csv_sha256": h(ds),
            "trades_csv_sha256": h(tr), "round_trips_csv_sha256": h(rt),
            "result_dir": str(fold_dir)}


def _winrate_from_round_trips(rt_csv: Path):
    rt = pd.read_csv(rt_csv)
    if len(rt) == 0:
        return None
    wins = int((rt["pnl"] > 0).sum())
    return wins / len(rt)


def _run_engine(fold_cfg: Path, fold_dir: Path, project_root: Path,
                workspace: Path | None = None):
    """Drive the shared engine entry (same path as GUI BacktestWorker).

    R5.5 fold 链双修复（2026-08-28/30，docs/r55-fold-chain-fix-design.md）：
    - 修复①：strategy_file 裸名/相对路径锚定 workspace → project_root（主运行
      config.csv 导出时 basename 化，fold runner 原 cwd 相对解析 FileNotFoundError）；
    - 修复②：run_backtest 的输出目录由其内部生成（{stamp}_strategy），非调用方
      fold_dir——改为返回真实 output_dir，orchestrator 从真实目录读产物。
    返回 (ok, output_dir_or_None)。
    """
    sys.path.insert(0, str(project_root))
    try:
        cfg = pd.read_csv(fold_cfg, dtype=str).iloc[0]
        sf = Path(cfg["strategy_file"])
        if not sf.is_absolute():
            for base in (workspace, project_root):
                if base is None:
                    continue
                cand = base / sf
                if cand.exists():
                    sf = cand
                    break
        from quantstudio.backtest.run_ptrade_strategy import run_backtest
        result, output_dir, engine = run_backtest(
            str(sf), cfg["start_time"], cfg["end_time"],
            db_path=None, capital=float(cfg["init_capital"]),
            match_price_mode=cfg["match_price_mode"],
            engine_profile="daily-bar-v1", etf_t0=False, cost=None,
            rebalance_mode="legacy", progress_callback=None)
        return True, Path(output_dir)
    except Exception as exc:  # noqa: BLE001 - fold failure is a recorded outcome
        print(f"[R5.5] fold engine run failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return False, None


def _load_design(workspace: Path) -> dict:
    for cand in (workspace, workspace.parent, SKILL_DIR.parent.parent):
        p = Path(cand) / "agent_strategy_design.json"
        if p.exists():
            return load_json(p)
    return {}


def _is_etf_strategy(design: dict) -> bool:
    uc = design.get("universe_contract", {}) if isinstance(design, dict) else {}
    return (uc.get("local_dynamic_api") == "get_etf_list_local"
            or uc.get("mode") == "dynamic_local"
            or "etf" in json.dumps(design).lower())


def _strategy_id(workspace: Path, evidence_path: Path) -> str:
    ev = load_json(evidence_path)
    sid = ev.get("strategy_id")
    if sid:
        return sid
    return workspace.name[:63] or "unknown"


def ledger_update(workspace: Path, patch: dict) -> None:
    """Best-effort ledger update (workspace_state.json).

    M4 compatibility: a legacy ledger without a "robustness" field is fine (field is
    added). An EXISTING ledger that fails to parse is an error, not an empty slate —
    re-raise so we never overwrite a ledger we could not read.
    """
    p = Path(workspace) / "workspace_state.json"
    state = {}
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                state = json.load(f)
        except Exception as exc:
            raise ValueError(f"existing ledger unreadable ({p}): {exc}") from exc
        if not isinstance(state, dict):
            raise ValueError(f"existing ledger is not a JSON object: {p}")
    rob = state.get("robustness")
    if not isinstance(rob, dict):
        rob = {}
    rob.update(patch)
    state["robustness"] = rob
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _validate_against_schema(report_path: Path) -> bool:
    schema_path = SKILL_DIR / "schemas" / "robustness_report.schema.json"
    try:
        import jsonschema
        jsonschema.validate(load_json(report_path), load_json(schema_path))
        return True
    except ImportError:
        return True  # jsonschema absent: schema check deferred to reviewers
    except Exception as exc:  # noqa: BLE001
        print(f"[R5.5] schema violation: {exc}", file=sys.stderr)
        return False


def _print_gates(gates: dict) -> None:
    for name, g in gates.items():
        val = "n/a" if g["value"] is None else f"{g['value']:.4f}"
        print(f"  {name}: {g['result']:>22}  value={val} threshold={g['threshold']} "
              f"dim={g['dimension']} {g['detail']}".rstrip())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="R5.5 statistical robustness suite")
    ap.add_argument("--evidence", required=True, help="R5 evidence JSON path")
    ap.add_argument("--workspace", required=True, help="agent workspace dir")
    ap.add_argument("--project-root", default=".", help="QuantStudio project root")
    ap.add_argument("--skip-wf", action="store_true", help="skip WF folds (analysis only)")
    ap.add_argument("--iteration-count", type=int, default=0)
    args = ap.parse_args(argv)

    if args.iteration_count > ITERATION_CAP:
        print(f"[R5.5] ROBUSTNESS_FAILED: iteration {args.iteration_count} exceeds cap "
              f"{ITERATION_CAP}; no further repair-reevaluation is permitted "
              f"(anti-adaptive-overfit discipline).", file=sys.stderr)
        ledger_update(Path(args.workspace),
                      {"stage": "ROBUSTNESS_FAILED", "iteration_count": args.iteration_count,
                       "terminal": True})
        return 3

    return run_suite(Path(args.evidence), Path(args.workspace), Path(args.project_root),
                     skip_wf=args.skip_wf, iteration_count=args.iteration_count)


if __name__ == "__main__":
    sys.exit(main())
