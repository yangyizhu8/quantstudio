# -*- coding: utf-8 -*-
"""PD-2026-0020 期1：冻结（输入/代码/配置/依赖）＋ 六策略第一跳重生 ＋ 产物 hash 登记。

纪律：
 - **不继承历史产物 PASS**：六策略逐个 `qs-compile import` 重生，产物 hash 取**重生后**的值；
 - 若某策略第一跳产物因 `source_import.py` 改动而过期，本脚本的重生即为修复动作；
 - api_portability 以**重生后** run_card 为准，历史 run_card 一律不采信。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import platform
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT_DATE = "20261113"
EVID = ROOT / "docs" / "evidence" / ("pd0020-freeze-" + OUT_DATE)

STRATEGIES = [
    ("CANSLIM突破成长选股策略", "quantstudio/backtest/strategies/CANSLIM突破成长选股策略.py"),
    ("fall_reversal", "quantstudio/backtest/strategies/fall_reversal_quantstudio.py"),
    ("tech_etf_mvo_rotation", "quantstudio/backtest/strategies/tech_etf_mvo_rotation_quantstudio.py"),
    ("vol_regime_mom_rev", "quantstudio/backtest/strategies/vol_regime_mom_rev_quantstudio.py"),
    ("weekly_smallcap_growth_momentum_10", "quantstudio/backtest/strategies/weekly_smallcap_growth_momentum_10_quantstudio.py"),
    ("周频小市值成长动量（三层止损）", "quantstudio/backtest/strategies/周频小市值成长动量（三层止损）.py"),
]

CODE = [
    "quantstudio/strategy_compiler/source_import.py",
    "quantstudio/strategy_compiler/portability_rules.py",
    "quantstudio/strategy_compiler/validators/validate_ptrade_portability.py",
    "quantstudio/ptrade_deploy/transformer.py",
    "quantstudio/ptrade_deploy/templates/isolation_template.py",
    "quantstudio/ptrade_deploy/templates/persistence_template.py",
]

TESTS = [
    "tests/test_api_portability_rules.py",
    "tests/test_isolation_phase1.py",
    "tests/test_deploy_quickmode.py",
]

VERIFY = [
    "scripts/verify_pd0020_c_probe_diff.py",
    "scripts/verify_pd0020_multicfg.py",
    "scripts/verify_pd0020_ast_stub.py",
]


def sha_file(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "-MISSING-"


def main() -> int:
    EVID.mkdir(parents=True, exist_ok=True)
    rec = {"frozen_at": "20261113", "deploy_profile": "daily_eod_1450",
           "python": platform.python_version(), "platform": platform.platform()}

    print("=== 1/4 冻结输入（六策略源）===")
    rec["inputs"] = {}
    for sid, rel in STRATEGIES:
        h = sha_file(ROOT / rel); rec["inputs"][sid] = {"path": rel, "sha256": h}
        print("  %-38s %s" % (sid, h[:16]))

    print("=== 2/4 冻结代码／测试／核验器 ===")
    rec["code"], rec["tests"], rec["verify"] = {}, {}, {}
    for rel in CODE:
        rec["code"][rel] = sha_file(ROOT / rel); print("  %-62s %s" % (rel, rec["code"][rel][:16]))
    for rel in TESTS:
        rec["tests"][rel] = sha_file(ROOT / rel)
    for rel in VERIFY:
        rec["verify"][rel] = sha_file(ROOT / rel)

    print("=== 3/4 冻结配置与依赖 ===")
    rec["config"] = {"strategy_id": "tech_etf_mvo_rotation", "profile": "daily_eod_1450",
                     "persistence_enabled": True, "isolation_enabled": False,
                     "note": "六策略重生使用 CLI 默认 profile；本字段为 ⓒ/多配置核验所用配置"}
    pkgs = {}
    for name in ("duckdb", "pandas", "numpy", "pytest"):
        try:
            m = __import__(name); pkgs[name] = getattr(m, "__version__", "?")
        except Exception:
            pkgs[name] = "NOT-INSTALLED"
    rec["deps"] = pkgs
    print("   python", rec["python"], "| deps", pkgs)

    print("=== 4/4 六策略第一跳重生（不继承历史产物）===")
    rec["products"] = {}
    ok_all = True
    for sid, rel in STRATEGIES:
        print("  --- %s ---" % sid)
        r = subprocess.run([sys.executable, "-m", "quantstudio.strategy_compiler.cli",
                            "import", rel, "--no-smoke"],
                           cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        tail = (r.stdout or "").strip().split("\n")[-2:]
        for l in tail:
            print("     ", l[:150])
        # 找产物与 run_card
        prod = ROOT / "output" / "ptrade_export" / sid / ("%s_ptrade.py" % sid)
        cands = list((ROOT / "output" / "ptrade_export").glob("*"))
        if not prod.is_file():
            for d in cands:
                if d.is_dir() and sid.split("_")[0] in d.name:
                    ps = list(d.glob("*_ptrade.py"))
                    if ps:
                        prod = ps[0]; break
        rc = prod.parent / "run_card.json" if prod.is_file() else None
        ap = "-"
        if rc and rc.is_file():
            try:
                ap = (json.loads(rc.read_text(encoding="utf-8")).get("validation") or {}).get("api_portability", "-")
            except Exception:
                ap = "PARSE-ERR"
        rec["products"][sid] = {
            "path": str(prod.relative_to(ROOT)) if prod.is_file() else "MISSING",
            "sha256": sha_file(prod) if prod.is_file() else "-MISSING-",
            "bytes": prod.stat().st_size if prod.is_file() else 0,
            "api_portability": ap,
            "returncode": r.returncode,
        }
        print("      api_portability=%s returncode=%d" % (ap, r.returncode))
        if ap != "PASS":
            ok_all = False

    (EVID / "freeze.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2),
                                      encoding="utf-8", newline="\n")
    md = ["# PD-2026-0020 期1 · 冻结与六策略重生登记\n\n",
          "> 六策略**逐个重生**，产物 hash 取重生后值；**不继承历史产物 PASS**。\n\n",
          "## 代码冻结\n\n| 文件 | sha256 |\n|---|---|\n"]
    for k, v in rec["code"].items():
        md.append("| `%s` | `%s` |\n" % (k, v))
    md.append("\n## 输入冻结（策略源）\n\n| 策略 | sha256 |\n|---|---|\n")
    for k, v in rec["inputs"].items():
        md.append("| %s | `%s` |\n" % (k, v["sha256"]))
    md.append("\n## 依赖\n\n`python %s` ｜ %s\n" % (rec["python"], rec["deps"]))
    md.append("\n## 产物登记（重生后）\n\n| 策略 | 字节 | sha256 | api_portability | rc |\n|---|---|---|---|---|\n")
    for k, v in rec["products"].items():
        md.append("| %s | %d | `%s` | **%s** | %d |\n"
                  % (k, v["bytes"], v["sha256"], v["api_portability"], v["returncode"]))
    md.append("\n## 结论\n\n六策略 api_portability 全 PASS：%s\n" % ("YES" if ok_all else "NO"))
    (EVID / "manifest.md").write_text("".join(md), encoding="utf-8", newline="\n")
    print()
    print("  总判定:", "PASS" if ok_all else "FAIL")
    print("  证据:", (EVID / "manifest.md").relative_to(ROOT))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
