# -*- coding: utf-8 -*-
"""test_qmt_export_parity —— M5.2 件6：CLI/PyQt 双端一致性门禁。

架构依据（双端对等铁律）：单一内核+双薄壳——CLI（strategy_compiler/cli.py cmd_import
--target qmt --deploy-qmt）与 PyQt（gui/tabs/qmt_export_tab.py QmtExportWorker）调
**同一** orchestrate_source(target='qmt', deploy_qmt=...)；两端产出一致性的机检等价于：
  1. 内核确定性：同输入同参数两次转换，产物 sha256 逐位一致；
  2. 薄壳纯透传：PyQt worker 的调用参数集与 CLI 等价（源码级静态断言，防壳内私改参数）。

run: pytest tests/test_qmt_export_parity.py -q
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CLI_PATH = ROOT / "quantstudio" / "strategy_compiler" / "cli.py"
TAB_PATH = ROOT / "quantstudio" / "gui" / "tabs" / "qmt_export_tab.py"


def _kernel_deterministic(tmp_path):
    """同输入两次转换产物 sha 逐位一致（转换落盘在 out_dir，互不覆盖）。"""
    from quantstudio.strategy_compiler.orchestrator import orchestrate_source
    src = ROOT / "quantstudio" / "backtest" / "strategies" / "fall_reversal_quantstudio.py"
    shas = []
    for i in (1, 2):
        out = tmp_path / f"run{i}"
        rc = orchestrate_source(src, run_smoke=False, strict=True,
                                target="qmt", deploy_qmt=False, out_dir=out)
        arts = [a for a in rc["artifacts"] if a["name"].endswith("_qmt.py")]
        assert len(arts) == 1, f"run{i} 应恰有 1 个 qmt 产物: {[a['name'] for a in arts]}"
        shas.append(arts[0]["sha256"])
    assert shas[0] == shas[1], f"内核非确定性: {shas}"
    return shas[0]


def test_kernel_deterministic_same_input_same_sha(tmp_path):
    sha = _kernel_deterministic(tmp_path)
    assert re.fullmatch(r"[0-9a-f]{64}", sha)


def test_shell_purity_pyqt_and_cli_call_same_kernel():
    """薄壳纯透传（源码级静态断言）：两端源码都 import 并调用 orchestrate_source，
    且 PyQt worker 固定 target='qmt'，deploy_qmt 由构造参数透传。"""
    tab_src = TAB_PATH.read_text(encoding="utf-8")
    assert "from quantstudio.strategy_compiler.orchestrator import orchestrate_source" in tab_src
    assert re.search(r'target\s*=\s*"qmt"', tab_src), "PyQt worker 应固定 target='qmt'"
    assert re.search(r"deploy_qmt\s*=\s*self\.deploy_qmt", tab_src), "deploy_qmt 应构造参数透传"
    # CLI 侧同核 + 旗标透传（M5.2 件4 层2 落点）
    cli_src = CLI_PATH.read_text(encoding="utf-8")
    assert "orchestrate_source" in cli_src
    assert re.search(r"deploy_qmt\s*=\s*getattr\(args,\s*'deploy_qmt'", cli_src)


def test_tab_registered_in_main_window():
    """导航注册：_NAV_ITEMS 11 项 + 工厂链 row 10 → QmtExportTab。"""
    mw = (ROOT / "quantstudio" / "gui" / "main_window.py").read_text(encoding="utf-8")
    assert "QMT 转换部署" in mw
    assert re.search(r"elif row == 10:.*\n\s*from \.tabs\.qmt_export_tab import QmtExportTab", mw)
    nav_block = re.search(r"_NAV_ITEMS = \[(.*?)\]", mw, re.S).group(1)
    # 导航项会随后续页面增加（specs/003 已加「数据交付」）；本用例只关心 QMT 已注册，不锁死总数
    assert len(re.findall(r"\(\"", nav_block)) >= 11


if __name__ == "__main__":
    sys.exit(pytest_main := __import__("pytest").main([__file__, "-q"]))
