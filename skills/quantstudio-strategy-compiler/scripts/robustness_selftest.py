#!/usr/bin/env python3
"""R5.5 selftest: machine-runnable acceptance checks 2/3/4 (+MC direction 3).

Checks (design doc §5):
  [2] hash tamper injection  -> EVIDENCE_INCOMPLETE, exit 2, no report
  [3] MC p-value direction   -> positive-mean synthetic series p<0.01;
                                zero-mean p high; negative-mean p -> 1
  [4] iteration cap          -> iteration_count=3 refused: ROBUSTNESS_FAILED, exit 3

Usage: python robustness_selftest.py --all [--keep-temp]
Exit 0 = all green.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

SKILL_SCRIPTS = Path(__file__).resolve().parent
SUITE = SKILL_SCRIPTS / "run_robustness_suite.py"

DATES = pd.bdate_range("2026-01-01", periods=300)  # 300 trading days >= WF minimum


def _write_workspace(root: Path, nav: np.ndarray, bench: np.ndarray) -> tuple[Path, Path]:
    """Build a minimal-but-schema-shaped fake R5 workspace: evidence JSON + run dir."""
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    ds = pd.DataFrame({
        "date": DATES.strftime("%Y-%m-%d"),
        "total_asset": nav,
        "cash": 1000.0,
        "market_value": nav - 1000.0,
        "benchmark": bench,
        "positions": 5,
        "daily_return": [0.0] + list(nav[1:] / nav[:-1] - 1.0),
    })
    ds.to_csv(run_dir / "daily_stats.csv", index=False)
    cfg = pd.DataFrame([{
        "strategy_file": "selftest_strategy.py",
        "strategy": "selftest_strategy",
        "start_time": DATES[0].strftime("%Y-%m-%d"),
        "end_time": DATES[-1].strftime("%Y-%m-%d"),
        "init_capital": 100000,
        "commission_rate": 0.00035, "min_commission": 5.0, "stamp_tax_rate": 0.001,
        "transfer_fee_rate": 1e-05, "slippage_rate": 0.0, "fixed_slippage": 0.0,
        "match_price_mode": "close", "engine_semantics_version": "0.1.0-legacy",
        "min_rebalance_pct": 0.005,
    }])
    cfg.to_csv(run_dir / "config.csv", index=False)
    tr = pd.DataFrame({
        "datetime": ["2026-01-05", "2026-02-05"],
        "code": ["000001.SZ", "000001.SZ"],
        "action": ["buy", "sell"],
        "volume": [100, 100], "price": [10.0, 11.0],
        "commission": [5.0, 5.0], "tax": [0.0, 1.0],
        "pnl": [0.0, 95.0], "amount": [1000.0, 1100.0],
    })
    tr.to_csv(run_dir / "trades.csv", index=False)
    ev = {
        "evidence_version": "2.1",
        "strategy_id": "selftest_strategy",
        "result_dir": str(run_dir),
        "artifacts": {
            "config_csv": {"path": str(run_dir / "config.csv"),
                           "sha256": _sha(run_dir / "config.csv")},
            "daily_stats_csv": {"path": str(run_dir / "daily_stats.csv"),
                                "sha256": _sha(run_dir / "daily_stats.csv")},
            "trades_csv": {"path": str(run_dir / "trades.csv"),
                           "sha256": _sha(run_dir / "trades.csv")},
            "log_file": {"path": "log.txt", "sha256": _sha_bytes(b"log")},
        },
    }
    ev_path = root / "user_backtest_evidence.json"
    ev_path.write_text(json.dumps(ev), encoding="utf-8")
    return ev_path, run_dir


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def check_mc_direction() -> bool:
    """Acceptance 3: synthetic series with known means -> p direction correct."""
    sys.path.insert(0, str(SKILL_SCRIPTS))
    from run_robustness_suite import mc_block_bootstrap_pvalue  # noqa: E402

    rng = np.random.default_rng(42)
    n = 300
    strong_pos = mc_block_bootstrap_pvalue(rng.normal(0.002, 0.01, n), "00000001")
    zero = mc_block_bootstrap_pvalue(rng.normal(0.0, 0.01, n), "00000002")
    strong_neg = mc_block_bootstrap_pvalue(rng.normal(-0.002, 0.01, n), "00000003")
    ok_pos = strong_pos["p_value"] < 0.01
    ok_zero = zero["p_value"] > 0.05
    ok_neg = strong_neg["p_value"] > 0.9
    print(f"[mc-direction] pos_mean p={strong_pos['p_value']:.4f} (<0.01: {ok_pos}); "
          f"zero_mean p={zero['p_value']:.4f} (>0.05: {ok_zero}); "
          f"neg_mean p={strong_neg['p_value']:.4f} (>0.9: {ok_neg})")
    return ok_pos and ok_zero and ok_neg


def check_hash_tamper(tmp: Path) -> bool:
    """Acceptance 2: flip one byte in trades.csv -> EVIDENCE_INCOMPLETE, exit 2."""
    root = tmp / "tamper"
    ev_path, run_dir = _write_workspace(root, np.linspace(100000, 110000, len(DATES)),
                                        np.linspace(100, 105, len(DATES)))
    # tamper AFTER evidence binding
    tr = run_dir / "trades.csv"
    data = tr.read_bytes().replace(b"11.0", b"11.1", 1) if b"11.0" in tr.read_bytes() \
        else tr.read_bytes() + b"\n"
    tr.write_bytes(data)
    proc = subprocess.run(
        [sys.executable, str(SUITE), "--evidence", str(ev_path),
         "--workspace", str(root), "--skip-wf"],
        capture_output=True, text=True)
    no_report = not (root / "robustness").exists()
    ok = proc.returncode == 2 and "EVIDENCE_INCOMPLETE" in (proc.stderr + proc.stdout) \
        and no_report
    print(f"[hash-tamper] exit={proc.returncode} (want 2); "
          f"evidence_incomplete={'EVIDENCE_INCOMPLETE' in (proc.stderr + proc.stdout)}; "
          f"no_report={no_report}")
    return ok


def check_iteration_cap(tmp: Path) -> bool:
    """Acceptance 4: iteration_count=3 refused -> ROBUSTNESS_FAILED, exit 3."""
    root = tmp / "itercap"
    ev_path, _ = _write_workspace(root, np.linspace(100000, 110000, len(DATES)),
                                  np.linspace(100, 105, len(DATES)))
    proc = subprocess.run(
        [sys.executable, str(SUITE), "--evidence", str(ev_path),
         "--workspace", str(root), "--skip-wf", "--iteration-count", "3"],
        capture_output=True, text=True)
    ledger = root / "workspace_state.json"
    ledger_ok = False
    if ledger.exists():
        state = json.loads(ledger.read_text(encoding="utf-8"))
        ledger_ok = (state.get("robustness", {}).get("stage") == "ROBUSTNESS_FAILED"
                     and state["robustness"].get("terminal") is True)
    ok = proc.returncode == 3 and "ROBUSTNESS_FAILED" in (proc.stderr + proc.stdout) \
        and ledger_ok
    print(f"[iteration-cap] exit={proc.returncode} (want 3); "
          f"terminal_ledger={ledger_ok}")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--keep-temp", action="store_true")
    args = ap.parse_args(argv)
    if not args.all:
        print("nothing to do: pass --all")
        return 4
    results = {}
    results["mc_direction"] = check_mc_direction()
    tmp = Path(tempfile.mkdtemp(prefix="r55_selftest_"))
    try:
        results["hash_tamper"] = check_hash_tamper(tmp)
        results["iteration_cap"] = check_iteration_cap(tmp)
    finally:
        if not args.keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)
    all_ok = all(results.values())
    print(f"[selftest] {'ALL GREEN' if all_ok else 'FAILURES'}: {results}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
