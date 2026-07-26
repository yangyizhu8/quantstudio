#!/usr/bin/env python3
"""Parse real QuantStudio backtest artifacts into machine-checkable R5 metrics.

Reads the files produced by quantstudio/backtest/result_exporter.py plus the
run log and computes the deployment facts R5 needs; it never trusts
self-reported booleans.

- config.csv      -> strategy_file, start/end, init_capital, match_price_mode,
                     engine_semantics_version, commission/slippage
- daily_stats.csv -> max_concurrent_positions, positions_after_rebalance,
                     gross_exposure, cash_ratio (post-first-rebalance means)
- trades.csv      -> buy/sell counts, unique bought symbols, rebalance days,
                     turnover
- run log         -> rejection counters (insufficient_cash, ...) and the
                     QS_REBALANCE_AUDIT / QS_PORTFOLIO_AUDIT lines

Usage:
    python analyze_backtest_artifacts.py <result_dir> [--log <log_file>] [--out report.json]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

LOG_PATTERNS = {
    "insufficient_cash": re.compile(r"insufficient_cash|资金不足"),
    "insufficient_sellable": re.compile(r"insufficient_sellable|可用.*不足"),
    "limit_up_blocked": re.compile(r"limit_up_blocked|涨停"),
    "limit_down_blocked": re.compile(r"limit_down_blocked|跌停"),
    "halted": re.compile(r"\bhalt(?:ed)?\b|停牌"),
    "no_price": re.compile(r"no_price|无价格|缺.*价格"),
    "callback_exception": re.compile(r"Traceback|callback.*error|exception", re.IGNORECASE),
}

AUDIT_REBALANCE = "QS_REBALANCE_AUDIT"
AUDIT_PORTFOLIO = "QS_PORTFOLIO_AUDIT"


def sha256_path(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def parse_config(path: Path) -> dict[str, Any]:
    rows = _read_rows(path)
    if not rows:
        raise ValueError(f"config.csv is empty: {path}")
    row = rows[0]
    return {
        "strategy_file": row.get("strategy_file") or row.get("strategy"),
        "start_time": row.get("start_time"),
        "end_time": row.get("end_time"),
        "init_capital": _float(row.get("init_capital")),
        "match_price_mode": row.get("match_price_mode"),
        "engine_semantics_version": row.get("engine_semantics_version"),
        "commission_rate": _float(row.get("commission_rate")),
        "slippage_rate": _float(row.get("slippage_rate")),
        "fixed_slippage": _float(row.get("fixed_slippage")),
    }


def parse_trades(path: Path) -> dict[str, Any]:
    rows = _read_rows(path) if path.exists() else []
    buys = [r for r in rows if (r.get("action") or "").lower() == "buy"]
    sells = [r for r in rows if (r.get("action") or "").lower() == "sell"]
    day_key = "datetime" if rows and "datetime" in rows[0] else "date"
    rebalance_days = sorted({(r.get(day_key) or "")[:10] for r in rows if r.get(day_key)})
    buy_days: dict[str, int] = {}
    for r in buys:
        day = (r.get(day_key) or "")[:10]
        buy_days[day] = buy_days.get(day, 0) + 1
    traded_value = sum(_float(r.get("amount", _float(r.get("price")) * _float(r.get("volume"))))
                       for r in rows)
    return {
        "buy_count": len(buys),
        "sell_count": len(sells),
        "unique_bought_symbols": len({r.get("code") for r in buys if r.get("code")}),
        "rebalance_days": rebalance_days,
        "rebalance_day_count": len(rebalance_days),
        "rebalance_day_buy_counts": buy_days,
        "traded_value": traded_value,
    }


def parse_daily_stats(path: Path, rebalance_days: list[str]) -> dict[str, Any]:
    rows = _read_rows(path)
    if not rows:
        return {
            "days": 0,
            "max_concurrent_positions": 0,
            "positions_after_rebalance": 0,
            "gross_exposure": 0.0,
            "cash_ratio": 1.0,
            "final_total_asset": 0.0,
        }
    positions = [_int(r.get("positions")) for r in rows]
    first_rebalance = rebalance_days[0] if rebalance_days else None
    post = [r for r in rows if first_rebalance and (r.get("date") or "")[:10] >= first_rebalance] or rows

    def ratio(row: dict[str, str], numerator: str) -> float:
        total = _float(row.get("total_asset", row.get("nav")))
        return (_float(row.get(numerator)) / total) if total else 0.0

    exposures = [ratio(r, "market_value") for r in post]
    cash_ratios = [ratio(r, "cash") for r in post]
    post_positions = [_int(r.get("positions")) for r in post]
    return {
        "days": len(rows),
        "max_concurrent_positions": max(positions),
        "positions_after_rebalance": max(post_positions) if post_positions else 0,
        "gross_exposure": sum(exposures) / len(exposures) if exposures else 0.0,
        "cash_ratio": sum(cash_ratios) / len(cash_ratios) if cash_ratios else 1.0,
        "final_total_asset": _float(rows[-1].get("total_asset", rows[-1].get("nav"))),
    }


def parse_log(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig", errors="replace") if path.exists() else ""
    counters = {name: len(pattern.findall(text)) for name, pattern in LOG_PATTERNS.items()}
    rebalance_audits = [line.strip() for line in text.splitlines() if AUDIT_REBALANCE in line]
    portfolio_audits = [line.strip() for line in text.splitlines() if AUDIT_PORTFOLIO in line]

    def audit_values(lines: list[str], key: str) -> list[float]:
        values = []
        for line in lines:
            match = re.search(rf"\b{key}=(-?\d+(?:\.\d+)?)", line)
            if match:
                values.append(float(match.group(1)))
        return values

    def audit_records(lines: list[str]) -> list[dict[str, Any]]:
        records = []
        for line in lines:
            record: dict[str, Any] = {"raw": line}
            date_match = re.search(r"\bdate=(\d{4}-\d{2}-\d{2})", line)
            record["date"] = date_match.group(1) if date_match else None
            id_match = re.search(r"\brebalance_id=(\S+)", line)
            record["rebalance_id"] = id_match.group(1) if id_match else None
            for key, value in re.findall(r"\b([a-z_]+)=(-?\d+(?:\.\d+)?)", line):
                if key in {"date", "rebalance_id"}:
                    continue
                record[key] = float(value)
            records.append(record)
        return records

    return {
        "rejection_counters": counters,
        "rebalance_audit_count": len(rebalance_audits),
        "portfolio_audit_count": len(portfolio_audits),
        "rebalance_audit_lines": rebalance_audits[:50],
        "portfolio_audit_lines": portfolio_audits[:50],
        "rebalance_audits": audit_records(rebalance_audits),
        "portfolio_audits": audit_records(portfolio_audits),
        "audit_selected": audit_values(rebalance_audits, "selected"),
        "audit_buy_submitted": audit_values(rebalance_audits, "buy_submitted"),
        "audit_positions": audit_values(portfolio_audits, "positions"),
        "audit_history_eligible_count": audit_values(rebalance_audits, "history_eligible_count"),
    }


def analyze(result_dir: str | Path, log_file: str | Path | None = None) -> dict[str, Any]:
    root = Path(result_dir)
    log_path = Path(log_file) if log_file else (root / "backtest.log")
    if not log_path.exists():
        candidates = sorted(root.glob("*.log")) + sorted(root.glob("*.txt"))
        log_path = candidates[0] if candidates else log_path
    return analyze_verified(
        config_path=root / "config.csv",
        daily_stats_path=root / "daily_stats.csv",
        trades_path=root / "trades.csv",
        log_path=log_path,
        result_dir=root,
    )


def analyze_verified(*, config_path: str | Path, daily_stats_path: str | Path,
                     trades_path: str | Path | None, log_path: str | Path,
                     result_dir: str | Path | None = None) -> dict[str, Any]:
    """Analyze exactly the given files — never re-derive paths from a directory.

    R5 calls this with the same resolved paths whose SHA-256 was just verified,
    so the parsed bytes are guaranteed to be the hash-bound bytes.
    """
    config_path = Path(config_path)
    daily_stats_path = Path(daily_stats_path)
    trades_path = Path(trades_path) if trades_path else None
    log_path = Path(log_path)
    report: dict[str, Any] = {
        "report_version": "1.1",
        "result_dir": str(result_dir) if result_dir else str(config_path.parent),
        "parsed_files": {
            "config_csv": str(config_path),
            "daily_stats_csv": str(daily_stats_path),
            "trades_csv": str(trades_path) if trades_path else None,
            "log_file": str(log_path),
        },
        "missing_artifacts": [],
    }
    paths = {
        "config_csv": config_path,
        "daily_stats_csv": daily_stats_path,
        "log_file": log_path,
    }
    if trades_path is not None:
        paths["trades_csv"] = trades_path
    for name, path in paths.items():
        if not path.exists():
            report["missing_artifacts"].append(name)
    if report["missing_artifacts"]:
        report["status"] = "ARTIFACT_MISSING"
        return report

    trades = (parse_trades(trades_path) if trades_path is not None
              else parse_trades(config_path.parent / "__no_trades__.csv"))
    report["config"] = parse_config(config_path)
    report["trades"] = trades
    report["daily_stats"] = parse_daily_stats(daily_stats_path, trades["rebalance_days"])
    report["log"] = parse_log(log_path)
    report["trades"]["turnover"] = (
        trades["traded_value"] / report["daily_stats"]["final_total_asset"]
        if report["daily_stats"]["final_total_asset"] else 0.0
    )
    report["status"] = "PASS"
    return report


def verify_artifact_hashes(evidence: dict[str, Any]) -> list[str]:
    """Return a list of mismatch/missing messages for evidence-bound artifacts."""
    problems: list[str] = []
    artifacts = evidence.get("artifacts", {})
    for name in ("config_csv", "daily_stats_csv", "trades_csv", "log_file"):
        entry = artifacts.get(name)
        if entry is None and name == "trades_csv":
            continue  # signal_dependent no-trade runs may bind trades_csv=null
        if not isinstance(entry, dict):
            problems.append(f"artifacts.{name} is missing")
            continue
        raw_path = str(entry.get("path", ""))
        path = Path(raw_path)
        if not path.is_absolute():
            problems.append(f"artifacts.{name}.path must be an absolute path: {raw_path}")
            continue
        resolved = path.resolve()
        if resolved != path.parent.resolve() / path.name or ".." in path.parts:
            problems.append(f"artifacts.{name}.path must not contain traversal: {raw_path}")
            continue
        if not resolved.exists():
            problems.append(f"artifacts.{name}.path does not exist: {resolved}")
            continue
        actual = sha256_path(resolved)
        if actual != entry.get("sha256"):
            problems.append(f"artifacts.{name} sha256 mismatch (file changed after evidence)")
    return problems


def verified_artifact_paths(evidence: dict[str, Any]) -> dict[str, Path | None]:
    """Resolve the hash-bound artifact paths for analysis (absolute, no traversal)."""
    artifacts = evidence.get("artifacts", {})
    resolved: dict[str, Path | None] = {}
    for name in ("config_csv", "daily_stats_csv", "trades_csv", "log_file"):
        entry = artifacts.get(name)
        if entry is None:
            resolved[name] = None
        else:
            resolved[name] = Path(str(entry["path"])).resolve()
    return resolved


def artifact_path_binding_problems(evidence: dict[str, Any]) -> list[str]:
    """Ensure the hash-bound CSV artifacts are exactly result_dir/<canonical name>.

    The directory and the artifact paths must describe one identical set of
    files; otherwise a wrong run can be hash-bound while a different run is
    analyzed.
    """
    problems: list[str] = []
    raw_dir = str(evidence.get("result_dir", ""))
    root = Path(raw_dir)
    if not root.is_absolute():
        problems.append(f"ARTIFACT-PATH-MISMATCH: result_dir must be an absolute path: {raw_dir}")
        return problems
    root = root.resolve()
    paths = verified_artifact_paths(evidence)
    canonical = {
        "config_csv": "config.csv",
        "daily_stats_csv": "daily_stats.csv",
        "trades_csv": "trades.csv",
    }
    for name, filename in canonical.items():
        path = paths.get(name)
        if path is None:
            continue  # nullable trades_csv handled separately
        expected = (root / filename).resolve()
        if path != expected:
            problems.append(
                f"ARTIFACT-PATH-MISMATCH: artifacts.{name}.path ({path}) must equal "
                f"result_dir/{filename} ({expected})")
    log_path = paths.get("log_file")
    if log_path is not None and log_path.suffix not in {".log", ".txt"}:
        problems.append(
            f"ARTIFACT-PATH-MISMATCH: artifacts.log_file.path must be a .log/.txt file: {log_path}")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Analyze real backtest artifacts")
    parser.add_argument("result_dir")
    parser.add_argument("--log", help="Explicit run log file")
    parser.add_argument("--out", help="Write analysis JSON")
    args = parser.parse_args(argv)
    report = analyze(args.result_dir, args.log)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
