"""Strategy package manifest writer (G3).

Produces a deterministic manifest.json for a strategy package: version, entry
points, sha256 digests of each artifact, target platforms, and optional G2
reference-closure linkage. render_timestamp is a fixed sentinel (NOT wall-clock)
so two builds of the same package are byte-identical.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..reference.source_digest import sha256_file

MANIFEST_VERSION = "1.0"
# Fixed sentinel for deterministic builds (never wall-clock).
DETERMINISTIC_RENDER_TIMESTAMP = "1970-01-01T00:00:00+08:00"


def _digests_of(pkg_dir: Path, filenames: List[str]) -> Dict[str, str]:
    """sha256 (hex) of each packaged artifact file."""
    out: Dict[str, str] = {}
    for fn in filenames:
        p = pkg_dir / fn
        if p.exists():
            out[fn] = sha256_file(p)
    return out


def build_manifest(
    *,
    strategy_id: str,
    strategy_name: str,
    package_version: str,
    target_platforms: List[str],
    engine_semantics_version: str,
    entry_points: List[Dict[str, str]],
    artifact_filenames: List[str],
    pkg_dir: Path,
    g2_reference: Optional[Dict[str, Any]] = None,
    builder_version: str,
) -> Dict[str, Any]:
    """Construct the manifest dict (validated against strategy_package_manifest.schema.json).

    g2_reference: optional dict of G2 frozen-closure paths; when present the
    manifest records data_digest_status from the G2 source_digest (proving the
    reference closure is tracked, not bypassed).
    """
    manifest: Dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "package_version": package_version,
        "strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "target_platforms": sorted(set(target_platforms)),
        "engine_semantics_version": engine_semantics_version,
        "entry_points": entry_points,
        "artifact_digests": _digests_of(pkg_dir, artifact_filenames),
        "render_timestamp": DETERMINISTIC_RENDER_TIMESTAMP,
        "builder_version": builder_version,
    }

    if g2_reference is not None:
        # Read G2 source_digest to record the (deferred) data-digest boundary honestly.
        sd_path = g2_reference.get("source_digest_path")
        g2_block: Dict[str, Any] = {
            "reference_signals_path": g2_reference.get("reference_signals_path"),
            "reference_orders_path": g2_reference.get("reference_orders_path"),
            "reference_nav_path": g2_reference.get("reference_nav_path"),
            "source_digest_path": sd_path,
        }
        if sd_path and Path(sd_path).exists():
            sd = json.loads(Path(sd_path).read_text(encoding="utf-8"))
            g2_block["data_digest_status"] = sd.get("data_digest_status", "blocked")
            g2_block["oracle_source_digest"] = sd.get("oracle_source_digest")
        else:
            g2_block["data_digest_status"] = "blocked"
            g2_block["oracle_source_digest"] = None
        manifest["g2_reference_closure"] = g2_block
    else:
        manifest["g2_reference_closure"] = None

    return manifest


def write_manifest(manifest: Dict[str, Any], pkg_dir: Path) -> Path:
    """Write manifest.json to pkg_dir with canonical (sorted, compact) UTF-8 bytes."""
    path = pkg_dir / "manifest.json"
    from ..reference.source_digest import canonical_json_bytes
    path.write_bytes(canonical_json_bytes(manifest))
    return path
