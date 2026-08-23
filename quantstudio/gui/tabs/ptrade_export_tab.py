"""Tab9: 导出 PTrade 策略（source entry 转换）

05 规格 §3 组件 + §4 线程模型 + 07 规格 §4 ETF 日历交互。
转换由 orchestrate_source 全流程驱动（转换→门禁→冒烟→run_card）。
"""
from __future__ import annotations

import ast
import logging
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget, QCalendarWidget, QDialogButtonBox,
    QTabWidget, QMessageBox,
)
from qfluentwidgets import (
    ComboBox, LineEdit, PushButton, CheckBox, GroupHeaderCardWidget,
    BodyLabel, PrimaryPushButton, InfoBar, InfoBarPosition, CalendarPicker,
)

from ..skin import PageHeader

logger = logging.getLogger(__name__)

_STRATEGIES_DIR = Path(__file__).resolve().parents[2] / "backtest" / "strategies"


def _detect_etf_pool_call(source_path: Path) -> bool:
    """07 §4.1 步骤 2：AST 预扫描（轻量 <1s），检测 get_etf_list_local 调用。"""
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "get_etf_list_local":
            return True
    return False


class PtradeExportWorker(QThread):
    """转换 worker（05 规格 §4：QThread 强制，防止 GUI 冻结）。"""
    progress = pyqtSignal(int, str)          # (percent, message)
    finished_ok = pyqtSignal(dict, str, str)  # (run_card, source_import_report_path, out_dir)
    finished_err = pyqtSignal(str)

    def __init__(self, source_path: Path, start: str | None, end: str | None,
                 run_smoke: bool, etf_pool_start_date: str | None,
                 db_path: str | None = None, parent=None):
        super().__init__(parent)
        self.source_path = source_path
        # 注意：不能用 self.start/self.end —— 会遮蔽 QThread.start() 方法
        # （实例属性遮蔽 → 点击转换时 'str' object is not callable → PyQt 主线程
        # 槽内未捕获异常 → 整个进程 abort 闪退，2026-08-11 实测复现）
        self.start_date = start
        self.end_date = end
        self.run_smoke = run_smoke
        self.etf_pool_start_date = etf_pool_start_date
        self.db_path = db_path

    def run(self):
        try:
            self.progress.emit(10, "正在转换...")
            from quantstudio.strategy_compiler.orchestrator import orchestrate_source
            run_card = orchestrate_source(
                self.source_path,
                start=self.start_date, end=self.end_date,
                run_smoke=self.run_smoke,
                strict=True,
                etf_pool_start_date=self.etf_pool_start_date,
                db_path=self.db_path,
            )
            status = run_card.get("status", "UNKNOWN")
            if status == "PASS":
                self.progress.emit(100, "完成（PASS）")
            elif status == "PARTIAL":
                self.progress.emit(100, "完成（PARTIAL：冒烟未跑或被门禁拦）")
            else:
                self.progress.emit(100, f"完成（{status}）")
            # 从 run_card.artifacts 定位真实产物路径（run_card schema additionalProperties=false，
            # 不能加 out_dir 键；artifacts 的 name/path 由 orchestrate_source 写入，最可靠）
            report_path = ""
            out_dir_str = ""
            for a in run_card.get("artifacts", []):
                ap = Path(a.get("path", ""))
                if a.get("name") == "source_import_report.json":
                    report_path = str(ap)
                if a.get("name", "").endswith("_ptrade.py") and ap.parent:
                    out_dir_str = str(ap.parent)
            self.finished_ok.emit(run_card, report_path, out_dir_str)
        except Exception as e:
            logger.exception("ptrade export failed")
            self.finished_err.emit(f"{type(e).__name__}: {e}")


class PtradeExportTab(QWidget):
    """导出 PTrade 策略：策略文件选择 + 参数 + 转换 + 结果预览。"""

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self._worker: PtradeExportWorker | None = None
        self._setup_ui()
        self._refresh_strategies()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 页面头（参考效果图风格）
        layout.addWidget(PageHeader(
            "PTRADE EXPORT", "导出 PTrade 策略",
            "源码转换 · 门禁检查 · 冒烟回测 · run_card 报告"))

        # 策略文件选择
        grp_src = GroupHeaderCardWidget()
        grp_src.setTitle("策略文件（quantstudio/backtest/strategies/）")
        inner = QWidget()
        row = QHBoxLayout(inner)
        grp_src.layout().addWidget(inner)
        self.strategy_combo = ComboBox()
        self.strategy_combo.setMinimumWidth(420)
        row.addWidget(self.strategy_combo, 1)
        browse_btn = PushButton("浏览...")
        browse_btn.clicked.connect(self._browse_strategy)
        row.addWidget(browse_btn)
        layout.addWidget(grp_src)

        # 参数
        grp_cfg = GroupHeaderCardWidget()
        grp_cfg.setTitle("转换参数")
        inner = QWidget()
        row = QHBoxLayout(inner)
        grp_cfg.layout().addWidget(inner)
        self.smoke_check = CheckBox("冒烟回测（round-trip，默认开）")
        self.smoke_check.setChecked(True)
        row.addWidget(self.smoke_check)
        row.addWidget(QLabel("区间:"))
        self.start_edit = LineEdit()
        self.start_edit.setPlaceholderText("YYYY-MM-DD")
        self.start_edit.setFixedWidth(110)
        row.addWidget(self.start_edit)
        row.addWidget(QLabel("~"))
        self.end_edit = LineEdit()
        self.end_edit.setPlaceholderText("YYYY-MM-DD")
        self.end_edit.setFixedWidth(110)
        row.addWidget(self.end_edit)
        row.addStretch(1)
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

        # 结果
        self.result_tabs = QTabWidget()
        self.report_table = QTableWidget()
        self.report_table.setColumnCount(6)
        self.report_table.setHorizontalHeaderLabels(
            ["行号", "动作", "API", "规则", "严重度", "说明"])
        self.report_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.code_view = QTextEdit()
        self.code_view.setReadOnly(True)
        self.result_tabs.addTab(self.report_table, "转换报告")
        self.result_tabs.addTab(self.code_view, "PTrade 代码")
        layout.addWidget(self.result_tabs, 1)

        # 底部
        bottom_row = QHBoxLayout()
        self.save_btn = PushButton("保存到...")
        self.save_btn.clicked.connect(self._save_code)
        bottom_row.addWidget(self.save_btn)
        self.open_dir_btn = PushButton("打开输出目录")
        self.open_dir_btn.clicked.connect(self._open_out_dir)
        bottom_row.addWidget(self.open_dir_btn)
        bottom_row.addStretch(1)
        layout.addLayout(bottom_row)

        self._last_out_dir: Path | None = None

    def _refresh_strategies(self):
        self.strategy_combo.clear()
        if _STRATEGIES_DIR.exists():
            for f in sorted(_STRATEGIES_DIR.glob("*.py")):
                if f.name != "__init__.py":
                    self.strategy_combo.addItem(f.name)

    def refresh(self):
        """主窗口切到本 Tab 时刷新策略列表。"""
        self._refresh_strategies()

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _browse_strategy(self):
        from PyQt6.QtWidgets import QFileDialog
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
        start = self.start_edit.text().strip() or None
        end = self.end_edit.text().strip() or None
        run_smoke = self.smoke_check.isChecked()

        # 07 §4.1：ETF 日历交互——先预扫描，检测到 get_etf_list_local 弹日历
        etf_start = None
        if _detect_etf_pool_call(src):
            etf_start = self._ask_etf_start_date(start)
            if etf_start is None:
                self.status_label.setText("未提供 ETF 池起始日，转换中止")
                InfoBar.info("转换中止", "未提供 ETF 池起始日（get_etf_list_local 需要固化静态池）",
                             parent=self, position=InfoBarPosition.TOP)
                return

        self._last_out_dir = None
        self._run_btn_enabled(False)
        self.status_label.setText("正在转换...")
        self._worker = PtradeExportWorker(
            src, start, end, run_smoke, etf_pool_start_date=etf_start, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished_ok)
        self._worker.finished_err.connect(self._on_finished_err)
        self._worker.start()

    def _ask_etf_start_date(self, default_start: str | None) -> str | None:
        """07 §4.3：QCalendarWidget 对话框（标题/说明/上限今天/确定取消）。"""
        from PyQt6.QtCore import QDate
        dlg = QDialog(self)
        dlg.setWindowTitle("该策略使用了动态 ETF 池（get_etf_list_local）")
        dlg.setMinimumWidth(460)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(
            "PTrade 回测不支持动态 ETF 池，需根据回测起始日固化为静态池。\n"
            "请选择回测起始日（PTrade 平台回测起始日不得早于此日期）："))
        cal = QCalendarWidget(dlg)
        cal.setMaximumDate(QDate.currentDate())
        if default_start:
            try:
                y, m, d = default_start.split("-")
                cal.setSelectedDate(QDate(int(y), int(m), int(d)))
            except Exception:
                pass
        v.addWidget(cal)
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        v.addWidget(btn_box)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        qd = cal.selectedDate()
        return f"{qd.year():04d}-{qd.month():02d}-{qd.day():02d}"

    # ------------------------------------------------------------------
    # Worker 回调
    # ------------------------------------------------------------------
    def _on_progress(self, percent: int, message: str):
        self.progress_bar.setValue(percent)
        self.status_label.setText(message)

    def _on_finished_ok(self, run_card: dict, report_path: str, out_dir_str: str):
        self._run_btn_enabled(True)
        status = run_card.get("status", "UNKNOWN")
        if status not in ("PASS", "PARTIAL"):
            QMessageBox.warning(self, "转换未通过",
                                f"run_card status={status}\n"
                                + "\n".join(run_card.get("known_limitations", [])[:5]))
        # 填充转换报告表
        self._fill_report_table(report_path)
        # 填充代码预览 + 记录输出目录
        out_dir = Path(out_dir_str) if out_dir_str else None
        if out_dir is not None:
            pt_path = out_dir / f"{run_card.get('strategy_id', 'strategy')}_ptrade.py"
            if pt_path.exists():
                self.code_view.setPlainText(pt_path.read_text(encoding="utf-8"))
            self._last_out_dir = out_dir
            self.status_label.setText(f"完成：输出目录 {out_dir}（{status}）")
        else:
            self._last_out_dir = None
            self.status_label.setText(f"完成（{status}，未找到输出目录）")

    def _on_finished_err(self, message: str):
        self._run_btn_enabled(True)
        self.status_label.setText("转换失败")
        QMessageBox.critical(self, "转换失败", message)

    def _fill_report_table(self, report_path: str):
        import json
        try:
            report = json.loads(Path(report_path).read_text(encoding="utf-8"))
        except Exception:
            self.report_table.setRowCount(0)
            return
        actions = report.get("actions", [])
        self.report_table.setRowCount(len(actions))
        for i, a in enumerate(actions):
            self.report_table.setItem(i, 0, QTableWidgetItem(str(a.get("line", ""))))
            self.report_table.setItem(i, 1, QTableWidgetItem(a.get("action_type", "")))
            self.report_table.setItem(i, 2, QTableWidgetItem(a.get("api_name", "")))
            self.report_table.setItem(i, 3, QTableWidgetItem(a.get("rule_id", "")))
            self.report_table.setItem(i, 4, QTableWidgetItem(a.get("severity", "")))
            self.report_table.setItem(i, 5, QTableWidgetItem(a.get("message", "")))
        self.report_table.resizeColumnsToContents()

    # ------------------------------------------------------------------
    # 保存/打开目录
    # ------------------------------------------------------------------
    def _save_code(self):
        if not self._last_out_dir:
            return
        from PyQt6.QtWidgets import QFileDialog
        src = self._last_out_dir
        files = list(src.glob("*_ptrade.py"))
        if not files:
            return
        dst, _ = QFileDialog.getSaveFileName(self, "保存 PTrade 策略", str(files[0].name),
                                             "Python 文件 (*.py)")
        if dst:
            Path(dst).write_text(files[0].read_text(encoding="utf-8"), encoding="utf-8")

    def _open_out_dir(self):
        """打开输出目录：无目录/不存在/打开失败都给用户反馈（不再静默无反应）。"""
        if not self._last_out_dir:
            InfoBar.info("无输出目录", "请先执行一次转换", parent=self,
                         position=InfoBarPosition.TOP)
            return
        d = self._last_out_dir.resolve()
        if not d.exists():
            InfoBar.error("输出目录不存在", str(d), parent=self,
                          position=InfoBarPosition.TOP)
            return
        try:
            os.startfile(str(d))  # noqa: S606 Windows only
        except OSError as e:
            InfoBar.error("打开失败", f"{d}: {e}", parent=self,
                          position=InfoBarPosition.TOP)

    def _run_btn_enabled(self, enabled: bool):
        self.run_btn.setEnabled(enabled)
        self.strategy_combo.setEnabled(enabled)
        self.smoke_check.setEnabled(enabled)
