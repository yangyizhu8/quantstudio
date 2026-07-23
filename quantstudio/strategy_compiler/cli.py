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
    # build_strategy_package also raises G2ReferenceError (fail-closed) which propagulates
    # as an uncaught exception -> non-zero exit + traceback (honest failure surfacing).

    print(f"package built: {pkg_dir}")
    manifest = json.loads((pkg_dir / "manifest.json").read_text(encoding="utf-8"))
    print(f"  strategy_id={manifest['strategy_id']} version={manifest['package_version']}")
    print(f"  platforms={manifest['target_platforms']}")
    if manifest.get("g2_reference_closure"):
        g2 = manifest["g2_reference_closure"]
        print(f"  g2_reference_closure: data_digest_status={g2['data_digest_status']}")
    return 0


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
    p_pkg.add_argument("--package-version", default="0.1.0", help="Package semver (default 0.1.0)")
    p_pkg.set_defaults(func=cmd_package)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # ensure --out is a Path
    if hasattr(args, "out"):
        args.out = Path(args.out)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
