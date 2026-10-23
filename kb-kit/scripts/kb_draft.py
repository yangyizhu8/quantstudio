#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_draft —— 知识库条目草稿器（KMAP-M1 泛化版，2026-10-22）

泛化点（M1-1）：`--kb <族目录>` 任意族可用；schema 由 knowledge/kb-info.yaml 的 families 段驱动
（id_pattern / fix_layer_enum / fix_layer_multi / domain_vocab / required_fields / fix_layer_keywords）。
默认 --kb knowledge/qmt（向后兼容，旧调用零改动）。

按族 schema 生成新条目草稿：编号 <id_prefix><year>-<max+1> 自增、recurrence=0 / status=open /
closed_round=null、正文三节占位；无 CLI 来源字段（trigger / feedback.contract_row）以模块常量占位
（负向测试可注入空串验门禁）。写入前必经 kb_hook.validate_record（族 schema 单一事实源）——
不过则 stderr 列全部错误 + exit 1 且不落盘。

fix_layer 缺省按族的 fix_layer_keywords 顺序判定（顺序命中即定；判据必须打印，--json 进 notes）；
族未声明关键词 ⇒ 必须显式 --layer（exit 2），不做跨族猜测。

用法：
  python scripts/kb_draft.py --symptom … --evidence … --fix-target … --domain D3 \
      [--kb knowledge/<族>] [--layer <枚举值>] [--root-cause …] [--fixture …] [--out <path>] [--json]
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
import kb_preflight as _pf  # noqa: E402 （同目录；parse_entry 单一事实源）
import kb_hook as _hk  # noqa: E402 （族 schema 校验单一事实源）

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_KB = ROOT / "knowledge" / "qmt"
KB_INFO = "knowledge/kb-info.yaml"

# 无 CLI 来源字段的草稿占位（负向注入点：置空串即触发族 schema 门禁）
TRIGGER_PLACEHOLDER = "待补充：最小复现触发条件（一句话可判定是否命中）"
FEEDBACK_CONTRACT_PLACEHOLDER = "待补充：registry/矩阵新增行描述（或 null）"


def _resolve_kb(kb_arg):
    p = pathlib.Path(kb_arg)
    return p if p.is_absolute() else (ROOT / p)


def _family_for(kb_dir):
    """按 families[].dir 定位族（kb-info 相对仓库根）；缺失返回 (None, 原因)。"""
    fams, note = _hk._load_families(ROOT / KB_INFO)
    if fams is None:
        return None, note
    try:
        rel = kb_dir.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return None, "知识库目录不在仓库内：%s" % kb_dir
    for fam in fams:
        if _hk._norm(fam.get("dir", "")) == rel:
            return fam, None
    return None, "kb-info 未登记该目录的族：%s" % rel


def _auto_layer(fix_target, fam):
    """按族 fix_layer_keywords 顺序判定 → (layer|None, 判据说明)。"""
    table = (fam or {}).get("fix_layer_keywords") or {}
    for layer, keys in table.items():
        hit = [k for k in (keys or []) if k and k in fix_target]
        if hit:
            return layer, "fix_layer 自动判定：fix_target 含关键词「%s」→ %s" % ("、".join(hit), layer)
    return None, "族未声明判层关键词或未命中任何关键词"


def _id_prefix(fam):
    m = re.match(r"^([A-Za-z]+-)", str((fam or {}).get("id_pattern") or ""))
    return m.group(1) if m else "XX-"


def _next_id(kb_dir, prefix):
    year = "%04d" % datetime.date.today().year
    pat = re.compile(r"^%s(\d{4})-(\d{4})$" % re.escape(prefix))
    mx = 0
    for p in kb_dir.glob("%s*.md" % prefix):
        m = pat.match(p.stem)
        if m and m.group(1) == year:
            mx = max(mx, int(m.group(2)))
    return "%s%s-%04d" % (prefix, year, mx + 1)


def _one_line(text):
    return " ".join(str(text).split())


def _render(rec):
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
    ap = argparse.ArgumentParser(description="知识库条目草稿器（族 schema 驱动；写入前经 kb_hook.validate_record 校验）")
    ap.add_argument("--symptom", required=True, help="现象一句话（可检索）")
    ap.add_argument("--evidence", required=True, help="验证证据指针（主仓路径）")
    ap.add_argument("--fix-target", required=True, help="修复落点（文件路径:行 或 描述）")
    ap.add_argument("--domain", required=True, help="域编码（族的 domain_vocab，默认 D1..D5）")
    ap.add_argument("--kb", default=str(DEFAULT_KB), help="目标族目录（默认 knowledge/qmt）")
    ap.add_argument("--layer", help="fix_layer；缺省按族关键词自动判定（判据打印/入 notes）")
    ap.add_argument("--root-cause", help="根因一句话（省略则校验不过、不落盘）")
    ap.add_argument("--fixture", help="复现固件/测试路径（省略则校验不过、不落盘）")
    ap.add_argument("--out", help="输出路径（默认 <kb>/<前缀><year>-<max+1>.md；已存在拒绝覆盖）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    a = ap.parse_args(argv)

    kb_dir = _resolve_kb(a.kb)
    fam, note = _family_for(kb_dir)
    if not kb_dir.is_dir():
        print("知识库目录不存在：%s" % kb_dir, file=sys.stderr)
        return 2

    vocab = list((fam or {}).get("domain_vocab") or _hk.DEFAULT_DOMAINS)
    if a.domain not in vocab:
        print("错误：domain %r 不在该族词表（%s）" % (a.domain, "|".join(vocab)), file=sys.stderr)
        return 2

    enum = list((fam or {}).get("fix_layer_enum") or [])
    multi = bool((fam or {}).get("fix_layer_multi"))
    if a.layer:
        tokens = a.layer.split("+") if multi else [a.layer]
        bad = [t for t in tokens if enum and _hk._PAREN_SUFFIX.sub("", t.strip()).strip() not in enum]
        if bad:
            print("错误：fix_layer %r 不在该族枚举（%s）%s" % (
                a.layer, "|".join(enum), "，多落点以 + 连接" if multi else ""), file=sys.stderr)
            return 2
        layer, basis = a.layer, "fix_layer 由 --layer 显式指定：%s" % a.layer
    else:
        auto, why = _auto_layer(a.fix_target, fam)
        if not auto:
            print("错误：%s；须显式 --layer（族 %s 枚举：%s）" % (
                why, (fam or {}).get("id", "?"), "|".join(enum) or "未声明"), file=sys.stderr)
            return 2
        layer, basis = auto, why
    notes = [basis]

    entry_id = _next_id(kb_dir, _id_prefix(fam))
    notes.append("编号 %s（%s 当年最大号+1；族 %s）" % (entry_id, kb_dir.name, (fam or {}).get("id", "未登记")))
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
    out = pathlib.Path(a.out) if a.out else kb_dir / ("%s.md" % entry_id)
    errs = list(_hk.validate_record(rec, fam))
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
        print("校验：kb_hook.validate_record 通过（族 %s；recurrence=0 / status=open）" % (fam or {}).get("id", "未登记"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
