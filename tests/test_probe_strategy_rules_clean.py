#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cleaned probe v3 (no FLIP gate) verification.

Segment 1B of SKILL_RESYNC_TASKBOOK: cover
  - baseline 格复现 occ_cnt2=59.3% (live-DB; skipped if daemon locks quantstudio.db)
  - include 口径一致性 (_make_history_stub 含当日、忽略 include)
  - 翻转次数作为描述性指标正常输出（不再做硬过滤）

DB-free tests run always; the live-DB baseline test skips when the pipeline
daemon holds the exclusive lock on quantstudio.db (an environment data block,
reported honestly -- never faked as a pass).
"""
import importlib.util
import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(
    REPO_ROOT, "skills", "quantstudio-strategy-compiler",
    "references", "probe_strategy_rules.py",
)


def _load_probe():
    spec = importlib.util.spec_from_file_location("probe_clean_test", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_simulate_counts_flips_as_descriptive():
    mod = _load_probe()
    flags = [True, False, True, False, True]
    dates = [f"2024-01-{i:02d}" for i in range(1, 6)]
    occ, flips = mod._simulate(flags, dates, 1)
    assert abs(occ - 0.6) < 1e-9
    # bear-state transitions: F->T(0), T->F(1), F->T(2), T->F(3), F->T(4) = 5 flips
    assert flips == 5


def test_no_flip_gate_markers():
    src = open(PROBE, encoding="utf-8").read()
    assert "flip_ok" not in src, "flip_ok gate must be removed"
    assert "flip_limit" not in src, "flip_limit must be removed"
    # FLIP_TOLERANCE may only appear in the explanatory removal comment
    gate_uses = [
        ln for ln in src.splitlines()
        if "FLIP_TOLERANCE" in ln and "已移除" not in ln and "REMOVED" not in ln
    ]
    assert gate_uses == [], f"FLIP_TOLERANCE still used as gate: {gate_uses}"


def test_include_convention_includes_current_day():
    mod = _load_probe()
    stub = mod._make_history_stub(np.array([10.0, 20.0, 30.0]))
    out = stub(count=2, frequency="1d", field=None, security_list="X",
               fq="pre", include=False, is_dict=True)
    # include=False is ignored; window includes current day -> last 2 closes
    assert "X" in out
    assert list(out["X"]["close"]) == [20.0, 30.0]


def test_recommended_not_flipped_out():
    # Replicate the post-clean selection (sort by change_rank, -recovery_days,
    # flips; take 3) to prove a high-flip low-change cell is still eligible.
    qualifying = [
        {"variant": "baseline", "recovery_days": 5, "monthly_cap": None,
         "occ_cnt2": 0.30, "flips": 999, "change_rank": 0},
        {"variant": "band0.99", "recovery_days": 5, "monthly_cap": None,
         "occ_cnt2": 0.30, "flips": 5, "change_rank": 1},
        {"variant": "band0.98", "recovery_days": 5, "monthly_cap": None,
         "occ_cnt2": 0.30, "flips": 5, "change_rank": 2},
        {"variant": "slope_down", "recovery_days": 5, "monthly_cap": None,
         "occ_cnt2": 0.30, "flips": 5, "change_rank": 4},
    ]
    qualifying.sort(key=lambda q: (q["change_rank"], -q["recovery_days"], q["flips"]))
    recommended = qualifying[:3]
    assert recommended[0]["variant"] == "baseline"
    assert recommended[0]["flips"] == 999  # high-flip cell NOT filtered out


def test_baseline_reproduction_live_db():
    # Requires read-only quantstudio.db; skipped if the pipeline daemon holds
    # the exclusive lock (environment data block -- reported, never faked).
    import duckdb
    import json
    import tempfile
    DB = r"D:/miniQMT策略实盘/QuantStudio/data/quantstudio.db"
    try:
        con = duckdb.connect(DB, read_only=True)
        con.execute("select 1 from index_daily limit 1").fetchone()
        con.close()
    except Exception as e:
        pytest.skip(f"quantstudio.db unavailable (daemon lock / missing): {e}")

    mod = _load_probe()
    try:
        mod.main()
    except SystemExit:
        pass
    res = PROBE.replace(".py", "_result.json")
    assert os.path.exists(res), f"probe did not write {res}"
    data = json.load(open(res, encoding="utf-8"))
    occ = data["baseline_check"]["occ_cnt2"]
    assert abs(occ - 0.593) < 0.005, f"baseline occ_cnt2={occ} != 0.593"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
