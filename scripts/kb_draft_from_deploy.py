#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""失败报告 → KB 条目起草命令（PD-0011 / 缺口 G1）。

**只生成命令与待填清单，绝不自动落盘**（②审 2026-10-28 裁定 ③：防脏条目污染族库）。

输入（三选一）：
  --report-json <path>   run_deploy(...).to_dict() 的输出（推荐）
  --report-log  <path>   转换日志文本（逐行匹配 `[P?] FAIL — ...`）
  --run <deploy.yaml>    直接跑一次 run_deploy 取报告

输出：可直接复制执行的 `py -3.11 scripts/kb_draft.py --kb knowledge/ptrade-deploy ...` 命令
      + 待人工填写的字段清单（root_cause / fixture / trigger）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def from_report_dict(d: dict) -> dict:
    stages = d.get("stages") or {}
    failed = [(k, v.get("reason", "")) for k, v in stages.items() if str(v.get("status")) == "FAIL"]
    skipped = [k for k, v in stages.items() if str(v.get("status")) == "SKIPPED"]
    return {"status": d.get("status", ""), "failed": failed, "skipped": skipped,
            "notes": [n for n in (d.get("notes") or []) if "P5" in n or "P3" in n][:6]}


def from_log_text(text: str) -> dict:
    failed = []
    for line in text.splitlines():
        m = re.match(r"\[(P\d[^\]]*)\]\s*FAIL\s*[—-]\s*(.+)", line.strip())
        if m:
            failed.append((m.group(1), m.group(2).strip()))
    notes = [l.strip() for l in text.splitlines() if "[note]" in l][:8]
    return {"status": "FAIL" if failed else "", "failed": failed, "skipped": [], "notes": notes}


def build_command(info: dict, domain: str = "D3", layer: str = "kernel") -> tuple[str, list]:
    sig = "；".join(f"{k}: {r}" for k, r in info["failed"]) or "（未解析到 FAIL 段）"
    symptom = f"qs-deploy 转换 FAIL：{sig}"[:400]
    cmd = (
        "py -3.11 scripts/kb_draft.py --kb knowledge/ptrade-deploy "
        f"--domain {domain} --layer {layer} "
        '--symptom "' + symptom.replace('"', "'") + '" '
        '--root-cause "<待填：一句话根因>" '
        '--fix-target "<待填：文件:行 或 层>" '
        '--evidence "<待填：docs/evidence/... 或 本次运行报告路径>" '
        '--fixture "<待填：tests/... 复现固件>"'
    )
    todo = ["root_cause（根因一句话，须可检索）",
            "fix_target（修复落点：文件 + 行为）",
            "evidence（证据件路径）",
            "fixture（复现固件/测试路径）",
            "trigger（最小复现触发条件，一句话可判定；族 schema 必填）"]
    return cmd, todo


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="失败报告 → KB 起草命令（不落盘）")
    ap.add_argument("--report-json", default="")
    ap.add_argument("--report-log", default="")
    ap.add_argument("--run", default="", help="deploy.yaml 路径（现场跑一次）")
    ap.add_argument("--domain", default="D3")
    ap.add_argument("--layer", default="kernel")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.report_json:
        info = from_report_dict(json.loads(Path(a.report_json).read_text(encoding="utf-8")))
    elif a.report_log:
        info = from_log_text(Path(a.report_log).read_text(encoding="utf-8", errors="replace"))
    elif a.run:
        from quantstudio.ptrade_deploy import run_deploy
        info = from_report_dict(run_deploy(a.run).to_dict())
    else:
        print("[kb_draft_from_deploy] 需提供 --report-json / --report-log / --run 之一")
        return 2

    cmd, todo = build_command(info, a.domain, a.layer)
    payload = {"parsed": info, "command": cmd, "todo": todo}

    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print("[kb_draft_from_deploy] 解析结果：")
        print(f"  status={info['status'] or '-'}  failed={len(info['failed'])}  skipped={info['skipped']}")
        for k, r in info["failed"]:
            print(f"  FAIL {k}: {r[:160]}")
        print("\n[起草命令（复制执行；不自动落盘）]\n" + cmd)
        print("\n[待人工填写]")
        for i, t in enumerate(todo, 1):
            print(f"  {i}. {t}")
    # 有 FAIL 段才算「有可沉淀项」
    return 0 if info["failed"] else 2


if __name__ == "__main__":
    sys.exit(main())
