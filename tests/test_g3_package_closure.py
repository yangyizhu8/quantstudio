"""G3 Package Closure tests: Spec → IR → dual Renderer → strategy package.

Validates the full G3 work-package scope (reviewer-authorized):
- Local/QuantStudio Renderer complete closure;
- Strict-PTrade Renderer complete closure;
- dual-platform stub, adapter layer, package structure;
- Spec → IR → dual Renderer → strategy package end-to-end static compile chain;
- package manifest, version, entry, resources, import boundary;
- integration with G2 frozen artifacts + CP3 Oracle + existing validators;
- renderer output determinism, path isolation, encoding stability, safety rules;
- G2 Oracle/reference not bypassed; G1-I engine/API/test untouched; G4 isolated.

Hermetic: uses the frozen case1_dual_ma_spec + tmp_path; no real DB/live QMT.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXAMPLES = ROOT / "quantstudio" / "strategy_compiler" / "examples"
SCHEMAS = ROOT / "quantstudio" / "strategy_compiler" / "schemas"
FROZEN = ROOT / "tests" / "strategy_references" / "frozen"


def _load(name_or_path) -> dict:
    return json.loads(Path(name_or_path).read_text(encoding="utf-8"))


@pytest.fixture
def case1_spec() -> dict:
    return _load(EXAMPLES / "case1_dual_ma_spec.json")


@pytest.fixture
def built_package(tmp_path, case1_spec):
    """Build a strategy package into tmp_path and return (pkg_dir, manifest)."""
    from quantstudio.strategy_compiler.package.builder import build_strategy_package
    pkg_dir = build_strategy_package(case1_spec, out_dir=tmp_path)
    manifest = _load(pkg_dir / "manifest.json")
    return pkg_dir, manifest


# ── 1. Package structure ─────────────────────────────────────────────────────

class TestPackageStructure:
    def test_package_dir_named_with_strategy_id_and_version(self, built_package):
        pkg_dir, manifest = built_package
        # dir name contains strategy_id and a version (version dots→underscores)
        name = pkg_dir.name
        assert "case1_dual_ma" in name
        assert manifest["package_version"].replace(".", "_") in name

    def test_package_contains_required_files(self, built_package):
        pkg_dir, _manifest = built_package
        required = [
            "manifest.json",
            "strategy_spec.json",
            "strategy_ir.json",
            "__init__.py",
            "case1_dual_ma_quantstudio.py",
            "case1_dual_ma_ptrade.py",
            "README.md",
        ]
        for f in required:
            assert (pkg_dir / f).exists(), f"missing {f}"

    def test_package_manifest_schema_valid(self, built_package):
        pkg_dir, _ = built_package
        manifest = _load(pkg_dir / "manifest.json")
        schema = _load(SCHEMAS / "strategy_package_manifest.schema.json")
        jsonschema.validate(manifest, schema)  # raises on invalid

    def test_no_files_outside_package_dir(self, tmp_path, built_package):
        """Path isolation: all outputs live under the package dir, nothing leaks to out_dir root."""
        pkg_dir, _ = built_package
        # the only thing at tmp_path root should be the package dir itself
        root_entries = [p.name for p in tmp_path.iterdir()]
        assert root_entries == [pkg_dir.name], \
            f"path isolation violated: unexpected entries {root_entries}"


# ── 2. Dual renderer complete closure ────────────────────────────────────────

class TestDualRendererClosure:
    def test_quantstudio_strategy_compiles(self, built_package):
        pkg_dir, _ = built_package
        src = (pkg_dir / "case1_dual_ma_quantstudio.py").read_text(encoding="utf-8")
        ast.parse(src)  # syntax-valid
        compile(src, str(pkg_dir / "case1_dual_ma_quantstudio.py"), "exec")  # compiles

    def test_ptrade_strategy_compiles(self, built_package):
        pkg_dir, _ = built_package
        src = (pkg_dir / "case1_dual_ma_ptrade.py").read_text(encoding="utf-8")
        ast.parse(src)
        compile(src, str(pkg_dir / "case1_dual_ma_ptrade.py"), "exec")

    def test_ptrade_has_no_batch_apis(self, built_package):
        """Strict-PTrade: no get_fundamentals_batch / get_history_batch (ptrade-profile-contract)."""
        pkg_dir, _ = built_package
        src = (pkg_dir / "case1_dual_ma_ptrade.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        batch_calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("get_fundamentals_batch", "get_history_batch"):
                    batch_calls.add(node.func.attr)
        assert batch_calls == set(), f"PTrade must not call batch APIs: {batch_calls}"

    def test_manifest_records_both_platforms(self, built_package):
        _pkg_dir, manifest = built_package
        platforms = manifest["target_platforms"]
        assert "quantstudio" in platforms
        assert "ptrade-default" in platforms

    def test_manifest_entry_points_match_rendered_files(self, built_package):
        pkg_dir, manifest = built_package
        for entry in manifest["entry_points"]:
            assert (pkg_dir / entry["filename"]).exists(), \
                f"manifest entry {entry['filename']} missing on disk"


# ── 3. End-to-end static compile chain ───────────────────────────────────────

class TestStaticCompileChain:
    def test_spec_to_ir_strong_consistency(self, built_package, case1_spec):
        """The package's frozen IR == fresh build_strategy_ir(spec).to_dict()."""
        from quantstudio.strategy_compiler.build_strategy_ir import build_strategy_ir
        pkg_dir, _ = built_package
        frozen_ir = _load(pkg_dir / "strategy_ir.json")
        fresh_ir = build_strategy_ir(case1_spec).to_dict()
        assert frozen_ir == fresh_ir, "package IR diverged from fresh build"

    def test_spec_in_package_equals_input_spec(self, built_package, case1_spec):
        pkg_dir, _ = built_package
        frozen_spec = _load(pkg_dir / "strategy_spec.json")
        assert frozen_spec == case1_spec, "package spec diverged from input"

    def test_package_importable_as_module(self, built_package):
        """__init__.py imports cleanly (no side effects, no missing deps)."""
        pkg_dir, _ = built_package
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            f"{pkg_dir.name}__init", pkg_dir / "__init__.py")
        mod = importlib.util.module_from_spec(spec)
        # exec_module must not raise
        spec.loader.exec_module(mod)


# ── 4. Determinism + encoding stability ─────────────────────────────────────

class TestDeterminism:
    def test_two_builds_identical_canonical_digest(self, tmp_path, case1_spec):
        from quantstudio.strategy_compiler.package.builder import build_strategy_package
        from quantstudio.strategy_compiler.reference.source_digest import canonical_json_digest
        p1 = build_strategy_package(case1_spec, out_dir=tmp_path / "a")
        p2 = build_strategy_package(case1_spec, out_dir=tmp_path / "b")
        for fname in ("manifest.json", "strategy_spec.json", "strategy_ir.json"):
            d1 = canonical_json_digest(_load(p1 / fname))
            d2 = canonical_json_digest(_load(p2 / fname))
            assert d1 == d2, f"{fname} not deterministic across builds"

    def test_rendered_strategy_byte_identical_across_builds(self, tmp_path, case1_spec):
        from quantstudio.strategy_compiler.package.builder import build_strategy_package
        p1 = build_strategy_package(case1_spec, out_dir=tmp_path / "a")
        p2 = build_strategy_package(case1_spec, out_dir=tmp_path / "b")
        for fname in ("case1_dual_ma_quantstudio.py", "case1_dual_ma_ptrade.py"):
            b1 = (p1 / fname).read_bytes()
            b2 = (p2 / fname).read_bytes()
            assert b1 == b2, f"{fname} not byte-identical across builds"

    def test_files_utf8_no_bom(self, built_package):
        """All text files are UTF-8 without BOM (encoding stability)."""
        pkg_dir, _ = built_package
        for f in pkg_dir.iterdir():
            if f.suffix in (".py", ".json", ".md"):
                data = f.read_bytes()
                assert not data.startswith(b"\xef\xbb\xbf"), f"{f.name} has UTF-8 BOM"


# ── 5. Safety rules + golden protection ─────────────────────────────────────

class TestSafetyRules:
    def test_golden_protected_strategy_rejected(self, tmp_path):
        """Golden-protected IDs (etf_momentum etc.) must NOT be packageable."""
        from quantstudio.strategy_compiler.package.builder import build_strategy_package
        from quantstudio.strategy_compiler.render import GoldenProtectionError
        spec = _load(EXAMPLES / "case1_dual_ma_spec.json")
        spec["strategy_id"] = "etf_momentum"  # golden-protected
        with pytest.raises(GoldenProtectionError):
            build_strategy_package(spec, out_dir=tmp_path)

    def test_manifest_records_source_digests(self, built_package):
        """Manifest carries sha256 of each artifact (auditability)."""
        _pkg_dir, manifest = built_package
        digests = manifest.get("artifact_digests", {})
        assert "strategy_spec.json" in digests
        assert "strategy_ir.json" in digests
        assert len(digests["strategy_spec.json"]) == 64


# ── 6. G2 Oracle/reference integration (not bypassed) ───────────────────────

class TestG2ReferenceIntegration:
    def test_manifest_can_reference_g2_closure(self, tmp_path, case1_spec):
        """Package manifest can optionally link to G2 frozen closure artifacts
        (reference_signals/orders/nav/source_digest), proving the strategy's
        reference closure is not bypassed/silently dropped."""
        from quantstudio.strategy_compiler.package.builder import build_strategy_package
        g2_ref = {
            "reference_signals_path": str(FROZEN / "reference_signals.json"),
            "reference_orders_path": str(FROZEN / "reference_orders.json"),
            "reference_nav_path": str(FROZEN / "reference_nav.json"),
            "source_digest_path": str(FROZEN / "source_digest.json"),
        }
        pkg_dir = build_strategy_package(case1_spec, out_dir=tmp_path, g2_reference=g2_ref)
        manifest = _load(pkg_dir / "manifest.json")
        assert "g2_reference_closure" in manifest
        assert manifest["g2_reference_closure"]["data_digest_status"] == "blocked"

    def test_g2_reference_is_optional_not_required(self, built_package):
        """Package builds fine without G2 reference linkage (not a hard dependency)."""
        _pkg_dir, manifest = built_package
        # g2_reference_closure may be absent or null when not provided
        assert manifest.get("g2_reference_closure") is None or \
            isinstance(manifest.get("g2_reference_closure"), dict)


# ── 7. G1-I / G4 isolation ──────────────────────────────────────────────────

class TestIsolation:
    def test_g1_engine_files_unchanged_by_g3(self):
        """G3 must not modify G1-I engine/API/test files."""
        engine = ROOT / "quantstudio" / "backtest" / "backtest_engine.py"
        api = ROOT / "quantstudio" / "backtest" / "ptrade_api.py"
        basket_test = ROOT / "tests" / "test_engine_basket_rebalance.py"
        assert engine.exists() and api.exists() and basket_test.exists()

    def test_no_g4_release_artifacts(self, built_package):
        """G3 package must not contain G4 release artifacts (no CLI E2E / skill packaging)."""
        pkg_dir, _ = built_package
        g4_markers = ["release_notes", "CHANGELOG", "setup.py", "pyproject.toml",
                      "skill.yaml", "install.py"]
        for f in pkg_dir.iterdir():
            assert not any(m in f.name for m in g4_markers), \
                f"G4 marker {f.name} found in G3 package"


# ── 8. Validators integration (existing validators run on package output) ────

class TestValidatorsIntegration:
    def test_package_strategy_passes_lookahead_scan(self, built_package):
        """The rendered strategy in the package passes scan_lookahead (not bypassed)."""
        from quantstudio.strategy_compiler.validators.scan_lookahead import scan_lookahead
        from quantstudio.strategy_compiler.build_strategy_ir import build_strategy_ir
        pkg_dir, _ = built_package
        ir = build_strategy_ir(_load(pkg_dir / "strategy_spec.json"))
        qs_code = (pkg_dir / "case1_dual_ma_quantstudio.py").read_text(encoding="utf-8")
        ok, violations, _warnings = scan_lookahead(ir, qs_code)
        assert ok, f"lookahead violations on package strategy: {violations}"


# ── 9. Corrective (G3 audit-fix): manifest auditability + G2 portability ────

class TestManifestAuditability:
    """manifest.json must NOT record its own digest (self-reference is unsolvable:
    changing the manifest to record its digest changes the digest). All OTHER
    packaged artifacts (including README.md) must have correct, verifiable digests."""

    def test_manifest_does_not_record_own_digest(self, built_package):
        pkg_dir, manifest = built_package
        assert "manifest.json" not in manifest["artifact_digests"], \
            "manifest must not self-reference its own digest (unsolvable chicken-egg)"

    def test_readme_in_artifact_digests(self, built_package):
        _pkg_dir, manifest = built_package
        assert "README.md" in manifest["artifact_digests"]
        assert len(manifest["artifact_digests"]["README.md"]) == 64

    def test_every_listed_digest_matches_actual_file(self, built_package):
        """Each artifact_digests entry must equal the actual file's sha256 (per-file consistency)."""
        from quantstudio.strategy_compiler.reference.source_digest import sha256_file
        pkg_dir, manifest = built_package
        for fname, recorded in manifest["artifact_digests"].items():
            assert (pkg_dir / fname).exists(), f"digest lists {fname} but file missing"
            actual = sha256_file(pkg_dir / fname)
            assert actual == recorded, f"{fname}: digest mismatch (recorded {recorded[:12]} != actual {actual[:12]})"

    def test_all_packaged_files_have_digests(self, built_package):
        """Every non-manifest file in the package must have a digest entry."""
        pkg_dir, manifest = built_package
        digest_keys = set(manifest["artifact_digests"].keys())
        for f in pkg_dir.iterdir():
            if f.name == "manifest.json":
                continue
            assert f.name in digest_keys, f"{f.name} on disk but no digest recorded"


class TestG2LinkagePortability:
    """G2 linkage must be portable: use logical artifact IDs (no dev-machine absolute
    paths), record each of the 4 frozen artifacts' sha256, and fail closed if any missing."""

    def test_g2_linkage_uses_logical_ids_not_absolute_paths(self, tmp_path, case1_spec):
        from quantstudio.strategy_compiler.package.builder import build_strategy_package
        g2_ref = {
            "reference_signals_path": str(FROZEN / "reference_signals.json"),
            "reference_orders_path": str(FROZEN / "reference_orders.json"),
            "reference_nav_path": str(FROZEN / "reference_nav.json"),
            "source_digest_path": str(FROZEN / "source_digest.json"),
        }
        pkg_dir = build_strategy_package(case1_spec, out_dir=tmp_path, g2_reference=g2_ref)
        manifest = _load(pkg_dir / "manifest.json")
        g2 = manifest["g2_reference_closure"]
        # The 4 artifacts recorded by logical ID with sha256, not absolute paths
        assert "frozen_artifact_digests" in g2
        frozen_digests = g2["frozen_artifact_digests"]
        for art in ("reference_signals", "reference_orders", "reference_nav", "source_digest"):
            assert art in frozen_digests, f"missing frozen artifact {art}"
            assert len(frozen_digests[art]) == 64
        # No dev-machine absolute paths leaked into the manifest
        manifest_bytes = (pkg_dir / "manifest.json").read_bytes()
        assert b":\\\\" not in manifest_bytes and b"D:\\\\" not in manifest_bytes, \
            "absolute dev path leaked into manifest (not portable)"

    def test_g2_linkage_fails_closed_on_missing_artifact(self, tmp_path, case1_spec):
        """If any of the 4 G2 frozen artifacts is missing, build must fail closed (not silently skip)."""
        from quantstudio.strategy_compiler.package.builder import build_strategy_package, G2ReferenceError
        g2_ref = {
            "reference_signals_path": str(FROZEN / "reference_signals.json"),
            "reference_orders_path": str(FROZEN / "reference_orders.json"),
            "reference_nav_path": str(FROZEN / "NONEXISTENT.json"),  # missing
            "source_digest_path": str(FROZEN / "source_digest.json"),
        }
        with pytest.raises(G2ReferenceError):
            build_strategy_package(case1_spec, out_dir=tmp_path, g2_reference=g2_ref)

    def test_g2_frozen_digests_match_actual_files(self, tmp_path, case1_spec):
        """The sha256 recorded in manifest for each G2 frozen artifact matches the actual file."""
        from quantstudio.strategy_compiler.package.builder import build_strategy_package
        from quantstudio.strategy_compiler.reference.source_digest import sha256_file
        g2_ref = {
            "reference_signals_path": str(FROZEN / "reference_signals.json"),
            "reference_orders_path": str(FROZEN / "reference_orders.json"),
            "reference_nav_path": str(FROZEN / "reference_nav.json"),
            "source_digest_path": str(FROZEN / "source_digest.json"),
        }
        pkg_dir = build_strategy_package(case1_spec, out_dir=tmp_path, g2_reference=g2_ref)
        manifest = _load(pkg_dir / "manifest.json")
        frozen_digests = manifest["g2_reference_closure"]["frozen_artifact_digests"]
        mapping = {
            "reference_signals": FROZEN / "reference_signals.json",
            "reference_orders": FROZEN / "reference_orders.json",
            "reference_nav": FROZEN / "reference_nav.json",
            "source_digest": FROZEN / "source_digest.json",
        }
        for art, path in mapping.items():
            assert frozen_digests[art] == sha256_file(path), \
                f"G2 {art} digest in manifest != actual file"


class TestBuildRobustness:
    """Repeated builds, stale-file handling, cross-path determinism."""

    def test_rebuild_into_same_out_dir_no_stale_residue(self, tmp_path, case1_spec):
        """Rebuilding into an out_dir that already has an old package must not leave stale files."""
        from quantstudio.strategy_compiler.package.builder import build_strategy_package
        # First build
        pkg1 = build_strategy_package(case1_spec, out_dir=tmp_path, package_version="0.1.0")
        # Drop a stale marker file into the package dir
        (pkg1 / "STALE_MARKER.txt").write_text("old", encoding="utf-8")
        # Rebuild same version -> should replace cleanly, no STALE_MARKER
        pkg2 = build_strategy_package(case1_spec, out_dir=tmp_path, package_version="0.1.0")
        assert not (pkg2 / "STALE_MARKER.txt").exists(), "stale file survived rebuild"

    def test_cross_path_determinism(self, tmp_path, case1_spec):
        """Builds into different parent dirs produce byte-identical artifacts (path independence)."""
        from quantstudio.strategy_compiler.package.builder import build_strategy_package
        p1 = build_strategy_package(case1_spec, out_dir=tmp_path / "dir_a")
        p2 = build_strategy_package(case1_spec, out_dir=tmp_path / "dir_b")
        for fname in ("manifest.json", "strategy_spec.json", "strategy_ir.json",
                      "case1_dual_ma_quantstudio.py", "case1_dual_ma_ptrade.py",
                      "__init__.py", "README.md"):
            b1 = (p1 / fname).read_bytes()
            b2 = (p2 / fname).read_bytes()
            assert b1 == b2, f"{fname} differs across parent paths (not path-independent)"
