# -*- coding: utf-8 -*-
"""SC-009 的 GUI 半边 + D-29 真机启动：offscreen 实测（W4 条件项）。

口径：`QT_QPA_PLATFORM=offscreen` 下**真实实例化** `DeliveryTab`，点一次「开始导出」按钮，
让 worker 走完整六相位内核，再把产物与 CLI 子进程导出的产物**逐文件哈希比对**。
不用静态门、不用传递性论证——本测试就是 GUI 那一半的实测。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets", reason="无 PyQt6 ⇒ GUI 半边无法实测")
pytest.importorskip("qfluentwidgets", reason="无 qfluentwidgets ⇒ GUI 半边无法实测")

from PyQt6.QtWidgets import QApplication  # noqa: E402

# 模块级唯一 QApplication：qfluentwidgets 的 qconfig 单例挂在 QApplication 上，
# 每个测试各建一个再销毁会触发 "QConfig has been deleted"（与 conftest 同类注释）。
_APP = QApplication.instance() or QApplication([])

from quantstudio.delivery import export_kernel  # noqa: E402
from quantstudio.delivery import probe as P  # noqa: E402
from tests.fixtures.delivery import Env  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


class _FakeMainWindow:
    """页签只需要记录日志用不到主窗口；给一个最小壳即可（不构造 FluentWindow）。"""

    def __init__(self, app_root: Path):
        self.app_root = app_root


def _hashes(bundle_dir: Path) -> dict:
    doc = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    return {item["path"]: (item["bytes"], item["sha256"]) for item in doc["files"]}


def _cli_export(server, out_dir: Path, tmp_path: Path) -> Path:
    env = dict(os.environ)
    env.update({
        "QUANTSTUDIO_DATA_ROOT": str(server.root),
        "QS_WRITE_LOCK_DIR": str(tmp_path / "_lock_cli"),
        "PYTHONPATH": str(REPO),
    })
    proc = subprocess.run(
        [sys.executable, "-m", "quantstudio.delivery.cli", "export",
         "--export-dir", str(out_dir), "--config-dir", str(server.config_root),
         "--json", "--quiet"],
        capture_output=True, env=env, timeout=600,
    )
    assert proc.returncode == 0, proc.stdout.decode("utf-8", "replace")[-800:] + proc.stderr.decode("utf-8", "replace")[-800:]
    line = [ln for ln in proc.stdout.decode("utf-8", "replace").splitlines() if ln.startswith('{"ok"')][-1]
    return Path(json.loads(line)["result"]["bundle_dir"])


def test_sc009_gui_offscreen_export_matches_cli(tmp_path, monkeypatch):
    server = Env(tmp_path / "server", monkeypatch)
    ci_bundle = server.make_package()                      # 内核直调（W1 fixture 产物）
    cli_bundle = _cli_export(server, tmp_path / "cli_out", tmp_path)

    # GUI 侧：worker 以 config_dir=None / data_root=None 调内核 ⇒ 需把默认源指到 fixture
    fake_repo = tmp_path / "gui_repo"
    (fake_repo).mkdir()
    shutil.copytree(server.config_root, fake_repo / "config")
    monkeypatch.setattr(export_kernel, "_REPO_ROOT", fake_repo)
    monkeypatch.setattr(P, "data_root", lambda: server.root)
    monkeypatch.setenv("QUANTSTUDIO_DATA_ROOT", str(server.root))
    monkeypatch.setenv("QS_WRITE_LOCK_DIR", str(tmp_path / "_lock_gui"))

    app = _APP

    from quantstudio.gui.tabs import delivery_tab as DT
    from quantstudio.gui.workers import DeliveryExportWorker

    # 确定性：把 start() 换成同步执行，避免 QThread 与事件循环的竞态（接线顺序不变）
    monkeypatch.setattr(DeliveryExportWorker, "start", lambda self: self.run(), raising=False)

    tab = DT.DeliveryTab(_FakeMainWindow(fake_repo))        # ← GUI 实例化（D-29）
    assert tab.export_edit is not None and tab.export_btn is not None

    captured: dict = {}
    real_on_ok = tab._on_export_ok

    def _spy(result):                     # 记录结果的同时保持真实槽行为（日志/状态行）
        captured.setdefault("res", result)
        real_on_ok(result)

    tab._on_export_ok = _spy
    tab.export_edit.setText(str(tmp_path / "gui_out"))
    tab.export_btn.click()                                  # ← 走完整导出
    app.processEvents()

    assert "res" in captured, f"GUI 导出未回传结果；日志：{tab.log_edit.toPlainText()[-800:]}"
    gui_bundle = Path(captured["res"]["bundle_dir"])
    assert captured["res"]["qualified"] is True
    assert "导出完成" in tab.log_edit.toPlainText()

    # SC-009 GUI 半边：GUI 产物 == CLI 产物 == 内核产物（逐文件 bytes+sha256）
    gui_hashes = _hashes(gui_bundle)
    assert gui_hashes == _hashes(cli_bundle), "GUI 与 CLI 产物哈希必须全等（SC-009 双端对等）"
    assert gui_hashes == _hashes(ci_bundle), "GUI 与内核产物的哈希必须全等"


def test_gui_tab_imports_without_kernel(tmp_path, monkeypatch):
    """薄壳约束的行为面复核：页签模块可独立导入，且不因缺 GUI 依赖而崩。"""
    from quantstudio.gui.tabs import delivery_tab as DT

    assert hasattr(DT, "DeliveryTab")
    src = Path(DT.__file__).read_text(encoding="utf-8")
    imports = [ln for ln in src.splitlines() if ln.strip().startswith(("import ", "from "))]
    assert not [ln for ln in imports if "quantstudio.delivery" in ln]

def test_sc010_gui_buttons_disabled_until_dir_chosen(tmp_path, monkeypatch):
    """SC-010：未选目录 ⇒ 三个动作按钮禁用 + 状态行提示「请先选择目录」；选好即启用。"""
    app = _APP
    from quantstudio.gui.tabs.delivery_tab import DeliveryTab

    tab = DeliveryTab(_FakeMainWindow(tmp_path))
    app.processEvents()
    assert not tab.export_btn.isEnabled()
    assert not tab.verify_btn.isEnabled()
    assert not tab.import_btn.isEnabled()
    assert "请先选择目录" in tab.status_label.text()

    tab.export_edit.setText(str(tmp_path / "out"))
    app.processEvents()
    assert tab.export_btn.isEnabled()
    assert not tab.verify_btn.isEnabled(), "导入目录未选时校验/导入仍应禁用"

    tab.import_edit.setText(str(tmp_path / "pkg"))
    app.processEvents()
    assert tab.verify_btn.isEnabled() and tab.import_btn.isEnabled()
