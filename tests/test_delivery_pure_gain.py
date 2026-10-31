# -*- coding: utf-8 -*-
"""纯增益与门禁的机检（T003 / T004 / T007 / T009 / T016）。

覆盖：
- 常量不变量（FR-001/FR-002，白名单刚性）
- manifest schema ↔ data-model 字段一致性（防文档漂移）
- 依赖守卫：内核不 import 写入路径/daemon/backtest，不调用 copytree / 逻辑导出（FR-005/029/030）
- .gitignore 忽略备份目录与 journal（FR-031）
- 审计脚本的纯函数分类与基线文件完整性（FR-041/SC-013）
- 波次门禁的读数校验（plan「波次门控」）
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DELIVERY = REPO / "quantstudio" / "delivery"
SPEC_DIR = REPO / "specs" / "003-delivery-bundle-import"


def _load_script(name: str):
    path = REPO / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ── 常量 ────────────────────────────────────────────────────────────────────
def test_constants_whitelist_is_hardcoded():
    from quantstudio.delivery import constants as C

    assert C.WHITELIST == ("quantstudio.db", "qfq_aux.db", "qfq_aux_mcp_gen1.db")
    assert isinstance(C.WHITELIST, tuple), "白名单必须是不可变元组（禁止运行时扩展）"
    for name in C.WHITELIST:
        assert not name.endswith("-wal") and not name.endswith("-shm")
    assert C.BUNDLE_ALLOWED_NAMES == C.WHITELIST + ("manifest.json",)


def test_exclude_patterns_cover_fr002():
    from quantstudio.delivery import constants as C

    samples = {
        "batch_audit": "batch_audit.db",
        "quarantine": "quarantine.db",
        "task_resume": "task_resume/2026.json",
        "daemon_status": "daemon_status.json",
        "snapshots": "snapshots/x.db",
        "logs": "logs/run.log",
        "mcp_landing": "mcp_landing/raw.json",
        "staging": "staging_r1_balance_20260828/config/secrets.env",
        "backup": "backup_stepB/x.db",
        "wp2_release": "wp2_release/a.json",
        "ptrade_fidelity": "ptrade_fidelity/b.json",
        "c4resume_staging": "qfq_aux_c4resume_staging.db",
        "env": "config/secrets.env",
        "shm": "qfq_aux.db-shm",
    }
    labels = {label for label, _ in C.EXCLUDE_PATTERNS}
    assert labels == set(samples), f"排除项标签不一致：缺 {set(samples) - labels}"
    for label, pattern in C.EXCLUDE_PATTERNS:
        assert re.search(pattern, samples[label]), f"{label} 的样例未被匹配"


def test_state_enums_stay_consistent():
    from quantstudio.delivery import constants as C

    assert C.EXCLUSIONARY_STATES == {
        C.TableState.EMPTY,
        C.TableState.UNKNOWN_BASIS,
        C.TableState.NO_WATERMARK_SYSTEM,
    }
    assert C.DEFECT_STATES == {
        C.TableState.MISSING,
        C.TableState.BEHIND,
        C.TableState.AHEAD,
    }
    assert C.ALL_STATES == C.EXCLUSIONARY_STATES | C.DEFECT_STATES | {
        C.TableState.OK,
        C.TableState.UNKNOWN_SOURCE,
    }


# ── 文档一致性 ──────────────────────────────────────────────────────────────
def test_manifest_schema_matches_data_model():
    from quantstudio.delivery.constants import MANIFEST_REQUIRED_TOP

    schema = json.loads(
        (SPEC_DIR / "contracts" / "manifest-v1.schema.json").read_text(encoding="utf-8")
    )
    assert set(schema["required"]) == set(MANIFEST_REQUIRED_TOP), (
        "schema required 与 constants.MANIFEST_REQUIRED_TOP 不一致"
    )
    assert set(MANIFEST_REQUIRED_TOP) <= set(schema["properties"]), "required 字段缺 properties 定义"

    data_model = (SPEC_DIR / "data-model.md").read_text(encoding="utf-8")
    for field in MANIFEST_REQUIRED_TOP:
        assert f"`{field}`" in data_model, f"data-model.md 未描述字段 {field}"


def test_table_set_rule_constant_matches_plan():
    from quantstudio.delivery.constants import TABLE_SET_RULE

    plan = (SPEC_DIR / "plan.md").read_text(encoding="utf-8")
    assert TABLE_SET_RULE in plan, "plan 里的表集规则字符串与 constants 不一致"


# ── 依赖守卫（结构性纯增益证据）──────────────────────────────────────────────
FORBIDDEN_IMPORT_PREFIXES = (
    "quantstudio.pipeline.writers",
    "quantstudio.pipeline.daemon",
    "quantstudio.pipeline.qfq_reanchor",   # QFQ 写入/重锚引擎
    "quantstudio.backtest",
)
FORBIDDEN_CALLS = {"copytree", "copy_tree"}


def test_delivery_kernel_imports_guard():
    import ast

    offenders = []
    for path in DELIVERY.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(FORBIDDEN_IMPORT_PREFIXES):
                        offenders.append((path.name, alias.name))
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(FORBIDDEN_IMPORT_PREFIXES):
                    offenders.append((path.name, node.module))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in FORBIDDEN_CALLS:
                    offenders.append((path.name, f"call:{node.func.attr}"))
    assert not offenders, f"内核出现禁止的 import/调用：{offenders}"


def test_no_logical_export_in_kernel():
    """FR-005：禁止 CTAS / EXPORT DATABASE / COPY … TO（会丢 PK/NOT NULL/CHECK）。"""
    patterns = [
        r"(?i)EXPORT\s+DATABASE",
        r"(?i)COPY\s+\w+\s+TO\s+'",
        r"(?i)CREATE\s+TABLE\s+\w+\s+AS\s+SELECT",
    ]
    offenders = []
    for path in DELIVERY.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            if re.search(pattern, text):
                offenders.append((path.name, pattern))
    assert not offenders, f"内核出现逻辑导出写法：{offenders}"


# ── .gitignore（FR-031）─────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "path",
    ["data/backup_import_20261031_031500/quantstudio.db", "data/import_journal.json"],
)
def test_gitignore_ignores_backup_and_journal(path: str):
    proc = subprocess.run(
        ["git", "check-ignore", "-q", path], cwd=str(REPO), capture_output=True
    )
    assert proc.returncode == 0, f"{path} 未被 .gitignore 忽略（FR-031）"


# ── 审计脚本（SC-013）───────────────────────────────────────────────────────
def test_audit_classify_three_branches():
    mod = _load_script("delivery_pure_gain_audit")
    base = {"path": "a.py", "diff_sha256": "x", "worktree_sha256": "y"}
    same = {"path": "a.py", "diff_sha256": "x", "worktree_sha256": "y"}
    changed = {"path": "a.py", "diff_sha256": "z", "worktree_sha256": "w"}

    assert mod.classify(same, base, [], []).label == "SAME"
    assert mod.classify(changed, base, ["a.py"], []).label == "SELF_CHANGED"
    assert mod.classify(changed, base, [], []).label == "OTHER_LINE"


def test_audit_baseline_file_covers_protected_and_append_only():
    path = REPO / "docs" / "evidence" / "delivery-pure-gain-baseline-20261031.json"
    assert path.exists(), "缺 W0 基线快照（T005 必须先于实现代码落盘）"
    doc = json.loads(path.read_text(encoding="utf-8"))
    paths = {e["path"] for e in doc["protected"]}
    assert "quantstudio/pipeline/writers.py" in paths
    assert len(doc["protected"]) >= 30, f"保护路径数量异常：{len(doc['protected'])}"
    aux = {e["path"] for e in doc["append_only"]}
    assert aux == {
        "quantstudio/gui/main_window.py",
        "quantstudio/gui/workers.py",
        "pyproject.toml",
        ".gitignore",
    }, "D-6 裁定的 4 个追加型文件必须记入基线"


def test_audit_protected_globs_match_expected_scope():
    mod = _load_script("delivery_pure_gain_audit")
    files = mod.protected_files()
    assert "quantstudio/pipeline/writers.py" in files
    assert all(f.startswith("quantstudio/pipeline/") for f in files)
    assert not any(f.endswith("delivery") for f in files)


# ── 波次门禁 ────────────────────────────────────────────────────────────────
def test_wave_gate_reports_missing_and_placeholder():
    mod = _load_script("delivery_wave_gate")
    text = "测试: 12 passed / 0 failed\nSC-001: 见下表\nSC-002: TODO\n"
    problems = mod.check_wave(1, text)
    assert any("SC-003" in p for p in problems)
    assert any("占位词" in p for p in problems)


def test_wave_gate_passes_with_complete_readings():
    mod = _load_script("delivery_wave_gate")
    text = "\n".join(
        [
            "测试: 30 passed / 0 failed",
            "SC-001: 五场景零产出（各一次实测）",
            "SC-002: 白名单外=0 env=0 shm=0",
            "SC-003: 三件连续只读打开成功",
            "SC-012: 体积读数=62306660352",
            "SC-013: 与基线一致",
        ]
    )
    assert mod.check_wave(1, text) == []


def test_wave_gate_rejects_unknown_wave():
    mod = _load_script("delivery_wave_gate")
    assert mod.check_wave(9, "测试: 1 passed")
