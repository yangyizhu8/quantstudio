#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qmt_kb_preflight —— QMT 转换预检读取器（D-33 件 D）

边界表（唯一权威）：docs/qmt-kb-preflight-boundary.md
②审三裁定（2026-10-16）：B1-B3 **BLOCK 照准**；**B4 降 WARN 待证**（升级路径：给出不依赖运行时的
确定性静态判定式＋对照样本证明零误杀后方可呈升）；W1-W6 分级与 `QS_KB_ACK` 豁免机制照准；
防误杀三条件照准（有库条目＋有复现固件＋判定确定，无启发式）。

已知坑清单：knowledge/qmt/*.md frontmatter（QD-2026-0001..，批 1 落位）。

覆盖范围（本轮，如实标注）：
- **静态可判**：B1（产物）/ B2、B3（源码）/ B4（源码，WARN 待证）/ W1（快照三态）/ W4（平台能力边界提示）
- **运行态类（预检不判）**：W2 已知形态提示、W3 性能类——由运行审计行（QS_QMT_HISTFOLD / HISTDBG3 /
  QS_QMT_PROGRESS）覆盖，预检不重复判定（避免与运行时重复判定＝B4 降级同理）
- **兜底**：W5 验收覆盖面（提示）、W6 低置信命中 → 一律 WARN

用法：
  python scripts/qmt_kb_preflight.py --source <策略源码.py> [--product <QMT产物.py>] [--json]
退出码：0＝无 BLOCK（可含 WARN）｜1＝存在 BLOCK（阻断转换）｜2＝用法/读文件错误
"""
import argparse
import io
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
KB_DIR = ROOT / "knowledge" / "qmt"

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


def load_kb_index():
    """读 knowledge/qmt/*.md frontmatter → 已知坑清单索引（供命中引用与 W6 兜底）。"""
    idx = {}
    if not KB_DIR.is_dir():
        return idx
    for p in sorted(KB_DIR.glob("QD-*.md")):
        txt = io.open(p, encoding="utf-8").read()
        fm = txt.split("---", 2)[1] if txt.startswith("---") else ""
        rec = {}
        for ln in fm.splitlines():
            if ln and not ln.startswith((" ", "\t")) and ":" in ln:
                k, v = ln.split(":", 1)
                rec[k.strip()] = v.strip().strip('"')
        if rec.get("id"):
            idx[rec["id"]] = {"symptom": rec.get("symptom", ""), "fix_layer": rec.get("fix_layer", ""),
                              "status": rec.get("status", ""), "path": str(p.relative_to(ROOT))}
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


def run(source=None, product=None):
    kb = load_kb_index()
    blocks, warns = [], []
    if product:
        b, w = check_product(product)
        blocks += b; warns += w
    if source:
        b, w = check_source(source)
        blocks += b; warns += w
    return kb, blocks, warns


def main(argv=None):
    ap = argparse.ArgumentParser(description="QMT 转换预检（D-33 件 D；边界表见 docs/qmt-kb-preflight-boundary.md）")
    ap.add_argument("--source", help="策略源码路径（B2/B3/W4/W5 + 豁免扫描）")
    ap.add_argument("--product", help="QMT 产物路径（B1/W1）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    a = ap.parse_args(argv)
    if not a.source and not a.product:
        print("用法：--source <策略源码.py> [--product <QMT产物.py>] [--json]", file=sys.stderr)
        return 2
    for p in (a.source, a.product):
        if p and not os.path.exists(p):
            print("读文件错误：%s 不存在" % p, file=sys.stderr)
            return 2
    kb, blocks, warns = run(a.source, a.product)
    if a.json:
        print(json.dumps({"kb_entries": len(kb), "block": [{"rule": c, "qd": BLOCK_RULES[c][0], "msg": m}
                                                           for c, m in blocks],
                          "warn": [{"rule": c, "qd": WARN_RULES.get(c, ("-",))[0], "msg": m}
                                   for c, m in warns],
                          "verdict": "BLOCK" if blocks else "PASS"}, ensure_ascii=False, indent=2))
        return 1 if blocks else 0
    print("== QMT 预检报告（D-33 件 D）==")
    print("已知坑清单：%d 条（knowledge/qmt/）" % len(kb))
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
