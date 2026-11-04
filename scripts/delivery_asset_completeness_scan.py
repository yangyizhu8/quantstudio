# -*- coding: utf-8 -*-
"""出仓前例行扫描：已入库文件引用了**未入库**的本地资产（DB-2026-0026）。

背景：2026-11-04 实测——`tests/test_delivery_pure_gain.py` 动态加载
`scripts/delivery_wave_gate.py`，而该脚本从未入库 ⇒ 副本/新克隆跑共享层回归必挂 3 例
（与 11-01「远端 delivery 族 15 测试必挂修复 b」同类）。

用法：
    python scripts/delivery_asset_completeness_scan.py            # 退出码 0=无缺口，1=有缺口
    python scripts/delivery_asset_completeness_scan.py --scope delivery
    python scripts/delivery_asset_completeness_scan.py --write-baseline <json>   # 记录白名单

白名单（声明式"本地不推送"资产）写在同目录 *_local_only.txt，一行一个路径。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WHITELIST_FILE = Path(__file__).with_name("delivery_local_only_assets.txt")

# 扫描排除项（避免自指与草稿区噪声）：
# ① 扫描器自身与它的自测文件——里面含 `_load_script("smoke")` 这类**示例字样**，不是依赖；
# ② agent_workspace/ 是本地草稿区（非交付资产，其内部互引不构成完整性缺口）。
EXCLUDE_FILES = {
    "scripts/delivery_asset_completeness_scan.py",
    "tests/test_delivery_asset_completeness_scan.py",
}
EXCLUDE_PREFIXES = (
    "agent_workspace/",          # 本地草稿区
    "docs/handoff/backup-",      # 交接备份副本（备份件被字面引用，不算交付资产）
    "data/backup/",              # 数据备份副本
    "docs/backup-",
)

SCOPES = {
    # scope → (入库文件匹配前缀, 允许被引用的资产目录)
    "delivery": (("tests/test_delivery_", "quantstudio/delivery/", "scripts/delivery_"), ()),
    "all": ((), ()),
}


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8").stdout


def _load_whitelist() -> set[str]:
    if not WHITELIST_FILE.exists():
        return set()
    return {ln.strip().replace("\\", "/") for ln in WHITELIST_FILE.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")}


def scan(scope: str = "delivery") -> dict:
    import time as _time
    _t0 = _time.monotonic()
    tracked = {l.strip() for l in git("ls-files").splitlines() if l.strip()}
    # 未跟踪集用 `git ls-files --others`（比 `git status --porcelain` 快得多：
    # 后者要遍历整个工作树，在含大 data/ 的仓里是分钟级开销）
    untracked = {l.strip().replace("\\", "/")
                 for l in git("ls-files", "--others", "--exclude-standard").splitlines()
                 if l.strip()}
    whitelist = _load_whitelist()
    untracked = {p for p in untracked
                 if p not in whitelist
                 and p not in EXCLUDE_FILES
                 and not p.startswith(EXCLUDE_PREFIXES)}

    prefixes, _ = SCOPES.get(scope, SCOPES["delivery"])
    if scope == "all":
        srcs = sorted(p for p in tracked if p.endswith(".py"))
    else:
        srcs = sorted(p for p in tracked if p.endswith(".py") and p.startswith(prefixes))
    srcs = [p for p in srcs
            if p not in EXCLUDE_FILES and not p.startswith(EXCLUDE_PREFIXES)]

    # 引用形态判据（避免"词面误命中"：普通词出现不算引用）
    #   ① 路径式：.../<stem>.py   ② _load_script("<stem>") ③ importlib.import_module("<stem>")
    #   ④ import <stem>   ⑤ from <stem> import ...
    gaps: dict[str, set[str]] = {}
    for rel in srcs:
        try:
            txt = (ROOT / rel).read_text(encoding="utf-8")
        except Exception:
            continue
        for cand in sorted(untracked):
            if not cand.endswith(".py"):
                continue
            stem = Path(cand).stem
            if not stem:
                continue
            if stem not in txt:      # 预筛：词面都不出现 ⇒ 必非引用
                continue
            pats = [
                r"[\w./\\-]*" + re.escape(stem) + r"\.py",
                r"_load_script\(\s*[\"\']" + re.escape(stem) + r"[\"\']",
                r"import_module\(\s*[\"\']" + re.escape(stem) + r"[\"\']",
                r"^\s*import\s+" + re.escape(stem) + r"\b",
                r"^\s*from\s+" + re.escape(stem) + r"\s+import\b",
            ]
            if any(re.search(pp, txt, re.M) for pp in pats):
                gaps.setdefault(cand, set()).add(rel)

    return {
        "scope": scope,
        "scanned_files": len(srcs),
        "elapsed_s": round(_time.monotonic() - _t0, 2),
        "whitelist": sorted(whitelist),
        "gaps": {k: sorted(v) for k, v in sorted(gaps.items())},
        "ok": not gaps,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="出仓前资产完整性扫描")
    ap.add_argument("--scope", default="delivery", choices=sorted(SCOPES))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-baseline", default="", help="把本次结果写入 JSON（留痕用）")
    args = ap.parse_args(argv)

    report = scan(args.scope)
    if args.write_baseline:
        out = Path(args.write_baseline)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[资产完整性扫描] scope={report['scope']} 扫描 {report['scanned_files']} 个入库文件"
              f"（耗时 {report['elapsed_s']:.1f}s）")
        if report["whitelist"]:
            print(f"  白名单（声明式本地不推送）：{len(report['whitelist'])} 项")
        if report["ok"]:
            print("  ✅ 无缺口：已入库文件未引用未入库资产")
        else:
            print(f"  ❌ 缺口 {len(report['gaps'])} 个：")
            for k, srcs in report["gaps"].items():
                print(f"     {k}")
                for s in srcs:
                    print(f"        ← {s}")
            print("  处置：把资产精确入库（勿用 git add -A），或登记进白名单并说明理由。")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
