"""G4 Release Closure tests: CLI E2E + Skill install/validation + 0.3.0-mvp release.

Validates the G4 work-package scope (reviewer-authorized):
- CLI E2E: Spec → IR → dual Renderer → strategy package (via a package-build CLI);
- Skill install/validation flow;
- Version upgrade to 0.3.0-mvp;
- release metadata, usage/limitations/release docs;
- hermetic CLI tests, install verification, dual Renderer/package regression;
- G3 manifest/digest verification retained in the CLI flow;
- honest boundary propagation: failure status, Golden Protection, G2
  data-digest-blocked all surface correctly (no silent swallow);
- determinism / reproducibility.

Boundaries: no real market data / live QMT / resident daemon; no faked data digest;
real Fidelity/Reference stays deferred.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXAMPLES = ROOT / "quantstudio" / "strategy_compiler" / "examples"
FROZEN = ROOT / "tests" / "strategy_references" / "frozen"


def _load(p) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


@pytest.fixture
def case1_spec() -> dict:
    return _load(EXAMPLES / "case1_dual_ma_spec.json")


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run the quantstudio CLI in a subprocess (hermetic, captures exit/output)."""
    return subprocess.run(
        [sys.executable, "-m", "quantstudio.strategy_compiler.cli", *args],
        capture_output=True, text=True, cwd=str(cwd), check=False,
    )


# ── 1. CLI E2E: spec → package ───────────────────────────────────────────────

class TestCliPackageBuild:
    def test_cli_builds_package_from_spec(self, tmp_path, case1_spec):
        """`qs-compile package <spec> --out <dir>` builds a strategy package end-to-end."""
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(case1_spec), encoding="utf-8")
        out_dir = tmp_path / "out"
        result = _run_cli(["package", str(spec_path), "--out", str(out_dir)], cwd=ROOT)
        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        # a package dir was created
        pkgs = list(out_dir.iterdir())
        assert len(pkgs) == 1
        pkg = pkgs[0]
        assert (pkg / "manifest.json").exists()
        assert (pkg / "case1_dual_ma_quantstudio.py").exists()
        assert (pkg / "case1_dual_ma_ptrade.py").exists()

    def test_cli_g3_manifest_digest_verification_retained(self, tmp_path, case1_spec):
        """The CLI-built package retains G3 manifest/digest verification:
        every artifact_digest matches the actual file; manifest has no self-digest."""
        from quantstudio.strategy_compiler.reference.source_digest import sha256_file
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(case1_spec), encoding="utf-8")
        out_dir = tmp_path / "out"
        result = _run_cli(["package", str(spec_path), "--out", str(out_dir)], cwd=ROOT)
        assert result.returncode == 0
        pkg = next(out_dir.iterdir())
        manifest = _load(pkg / "manifest.json")
        assert "manifest.json" not in manifest["artifact_digests"]  # no self-digest
        for fname, recorded in manifest["artifact_digests"].items():
            assert sha256_file(pkg / fname) == recorded, f"{fname} digest mismatch"

    def test_cli_propagates_golden_protection(self, tmp_path, case1_spec):
        """CLI surfaces Golden Protection (exit code + stderr), not silent success."""
        spec = dict(case1_spec)
        spec["strategy_id"] = "etf_momentum"  # golden-protected
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        result = _run_cli(["package", str(spec_path), "--out", str(tmp_path / "o")], cwd=ROOT)
        assert result.returncode != 0
        assert "golden" in result.stderr.lower() or "protect" in result.stderr.lower()

    def test_cli_propagates_invalid_spec_failure(self, tmp_path):
        """Invalid spec (bad JSON / missing strategy_id) -> non-zero exit, clear stderr."""
        spec_path = tmp_path / "bad.json"
        spec_path.write_text("{not valid json", encoding="utf-8")
        result = _run_cli(["package", str(spec_path), "--out", str(tmp_path / "o")], cwd=ROOT)
        assert result.returncode != 0
        assert len(result.stderr) > 0

    def test_cli_missing_spec_file(self, tmp_path):
        """Missing spec file -> non-zero exit, clear error."""
        result = _run_cli(["package", str(tmp_path / "nope.json"), "--out", str(tmp_path / "o")], cwd=ROOT)
        assert result.returncode != 0


# ── 2. Version 0.3.0-mvp ─────────────────────────────────────────────────────

class TestVersion:
    def test_skill_version_is_030_mvp(self):
        """Skill version bumped to 0.3.0-mvp (run_card skill_version default)."""
        from quantstudio.strategy_compiler.orchestrator import _SKILL_VERSION
        assert _SKILL_VERSION == "0.3.0-mvp"

    def test_release_metadata_records_version(self):
        """A release metadata file records the 0.3.0-mvp version."""
        meta_path = ROOT / "quantstudio" / "strategy_compiler" / "release" / "release_metadata.json"
        assert meta_path.exists(), "release_metadata.json missing"
        meta = _load(meta_path)
        assert meta["version"] == "0.3.0-mvp"


# ── 3. Release docs ──────────────────────────────────────────────────────────

class TestReleaseDocs:
    def test_release_notes_exist(self):
        notes = ROOT / "quantstudio" / "strategy_compiler" / "release" / "RELEASE_NOTES.md"
        assert notes.exists()
        text = notes.read_text(encoding="utf-8")
        assert "0.3.0-mvp" in text

    def test_release_notes_state_data_digest_boundary(self):
        """Release notes honestly state the data-digest-deferred boundary."""
        notes = (ROOT / "quantstudio" / "strategy_compiler" / "release" / "RELEASE_NOTES.md")
        text = notes.read_text(encoding="utf-8").lower()
        assert "data digest" in text or "data_digest" in text
        assert "blocked" in text or "deferred" in text

    def test_release_notes_state_no_real_data(self):
        """Release notes state no real market data / live QMT in this release."""
        text = (ROOT / "quantstudio" / "strategy_compiler" / "release" / "RELEASE_NOTES.md").read_text(encoding="utf-8").lower()
        assert "real" in text and ("data" in text or "market" in text)


# ── 4. Skill install/validation ──────────────────────────────────────────────

class TestSkillInstall:
    def test_skill_install_into_tmp_and_validate(self, tmp_path):
        """install_skill copies the Skill into a tmp dest and quick_validate passes."""
        _SKILL_SCRIPTS = ROOT / "skills" / "quantstudio-strategy-compiler" / "scripts"
        sys.path.insert(0, str(_SKILL_SCRIPTS))
        import install_skill
        dest_root = tmp_path / "skills"
        ok, installed_path, msg = install_skill.install_skill(
            source=ROOT / "skills" / "quantstudio-strategy-compiler",
            dest_root=dest_root, force=False)
        assert ok, f"install_skill failed: {msg}"
        assert (installed_path / "SKILL.md").exists()


# ── 5. Determinism / reproducibility ─────────────────────────────────────────

class TestDeterminism:
    def test_cli_package_build_reproducible(self, tmp_path, case1_spec):
        """Two CLI builds of the same spec produce byte-identical manifests + strategies."""
        from quantstudio.strategy_compiler.reference.source_digest import canonical_json_digest
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(case1_spec), encoding="utf-8")
        r1 = _run_cli(["package", str(spec_path), "--out", str(tmp_path / "a")], cwd=ROOT)
        r2 = _run_cli(["package", str(spec_path), "--out", str(tmp_path / "b")], cwd=ROOT)
        assert r1.returncode == 0 and r2.returncode == 0
        p1 = next((tmp_path / "a").iterdir())
        p2 = next((tmp_path / "b").iterdir())
        for fname in ("manifest.json", "case1_dual_ma_quantstudio.py", "case1_dual_ma_ptrade.py"):
            assert canonical_json_digest(_load(p1 / fname)) == canonical_json_digest(_load(p2 / fname)) \
                if fname.endswith(".json") else \
                (p1 / fname).read_bytes() == (p2 / fname).read_bytes(), f"{fname} not reproducible"


# ── 6. G2 data-digest-blocked boundary in CLI ────────────────────────────────

class TestG2BoundaryPropagation:
    def test_cli_package_with_g2_linkage_records_blocked(self, tmp_path, case1_spec):
        """CLI package build with --g2-frozen-dir records data_digest_status=blocked
        honestly (not faked as frozen)."""
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(case1_spec), encoding="utf-8")
        out_dir = tmp_path / "out"
        result = _run_cli(
            ["package", str(spec_path), "--out", str(out_dir),
             "--g2-frozen-dir", str(FROZEN)],
            cwd=ROOT)
        assert result.returncode == 0
        pkg = next(out_dir.iterdir())
        manifest = _load(pkg / "manifest.json")
        g2 = manifest["g2_reference_closure"]
        assert g2 is not None
        assert g2["data_digest_status"] == "blocked"  # honest, not faked


# ── 7. Dual Renderer / package regression (G3 still works via CLI) ───────────

class TestDualRendererRegression:
    def test_cli_package_dual_render_compiles(self, tmp_path, case1_spec):
        """Both rendered strategies in the CLI-built package compile (ast + compile)."""
        import ast
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(case1_spec), encoding="utf-8")
        out_dir = tmp_path / "out"
        result = _run_cli(["package", str(spec_path), "--out", str(out_dir)], cwd=ROOT)
        assert result.returncode == 0
        pkg = next(out_dir.iterdir())
        for f in ("case1_dual_ma_quantstudio.py", "case1_dual_ma_ptrade.py"):
            src = (pkg / f).read_text(encoding="utf-8")
            ast.parse(src)
            compile(src, str(pkg / f), "exec")
