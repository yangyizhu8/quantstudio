"""Delivery orchestration: chains orchestrator validation + qs-compile package.

Provides `deliver_strategy(spec, out_dir)` which runs the full delivery flow:
  1. orchestrator: Spec → IR → dual render → validators → run_card (validation/)
  2. qs-compile package: IR → dual Renderer → strategy package (package/)
  3. DELIVERY_REPORT.md: unified summary

Output structure:
  <out_dir>/<strategy_id>/
    validation/   ← orchestrator artifacts
    package/      ← qs-compile strategy package
    DELIVERY_REPORT.md

Boundaries: does not modify G1-I engine/API/test, G2 frozen artifacts, or
G3/G4 core semantics. Data digest stays blocked. No real data/live QMT.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from .cli import build_strategy_package  # reuse CLI's build entry
from .package.builder import build_strategy_package as _build_pkg


def deliver_strategy(
    spec: dict,
    out_dir: str | Path,
    *,
    g2_frozen_dir: Optional[str | Path] = None,
    package_version: str = "0.3.0-mvp",
    run_smoke: bool = True,
) -> Path:
    """Run full delivery flow: validate (orchestrator) + package (qs-compile) + report.

    Returns the delivery directory path.
    Raises on validation failure (static validation must pass to proceed to package).
    """
    strategy_id = spec["strategy_id"]
    base = Path(out_dir) / strategy_id
    val_dir = base / "validation"
    pkg_parent = base / "package"

    # Clean + create delivery dir
    if base.exists():
        shutil.rmtree(base)
    val_dir.mkdir(parents=True, exist_ok=True)
    pkg_parent.mkdir(parents=True, exist_ok=True)

    # --- Stage 1: orchestrator validation ---
    from .orchestrator import orchestrate
    run_card = orchestrate(
        spec,
        out_dir=val_dir,
        run_smoke=run_smoke,
    )

    # Check static validation gate
    validation = run_card.get("validation", {})
    static_ok = all(
        v in ("PASS", "BLOCKED") for v in validation.values()
        if isinstance(v, str)
    )
    if not static_ok:
        _write_delivery_report(base, spec, run_card, None, status="FAILED",
                               reason="static validation failed; package generation skipped")
        raise RuntimeError(
            f"Static validation failed ({validation}); cannot proceed to package generation. "
            f"See {val_dir / 'run_card.json'}")

    # --- Stage 2: qs-compile package ---
    g2_ref = None
    if g2_frozen_dir:
        g2_fd = Path(g2_frozen_dir)
        g2_ref = {
            "reference_signals_path": str(g2_fd / "reference_signals.json"),
            "reference_orders_path": str(g2_fd / "reference_orders.json"),
            "reference_nav_path": str(g2_fd / "reference_nav.json"),
            "source_digest_path": str(g2_fd / "source_digest.json"),
        }

    pkg_dir = _build_pkg(spec, out_dir=pkg_parent, package_version=package_version,
                         g2_reference=g2_ref)

    # --- Stage 3: delivery report ---
    _write_delivery_report(base, spec, run_card, pkg_dir, status="DELIVERED")

    return base


def _write_delivery_report(
    base: Path, spec: dict, run_card: dict, pkg_dir: Optional[Path],
    *, status: str, reason: str = "",
) -> None:
    """Write DELIVERY_REPORT.md summarizing the full delivery."""
    strategy_id = spec["strategy_id"]
    validation = run_card.get("validation", {})
    smoke = run_card.get("smoke_backtest") or {}
    manifest = {}
    if pkg_dir and (pkg_dir / "manifest.json").exists():
        manifest = json.loads((pkg_dir / "manifest.json").read_text(encoding="utf-8"))

    lines = [
        f"# Delivery Report: {strategy_id}",
        "",
        f"**Status**: {status}",
        f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M')} (build env)",
        "",
        "## Pipeline status",
        f"- Spec → IR → Renderer: {'PASS' if status == 'DELIVERED' else 'FAILED'}",
        f"- Static validation: {validation}",
        f"- Smoke backtest: {smoke.get('status', 'N/A')}",
        f"- Manifest digest: {'verified' if manifest.get('artifact_digests') else 'N/A'}",
        f"- G2 linkage: {manifest.get('g2_reference_closure', {}).get('data_digest_status', 'not linked') if manifest.get('g2_reference_closure') else 'not linked'}",
        f"- data_digest_status: blocked (deferred — not faked)",
        f"- Real Fidelity/Reference: NOT verified (deferred)",
        "",
    ]

    if reason:
        lines.append(f"**Reason**: {reason}")
        lines.append("")

    lines.extend([
        "## Output directories",
        f"- Validation: `{base / 'validation'}`",
    ])
    if pkg_dir:
        lines.append(f"- Package: `{pkg_dir}`")

    lines.extend([
        "",
        "## Files for the user",
    ])
    if pkg_dir:
        lines.append(f"- Strategy package: `{pkg_dir}`")
        lines.append(f"  - `{manifest.get('strategy_id', strategy_id)}_quantstudio.py`")
        lines.append(f"  - `{manifest.get('strategy_id', strategy_id)}_ptrade.py`")
        lines.append(f"  - `manifest.json`")
        lines.append(f"  - `strategy_spec.json` / `strategy_ir.json`")
    lines.extend([
        f"- Validation report: `{base / 'validation' / 'run_card.json'}`",
        f"- Capability report: `{base / 'validation' / 'capability_report.json'}`",
        "",
        "## Known limitations",
        "- data_digest_status = blocked (real market-data digest deferred)",
        "- Real Fidelity/Reference verification deferred",
        "- No real market data / live QMT / resident daemon in this MVP",
        "",
    ])

    (base / "DELIVERY_REPORT.md").write_text(
        "\n".join(lines), encoding="utf-8", newline="\n")
