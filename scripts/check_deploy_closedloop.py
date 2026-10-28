#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""qs-deploy 闭环管线巡检（PD-0011 / 缺口 G4 + G5 + G2-L2）。

一键回答「此刻闭环是否健康」，五项检查：
  ① 收敛判据（委托 scripts/check_pd_convergence.py）
  ② 回哺读库读数（preflight.read_vault：entries / source / degraded）
  ③ PD 族机检（scripts/kb_hook.py --strict）
  ④ R1/R4 留痕（台账最近轮次段落须含「知识反哺：」与「本轮沉淀：」）
  ⑤ L2 回哺有效性（**消费方记录制**：须提供运行报告 + 消费方确认行；缺任一即视为未确认）

L2 的两条硬约束（②审 2026-10-28 附加）：
  a) **不得由起草方自证**：确认行必须含 `confirmed_by=<消费方身份>`，且该身份不得含
     「起草/kb_draft/条目作者」字样；确认行必须引用运行报告路径。
  b) **读到但内容不相关 ⇒ 判「未救」**：条目 `trigger` 与运行失败签名的词面重叠 < 2 个 token
     即判未救（机检不采信条目内自述）。

退出码：0 = 闭环健康；1 = 硬缺口（收敛不满足 / 族机检不合规 / 回哺 degraded / L2 判未救）；
        2 = 仅 WARN（R1/R4 留痕缺失、样本缺项、L2 未提供材料）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

KB_DIR = REPO / "knowledge" / "ptrade-deploy"
LEDGER = REPO / "docs" / "ptrade-deploy-convergence-ledger.md"

_CJK = re.compile(r"[\u4e00-\u9fff]")
_ASCII_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _tokens(text: str) -> set:
    """CJK 二元组 + ASCII 词（≥3 字符）——用于 L2 相关性词面重叠。"""
    toks: set = set()
    for m in _ASCII_WORD.finditer(text):
        toks.add(m.group(0).lower())
    cjk = "".join(ch for ch in text if _CJK.match(ch))
    for i in range(len(cjk) - 1):
        toks.add(cjk[i:i + 2])
    return toks


def _ident_tokens(text: str) -> set:
    """标识符类 token（ASCII 词 ≥3）——L2 主判据。

    实证（2026-10-28）：仅用 CJK 二元组会因 `notes` 里的通用词（嵌套/门禁/注入）造成
    **假『已救』**（条目讲「未知 API 误报」、失败讲「set_limit_mode 未门控」，却被判相关 8 token）。
    故收紧为：必须命中**标识符**（API 名/阶段名/契约字段），通用中文词不单独构成相关。
    """
    return {m.group(0).lower() for m in _ASCII_WORD.finditer(text)}


def signature_from_report(path: str) -> str:
    """从运行报告取**失败签名**：仅 FAIL 段（排除 notes——notes 是通用提示，易致假相关）。"""
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    try:
        d = json.loads(text)
    except Exception:
        d = None
    if isinstance(d, dict) and d.get("stages"):
        parts = [f"{k}: {v.get('reason','')}"
                 for k, v in d["stages"].items() if str(v.get("status")) == "FAIL"]
        return "\n".join(parts) if parts else text
    # 文本日志：只留 FAIL 行
    lines = [l for l in text.splitlines() if "FAIL" in l.upper()]
    return "\n".join(lines) if lines else text


def judge_l2(report_text: str, confirm_text: str, entry_text: str) -> dict:
    """L2：库是否『真的救了这次』（消费方记录制 + 相关性机检）。"""
    # a) 消费方记录：必须有 confirmed_by 且非起草方自证
    m = re.search(r"confirmed_by\s*[=:]\s*([^\s,，;；]+)", confirm_text)
    if not m:
        return {"saved": False, "state": "未确认",
                "reason": "缺 confirmed_by=<消费方身份>（②审 a：命中判定须由消费方记录，禁起草方自证）"}
    who = m.group(1)
    if any(k in who for k in ("起草", "kb_draft", "作者", "draft")):
        return {"saved": False, "state": "未确认",
                "reason": f"confirmed_by={who} 属起草方自证，②审 a 明确禁止"}
    if not re.search(r"(report|report_path|运行报告)\s*[=:]\s*\S+", confirm_text):
        return {"saved": False, "state": "未确认",
                "reason": "确认行未引用运行报告路径（无法核对失败签名）"}

    # b) 相关性：以**标识符**为重（通用中文词不足以构成相关）
    entry_trig = ""
    mt = re.search(r"^trigger:\s*\"(.*)\"$", entry_text, re.M)
    if mt:
        entry_trig = mt.group(1)
    if not entry_trig:
        return {"saved": False, "state": "未救",
                "reason": "条目无 trigger 字段，无法判定相关性"}
    id_overlap = _ident_tokens(entry_trig) & _ident_tokens(report_text)
    cjk_overlap = _tokens(entry_trig) & _tokens(report_text)
    if not id_overlap:
        return {"saved": False, "state": "未救",
                "reason": f"条目 trigger 与失败签名**无标识符重叠**（CJK 重叠 {len(cjk_overlap)} 个，"
                          "通用词不计）⇒ 读到条目但内容不相关，判未救（②审 b）",
                "cjk_overlap": sorted(cjk_overlap)[:8]}
    if not (len(id_overlap) >= 1 and len(cjk_overlap) >= 2):
        return {"saved": False, "state": "未救",
                "reason": f"证据不足：标识符重叠 {len(id_overlap)}、CJK 重叠 {len(cjk_overlap)}"
                          "（需 标识符≥1 且 CJK≥2；纯通用中文词不构成相关）⇒ 读到条目但内容不相关，判未救（②审 b）",
                "id_overlap": sorted(id_overlap)[:8], "cjk_overlap": sorted(cjk_overlap)[:8]}
    return {"saved": True, "state": "已救",
            "reason": f"消费方 {who} 确认 + 标识符重叠 {len(id_overlap)}"
                      f"（{', '.join(sorted(id_overlap)[:4])}）/ CJK {len(cjk_overlap)}",
            "id_overlap": sorted(id_overlap)[:8]}


def check_r1_r4(ledger_text: str) -> dict:
    """最近轮次段落是否含 R1 留痕与 R4 沉淀行。"""
    has_r1 = "知识反哺：" in ledger_text
    has_r4 = "本轮沉淀：" in ledger_text
    return {"r1": has_r1, "r4": has_r4, "ok": has_r1 and has_r4}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="qs-deploy 闭环管线巡检")
    ap.add_argument("--l2-report", default="", help="运行报告（JSON/文本）路径")
    ap.add_argument("--l2-confirm", default="", help="消费方确认行文件路径")
    ap.add_argument("--l2-entry", default="", help="候选条目 md 路径")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    out: dict = {"checks": {}, "exit": 0}
    hard_fail = False
    warn = False

    # ① 收敛判据
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "check_pd_convergence.py"), "--json"],
                       capture_output=True, text=True, encoding="utf-8")
    try:
        conv = json.loads(r.stdout or "{}")
    except Exception:
        conv = {"pass": False, "reason": f"收敛脚本输出非 JSON：{r.stdout[:200]}{r.stderr[:200]}"}
    out["checks"]["convergence"] = conv
    if not conv.get("pass"):
        hard_fail = True

    # ② 回哺读库
    try:
        from quantstudio.ptrade_deploy.preflight import read_vault
        vr = read_vault()
        vault = {"entries": len(vr.entries), "source": vr.source, "degraded": vr.degraded,
                 "warnings": vr.warnings[:3]}
    except Exception as exc:  # pragma: no cover
        vault = {"entries": 0, "source": "", "degraded": True, "warnings": [f"读取异常：{exc}"]}
    out["checks"]["vault"] = vault
    if vault["degraded"] or vault["entries"] < 1:
        hard_fail = True

    # ③ PD 族机检
    files = sorted(str(p.relative_to(REPO)) for p in KB_DIR.glob("PD-*.md"))
    kb = {"files": len(files), "ok": True, "detail": ""}
    if files:
        rr = subprocess.run([sys.executable, str(REPO / "scripts" / "kb_hook.py"),
                             "--files", *files, "--strict"],
                            capture_output=True, text=True, encoding="utf-8")
        kb["ok"] = rr.returncode == 0
        kb["detail"] = (rr.stdout or rr.stderr).strip().splitlines()[-1] if (rr.stdout or rr.stderr) else ""
    out["checks"]["kb_hook"] = kb
    if not kb["ok"]:
        hard_fail = True

    # ④ R1/R4 留痕
    led = LEDGER.read_text(encoding="utf-8") if LEDGER.exists() else ""
    rr45 = check_r1_r4(led)
    out["checks"]["r1_r4"] = rr45
    if not rr45["ok"]:
        warn = True

    # ⑤ L2（消费方记录制）
    if a.l2_report and a.l2_confirm and a.l2_entry:
        try:
            l2 = judge_l2(signature_from_report(a.l2_report),
                          Path(a.l2_confirm).read_text(encoding="utf-8", errors="replace"),
                          Path(a.l2_entry).read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            l2 = {"saved": False, "state": "未确认", "reason": f"材料读取失败：{exc}"}
        out["checks"]["l2"] = l2
        if l2.get("state") == "未救":
            hard_fail = True
        elif l2.get("state") == "未确认":
            warn = True
    else:
        out["checks"]["l2"] = {"saved": False, "state": "未提供材料",
                               "reason": "未传 --l2-report/--l2-confirm/--l2-entry（L2 视为未确认，WARN）"}
        warn = True

    out["exit"] = 1 if hard_fail else (2 if warn else 0)
    out["summary"] = "硬缺口" if hard_fail else ("仅 WARN" if warn else "健康")

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        c = out["checks"]
        print(f"[closedloop] ① 收敛   : {'PASS' if c['convergence'].get('pass') else 'FAIL'} — {c['convergence'].get('reason','')}")
        print(f"[closedloop] ② 回哺库 : entries={c['vault']['entries']} source={c['vault']['source'] or '-'} degraded={c['vault']['degraded']}")
        print(f"[closedloop] ③ 族机检 : {'PASS' if c['kb_hook']['ok'] else 'FAIL'} — {c['kb_hook']['detail']}")
        print(f"[closedloop] ④ R1/R4  : {'PASS' if c['r1_r4']['ok'] else 'WARN'} (r1={c['r1_r4']['r1']}, r4={c['r1_r4']['r4']})")
        print(f"[closedloop] ⑤ L2     : {c['l2']['state']} — {c['l2']['reason']}")
        print(f"[closedloop] 结论     : {out['summary']}（退出码 {out['exit']}）")
    return out["exit"]


if __name__ == "__main__":
    sys.exit(main())
