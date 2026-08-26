"""G4 Strategy Compiler CLI: end-to-end Spec → IR → dual Renderer → strategy package.

Provides the `qs-compile` / `python -m quantstudio.strategy_compiler.cli` entry point.
The `package` subcommand wires the G3 package builder into a CLI flow that:
- reads + validates a strategy_spec;
- builds the dual-rendered strategy package (QuantStudio + Strict-PTrade);
- retains G3 manifest/digest verification;
- propagates Golden Protection and invalid-spec failures honestly (non-zero exit);
- optionally links G2 frozen closure (data_digest_status recorded honestly, never faked).

Boundaries (G4 release): no real market data / live QMT / resident daemon; no faked
data digest; real Fidelity/Reference stays deferred.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .contracts import validate_strategy_spec
from .package.builder import build_strategy_package
from .package.manifest import G2ReferenceError
from .render import GoldenProtectionError


def _load_spec(spec_path: Path) -> dict:
    """Load + JSON-parse a strategy_spec; raise ValueError on bad JSON."""
    try:
        return json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON in {spec_path}: {e}") from e


def _g2_reference_from_dir(g2_frozen_dir: Optional[Path]) -> Optional[dict]:
    """Build a g2_reference dict from a frozen-closure dir (4 artifacts), or None."""
    if g2_frozen_dir is None:
        return None
    return {
        "reference_signals_path": str(g2_frozen_dir / "reference_signals.json"),
        "reference_orders_path": str(g2_frozen_dir / "reference_orders.json"),
        "reference_nav_path": str(g2_frozen_dir / "reference_nav.json"),
        "source_digest_path": str(g2_frozen_dir / "source_digest.json"),
    }


def cmd_package(args: argparse.Namespace) -> int:
    """`package <spec> --out <dir> [--g2-frozen-dir <dir>] [--package-version <v>]`."""
    spec_path = Path(args.spec)
    if not spec_path.exists():
        print(f"ERROR: spec file not found: {spec_path}", file=sys.stderr)
        return 2
    try:
        spec = _load_spec(spec_path)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    # Schema-validate the spec (fail closed on invalid). validate_strategy_spec
    # raises ContractValidationError on invalid; we catch + surface honestly.
    from .contracts import ContractValidationError
    try:
        validate_strategy_spec(spec)
    except ContractValidationError as e:
        print(f"ERROR: spec failed schema validation: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # other contract errors (missing keys, etc.)
        print(f"ERROR: spec validation error: {e}", file=sys.stderr)
        return 2

    g2_ref = _g2_reference_from_dir(Path(args.g2_frozen_dir) if args.g2_frozen_dir else None)

    try:
        pkg_dir = build_strategy_package(
            spec, out_dir=args.out,
            package_version=args.package_version,
            g2_reference=g2_ref,
        )
    except GoldenProtectionError as e:
        # Honest propagation: golden-protected IDs are not packageable.
        print(f"ERROR: golden protection — {e}", file=sys.stderr)
        return 3
    except G2ReferenceError as e:
        # G2 reference linkage incomplete (missing frozen artifact) — fail closed
        # with a stable user-level message + deterministic exit code (no traceback).
        print(f"ERROR: G2 reference closure incomplete — {e}", file=sys.stderr)
        return 4

    print(f"package built: {pkg_dir}")
    manifest = json.loads((pkg_dir / "manifest.json").read_text(encoding="utf-8"))
    print(f"  strategy_id={manifest['strategy_id']} version={manifest['package_version']}")
    print(f"  platforms={manifest['target_platforms']}")
    if manifest.get("g2_reference_closure"):
        g2 = manifest["g2_reference_closure"]
        print(f"  g2_reference_closure: data_digest_status={g2['data_digest_status']}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """`import <strategy.py> [--out <dir>] [--start <date>] [--end <date>] [--no-smoke]`."""
    source_path = Path(args.strategy)
    if not source_path.exists():
        print(f"ERROR: strategy file not found: {source_path}", file=sys.stderr)
        return 2
    if source_path.suffix != ".py":
        print(f"ERROR: strategy file must be .py: {source_path}", file=sys.stderr)
        return 2
    from .orchestrator import orchestrate_source
    try:
        run_card = orchestrate_source(
            source_path,
            start=args.start, end=args.end,
            out_dir=Path(args.out) if args.out else None,
            run_smoke=not args.no_smoke,
            strict=True,
            etf_pool_start_date=args.etf_pool_start_date,
            db_path=Path(args.db_path) if args.db_path else None,
            exclude_bse=getattr(args, 'exclude_bse', False),  # P-D13 C1b
        )
    except GoldenProtectionError as e:
        print(f"ERROR: golden protection — {e}", file=sys.stderr)
        return 3
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    base_out = Path(args.out) if args.out else Path("output/ptrade_export")
    out_dir = base_out / run_card["strategy_id"]
    print(f"run_card written: {out_dir / 'run_card.json'}")
    print(f"  stage={run_card['stage']} status={run_card['status']}")
    print(f"  validation={run_card['validation']}")
    if run_card.get("smoke_backtest"):
        print(f"  smoke={run_card['smoke_backtest']['status']}")
    return 0 if run_card["status"] == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qs-compile",
        description="Strategy Compiler CLI: Spec → IR → dual Renderer → strategy package",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_pkg = sub.add_parser("package", help="Build a dual-rendered strategy package from a spec")
    p_pkg.add_argument("spec", help="Path to strategy_spec.json")
    p_pkg.add_argument("--out", required=True, help="Output directory for the package")
    p_pkg.add_argument("--g2-frozen-dir", default=None,
                       help="Directory with G2 frozen reference artifacts (4 files)")
    p_pkg.add_argument("--package-version", default="0.3.2-mvp", help="Package semver (default 0.3.2-mvp)")
    p_pkg.set_defaults(func=cmd_package)

    p_imp = sub.add_parser("import", help="Convert a local strategy .py to PTrade (source entry)")
    p_imp.add_argument("strategy", help="Path to local strategy .py (quantstudio/backtest/strategies/*.py)")
    p_imp.add_argument("--out", default=None, help="Output directory (default: output/ptrade_export/<strategy_id>)")
    p_imp.add_argument("--start", default=None, help="Round-trip smoke backtest start (YYYY-MM-DD)")
    p_imp.add_argument("--end", default=None, help="Round-trip smoke backtest end (YYYY-MM-DD)")
    p_imp.add_argument("--no-smoke", action="store_true", help="Skip the round-trip smoke backtest step")
    p_imp.add_argument("--etf-pool-start-date", default=None,
                       help="ETF 静态池固化起始日 YYYY-MM-DD（策略含 get_etf_list_local 时必填，见 07 规格）")
    p_imp.add_argument("--db-path", default=None,
                       help="查 etf_basic 的库路径（默认 data/quantstudio.db；T5 staging 副本场景传副本路径）")
    p_imp.add_argument("--exclude-bse", action="store_true", default=False,
                       help="北交所过滤（对齐平台 get_Ashares 不含 920xxx 口径；P-D13 C1b）")  # P-D13
    p_imp.set_defaults(func=cmd_import)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # ensure --out is a Path（None → 由 cmd_import 使用默认 output/ptrade_export/<strategy_id>）
    if hasattr(args, "out") and args.out is not None:
        args.out = Path(args.out)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
