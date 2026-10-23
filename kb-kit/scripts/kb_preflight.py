#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_preflight —— 知识库预检读取器公共核（A3 泛化，2026-10-21）

自 scripts/qmt_kb_preflight.py（D-33 件 D，202 行版）抽取公共核：
- BLOCK_RULES / WARN_RULES / ACK_RE / check_product / check_source 的判定逻辑与文案
  **逐字符搬运自旧脚本**（边界表唯一权威＝docs/qmt-kb-preflight-boundary.md，改动须重走②审）；
- 本核新增（A3）：多库支持（--kb 必填；目录缺失 stderr 报错 + exit 2——不复制旧版
  静默 0 条目 PASS 的缺陷）、域过滤（--domain D1..D5；未标注 domain 的条目不匹配任何域，
  并输出 DOM warn）、hits 命中清单（qd/domain/rule/path）、
  validate_entry 条目 schema 校验与 parse_entry frontmatter 解析。

用法：
  python scripts/kb_preflight.py --kb <知识库目录> [--source <策略源码.py>] [--product <QMT产物.py>] [--domain D1|D2|D3|D4|D5] [--json]
退出码：0＝无 BLOCK（可含 WARN）｜1＝存在 BLOCK｜2＝用法/读文件错误
"""
import argparse
import io
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# ---- 边界表配置（②审裁定固化；改动须重走②审） ----
BLOCK_RULES = {
    "B1": ("QD-2026-0001", "产物物理编码契约（GBK 可编码／首行 #coding:gbk／无 BOM）"),
    "B2": ("QD-2026-0002", "get_financial_data 必填参数空串（平台静默返回空）"),
    "B3": ("QD-2026-0005", "get_stock_list_in_sector 传入 realtime（静默空列表，无 as-of）"),
}
WARN_RULES = {
    "B4": ("QD-2026-0003", "hist 请求缺折叠键（**待证**：键恒附已由 wrapper 运行时保证，"
                           "预检层不重复判定；升级需确定性静态判定式＋零误杀对照）"),
    "W1": ("QD-2026-0004", "合约快照三态非 ok（设计已定回落 live；提示可复现性风险）"),
    "W4": ("QD-2026-0005", "平台能力边界：请求板块成份/合约信息 as-of 历史回放（无源，指引改设计）"),
    "W5": ("QD-2026-0006", "验收覆盖面提示（新增 wrapper 符号后须跑含 exec 命名空间的邻接套件）"),
}
ACK_RE = re.compile(r"#\s*QS_KB_ACK:\s*(QD-\d{4}-\d{4})\s+(.+)")


def _fm_block(txt):
    """按行边界提取 frontmatter（首行 `---` 起、至下一整行 `---` 止）。

    修复（2026-10-22，P2-b 验收期阻断修复·如实披露）：原 `txt.split('---', 2)[1]` 在
    值内含字面 `---` 时（实证：PD-2026-0009 root_cause 引用代码 `strip() == \"---\"`）
    把值内 `---` 误判为结束符 ⇒ 其后字段全丢（symptom/root_cause 之后的字段全部判空）。
    改行级判定；顺带容忍首行 BOM（\\ufeff--- 亦起判，对齐 PD-0009 记载的 utf-8-sig 教训）。
    """
    lines = txt.splitlines()
    if not lines or lines[0].strip().lstrip("\ufeff") != "---":
        return ""
    out = []
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        out.append(ln)
    return "\n".join(out)


def load_kb_index(kb_dir):
    """读 <kb_dir>/QD-*.md frontmatter → 已知坑清单索引（供命中引用与域过滤）。

    记录形状＝旧版四字段 + domain：symptom / fix_layer / status / domain / path（相对 kb_dir）。
    """
    base = pathlib.Path(kb_dir)
    idx = {}
    for p in sorted(base.glob("QD-*.md")):
        txt = io.open(p, encoding="utf-8").read()
        fm = _fm_block(txt)
        rec = {}
        for ln in fm.splitlines():
            if ln and not ln.startswith((" ", "\t")) and ":" in ln:
                k, v = ln.split(":", 1)
                rec[k.strip()] = v.strip().strip('"')
        if rec.get("id"):
            idx[rec["id"]] = {"symptom": rec.get("symptom", ""), "fix_layer": rec.get("fix_layer", ""),
                              "status": rec.get("status", ""), "domain": rec.get("domain", ""),
                              "path": str(p.relative_to(base))}
    return idx


def check_product(path):
    """B1 + W1（产物级）。返回 (blocks, warns)。"""
    blocks, warns = [], []
    raw = pathlib.Path(path).read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        blocks.append(("B1", "产物含 UTF-8 BOM（QMT 按 ANSI/GBK 解码，BOM 破坏首行解析）"))
    head = raw.split(b"\n", 1)[0].decode("gbk", errors="replace").strip()
    if head != "#coding:gbk":
        blocks.append(("B1", "产物首行非 `#coding:gbk`（实测首行=%r）" % head[:40]))
    try:
        raw.decode("gbk")
    except UnicodeDecodeError as e:
        bad = raw[e.start:e.start + 1]
        blocks.append(("B1", "产物非 GBK 可编码（offset=%d byte=%r）" % (e.start, bad)))
    # W1 快照三态
    snap = os.environ.get("QS_QMT_INSTR_SNAP_PATH") or str(ROOT / "output" / "qmt_instrument_snapshot.json")
    if os.path.exists(snap):
        try:
            json.load(io.open(snap, encoding="utf-8"))
        except Exception as e:
            warns.append(("W1", "快照文件存在但不可解析（broken 态 → 设计回落 live）：%s" % e))
    else:
        warns.append(("W1", "未发现合约快照文件（disabled 态；跨推送窗可复现性风险提示）：%s" % snap))
    return blocks, warns


def _args_of(call_text):
    """取括号内实参串（调用点已由调用方定位）。"""
    i = call_text.find("(")
    return call_text[i + 1: call_text.rfind(")")] if i >= 0 else ""


def check_source(path):
    """B2/B3（BLOCK）+ B4/W4/W5/W6（WARN）。"""
    blocks, warns = [], []
    src = io.open(path, encoding="utf-8", errors="replace").read()
    lines = src.splitlines()

    # B2：get_financial_data 必填参数空串（确定性：实参串含空串字面量）
    for i, ln in enumerate(lines, 1):
        if "get_financial_data(" in ln:
            a = _args_of(ln)
            if re.search(r"(^|,)\s*(''|\"\")\s*(,|$)", a):
                blocks.append(("B2", "行 %d：get_financial_data 实参含空串字面量 → 平台静默返回空" % i))
    # B3：get_stock_list_in_sector 传第二实参（realtime）
    for i, ln in enumerate(lines, 1):
        if "get_stock_list_in_sector(" in ln:
            a = _args_of(ln)
            top = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", "", a)   # 去嵌套括号内容后数顶层逗号
            if "," in top:
                blocks.append(("B3", "行 %d：get_stock_list_in_sector 传入第二实参（realtime）→ 静默空列表" % i))
    # B4（WARN 待证）：hist 表请求且窗口内未见折叠键（覆盖 get_fundamentals / get_financial_data 两形态）
    _hist_tbl = re.compile(r"(table\s*=\s*)?['\"](eps|growth_ability|profit_ability)['\"]")
    for i, ln in enumerate(lines, 1):
        if "get_fundamentals(" in ln or "get_financial_data(" in ln:
            # 窗口剔除注释行：注释中出现字段名不得抑制检测（确定性优先）
            window = "\n".join(l for l in lines[max(0, i - 4): i + 5]
                               if not l.lstrip().startswith("#"))
            if _hist_tbl.search(window) and not re.search(
                    r"(m_anntime|m_timetag|publ_date|end_date)", window):
                warns.append(("B4", "行 %d：hist 表请求窗口未见折叠键/报告期字段（待证项，仅提示；"
                                    "wrapper 键恒附为运行时保证）" % i))
    # W4：平台能力边界（板块成份/合约信息 as-of 意图）
    for i, ln in enumerate(lines, 1):
        if re.search(r"get_stock_list_in_sector\(|get_instrument_detail\(", ln) and \
                re.search(r"(realtime|as_of|asof|历史|PIT)", ln, re.I):
            warns.append(("W4", "行 %d：出现 as-of/历史时点意图的平台调用（QMT 无 as-of 能力，指引改设计）" % i))
    # W5：验收覆盖面提示（源码内自建 exec 命名空间且缺 MODULE_HEADER 的测试不在源码域——此处仅提示符号新增）
    if "_QS_QMT_MODULE_HEADER" in src and "exec(" in src and "_TEMPLATES" in src:
        warns.append(("W5", "检测到模板 exec 命名空间构造：新增 wrapper 符号后须跑含 MODULE_HEADER 的邻接套件"))
    # 豁免扫描（QS_KB_ACK）→ 命中条目对应的 BLOCK 降 WARN（B1 不可豁免，且产物级不适用）
    acks = []
    for i, ln in enumerate(lines, 1):
        m = ACK_RE.search(ln)
        if m:
            acks.append((m.group(1), m.group(2).strip(), i))
    kept = []
    for code, msg in blocks:
        qd = BLOCK_RULES[code][0]
        hit = [a for a in acks if a[0] == qd]
        if hit:
            warns.append((code, "行 %d 豁免（QS_KB_ACK: %s %s）→ 降 WARN：%s"
                          % (hit[0][2], hit[0][0], hit[0][1], msg)))
        else:
            kept.append((code, msg))
    return kept, warns


# ---- A3 新增：条目解析 / schema 校验 / 命中清单 / 泛化 CLI ----

def parse_entry(path):
    """读单条 QD-*.md frontmatter → 条目记录 dict（含嵌套 feedback 子 dict；供 validate_entry）。"""
    txt = io.open(path, encoding="utf-8").read()
    fm = _fm_block(txt)
    rec, feedback = {}, {}
    for ln in fm.splitlines():
        if not ln or ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        k, v = k.strip(), v.strip().strip('"')
        if ln[:1] in (" ", "\t"):     # 缩进行＝feedback 子字段
            feedback[k] = v
        else:
            rec[k] = v
    if feedback or "feedback" in rec:
        rec["feedback"] = feedback
    return rec


def validate_entry(rec):
    """校验条目记录（knowledge/qmt/README.md §2 schema）→ 错误串列表（空＝通过）。

    rec 形状：frontmatter 字段 dict（parse_entry 产物或等价 dict）；
    feedback 为子 dict（无 feedback 块视为空；字面 "null" 视为空）。
    """
    errs = []
    if not re.match(r"^QD-\d{4}-\d{4}$", str(rec.get("id") or "")):
        errs.append("id 非法（应形如 QD-YYYY-NNNN）：%r" % (rec.get("id"),))
    for k in ("symptom", "root_cause", "fix_target", "evidence", "fixture", "trigger"):
        if not str(rec.get(k) or "").strip():
            errs.append("%s 为空" % k)
    if rec.get("fix_layer") not in ("wrapper", "portability_rule", "matrix", "probe", "ledger", "engine"):
        errs.append("fix_layer 非法（枚举 wrapper|portability_rule|matrix|probe|ledger|engine）：%r" % (rec.get("fix_layer"),))
    if rec.get("domain") not in ("D1", "D2", "D3", "D4", "D5"):
        errs.append("domain 非法（应为 D1..D5）：%r" % (rec.get("domain"),))
    try:
        if int(rec.get("recurrence")) < 0:
            raise ValueError
    except (TypeError, ValueError):
        errs.append("recurrence 非法（应可转 int 且 ≥0）：%r" % (rec.get("recurrence"),))
    if rec.get("status") not in ("open", "closed"):
        errs.append("status 非法（open|closed）：%r" % (rec.get("status"),))
    fb = rec.get("feedback") if isinstance(rec.get("feedback"), dict) else {}
    vals = []
    for k in ("contract_row", "gate_rule", "template_patch"):
        v = str(fb.get(k) or "").strip()
        vals.append("" if v == "null" else v)
    if not any(vals):
        errs.append("feedback 三项（contract_row/gate_rule/template_patch）至少一项非空")
    return errs


def collect_hits(kb, domain=None):
    """从知识库索引收集命中清单（A3 新增）。

    hits 元素：{"qd","domain","rule","path"}——rule 取自 BLOCK_RULES/WARN_RULES 中引用该
    条目的规则码（多条以 "/" 连接，如 QD-2026-0005 同被 B3/W4 引用）；命中但无规则码记 "-"。
    domain 给定时仅保留该域条目（未标注 domain 的条目不匹配任何域，与 CLI --domain 同判据）。
    """
    rev = {}
    for rules in (BLOCK_RULES, WARN_RULES):
        for code, (qd, _desc) in rules.items():
            rev.setdefault(qd, []).append(code)
    hits = []
    for qd, rec in kb.items():
        if domain is not None and rec.get("domain") != domain:
            continue
        codes = rev.get(qd)
        hits.append({"qd": qd, "domain": rec.get("domain", ""),
                     "rule": "/".join(codes) if codes else "-",
                     "path": rec.get("path", "")})
    return hits


def main(argv=None, default_kb=None):
    """公共核 CLI（薄壳以 default_kb 传入缺省知识库目录，保持旧调用行为/退出码不变）。"""
    ap = argparse.ArgumentParser(description="知识库预检（公共核；QMT 边界表见 docs/qmt-kb-preflight-boundary.md）")
    ap.add_argument("--kb", help="知识库目录（含 QD-*.md 条目；薄壳调用时可由缺省值提供）")
    ap.add_argument("--source", help="策略源码路径（B2/B3/W4/W5 + 豁免扫描）")
    ap.add_argument("--product", help="QMT 产物路径（B1/W1）")
    ap.add_argument("--domain", choices=["D1", "D2", "D3", "D4", "D5"],
                    help="仅保留该域条目参与 kb_entries 与 hits（未标注 domain 的条目不匹配任何域）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    a = ap.parse_args(argv)
    kb_dir = a.kb or default_kb
    if not kb_dir:
        print("用法：--kb <知识库目录> [--source <策略源码.py>] [--product <QMT产物.py>] [--domain D1..D5] [--json]",
              file=sys.stderr)
        return 2
    if not pathlib.Path(kb_dir).is_dir():
        print("知识库目录不存在：%s" % kb_dir, file=sys.stderr)
        return 2
    if not a.source and not a.product:
        print("用法：--source <策略源码.py> [--product <QMT产物.py>] [--json]", file=sys.stderr)
        return 2
    for p in (a.source, a.product):
        if p and not os.path.exists(p):
            print("读文件错误：%s 不存在" % p, file=sys.stderr)
            return 2
    kb_all = load_kb_index(kb_dir)
    blocks, warns = [], []
    if a.product:
        b, w = check_product(a.product)
        blocks += b
        warns += w
    if a.source:
        b, w = check_source(a.source)
        blocks += b
        warns += w
    if a.domain:
        no_dom = sorted(qd for qd, r in kb_all.items() if not r.get("domain"))
        kb = {qd: r for qd, r in kb_all.items() if r.get("domain") == a.domain}
        if no_dom:
            warns.append(("DOM", "%d 条条目未标注 domain，已在 --domain 过滤中排除：%s…"
                          % (len(no_dom), "、".join(no_dom[:5]))))
    else:
        kb = kb_all
    # hits ＝ 本次预检**实际命中**的条目（规则触发 → 反查条目），供 A7 的 H 指标机读；
    # 未触发任何规则的条目不入 hits（域过滤后的条目全集见 kb_ids）
    triggered = set()
    for c, _m in blocks:
        triggered.add(BLOCK_RULES[c][0])
    for c, _m in warns:
        triggered.add(WARN_RULES.get(c, ("-",))[0])
    hits = [h for h in collect_hits(kb, a.domain) if h["qd"] in triggered]
    if a.json:
        print(json.dumps({"kb_entries": len(kb), "block": [{"rule": c, "qd": BLOCK_RULES[c][0], "msg": m}
                                                           for c, m in blocks],
                          "warn": [{"rule": c, "qd": WARN_RULES.get(c, ("-",))[0], "msg": m}
                                   for c, m in warns],
                          "verdict": "BLOCK" if blocks else "PASS",
                          "kb_ids": sorted(kb.keys()),
                          "hits": hits}, ensure_ascii=False, indent=2))
        return 1 if blocks else 0
    print("== QMT 预检报告（D-33 件 D）==")
    print("已知坑清单：%d 条（%s）" % (len(kb), kb_dir))
    print("BLOCK（%d）：" % len(blocks))
    for c, m in blocks:
        print("  [%s/%s] %s" % (c, BLOCK_RULES[c][0], m))
    print("WARN（%d）：" % len(warns))
    for c, m in warns:
        print("  [%s/%s] %s" % (c, WARN_RULES.get(c, ("-",))[0], m))
    print("判定：%s" % ("BLOCK（阻断转换）" if blocks else "PASS（可转换；WARN 仅提示）"))
    return 1 if blocks else 0


if __name__ == "__main__":
    sys.exit(main())
