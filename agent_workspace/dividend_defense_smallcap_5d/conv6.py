# -*- coding: utf-8 -*-
"""B2 验收 4：6 策略转换产物 SHA-256 对照（同一工作树、同一环境，仅代码版本不同）。"""
import hashlib, json, os, subprocess, sys
ROOT = os.path.abspath(sys.argv[1]); OUT = os.path.abspath(sys.argv[2])
STRATS = ["CANSLIM突破成长选股策略.py", "fall_reversal_quantstudio.py",
          "tech_etf_mvo_rotation_quantstudio.py", "vol_regime_mom_rev_quantstudio.py",
          "weekly_smallcap_growth_momentum_10_quantstudio.py", "周频小市值成长动量（三层止损）.py"]
DB = os.environ["QUANTSTUDIO_DB"]
res = {}
for s in STRATS:
    src = os.path.join(ROOT, "quantstudio", "backtest", "strategies", s)
    d = os.path.join(OUT, os.path.splitext(s)[0])
    cmd = [sys.executable, "-m", "quantstudio.strategy_compiler.cli", "import", src,
           "--out", d, "--no-smoke", "--engine-profile", "daily-bar-v1",
           "--db-path", DB, "--etf-pool-start-date", "2020-01-01"]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    files = {}
    for root, _, fs in os.walk(d):
        for f in sorted(fs):
            fp = os.path.join(root, f)
            files[os.path.relpath(fp, d).replace("\\", "/")] = hashlib.sha256(open(fp, "rb").read()).hexdigest()[:24]
    res[s] = {"exit": p.returncode, "files": files}
    print("  %-46s exit=%d files=%d" % (s, p.returncode, len(files)))
json.dump(res, open(os.path.join(OUT, "hashes.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("written:", os.path.join(OUT, "hashes.json"))
