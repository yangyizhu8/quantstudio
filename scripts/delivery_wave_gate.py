#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""波次门禁（plan「实施波次与门控」）：机检 evidence 文件的必填读数是否齐全。

用法：
    python scripts/delivery_wave_gate.py --wave 1
    python scripts/delivery_wave_gate.py --wave 1 --file docs/evidence/delivery-wave1-20261031.md

判定（缺一项即非 0 退出，阻断下一波）：
- 该波 evidence 文件存在（`docs/evidence/delivery-waveN-*.md`，取最新一份）
- 每个必填键在文件内出现，且**冒号后有非空内容**
- 不含"待补/TODO/待定"之类占位词

注意：本脚本只证明「读数齐全」，不证明「测试真的跑过」——每份 evidence 必须附
命令原文 + 尾部原始输出片段，供人工核（gate 不做真伪判定，避免假门）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence"

# 每波必填键（与 tasks.md 的 DoD 对应）
WAVE_REQUIRED: Dict[int, List[str]] = {
    0: ["测试:", "SC-013:", "基线快照:", "测试基线:", "fixture 状态:"],
    1: ["测试:", "SC-001:", "SC-002:", "SC-003:", "SC-012:", "SC-013:"],
    2: ["测试:", "SC-005:", "SC-013:"],
    3: [
        "测试:",
        "SC-004:",
        "SC-006:",
        "SC-007:",
        "SC-011:",
        "SC-014:",
        "SC-015:",
        "SC-016:",
        "SC-017:",
        "SC-013:",
    ],
    4: ["测试:", "SC-009:", "SC-010:", "CLI smoke:", "SC-013:"],
}

PLACEHOLDERS = ("待补", "TODO", "待定", "TBD", "xxx")


def latest_evidence(wave: int) -> Optional[Path]:
    matches = sorted(EVIDENCE_DIR.glob(f"delivery-wave{wave}-*.md"))
    return matches[-1] if matches else None


def check_wave(wave: int, text: str) -> List[str]:
    """返回问题清单（空 = 通过）。纯函数，便于单测。"""
    problems: List[str] = []
    required = WAVE_REQUIRED.get(wave)
    if required is None:
        return [f"未定义的波次：{wave}（已定义 {sorted(WAVE_REQUIRED)}）"]
    for key in required:
        idx = text.find(key)
        if idx < 0:
            problems.append(f"缺必填读数：{key}")
            continue
        tail = text[idx + len(key):].splitlines()[0].strip() if text[idx + len(key):] else ""
        if not tail:
            problems.append(f"读数为空：{key}")
            continue
        if any(p in tail for p in PLACEHOLDERS):
            problems.append(f"读数是占位词（{tail[:24]}）：{key}")
    return problems


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="交付特性波次门禁")
    ap.add_argument("--wave", type=int, required=True)
    ap.add_argument("--file", help="显式指定 evidence 文件（默认取该波最新）")
    args = ap.parse_args(argv)

    path = Path(args.file) if args.file else latest_evidence(args.wave)
    if path is None or not Path(path).exists():
        print(f"[wave-gate] 未找到 W{args.wave} 的 evidence 文件（docs/evidence/delivery-wave{args.wave}-*.md）")
        return 1
    text = Path(path).read_text(encoding="utf-8")
    problems = check_wave(args.wave, text)
    if problems:
        print(f"[wave-gate] W{args.wave} 未过（{path.name}）：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print(f"[wave-gate] W{args.wave} PASS（{path.name}）：必填读数齐全")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
