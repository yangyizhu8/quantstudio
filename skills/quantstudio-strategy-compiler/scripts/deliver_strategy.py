#!/usr/bin/env python3
"""Skill-local delivery orchestration: chains orchestrator + package builder.

Lives in the Skill (NOT the released wheel) so it works with the installed
0.3.0+mvp wheel without modification. Imports only modules already in the wheel:
  - quantstudio.strategy_compiler.orchestrator.orchestrate
  - quantstudio.strategy_compiler.package.builder.build_strategy_package

Usage (Skill auto-calls this; advanced users may invoke directly):
    python scripts/deliver_strategy.py <spec.json> --out <dir> [--g2-frozen-dir <dir>] [--no-smoke] [--allow-deferred-smoke]

Python API:
    from deliver_strategy import deliver_strategy  # after adding scripts/ to path
    deliver_strategy(spec, out_dir="output/strategy_deliveries")
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional


class DeliveryConfirmationError(Exception):
    """R2.5 HARD GATE: raised when user_confirmations not ALL CONFIRMED."""


# Static validation fields that must ALL be strictly "PASS".
_STATIC_FIELDS = ("schema", "timing", "hard_filters", "api_portability",
                  "variant_consistency")


def deliver_strategy(
    spec: dict,
    out_dir: str | Path,
    *,
    g2_frozen_dir: Optional[str | Path] = None,
    package_version: str = "0.3.0-mvp",
    run_smoke: bool = True,
    allow_deferred_smoke: bool = False,
) -> Path:
    """Run full delivery: R2.5 gate → validate → package → report.

    allow_deferred_smoke defaults False (fail-closed). Must be explicitly True
    to generate package when smoke is BLOCKED or not run.
    """
    strategy_id = spec["strategy_id"]

    # --- R2.5 HARD GATE (BEFORE any work) ---
    _enforce_confirmation_gate(spec)

    base = Path(out_dir) / strategy_id
    val_dir = base / "validation"
    pkg_parent = base / "package"

    if base.exists():
        shutil.rmtree(base)
    val_dir.mkdir(parents=True, exist_ok=True)
    pkg_parent.mkdir(parents=True, exist_ok=True)

    # --- Stage 1: orchestrator validation (from released wheel) ---
    from quantstudio.strategy_compiler.orchestrator import orchestrate
    run_card = orchestrate(spec, out_dir=val_dir, run_smoke=run_smoke)

    # --- Stage 2: strict static gate ---
    validation = run_card.get("validation", {})
    static_failures = [k for k in _STATIC_FIELDS if validation.get(k) != "PASS"]
    if static_failures:
        _write_report(base, spec, run_card, None, "FAILED_STATIC_VALIDATION",
                      f"static validators not PASS: {static_failures}")
        raise RuntimeError(f"Static validation failed: {static_failures}")

    # --- Stage 3: smoke gate (strict truth table) ---
    smoke = run_card.get("smoke_backtest") or {}
    smoke_status = smoke.get("status")

    if run_smoke and smoke_status == "FAILED":
        _write_report(base, spec, run_card, None, "FAILED_SMOKE", "smoke FAILED")
        raise RuntimeError("Smoke backtest FAILED; package prohibited.")

    # Determine delivery status per truth table
    if run_smoke:
        if smoke_status == "PASS":
            delivery_status = "DELIVERED"
        elif smoke_status == "BLOCKED":
            if not allow_deferred_smoke:
                _write_report(base, spec, run_card, None, "FAILED_SMOKE",
                              "smoke BLOCKED, allow_deferred_smoke=False")
                raise RuntimeError("Smoke BLOCKED and deferred not allowed.")
            delivery_status = "DELIVERED_WITH_DEFERRED_SMOKE"
        elif smoke_status in (None, "", "NOT_RUN"):
            if not allow_deferred_smoke:
                _write_report(base, spec, run_card, None, "FAILED_SMOKE",
                              "smoke status missing/unknown, allow_deferred_smoke=False")
                raise RuntimeError("Smoke status missing; deferred not allowed.")
            delivery_status = "DELIVERED_WITH_DEFERRED_SMOKE"
        else:
            # Unknown status → ALWAYS fail closed, regardless of allow_deferred_smoke.
            # An unrecognized smoke status must never produce a package, even with
            # explicit deferred authorization — it indicates a pipeline bug or
            # contract violation that must be investigated, not silently deferred.
            _write_report(base, spec, run_card, None, "FAILED_SMOKE",
                          f"unknown smoke status: {smoke_status}")
            raise RuntimeError(f"Unknown smoke status: {smoke_status}")
    else:
        # run_smoke=False
        if not allow_deferred_smoke:
            _write_report(base, spec, run_card, None, "FAILED_SMOKE",
                          "run_smoke=False, allow_deferred_smoke=False")
            raise RuntimeError("Smoke not run and deferred not allowed.")
        delivery_status = "DELIVERED_WITHOUT_SMOKE"

    # --- Stage 4: package (from released wheel) ---
    from quantstudio.strategy_compiler.package.builder import build_strategy_package
    g2_ref = None
    if g2_frozen_dir:
        g2_fd = Path(g2_frozen_dir)
        g2_ref = {
            "reference_signals_path": str(g2_fd / "reference_signals.json"),
            "reference_orders_path": str(g2_fd / "reference_orders.json"),
            "reference_nav_path": str(g2_fd / "reference_nav.json"),
            "source_digest_path": str(g2_fd / "source_digest.json"),
        }
    pkg_dir = build_strategy_package(spec, out_dir=pkg_parent,
                                     package_version=package_version, g2_reference=g2_ref)

    # --- Stage 5: report ---
    _write_report(base, spec, run_card, pkg_dir, delivery_status)
    return base


def _enforce_confirmation_gate(spec: dict) -> None:
    """R2.5 HARD GATE: ALL confirmations must be valid objects with confirmation_id
    and status==CONFIRMED. Any PENDING/REJECTED/missing/invalid → fail closed.
    Checked BEFORE any output work."""
    confirmations = spec.get("user_confirmations", [])
    if not confirmations:
        raise DeliveryConfirmationError(
            "R2.5 HARD GATE: user_confirmations is empty. "
            "User must explicitly confirm before delivery.")
    for i, c in enumerate(confirmations):
        if not isinstance(c, dict):
            raise DeliveryConfirmationError(
                f"R2.5 HARD GATE: confirmation[{i}] is not a valid object: {c}")
        if not c.get("confirmation_id"):
            raise DeliveryConfirmationError(
                f"R2.5 HARD GATE: confirmation[{i}] missing confirmation_id")
        if c.get("status") != "CONFIRMED":
            raise DeliveryConfirmationError(
                f"R2.5 HARD GATE: confirmation[{i}] status={c.get('status')!r} "
                f"(id={c.get('confirmation_id')}); ALL must be CONFIRMED.")


def _write_report(base, spec, run_card, pkg_dir, delivery_status, reason=""):
    """Write DELIVERY_REPORT.md — only lists files that exist."""
    validation = run_card.get("validation", {})
    smoke = run_card.get("smoke_backtest") or {}
    manifest = {}
    if pkg_dir and (pkg_dir / "manifest.json").exists():
        manifest = json.loads((pkg_dir / "manifest.json").read_text(encoding="utf-8"))

    lines = [
        f"# Delivery Report: {spec['strategy_id']}", "",
        f"**Delivery Status**: {delivery_status}", "",
        "## Pipeline status",
        f"- Spec to IR to Renderer: {'PASS' if pkg_dir else 'NOT REACHED'}",
    ]
    for f in _STATIC_FIELDS:
        lines.append(f"- Static {f}: {validation.get(f, 'NOT_RUN')}")
    lines.append(f"- Smoke backtest: {smoke.get('status', 'NOT_RUN')}")
    lines.append(f"- Manifest digest: {'verified' if manifest.get('artifact_digests') else 'N/A'}")
    g2 = manifest.get("g2_reference_closure", {})
    lines.append(f"- G2 linkage: {g2.get('data_digest_status', 'not linked') if g2 else 'not linked'}")
    lines.append("- data_digest_status: blocked (deferred)")
    lines.append("- Real Fidelity/Reference: NOT verified (deferred)")
    if reason:
        lines += ["", f"**Reason**: {reason}"]

    lines += ["", "## Output directories"]
    if (base / "validation").exists():
        lines.append(f"- Validation: `{base / 'validation'}`")
    if pkg_dir and pkg_dir.exists():
        lines.append(f"- Package: `{pkg_dir}`")

    lines += ["", "## Files for the user"]
    listed = False
    if pkg_dir and pkg_dir.exists():
        for f in sorted(pkg_dir.iterdir()):
            if f.is_file():
                lines.append(f"- `{f.name}`")
                listed = True
    val_path = base / "validation"
    if val_path.exists():
        for fn in ("run_card.json", "capability_report.json",
                    "variant_consistency_report.json", "strategy_ir.json"):
            if (val_path / fn).exists():
                lines.append(f"- `validation/{fn}`")
                listed = True
    if not listed:
        lines.append("- (no deliverable files generated)")

    lines += ["", "## Known limitations",
              "- data_digest_status = blocked (deferred)", "- Real Fidelity/Reference deferred",
              "- No real market data / live QMT / resident daemon", ""]

    (base / "DELIVERY_REPORT.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Deliver strategy: validate + package")
    parser.add_argument("spec", help="Path to strategy_spec.json")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--g2-frozen-dir", default=None)
    parser.add_argument("--package-version", default="0.3.0-mvp")
    parser.add_argument("--no-smoke", action="store_true")
    parser.add_argument("--allow-deferred-smoke", action="store_true",
                        help="Explicitly allow package generation with smoke BLOCKED/absent")
    args = parser.parse_args(argv)

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    try:
        base = deliver_strategy(
            spec, out_dir=args.out, g2_frozen_dir=args.g2_frozen_dir,
            package_version=args.package_version, run_smoke=not args.no_smoke,
            allow_deferred_smoke=args.allow_deferred_smoke)
        print(f"delivery complete: {base}")
        return 0
    except DeliveryConfirmationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 5
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 6


if __name__ == "__main__":
    raise SystemExit(main())
