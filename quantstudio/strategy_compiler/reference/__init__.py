"""CP3 Reference closure package [Strategy Compiler G2].

Independent hand-written Reference Oracle artifacts: frozen signal/order/NAV
reference outputs + source/data/config digests + run_card reference_closure.

Core relationship (anti-circular-validation, pr6b2a-plan §CP3):
    Independent Reference Oracle  →  frozen signal/order/NAV artifacts
    G1-I basket engine            →  runtime-captured actual output
    reference vs engine contrast  (Oracle is truth source; engine is validated object)

The Oracle itself (tests/strategy_references/etf_rotation_ref.py) is NOT modified
to mirror G1-I internals. This package only runs the Oracle against controlled
hermetic inputs and serializes its outputs as deterministic frozen artifacts.
"""
from __future__ import annotations

from .source_digest import (
    sha256_bytes, sha256_file, sha256_json, compute_source_digest,
    ARTIFACT_SCHEMA_VERSION,
)
from .artifact_builder import (
    build_reference_artifacts, ReferenceArtifacts,
)
from .run_card_writer import attach_reference_closure

__all__ = [
    "sha256_bytes", "sha256_file", "sha256_json", "compute_source_digest",
    "ARTIFACT_SCHEMA_VERSION",
    "build_reference_artifacts", "ReferenceArtifacts",
    "attach_reference_closure",
]
