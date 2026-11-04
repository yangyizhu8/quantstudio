# -*- coding: utf-8 -*-
"""出仓前例行扫描器（DB-2026-0026）的自测：引用形态判据两向 + 交付域实扫无缺口。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "dacs", ROOT / "scripts" / "delivery_asset_completeness_scan.py")
dacs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dacs)


def _refers(txt: str, stem: str) -> bool:
    """复刻扫描器的引用形态判据（路径/.py/_load_script/import_module/import/from-import）。"""
    import re
    pats = [
        r"[\w./\\-]*" + re.escape(stem) + r"\.py",
        r"_load_script\(\s*[\"']" + re.escape(stem) + r"[\"']",
        r"import_module\(\s*[\"']" + re.escape(stem) + r"[\"']",
        r"^\s*import\s+" + re.escape(stem) + r"\b",
        r"^\s*from\s+" + re.escape(stem) + r"\s+import\b",
    ]
    return any(re.search(p, txt, re.M) for p in pats)


def test_reference_form_positive():
    assert _refers("mod = _load_script('delivery_wave_gate')", "delivery_wave_gate")
    assert _refers("import wave_gate", "wave_gate")
    assert _refers("from wave_gate import main", "wave_gate")
    assert _refers("x = importlib.import_module('smoke')", "smoke")
    assert _refers('p = "scripts/delivery_wave_gate.py"', "delivery_wave_gate")


def test_word_occurrence_is_not_a_reference():
    """词面出现 ≠ 引用（2026-11-04 扫描器初版把 'smoke'/'_resid' 当缺口的误报根因）。"""
    assert not _refers("print('smoke test passed')", "smoke")
    assert not _refers("# residual check", "_resid")
    assert not _refers("value = smoke_level", "smoke")


def test_delivery_scope_has_no_gap_now():
    report = dacs.scan("delivery")
    assert report["ok"], report["gaps"]
    assert report["scanned_files"] > 0


def test_scanner_cli_exit_code(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    assert dacs.main(["--scope", "delivery"]) == 0
    assert dacs.main(["--scope", "delivery", "--json"]) == 0
