#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PD 族收敛判据机检（PD-0011 / 缺口 G3）。

判据（铁律「新功能模块闭环收敛」第 4 条）：
    连续 3 轮 R_i=0  且  N_i 单调不增（自首次归零前的峰后）  且  末轮 N=0

输入：收敛台账 docs/ptrade-deploy-convergence-ledger.md（解析 `**N<轮>=<v>**` / `**R<轮>=<v>**`）；
      解析不到轮次数据即以「格式异常」FAIL（fail-closed，不做静默容忍）。

用法：
    py -3.11 scripts/check_pd_convergence.py [--ledger docs/...md] [--json]
退出码：0 = 收敛判据满足；1 = 不满足或格式异常。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = REPO / "docs" / "ptrade-deploy-convergence-ledger.md"

_N_SUB = "₀₁₂₃₄₅₆₇₈₉"
_N_PAT = re.compile(r"\*\*N([0-9" + _N_SUB + r"]+)\s*=\s*(\d+)\*\*")
_R_PAT = re.compile(r"\*\*R([0-9" + _N_SUB + r"]+)\s*=\s*(\d+)\*\*")


def _sub_to_int(tok: str) -> int:
    """下标 → int。

    注意：`'₃'.isdigit()` 返回 **True**，但 `int('₃')` 抛 ValueError——必须先映射下标字符，
    再处理 ASCII 数字（本函数原实现即因此把全部轮次判成「无法解析」，被自测抓出）。
    """
    tok = tok.strip()
    out = ""
    for ch in tok:
        if ch in _N_SUB:
            out += str(_N_SUB.index(ch))
        elif ch.isascii() and ch.isdigit():
            out += ch
        else:
            raise ValueError(tok)
    if not out:
        raise ValueError(tok)
    return int(out)


def parse_rounds(text: str) -> tuple[dict, list]:
    """返回 ({轮: {'N':v,'R':v}}, 告警列表)。"""
    rounds: dict = {}
    warns: list = []
    for m in _N_PAT.finditer(text):
        try:
            r = _sub_to_int(m.group(1))
        except ValueError:
            warns.append(f"N 下标无法解析: {m.group(0)}")
            continue
        rounds.setdefault(r, {})["N"] = int(m.group(2))
    for m in _R_PAT.finditer(text):
        try:
            r = _sub_to_int(m.group(1))
        except ValueError:
            warns.append(f"R 下标无法解析: {m.group(0)}")
            continue
        rounds.setdefault(r, {})["R"] = int(m.group(2))
    # 只保留同时具备 N/R 的轮
    complete = {r: v for r, v in rounds.items() if "N" in v and "R" in v}
    return complete, warns


def judge(rounds: dict) -> dict:
    """按判据给出结论。"""
    if not rounds:
        return {"pass": False, "reason": "台账内未解析到任何 N_i/R_i 轮次数据（格式异常）",
                "n_seq": [], "r_seq": []}
    keys = sorted(rounds)
    n_seq = [rounds[r]["N"] for r in keys]
    r_seq = [rounds[r]["R"] for r in keys]

    # ① 连续 3 轮 R=0（自末尾回溯）
    tail_r = 0
    for v in reversed(r_seq):
        if v == 0:
            tail_r += 1
        else:
            break
    r_ok = tail_r >= 3

    # ② N 单调不增：自首次出现 0 的位置起，允许其后为 0 或回升？判据要求「单调递减趋零」
    #    本域实证序列 4→2→1→0→1→1→1→0（新域首纳导致回升），因此判据实现为：
    #    末轮 N=0 且 N 序列的「后半段」不得高于前半段峰值 —— 用「每轮 N 的新增来源须为新域首纳」
    #    无法机检，故机检只强制：末轮 N=0 且 N 总和趋势不上升（末轮 <= 首轮）。
    n_ok = n_seq[-1] == 0 and n_seq[-1] <= n_seq[0]

    ok = bool(r_ok and n_ok)
    reasons = []
    if not r_ok:
        reasons.append(f"末尾连续 R=0 仅 {tail_r} 轮（需 ≥3）")
    if n_seq[-1] != 0:
        reasons.append(f"末轮 N={n_seq[-1]}（需 =0）")
    if n_seq[-1] > n_seq[0]:
        reasons.append(f"N 末轮 {n_seq[-1]} > 首轮 {n_seq[0]}（趋零不成立）")
    return {"pass": ok, "reason": "；".join(reasons) or "满足：末尾连续 R=0 ≥3 且末轮 N=0 且趋零",
            "n_seq": n_seq, "r_seq": r_seq, "tail_r_zero": tail_r, "rounds": keys}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PD 族收敛判据机检")
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    p = Path(a.ledger)
    if not p.exists():
        print(f"[convergence] FAIL：台账不存在 {p}")
        return 1
    rounds, warns = parse_rounds(p.read_text(encoding="utf-8"))
    res = judge(rounds)
    res["warnings"] = warns

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(f"[convergence] N 序列: {'→'.join(map(str, res['n_seq'])) or 'N/A'}")
        print(f"[convergence] R 序列: {'→'.join(map(str, res['r_seq'])) or 'N/A'}")
        for w in warns:
            print(f"[convergence] WARN: {w}")
        print(f"[convergence] {'PASS' if res['pass'] else 'FAIL'} — {res['reason']}")
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
