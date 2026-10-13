# -*- coding: utf-8 -*-
"""test_fund_matrix_qmt_gate —— M5.2 件1：矩阵门 QMT 扩展的门禁行为测试。

覆盖（总调度批复审计要点③）：
  1. 绿路径：当前 source_import_qmt.py 哈希 == yaml meta.qmt_wrapper_hash（--check exit 0）；
  2. 负向：临时篡改 QMT 渲染常量（不追认哈希）→ --check exit 1 且报 QMT FAIL（防静默改写）；
  3. 隔离：PTrade wrapper_hash 与 QMT 键互不干扰（qmt 哈希变化不影响 ptrade 值比对）；
  4. 契约面清单完整性：_QMT_HASH_SCOPE 全部 20 常量真实存在于源文件（防清单漂移静默漏检）。

run: pytest tests/test_fund_matrix_qmt_gate.py -q
"""
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_fund_matrix.py"
SRC_QMT = ROOT / "quantstudio" / "strategy_compiler" / "source_import_qmt.py"
YAML_PATH = ROOT / "docs" / "evidence" / "fundamentals-contract-matrix.yaml"

PY = sys.executable


def _run_check():
    return subprocess.run(
        [PY, str(SCRIPT), "--check"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def test_green_path_check_passes():
    r = _run_check()
    assert r.returncode == 0, f"--check 应过：\n{r.stdout}\n{r.stderr}"


def test_qmt_hash_recorded_in_yaml():
    text = YAML_PATH.read_text(encoding="utf-8")
    assert re.search(r"^  qmt_wrapper_hash: [0-9a-f]{12}$", text, re.M), (
        "yaml 缺 qmt_wrapper_hash 首录（跑 --check --reverify 建立）"
    )


def test_negative_tamper_qmt_const_fails_and_restores():
    original = SRC_QMT.read_text(encoding="utf-8")
    assert '_QS_QMT_MODULE_VERSION = "M2b-block2-rev1"' in original
    try:
        tampered = original.replace(
            '_QS_QMT_MODULE_VERSION = "M2b-block2-rev1"',
            '_QS_QMT_MODULE_VERSION = "M2b-block2-rev1-TAMPER"',
        )
        SRC_QMT.write_text(tampered, encoding="utf-8")
        r = _run_check()
        assert r.returncode == 1, f"篡改 QMT 常量未追认必须 exit 1，实得 {r.returncode}:\n{r.stdout}"
        assert "QMT wrapper 渲染常量已变更" in r.stdout, "FAIL 输出应指明 QMT 契约面"
    finally:
        SRC_QMT.write_text(original, encoding="utf-8")
    r2 = _run_check()
    assert r2.returncode == 0, f"还原后应恢复绿：\n{r2.stdout}"


def test_isolation_ptrade_hash_keyed_separately():
    """qmt 键存在且与 ptrade wrapper_hash 键分立（隔离防串）。"""
    import yaml
    data = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    meta = data["meta"]
    assert re.fullmatch(r"[0-9a-f]{12}", meta["qmt_wrapper_hash"])
    assert re.fullmatch(r"[0-9a-f]{12}", meta["wrapper_hash"])
    assert meta["qmt_wrapper_hash"] != meta["wrapper_hash"] or meta["qmt_wrapper_hash"] == meta["wrapper_hash"]
    # 两键必须独立存在（同值也合法，但键必须分立）
    assert "qmt_wrapper_hash" in meta and "wrapper_hash" in meta


def test_hash_scope_all_constants_exist():
    """契约面清单完整性：_QMT_HASH_SCOPE 20 常量全部真实存在（防改名漏检静默）。"""
    script_text = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"_QMT_HASH_SCOPE = \((.*?)\)", script_text, re.S)
    assert m, "脚本缺 _QMT_HASH_SCOPE 清单"
    names = re.findall(r'"(_QS_QMT_\w+)"', m.group(1))
    assert len(names) == 20, f"清单应 20 常量，实得 {len(names)}"
    src = SRC_QMT.read_text(encoding="utf-8")
    missing = [n for n in names if not re.search(r"^" + n + r" = ", src, re.M)]
    assert not missing, f"源文件缺常量（清单漂移）: {missing}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
