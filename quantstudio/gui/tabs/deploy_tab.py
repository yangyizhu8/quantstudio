"""Tab10: 转模拟盘/实盘（qs-deploy M5 薄壳）

设计文档 docs/ptrade-deploy-pipeline-design.md §7.2：与「导出PTrade」tab 同族的
下游段——PTrade 回测策略 → 模拟盘/实盘部署代码（P0-P6 内核流水线）。

薄壳纪律（§7.3 审计检查点）：本文件零转换逻辑，唯一内核调用点 = run_deploy_kernel
（CLI 壳 cmd_deploy 与 parity 测试共用同一函数 → 哈希门禁可机检对等）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QProgressBar, QTextEdit,
    QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, GroupHeaderCardWidget, LineEdit, PrimaryPushButton, PushButton,
)

from ..skin import PageHeader

logger = logging.getLogger(__name__)


def run_deploy_kernel(config_path: str) -> dict:
    """唯一内核调用点（审计检查点：壳内零转换逻辑）。

    CLI（quantstudio.strategy_compiler.cli.cmd_deploy）与本 tab 的 DeployWorker
    共用本函数直调内核 run_deploy——对等判据 = 双端产物 SHA256 逐位一致（§7.3）。
    """
    from quantstudio.ptrade_deploy import run_deploy
    return run_deploy(config_path).to_dict()


class DeployWorker(QThread):
    """转换 worker（仿 PtradeExportWorker：QThread 防止 GUI 冻结）。"""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(dict)
    finished_err = pyqtSignal(str)

    def __init__(self, config_path: str, parent=None):
        super().__init__(parent)
        self.config_path = config_path

    def run(self):
        try:
            self.progress.emit(10, "P0-P6 流水线运行中...")
            d = run_deploy_kernel(self.config_path)
            self.progress.emit(100, f"完成（{d['status']}）")
            self.finished_ok.emit(d)
        except Exception as e:  # 内核契约「绝不抛异常」，此处仅防御性兜底
            logger.exception("qs-deploy 内核异常（防御性兜底触发）")
            self.finished_err.emit(str(e))


class DeployTab(QWidget):
    """转模拟盘/实盘 tab：deploy.yaml 选择 → 运行 → 报告展示（薄壳）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(PageHeader(
            "QS-DEPLOY", "转模拟盘/实盘",
            "PTrade 回测策略 → 模拟盘/实盘部署代码 · P0-P6 内核流水线 · "
            "与 qs-compile deploy 同内核（产物 SHA256 对等）"))

        # 部署配置选择（与 CLI 完全同输入：deploy.yaml 文件）
        grp_cfg = GroupHeaderCardWidget()
        grp_cfg.setTitle("部署配置（deploy.yaml）")
        inner = QWidget()
        row = QHBoxLayout(inner)
        grp_cfg.layout().addWidget(inner)
        self.config_edit = LineEdit()
        self.config_edit.setMinimumWidth(480)
        self.config_edit.setPlaceholderText(
            "deploy.yaml 路径（strategy_id / source / execution.profile 等七段配置）")
        row.addWidget(self.config_edit, 1)
        browse_btn = PushButton("浏览...")
        browse_btn.clicked.connect(self._browse_config)
        row.addWidget(browse_btn)
        layout.addWidget(grp_cfg)

        # 动作 + 进度
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

        # 报告区
        layout.addWidget(QLabel("转换报告（逐段状态 / 注记 / 产物）"))
        self.report_view = QTextEdit()
        self.report_view.setReadOnly(True)
        layout.addWidget(self.report_view, 1)

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _browse_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 deploy.yaml", "", "YAML (*.yaml *.yml)")
        if path:
            self.config_edit.setText(path)

    def _on_start_clicked(self):
        config_path = self.config_edit.text().strip()
        if not config_path:
            self.status_label.setText("请先选择 deploy.yaml")
            return
        if not Path(config_path).exists():
            self.status_label.setText(f"文件不存在: {config_path}")
            return
        if self._worker is not None and self._worker.isRunning():
            self.status_label.setText("转换进行中，请等待完成")
            return
        self.run_btn.setEnabled(False)
        self.status_label.setText("运行中...")
        self._worker = DeployWorker(config_path)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.finished_err.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        self.status_label.setText(msg)

    def _on_finished(self, d: dict):
        self.run_btn.setEnabled(True)
        self.progress_bar.setValue(100)
        self.status_label.setText(f"完成（{d['status']}）")
        lines = []
        for stage, payload in d.get("stages", {}).items():
            status = payload.get("status") if isinstance(payload, dict) else payload
            line = f"[{stage}] {status}"
            if isinstance(payload, dict) and payload.get("reason"):
                line += f" — {payload['reason']}"
            lines.append(line)
        for note in d.get("notes") or []:
            lines.append(f"[note] {note}")
        for key, p in (d.get("artifacts") or {}).items():
            lines.append(f"[artifact:{key}] {p}")
        self.report_view.setPlainText("\n".join(lines) or "(无报告内容)")

    def _on_error(self, msg: str):
        self.run_btn.setEnabled(True)
        self.status_label.setText(f"失败: {msg}")
        self.report_view.setPlainText(f"ERROR: {msg}")
