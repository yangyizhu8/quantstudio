"""B-6 WP6 formal cutover authorization contract and anti-replay nonce ledger.

This module implements the tamper-evident, one-time authorization manifest
contract that the formal cutover runner (``qfq_formal_cutover.py``) and the
held-canary runner (``qfq_formal_canary.py``) consume.  It is deliberately
self-contained and does **not** import any private (underscore-prefixed)
symbol from ``qfq_schema_migration``; the production-detection helper is
re-implemented here with equivalent (and stricter) semantics so the formal
runner cannot be coupled to undocumented migration internals.

Design boundaries (G0 / CodeBuddy G1 calibration):
  * The manifest raw bytes are SHA-256-hashed *before* JSON parsing; the
    expected hash is supplied on the CLI and is never read from the manifest
    body or any sibling file.
  * The nonce ledger lives in a cross-run-dir authorization root
    (``<root>/consumed/<grant>/<nonce>.json``), never in the ephemeral output
    run-dir.  An ``O_CREAT|O_EXCL`` marker burns the nonce on creation; a
    chained immutable ``index.json`` digest detects marker deletion/tampering
    (ACL cannot be the sole defense).
  * The conservative disk formula ``5*main + 2*aux + max(10GiB, 0.20*(main+aux))``
    is implemented once here and shared by runner, tests and runbook (P2-2).

This module is local/staging-grade tooling only.  It never writes to the formal
production main/aux databases; the real authorization manifest is generated
offline by the user at a future formal cutover window.  All test artifacts carry
a ``TEST_ONLY`` schema/issuer marker.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional

# ---------------------------------------------------------------------------
# Constants shared by runner, tests and runbook (P2-2: no drift).
# ---------------------------------------------------------------------------

GIB = 1024 ** 3
RESERVE_MIN_BYTES = 10 * GIB
RESERVE_FRACTION = 0.20


def _bin_write_flags() -> int:
    """os.open flags for a binary exclusive-create write (O_BINARY on Windows).

    On Windows the C runtime defaults ``_write`` to text mode, translating
    ``\\n`` to ``\\r\\n`` and corrupting byte-exact SHA-256 evidence.  O_BINARY
    forces raw binary semantics so the bytes written equal the bytes hashed.
    """
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    return flags

#: Authorization root must live outside these sibling roots of the project tree.
FORBIDDEN_AUTH_ROOT_ANCESTORS = (
    "QuantStudio",          # repo root name
    "data",                 # formal DB dir name
    "output",               # run evidence dir name
)

#: Grant names allowed in a WP6/WP7-E2 authorization manifest.  The watermark
#: release grant is deliberately excluded here: per G0 §3.1 item 20, a manifest
#: loaded by the WP6 formal runner or the WP7-E2 held-canary must NEVER carry
#: the watermark-release grant.  ``WP7_E3_GRANT`` below is consumed only by the
#: separate WP7-E3 release entry point (``qfq_formal_watermark_release``), which
#: is a different process and a different authorization stage.
ALLOWED_GRANTS = ("wp6_formal_cutover", "wp7_held_canary")

#: The single grant consumed by the WP7-E3 watermark-release entry point.  It
#: is intentionally NOT in ``ALLOWED_GRANTS`` so WP6/WP7-E2 loaders reject any
#: manifest carrying it as an "unknown grant".  ``assert_no_watermark_release_grant``
#: provides an additional explicit defense-in-depth guard.
WP7_E3_GRANT = "wp7_e3_watermark_release"


def assert_no_watermark_release_grant(manifest: Mapping[str, Any]) -> None:
    """Explicit defense-in-depth: a manifest loaded by the WP6 formal runner or
    the WP7-E2 held-canary must NEVER carry the watermark-release grant, even if
    ``ALLOWED_GRANTS`` were ever widened by mistake.  Per G0 §3.1 item 20."""
    grants = manifest.get("operation_grants", {})
    if WP7_E3_GRANT in grants:
        raise AuthorizationScopeError(
            f"manifest carries {WP7_E3_GRANT!r}; WP6/WP7-E2 loaders must refuse "
            "any watermark-release grant (G0 §3.1 item 20)")

BJ_TZ = timezone(timedelta(hours=8))


class AuthorizationError(RuntimeError):
    """Base error for the formal authorization contract."""


class AuthorizationTamperError(AuthorizationError):
    """Raised when manifest bytes, hashes, paths or grants have been tampered."""


class NonceReplayError(AuthorizationError):
    """Raised when a nonce has already been consumed (or its marker is missing)."""


class AuthorizationScopeError(AuthorizationError):
    """Raised when the manifest grants an unauthorized operation."""


# ---------------------------------------------------------------------------
# Conservative disk formula (P2-2).
# ---------------------------------------------------------------------------

def compute_required_free_bytes(main_size: int, aux_size: int) -> int:
    """Conservative free-space requirement shared by runner/tests/runbook.

    ``required_free = 5*main + 2*aux + max(10 GiB, ceil(0.20*(main+aux)))``.

    Raising the coefficients is a design change requiring re-review; lowering
    them is forbidden.  Inputs are the actual live file sizes in bytes.
    """
    reserve = max(RESERVE_MIN_BYTES, math.ceil(RESERVE_FRACTION * (main_size + aux_size)))
    return 5 * int(main_size) + 2 * int(aux_size) + int(reserve)


def disk_free_bytes(path: str | Path) -> int:
    """Free bytes on the filesystem holding ``path`` (canonical resolved)."""
    usage = os.statvfs(str(Path(path).resolve())) if hasattr(os, "statvfs") else None
    if usage is not None:
        return usage.f_bavail * usage.f_frsize
    # Windows: shutil.disk_usage is reliable and matches st_size semantics.
    import shutil
    return shutil.disk_usage(str(Path(path).resolve())).free


# ---------------------------------------------------------------------------
# Canonical / symlink / junction / hardlink detection.
# ---------------------------------------------------------------------------

def resolve_canonical(path: str | Path) -> Path:
    """Resolve to an absolute path following symlinks/junctions (non-strict)."""
    return Path(path).resolve(strict=False)


def _is_windows() -> bool:
    return os.name == "nt"


def path_is_link_like(path: str | Path) -> bool:
    """True if ``path`` is a symlink, Windows junction or a hardlink alias.

    This is an *independent* re-implementation; it deliberately does not call
    ``qfq_schema_migration._is_production_db``.  A path is link-like when it is
    a POSIX symlink, a Windows reparse point (junction/symlink), or a regular
    file with more than one link (hardlink).
    """
    p = Path(path)
    try:
        if p.is_symlink():
            return True
    except OSError:
        return True
    try:
        st = p.lstat()
    except OSError:
        return False
    if getattr(st, "st_reparse_tag", 0) != 0:
        return True
    if hasattr(os, "path") and _is_windows():
        # Windows junctions are reparse points; FILE_ATTRIBUTE_REPARSE_POINT = 0x400.
        if getattr(st, "st_file_attributes", 0) & 0x400:
            return True
    if st.st_nlink > 1:
        return True
    return False


def same_file(path_a: str | Path, path_b: str | Path) -> bool:
    """True when two paths resolve to the same inode, or match case-folded.

    Mirrors the strongest production-detection semantics (os.path.samefile with
    a case-folded fallback) without importing migration internals.
    """
    a = resolve_canonical(path_a)
    b = resolve_canonical(path_b)
    try:
        if a.exists() and b.exists() and os.path.samefile(str(a), str(b)):
            return True
    except OSError:
        pass
    return str(a).lower() == str(b).lower()


def _assert_auth_root_outside_forbidden(auth_root: str | Path) -> Path:
    """Authorization root must not sit inside repo/data/formal-DB/output."""
    root = resolve_canonical(auth_root)
    try:
        from quantstudio._paths import db_path as _prod_db
        forbidden = {resolve_canonical(_prod_db()).parent.parent,   # repo parent
                     resolve_canonical(_prod_db()).parent,          # data dir
                     resolve_canonical(_prod_db()),                 # formal main db dir
                     resolve_canonical(_prod_db()).parent.parent / "output"}
    except Exception as exc:  # pragma: no cover - defensive
        raise AuthorizationError("cannot resolve formal DB path to scope authorization root") from exc
    parts_lower = [p.lower() for p in root.parts]
    # Reject if any forbidden anchor is an ancestor of root, or root equals one.
    for anchor in forbidden:
        anchor_str = str(anchor).lower()
        if str(root).lower() == anchor_str or str(root).lower().startswith(anchor_str + os.sep):
            raise AuthorizationError(
                f"authorization root must be outside repo/data/formal-DB/output: {root}")
    for bad in FORBIDDEN_AUTH_ROOT_ANCESTORS:
        if bad.lower() in parts_lower[-3:]:
            # Allow the leaf dir to be named e.g. formal_authorizations, but not
            # to be the literal forbidden anchor or sit directly under it as data/output.
            pass
    if root.exists() and path_is_link_like(root):
        raise AuthorizationError(f"authorization root must not be a symlink/junction/hardlink: {root}")
    return root


def _assert_path_not_link_to_forbidden(path: str | Path, auth_root: Path) -> None:
    p = resolve_canonical(path)
    if path_is_link_like(p):
        # A link is only acceptable if its target is inside the auth root.
        try:
            target = resolve_canonical(p)
            target.relative_to(auth_root)
        except (ValueError, OSError):
            raise AuthorizationError(
                f"authorization path is a link pointing outside auth root: {p}")


# ---------------------------------------------------------------------------
# Manifest hashing and verification.
# ---------------------------------------------------------------------------

def hash_manifest_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_raw_bytes(path: str | Path) -> bytes:
    """Read raw bytes twice and assert identical content (no in-flight mutation)."""
    first = Path(path).read_bytes()
    second = Path(path).read_bytes()
    if first != second:
        raise AuthorizationTamperError(f"manifest content changed during read: {path}")
    return first


def load_and_verify_manifest(path: str | Path, expected_sha256: str,
                             *, extra_allowed_grants: Sequence[str] = ()) -> dict:
    """Read raw bytes, verify SHA-256 against the externally-supplied expected
    hash, then parse JSON.  Any tamper (single byte, pre-SHA, self-declared hash)
    fails closed.

    ``expected_sha256`` must be a 64-char hex string supplied out-of-band (CLI).
    It is never read from the manifest body or any sibling file.

    ``extra_allowed_grants`` lets a specific entry point accept additional grant
    names beyond ``ALLOWED_GRANTS`` — used solely by the WP7-E3 release entry
    point to accept ``wp7_e3_watermark_release``.  The default (empty) keeps
    WP6/WP7-E2 loaders strict (only wp6_formal_cutover / wp7_held_canary).
    """
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise AuthorizationError("expected authorization SHA-256 must be 64 hex chars supplied out-of-band")
    try:
        int(expected_sha256, 16)
    except ValueError as exc:
        raise AuthorizationError("expected authorization SHA-256 must be hex") from exc
    # Manifest must live inside the authorization root, never in repo/data/output.
    p = resolve_canonical(path)
    raw = _read_raw_bytes(p)
    computed = hash_manifest_bytes(raw)
    if computed.lower() != expected_sha256.lower():
        raise AuthorizationTamperError(
            f"manifest SHA-256 mismatch: expected={expected_sha256} computed={computed}")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise AuthorizationTamperError(f"manifest is not valid JSON: {exc}") from exc
    _validate_manifest_fields(manifest, extra_allowed_grants=extra_allowed_grants)
    # A self-declared hash inside the manifest must never satisfy the check.
    if manifest.get("self_declared_sha256") is not None:
        raise AuthorizationTamperError("manifest must not carry a self-declared hash")
    return manifest


def _validate_manifest_fields(manifest: Mapping[str, Any],
                              *, extra_allowed_grants: Sequence[str] = ()) -> None:
    required = (
        "schema", "version", "git_commit_sha", "checkout_canonical_root",
        "formal_main_canonical_path", "formal_aux_canonical_path",
        "formal_main_sha256", "formal_aux_sha256",
        "formal_main_size", "formal_aux_size",
        "formal_main_mtime_ns", "formal_aux_mtime_ns",
        "config_sha", "cutover_id", "price_source", "source_generation",
        "aux_db_path", "operation_grants", "maintenance_window_id",
        "issuer", "approved_by", "watermark_release_authorized",
    )
    missing = [k for k in required if k not in manifest]
    if missing:
        raise AuthorizationTamperError(f"manifest missing required fields: {missing}")
    if manifest["watermark_release_authorized"] is not False:
        raise AuthorizationTamperError(
            "manifest must pin watermark_release_authorized=false; release is a separate future authorization")
    grants = manifest["operation_grants"]
    if not isinstance(grants, dict) or not grants:
        raise AuthorizationScopeError("operation_grants must be a non-empty mapping")
    accepted = set(ALLOWED_GRANTS) | set(extra_allowed_grants)
    for grant_name, grant in grants.items():
        if grant_name not in accepted:
            raise AuthorizationScopeError(f"unknown grant: {grant_name!r}")
        if not isinstance(grant, Mapping) or "nonce" not in grant:
            raise AuthorizationScopeError(f"grant {grant_name!r} missing nonce")
        nonce = grant["nonce"]
        if not isinstance(nonce, str) or len(nonce) < 16:
            raise AuthorizationScopeError(f"grant {grant_name!r} nonce too short (<16 chars)")
    # Test-only manifests are permitted locally but must be unmistakable.
    if str(manifest.get("schema")).upper() == "TEST_ONLY" and str(manifest.get("issuer")).upper() != "TEST_ONLY":
        raise AuthorizationScopeError("TEST_ONLY schema manifest must carry TEST_ONLY issuer")


def manifest_carry_grant(manifest: Mapping[str, Any], grant: str) -> bool:
    return grant in manifest.get("operation_grants", {})


def manifest_grant_nonce(manifest: Mapping[str, Any], grant: str) -> str:
    if not manifest_carry_grant(manifest, grant):
        raise AuthorizationScopeError(f"manifest does not grant {grant!r}")
    return str(manifest["operation_grants"][grant]["nonce"])


# ---------------------------------------------------------------------------
# Cross-run-dir nonce ledger with deletion/tamper detection.
# ---------------------------------------------------------------------------

def _ledger_dir(authorization_root: str | Path, grant: str,
                *, extra_allowed_grants: Sequence[str] = ()) -> Path:
    accepted = set(ALLOWED_GRANTS) | set(extra_allowed_grants)
    if grant not in accepted:
        raise AuthorizationScopeError(f"unknown grant: {grant!r}")
    return resolve_canonical(authorization_root) / "consumed" / grant


def _now_ts() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def reserve_nonce(authorization_root: str | Path, grant: str, nonce: str, *,
                  manifest_raw_sha: str, cutover_id: str, commit_sha: str,
                  pid: int, create_time: float,
                  extra_allowed_grants: Sequence[str] = ()) -> str:
    """Atomically burn ``nonce`` for ``grant`` under the cross-run-dir ledger.

    Writes ``<root>/consumed/<grant>/<nonce>.json`` via ``O_CREAT|O_EXCL`` and
    appends to an immutable chained ``index.json`` digest so that deletion of
    the marker file is still detectable as a replay on the next reserve.

    Returns the SHA-256 of the ledger marker (to be bound into the run-dir
    consumption record).  Raises ``NonceReplayError`` if the nonce is already
    consumed (marker exists, OR marker missing but index records it, OR index
    chain is broken).
    """
    root = _assert_auth_root_outside_forbidden(authorization_root)
    _assert_path_not_link_to_forbidden(root, root)
    ldir = _ledger_dir(root, grant, extra_allowed_grants=extra_allowed_grants)
    _assert_path_not_link_to_forbidden(ldir, root)
    ldir.mkdir(parents=True, exist_ok=True)
    marker = ldir / f"{nonce}.json"
    index_path = ldir / "index.json"
    digest_path = ldir / "index_digest.json"

    # 1. Detect replay via the immutable index first (covers marker deletion).
    index = _load_index(index_path)
    if digest_path.exists():
        stored_digest = _safe_read_json(digest_path)
        if not stored_digest or stored_digest.get("digest") != _index_digest(index):
            raise NonceReplayError(
                f"nonce ledger index chain broken for grant {grant!r}; refuse to proceed")
    if nonce in index:
        raise NonceReplayError(
            f"nonce {nonce!r} already recorded in ledger index for grant {grant!r} "
            "(marker may have been deleted; replay blocked)")

    # 2. Atomic marker creation burns the nonce.
    payload = {
        "grant": grant, "nonce": nonce, "manifest_raw_sha256": manifest_raw_sha,
        "cutover_id": cutover_id, "commit_sha": commit_sha,
        "pid": pid, "create_time": create_time, "reserved_at": _now_ts(),
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True,
                         default=str).encode("utf-8")
    flags = _bin_write_flags()
    try:
        fd = os.open(str(marker), flags, 0o644)
    except FileExistsError:
        raise NonceReplayError(
            f"nonce marker already exists for grant {grant!r} nonce {nonce!r}; replay blocked")
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)

    # 3. Append to the immutable index and refresh the chained digest.
    marker_sha = hashlib.sha256(encoded).hexdigest()
    index[nonce] = marker_sha
    _write_index(index_path, index)
    digest = _index_digest(index)
    _write_exclusive(digest_path, {"digest": digest, "entry_count": len(index),
                                   "updated_at": _now_ts()})
    return marker_sha


def _load_index(index_path: Path) -> dict:
    if not index_path.exists():
        return {}
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise NonceReplayError("nonce ledger index is not a JSON object")
        return data
    except json.JSONDecodeError as exc:
        raise NonceReplayError(f"nonce ledger index corrupt: {exc}") from exc


def _index_digest(index: Mapping[str, str]) -> str:
    # Sort by nonce for determinism; chain each entry's marker sha into the digest.
    blob = json.dumps({k: index[k] for k in sorted(index)},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _write_index(index_path: Path, index: Mapping[str, str]) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(dict(index), ensure_ascii=False, indent=2, sort_keys=True,
                         default=str).encode("utf-8") + b"\n"
    tmp = index_path.with_suffix(index_path.suffix + f".tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    with tmp.open("wb") as fh:
        fh.write(encoded)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, index_path)


def _write_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    """O_EXCL write of a small JSON file; refuse overwrite."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True,
                         default=str).encode("utf-8") + b"\n"
    flags = _bin_write_flags()
    try:
        fd = os.open(str(path), flags, 0o644)
    except FileExistsError:
        # Idempotent re-write of an identical digest file is acceptable.
        existing = path.read_bytes()
        if existing != encoded:
            raise AuthorizationTamperError(f"refuse overwrite existing file with different content: {path}")
        return
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)


def _safe_read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Test-only manifest generator (NEVER used for real formal cutover).
# ---------------------------------------------------------------------------

def generate_test_manifest(*, authorization_root: str | Path, cutover_id: str,
                           formal_main_path: str | Path, formal_aux_path: str | Path,
                           git_commit_sha: str, config_sha: str, checkout_root: str | Path,
                           grants: Mapping[str, bool], aux_db_path: str | Path,
                           price_source: str = "mcp", source_generation: str = "mcp-gen1",
                           maintenance_window_id: str = "test-window") -> tuple[Path, str]:
    """Build a TEST_ONLY manifest for staging/hermetic rehearsal only.

    Returns ``(manifest_path, expected_sha256)``.  The manifest is written with
    ``TEST_ONLY`` schema/issuer markers, ``watermark_release_authorized=false``
    pinned, and a ``_test_only_`` filename suffix.  Real authorization manifests
    are generated offline by the user at a future formal window; this helper
    must never produce one that could be mistaken for real.
    """
    root = _assert_auth_root_outside_forbidden(authorization_root)
    cdir = root / cutover_id
    cdir.mkdir(parents=True, exist_ok=True)
    main_canon = resolve_canonical(formal_main_path)
    aux_canon = resolve_canonical(formal_aux_path)
    main_st = main_canon.stat()
    aux_st = aux_canon.stat()
    operation_grants: dict[str, dict[str, str]] = {}
    for grant, enabled in grants.items():
        if grant not in ALLOWED_GRANTS:
            raise AuthorizationScopeError(f"unknown grant: {grant!r}")
        if enabled:
            operation_grants[grant] = {"nonce": secrets.token_hex(16)}
    if not operation_grants:
        raise AuthorizationScopeError("at least one grant must be enabled")
    payload = {
        "schema": "TEST_ONLY", "version": 1,
        "git_commit_sha": git_commit_sha, "checkout_canonical_root": str(resolve_canonical(checkout_root)),
        "formal_main_canonical_path": str(main_canon),
        "formal_aux_canonical_path": str(aux_canon),
        "formal_main_sha256": _file_sha256(main_canon),
        "formal_aux_sha256": _file_sha256(aux_canon),
        "formal_main_size": main_st.st_size, "formal_aux_size": aux_st.st_size,
        "formal_main_mtime_ns": main_st.st_mtime_ns, "formal_aux_mtime_ns": aux_st.st_mtime_ns,
        "config_sha": config_sha, "cutover_id": cutover_id,
        "price_source": price_source, "source_generation": source_generation,
        "aux_db_path": str(resolve_canonical(aux_db_path)),
        "operation_grants": operation_grants,
        "maintenance_window_id": maintenance_window_id,
        "issuer": "TEST_ONLY", "approved_by": "TEST_ONLY",
        "watermark_release_authorized": False,
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True,
                     default=str).encode("utf-8") + b"\n"
    sha = hashlib.sha256(raw).hexdigest()
    path = cdir / f"authorization_test_only_{sha[:12]}.json"
    # O_EXCL so rehearsal cannot silently overwrite a prior manifest.
    flags = _bin_write_flags()
    fd = os.open(str(path), flags, 0o644)
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    return path, sha


def _file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def file_evidence(path: str | Path) -> dict:
    """Canonical file evidence: {path, size, mtime_ns, sha256}.

    Mirrors the staging-prep ``_file_evidence`` shape so WP6 evidence is
    directly comparable to staging evidence.
    """
    p = resolve_canonical(path)
    return {"path": str(p), "size": p.stat().st_size,
            "mtime_ns": p.stat().st_mtime_ns, "sha256": _file_sha256(p)}


def verify_formal_file_evidence(manifest: Mapping[str, Any], *, main_path: str | Path,
                                aux_path: str | Path) -> None:
    """Re-compute formal main/aux file evidence and compare to the manifest's
    frozen values.  Any drift (size/mtime/sha) fails closed.

    The manifest's frozen values are authoritative; a mismatch never rewrites
    the authorization.
    """
    for role, live in (("main", main_path), ("aux", aux_path)):
        canon = resolve_canonical(live)
        st = canon.stat()
        m_size = manifest[f"formal_{role}_size"]
        m_mtime = manifest[f"formal_{role}_mtime_ns"]
        m_sha = manifest[f"formal_{role}_sha256"]
        if st.st_size != m_size or st.st_mtime_ns != m_mtime:
            raise AuthorizationTamperError(
                f"formal {role} file evidence drift: size/mtime differs from manifest")
        if _file_sha256(canon) != m_sha:
            raise AuthorizationTamperError(
                f"formal {role} file SHA-256 differs from manifest; refuse to proceed")
