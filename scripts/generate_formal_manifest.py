"""Generate a REAL B-6 formal-cutover authorization manifest (method B helper).

This script ONLY assembles the manifest JSON from the baseline values YOU supply
and computes the manifest's raw-byte SHA-256.  It does NOT:
  - read or write the formal database (no duckdb/sqlite connection);
  - make any authorization decision (the authorization comes from YOUR values
    and YOUR nonce);
  - connect to the network or any external service;
  - generate the nonce (YOU choose the nonce strings).

Usage (two-step):

  Step 1 — collect baseline values from ZCode's read-only review, then write a
  small ``baseline.json`` next to this script:

      {
        "main_sha256":   "<64 hex from ZCode review>",
        "aux_sha256":    "<64 hex from ZCode review>",
        "main_size":     <integer>,
        "aux_size":      <integer>,
        "main_mtime_ns": <integer>,
        "aux_mtime_ns":  <integer>,
        "config_sha":    "<64 hex from ZCode review>"
      }

  Step 2 — run this script:

      python scripts/generate_formal_manifest.py \\
        --baseline baseline.json \\
        --auth-root "D:/miniQMT策略实盘/私募工作文件/QuantStudio-MCP全数据源替代任务文件/formal_authorizations/b6_formal_20260807" \\
        --cutover-id b6_formal_20260807 \\
        --wp6-nonce "<your-random-32-char-string>"

  For a WP7-E2 + WP7-E3 manifest (after WP6 cutover is done):

      python scripts/generate_formal_manifest.py \\
        --baseline baseline.json \\
        --auth-root ".../formal_authorizations/b6_wp7_20260807" \\
        --cutover-id b6_wp7_20260807 \\
        --wp7-canary-nonce "<random-32-char>" \\
        --wp7-release-nonce "<another-random-32-char>"

  The script prints:
    - the manifest file path (channel A — give this to ZCode in one message);
    - the manifest raw-byte SHA-256 (channel B — give this to ZCode in a
      SEPARATE message).

Safety properties:
  - ``watermark_release_authorized`` is hardcoded to ``false`` (this script
    cannot authorize watermark release; that is a separate future authorization).
  - The manifest is written via O_EXCL; it refuses to overwrite an existing file
    (a real manifest is single-use and must not be regenerated over itself).
  - The git commit SHA and checkout root are auto-captured from the current repo
    state (NOT supplied by you), so the manifest always binds the exact code that
    will run.
  - The manifest is stored under ``--auth-root`` which MUST be outside
    repo/data/output; the script validates this.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BJ_TZ = timezone(timedelta(hours=8))
_REPO = Path(__file__).resolve().parents[1]


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _git(args: list[str]) -> str:
    return subprocess.check_output(["git"] + args, cwd=str(_REPO), text=True).strip()


def _assert_auth_root_outside_repo(auth_root: Path) -> None:
    """Authorization root must not be inside the repo/data/output tree."""
    try:
        auth_root.relative_to(_REPO)
        # It's inside the repo — reject.
        raise SystemExit(
            f"ERROR: --auth-root must be OUTSIDE the repo (it is inside {_REPO}): {auth_root}")
    except ValueError:
        pass  # not inside repo — good.
    parts_lower = [p.lower() for p in auth_root.parts]
    for forbidden in ("output", "data"):
        if forbidden in parts_lower[-3:]:
            # Allow if it is clearly a distinct sibling dir name, but reject
            # being literally inside data/ or output/.
            if any(p.lower() == forbidden for p in auth_root.parents):
                raise SystemExit(
                    f"ERROR: --auth-root must not be inside a '{forbidden}' directory: {auth_root}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Generate a REAL B-6 formal-cutover authorization manifest (method B).")
    ap.add_argument("--baseline", required=True,
                    help="path to baseline.json with main/aux SHA/size/mtime + config_sha")
    ap.add_argument("--auth-root", required=True,
                    help="authorization root directory (MUST be outside repo/data/output)")
    ap.add_argument("--cutover-id", required=True, help="e.g. b6_formal_20260807")
    ap.add_argument("--wp6-nonce", default=None,
                    help="random 32+ char string for wp6_formal_cutover grant (YOU generate). "
                         "Omit for WP7-only manifests.")
    ap.add_argument("--wp7-canary-nonce", default=None,
                    help="random 32+ char string for wp7_held_canary grant (YOU generate). "
                         "Optional; adds the canary grant to this manifest.")
    ap.add_argument("--wp7-release-nonce", default=None,
                    help="random 32+ char string for wp7_e3_watermark_release grant (YOU generate). "
                         "Optional; adds the watermark-release grant. WARNING: a manifest carrying "
                         "this grant is rejected by WP6/WP7-E2 loaders (G0 §3.1 item 20); only the "
                         "WP7-E3 release entry point accepts it.")
    ap.add_argument("--price-source", default="mcp")
    ap.add_argument("--source-generation", default="mcp-gen1")
    ap.add_argument("--maintenance-window-id", default=None,
                    help="optional; defaults to mw_<cutover-id>")
    ap.add_argument("--issuer", default=None, help="optional; defaults to git user")
    args = ap.parse_args(argv)

    # Validate auth root location.
    auth_root = Path(args.auth_root).resolve()
    _assert_auth_root_outside_repo(auth_root)

    # Validate nonce lengths (each provided nonce must be >= 16 chars).
    # At least one grant nonce must be provided (a manifest with zero grants is useless).
    nonce_specs = [
        ("wp6_formal_cutover", args.wp6_nonce),
        ("wp7_held_canary", args.wp7_canary_nonce),
        ("wp7_e3_watermark_release", args.wp7_release_nonce),
    ]
    grants = {}
    for grant_name, nonce in nonce_specs:
        if nonce is not None:
            if not isinstance(nonce, str) or len(nonce) < 16:
                raise SystemExit(
                    f"ERROR: {grant_name} nonce must be at least 16 chars (you gave {len(nonce) if nonce else 0})")
            grants[grant_name] = {"nonce": nonce}
    if not grants:
        raise SystemExit("ERROR: at least one of --wp6-nonce / --wp7-canary-nonce / --wp7-release-nonce is required")

    # Load baseline values (provided by ZCode's read-only review).
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    required = ("main_sha256", "aux_sha256", "main_size", "aux_size",
                "main_mtime_ns", "aux_mtime_ns", "config_sha")
    missing = [k for k in required if k not in baseline]
    if missing:
        raise SystemExit(f"ERROR: baseline.json missing keys: {missing}")
    for k in ("main_sha256", "aux_sha256", "config_sha"):
        if len(str(baseline[k])) != 64:
            raise SystemExit(f"ERROR: {k} must be 64 hex chars (got {len(str(baseline[k]))})")

    # Auto-capture code identity (NOT user-supplied — binds the exact code).
    head = _git(["rev-parse", "HEAD"])
    origin = _git(["rev-parse", "origin/main"])
    if head != origin:
        print(f"WARNING: HEAD ({head}) != origin/main ({origin}). The manifest will bind HEAD.",
              file=sys.stderr)
    checkout_root = str(_REPO)
    issuer = args.issuer or _git(["config", "user.name"]) or "unknown"

    # Canonical formal paths.
    main_canon = str((_REPO / "data" / "quantstudio.db").resolve())
    aux_canon = str((_REPO / "data" / "qfq_aux.db").resolve())
    aux_db_path = str((_REPO / "data" / "qfq_aux_mcp_gen1.db").resolve())

    manifest = {
        "schema": "B6_FORMAL_CUTOVER",
        "version": 1,
        "git_commit_sha": head,
        "checkout_canonical_root": checkout_root,
        "formal_main_canonical_path": main_canon,
        "formal_aux_canonical_path": aux_canon,
        "formal_main_sha256": baseline["main_sha256"],
        "formal_aux_sha256": baseline["aux_sha256"],
        "formal_main_size": int(baseline["main_size"]),
        "formal_aux_size": int(baseline["aux_size"]),
        "formal_main_mtime_ns": int(baseline["main_mtime_ns"]),
        "formal_aux_mtime_ns": int(baseline["aux_mtime_ns"]),
        "config_sha": baseline["config_sha"],
        "cutover_id": args.cutover_id,
        "price_source": args.price_source,
        "source_generation": args.source_generation,
        "aux_db_path": aux_db_path,
        "operation_grants": grants,
        "maintenance_window_id": args.maintenance_window_id or f"mw_{args.cutover_id}",
        "issuer": issuer,
        "approved_by": issuer,
        "watermark_release_authorized": False,  # hardcoded — cannot be overridden here
    }

    # Serialize deterministically (sorted keys, UTF-8, LF newlines, binary write).
    raw = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True,
                     default=str).encode("utf-8") + b"\n"
    expected_sha = _sha256_bytes(raw)

    # Write via O_EXCL (refuse overwrite — a real manifest is single-use).
    auth_root.mkdir(parents=True, exist_ok=True)
    manifest_path = auth_root / f"authorization_{args.cutover_id}.json"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        fd = os.open(str(manifest_path), flags, 0o644)
    except FileExistsError:
        raise SystemExit(
            f"ERROR: manifest already exists (refuse overwrite): {manifest_path}\n"
            "A real manifest is single-use. Delete it manually ONLY if you intend "
            "to discard the previous authorization and start over.")
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)

    print("=" * 72)
    print("MANIFEST GENERATED. Review it before sending anything to ZCode:")
    print("=" * 72)
    print(f"  manifest path : {manifest_path}")
    print(f"  git commit SHA: {head}")
    print(f"  watermark_release_authorized: {manifest['watermark_release_authorized']}")
    print(f"  grants: {list(manifest['operation_grants'].keys())}")
    if "wp7_e3_watermark_release" in grants:
        print("  *** WARNING: this manifest carries wp7_e3_watermark_release. ***")
        print("  *** It will be REJECTED by WP6/WP7-E2 loaders (G0 §3.1 item 20). ***")
        print("  *** Only the WP7-E3 release entry point accepts this grant.      ***")
    print()
    print("SEND TO ZCode VIA TWO SEPARATE MESSAGES (independent channels):")
    print("-" * 72)
    print(f"  MESSAGE 1 (channel A — the path):")
    print(f"    {manifest_path}")
    print()
    print(f"  MESSAGE 2 (channel B — the expected SHA-256):")
    print(f"    {expected_sha}")
    print("=" * 72)
    print("Before sending, open the manifest file and verify the baseline values")
    print("match ZCode's read-only review. Do NOT send both values in one message.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
