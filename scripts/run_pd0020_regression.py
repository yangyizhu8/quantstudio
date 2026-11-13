# -*- coding: utf-8 -*-
"""PD-2026-0020 期1：全库回归原始日志采集（不做归属判定，只取证）。

产出（全部落 docs/evidence/pd0020-full-regression-20261113/）：
  env.json        命令/解释器/依赖/数据根/git 前后指纹/起止时间
  pytest-full.log 完整 stdout+stderr 原文
  junit.xml       结构化结果（含每个失败 nodeid + traceback）
  nodeids.txt     失败/错误 nodeid 清单
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import platform
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVID = ROOT / "docs" / "evidence" / "pd0020-full-regression-20261113"


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace").stdout.strip()
    except Exception as e:
        return "ERR:%s" % e


def main() -> int:
    EVID.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pytest", "tests", "-q",
           "--junitxml=" + str(EVID / "junit.xml")]
    started = dt.datetime.now().isoformat(timespec="seconds")
    env = {
        "started_at": started,
        "cwd": str(ROOT),
        "command": " ".join(cmd),
        "invoked_via": "subprocess.run from scripts/run_pd0020_regression.py",
        "python": sys.version,
        "python_exe": sys.executable,
        "platform": platform.platform(),
        "env": {k: os.environ.get(k, "") for k in
                ("PYTHONIOENCODING", "PYTHONHASHSEED", "DSH_HOME", "STAGED_SCOPE_ALLOW")},
        "git_head_before": git("rev-parse", "HEAD"),
        "git_status_before_count": len([l for l in git("status", "--porcelain").split("\n") if l.strip()]),
        "deps": {},
    }
    for name in ("duckdb", "pandas", "numpy", "pytest"):
        try:
            m = __import__(name); env["deps"][name] = getattr(m, "__version__", "?")
        except Exception:
            env["deps"][name] = "NOT-INSTALLED"
    # 数据根（若存在）
    for cand in ("data/quantstudio.db", "data"):
        p = ROOT / cand
        env["data_root_" + cand.replace("/", "_")] = (str(p) + (" EXISTS" if p.exists() else " ABSENT"))

    print("  启动:", started)
    print("  命令:", env["command"])
    print("  git HEAD(before):", env["git_head_before"][:16])

    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    finished = dt.datetime.now().isoformat(timespec="seconds")
    (EVID / "pytest-full.log").write_text(
        "$ " + env["command"] + "\n\n" + (r.stdout or "") + "\n--- STDERR ---\n" + (r.stderr or ""),
        encoding="utf-8", newline="\n")

    env["finished_at"] = finished
    env["duration_s"] = (dt.datetime.fromisoformat(finished)
                         - dt.datetime.fromisoformat(started)).total_seconds()
    env["returncode"] = r.returncode
    env["git_head_after"] = git("rev-parse", "HEAD")
    env["git_status_after_count"] = len([l for l in git("status", "--porcelain").split("\n") if l.strip()])
    env["head_changed_during_run"] = env["git_head_before"] != env["git_head_after"]

    # 从 junit.xml 抽 nodeid
    import xml.etree.ElementTree as ET
    nodeids = []
    jx = EVID / "junit.xml"
    if jx.is_file():
        tree = ET.parse(str(jx))
        for tc in tree.iter("testcase"):
            bad = [c for c in tc if c.tag in ("failure", "error")]
            if bad:
                cls = tc.get("classname", ""); nm = tc.get("name", "")
                kind = bad[0].tag
                nodeids.append("%s\t%s\t%s" % (kind, cls, nm))
    (EVID / "nodeids.txt").write_text("\n".join(nodeids) + "\n", encoding="utf-8", newline="\n")
    env["failed_or_error_count"] = len(nodeids)

    (EVID / "env.json").write_text(json.dumps(env, ensure_ascii=False, indent=2),
                                   encoding="utf-8", newline="\n")
    print("  结束:", finished, " 用时 %.0fs" % env["duration_s"])
    print("  rc=%d  失败/错误 nodeid=%d" % (r.returncode, len(nodeids)))
    print("  HEAD 在跑期间变化:", env["head_changed_during_run"])
    print("  日志:", (EVID / "pytest-full.log").relative_to(ROOT))
    print("  junit:", (EVID / "junit.xml").relative_to(ROOT))
    print("  nodeids:", (EVID / "nodeids.txt").relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
