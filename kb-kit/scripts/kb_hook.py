#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_hook —— 通用 KB 条目体检钩子（KMAP-M1，2026-10-22）

从 prepush-gate 内联体检抽壳而来：读 knowledge/kb-info.yaml 的 families 段，
按 entry_glob 把每个文件路由到所属族，用该族 schema 校验（枚举/必填/多落点/括注容忍）。

单一事实源：
  - frontmatter 解析 → kb_preflight.parse_entry（与 QD 门禁同源）
  - 族定义 → kb-info.yaml（families），QuantStudio 自身即第一消费者

用法：
  python scripts/kb_hook.py --files <repo相对路径...> [--repo-root <root>] [--kb-info <path>]
                            [--json] [--strict]
退出码：0＝体检完成（含 WARN；默认不阻断）｜1＝--strict 且有不合规｜2＝环境错误（kb-info 缺失等）

WARN 起步纪律（总调度裁定⑤ 2026-10-22）：默认永不以非零退出阻断推送；
观察一轮后由项目裁定升 --strict（BLOCK）。
"""
import argparse
import fnmatch
import io
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import kb_preflight as _pf  # noqa: E402 （同目录；frontmatter 解析单一事实源）

DEFAULT_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_KB_INFO = "knowledge/kb-info.yaml"
DEFAULT_REQUIRED = ["symptom", "root_cause", "fix_target", "evidence", "fixture", "trigger"]
DEFAULT_DOMAINS = ["D1", "D2", "D3", "D4", "D5"]
DEFAULT_FEEDBACK = ["contract_row", "gate_rule", "template_patch"]
DEFAULT_STATUS = ["open", "closed"]
_PAREN_SUFFIX = re.compile(r"（[^）]*）\s*$")


def _load_families(kb_info_path):
    """读 kb-info.yaml → families 列表；缺失/不可解析返回 (None, 原因)。"""
    p = pathlib.Path(kb_info_path)
    if not p.is_file():
        return None, "kb-info 不存在：%s" % kb_info_path
    try:
        import yaml  # 延迟导入：缺 PyYAML 时给出可读原因而非崩栈
        doc = yaml.safe_load(io.open(p, encoding="utf-8").read()) or {}
    except Exception as e:  # noqa: BLE001 —— 任何解析失败都应降级为「不可用」而非阻断
        return None, "kb-info 解析失败：%s" % e
    fams = doc.get("families") or []
    if not fams:
        return None, "kb-info 无 families 段（旧版注册卡——跳过族校验）"
    return fams, None


def _norm(rel_path):
    return str(rel_path).replace("\\", "/").lstrip("./")


def _match_family(rel_path, families):
    """entry_glob → 族。支持 ** 通配（fnmatch 原生无 **，转成 *）。"""
    rel = _norm(rel_path)
    for fam in families:
        pat = _norm(fam.get("entry_glob", ""))
        if not pat:
            continue
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, pat.replace("**", "*")):
            return fam
    return None


def _fix_layer_errors(value, fam):
    raw = str(value or "").strip()
    if not raw:
        return ["fix_layer 为空"]
    enum = list(fam.get("fix_layer_enum") or [])
    multi = bool(fam.get("fix_layer_multi"))
    tokens = raw.split("+") if multi else [raw]
    bad = []
    for t in tokens:
        tok = _PAREN_SUFFIX.sub("", t.strip()).strip()
        if enum and tok not in enum:
            bad.append(tok)
    if bad:
        return ["fix_layer 非法（族 %s 枚举：%s%s）：%r" % (
            fam.get("id", "?"), "|".join(enum),
            "，多落点以 + 连接" if multi else "", raw)]
    return []


def validate_record(rec, fam):
    """单条记录 × 族 schema → 错误串列表（空＝通过）。kb_draft 复用同一函数。"""
    errs = []
    fam = fam or {}
    id_pat = fam.get("id_pattern")
    if id_pat and not re.match(id_pat, str(rec.get("id") or "")):
        errs.append("id 非法（族 %s 期望 %s）：%r" % (fam.get("id", "?"), id_pat, rec.get("id")))
    for k in list(fam.get("required_fields") or DEFAULT_REQUIRED):
        if not str(rec.get(k) or "").strip():
            errs.append("%s 为空" % k)
    errs += _fix_layer_errors(rec.get("fix_layer"), fam)
    vocab = list(fam.get("domain_vocab") or DEFAULT_DOMAINS)
    if str(rec.get("domain") or "") not in vocab:
        errs.append("domain 非法（族 %s 词表：%s）：%r" % (fam.get("id", "?"), "|".join(vocab), rec.get("domain")))
    try:
        if int(rec.get("recurrence")) < 0:
            raise ValueError
    except Exception:  # noqa: BLE001
        errs.append("recurrence 非整数或为负：%r" % (rec.get("recurrence"),))
    fb = rec.get("feedback") or {}
    keys = list(fam.get("feedback_keys") or DEFAULT_FEEDBACK)
    vals = [str(fb.get(k) or "").strip() for k in keys]
    vals = ["" if v == "null" else v for v in vals]
    if not any(vals):
        errs.append("feedback 至少一项非空（%s）" % "/".join(keys))
    status_vocab = list(fam.get("status_vocab") or DEFAULT_STATUS)
    if str(rec.get("status") or "") not in status_vocab:
        errs.append("status 非法（%s）：%r" % ("|".join(status_vocab), rec.get("status")))
    return errs


def check_files(repo_root, kb_info, files):
    """批量体检 → {checked, bad[], families_used[], note}。"""
    root = pathlib.Path(repo_root)
    fams, note = _load_families(pathlib.Path(root) / kb_info if not os.path.isabs(kb_info) else kb_info)
    out = {"checked": 0, "bad": [], "families_used": [], "note": note}
    if fams is None:
        return out
    for f in files:
        rel = _norm(f)
        fam = _match_family(rel, fams)
        if fam is None:
            continue  # 非条目件（族外的 md）不体检
        out["checked"] += 1
        if fam.get("id") and fam["id"] not in out["families_used"]:
            out["families_used"].append(fam["id"])
        path = root / rel
        try:
            rec = _pf.parse_entry(path)
        except Exception as e:  # noqa: BLE001
            out["bad"].append({"file": rel, "family": fam.get("id"), "errors": ["parse_fail: %s" % e]})
            continue
        errs = validate_record(rec, fam)
        if errs:
            out["bad"].append({"file": rel, "family": fam.get("id"), "errors": errs})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="通用 KB 条目体检钩子（读 kb-info.yaml families 分族路由）")
    ap.add_argument("--files", nargs="*", default=[], help="待体检文件（repo 相对路径）")
    ap.add_argument("--repo-root", default=str(DEFAULT_ROOT), help="仓库根（默认脚本上一级）")
    ap.add_argument("--kb-info", default=DEFAULT_KB_INFO, help="kb-info.yaml 相对路径（默认 knowledge/kb-info.yaml）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--strict", action="store_true", help="有不合规即 exit 1（默认 WARN 不阻断）")
    a = ap.parse_args(argv)
    res = check_files(a.repo_root, a.kb_info, a.files)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        if res["note"]:
            print("[kb_hook] 跳过：%s" % res["note"], file=sys.stderr)
        else:
            print("[kb_hook] 体检 %d 件（族：%s），不合规 %d 件" % (
                res["checked"], "、".join(res["families_used"]) or "无", len(res["bad"])))
        for b in res["bad"]:
            print("  %s [%s] : %s" % (b["file"], b["family"], "; ".join(b["errors"])))
    if a.strict and res["bad"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
