# -*- coding: utf-8 -*-
"""qs-deploy PyQt 壳测试（M5；设计文档 §7.2/§7.3——双端对等完整形态）。

- **三路 parity 哈希门禁**：内核直调 == CLI 壳 == PyQt 壳（run_deploy_kernel），
  同一 deploy.yaml 三入口产物 SHA256 逐位一致（§7.3 完整形态，V3）；
- DeployTab offscreen 实例化冒烟；
- main_window.py 侵入验证（AST 级：_NAV_ITEMS 第 10 项 + _create_tab row==9 分支）。
"""
import ast
import hashlib
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("PyQt6")
pytest.importorskip("qfluentwidgets")

FIXTURE_DIR = Path(__file__).resolve().parent / "deploy_fixtures"
DAILY_CFG = FIXTURE_DIR / "deploy_daily_eod.yaml"

from quantstudio.ptrade_deploy import run_deploy  # noqa: E402
from quantstudio.strategy_compiler.cli import main  # noqa: E402
from quantstudio.gui.tabs.deploy_tab import DeployTab, run_deploy_kernel  # noqa: E402


def _sha_of_kernel_run() -> tuple[str, str]:
    d = run_deploy(str(DAILY_CFG)).to_dict()
    assert d["status"] == "PASS"
    return d["artifacts"]["product"], hashlib.sha256(
        Path(d["artifacts"]["product"]).read_bytes()).hexdigest()


def test_three_way_parity_sha():
    """§7.3 完整形态：内核 == CLI == PyQt 三入口产物 SHA256 逐位一致。"""
    product_path, h_kernel = _sha_of_kernel_run()

    assert main(["deploy", str(DAILY_CFG)]) == 0
    h_cli = hashlib.sha256(Path(product_path).read_bytes()).hexdigest()

    d_gui = run_deploy_kernel(str(DAILY_CFG))
    assert d_gui["status"] == "PASS"
    h_gui = hashlib.sha256(Path(product_path).read_bytes()).hexdigest()

    assert h_kernel == h_cli == h_gui, (h_kernel, h_cli, h_gui)


def test_deploy_tab_offscreen_smoke():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    tab = DeployTab()
    assert tab.run_btn.text() == "开始转换"
    assert tab.config_edit is not None
    # 空输入点按钮 → 状态提示，不崩
    tab._on_start_clicked()
    assert tab.status_label.text() == "请先选择 deploy.yaml"
    tab.deleteLater()


def test_main_window_registration_ast():
    """main_window.py 最小侵入验证：_NAV_ITEMS 第 10 项为 deploy + row==9 分支（AST 级）。

    长度断言为演进稳健锚定（>=10）：其他会话可 append-only 追加后续 tab
    （M5.2 件6 row==10 实例，2026-10-11 叠加时本断言由 ==10 放宽）——
    deploy 项的位置（elts[9]）与分支存在性才是本测试锚定目标。
    """
    src = Path("quantstudio/gui/main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    nav = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(
                node.targets[0], "id", "") == "_NAV_ITEMS":
            nav = node.value
            break
    assert nav is not None and isinstance(nav, ast.List)
    assert len(nav.elts) >= 10
    assert "转模拟盘/实盘" in ast.unparse(nav.elts[9])
    # _create_tab 含 row==9 → DeployTab 分支
    assert "elif row == 9:" in src and "DeployTab(self)" in src
