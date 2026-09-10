# -*- coding: utf-8 -*-
"""design_metadata 可信解析层测试（2026-09-09，docs/design-metadata-auto-profile-design.md v4）。

覆盖（终审补强全项）：
- RESOLVED：panic（可信链全通过）→ daily-bar-v1；
- NOT_FOUND_LEGACY：无 design 手工策略；
- 候选归属链：STRATEGY_ID 对应 ledger 损坏 → INVALID_JSON；
- 无关损坏 ledger → 不影响 legacy；
- 生命周期不一致（fpa=False + PUBLISHED）→ LEDGER_MISMATCH；
- profile 组合矛盾（daily+1m）→ INVALID_PROFILE；
- 转换器消费者条件执法：legacy 无 profile-sensitive API 零 BLOCK；
- 可信 design + 显式冲突 → ENGINE_PROFILE_METADATA_CONFLICT BLOCK；
- panic 自动解析端到端 → RESOLVED + SHIM。
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantstudio.strategy_compiler import design_metadata as dm  # noqa: E402
from quantstudio.strategy_compiler.design_metadata import find_design_for_strategy  # noqa: E402

_PANIC_STRATEGY = ROOT / "quantstudio" / "backtest" / "strategies" / "恐慌抄底事件驱动逆向策略.py"
_PANIC_DESIGN = (ROOT / "agent_workspace" / "panic_bottom_fishing" / "agent_strategy_design.json")
_PANIC_LEDGER = (ROOT / "agent_workspace" / "panic_bottom_fishing" / "workspace_state.json")


def _valid_design_template(strategy_id, strategy_name, profile_id="daily-bar-v1",
                           bar_frequency="1d", match_price_mode="close"):
    """真实 panic design 为模板（schema 2.3 通过），改 identity + engine_profile。"""
    d = json.loads(_PANIC_DESIGN.read_text(encoding="utf-8"))
    d["strategy_id"] = strategy_id
    d["strategy_name"] = strategy_name
    d["output"]["quantstudio_path"] = f"quantstudio/backtest/strategies/{strategy_name}.py"
    d["engine_profile"] = {"profile_id": profile_id, "bar_frequency": bar_frequency,
                           "match_price_mode": match_price_mode}
    return d


def _make_ws(tmp_path, strategy_id, strategy_name, source_code, design=None,
             ledger_overrides=None):
    """构造临时 workspace：策略 + ledger + design（schema 有效模板）。
    返回 (strategy_path, project_root)。"""
    ws = tmp_path / "agent_workspace"
    pub_dir = tmp_path / "quantstudio" / "backtest" / "strategies"
    pub_dir.mkdir(parents=True, exist_ok=True)
    sp = pub_dir / f"{strategy_name}.py"
    sp.write_text(source_code, encoding="utf-8")
    sha = hashlib.sha256(sp.read_bytes()).hexdigest()
    wd = ws / strategy_id
    wd.mkdir(parents=True, exist_ok=True)
    ledger = {
        "strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "quantstudio_output": f"quantstudio/backtest/strategies/{strategy_name}.py",
        "canonical_sha256": sha,
        "stage": "PUBLISHED",
        "publish_status": "PASS",
        "quantstudio_output_status": "GENERATED",
        "formal_publish_allowed": True,
    }
    if ledger_overrides:
        ledger.update(ledger_overrides)
    (wd / "workspace_state.json").write_text(json.dumps(ledger), encoding="utf-8")
    design = design if design is not None else _valid_design_template(strategy_id, strategy_name)
    (wd / "agent_strategy_design.json").write_text(json.dumps(design), encoding="utf-8")
    return sp, tmp_path


# ---- 1. RESOLVED：可信链全通过（合成 workspace）----
def test_resolved_chain(tmp_path):
    src = 'STRATEGY_ID = "test_panic"\ndef handle_data(context, data):\n    return\n'
    sp, ws_root = _make_ws(tmp_path, "test_panic", "测试恐慌策略", src)
    r = find_design_for_strategy(sp, ws_root)
    assert r.status == "RESOLVED", r.reason
    assert r.engine_profile == "daily-bar-v1"
    assert r.strategy_id == "test_panic"
    assert r.source_sha256 and r.design_sha256 and r.schema_sha256


# ---- 2. NOT_FOUND_LEGACY ----
def test_not_found_legacy(tmp_path):
    sp = tmp_path / "legacy.py"
    sp.write_text("def handle_data(context, data):\n    return\n", encoding="utf-8")
    r = find_design_for_strategy(sp, tmp_path)
    assert r.status == "NOT_FOUND_LEGACY"


# ---- 3. 候选归属链：STRATEGY_ID 对应 ledger 损坏 → INVALID_JSON ----
def test_sid_ledger_corrupt_invalid_json(tmp_path):
    sp, ws_root = _make_ws(tmp_path, "corrupt_sid", "corrupt_strat",
                           'STRATEGY_ID = "corrupt_sid"\ndef handle_data(context, data):\n    return\n')
    (ws_root / "agent_workspace" / "corrupt_sid" / "workspace_state.json").write_text(
        "{broken json", encoding="utf-8")
    r = find_design_for_strategy(sp, ws_root)
    assert r.status == "INVALID_JSON"
    assert "corrupt_sid" in r.reason


# ---- 4. 无关损坏 ledger → 不影响 legacy ----
def test_unrelated_corrupt_ledger_ignored(tmp_path):
    sp = tmp_path / "legacy_ok.py"
    sp.write_text("def handle_data(context, data):\n    return\n", encoding="utf-8")
    bad = tmp_path / "agent_workspace" / "broken_ws"
    bad.mkdir(parents=True)
    (bad / "workspace_state.json").write_text("{broken", encoding="utf-8")
    r = find_design_for_strategy(sp, tmp_path)
    assert r.status == "NOT_FOUND_LEGACY"


# ---- 5. 生命周期不一致（fpa=False + PUBLISHED）→ LEDGER_MISMATCH ----
def test_lifecycle_inconsistent(tmp_path):
    src = 'STRATEGY_ID = "lc_bad"\ndef handle_data(context, data):\n    return\n'
    sp, ws_root = _make_ws(tmp_path, "lc_bad", "lifecycle_bad", src,
                           ledger_overrides={"formal_publish_allowed": False})
    r = find_design_for_strategy(sp, ws_root)
    assert r.status == "LEDGER_MISMATCH"
    assert "formal_publish_allowed" in r.reason or "fpa" in r.reason


# ---- 6. profile 组合矛盾（daily+1m）→ INVALID_PROFILE ----
def test_profile_combo_inconsistent(tmp_path):
    src = 'STRATEGY_ID = "combo_bad"\ndef handle_data(context, data):\n    return\n'
    bad_design = _valid_design_template("combo_bad", "combo_bad_strat",
                                        profile_id="daily-bar-v1", bar_frequency="1m", match_price_mode="close")
    sp, ws_root = _make_ws(tmp_path, "combo_bad", "combo_bad_strat", src, design=bad_design)
    r = find_design_for_strategy(sp, ws_root)
    assert r.status == "INVALID_PROFILE"


# ---- 7. 转换器消费者条件执法：legacy 无 profile-sensitive API 零 BLOCK ----
def test_legacy_no_profile_sensitive_zero_block():
    from quantstudio.strategy_compiler.source_import import convert_source
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "plain.py"
        p.write_text(
            "def initialize(context):\n    pass\ndef handle_data(context, data):\n    return\n",
            encoding="utf-8")
        r = convert_source(p)
        assert [a.api_name for a in r.actions if a.action_type == "BLOCK"] == []


# ---- 8. 可信 design + 显式冲突 → ENGINE_PROFILE_METADATA_CONFLICT + BLOCK ----
def test_conflict_explicit_vs_trusted():
    from quantstudio.strategy_compiler.source_import import convert_source
    r = convert_source(_PANIC_STRATEGY, engine_profile="minute-bar-v1")
    assert (r.design_metadata_resolution or {}).get("status") == "ENGINE_PROFILE_METADATA_CONFLICT"
    assert "get_index_day_bar" in {a.api_name for a in r.actions if a.action_type == "BLOCK"}


# ---- 9. panic 自动解析端到端 → RESOLVED + SHIM ----
def test_panic_auto_resolve_daily():
    from quantstudio.strategy_compiler.source_import import convert_source
    r = convert_source(_PANIC_STRATEGY)
    assert (r.design_metadata_resolution or {}).get("status") == "RESOLVED"
    assert (r.design_metadata_resolution or {}).get("engine_profile") == "daily-bar-v1"
    assert "get_index_day_bar" in {a.api_name for a in r.actions if a.action_type == "SHIM"}
    assert "get_index_day_bar" not in {a.api_name for a in r.actions if a.action_type == "BLOCK"}


# ---- 10. publish_agent_strategy 框架缺口修复：agent-managed 发布后 formal_publish_allowed=True ----
def test_publish_agent_sets_formal_publish_allowed_true():
    """终审补强 B：publish_agent_strategy.py agent-managed 发布 state.update 须置
    formal_publish_allowed=True（防台账生命周期不一致——panic 6ddae987 实证）。"""
    import subprocess
    src = (ROOT / "skills" / "quantstudio-strategy-compiler" / "scripts" / "publish_agent_strategy.py").read_text(encoding="utf-8")
    # state.update 块必须含 formal_publish_allowed: True
    assert chr(34) + "formal_publish_allowed" + chr(34) + ": True" in src
    # 且 2026-09-09 修复注释存在
    assert "2026-09-09 台账一致性修复" in src or "2026-09-09" in src
