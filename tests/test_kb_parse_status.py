# -*- coding: utf-8 -*-
"""C4 解析状态可见化 —— 聚焦隔离回归（合成文本 ＋ tmp_path，不触生产库／daemon）。

**契约锚**：`docs/handoff/kb-parse-status-detection-design-20261115.md`
  §3.1 桶定义（L97）／§3.2 三守恒式（L108-113）／§3.3 payload 字段（L119-136）
  §5 验收判据十条（L158-168）

本文件按契约逐条对齐（**不用自造字段名**）：
  * 桶：`ok`＝非空且校验无错；`empty`＝解析空/残**或**契约字段空/非法；`fail`＝解析**抛异常**
  * 明细：`{file, status, tags, id, level}`；`skip_detail`：`{file, reason, id, level}`
  * 标签六种：`name_not_canonical` / `name_id_conflict` / `id_missing` /
    `parse_empty` / `field_empty` / `dup_id`
  * `level`：`P1` / `P2` / `P3` / `OUT-OF-FAMILY`

隔离纪律：只用 `tmp_path` 内合成文本。`tests/conftest.py` L24-34 已把
`QUANTSTUDIO_DATA_ROOT` 指向临时根、L63 断言隔离（否则 exit 97）。
"""
import io
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import kb_hook  # noqa: E402

KB_INFO = """families:
  - id: tst
    dir: knowledge/tst
    entry_glob: knowledge/tst/TST-*.md
    id_pattern: 'TST-[0-9]{4}-[0-9]{4}'
    required_fields: [symptom, root_cause, fix_target, evidence, fixture, trigger]
    domain_vocab: [D1, D2]
    fix_layer_enum: [L1, L2]
    feedback_keys: [contract_row]
    status_vocab: [open, closed]
"""

GOOD = """---
id: TST-2026-0001
symptom: s
root_cause: r
fix_target: f
evidence: e
fixture: x
trigger: "\u89e6\u53d1\uff1b\u95ee\u6cd5\uff1a\u600e\u4e48\u529e"
fix_layer: L1
domain: D1
recurrence: 1
feedback:
  contract_row: y
status: open
---
"""


def _write(root, rel, text):
    p = pathlib.Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return p


@pytest.fixture()
def kb(tmp_path):
    _write(tmp_path, "knowledge/kb-info.yaml", KB_INFO)
    return tmp_path


def _run(kb, files):
    return kb_hook.check_files(str(kb), "knowledge/kb-info.yaml", files)


def _detail(res, rel):
    hits = [d for d in res["parse_detail"] if d["file"] == rel]
    assert len(hits) == 1, "parse_detail 中 %s 应有且仅一条：%r" % (rel, hits)
    return hits[0]


def _skip(res, rel):
    hits = [d for d in res["skip_detail"] if d["file"] == rel]
    assert len(hits) == 1, "skip_detail 中 %s 应有且仅一条：%r" % (rel, hits)
    return hits[0]


def _bad(res, rel):
    return [b for b in res["bad"] if b["file"] == rel]


# ── §5-1 P1：合法 ID 可见、标签在、不 skip ──────────────────
def test_s51_p1_visible_with_level_p1(kb):
    _write(kb, "knowledge/tst/TST-2026-0001.md", GOOD)
    res = _run(kb, ["knowledge/tst/TST-*.md"])
    d = _detail(res, "knowledge/tst/TST-2026-0001.md")
    assert d["status"] == "ok"
    assert d["tags"] == []
    assert d["id"] == "TST-2026-0001"
    assert d["level"] == "P1"
    assert all(s["file"] != "knowledge/tst/TST-2026-0001.md" for s in res["skip_detail"])
    assert res["bad"] == []


# ── §5-2 P2：缺 ID ⇒ empty ＋ id_missing，不 skip ───────────
def test_s52_missing_id_is_empty_level_p2(kb):
    body = GOOD.replace("id: TST-2026-0001\n", "")
    _write(kb, "knowledge/tst/TST-2026-0001.md", body)
    res = _run(kb, ["knowledge/tst/TST-*.md"])
    d = _detail(res, "knowledge/tst/TST-2026-0001.md")
    assert d["status"] == "empty"
    assert "id_missing" in d["tags"]
    assert d["id"] is None
    assert d["level"] == "P2"
    assert _bad(res, "knowledge/tst/TST-2026-0001.md"), "既有契约：缺 id 仍进 bad"


# ── §5-3 损坏 front-matter ⇒ empty/fail ＋ parse_empty ──────
def test_s53_blank_frontmatter_is_empty_with_parse_empty(kb):
    _write(kb, "knowledge/tst/TST-2026-0002.md", "---\n---\n")
    res = _run(kb, ["knowledge/tst/TST-*.md"])
    d = _detail(res, "knowledge/tst/TST-2026-0002.md")
    assert d["status"] == "empty"
    assert "parse_empty" in d["tags"]
    assert _bad(res, "knowledge/tst/TST-2026-0002.md"), "既有契约：空记录仍进 bad"


def test_s53_parse_exception_is_fail_with_parse_empty(kb):
    """解析**抛异常** ⇒ 唯一进 `fail` 桶的路径（契约 §3.1）。"""
    _write(kb, "knowledge/tst/TST-2026-0001.md", GOOD)
    orig = kb_hook._pf.parse_entry

    def boom(_p):
        raise ValueError("synthetic parse failure")

    kb_hook._pf.parse_entry = boom
    try:
        res = _run(kb, ["knowledge/tst/TST-*.md"])
    finally:
        kb_hook._pf.parse_entry = orig
    d = _detail(res, "knowledge/tst/TST-2026-0001.md")
    assert d["status"] == "fail"
    assert "parse_empty" in d["tags"]
    assert d["level"] == "P2"
    assert _bad(res, "knowledge/tst/TST-2026-0001.md")[0]["errors"][0].startswith("parse_fail:")


# ── 审核点名补测：四类残缺/异常，逐项核桶 ＋ 原 bad ──────────
def test_legal_id_missing_field_is_empty_not_fail(kb):
    """**审核点名①**：合法 id 缺字段 ⇒ `empty`（**不是 `fail`**）＋ 原 bad 不变。"""
    _write(kb, "knowledge/tst/TST-2026-0001.md", GOOD.replace("symptom: s\n", ""))
    res = _run(kb, ["knowledge/tst/TST-*.md"])
    d = _detail(res, "knowledge/tst/TST-2026-0001.md")
    assert d["status"] == "empty", "字段空归 empty（契约 §3.1）"
    assert "field_empty" in d["tags"]
    assert d["level"] == "P1", "id 合法 ⇒ level P1（不因字段残缺降级）"
    errs = _bad(res, "knowledge/tst/TST-2026-0001.md")[0]["errors"]
    assert any("symptom \u4e3a\u7a7a" in e for e in errs)


def test_no_id_but_other_content_is_empty(kb):
    """**审核点名②**：缺 id 但有其他内容 ⇒ `empty` ＋ `id_missing`（非空记录也不得判 ok）。"""
    body = GOOD.replace("id: TST-2026-0001\n", "")
    _write(kb, "knowledge/tst/TST-2026-0001.md", body)
    res = _run(kb, ["knowledge/tst/TST-*.md"])
    d = _detail(res, "knowledge/tst/TST-2026-0001.md")
    assert d["status"] == "empty"
    assert "id_missing" in d["tags"]
    assert "parse_empty" not in d["tags"], "记录非空，不应打 parse_empty"
    assert _bad(res, "knowledge/tst/TST-2026-0001.md")


def test_illegal_domain_is_empty_and_bad_kept(kb):
    """**审核点名③**：非法 domain ⇒ `empty`，且既有 bad 文案逐字保持。"""
    _write(kb, "knowledge/tst/TST-2026-0001.md", GOOD.replace("domain: D1", "domain: D9"))
    res = _run(kb, ["knowledge/tst/TST-*.md"])
    d = _detail(res, "knowledge/tst/TST-2026-0001.md")
    assert d["status"] == "empty"
    assert d["level"] == "P1"
    errs = _bad(res, "knowledge/tst/TST-2026-0001.md")[0]["errors"]
    assert any("domain \u975e\u6cd5" in e for e in errs)


def test_bad_text_unchanged_for_all_four_cases(kb):
    """**审核点名④**：四类情形的既有 bad 文案均**不含任何新增诊断字样**（纯增量）。"""
    cases = {
        "TST-2026-0001.md": GOOD.replace("symptom: s\n", ""),
        "TST-2026-0002.md": GOOD.replace("id: TST-2026-0001\n", "").replace("TST", "TST"),
        "TST-2026-0003.md": GOOD.replace("domain: D1", "domain: D9"),
        "TST-2026-0004.md": "---\n---\n",
    }
    for name, body in cases.items():
        _write(kb, "knowledge/tst/" + name, body.replace("TST-2026-0001", name[:-3]))
    res = _run(kb, ["knowledge/tst/TST-*.md"])
    banned = ("status=", "tags=", "level=", "P1", "P2", "P3", "OUT-OF-FAMILY")
    for b in res["bad"]:
        joined = "; ".join(b["errors"])
        for token in banned:
            assert token not in joined, "bad 文案混入诊断字样 %r：%s" % (token, joined)


# ── §5-4/§5-5 skip 的 level 与分类 ──────────────────────────
def test_s54_non_entry_file_is_p3_and_not_counted(kb):
    _write(kb, "knowledge/tst/README.md", "# doc\n")
    res = _run(kb, ["knowledge/tst/README.md"])
    s = _skip(res, "knowledge/tst/README.md")
    assert s["reason"] == "non-entry-file"
    assert s["level"] == "P3"
    assert res["checked"] == 0, "P3 不进 checked、不占桶"
    assert res["parse_stats"] == {"ok": 0, "empty": 0, "fail": 0}


def test_s55_out_of_family_is_explicit(kb):
    """族外（不与任何族 dir 相关）⇒ `no_family_match` ＋ `OUT-OF-FAMILY`。

    注：族**目录内**的非条目文件按现状 `_skip_reason` 归 `non-entry-file`（P3），
    见 `test_s54_*`。契约 §2.4/§5-5 的 `knowledge/ops/notes.md` 示例落在族目录内，
    在现状实现下会得 `non-entry-file` 而非 `no_family_match`——该冲突已上报裁定，
    **本实现不改 `_skip_reason`**（R5 指示「原 skipped reason 保持」）。
    """
    _write(kb, "docs/plain.md", GOOD.replace("TST-2026-0001", "TST-2026-0007"))
    res = _run(kb, ["docs/plain.md"])
    s = _skip(res, "docs/plain.md")
    assert s["reason"] == "no_family_match"
    assert s["level"] == "OUT-OF-FAMILY", "族外必须显式记，不得静默"
    assert s["id"] is None
    assert res["checked"] == 0


def test_family_dir_non_entry_is_non_entry_file_p3(kb):
    """族目录内、族前缀不匹配、无 id ⇒ `non-entry-file` ＋ `P3`（契约 §5-4）。"""
    _write(kb, "knowledge/tst/notes.md", "plain text without front-matter\n")
    res = _run(kb, ["knowledge/tst/notes.md"])
    s = _skip(res, "knowledge/tst/notes.md")
    assert s["reason"] == "non-entry-file"
    assert s["level"] == "P3"
    assert res["checked"] == 0


# ── §3.2 三守恒式 ───────────────────────────────────────────
def test_s58_equation1_total_equals_checked_plus_skipped(kb):
    _write(kb, "knowledge/tst/TST-2026-0001.md", GOOD)
    _write(kb, "knowledge/tst/README.md", "x\n")
    res = _run(kb, ["knowledge/tst/TST-*.md", "knowledge/tst/README.md",
                    "knowledge/tst/NOPE-*.md"])
    assert res["total"] == res["checked"] + len(res["skipped"])


def test_s58_equation2_checked_equals_buckets(kb):
    _write(kb, "knowledge/tst/TST-2026-0001.md", GOOD)
    _write(kb, "knowledge/tst/TST-2026-0002.md", "---\n---\n")
    # 注意：id 必须逐个改写，否则三件同 id 会触发 dup_id 而**正确地**把 ok 降为 empty
    _write(kb, "knowledge/tst/TST-2026-0003.md",
           GOOD.replace("TST-2026-0001", "TST-2026-0003").replace("domain: D1", "domain: D9"))
    res = _run(kb, ["knowledge/tst/TST-*.md"])
    ps = res["parse_stats"]
    assert res["checked"] == ps["ok"] + ps["empty"] + ps["fail"]
    assert ps == {"ok": 1, "empty": 2, "fail": 0}, "残缺/非法一律 empty，fail 仅解析异常"


def test_s58_equation3_total_equals_expanded_plus_glob_misses(kb):
    _write(kb, "knowledge/tst/TST-2026-0001.md", GOOD)
    res = _run(kb, ["knowledge/tst/TST-*.md", "knowledge/tst/NOPE-*.md"])
    glob_miss = len([s for s in res["skipped"] if s["reason"] == "glob_no_match"])
    non_miss = len([s for s in res["skipped"] if s["reason"] != "glob_no_match"])
    assert res["total"] == (len(res["parse_detail"]) + non_miss) + glob_miss


# ── §5-7 互斥与叠加 / §5-9 逐件性 ───────────────────────────
def test_s57_buckets_exclusive_tags_stacked(kb):
    _write(kb, "knowledge/tst/TST-2026-0002 \u526f\u672c.md",
           GOOD.replace("TST-2026-0001", "TST-2026-0002"))          # 命名不规范但内容合法
    _write(kb, "knowledge/tst/TST-2026-0003.md",
           GOOD.replace("TST-2026-0001", "TST-2026-0099").replace("symptom: s\n", ""))
    res = _run(kb, ["knowledge/tst/TST-*.md"])
    a = _detail(res, "knowledge/tst/TST-2026-0002 \u526f\u672c.md")
    assert a["status"] == "ok", "文件名问题只附标签、不改桶（契约 §3.1）"
    assert "name_not_canonical" in a["tags"]
    b = _detail(res, "knowledge/tst/TST-2026-0003.md")
    assert b["status"] == "empty"
    assert "field_empty" in b["tags"] and "name_id_conflict" in b["tags"], "标签可叠加"


def test_s59_each_input_exactly_one_row(kb):
    files = ["knowledge/tst/TST-2026-0001.md", "knowledge/tst/README.md",
             "knowledge/tst/NOPE-*.md", "knowledge/tst/TST-2026-7777.md"]
    _write(kb, "knowledge/tst/TST-2026-0001.md", GOOD)
    _write(kb, "knowledge/tst/README.md", "x\n")
    res = _run(kb, files)
    rows = len(res["parse_detail"]) + len(res["skip_detail"])
    assert rows == len(files), "每个输入恰一行（无'其他'归类）"
    assert len(res["parse_detail"]) == res["checked"]
    assert len(res["skip_detail"]) == len(res["skipped"])


def test_dup_id_moves_status_to_empty_and_syncs_stats(kb):
    """**R5 补测**：`duplicate_id` 在末段进 `bad` ⇒ 该件必须同步降为 `empty`，
    不得出现「报告 status=ok 却在 bad 里挂着 duplicate_id」的自相矛盾。"""
    _write(kb, "knowledge/tst/TST-2026-0004.md", GOOD.replace("TST-2026-0001", "TST-2026-0004"))
    _write(kb, "knowledge/tst/TST-2026-0005.md", GOOD.replace("TST-2026-0001", "TST-2026-0004"))
    res = _run(kb, ["knowledge/tst/TST-*.md"])
    for rel in ("knowledge/tst/TST-2026-0004.md", "knowledge/tst/TST-2026-0005.md"):
        d = _detail(res, rel)
        assert "dup_id" in d["tags"]
        assert d["status"] == "empty", "dup_id 已进 bad ⇒ 不得仍报 ok：%s" % rel
        assert _bad(res, rel), "该件必须在 bad 中"
    ps = res["parse_stats"]
    assert ps == {"ok": 0, "empty": 2, "fail": 0}, "桶计数必须同步：%r" % ps
    assert res["checked"] == ps["ok"] + ps["empty"] + ps["fail"], "式2 仍须成立"


def test_dup_id_tag_stacked_on_both(kb):
    _write(kb, "knowledge/tst/TST-2026-0004.md", GOOD.replace("TST-2026-0001", "TST-2026-0004"))
    _write(kb, "knowledge/tst/TST-2026-0005.md", GOOD.replace("TST-2026-0001", "TST-2026-0004"))
    res = _run(kb, ["knowledge/tst/TST-*.md"])
    a = _detail(res, "knowledge/tst/TST-2026-0004.md")
    b = _detail(res, "knowledge/tst/TST-2026-0005.md")
    assert "dup_id" in a["tags"] and "dup_id" in b["tags"]
    assert any("duplicate_id" in e for e in _bad(res, "knowledge/tst/TST-2026-0004.md")[0]["errors"])


# ── §5-10 退出码不变 ────────────────────────────────────────
def test_s510_exit_code_unchanged(kb):
    _write(kb, "knowledge/tst/TST-2026-0001.md", GOOD)
    argv = ["--repo-root", str(kb), "--kb-info", "knowledge/kb-info.yaml",
            "--strict", "--json", "--files", "knowledge/tst/TST-*.md"]
    assert kb_hook.main(argv) == 0
    _write(kb, "knowledge/tst/TST-2026-0002.md", "---\n---\n")
    assert kb_hook.main(argv) == 1, "新增诊断不改变 verdict"


def test_payload_field_contract(kb):
    """字段对照（契约 §3.3）：明细**只有** file/status/tags/id/level。"""
    _write(kb, "knowledge/tst/TST-2026-0001.md", GOOD)
    _write(kb, "knowledge/tst/README.md", "x\n")
    res = _run(kb, ["knowledge/tst/TST-*.md", "knowledge/tst/README.md"])
    for d in res["parse_detail"]:
        assert set(d) == {"file", "status", "tags", "id", "level"}, "明细字段偏离契约：%r" % sorted(d)
    for s in res["skip_detail"]:
        assert set(s) == {"file", "reason", "id", "level"}, "skip 明细字段偏离契约：%r" % sorted(s)
    assert set(res["parse_stats"]) == {"ok", "empty", "fail"}
