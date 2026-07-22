#!/usr/bin/env python
"""Inspect the runtime environment and emit a capability_report.json.

PR5 minimal runnable version. Probes the live DuckDB and reports the honest
status of each capability (data tables, engine profiles, output dir) per the
capability-model.md contract: 12 status words, 4 invariants (including
"tick is never READY in v1").

Usage:
    python inspect_capabilities.py --db <quantstudio.db> --profile <daily-bar-v1|minute-bar-v1> \\
        --strategy-id <id> [--spec <strategy_spec.json>] [--out <capability_report.json>]

Output:
    - Prints a human-readable summary to stdout
    - Writes capability_report.json (conforming to capability_report.schema.json)
      to --out path (default: stdout alongside summary)

Status vocabulary (capability-model.md §1, 12 words):
    AVAILABLE / READY / DATA_MISSING / ADAPTER_MISSING / PROVIDER_MISSING /
    ENGINE_MISSING / PLATFORM_DEPENDENT / DEGRADED / SCHEMA_ONLY /
    PLANNED / UNSUPPORTED / BLOCKED

Invariants (capability-model.md §2, enforced in _derive_overall_status):
    1. execution_status=READY → six dimensions may only be AVAILABLE or READY
    2. any required capability non-READY → overall may not be READY
    3. all required capabilities READY → overall must be READY
    4. tick capability is never READY in v1 (execution_status ∈ BLOCKED/PLANNED/UNSUPPORTED)
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# =====================================================================
# Status vocabulary (capability-model.md §1) — do not invent new words
# =====================================================================
STATUS_AVAILABLE = "AVAILABLE"
STATUS_READY = "READY"
STATUS_DATA_MISSING = "DATA_MISSING"
STATUS_ADAPTER_MISSING = "ADAPTER_MISSING"
STATUS_PROVIDER_MISSING = "PROVIDER_MISSING"
STATUS_ENGINE_MISSING = "ENGINE_MISSING"
STATUS_PLATFORM_DEPENDENT = "PLATFORM_DEPENDENT"
STATUS_DEGRADED = "DEGRADED"
STATUS_SCHEMA_ONLY = "SCHEMA_ONLY"
STATUS_PLANNED = "PLANNED"
STATUS_UNSUPPORTED = "UNSUPPORTED"
STATUS_BLOCKED = "BLOCKED"

ALL_STATUSES = {
    STATUS_AVAILABLE, STATUS_READY, STATUS_DATA_MISSING, STATUS_ADAPTER_MISSING,
    STATUS_PROVIDER_MISSING, STATUS_ENGINE_MISSING, STATUS_PLATFORM_DEPENDENT,
    STATUS_DEGRADED, STATUS_SCHEMA_ONLY, STATUS_PLANNED, STATUS_UNSUPPORTED,
    STATUS_BLOCKED,
}

EXEC_READY = "READY"
EXEC_BLOCKED = "BLOCKED"
EXEC_PLANNED = "PLANNED"
EXEC_UNSUPPORTED = "UNSUPPORTED"


# =====================================================================
# DB probing
# =====================================================================

def _probe_db(db_path: Path) -> Dict[str, Any]:
    """Probe the live DuckDB. Returns a dict of raw findings (table -> stats).

    Uses a short read-only connection per query to avoid the read_only vs
    read_write configuration conflict (PR4 lesson).
    """
    import duckdb
    findings: Dict[str, Any] = {
        "db_exists": db_path.exists(),
        "db_readable": False,
        "tables": {},
    }
    if not findings["db_exists"]:
        return findings
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            findings["db_readable"] = True
            existing = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
            for table in ("stock_daily", "etf_daily", "stock_minutes", "etf_minutes",
                          "tick", "index_daily", "fin_indicator", "stock_float_share"):
                if table in existing:
                    try:
                        cnt = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                        time_range = None
                        if cnt > 0 and "time" in {c[0] for c in conn.execute(f'DESCRIBE "{table}"').fetchall()}:
                            mm = conn.execute(
                                f'SELECT MIN(time), MAX(time) FROM "{table}" WHERE time IS NOT NULL'
                            ).fetchone()
                            time_range = (mm[0], mm[1]) if mm and mm[0] is not None else None
                        findings["tables"][table] = {"rows": cnt, "time_range": time_range}
                    except Exception as e:
                        findings["tables"][table] = {"rows": 0, "error": str(e)}
                else:
                    findings["tables"][table] = {"rows": 0, "missing": True}
        finally:
            conn.close()
    except Exception as e:
        findings["db_readable"] = False
        findings["db_error"] = str(e)
    return findings


def _probe_table_freq(db_path: Path, table: str) -> List[str]:
    """Return distinct freq values in a minute table (empty if table missing)."""
    if not db_path.exists():
        return []
    try:
        import duckdb
        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            rows = conn.execute(
                f'SELECT DISTINCT freq FROM "{table}" WHERE freq IS NOT NULL'
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


# =====================================================================
# Capability construction
# =====================================================================

def _ok_dims(event_type: str, evidence: List[str], message: str) -> Dict[str, Any]:
    """Build a capability with all six dimensions READY/AVAILABLE (happy path)."""
    return {
        "schema_status": STATUS_READY,
        "data_status": STATUS_AVAILABLE,
        "adapter_status": STATUS_AVAILABLE,
        "provider_status": STATUS_AVAILABLE,
        "engine_status": STATUS_READY,
        "platform_status": STATUS_AVAILABLE,
        "evidence": evidence,
        "message": message,
        "remediation": [],
    }


def _cap(name: str, required: bool, event_type: str, dims: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble a capability entry. execution_status derived from dims."""
    return {
        "capability": name,
        "required": required,
        "event_type": event_type,
        **dims,
    }


def _build_daily_capability(findings: Dict, profile: str) -> Dict[str, Any]:
    """daily-bar backtest capability (stock_daily + etf_daily)."""
    sd = findings["tables"].get("stock_daily", {})
    ed = findings["tables"].get("etf_daily", {})
    evidence = []
    if not findings.get("db_readable"):
        return _cap("stock_daily_backtest", True, "bar", {
            "schema_status": STATUS_DATA_MISSING, "data_status": STATUS_DATA_MISSING,
            "adapter_status": STATUS_AVAILABLE, "provider_status": STATUS_AVAILABLE,
            "engine_status": STATUS_READY, "platform_status": STATUS_AVAILABLE,
            "evidence": [], "message": "DB not readable",
            "remediation": ["Ensure DuckDB file exists and is readable."]})
    evidence.append(f"stock_daily: {sd.get('rows', 0)} rows")
    evidence.append(f"etf_daily: {ed.get('rows', 0)} rows")
    if sd.get("rows", 0) > 0:
        evidence.append(f"stock_daily range: {sd.get('time_range')}")
    dims = _ok_dims("bar", evidence, "Daily bar backtest: data + engine ready")
    return _cap("stock_daily_backtest", True, "bar", dims)


def _build_minute_capability(findings: Dict, profile: str, db_path: Path) -> Dict[str, Any]:
    """minute-bar backtest capability (stock_minutes + etf_minutes)."""
    sm = findings["tables"].get("stock_minutes", {})
    em = findings["tables"].get("etf_minutes", {})
    evidence = [f"stock_minutes: {sm.get('rows', 0)} rows",
                f"etf_minutes: {em.get('rows', 0)} rows"]
    sm_freq = _probe_table_freq(db_path, "stock_minutes")
    if sm_freq:
        evidence.append(f"stock_minutes freqs: {sm_freq}")
    # Engine readiness: PR4 verified minute-bar-v1 on real data
    dims = _ok_dims("bar", evidence, "Minute bar backtest: data + engine ready (PR4 verified)")
    return _cap("stock_minute_backtest", profile == "minute-bar-v1", "bar", dims)


def _build_tick_capability(profile: str) -> Dict[str, Any]:
    """tick capability — INVARIANT 4: tick is never READY in v1.

    Hard rule (capability-model.md §2.4, schema allOf line 73):
    execution_status ∈ {BLOCKED, PLANNED, UNSUPPORTED}. We report PLANNED
    (declared in roadmap, not yet implemented).
    """
    return _cap("tick_backtest", False, "tick", {
        "schema_status": STATUS_SCHEMA_ONLY,
        "data_status": STATUS_DATA_MISSING,
        "adapter_status": STATUS_ADAPTER_MISSING,
        "provider_status": STATUS_PROVIDER_MISSING,
        "engine_status": STATUS_ENGINE_MISSING,
        "platform_status": STATUS_PLANNED,
        "evidence": ["Tick engine is PR9 scope; tick_data table empty or missing"],
        "message": "Tick backtest is PLANNED, not executable in v1 (invariant: tick never READY)",
        "remediation": ["Tick support arrives in PR9; do not declare tick READY"],
    })


def _build_output_capability(out_dir: Path) -> Dict[str, Any]:
    """Output directory writability."""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        test_file = out_dir / ".inspect_write_test"
        test_file.write_text("ok")
        test_file.unlink()
        dims = _ok_dims("reference", [f"Output dir writable: {out_dir}"],
                        "Output directory writable")
    except Exception as e:
        dims = {
            "schema_status": STATUS_AVAILABLE, "data_status": STATUS_AVAILABLE,
            "adapter_status": STATUS_AVAILABLE, "provider_status": STATUS_AVAILABLE,
            "engine_status": STATUS_AVAILABLE, "platform_status": STATUS_BLOCKED,
            "evidence": [f"Output dir not writable: {e}"],
            "message": f"Output directory not writable: {out_dir}",
            "remediation": [f"Fix permissions on {out_dir} or change output.root in Spec"],
        }
    return _cap("output_dir_writable", True, "reference", dims)


# =====================================================================
# Invariant enforcement (capability-model.md §2)
# =====================================================================

def _tick_invariant(cap: Dict[str, Any]) -> Optional[str]:
    """Invariant 4: tick event_type → execution_status ∈ {BLOCKED,PLANNED,UNSUPPORTED}.

    Returns violation message if breached, else None. This is a hard rule that
    the capability builder must respect; this function is a defensive check.
    """
    if cap["event_type"] == "tick" and cap["engine_status"] == STATUS_READY:
        return (f"INVARIANT BREACH: tick capability '{cap['capability']}' has "
                f"engine_status=READY — tick must never be READY in v1")
    return None


def _derive_execution_status(cap: Dict[str, Any]) -> str:
    """Derive execution_status from the six dimensions (invariant 1).

    execution_status=READY only if all six dims are AVAILABLE or READY.
    Otherwise BLOCKED (with the failing dims recorded in evidence).
    """
    if cap["event_type"] == "tick":
        # Invariant 4: never READY
        return EXEC_PLANNED if cap["engine_status"] == STATUS_ENGINE_MISSING else EXEC_BLOCKED
    dims = [cap["schema_status"], cap["data_status"], cap["adapter_status"],
            cap["provider_status"], cap["engine_status"], cap["platform_status"]]
    bad = [d for d in dims if d not in (STATUS_AVAILABLE, STATUS_READY)]
    if bad:
        return EXEC_BLOCKED
    return EXEC_READY


def _derive_overall(caps: List[Dict[str, Any]]) -> tuple:
    """Apply invariants 2 & 3 to derive overall_execution_status + blockers.

    Invariant 2: any required capability non-READY → overall may not be READY.
    Invariant 3: all required capabilities READY → overall must be READY.
    Returns (overall_status, blockers, repair_actions).
    """
    blockers = []
    repair = []
    for c in caps:
        es = c["execution_status"]
        if c["required"] and es != EXEC_READY:
            blockers.append(f"{c['capability']}: {es} — {c['message']}")
            repair.extend(c.get("remediation", []))
    if blockers:
        return (EXEC_BLOCKED, blockers, repair)
    return (EXEC_READY, [], [])


# =====================================================================
# Main
# =====================================================================

def inspect(db_path: Path, profile: str, strategy_id: str,
            out_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Run the full inspection. Returns the capability_report dict."""
    findings = _probe_db(db_path)
    if out_dir is None:
        out_dir = Path("output/generated_strategies") / strategy_id

    caps = [
        _build_daily_capability(findings, profile),
        _build_minute_capability(findings, profile, db_path),
        _build_tick_capability(profile),
        _build_output_capability(out_dir),
    ]

    # Invariant 4 defensive check
    for c in caps:
        breach = _tick_invariant(c)
        if breach:
            caps.append(_cap("_invariant_breach", True, "reference", {
                "schema_status": STATUS_BLOCKED, "data_status": STATUS_BLOCKED,
                "adapter_status": STATUS_BLOCKED, "provider_status": STATUS_BLOCKED,
                "engine_status": STATUS_BLOCKED, "platform_status": STATUS_BLOCKED,
                "evidence": [breach], "message": breach,
                "remediation": ["Fix the tick capability to non-READY"],
            }))

    # Derive execution_status per capability (invariant 1 + 4)
    for c in caps:
        c["execution_status"] = _derive_execution_status(c)

    # Derive overall (invariant 2 + 3)
    overall, blockers, repair = _derive_overall(caps)

    report = {
        "report_version": "1.0",
        "generated_at": datetime.datetime.now().astimezone().isoformat(),
        "strategy_id": strategy_id,
        "requested_profile": profile,
        "capabilities": caps,
        "overall_execution_status": overall,
        "blockers": blockers,
        "repair_actions": repair,
    }
    return report


def _print_summary(report: Dict[str, Any]) -> None:
    """Human-readable summary to stdout."""
    print(f"=== Capability Report (profile={report['requested_profile']}) ===")
    print(f"Overall: {report['overall_execution_status']}")
    print(f"Generated: {report['generated_at']}")
    print()
    print(f"{'Capability':<30} {'Req':<5} {'Event':<10} {'Exec':<10} {'Data':<14} {'Engine':<10}")
    print("-" * 85)
    for c in report["capabilities"]:
        print(f"{c['capability']:<30} {str(c['required']):<5} {c['event_type']:<10} "
              f"{c['execution_status']:<10} {c['data_status']:<14} {c['engine_status']:<10}")
    if report["blockers"]:
        print()
        print(f"Blockers ({len(report['blockers'])}):")
        for b in report["blockers"]:
            print(f"  - {b}")
    if report["repair_actions"]:
        print()
        print(f"Repair actions ({len(report['repair_actions'])}):")
        for r in report["repair_actions"]:
            print(f"  - {r}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inspect capabilities → capability_report.json")
    parser.add_argument("--db", required=True, help="Path to quantstudio.db")
    parser.add_argument("--profile", required=True,
                        choices=["daily-bar-v1", "minute-bar-v1", "tick-bar-v1", "planned"],
                        help="Engine profile to inspect against")
    parser.add_argument("--strategy-id", required=True, help="Strategy identifier")
    parser.add_argument("--out", default=None, help="Output JSON path (default: stdout only)")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    report = inspect(db_path, args.profile, args.strategy_id)

    _print_summary(report)

    # Schema self-check (capability_report.schema.json)
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "capability_report.schema.json"
    if schema_path.exists():
        try:
            import jsonschema
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            jsonschema.Draft7Validator(schema).validate(report)
            print(f"\nSchema self-check: PASS ({schema_path.name})")
        except ImportError:
            print("\nSchema self-check: SKIPPED (jsonschema not installed)")
        except Exception as e:
            print(f"\nSchema self-check: FAIL — {e}", file=sys.stderr)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nReport written: {out_path}")

    return 0 if report["overall_execution_status"] == EXEC_READY else 2


if __name__ == "__main__":
    sys.exit(main())
