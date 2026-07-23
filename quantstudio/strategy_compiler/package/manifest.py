"""Strategy package manifest writer (G3, audit-fix corrective).

Produces a deterministic manifest.json for a strategy package: version, entry
points, sha256 digests of each NON-MANIFEST artifact (manifest never self-references
its own digest — that chicken-egg is unsolvable), target platforms, and optional G2
reference-closure linkage.

G3 audit-fix changes:
- manifest.json excluded from artifact_digests (self-reference removed).
- README.md included in artifact_digests.
- G2 linkage uses logical artifact IDs + records each frozen artifact's sha256
  (frozen_artifact_digests); no dev-machine absolute paths leak into the manifest.
- render_timestamp is a fixed sentinel (NOT wall-clock) for determinism.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..reference.source_digest import sha256_file

MANIFEST_VERSION = "1.0"
# Fixed sentinel for deterministic builds (never wall-clock).
DETERMINISTIC_RENDER_TIMESTAMP = "1970-01-01T00:00:00+08:00"

# The 4 logical G2 frozen artifact IDs (portable; no absolute paths in manifest).
G2_FROZEN_ARTIFACT_IDS = (
    "reference_signals",
    "reference_orders",
    "reference_nav",
    "source_digest",
)


def digests_of(pkg_dir: Path, filenames: List[str]) -> Dict[str, str]:
    """sha256 (hex) of each packaged artifact file. Caller MUST exclude manifest.json
    (self-reference is unsolvable) and include all other files (incl. README.md)."""
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

    artifact_filenames MUST list every non-manifest packaged file (incl. README.md,
    __init__.py, both rendered strategies, spec, IR). manifest.json is never in the list.
    """
    manifest: Dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "package_version": package_version,
        "strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "target_platforms": sorted(set(target_platforms)),
        "engine_semantics_version": engine_semantics_version,
        "entry_points": entry_points,
        "artifact_digests": digests_of(pkg_dir, artifact_filenames),
        "render_timestamp": DETERMINISTIC_RENDER_TIMESTAMP,
        "builder_version": builder_version,
    }

    if g2_reference is not None:
        manifest["g2_reference_closure"] = _build_g2_linkage(g2_reference)
    else:
        manifest["g2_reference_closure"] = None

    return manifest


def _build_g2_linkage(g2_reference: Dict[str, Any]) -> Dict[str, Any]:
    """Build portable G2 linkage: verify all 4 frozen artifacts exist (fail closed),
    record each sha256 under logical IDs, and pull data_digest_status from source_digest.
    No absolute paths are stored in the returned dict (only logical IDs + digests)."""
    # Map logical ID -> provided path
    path_for = {
        "reference_signals": g2_reference.get("reference_signals_path"),
        "reference_orders": g2_reference.get("reference_orders_path"),
        "reference_nav": g2_reference.get("reference_nav_path"),
        "source_digest": g2_reference.get("source_digest_path"),
    }
    # Fail closed: every one of the 4 must exist
    missing = [art for art, p in path_for.items() if not p or not Path(p).exists()]
    if missing:
        raise G2ReferenceError(
            f"G2 reference linkage missing frozen artifact(s): {missing}. "
            f"Build fails closed rather than silently dropping reference closure.")

    frozen_digests = {art: sha256_file(Path(path_for[art])) for art in G2_FROZEN_ARTIFACT_IDS}

    # Pull data_digest_status + oracle_source_digest from the source_digest file (honest boundary).
    import json
    sd = json.loads(Path(path_for["source_digest"]).read_text(encoding="utf-8"))

    return {
        # Logical artifact IDs only — no absolute paths (portability).
        "frozen_artifact_digests": frozen_digests,
        "data_digest_status": sd.get("data_digest_status", "blocked"),
        "oracle_source_digest": sd.get("oracle_source_digest"),
    }


def write_manifest(manifest: Dict[str, Any], pkg_dir: Path) -> Path:
    """Write manifest.json to pkg_dir ONCE with canonical (sorted, compact) UTF-8 bytes.
    Caller computes digests over already-written artifacts before calling this; manifest
    is never self-referenced, so a single write is correct and stable."""
    path = pkg_dir / "manifest.json"
    from ..reference.source_digest import canonical_json_bytes
    path.write_bytes(canonical_json_bytes(manifest))
    return path


class G2ReferenceError(ValueError):
    """Raised when G2 reference linkage is incomplete (a frozen artifact is missing).
    Build fails closed rather than silently dropping the reference closure."""
