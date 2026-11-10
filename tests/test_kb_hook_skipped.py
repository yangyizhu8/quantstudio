#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_hook 改造单测（2026-11-10 派单 ①②③）。

覆盖：
  T1 skipped 字段存在且守恒判据成立（total == checked + len(skipped)）
  T2 --files glob 展开（修复 PS 5.1 不展开通配符致 checked=0 假空）
  T3 同族目录内非条目件 → skipped reason=non-entry-file（knowledge/db/README.md）
  T4 glob 无命中 → skipped reason=glob_no_match（不静默丢）
  T5 bad ⊆ checked（不合规项必属已体检集合）
  T6 真实库回归：knowledge/db/DB-*.md 三条 checked=3 / bad=[]
"""
import io
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import kb_hook as hk  # noqa: E402

ENTRY = """---
id: {eid}
symptom: "s"
root_cause: "r"
fix_target: "f"
fix_layer: {layer}
domain: D1
trigger: "复现条件；问法：怎么办"
recurrence: 0
evidence: "e"
fixture: "x"
feedback:
  contract_row: "ok"
status: open
---
body
"""


@pytest.fixture()
def sandbox(tmp_path):
    """迷你仓库：tb 族（TB-*.md）＋ README ＋ 一条越界路径。"""
    kb = tmp_path / "knowledge"
    fam = kb / "tb"
    fam.mkdir(parents=True)
    (kb / "kb-info.yaml").write_text(
        "families:\n"
        "  - id: tb\n"
        '    dir: "knowledge/tb"\n'
        '    entry_glob: "knowledge/tb/TB-*.md"\n'
        "    id_pattern: 'TB-\\\\d{4}-\\\\d{4}'\n"
        "    fix_layer_enum: [alpha, beta]\n"
        "    fix_layer_multi: true\n"
        "    domain_vocab: [D1, D2]\n"
        "    required_fields: [symptom, root_cause, fix_target, evidence, fixture, trigger]\n",
        encoding="utf-8",
    )
    (fam / "TB-2026-0001.md").write_text(
        ENTRY.format(eid="TB-2026-0001", layer="alpha"), encoding="utf-8")
    (fam / "TB-2026-0002.md").write_text(
        ENTRY.format(eid="TB-2026-0002", layer="beta+alpha"), encoding="utf-8")
    # 坏条目：fix_layer 越界 → 应进 bad
    (fam / "TB-2026-0003.md").write_text(
        ENTRY.format(eid="TB-2026-0003", layer="gamma"), encoding="utf-8")
    (fam / "README.md").write_text("# 族说明（非条目件）\n", encoding="utf-8")
    return tmp_path


def _call(sandbox, files):
    return hk.check_files(str(sandbox), "knowledge/kb-info.yaml", files)


def test_t1_conservation(sandbox):
    """T1：守恒判据 total == checked + len(skipped)；skipped 字段恒在。"""
    res = _call(sandbox, ["knowledge/tb/TB-2026-0001.md", "knowledge/tb/README.md",
                          "knowledge/tb/not-exist.md"])
    assert "skipped" in res and isinstance(res["skipped"], list)
    assert res["total"] == res["checked"] + len(res["skipped"]), res
    assert res["checked"] == 1
    reasons = {s["file"]: s["reason"] for s in res["skipped"]}
    assert reasons["knowledge/tb/README.md"] == "non-entry-file"
    assert reasons["knowledge/tb/not-exist.md"] == "not_found"


def test_t2_glob_expansion(sandbox):
    """T2：含通配符的入参经 glob 展开（PS 5.1 不展开 → 修复前 checked=0 假空）。"""
    res = _call(sandbox, ["knowledge/tb/TB-*.md"])
    assert res["checked"] == 3, res          # 0001/0002/0003 全部命中
    assert res["total"] == res["checked"] + len(res["skipped"])
    assert all(not s["file"].startswith("knowledge/tb/TB-*") for s in res["skipped"])


def test_t3_readme_explicit_skip(sandbox):
    """T3：同族目录内非条目件显式 skip（reason=non-entry-file），不静默丢弃。"""
    res = _call(sandbox, ["knowledge/tb/README.md"])
    assert res["checked"] == 0
    assert len(res["skipped"]) == 1
    assert res["skipped"][0]["reason"] == "non-entry-file"
    assert res["total"] == 1


def test_t4_glob_no_match(sandbox):
    """T4：glob 无命中 → glob_no_match（保持可见，不静默成空）。"""
    res = _call(sandbox, ["knowledge/tb/TB-9999-*.md"])
    assert res["checked"] == 0
    assert len(res["skipped"]) == 1
    assert res["skipped"][0]["reason"] == "glob_no_match"
    assert res["total"] == 1


def test_t5_bad_subset_checked(sandbox):
    """T5：bad ⊆ checked（越界 fix_layer 进 bad，且该文件在已体检集合内）。"""
    res = _call(sandbox, ["knowledge/tb/TB-2026-0003.md"])
    assert res["checked"] == 1
    assert len(res["bad"]) == 1 and res["bad"][0]["file"] == "knowledge/tb/TB-2026-0003.md"
    checked_files = {"knowledge/tb/TB-2026-0003.md"}
    assert all(b["file"] in checked_files for b in res["bad"])


def test_t6_real_repo_regression():
    """T6：真实库回归——knowledge/db/DB-*.md 经 glob 展开 → 全量命中 / bad=[]（不写死条数）。"""
    expected = len(list((REPO / "knowledge" / "db").glob("DB-*.md")))
    assert expected >= 3, "db 族条目数异常（<3）"
    res = hk.check_files(str(REPO), "knowledge/kb-info.yaml", ["knowledge/db/DB-*.md"])
    assert res["checked"] == expected, (res["checked"], expected)
    assert res["bad"] == [], res
    assert res["families_used"] == ["db"], res
    assert res["total"] == res["checked"] + len(res["skipped"])
