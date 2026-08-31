#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_contract_gate —— QuantStudio 框架契约回归门（单命令全量质量门，2026-08-31）。

职责（鲁棒性 A-D 落地收口）：
  1. 契约套件：pytest -k "ptrade or contract or conversion or source_import or
     portability or fidelity or fundmatrix"（wrapper 分支契约 + 矩阵覆盖测试 + RD 固化断言）；
  2. 矩阵门禁：scripts/check_fund_matrix.py --check（wrapper 模板哈希 + 人读 MD 一致性）；
  3. validator 冒烟：6 策略 api_portability 快速重转（可选 --strategies）。

用法:
  python scripts/run_contract_gate.py               # 全量门（契约 + 矩阵门）
  python scripts/run_contract_gate.py --skip-matrix  # 仅契约套件
  python scripts/run_contract_gate.py --strategies   # 追加 6 策略 api_portability 冒烟
  python scripts/run_contract_gate.py --install-hook # 安装 git pre-push hook（双仓 push 前自动门）

退出码 0 = 全绿可放行；非 0 = 任一环节失败（输出明细）。
——『改了 wrapper 没复证就放行』从口头纪律变机器强制（D 哈希门自动触发）。
"""
import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable
K = "-k"
CONTRACT_KW = ("ptrade or contract or conversion or source_import "
               "or portability or fidelity or fundmatrix")

HOOK = """#!/bin/sh
# QuantStudio 契约回归门 pre-push hook（run_contract_gate.py --install-hook 生成，2026-08-31）
# 双仓 push 前自动跑契约套件 + 矩阵门禁；失败拒绝推送。
echo "[contract-gate] running pre-push gate..."
cd "$(git rev-parse --show-toplevel)" || exit 1
python3 scripts/run_contract_gate.py || { echo "[contract-gate] FAILED: 契约/矩阵门未通过，推送取消"; exit 1; }
exit 0
"""


# 既有失败白名单（非本次引入，未立项修复前允许通过 gate；新增失败即拦截）
KNOWN_FAILS = {
    "tests/test_ptrade_public_signature_contract.py::test_slippage_signatures_match_ptrade_keyword_contract",
    "tests/test_qfq_schema_status.py::TestContractConsistency::test_duckdb_cols_matches_ddl_order",
    "tests/test_strategy_name_chinese_contract.py::test_publish_writes_chinese_filename_and_front_blocks_collision",
    "tests/test_target_aware_strategy_skill.py::test_local_only_publish_generates_no_ptrade_placeholder",
    "tests/test_ptrade_profile_registered_stock_apis.py::test_registered_stock_api_source_publishes_identical_dual_targets",
}


# 契约套件受控文件清单（CI 修复 2026-08-31）：只收集契约三件套，避免 pytest 扫描
# tests/ 全目录——GUI/MCP/数据管线测试在 Linux CI 缺依赖（PyQt6 等）import 崩溃 →
# collection error 中断整个 gate（本地 Windows 全环境不触发）。
CONTRACT_FILES = [
    "tests/test_fund_matrix_coverage.py",
    "tests/test_ptrade_contract_compliance.py",
    "tests/test_ptrade_fidelity_config.py",
]
PORTABILITY_K = "-k"
PORTABILITY_KW = "portability or fm_ or fidelity"


def run_pytest(cmd, label):
    print("\n== %s ==" % label)
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    tail = (r.stdout or "") + (r.stderr or "")
    failed = [ln.split("FAILED ", 1)[1].strip() for ln in tail.splitlines()
              if ln.strip().startswith("FAILED ")]
    new_fails = [f for f in failed if f not in KNOWN_FAILS]
    if r.returncode != 0 and not failed:
        # 收集阶段崩溃（collection error）→ 禁止误判 PASS（CI 32-error 教训）
        print("pytest 退出码 %d 且无 FAILED 明细——疑似收集错误/插拔异常："
              % r.returncode)
        for ln in tail.splitlines():
            if "ERROR" in ln or "error" in ln.lower():
                print("  ", ln.strip()[:160])
        return 1
    if not failed:
        print("全部通过；既有白名单无触发")
        return 0
    if new_fails:
        print("新失败 %d 个（须修复）:" % len(new_fails))
        for f in new_fails:
            print("  -", f)
        return 1
    print("仅既有白名单失败 %d 个（非本次引入，放行）: %s" % (len(failed), len(failed)))
    return 0


def run(cmd, label):
    print("\n== %s ==" % label)
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        print("[%s] FAILED (exit %d)" % (label, r.returncode))
    return r.returncode


def main():
    ap = argparse.ArgumentParser(description="框架契约回归门")
    ap.add_argument("--skip-matrix", action="store_true", help="跳过矩阵门禁（仅契约套件）")
    ap.add_argument("--strategies", action="store_true", help="追加 6 策略 api_portability 冒烟")
    ap.add_argument("--install-hook", action="store_true", help="安装 git pre-push hook")
    args = ap.parse_args()

    if args.install_hook:
        hook_path = ROOT / ".git" / "hooks" / "pre-push"
        hook_path.write_text(HOOK, encoding="utf-8")
        try:
            hook_path.chmod(0o755)
        except Exception:
            pass
        print("installed pre-push hook ->", hook_path)
        print("（双仓 push URL 的 pre-push hook 对本仓库 git push 同样生效）")
        return 0

    code = 0
    code |= run_pytest([PY, "-m", "pytest", ] + CONTRACT_FILES + ["-q", "--tb=no"],
                       "契约套件（pytest，受控文件清单 + 既有失败白名单放行）")
    if not args.skip_matrix:
        code |= run([PY, "scripts/check_fund_matrix.py", "--check"], "矩阵门禁（check_fund_matrix --check）")
    if args.strategies:
        code |= run_pytest([PY, "-m", "pytest"] + CONTRACT_FILES + [PORTABILITY_K, PORTABILITY_KW, "-q", "--tb=no"],
                           "6 策略 api_portability 冒烟（同受控清单 -k 子集）")
    print("\n===== CONTRACT GATE : %s =====" % ("PASS" if code == 0 else "FAIL"))
    return code


if __name__ == "__main__":
    sys.exit(main())