"""Tab8: 策略回测控制台（选策略→设参数→启动回测→进度→日志→结果窗口）"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QMessageBox)
from qfluentwidgets import (
    ComboBox, LineEdit, PushButton, ProgressBar,
    GroupHeaderCardWidget, DoubleSpinBox, SpinBox)

from ..workers import BacktestWorker
from quantstudio._paths import db_path

logger = logging.getLogger(__name__)


class BacktestTab(QWidget):
    """回测控制台 Tab"""

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self._worker = None
        self._result_window = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 1. 策略选择区
        strat_group = GroupHeaderCardWidget()
        strat_group.setTitle("策略选择")
        inner = QWidget()
        strat_layout = QHBoxLayout(inner)
        strat_group.layout().addWidget(inner)
        strat_layout.addWidget(QLabel("策略文件:"))
        self.strategy_combo = ComboBox()
        self.strategy_combo.setMinimumWidth(300)
        strat_layout.addWidget(self.strategy_combo)
        refresh_btn = PushButton("🔄 刷新")
        refresh_btn.clicked.connect(self._refresh_strategies)
        strat_layout.addWidget(refresh_btn)
        strat_layout.addStretch()
        layout.addWidget(strat_group)

        # 2. 参数输入区
        param_group = GroupHeaderCardWidget()
        param_group.setTitle("回测参数")
        inner = QWidget()
        param_form = QFormLayout(inner)
        param_group.layout().addWidget(inner)

        self.start_edit = LineEdit()
        self.start_edit.setText("2026-01-01")
        self.end_edit = LineEdit()
        self.end_edit.setText("2026-07-13")
        param_form.addRow("起始日期:", self.start_edit)
        param_form.addRow("结束日期:", self.end_edit)

        self.capital_spin = SpinBox()
        self.capital_spin.setRange(10000, 100000000)
        self.capital_spin.setValue(100000)
        param_form.addRow("初始资金:", self.capital_spin)

        self.commission_spin = DoubleSpinBox()
        self.commission_spin.setDecimals(5)
        self.commission_spin.setValue(0.00035)
        param_form.addRow("佣金费率:", self.commission_spin)

        self.stamp_spin = DoubleSpinBox()
        self.stamp_spin.setDecimals(5)
        self.stamp_spin.setValue(0.001)
        param_form.addRow("印花税:", self.stamp_spin)

        self.slippage_spin = DoubleSpinBox()
        self.slippage_spin.setDecimals(4)
        self.slippage_spin.setValue(0.0)
        param_form.addRow("滑点:", self.slippage_spin)

        self.match_price_combo = ComboBox()
        self.match_price_combo.addItem("close (PTrade daily compatible)", userData="close")
        self.match_price_combo.addItem("open", userData="open")
        self.match_price_combo.addItem("next_open (anti-lookahead)", userData="next_open")
        param_form.addRow("Match price:", self.match_price_combo)

        layout.addWidget(param_group)

        # 3. 按钮区
        btn_bar = QHBoxLayout()
        self.run_btn = PushButton("▶ 启动回测")
        self.run_btn.clicked.connect(self._on_run)
        self.stop_btn = PushButton("⏹ 停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        btn_bar.addWidget(self.run_btn)
        btn_bar.addWidget(self.stop_btn)
        btn_bar.addStretch()
        self.status_label = QLabel("")
        btn_bar.addWidget(self.status_label)
        layout.addLayout(btn_bar)

        # 4. 进度条
        self.progress_bar = ProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self._refresh_strategies()

    def _refresh_strategies(self):
        """扫描 strategies/ 目录，填充下拉框"""
        self.strategy_combo.clear()
        strat_dir = self.mw.root_path / "quantstudio" / "backtest" / "strategies"
        if strat_dir.exists():
            for f in sorted(strat_dir.glob("*.py")):
                if not f.name.startswith("_"):
                    self.strategy_combo.addItem(f.name, userData=str(f))

    def _on_run(self):
        """启动回测"""
        strategy_path = self.strategy_combo.currentData()
        if not strategy_path:
            QMessageBox.information(self, "提示", "请先选择策略文件")
            return

        db_path_str = str(db_path())
        from pathlib import Path as P
        if not P(db_path_str).exists():
            QMessageBox.warning(self, "错误", f"DuckDB 不存在: {db_path_str}")
            return

        params = {
            'db_path': db_path_str,
            'start': self.start_edit.text().strip(),
            'end': self.end_edit.text().strip(),
            'capital': self.capital_spin.value(),
            'commission': self.commission_spin.value(),
            'stamp_tax': self.stamp_spin.value(),
            'slippage': self.slippage_spin.value(),
            'match_price_mode': self.match_price_combo.currentData(),
        }

        # 创建 Worker
        self._worker = BacktestWorker(strategy_path, params)
        self._worker.progress.connect(self._on_progress)
        self._worker.day_progress.connect(self._on_day_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.finished_err.connect(self._on_error)

        # 按钮状态切换
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        # 启动
        self.mw.hold_worker(self._worker)
        self._worker.start()

    def _on_stop(self):
        """停止回测"""
        if self._worker:
            self._worker.cancel()
            self.status_label.setText("正在停止...")

    def _on_progress(self, msg):
        """记录回测进度消息并更新状态。"""
        logger.info(msg)
        self.status_label.setText(msg[:80])

    def _on_day_progress(self, current, total, date_str):
        """进度条更新"""
        pct = int(current / total * 100) if total > 0 else 0
        self.progress_bar.setValue(pct)

    def _on_finished(self, result):
        """回测完成"""
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setValue(100)

        output_dir = result.get("output_dir", "")
        self.status_label.setText(f"✅ 回测完成: {output_dir}")

        # 打开结果可视化窗口
        try:
            from ..backtest_result_window import BacktestResultWindow
            self._result_window = BacktestResultWindow(output_dir, self.mw.root_path)
            self._result_window.show()
        except ImportError:
            QMessageBox.information(self, "回测完成",
                f"回测完成！结果已导出:\n{output_dir}\n\n（结果可视化窗口待 G2 阶段实现）")

    def _on_error(self, err):
        """回测出错"""
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"❌ 回测失败")
        logger.error(err)
        QMessageBox.critical(self, "回测错误", err[:500])

    def refresh(self):
        self._refresh_strategies()
