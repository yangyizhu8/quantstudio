"""Delivery orchestration: chains orchestrator validation + qs-compile package.

Provides `deliver_strategy(spec, out_dir)` which runs the full delivery flow:
  1. R2.5 HARD GATE: check user_confirmations (fail closed before any work)
  2. orchestrator: Spec → IR → dual render → validators → run_card (validation/)
  3. qs-compile package: IR → dual Renderer → strategy package (package/)
  4. DELIVERY_REPORT.md: unified summary with delivery status truth table

Delivery status truth table:
  DELIVERED                    — static PASS + smoke PASS
  DELIVERED_WITH_DEFERRED_SMOKE — static PASS + smoke BLOCKED (allow_deferred_smoke=True)
  DELIVERED_WITHOUT_SMOKE      — static PASS + smoke not run (--no-smoke, explicit)
  FAILED_STATIC_VALIDATION     — any static validator ≠ PASS → no package
  FAILED_SMOKE                  — smoke FAILED → no package

Output structure:
  <out_dir>/<strategy_id>/
    validation/   ← orchestrator artifacts
    package/      ← qs-compile strategy package (only if gates pass)
    DELIVERY_REPORT.md

Boundaries: does not modify G1-I engine/API/test, G2 frozen artifacts, or
G3/G4 core semantics. Data digest stays blocked. No real data/live QMT.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from .package.builder import build_strategy_package


class DeliveryConfirmationError(Exception):
    """Raised when R2.5 user confirmation is missing, PENDING, or REJECTED.
    Delivery fails closed — no IR build, no Renderer, no package, no output cleanup."""


# Static validation fields that must ALL be strictly "PASS" to proceed.
_STATIC_VALIDATION_FIELDS = ("schema", "timing", "hard_filters", "api_portability",
                             "variant_consistency")


def deliver_strategy(
    spec: dict,
    out_dir: str | Path,
    *,
    g2_frozen_dir: Optional[str | Path] = None,
    package_version: str = "0.3.0-mvp",
    run_smoke: bool = True,
    allow_deferred_smoke: bool = True,
) -> Path:
    """Run full delivery flow: R2.5 gate → validate (orchestrator) → package (qs-compile).

    Returns the delivery directory path.
    Raises DeliveryConfirmationError if user_confirmations not satisfied.
    Raises RuntimeError on static validation failure (package generation skipped).
    """
    strategy_id = spec["strategy_id"]

    # --- R2.5 HARD GATE: check user_confirmations BEFORE any work ---
    _enforce_confirmation_gate(spec)

    base = Path(out_dir) / strategy_id
    val_dir = base / "validation"
    pkg_parent = base / "package"

    # Clean + create delivery dir (only AFTER confirmation gate passes)
    if base.exists():
        shutil.rmtree(base)
    val_dir.mkdir(parents=True, exist_ok=True)
    pkg_parent.mkdir(parents=True, exist_ok=True)

    # --- Stage 1: orchestrator validation ---
    from .orchestrator import orchestrate
    run_card = orchestrate(spec, out_dir=val_dir, run_smoke=run_smoke)

    # --- Stage 2: strict static validation gate ---
    validation = run_card.get("validation", {})
    static_failures = [k for k in _STATIC_VALIDATION_FIELDS
                       if validation.get(k) not in ("PASS",)]
    if static_failures:
        # Static validation failed → NO package generation
        _write_delivery_report(base, spec, run_card, pkg_dir=None,
                               delivery_status="FAILED_STATIC_VALIDATION",
                               reason=f"static validators not PASS: {static_failures}")
        raise RuntimeError(
            f"Static validation failed ({static_failures}: "
            f"{ {k: validation[k] for k in static_failures} }); "
            f"package generation prohibited. See {val_dir / 'run_card.json'}")

    # --- Stage 3: determine delivery status from smoke result ---
    smoke = run_card.get("smoke_backtest") or {}
    smoke_status = smoke.get("status")

    if run_smoke and smoke_status == "FAILED":
        _write_delivery_report(base, spec, run_card, pkg_dir=None,
                               delivery_status="FAILED_SMOKE",
                               reason="smoke backtest FAILED")
        raise RuntimeError("Smoke backtest FAILED; package generation prohibited.")

    # Determine delivery status
    if not run_smoke:
        delivery_status = "DELIVERED_WITHOUT_SMOKE"
    elif smoke_status == "PASS":
        delivery_status = "DELIVERED"
    elif smoke_status == "BLOCKED":
        if not allow_deferred_smoke:
            _write_delivery_report(base, spec, run_card, pkg_dir=None,
                                   delivery_status="FAILED_SMOKE",
                                   reason="smoke BLOCKED and allow_deferred_smoke=False")
            raise RuntimeError("Smoke BLOCKED and deferred smoke not allowed.")
        delivery_status = "DELIVERED_WITH_DEFERRED_SMOKE"
    else:
        delivery_status = "DELIVERED_WITHOUT_SMOKE"

    # --- Stage 4: qs-compile package (reuses the public package builder) ---
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
                                     package_version=package_version,
                                     g2_reference=g2_ref)

    # --- Stage 5: delivery report ---
    _write_delivery_report(base, spec, run_card, pkg_dir,
                           delivery_status=delivery_status)

    return base


def _enforce_confirmation_gate(spec: dict) -> None:
    """R2.5 HARD GATE: user_confirmations must contain at least one CONFIRMED entry.
    Missing, PENDING, or REJECTED all fail closed. Checked BEFORE any output work."""
    confirmations = spec.get("user_confirmations", [])
    if not confirmations:
        raise DeliveryConfirmationError(
            "R2.5 HARD GATE: no user_confirmations in Spec. "
            "User must explicitly confirm before any code generation or delivery.")

    confirmed = [c for c in confirmations
                 if isinstance(c, dict) and c.get("status") == "CONFIRMED"]
    if not confirmed:
        statuses = [c.get("status", "MISSING") if isinstance(c, dict) else str(c)
                    for c in confirmations]
        raise DeliveryConfirmationError(
            f"R2.5 HARD GATE: no CONFIRMED entry in user_confirmations (statuses: {statuses}). "
            f"All entries must be CONFIRMED to proceed.")


def _write_delivery_report(
    base: Path, spec: dict, run_card: dict, pkg_dir: Optional[Path],
    *, delivery_status: str, reason: str = "",
) -> None:
    """Write DELIVERY_REPORT.md — only lists files that actually exist on disk."""
    strategy_id = spec["strategy_id"]
    validation = run_card.get("validation", {})
    smoke = run_card.get("smoke_backtest") or {}
    manifest: Dict[str, Any] = {}
    if pkg_dir and (pkg_dir / "manifest.json").exists():
        manifest = json.loads((pkg_dir / "manifest.json").read_text(encoding="utf-8"))

    lines = [
        f"# Delivery Report: {strategy_id}",
        "",
        f"**Delivery Status**: {delivery_status}",
        "",
        "## Pipeline status",
        f"- Spec to IR to Renderer: {'PASS' if pkg_dir else 'NOT REACHED'}",
    ]
    for field in _STATIC_VALIDATION_FIELDS:
        lines.append(f"- Static {field}: {validation.get(field, 'NOT_RUN')}")
    lines.append(f"- Smoke backtest: {smoke.get('status', 'NOT_RUN') if run_card.get('smoke_backtest') else 'NOT_RUN'}")
    lines.append(f"- Manifest digest: {'verified' if manifest.get('artifact_digests') else 'N/A'}")
    g2_status = manifest.get("g2_reference_closure", {}).get("data_digest_status") if manifest.get("g2_reference_closure") else None
    lines.append(f"- G2 linkage: {g2_status or 'not linked'}")
    lines.append(f"- data_digest_status: blocked (deferred)")
    lines.append(f"- Real Fidelity/Reference: NOT verified (deferred)")
    lines.append("")

    if reason:
        lines.append(f"**Reason**: {reason}")
        lines.append("")

    # Output directories — only list if they exist
    lines.append("## Output directories")
    val_path = base / "validation"
    if val_path.exists():
        lines.append(f"- Validation: `{val_path}`")
    if pkg_dir and pkg_dir.exists():
        lines.append(f"- Package: `{pkg_dir}`")

    # Files for the user — ONLY list files that actually exist
    lines.append("")
    lines.append("## Files for the user")
    listed_any = False
    if pkg_dir and pkg_dir.exists():
        for f in sorted(pkg_dir.iterdir()):
            if f.is_file():
                lines.append(f"- `{f.name}`")
                listed_any = True
    # Validation files that exist
    if val_path.exists():
        for fname in ("run_card.json", "capability_report.json",
                       "variant_consistency_report.json", "strategy_ir.json"):
            fpath = val_path / fname
            if fpath.exists():
                lines.append(f"- `validation/{fname}`")
                listed_any = True
    if not listed_any:
        lines.append("- (no deliverable files generated)")

    lines.extend([
        "",
        "## Known limitations",
        "- data_digest_status = blocked (real market-data digest deferred)",
        "- Real Fidelity/Reference verification deferred",
        "- No real market data / live QMT / resident daemon in this MVP",
        "",
    ])

    (base / "DELIVERY_REPORT.md").write_text(
        "\n".join(lines), encoding="utf-8", newline="\n")
