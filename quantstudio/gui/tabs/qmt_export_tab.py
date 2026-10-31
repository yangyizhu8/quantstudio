"""Tab11: QMT 转换/部署（M5.2 件6——PyQt 双端对等，单一内核架构）

与 CLI 同内核：转换=orchestrate_source(target='qmt', deploy_qmt=...)；
部署=qmt_deploy 内核（deploy_strategy/deploy_file/resolve_deploy_dir）。
PyQt 壳只做交互与展示，无独立实现（双端对等铁律：CLI/PyQt 产出哈希逐位一致）。
件5 落点：部署目录设置=QFileDialog 手选 → qmt_deploy.save_deploy_dir 持久化（无自动探测）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QProgressBar, QTextEdit, QVBoxLayout, QWidget,
    QFileDialog,
)
from qfluentwidgets import (
    ComboBox, PushButton, CheckBox, GroupHeaderCardWidget,
    BodyLabel, PrimaryPushButton, InfoBar, InfoBarPosition, ListWidget,
)

from ..skin import PageHeader

logger = logging.getLogger(__name__)

_STRATEGIES_DIR = Path(__file__).resolve().parents[2] / "backtest" / "strategies"


class QmtExportWorker(QThread):
    """转换 worker（05 规格 §4：QThread 强制防冻结；与 CLI 同内核同参数）。"""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(dict)   # run_card
    finished_err = pyqtSignal(str)

    def __init__(self, source_path: Path, deploy_qmt: bool, parent=None):
        super().__init__(parent)
        self.source_path = source_path
        self.deploy_qmt = deploy_qmt

    def run(self):
        try:
            self.progress.emit(10, "正在转换（QMT 面）...")
            from quantstudio.strategy_compiler.orchestrator import orchestrate_source
            run_card = orchestrate_source(
                self.source_path,
                run_smoke=False,          # QMT 面不跑本地冒烟（M2b 口径）
                strict=True,
                target="qmt",
                deploy_qmt=self.deploy_qmt,
            )
            self.progress.emit(100, f"完成（{run_card.get('status', '?')}）")
            self.finished_ok.emit(run_card)
        except Exception as e:
            logger.exception("qmt export failed")
            self.finished_err.emit(f"{type(e).__name__}: {e}")


class QmtExportTab(QWidget):
    """QMT 转换 + 部署：策略选择 → 转换（可选自动部署）→ 批量部署 canonical 六产物。"""

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self._worker: QmtExportWorker | None = None
        self._setup_ui()
        self._refresh_strategies()
        self._refresh_deploy_state()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(
            "QMT EXPORT", "QMT 转换 / 部署",
            "源码转换 · QMT 白名单门禁 · GBK 产物 · 一键部署"))

        # ---- 部署目录设置（件5：手选+持久化，无自动探测） ----
        grp_dir = GroupHeaderCardWidget()
        grp_dir.setTitle("QMT 部署目录（config/qmt_deploy.json 持久化）")
        inner = QWidget()
        row = QHBoxLayout(inner)
        self.dir_label = BodyLabel("（未设置）")
        row.addWidget(self.dir_label, 1)
        pick_btn = PushButton("选择目录...")
        pick_btn.clicked.connect(self._pick_deploy_dir)
        row.addWidget(pick_btn)
        grp_dir.layout().addWidget(inner)
        layout.addWidget(grp_dir)

        # ---- 转换 ----
        grp_conv = GroupHeaderCardWidget()
        grp_conv.setTitle("策略转换（源码 → QMT 产物）")
        inner = QWidget()
        row = QHBoxLayout(inner)
        self.strategy_combo = ComboBox()
        self.strategy_combo.setMinimumWidth(360)
        row.addWidget(self.strategy_combo, 1)
        browse_btn = PushButton("浏览...")
        browse_btn.clicked.connect(self._browse_strategy)
        row.addWidget(browse_btn)
        self.deploy_check = CheckBox("转换后自动部署")
        self.deploy_check.setChecked(True)
        self.deploy_check.setToolTip("产物落盘后自动部署到上方目录（目录未设置时告警跳过，不 BLOCK）")
        row.addWidget(self.deploy_check)
        grp_conv.layout().addWidget(inner)
        layout.addWidget(grp_conv)

        action_row = QHBoxLayout()
        self.run_btn = PrimaryPushButton("开始转换")
        self.run_btn.clicked.connect(self._on_start_clicked)
        action_row.addWidget(self.run_btn)
        self.status_label = BodyLabel("就绪")
        action_row.addWidget(self.status_label, 1)
        layout.addLayout(action_row)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        # ---- 批量部署（层1 内核：canonical 六产物） ----
        grp_batch = GroupHeaderCardWidget()
        grp_batch.setTitle("批量部署（output/qmt_import canonical 产物）")
        inner = QWidget()
        row = QHBoxLayout(inner)
        self.batch_list = ListWidget()
        self.batch_list.setSelectionMode(
            __import__("PyQt6.QtWidgets", fromlist=["QAbstractItemView"])
            .QAbstractItemView.SelectionMode.MultiSelection)
        self.batch_list.setMaximumHeight(140)
        row.addWidget(self.batch_list, 1)
        col = QVBoxLayout()
        self.batch_btn = PrimaryPushButton("全部部署")
        self.batch_btn.clicked.connect(self._on_batch_deploy)
        col.addWidget(self.batch_btn)
        self.deploy_sel_btn = PushButton("部署选中")
        self.deploy_sel_btn.clicked.connect(self._on_deploy_selected)
        col.addWidget(self.deploy_sel_btn)
        self.batch_status = BodyLabel("")
        col.addWidget(self.batch_status)
        col.addStretch(1)
        row.addLayout(col)
        grp_batch.layout().addWidget(inner)
        layout.addWidget(grp_batch)

        # ---- 结果 ----
        self.result_view = QTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setMaximumHeight(180)
        layout.addWidget(self.result_view, 1)

    def _refresh_strategies(self):
        self.strategy_combo.clear()
        if _STRATEGIES_DIR.exists():
            for f in sorted(_STRATEGIES_DIR.glob("*.py")):
                if f.name != "__init__.py":
                    self.strategy_combo.addItem(f.name)

    def refresh(self):
        self._refresh_strategies()
        self._refresh_deploy_state()

    # ------------------------------------------------------------------
    # 部署目录（件5）
    # ------------------------------------------------------------------
    def _refresh_deploy_state(self):
        from quantstudio.strategy_compiler import qmt_deploy as kd
        cfg = kd.load_deploy_config()
        d = cfg.get("deploy_dir")
        self.dir_label.setText(d if d else "（未设置——点击「选择目录...」首次设置）")
        self.batch_list.clear()
        for sid in kd.list_products():
            self.batch_list.addItem(sid)

    def _pick_deploy_dir(self):
        from quantstudio.strategy_compiler import qmt_deploy as kd
        path = QFileDialog.getExistingDirectory(
            self, "选择大 QMT 客户端 python 目录（如 D:/QMT/国金QMT交易端模拟/python）")
        if not path:
            return
        try:
            saved = kd.save_deploy_dir(path)
            self.dir_label.setText(str(saved))
            InfoBar.success("已保存", f"部署目录: {saved}", parent=self,
                            position=InfoBarPosition.TOP)
        except Exception as e:
            InfoBar.error("保存失败", str(e), parent=self, position=InfoBarPosition.TOP)

    # ------------------------------------------------------------------
    # 转换
    # ------------------------------------------------------------------
    def _browse_strategy(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择策略文件", str(_STRATEGIES_DIR), "Python 文件 (*.py)")
        if path:
            self.strategy_combo.addItem(Path(path).name)
            self.strategy_combo.setCurrentText(Path(path).name)
            self._custom_strategy_path = Path(path)

    def _selected_path(self) -> Path:
        name = self.strategy_combo.currentText()
        if hasattr(self, "_custom_strategy_path") and self._custom_strategy_path.name == name:
            return self._custom_strategy_path
        return _STRATEGIES_DIR / name

    def _on_start_clicked(self):
        if self._worker and self._worker.isRunning():
            InfoBar.warning("转换进行中", "请等待当前转换完成", parent=self,
                            position=InfoBarPosition.TOP)
            return
        src = self._selected_path()
        if not src.exists():
            InfoBar.error("文件不存在", str(src), parent=self, position=InfoBarPosition.TOP)
            return
        self.run_btn.setEnabled(False)
        self.status_label.setText("正在转换...")
        self._worker = QmtExportWorker(src, self.deploy_check.isChecked(), parent=self)
        self._worker.progress.connect(
            lambda p, m: (self.progress_bar.setValue(p), self.status_label.setText(m)))
        self._worker.finished_ok.connect(self._on_finished_ok)
        self._worker.finished_err.connect(self._on_finished_err)
        self._worker.start()

    def _on_finished_ok(self, run_card: dict):
        self.run_btn.setEnabled(True)
        self._refresh_deploy_state()
        lines = [f"stage={run_card.get('stage')} status={run_card.get('status')}"]
        for a in run_card.get("artifacts", []):
            tag = " [deployed]" if a.get("deployed") else ""
            lines.append(f"- {a.get('name')}{tag}  sha256={str(a.get('sha256', ''))[:16]}")
        for w in (run_card.get("warnings") or [])[-4:]:
            lines.append(f"! {w}")
        self.result_view.setPlainText("\n".join(lines))

    def _on_finished_err(self, message: str):
        self.run_btn.setEnabled(True)
        self.status_label.setText("转换失败")
        InfoBar.error("转换失败", message, parent=self, position=InfoBarPosition.TOP)
        self.result_view.setPlainText(message)

    # ------------------------------------------------------------------
    # 批量部署（层1）
    # ------------------------------------------------------------------
    def _on_batch_deploy(self):
        self._deploy_items([self.batch_list.item(i).text()
                            for i in range(self.batch_list.count())])

    def _on_deploy_selected(self):
        items = [it.text() for it in self.batch_list.selectedItems()]
        self._deploy_items(items)

    def _deploy_items(self, sids: list[str]):
        from quantstudio.strategy_compiler import qmt_deploy as kd
        target = kd.resolve_deploy_dir()
        if target is None:
            InfoBar.error("目录未设置", "先在上方选择 QMT 部署目录", parent=self,
                          position=InfoBarPosition.TOP)
            return
        ok, fail, log = 0, 0, []
        for sid in sids:
            try:
                e = kd.deploy_strategy(sid, target, origin="pyqt-batch")
                ok += 1
                log.append(f"OK  {sid} -> {Path(e['dst']).name}  sha={e['sha256'][:16]}")
            except (FileNotFoundError, AssertionError) as exc:
                fail += 1
                log.append(f"FAIL {sid}: {exc}")
        self.batch_status.setText(f"部署: {ok} 成功 / {fail} 失败")
        self.result_view.setPlainText("\n".join(log))
