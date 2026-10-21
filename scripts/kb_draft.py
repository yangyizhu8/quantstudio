#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_draft —— 知识库条目草稿器（A5，2026-10-21）

按 knowledge/qmt/README.md §2 schema 生成新条目草稿：
编号 QD-<year>-<max+1> 自增、recurrence=0 / status=open / closed_round=null、
正文三节（复现步骤/影响面/讨论）占位；无 CLI 来源的字段（trigger /
feedback.contract_row）以模块常量占位串填充（负向测试可注入空串验门禁）。
写入前必经 kb_preflight.validate_entry 校验——不过则 stderr 列全部错误 + exit 1 且不落盘。

fix_layer 缺省按 --fix-target 关键词顺序判定（顺序命中即定；判据必须打印，--json 时进 notes）：
  含 matrix|verify|exec 命名空间|契约测试 → matrix；含 台账|registry|进度报告 → ledger；
  含 reconvert|portability_rules|orchestrator|编码|写盘 → portability_rule；
  含 probe|探针|能力边界 → probe；否则 → wrapper。

用法：
  python scripts/kb_draft.py --symptom "…" --evidence "…" --fix-target "文件:行" --domain D1..D5 \
      [--layer wrapper|portability_rule|matrix|probe|ledger] [--root-cause "…"] [--fixture "…"] [--out <path>] [--json]
退出码：0＝草稿已落盘｜1＝校验不过/拒绝覆盖（不落盘）｜2＝用法/环境错误
"""
import argparse
import datetime
import io
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import kb_preflight as _pf  # noqa: E402 （同目录导入；CLI 与 importlib 双语境可用）

ROOT = pathlib.Path(__file__).resolve().parents[1]
KB_DIR = ROOT / "knowledge" / "qmt"

# 无 CLI 来源字段的草稿占位（负向注入点：置空串即触发 validate_entry 门禁）
TRIGGER_PLACEHOLDER = "待补充：最小复现触发条件（一句话可判定是否命中）"
FEEDBACK_CONTRACT_PLACEHOLDER = "待补充：registry/矩阵新增行描述（或 null）"

_LAYER_KEYS = (
    ("matrix", ("matrix", "verify", "exec 命名空间", "契约测试")),
    ("ledger", ("台账", "registry", "进度报告")),
    ("portability_rule", ("reconvert", "portability_rules", "orchestrator", "编码", "写盘")),
    ("probe", ("probe", "探针", "能力边界")),
)


def _auto_layer(fix_target):
    """按 --fix-target 关键词顺序判定 fix_layer → (layer, 判据说明)。"""
    for layer, keys in _LAYER_KEYS:
        hit = [k for k in keys if k in fix_target]
        if hit:
            return layer, "fix_layer 自动判定：fix_target 含关键词「%s」→ %s" % ("、".join(hit), layer)
    return "wrapper", "fix_layer 自动判定：fix_target 未命中任何关键词 → 默认 wrapper"


def _next_id():
    """扫描 knowledge/qmt 现有编号 → 当年（QD-<year>-）最大号 +1。"""
    year = "%04d" % datetime.date.today().year
    pat = re.compile(r"^QD-(\d{4})-(\d{4})$")
    mx = 0
    for p in KB_DIR.glob("QD-*.md"):
        m = pat.match(p.stem)
        if m and m.group(1) == year:
            mx = max(mx, int(m.group(2)))
    return "QD-%s-%04d" % (year, mx + 1)


def _one_line(text):
    """压平换行（frontmatter 单行值约束；CLI 实参可能带换行）。"""
    return " ".join(str(text).split())


def _render(rec):
    """条目记录 → 条目 Markdown 文本（UTF-8 无 BOM 由写盘编码保证）。"""
    lines = [
        "---",
        "id: %s" % rec["id"],
        'symptom: "%s"' % _one_line(rec["symptom"]),
        'root_cause: "%s"' % _one_line(rec["root_cause"]),
        'fix_target: "%s"' % _one_line(rec["fix_target"]),
        "fix_layer: %s" % rec["fix_layer"],
        "domain: %s" % rec["domain"],
        'trigger: "%s"' % _one_line(rec["trigger"]),
        "recurrence: %d" % rec["recurrence"],
        'evidence: "%s"' % _one_line(rec["evidence"]),
        'fixture: "%s"' % _one_line(rec["fixture"]),
        "feedback:",
        '  contract_row: "%s"' % _one_line(rec["feedback"]["contract_row"]),
        "  gate_rule: null",
        "  template_patch: null",
        "status: %s" % rec["status"],
        "closed_round: null",
        "---",
        "",
        "## 复现步骤",
        "",
        "（待补充）",
        "",
        "## 影响面",
        "",
        "（待补充）",
        "",
        "## 讨论",
        "",
        "（待补充）",
        "",
    ]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="知识库条目草稿器（A5；写入前经 kb_preflight.validate_entry 校验）")
    ap.add_argument("--symptom", required=True, help="现象一句话（可检索）")
    ap.add_argument("--evidence", required=True, help="验证证据指针（主仓路径）")
    ap.add_argument("--fix-target", required=True, help="修复落点（文件路径:行 或 描述）")
    ap.add_argument("--domain", required=True, choices=["D1", "D2", "D3", "D4", "D5"],
                    help="域编码（knowledge/qmt/README.md §2.1 判域规则）")
    ap.add_argument("--layer", choices=["wrapper", "portability_rule", "matrix", "probe", "ledger"],
                    help="fix_layer；缺省按 --fix-target 关键词自动判定（判据打印/入 notes）")
    ap.add_argument("--root-cause", help="根因一句话（省略则校验不过、不落盘）")
    ap.add_argument("--fixture", help="复现固件/测试路径（省略则校验不过、不落盘）")
    ap.add_argument("--out", help="输出路径（默认 knowledge/qmt/QD-<year>-<max+1>.md；已存在拒绝覆盖）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    a = ap.parse_args(argv)

    if a.layer:
        layer, basis = a.layer, "fix_layer 由 --layer 显式指定：%s" % a.layer
    else:
        layer, basis = _auto_layer(a.fix_target)
    notes = [basis]
    if not a.out and not KB_DIR.is_dir():
        print("知识库目录不存在（默认输出路径依赖它）：%s" % KB_DIR, file=sys.stderr)
        return 2
    entry_id = _next_id()
    notes.append("编号 %s（knowledge/qmt 当年最大号+1）" % entry_id)
    rec = {
        "id": entry_id,
        "symptom": a.symptom,
        "root_cause": a.root_cause or "",
        "fix_target": a.fix_target,
        "fix_layer": layer,
        "domain": a.domain,
        "trigger": TRIGGER_PLACEHOLDER,
        "recurrence": 0,
        "evidence": a.evidence,
        "fixture": a.fixture or "",
        "feedback": {"contract_row": FEEDBACK_CONTRACT_PLACEHOLDER, "gate_rule": None, "template_patch": None},
        "status": "open",
    }
    out = pathlib.Path(a.out) if a.out else KB_DIR / ("%s.md" % entry_id)
    errs = list(_pf.validate_entry(rec))
    if out.exists():
        errs.append("目标文件已存在，拒绝覆盖（不落盘）：%s" % out)
    if errs:
        for e in errs:
            print("错误：%s" % e, file=sys.stderr)
        if a.json:
            print(json.dumps({"ok": False, "path": None, "domain": a.domain, "fix_layer": layer,
                              "notes": notes, "errors": errs}, ensure_ascii=False, indent=2))
        return 1
    try:
        with io.open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(_render(rec))
    except OSError as e:
        print("写文件错误：%s" % e, file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps({"ok": True, "path": str(out), "domain": a.domain, "fix_layer": layer,
                          "notes": notes, "errors": []}, ensure_ascii=False, indent=2))
    else:
        print(basis)
        print("编号：%s → 已写入：%s" % (entry_id, out))
        print("校验：validate_entry 通过（recurrence=0 / status=open / closed_round=null）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
