# -*- coding: utf-8 -*-
"""版本门三入口替身触发自检（T016 / T017 / T019）。

说明：真实进程触发（真起 GUI / daemon / GUI 子进程）须在**停服窗内**执行
（会打断在跑的采集进程）。本脚本做的是**替身触发**：以子进程 + 伪造 duckdb 模块
驱动**同一个门禁函数** `require_duckdb_version(component)`，验证：

  - 三个入口形态（GUI / daemon / GUI 拉起子进程）正反向判定一致；
  - 拒启退出码恒为 3，文案含三类出路（升级 / 换解释器 / 显式放行）；
  - 逃生阀 `QS_DUCKDB_VERSION_GATE=0` 放行且留醒目告警。

用法：python docs/evidence/version_gate_entrypoint_check.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

ENTRYPOINTS = [
    "GUI (main_gui.py)",
    "daemon (quantstudio.pipeline.daemon)",
    "GUI subprocess (main_gui.py -> daemon child)",
]

ALLOW = ["1.4.5", "1.4.9", "1.5.6"]
DENY = ["1.4.4", "1.5.0", "1.5.5", "1.6.0", "2.0.1", "2.1"]

CHILD = (
    "import logging, sys\n"
    "logging.basicConfig(level=logging.DEBUG, stream=sys.stderr,\n"
    "                    format='%(levelname)s %(message)s')\n"
    "from quantstudio.pipeline.duckdb_version_gate import require_duckdb_version\n"
    "require_duckdb_version(sys.argv[1])\n"
    "print('GATE_PASSED')\n"
)


def _shim(root: Path, version: str) -> Path:
    d = root / ("shim_" + version.replace(".", "_"))
    (d / "duckdb").mkdir(parents=True, exist_ok=True)
    (d / "duckdb" / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    return d


def _run(component: str, version: str, root: Path, escape: bool = False):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_shim(root, version)) + os.pathsep + str(REPO)
    env.pop("QS_DUCKDB_VERSION_GATE", None)
    if escape:
        env["QS_DUCKDB_VERSION_GATE"] = "0"
    p = subprocess.run([sys.executable, "-c", CHILD, component],
                       capture_output=True, text=True, env=env, cwd=str(REPO))
    return p


def main() -> int:
    failures = []
    checked = 0
    with tempfile.TemporaryDirectory(prefix="qs_gate_entry_") as td:
        root = Path(td)
        for comp in ENTRYPOINTS:
            for ver in ALLOW:
                p = _run(comp, ver, root)
                checked += 1
                if p.returncode != 0 or "GATE_PASSED" not in p.stdout:
                    failures.append(f"{comp} @ {ver}: 期望放行(rc=0)，实得 rc={p.returncode} "
                                    f"stderr={p.stderr[-200:]}")
            for ver in DENY:
                p = _run(comp, ver, root)
                checked += 1
                if p.returncode != 3:
                    failures.append(f"{comp} @ {ver}: 期望拒启(rc=3)，实得 rc={p.returncode}")
                if "拒绝启动" not in p.stderr:
                    failures.append(f"{comp} @ {ver}: 缺少「拒绝启动」文案")
                for hint in ("升级到目标版本", "改用已合规的解释器", "临时放行"):
                    if hint not in p.stderr:
                        failures.append(f"{comp} @ {ver}: 拒启文案缺少出路「{hint}」")
            # 逃生阀：集合外版本 + 显式放行 ⇒ 放行且留告警
            p = _run(comp, "1.5.5", root, escape=True)
            checked += 1
            if p.returncode != 0 or "GATE_PASSED" not in p.stdout:
                failures.append(f"{comp} @ 1.5.5 + 逃生阀: 期望放行(rc=0)，实得 rc={p.returncode}")
            if "显式放行" not in p.stderr:
                failures.append(f"{comp} @ 1.5.5 + 逃生阀: 缺少醒目告警")

    print(f"检查组合数 = {checked}（3 入口 × {len(ALLOW)} 放行 + {len(DENY)} 拒绝 + 逃生阀）")
    if failures:
        print(f"FAIL: {len(failures)}")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS: 三入口正反向 + 拒启退出码 + 三出路文案 + 逃生阀告警 全部符合")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
