#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R0 黄金题集基线复跑器（KMAP-A1/A3，2026-10-25）

用途：
  1) 月度复测：按 docs/evidence/r0-golden-baseline-*.md 的题集表重算 M1 / M1.5 / M2 命中率
  2) 判据 8 一键化（A3）：比对主仓与副本的 R1/R4 节段 sha 与铁律归档 sha 清单

用法：
  python scripts/kb_r0_baseline.py                     # 自动取最新 r0 证据件的题集表，输出读数
  python scripts/kb_r0_baseline.py --json              # 机器可读
  python scripts/kb_r0_baseline.py --evidence <md>     # 指定题集来源
  python scripts/kb_r0_baseline.py --check-copy <副本仓路径>   # A3：副本跟随核验

判据口径（与 docs/templates/kb-golden-questions-v1.md 一致）：
  M1   ≥1 个关键词命中目标条目文本（宽松 OR）
  M1.5 ≥ceil(2/3 × 关键词数) 命中（生产口径）
  M2   全部关键词命中（严格 AND）
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KW_SEP = re.compile(r"[、,，]")


def _read(p):
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def parse_questions(md_path):
    """从证据件的 Markdown 表解析题集：qid / 问法 / 目标条目 / 关键词。"""
    rows = []
    for line in _read(md_path).splitlines():
        if not line.startswith("| Q-"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        qid, ask, target, kws = cells[0], cells[1], cells[2], cells[3]
        kw_list = [k.strip() for k in KW_SEP.split(kws) if k.strip()]
        rows.append({"qid": qid, "ask": ask, "target": target, "keywords": kw_list})
    return rows


def corpus():
    """检索面：QD + PD 族条目（按 kb-info families 的 entry_glob 口径）。"""
    files = {}
    for pat in ("knowledge/qmt/QD-*.md", "knowledge/ptrade-deploy/PD-*.md"):
        for p in glob.glob(os.path.join(ROOT, pat)):
            files[os.path.basename(p)[:12]] = _read(p)
    return files


def grade(kws, text):
    """返回 (M1命中, M1.5命中, M2命中, 命中数)。"""
    if not kws:
        return (False, False, False, 0)
    hit = sum(1 for k in kws if k in text)
    need15 = math.ceil(len(kws) * 2 / 3)
    return (hit >= 1, hit >= need15, hit == len(kws), hit)


def run(evidence_md):
    qs = parse_questions(evidence_md)
    corp = corpus()
    detail, agg = [], {"M1": 0, "M1.5": 0, "M2": 0}
    misses = {"M1": [], "M1.5": [], "M2": []}
    for q in qs:
        target = q["target"][:12]
        text = corp.get(target)
        if text is None:
            m1 = m15 = m2 = False
            hit = 0
        else:
            m1, m15, m2, hit = grade(q["keywords"], text)
        agg["M1"] += int(m1)
        agg["M1.5"] += int(m15)
        agg["M2"] += int(m2)
        for k, v in (("M1", m1), ("M1.5", m15), ("M2", m2)):
            if not v:
                misses[k].append(q["qid"])
        detail.append({"qid": q["qid"], "target": target, "keywords": q["keywords"],
                       "hits": hit, "M1": m1, "M1.5": m15, "M2": m2})
    total = len(qs)
    rate = {k: (round(agg[k] * 100.0 / total, 1) if total else 0.0) for k in agg}
    return {"total": total, "counts": agg, "rates": rate,
            "misses": misses, "detail": detail,
            "corpus_size": len(corp), "evidence": os.path.relpath(evidence_md, ROOT)}


def sect_sha(text, start_marker):
    i = text.find(start_marker)
    if i < 0:
        return None, 0
    j = text.find("\n## ", i + 5)
    s = text[i:j if j > 0 else len(text)]
    return hashlib.sha256(s.encode("utf-8")).hexdigest(), s.count("\n") + 1


def check_copy(copy_root):
    """A3：判据 8 一键化 —— 主仓 vs 副本的 R1/R4 节段 sha + 归档 sha 清单比对。"""
    out = {"section": {}, "archive": {}, "ok": True}
    marker = "## 【铁律】知识反哺 R1/R4"
    for label, root in (("main", ROOT), ("copy", copy_root)):
        agents = os.path.join(root, "AGENTS.md")
        if not os.path.exists(agents):
            out["section"][label] = {"error": "AGENTS.md missing"}
            out["ok"] = False
            continue
        sha, lines = sect_sha(_read(agents), marker)
        out["section"][label] = {"sha16": (sha or "")[:16], "lines": lines}
    m, c = out["section"].get("main", {}), out["section"].get("copy", {})
    out["section_match"] = bool(m.get("sha16")) and m.get("sha16") == c.get("sha16")
    out["ok"] = out["ok"] and out["section_match"]

    arch = os.path.join(root, "knowledge", "iron-rules")
    main_arch = os.path.join(ROOT, "knowledge", "iron-rules")
    mains = {os.path.basename(p): hashlib.sha256(_read(p).encode("utf-8")).hexdigest()[:16]
             for p in glob.glob(os.path.join(main_arch, "*.md"))}
    copies_dir = os.path.join(copy_root, "knowledge", "iron-rules")
    copys = {os.path.basename(p): hashlib.sha256(_read(p).encode("utf-8")).hexdigest()[:16]
             for p in glob.glob(os.path.join(copies_dir, "*.md"))}
    missing = sorted(set(mains) - set(copys))
    diff = sorted(k for k in set(mains) & set(copys) if mains[k] != copys[k])
    out["archive"] = {"main_count": len(mains), "copy_count": len(copys),
                      "missing_in_copy": missing, "sha_diff": diff}
    if missing or diff:
        out["ok"] = False
    out["copy_root"] = copy_root
    return out


def main():
    ap = argparse.ArgumentParser(description="R0 题集基线复跑 / 判据8 一键化")
    ap.add_argument("--evidence", default=None, help="题集证据件路径（默认取最新 r0-golden-baseline-*.md）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--check-copy", default=None, metavar="PATH", help="A3：副本仓路径（如 ../QuantStudio-trading）")
    args = ap.parse_args()

    if args.check_copy:
        res = check_copy(os.path.abspath(args.check_copy))
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            s = res["section"]
            print("[check-copy] section main=%s(%sL) copy=%s(%sL) match=%s"
                  % (s.get("main", {}).get("sha16"), s.get("main", {}).get("lines"),
                     s.get("copy", {}).get("sha16"), s.get("copy", {}).get("lines"),
                     res.get("section_match")))
            a = res["archive"]
            print("[check-copy] archive main=%d copy=%d missing=%s diff=%s"
                  % (a["main_count"], a["copy_count"], a["missing_in_copy"] or "-", a["sha_diff"] or "-"))
            print("[check-copy] RESULT=%s" % ("PASS" if res["ok"] else "FAIL"))
        return 0 if res["ok"] else 1

    ev = args.evidence
    if not ev:
        cands = sorted(glob.glob(os.path.join(ROOT, "docs", "evidence", "r0-golden-baseline-*.md")))
        if not cands:
            print("ERR: 未找到 r0-golden-baseline-*.md", file=sys.stderr)
            return 2
        ev = cands[-1]
    res = run(ev)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("[R0] evidence=%s corpus=%d questions=%d" % (res["evidence"], res["corpus_size"], res["total"]))
        for k in ("M1", "M1.5", "M2"):
            print("  %-5s %2d/%d = %5.1f%%%s" % (k, res["counts"][k], res["total"], res["rates"][k],
                                                 "" if k != "M2" else "   (阈值 85%)"))
        for k in ("M1", "M1.5", "M2"):
            if res["misses"][k]:
                print("  miss[%s]=%s" % (k, ",".join(res["misses"][k])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
