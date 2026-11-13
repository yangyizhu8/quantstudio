# -*- coding: utf-8 -*-
"""占位门隔离负例（2026-11-13 方案 v3 §6；限定实现，全族档位保持 off）。

覆盖 R2 指定五项：
  ① stage 冲突（`.draft.` 命名 / `stage: draft` 不得跳门）
  ② trigger 前半（复现条件）非空
  ③ OFF 矩阵（off/warn/strict × 全局 --strict；off 不关原必填）
  ④ 字段引用正例（受检字段内引述"占位"⇒ mention 放行）
  ⑤ 真占位负例（开头式假填 ⇒ fake 拦截）

全部用例只在 tmp 沙箱内造文件，不触碰真实知识库。
"""
import io
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import kb_hook  # noqa: E402

FAM_BASE = {
    "id": "ops",
    "dir": "knowledge/ops",
    "entry_glob": "knowledge/ops/OPS-*.md",
    "id_pattern": r"OPS-\d{4}-\d{4}",
    "fix_layer_enum": ["probe", "guard", "lifecycle", "recovery", "observability"],
    "fix_layer_multi": True,
    "domain_vocab": ["D1", "D2", "D3", "D4", "D5"],
    "required_fields": ["symptom", "root_cause", "fix_target", "evidence", "fixture", "trigger"],
}


def fam(gate):
    f = dict(FAM_BASE)
    f["placeholder_gate"] = gate
    return f


def rec(trigger="条件成立；问法：怎么又掉了", **over):
    r = {
        "id": "OPS-2026-0001",
        "symptom": "现象",
        "root_cause": "根因",
        "fix_target": "scripts/x.py:1",
        "fix_layer": "probe",
        "domain": "D5",
        "trigger": trigger,
        "recurrence": 0,
        "evidence": "docs/e.md#x",
        "fixture": "tests/t.py",
        "feedback": {"contract_row": "实质判据", "gate_rule": None, "template_patch": None},
        "status": "open",
    }
    r.update(over)
    return r


ENTRY = "knowledge/ops/OPS-2026-0001.md"


# ── 基础分类 ────────────────────────────────────────────────────────────────
def test_classify_fake_vs_mention():
    assert kb_hook.classify_placeholder("待补充：最小复现触发条件") == "fake"
    assert kb_hook.classify_placeholder("TODO") == "fake"
    assert kb_hook.classify_placeholder("看到报「校验通过」实为全占位") == "mention"
    assert kb_hook.classify_placeholder("禁止用占位文字充当已填内容") == "mention"
    assert kb_hook.classify_placeholder("正常条件；问法：怎么又掉了") is None
    assert kb_hook.classify_placeholder("") is None


# ── ② trigger 前半非空（v3 T3；v2 曾漏放）──────────────────────────────────
def test_trigger_head_empty_rejected():
    errs = kb_hook._trigger_errors("；问法：怎么又掉了")
    assert any("前半" in e for e in errs), errs


def test_trigger_head_one_char_rejected():
    errs = kb_hook._trigger_errors("就；问法：怎么又掉了")
    assert any("前半" in e for e in errs), errs


def test_trigger_tail_short_rejected():
    errs = kb_hook._trigger_errors("条件成立；问法：怎")
    assert any("后半" in e for e in errs), errs


def test_trigger_missing_question_part():
    assert kb_hook._trigger_errors("重启后进程不活")
    assert kb_hook._trigger_errors("重启后不活；问法没事")


def test_trigger_semicolon_halfwidth_ok():
    assert kb_hook._trigger_errors("条件成立; 问法: 怎么又掉了") == []
    assert kb_hook._trigger_errors("条件成立；问法：怎么又掉了") == []


# ── ① stage 冲突：族目录内不得靠改名/标记降级 ────────────────────────────────
def test_entry_stage_in_family_dir():
    assert kb_hook.entry_stage(ENTRY, fam("warn")) == "entry"


def test_entry_stage_draft_name_inside_family_dir_is_still_entry():
    rel = "knowledge/ops/OPS-2026-0001.draft.md"
    assert kb_hook.entry_stage(rel, fam("warn")) == "entry"
    rep = kb_hook.placeholder_report(rel, rec(trigger="待补充：x"), fam("strict"))
    assert "DRAFT_NAME_IGNORED_ON_ENTRY_PATH" in rep["notes"]
    assert rep["fake"], "族目录内改名不得跳门"


def test_draft_name_conflict_diagnostic_not_silently_skipped():
    """R3 第④条：.draft. 命名在族目录内时，entry_glob 命中 + id_pattern 前缀匹配
    ⇒ 同 id 双文件，必须显式诊断（不得默默跳过）。"""
    rel = "knowledge/ops/OPS-2026-0001.draft.md"
    rep = kb_hook.placeholder_report(rel, rec(trigger="待补充：x"), fam("strict"))
    assert rep["diagnostics"], "必须给出冲突诊断"
    d = rep["diagnostics"][0]
    assert d["code"] == "DRAFT_NAME_ON_ENTRY_PATH"
    assert d["entry_glob_matched"] is True
    assert d["id_match_mode"] == "prefix"
    assert d["id_value"] == "OPS-2026-0001"
    assert d["duplicate_of"] == "OPS-2026-0001.md"
    assert "人工处置" in d["action"]



def test_draft_outside_family_dir_is_draft():
    for rel in ("docs/drafts/OPS-2026-0001.md", "tmp/OPS-2026-0001.md"):
        assert kb_hook.entry_stage(rel, fam("warn")) == "draft"
        rep = kb_hook.placeholder_report(rel, rec(trigger="待补充：x"), fam("strict"))
        assert rep["notes"] == ["DRAFT_ALLOWED"]
        assert rep["fake"] == []


def test_stage_marker_has_no_effect_on_entry_path():
    """front-matter 的 stage:draft 由 entry_stage 忽略（路径判据不看字段）。"""
    rel = ENTRY
    assert kb_hook.entry_stage(rel, fam("strict")) == "entry"
    r = rec(trigger="待补充：x")
    r["stage"] = "draft"
    rep = kb_hook.placeholder_report(rel, r, fam("strict"))
    assert rep["stage"] == "entry" and rep["fake"]


# ── ③ OFF 矩阵（报告层）────────────────────────────────────────────────────
def test_gate_off_returns_none():
    assert kb_hook.placeholder_report(ENTRY, rec(trigger="待补充：x"), fam("off")) is None
    assert kb_hook.placeholder_report(ENTRY, rec(trigger="待补充：x"), fam("bogus")) is None


def test_gate_warn_reports_fake_but_not_none():
    rep = kb_hook.placeholder_report(ENTRY, rec(trigger="待补充：x"), fam("warn"))
    assert rep["level"] == "warn" and rep["fake"]


# ── ④ 字段引用正例 / ⑤ 真占位负例 ───────────────────────────────────────────
def test_field_mention_is_not_fake():
    r = rec(trigger="看到报「校验通过」实为全占位；问法：这算完成了吗？")
    rep = kb_hook.placeholder_report(ENTRY, r, fam("strict"))
    assert rep["fake"] == []
    assert "trigger" in rep["mention"]


def test_field_fake_prefix_rejected():
    r = rec(trigger="待补充：最小复现触发条件")
    rep = kb_hook.placeholder_report(ENTRY, r, fam("strict"))
    assert rep["fake"]


def test_feedback_fake_rejected():
    r = rec(feedback={"contract_row": "待补充：registry 新增行", "gate_rule": None, "template_patch": None})
    rep = kb_hook.placeholder_report(ENTRY, r, fam("strict"))
    assert any("feedback" in e for e in rep["fake"])


def test_feedback_null_allowed():
    r = rec(feedback={"contract_row": None, "gate_rule": "实质规则", "template_patch": None})
    assert kb_hook._feedback_placeholder_errors(r, fam("strict")) == []


# ── ③ 集成：check_files 三档 + 既有判据不被 off 关闭 ─────────────────────────
def _sandbox(tmp_path, gate):
    root = tmp_path
    (root / "knowledge" / "ops").mkdir(parents=True, exist_ok=True)
    f = dict(FAM_BASE)
    f["placeholder_gate"] = gate
    import yaml

    kb = {"families": [f]}
    (root / "kb-info.yaml").write_text(yaml.safe_dump(kb, allow_unicode=True), encoding="utf-8")
    return root, (root / "kb-info.yaml")


def _write_entry(root, name="OPS-2026-0001.md", trigger="条件成立；问法：怎么又掉了", drop=None):
    body = (
        "---\n"
        "id: OPS-2026-0001\n"
        'symptom: "现象"\n'
        'root_cause: "根因"\n'
        'fix_target: "scripts/x.py:1"\n'
        "fix_layer: probe\n"
        "domain: D5\n"
        'trigger: "%s"\n'
        "recurrence: 0\n"
        'evidence: "docs/e.md#x"\n'
        'fixture: "tests/t.py"\n'
        "feedback:\n"
        '  contract_row: "实质判据"\n'
        "  gate_rule: null\n"
        "  template_patch: null\n"
        "status: open\n"
        "closed_round: null\n"
        "---\n"
    ) % trigger
    if drop:
        body = "\n".join(l for l in body.splitlines() if not l.startswith(drop + ":")) + "\n"
    p = root / "knowledge" / "ops" / name
    p.write_text(body, encoding="utf-8")
    return "knowledge/ops/" + name


def test_duplicate_id_affects_result(tmp_path):
    """R4：同 id 双条必须**影响结果**（进 bad），不能只打印诊断后仍被下游当两个有效条目。"""
    root, kb = _sandbox(tmp_path, "off")
    rel1 = _write_entry(root, name="OPS-2026-0001.md")
    rel2 = _write_entry(root, name="OPS-2026-0001.draft.md")
    res = kb_hook.check_files(str(root), str(kb), [rel1, rel2])
    files = {b["file"] for b in res["bad"]}
    assert files == {rel1, rel2}, res["bad"]
    assert all(any("duplicate_id" in e for e in b["errors"]) for b in res["bad"])
    # 重复检测独立于占位门：off 档不产出 placeholder 报告，但仍须拦下重复
    assert "placeholder" not in res
    rc = kb_hook.main(["--repo-root", str(root), "--kb-info", str(kb),
                       "--files", rel1, rel2, "--json", "--strict"])
    assert rc == 1, "--strict 下同 id 重复必须非零退出"


def test_check_files_off_no_placeholder_key(tmp_path):
    root, kb = _sandbox(tmp_path, "off")
    rel = _write_entry(root, trigger="待补充：最小复现触发条件")
    res = kb_hook.check_files(str(root), str(kb), [rel])
    assert res["bad"] == []
    assert "placeholder" not in res


def test_check_files_warn_reports_but_passes(tmp_path):
    root, kb = _sandbox(tmp_path, "warn")
    rel = _write_entry(root, trigger="待补充：最小复现触发条件")
    res = kb_hook.check_files(str(root), str(kb), [rel])
    assert res["bad"] == []
    assert res["placeholder"][0]["level"] == "warn"
    assert res["placeholder"][0]["fake"]


def test_check_files_strict_fails_on_fake(tmp_path):
    root, kb = _sandbox(tmp_path, "strict")
    rel = _write_entry(root, trigger="待补充：最小复现触发条件")
    res = kb_hook.check_files(str(root), str(kb), [rel])
    assert res["bad"] and "占位检测(strict)" in "; ".join(res["bad"][0]["errors"])


def test_check_files_strict_passes_on_mention(tmp_path):
    root, kb = _sandbox(tmp_path, "strict")
    rel = _write_entry(root, trigger="看到报「校验通过」实为全占位；问法：这算完成了吗？")
    res = kb_hook.check_files(str(root), str(kb), [rel])
    assert res["bad"] == []
    assert res["placeholder"][0]["mention"] == ["trigger"]


def test_off_does_not_disable_原有必填(tmp_path):
    """R4/R2 要求：off 只回退新门，六必填照常生效。"""
    root, kb = _sandbox(tmp_path, "off")
    rel = _write_entry(root, drop="root_cause")
    res = kb_hook.check_files(str(root), str(kb), [rel])
    assert res["bad"], "off 档下缺 root_cause 仍须 FAIL"
    assert any("root_cause 为空" in e for e in res["bad"][0]["errors"])


def test_off_with_global_strict_still_passes_placeholder(tmp_path):
    """off 档 + 全局 --strict：既有判据全通过时，占位不产生 FAIL。"""
    root, kb = _sandbox(tmp_path, "off")
    rel = _write_entry(root, trigger="待补充：最小复现触发条件")
    res = kb_hook.check_files(str(root), str(kb), [rel])
    assert res["bad"] == []
    rc = kb_hook.main(["--repo-root", str(root), "--kb-info", str(kb),
                       "--files", rel, "--json", "--strict"])
    assert rc == 0
