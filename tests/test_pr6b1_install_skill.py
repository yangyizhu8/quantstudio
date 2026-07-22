"""PR6b-1 install_skill tests.

Covers (handoff §2 CP9):
  - install into tmp_path succeeds + quick_validate PASS on installed copy
  - --force overwrites existing destination
  - rollback on quick_validate failure (bad Skill removed, dest clean)
  - __pycache__ excluded from the installed copy

These tests use the real project Skill as source and a tmp dest root, so they
exercise the actual copytree + quick_validate chain without touching the live
~/.agents/skills/ directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# install_skill.py lives in the Skill scripts dir (not in the quantstudio pkg),
# so import it by path.
_SKILL_SCRIPTS = (Path(__file__).resolve().parents[1]
                  / "skills" / "quantstudio-strategy-compiler" / "scripts")
sys.path.insert(0, str(_SKILL_SCRIPTS))
import install_skill  # noqa: E402


SOURCE = _SKILL_SCRIPTS.parent  # skills/quantstudio-strategy-compiler/


@pytest.fixture
def dest_root(tmp_path):
    return tmp_path / "skills"


class TestInstallSkill:
    def test_install_success_and_validates(self, dest_root):
        ok, dest, msg = install_skill.install_skill(
            source=SOURCE, dest_root=dest_root, name="quantstudio-strategy-compiler",
        )
        assert ok, msg
        assert dest.exists()
        assert (dest / "SKILL.md").exists()
        assert "quick_validate PASS" in msg

    def test_installed_copy_has_all_components(self, dest_root):
        """All 6 components copied (SKILL.md + 5 dirs)."""
        ok, dest, _ = install_skill.install_skill(
            source=SOURCE, dest_root=dest_root, name="qs-compiler-test",
        )
        assert ok
        for component in ("SKILL.md", "agents", "references", "schemas", "scripts", "templates"):
            assert (dest / component).exists(), f"missing component: {component}"

    def test_pycache_excluded(self, dest_root):
        """__pycache__ must NOT be copied into the install."""
        ok, dest, _ = install_skill.install_skill(
            source=SOURCE, dest_root=dest_root, name="qs-no-pycache",
        )
        assert ok
        # no __pycache__ anywhere in the install
        caches = list(dest.rglob("__pycache__"))
        assert caches == [], f"__pycache__ leaked into install: {caches}"

    def test_force_overwrites_existing(self, dest_root):
        """--force removes existing dest then installs."""
        install_skill.install_skill(
            source=SOURCE, dest_root=dest_root, name="qs-force",
        )
        # second install without force → fails (exists)
        ok, _, msg = install_skill.install_skill(
            source=SOURCE, dest_root=dest_root, name="qs-force",
        )
        assert not ok
        assert "already exists" in msg
        # with force → succeeds
        ok, dest, msg = install_skill.install_skill(
            source=SOURCE, dest_root=dest_root, name="qs-force", force=True,
        )
        assert ok, msg

    def test_rollback_on_validate_failure(self, dest_root, tmp_path):
        """A structurally-invalid Skill (missing description) rolls back."""
        bad_source = tmp_path / "bad-skill"
        bad_source.mkdir()
        # SKILL.md with valid frontmatter but missing required `description`
        (bad_source / "SKILL.md").write_text("---\nname: bad-skill\n---\nbody\n", encoding="utf-8")
        ok, dest, msg = install_skill.install_skill(
            source=bad_source, dest_root=dest_root, name="bad-skill",
        )
        assert not ok
        assert "rolled back" in msg
        # rollback: dest must NOT exist
        assert not dest.exists(), "rollback failed: bad Skill left installed"

    def test_invalid_source_rejected(self, dest_root):
        """Source without SKILL.md is rejected before any copy."""
        ok, dest, msg = install_skill.install_skill(
            source=dest_root / "nope", dest_root=dest_root, name="x",
        )
        assert not ok
        assert "not a directory" in msg or "SKILL.md missing" in msg

    def test_skip_validate_installs_without_check(self, dest_root):
        """--skip-validate installs even a bad Skill (diagnostic mode)."""
        ok, dest, msg = install_skill.install_skill(
            source=SOURCE, dest_root=dest_root, name="qs-skip", skip_validate=True,
        )
        assert ok
        assert dest.exists()
        assert "validation skipped" in msg

    def test_quick_validate_missing_fails_and_rolls_back(self, dest_root, monkeypatch):
        """If quick_validate.py cannot be found, install FAILS + rolls back.

        Validation is the install's acceptance gate — an unverified install must
        not be reported as success. (Reviewer fix: was returning True.)
        """
        monkeypatch.setattr(install_skill, "_find_quick_validate", lambda: None)
        ok, dest, msg = install_skill.install_skill(
            source=SOURCE, dest_root=dest_root, name="qs-no-qv",
        )
        assert not ok
        assert "rolled back" in msg
        assert not dest.exists(), "unverified install must be rolled back"
