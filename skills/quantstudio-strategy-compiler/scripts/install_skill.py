#!/usr/bin/env python
"""Install the quantstudio-strategy-compiler Skill into the agent skill root.

PR6b-1, CP8. Copies the project Skill (skills/quantstudio-strategy-compiler)
into the directory the running agent reads skills from
(~/.agents/skills/<name> by default), then chains quick_validate to confirm
the installed copy is structurally valid. On validation failure the install is
rolled back (the copied directory removed) so a half-installed Skill never
shadows the project source.

Flow: _validate_skill(source) → copytree(dest) → subprocess quick_validate(dest)
      → on non-zero exit: rmtree(dest), report failure.

The Skill has no code to compile; "validation" = quick_validate's 7 SKILL.md
frontmatter rules (name/description/triggers/etc.). Use --skip-validate only
for diagnostics.

Usage:
    python install_skill.py [--source <dir>] [--dest-root <dir>] [--name <id>]
                            [--force] [--skip-validate]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Default install root: the directory the running agent reads skills from.
# ~/.agents/skills/ is where qf-rock-pipeline / x2strategy / etc. already live
# (verified at CP8 dev time).
_DEFAULT_DEST_ROOT = Path.home() / ".agents" / "skills"

# Default source: the project-tracked Skill.
# Script lives at <repo>/skills/quantstudio-strategy-compiler/scripts/install_skill.py
# so parents[3] is the repo root.
_DEFAULT_SOURCE = (
    Path(__file__).resolve().parents[3]
    / "skills" / "quantstudio-strategy-compiler"
)

# quick_validate.py locations, searched in order. The canonical one ships with
# the skill-creator system skill; a couple of historical copies exist.
_QUICK_VALIDATE_CANDIDATES = [
    Path.home() / ".codex" / "skills" / ".system" / "skill-creator" / "scripts" / "quick_validate.py",
    Path.home() / ".agents" / "skills_archive" / "_retired" / "skill-creator" / "scripts" / "quick_validate.py",
]


def _find_quick_validate() -> Path | None:
    for c in _QUICK_VALIDATE_CANDIDATES:
        if c.exists():
            return c
    return None


def _validate_skill(source: Path) -> list[str]:
    """Pre-copy sanity checks on the source Skill. Returns list of problems."""
    problems: list[str] = []
    if not source.is_dir():
        problems.append(f"source is not a directory: {source}")
        return problems
    skill_md = source / "SKILL.md"
    if not skill_md.is_file():
        problems.append(f"SKILL.md missing in source: {skill_md}")
    return problems


def install_skill(
    source: Path | None = None,
    dest_root: Path | None = None,
    name: str | None = None,
    *,
    force: bool = False,
    skip_validate: bool = False,
) -> tuple[bool, Path, str]:
    """Install the Skill. Returns (ok, installed_path, message).

    On failure (validation or pre-checks), no partial install is left behind.
    """
    source = Path(source) if source else _DEFAULT_SOURCE
    dest_root = Path(dest_root) if dest_root else _DEFAULT_DEST_ROOT
    # Derive the skill name from SKILL.md frontmatter `name:` if not given.
    name = name or _read_skill_name(source) or source.name
    dest = dest_root / name

    problems = _validate_skill(source)
    if problems:
        return False, dest, "; ".join(problems)

    # Handle pre-existing dest.
    if dest.exists():
        if not force:
            return False, dest, (
                f"destination already exists: {dest}. Use --force to overwrite "
                f"(removes the existing copy first)."
            )
        shutil.rmtree(dest)

    dest_root.mkdir(parents=True, exist_ok=True)

    # copytree — __pycache__ etc. would just bloat the install; exclude them.
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
    shutil.copytree(source, dest, ignore=ignore)

    if skip_validate:
        return True, dest, f"installed (validation skipped): {dest}"

    # Chain quick_validate on the INSTALLED copy (not the source).
    qv = _find_quick_validate()
    if qv is None:
        # Validation is the install's acceptance gate. If the validator itself
        # cannot be found, the install is UNVERIFIED — roll back so an unverified
        # Skill never shadows the source. (Use --skip-validate to install
        # without verification, accepting responsibility explicitly.)
        shutil.rmtree(dest, ignore_errors=True)
        return False, dest, (
            f"quick_validate.py not found in known locations "
            f"({[str(c) for c in _QUICK_VALIDATE_CANDIDATES]}); install rolled back "
            f"(unverified install not allowed — pass --skip-validate to force)."
        )

    # Attention point ② (same as smoke): force UTF-8 so console codepage cannot
    # corrupt quick_validate's output on Windows.
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, str(qv), str(dest)],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    if result.returncode != 0:
        # Rollback: remove the installed copy so a bad Skill never shadows source.
        shutil.rmtree(dest, ignore_errors=True)
        return False, dest, (
            f"quick_validate FAILED (exit {result.returncode}) on installed "
            f"copy; rolled back. stderr: {(result.stderr or '').strip()}"
        )
    return True, dest, f"installed + quick_validate PASS: {dest}"


def _read_skill_name(source: Path) -> str | None:
    """Read the `name:` field from SKILL.md frontmatter (best-effort)."""
    skill_md = source / "SKILL.md"
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    # frontmatter block
    end = text.find("---", 3)
    if end == -1:
        return None
    for line in text[3:end].splitlines():
        line = line.strip()
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install quantstudio-strategy-compiler Skill")
    parser.add_argument("--source", default=None, help=f"Source Skill dir (default: {_DEFAULT_SOURCE})")
    parser.add_argument("--dest-root", default=None, help=f"Install root (default: {_DEFAULT_DEST_ROOT})")
    parser.add_argument("--name", default=None, help="Skill name (default: from SKILL.md frontmatter)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing destination")
    parser.add_argument("--skip-validate", action="store_true", help="Skip quick_validate chain")
    args = parser.parse_args(argv)

    ok, dest, msg = install_skill(
        source=Path(args.source) if args.source else None,
        dest_root=Path(args.dest_root) if args.dest_root else None,
        name=args.name, force=args.force, skip_validate=args.skip_validate,
    )
    print(("OK: " if ok else "FAIL: ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
