"""Source/data/config/engine digests for CP3 Reference closure.

review 硬要求：digest 不能只含 Oracle 源码 hash。必须覆盖 oracle_source /
spec / artifact_schema / input_data / config / engine_commit / engine_semantics_version。
若当前 CP3 无法生成真实 data digest，必须明确标 blocked，不得用文件存在冒充冻结证据。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ARTIFACT_SCHEMA_VERSION = "1.0"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    """Deterministic SHA-256 over a file's content, CRLF-safe.

    Reads git blob bytes (LF-normalized) when the path is inside a git repo so
    the digest is identical regardless of core.autocrlf / platform line endings.
    Falls back to raw bytes (LF-normalized in-memory) for non-repo paths. This is
    required for byte-level determinism of frozen artifacts and provenance hashes.
    """
    p = Path(path)
    data = _git_blob_bytes(p)
    if data is None:
        # Not in a repo / not tracked: normalize CRLF→LF in memory for cross-platform stability.
        data = p.read_bytes().replace(b"\r\n", b"\n")
    return sha256_bytes(data)


def _git_blob_bytes(path: Path) -> bytes | None:
    """Return the LF-normalized bytes of `path` as git stores it (index blob), or None if unavailable."""
    import subprocess
    try:
        rel = _repo_relative(path)
    except Exception:
        return None
    if rel is None:
        return None
    try:
        # `git show :<path>` reads the staged/index blob (LF-normalized by git).
        out = subprocess.run(
            ["git", "show", f":{rel}"], capture_output=True, cwd=str(path.parent),
            check=False,
        )
        if out.returncode == 0:
            return out.stdout
        # Fallback: HEAD blob (for committed-but-not-staged-identical files).
        out = subprocess.run(
            ["git", "show", f"HEAD:{rel}"], capture_output=True, cwd=str(path.parent),
            check=False,
        )
        return out.stdout if out.returncode == 0 else None
    except Exception:
        return None


def _repo_relative(path: Path) -> str | None:
    """Return path relative to repo root if tracked, else None."""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True,
            cwd=str(path.parent if path.is_file() else str(path)), check=False,
        )
        if out.returncode != 0:
            return None
        root = out.stdout.decode("utf-8", "replace").strip()
        if not root:
            return None
        try:
            rel = Path(path).resolve().relative_to(Path(root).resolve())
        except ValueError:
            return None
        rel_str = str(rel).replace("\\", "/")
        return rel_str
    except Exception:
        return None


def sha256_json(obj: Any) -> str:
    """Deterministic SHA-256 over a JSON-serializable object (sorted keys, no whitespace)."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256_bytes(blob)


def canonical_json_bytes(obj: Any) -> bytes:
    """Canonical UTF-8 JSON bytes (sorted keys, compact, LF) for byte-level digest/reproducibility."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_json_digest(obj: Any) -> str:
    """Deterministic SHA-256 over the canonical JSON bytes of an artifact dict.

    Used to prove frozen artifacts are byte-reproducible (independent of generated_at
    when generated_at is fixed, and independent of dict insertion order / platform).
    """
    return sha256_bytes(canonical_json_bytes(obj))


def compute_source_digest(
    oracle_path: str | Path,
    spec_path: str | Path,
    artifact_schema_paths: list,
    config_spec: dict,
    engine_commit: str,
    engine_semantics_version: str,
    input_data_digest: str | None = None,
    data_digest_status: str = "blocked",
    data_digest_block_reason: str | None = None,
    provenance_ref: str | None = None,
    strategy_id: str = "etf_regression_rotation_v1",
) -> dict:
    """Build the source_digest artifact dict (validated against reference_source_digest.schema.json).

    Args:
        oracle_path: Oracle source file (.py).
        spec_path: frozen Spec (.json).
        artifact_schema_paths: list of the 4 reference_*.schema.json file paths
            (signals/orders/nav/source_digest); hashed in fixed order.
        config_spec: the frozen Spec dict (config_digest = sha256 over its frozen bytes).
        input_data_digest: real input-data digest, or None if not yet producible.
        data_digest_status: "frozen" if input_data_digest present, else "blocked".
    """
    oracle_digest = sha256_file(oracle_path)
    spec_digest = sha256_file(spec_path)
    # artifact_schema_digest: concatenation of the 4 schema files in fixed order
    schema_blob = b"".join(Path(p).read_bytes() for p in artifact_schema_paths)
    artifact_schema_digest = sha256_bytes(schema_blob)
    config_digest = sha256_json(config_spec)

    if input_data_digest is None and data_digest_status != "blocked":
        # Defensive: enforce honest blocked labeling when no real data digest exists.
        data_digest_status = "blocked"
        if data_digest_block_reason is None:
            data_digest_block_reason = "no real input-data digest available"

    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "strategy_id": strategy_id,
        "oracle_source_digest": oracle_digest,
        "spec_digest": spec_digest,
        "artifact_schema_digest": artifact_schema_digest,
        "input_data_digest": input_data_digest,
        "data_digest_status": data_digest_status,
        "data_digest_block_reason": data_digest_block_reason,
        "config_digest": config_digest,
        "engine_commit": engine_commit,
        "engine_semantics_version": engine_semantics_version,
        "provenance_ref": provenance_ref or "tests/strategy_references/reference_provenance.json",
    }
