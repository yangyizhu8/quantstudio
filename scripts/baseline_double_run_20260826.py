"""黄金基线双跑（第 4 步）——SNAP_003 快照副本上三策略 ×2 逐字节比对。

依据：docs/governance-snapshot-design.md §109（db_path 指向快照副本 + bind 入档）；
总调度 2026-08-26 04:2x 发车指令（方案 2 强化版）。
三策略定案：docs/governance-step1-callchain.md §4。
窗口：2026-07-01 ~ 2026-07-31（审计第 11 轮基线区间）。
"""
import hashlib
import io
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
SNAP = ROOT / "data" / "snapshots" / "SNAP_20260825_003_81260e83"
OUT = ROOT / "output" / "golden_baseline" / "baseline_runs_20260826"
STRATS = [
    ("etf_theme", "quantstudio/backtest/strategies/etf_theme_rotation_quantstudio.py", []),
    ("smallcap", "quantstudio/backtest/strategies/小市值策略ptrade.py", []),
    ("overnight_s7", "quantstudio/backtest/strategies/smallcap_overnight_scalp_7_quantstudio.py",
     ["--profile", "minute-bar-v1"]),
]
WINDOW = ["2026-07-01", "2026-07-31"]


def run_once(tag, strat, extra):
    env = dict(os.environ)
    env["QUANTSTUDIO_DATA_ROOT"] = str(SNAP)
    cmd = [sys.executable, "-m", "quantstudio.backtest.run_ptrade_strategy",
           strat, *WINDOW, *extra]
    log = OUT / f"{tag}.log"
    with io.open(log, "w", encoding="utf-8") as f:
        rc = subprocess.call(cmd, cwd=str(ROOT), env=env, stdout=f,
                             stderr=subprocess.STDOUT)
    return rc


def last_output_dir(strategy_hint: str = ""):
    """取最新回测产物目录；带策略提示时按目录名后缀过滤（防并行会话混淆）。"""
    base = ROOT / "output" / "backtest_results"
    dirs = [d for d in base.iterdir() if d.is_dir()
            and (not strategy_hint or d.name.endswith(strategy_hint))]
    dirs.sort(key=lambda d: d.stat().st_mtime)
    return dirs[-1] if dirs else None


def dir_hash(d: Path):
    """目录内容逻辑 hash（相对路径+文件字节，排除时间戳文件名本身）。"""
    h = hashlib.sha256()
    files = sorted(p for p in d.rglob("*") if p.is_file())
    for p in files:
        h.update(str(p.relative_to(d)).encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest(), [(str(p.relative_to(d)), p.stat().st_size) for p in files]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"launched_at": datetime.now().isoformat(),
              "snapshot": "SNAP_20260825_003_81260e83",
              "window": WINDOW, "runs": [], "compare": {}}
    for name, strat, extra in STRATS:
        hint = Path(strat).stem
        hashes = []
        for i in (1, 2):
            before = last_output_dir(hint)
            rc = run_once(f"{name}_r{i}", strat, extra)
            after = last_output_dir(hint)
            od = after if (after and after != before) else None
            digest, listing = (dir_hash(od) if od else (None, None))
            report["runs"].append({"strategy": name, "round": i, "rc": rc,
                                   "output_dir": str(od) if od else None,
                                   "content_sha256": digest})
            hashes.append(digest)
            print(f"{name} r{i}: rc={rc} dir={od} hash={digest and digest[:12]}", flush=True)
        report["compare"][name] = {
            "identical": hashes[0] == hashes[1] and hashes[0] is not None,
            "r1": hashes[0], "r2": hashes[1]}
    io.open(OUT / "baseline_double_run_report.json", "w", encoding="utf-8").write(
        json.dumps(report, ensure_ascii=False, indent=2))
    allpass = all(v["identical"] for v in report["compare"].values())
    print("BASELINE", "PASS" if allpass else "FAIL")


if __name__ == "__main__":
    main()
