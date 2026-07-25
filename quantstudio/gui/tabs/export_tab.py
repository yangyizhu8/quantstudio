"""Tab6: K线分库导出（分库导出 .db 文件）"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFileDialog, QMessageBox)
from qfluentwidgets import (
    LineEdit, PushButton, CheckBox, GroupHeaderCardWidget)

from ..workers import ExportWorker
from quantstudio._paths import db_path, DATA_ROOT

logger = logging.getLogger(__name__)


class ExportTab(QWidget):
    """K线分库导出：选目录 + 输入代码 + 选频率 + 后台导出"""

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 源库路径
        src_group = GroupHeaderCardWidget()
        src_group.setTitle("源库")
        inner = QWidget()
        src_layout = QHBoxLayout(inner)
        src_group.layout().addWidget(inner)
        src_layout.addWidget(QLabel("源库:"))
        src_path = str(db_path())
        src_layout.addWidget(QLabel(src_path))
        layout.addWidget(src_group)

        # 导出目录
        out_group = GroupHeaderCardWidget()
        out_group.setTitle("导出目录")
        inner = QWidget()
        out_layout = QHBoxLayout(inner)
        out_group.layout().addWidget(inner)
        self.out_edit = LineEdit()
        self.out_edit.setText(str(DATA_ROOT / "kline_db"))
        out_layout.addWidget(self.out_edit)
        browse_btn = PushButton("浏览...")
        browse_btn.clicked.connect(self._browse_dir)
        out_layout.addWidget(browse_btn)
        layout.addWidget(out_group)

        # 股票代码
        code_group = GroupHeaderCardWidget()
        code_group.setTitle("股票代码（裸码，逗号分隔，如 600000,000001）")
        inner = QWidget()
        code_layout = QHBoxLayout(inner)
        code_group.layout().addWidget(inner)
        self.code_edit = LineEdit()
        self.code_edit.setText("600000")
        code_layout.addWidget(self.code_edit)
        all_btn = PushButton("全部")
        all_btn.clicked.connect(self._fill_all_codes)
        code_layout.addWidget(all_btn)
        layout.addWidget(code_group)

        # 频率勾选
        freq_group = GroupHeaderCardWidget()
        freq_group.setTitle("频率")
        inner = QWidget()
        freq_layout = QHBoxLayout(inner)
        freq_group.layout().addWidget(inner)
        self.cb_daily = CheckBox("日线 daily")
        self.cb_daily.setChecked(True)
        self.cb_1min = CheckBox("1分钟 1min")
        self.cb_5min = CheckBox("5分钟 5min")
        freq_layout.addWidget(self.cb_daily)
        freq_layout.addWidget(self.cb_1min)
        freq_layout.addWidget(self.cb_5min)
        freq_layout.addStretch()
        layout.addWidget(freq_group)

        # 导出按钮 + 状态
        btn_bar = QHBoxLayout()
        self.export_btn = PushButton("📤 导出")
        self.export_btn.clicked.connect(self._do_export)
        btn_bar.addWidget(self.export_btn)
        btn_bar.addStretch()
        self.status_label = QLabel("")
        btn_bar.addWidget(self.status_label)
        layout.addLayout(btn_bar)

        # 导出结果展示
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        layout.addStretch()

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if d:
            self.out_edit.setText(d)

    def _fill_all_codes(self):
        try:
            tables = self.mw.db_helper.query_duckdb("SELECT DISTINCT code FROM stock_daily")
            codes = tables["code"].tolist()
            self.code_edit.setText(",".join(codes))
        except Exception as e:
            logger.error(f"读取代码失败: {e}")

    def _do_export(self):
        codes_text = self.code_edit.text().strip()
        if not codes_text:
            QMessageBox.information(self, "提示", "请输入股票代码")
            return
        codes = [c.strip() for c in codes_text.split(",") if c.strip()]
        freqs = []
        if self.cb_daily.isChecked():
            freqs.append("daily")
        if self.cb_1min.isChecked():
            freqs.append("1min")
        if self.cb_5min.isChecked():
            freqs.append("5min")
        if not freqs:
            QMessageBox.information(self, "提示", "请至少选一个频率")
            return

        from quantstudio.pipeline.exporter import KLineExporter
        exporter = KLineExporter(
            db_path(), self.out_edit.text())
        worker = ExportWorker(exporter, codes, freqs)
        worker.progress.connect(lambda msg: self.status_label.setText(msg))
        worker.finished_ok.connect(self._on_done)
        worker.finished_err.connect(lambda err: self.status_label.setText(f"❌ {err}"))
        self.mw.hold_worker(worker)
        self.export_btn.setEnabled(False)
        worker.start()

    def _on_done(self, result):
        self.export_btn.setEnabled(True)
        paths = result.get("paths", [])
        self.status_label.setText(f"✅ 导出完成: {len(paths)} 个文件")
        self.result_label.setText("\n".join(paths))

    def refresh(self):
        pass
