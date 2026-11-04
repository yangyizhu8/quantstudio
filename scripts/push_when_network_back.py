# -*- coding: utf-8 -*-
"""网络恢复即自动重推（GitHub 不可达期间的出仓等待）。

纪律：
- 只做 **git push**（远端写入），不杀进程、不改网络配置、不重启代理；
- 到达重试上限仍失败 ⇒ 退出并留下明确日志（由人决定是否继续等）。

用法：python scripts/push_when_network_back.py --range origin/main..HEAD
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "logs" / "push_retry.log"


def _log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    except Exception:   # noqa: BLE001  日志失败不得影响推送
        pass


def git(*args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def network_ok() -> bool:
    rc, out = git("ls-remote", "origin", "refs/heads/main")
    if rc == 0:
        return True
    _log(f"网络仍不通：{out.strip().splitlines()[-1] if out.strip() else ''}")
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--range", default="origin/main..HEAD")
    ap.add_argument("--interval", type=int, default=120, help="重试间隔秒")
    ap.add_argument("--max-tries", type=int, default=60, help="最大尝试次数")
    args = ap.parse_args(argv)

    for i in range(1, args.max_tries + 1):
        _log(f"第 {i}/{args.max_tries} 次尝试（间隔 {args.interval}s）")
        if not network_ok():
            if i == args.max_tries:
                _log("达到最大尝试次数，网络仍未恢复 —— 退出，等待人工处置")
                return 2
            time.sleep(args.interval)
            continue
        # 范围声明：锚=范围首笔，其余全量声明（C7 全量显式声明制）
        rc, out = git("log", "--format=%h", args.range)
        shas = [l for l in out.splitlines() if l.strip()]
        if not shas:
            _log("范围内无提交，无需推送")
            return 0
        anchor, rest = shas[0], shas[1:]
        import os
        env = dict(os.environ)
        env["QS_PUSH_BUNDLE_ALLOWALSO"] = ",".join(rest)
        p = subprocess.run(["git", "push", "origin", "main:main"], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", env=env)
        _log(f"push exit={p.returncode}")
        for ln in (p.stdout or "").splitlines()[-4:]:
            _log("  " + ln)
        if p.returncode == 0:
            _, sha = git("rev-parse", "--short", "HEAD")
            _log(f"推送成功：origin/main ← {sha.strip()}")
            return 0
        time.sleep(args.interval)
    _log("达到最大尝试次数 —— 退出")
    return 2


if __name__ == "__main__":
    sys.exit(main())
