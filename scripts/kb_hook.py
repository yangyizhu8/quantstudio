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


def _expand_files(root, files):
    """展开 --files 入参 → (reals, glob_misses)。

    含通配符（* ? [）的按 repo_root 做 glob 展开：PowerShell 5.1 **不替外部程序展开通配符**
    （`--files knowledge/db/*.md` 会原样传给 Python），直接当字面路径处理会得到静默空集，
    表现为 `checked=0` 的假空。字面路径原样保留；去重保序。
    """
    seen, reals, misses = set(), [], []
    for f in files:
        s = _norm(str(f))
        if not s:
            continue
        if any(c in s for c in "*?["):
            hits = sorted(p for p in root.glob(s) if p.is_file())
            if not hits:
                if s not in seen:
                    seen.add(s)
                    misses.append(s)
                continue
            for h in hits:
                try:
                    rel = _norm(h.relative_to(root).as_posix())
                except ValueError:  # 绝对路径落在 root 之外
                    rel = _norm(str(h))
                if rel not in seen:
                    seen.add(rel)
                    reals.append(rel)
        elif s not in seen:
            seen.add(s)
            reals.append(s)
    return reals, misses


def _skip_reason(rel, families):
    """未命中任何族时的跳过原因分类（可机读，供 skipped[].reason）。

    - `non-entry-file`：位于某族 dir 下但不符合该族 entry_glob（如 knowledge/db/README.md）；
    - `no_family_match`：与该族目录无关的其它文件。
    """
    for fam in families:
        d = _norm(fam.get("dir", "")).rstrip("/")
        if d and (rel == d or rel.startswith(d + "/")):
            return "non-entry-file"
    return "no_family_match"


# ── 占位文本检测（2026-11-13 方案 v3 §2/§3；**默认全族 off，不改变既有 verdict**）──────
# 定性：新增检测型。启用（warn/strict）须独立审批准；本文件只提供能力，档位由 kb-info.yaml 各族
# 的 placeholder_gate 决定，缺省/非法值一律按 off 处理（fail-safe 方向＝不新增阻断）。
PLACEHOLDER_TOKENS = ("待补充", "待填", "TODO", "TBD", "FIXME", "placeholder", "占位")
PLACEHOLDER_RE = re.compile("|".join(re.escape(t) for t in PLACEHOLDER_TOKENS), re.I)
# 开头式＝「用占位词顶替内容」（假填）；句中/句尾出现＝「在讲占位这件事」（引述）
FAKE_PREFIX_RE = re.compile(
    r"^(?:待补充|待填|TODO|TBD|FIXME|placeholder|占位)\s*(?:[：:…\-—、，,\s]|$)", re.I)
GATE_LEVELS = ("off", "warn", "strict")


def classify_placeholder(value):
    """受检字段值 → 'fake' | 'mention' | None。

    **这是位置规则，不是语义完整性证明**：`mention` 只表示"占位词不在值开头"，
    不代表该字段内容已完成；`fake` 只表示"以占位词开头"，不代表其余字段无问题。
    """
    v = str(value or "").strip()
    if not v:
        return None
    if FAKE_PREFIX_RE.match(v):
        return "fake"
    if PLACEHOLDER_RE.search(v):
        return "mention"
    return None


def _trigger_errors(value):
    """trigger 两段式（T1–T5，v3 §2.2）。仅作占位门内附加判据，不并入既有 validate_record。"""
    v = str(value or "").strip()
    if not re.search(r"[；;]", v):
        return ["trigger 缺两段式：未找到分隔符「；」或「;」"]
    m = re.search(r"[；;]\s*问法[：:]", v)
    if not m:
        return ["trigger 缺两段式：未找到「问法：」段"]
    head = v[:m.start()].strip()
    tail = v[m.end():].strip()
    errs = []
    if len(head) < 2:
        errs.append("trigger 前半（复现条件）为空或过短（<2 字）")
    if len(tail) < 4:
        errs.append("trigger 后半（口头问法）为空或过短（<4 字）")
    if classify_placeholder(head) == "fake":
        errs.append("trigger 前半为占位文字")
    if classify_placeholder(tail) == "fake":
        errs.append("trigger 后半为占位文字")
    return errs


def _feedback_placeholder_errors(rec, fam=None):
    """feedback 三键假填检测（F2）；假填写了比留 null 更差。"""
    fb = rec.get("feedback")
    if fb is None:
        return []
    if not isinstance(fb, dict):
        return ["feedback 形态非映射"]
    keys = list((fam or {}).get("feedback_keys") or DEFAULT_FEEDBACK)
    fake = [k for k in keys if classify_placeholder(fb.get(k)) == "fake"]
    return ["feedback 占位假填（%s）" % "/".join(fake)] if fake else []


def entry_stage(rel, fam):
    """正式 entry / 草稿：**纯路径判据**（v3 §3.1）。

    正式 entry ＝ 命中族 entry_glob ∧ 位于族 dir ∧ basename 匹配 id_pattern（**不再看 `.draft.`**）。
    **`.draft.` 命名不能使族目录内的文件降级为草稿**——否则换个文件名就能跳门（2026-11-13 R2）。
    其余（族目录之外：`docs/drafts/`、`tmp/`、任意 `--out`）一律草稿隔离区。
    判据不引用"是否通过机检"，故无循环定义。
    """
    rel = _norm(rel)
    fam = fam or {}
    d = _norm(fam.get("dir", "")).rstrip("/")
    glob_ = fam.get("entry_glob") or ""
    id_pat = fam.get("id_pattern") or ""
    base = os.path.basename(rel)
    in_dir = bool(d) and (rel == d or rel.startswith(d + "/"))
    glob_ok = bool(glob_) and fnmatch.fnmatch(rel, glob_)
    id_ok = bool(re.match(id_pat, base)) if id_pat else True
    if in_dir and glob_ok and id_ok:
        return "entry"
    return "draft"


def placeholder_report(rel, rec, fam):
    """按族档位产出占位报告；**off/非法档位返回 None（完全不介入，payload 不变）**。"""
    fam = fam or {}
    level = str(fam.get("placeholder_gate") or "off").strip().lower()
    if level not in GATE_LEVELS:
        level = "off"
    if level == "off":
        return None
    stage = entry_stage(rel, fam)
    rep = {"level": level, "stage": stage, "fake": [], "mention": [], "notes": []}
    if stage == "draft":
        # 草稿隔离区：允许占位，只标记（不产生 fake/mention）
        rep["notes"].append("DRAFT_ALLOWED")
        return rep
    if ".draft." in os.path.basename(rel):
        # 族目录内用 .draft. 命名企图降级：判据不认，仅提示（防"改名跳门"）
        rep["notes"].append("DRAFT_NAME_IGNORED_ON_ENTRY_PATH")
    for err in _trigger_errors(rec.get("trigger")):
        rep["fake"].append(err)
    for err in _feedback_placeholder_errors(rec, fam):
        rep["fake"].append(err)
    for k in ("trigger",) + tuple(list(fam.get("feedback_keys") or DEFAULT_FEEDBACK)):
        val = rec.get(k) if k == "trigger" else (rec.get("feedback") or {}).get(k)
        if classify_placeholder(val) == "mention":
            rep["mention"].append(k)
    return rep


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
    """批量体检 → {total, checked, bad[], skipped[], families_used[], note}。

    **守恒判据**：`total == checked + len(skipped)`；`bad` 的 file 必在 checked 集合内
    （bad ⊆ checked）。`skipped[].reason` 取值：`glob_no_match` / `not_found` /
    `non-entry-file` / `no_family_match` / `kb_info_unavailable`。
    """
    root = pathlib.Path(repo_root)
    fams, note = _load_families(pathlib.Path(root) / kb_info if not os.path.isabs(kb_info) else kb_info)
    out = {"total": 0, "checked": 0, "bad": [], "skipped": [], "families_used": [], "note": note}
    expanded, glob_misses = _expand_files(root, files)
    out["total"] = len(expanded) + len(glob_misses)
    for pat in glob_misses:
        out["skipped"].append({"file": pat, "reason": "glob_no_match"})
    if fams is None:
        for rel in expanded:
            out["skipped"].append({"file": rel, "reason": "kb_info_unavailable"})
        return out
    for rel in expanded:
        path = root / rel
        if not path.is_file():
            # 存在性优先于族路由：路径即便落在族目录下，缺文件也应是 not_found 而非 non-entry-file
            out["skipped"].append({"file": rel, "reason": "not_found"})
            continue
        fam = _match_family(rel, fams)
        if fam is None:
            out["skipped"].append({"file": rel, "reason": _skip_reason(rel, fams)})
            continue
        out["checked"] += 1
        if fam.get("id") and fam["id"] not in out["families_used"]:
            out["families_used"].append(fam["id"])
        try:
            rec = _pf.parse_entry(path)
        except Exception as e:  # noqa: BLE001
            out["bad"].append({"file": rel, "family": fam.get("id"), "errors": ["parse_fail: %s" % e]})
            continue
        errs = validate_record(rec, fam)
        # 占位门（新增检测型）：仅当族档位 ≠ off 时才产出；严格档把 fake 并入 errors。
        rep = placeholder_report(rel, rec, fam)
        if rep is not None:
            out.setdefault("placeholder", []).append({"file": rel, **rep})
            if rep["level"] == "strict" and rep["fake"]:
                errs = errs + ["占位检测(strict)：%s" % "; ".join(rep["fake"])]
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
            print("[kb_hook] 体检 %d 件（族：%s），不合规 %d 件，跳过 %d 件" % (
                res["checked"], "、".join(res["families_used"]) or "无",
                len(res["bad"]), len(res["skipped"])))
        for s in res["skipped"]:
            print("  [skip] %s : %s" % (s["file"], s["reason"]))
        for b in res["bad"]:
            print("  %s [%s] : %s" % (b["file"], b["family"], "; ".join(b["errors"])))
    if a.strict and res["bad"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
