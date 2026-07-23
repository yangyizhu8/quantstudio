"""G3 Strategy Package closure module.

Builds deterministic strategy packages from a strategy_spec:
Spec → IR → dual Renderer (QuantStudio + Strict-PTrade) → structured package.

Reuses the PR6a render.py (golden-protected, profile-mapped) and orchestrator's
IR build. The package adds: manifest, version, entry points, artifact digests,
import boundary (__init__.py), and optional G2 reference-closure linkage.

Boundaries (reviewer-authorized G3 scope):
- No G4 release / CLI E2E / skill packaging.
- No real market data / live QMT / resident daemon.
- Does not modify G2 frozen artifacts or G1-I engine/API/test.
"""
from __future__ import annotations

from .builder import build_strategy_package, PACKAGE_BUILDER_VERSION
from .manifest import build_manifest, write_manifest, G2ReferenceError

__all__ = [
    "build_strategy_package",
    "build_manifest",
    "write_manifest",
    "G2ReferenceError",
    "PACKAGE_BUILDER_VERSION",
]
