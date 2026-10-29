# -*- coding: utf-8 -*-
"""三判定面一致性 + 声明面核对机检（T020 / T021）。

三个判定面：
  ① 判定源单文件直调  `python quantstudio/pipeline/duckdb_version_policy.py --check <v>`
  ② 门禁模块入口      `python -m quantstudio.pipeline.duckdb_version_gate --check <v>`
  ③ 进程内判定        `duckdb_version_policy.is_allowed(v)`

声明面：`requirements.txt:1` 与 `pyproject.toml:20/:50` 必须是 `duckdb==1.5.6`
（单版本精确锁定），且注释载明放行集合 ⇒ 声明面**落于集合内**。

用法：python docs/evidence/gate_matrix_check.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from quantstudio.pipeline import duckdb_version_policy as policy  # noqa: E402

POINTS = ["1.4.5", "1.4.9", "1.5.6", "1.4.4", "1.5.0", "1.5.5", "1.6.0", "2.0.1", "2.1"]
EXPECT = {"1.4.5": True, "1.4.9": True, "1.5.6": True, "1.4.4": False, "1.5.0": False,
          "1.5.5": False, "1.6.0": False, "2.0.1": False, "2.1": False}


def _cli(cmd: list[str]):
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO))
    return p.returncode, (p.stdout or "").strip().splitlines()


def main() -> int:
    failures = []
    print("== 三判定面一致性 ==")
    for ver in POINTS:
        want = EXPECT[ver]
        rc1, out1 = _cli([sys.executable, str(REPO / "quantstudio/pipeline/duckdb_version_policy.py"),
                          "--check", ver])
        rc2, out2 = _cli([sys.executable, "-m", "quantstudio.pipeline.duckdb_version_gate",
                          "--check", ver])
        inproc = policy.is_allowed(ver)
        rc_expect = 0 if want else 1
        ok = (rc1 == rc_expect and rc2 == rc_expect and inproc == want)
        print(f"  {ver:<7} 单文件 rc={rc1} {out1[0] if out1 else '':<18} "
              f"模块 rc={rc2} {out2[0] if out2 else '':<18} 进程内={inproc}  {'OK' if ok else 'MISMATCH'}")
        if not ok:
            failures.append(f"{ver}: 三面不一致（期望 allowed={want}）")

    print("== 声明面核对 ==")
    req = (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines()
    if not req or req[0].strip() != "duckdb==1.5.6":
        failures.append(f"requirements.txt:1 不是 duckdb==1.5.6（实得 {req[0] if req else '<空>'}）")
    else:
        print(f"  requirements.txt:1 = {req[0].strip()}  OK")
    if not any("放行集合" in line for line in req[1:]):
        failures.append("requirements.txt 注释未载明放行集合")
    py = (REPO / "pyproject.toml").read_text(encoding="utf-8").splitlines()
    hits = [i + 1 for i, line in enumerate(py) if '"duckdb==1.5.6"' in line]
    if len(hits) != 2:
        failures.append(f"pyproject.toml 的 duckdb pin 命中数 = {len(hits)}（期望 2）")
    else:
        print(f"  pyproject.toml pin 行号 = {hits}  OK")
    for line in py:
        if '"duckdb==1.5.6"' in line and "放行集合" not in line:
            failures.append("pyproject.toml 的 pin 注释未载明放行集合")
            break

    print("== 单源同一性 ==")
    from quantstudio.pipeline import duckdb_version_gate as gate
    same = (gate.ALLOWED_RANGES is policy.ALLOWED_RANGES
            and gate.is_allowed is policy.is_allowed
            and gate.parse_version is policy.parse_version)
    print(f"  gate 与 policy 共用同一对象 = {same}")
    if not same:
        failures.append("门禁模块与判定源未共用同一对象（存在第二份规则）")

    print()
    if failures:
        print(f"FAIL: {len(failures)}")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS: 三判定面逐项一致 + 声明面落于集合内 + 单源同一性成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
