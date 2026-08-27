"""合并基线重验双跑（2026-08-27）——当前代码态（HEAD=bb602f3）+ 当前数据态（生产库）。

与 8/26 权威基线（快照副本）的差异 = 重验对象（P-A3/B2/D2/D3 四元落地效果），不判 FAIL。
"""
import hashlib
import io
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
OUT = ROOT / "output" / "golden_baseline" / "baseline_rerun_20260827"
STRATS = [
    ("etf_theme", "quantstudio/backtest/strategies/etf_theme_rotation_quantstudio.py", []),
    ("smallcap", "quantstudio/backtest/strategies/小市值策略ptrade.py", []),
    ("overnight_s7", "quantstudio/backtest/strategies/smallcap_overnight_scalp_7_quantstudio.py",
     ["--profile", "minute-bar-v1"]),
]
WINDOW = ["2026-07-01", "2026-07-31"]
REF = {"etf_theme": "07cfabc923c6", "smallcap": "a11cdbad0d2f",
       "overnight_s7": "2e29a9532f1f"}


def run_once(tag, strat, extra):
    cmd = [sys.executable, "-m", "quantstudio.backtest.run_ptrade_strategy",
           strat, *WINDOW, *extra]
    log = OUT / f"{tag}.log"
    with io.open(log, "w", encoding="utf-8") as f:
        return subprocess.call(cmd, cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT)


def last_output_dir(hint):
    base = ROOT / "output" / "backtest_results"
    dirs = [d for d in base.iterdir() if d.is_dir() and d.name.endswith(hint)]
    dirs.sort(key=lambda d: d.stat().st_mtime)
    return dirs[-1] if dirs else None


def dir_hash(d):
    h = hashlib.sha256()
    for p in sorted(q for q in d.rglob("*") if q.is_file()):
        h.update(str(p.relative_to(d)).encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"launched_at": datetime.now().isoformat(), "head": "bb602f3",
              "db": "生产库（当前数据态：eps 回补+D3 首日修复后）",
              "window": WINDOW, "runs": [], "compare": {}}
    for name, strat, extra in STRATS:
        hint = Path(strat).stem
        hashes = []
        for i in (1, 2):
            before = last_output_dir(hint)
            rc = run_once(f"{name}_r{i}", strat, extra)
            after = last_output_dir(hint)
            od = after if after != before else None
            digest = dir_hash(od) if od else None
            report["runs"].append({"strategy": name, "round": i, "rc": rc,
                                   "output_dir": od.name if od else None,
                                   "content_sha256": digest})
            hashes.append(digest)
            print(f"{name} r{i}: rc={rc} dir={od and od.name} hash={digest and digest[:12]}",
                  flush=True)
        report["compare"][name] = {
            "self_consistent": hashes[0] == hashes[1] and hashes[0] is not None,
            "r1": hashes[0], "r2": hashes[1],
            "ref_0826": REF[name],
            "diff_vs_0826": (hashes[0] != REF[name]) if hashes[0] else None}
    ok = all(v["self_consistent"] for v in report["compare"].values())
    report["verdict"] = "SELF-CONSISTENT PASS（双跑一致；与 8/26 差异待归因，不判 FAIL）" if ok \
        else "SELF-CONSISTENT FAIL"
    io.open(OUT / "rerun_report.json", "w", encoding="utf-8").write(
        json.dumps(report, ensure_ascii=False, indent=2))
    print("RERUN", report["verdict"])


if __name__ == "__main__":
    main()
