"""Tab8: 策略回测控制台（选策略→设参数→启动回测→进度→日志→结果窗口）"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QElapsedTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QMessageBox)
from qfluentwidgets import (
    ComboBox, LineEdit, PushButton, PrimaryPushButton, ProgressBar,
    GroupHeaderCardWidget, DoubleSpinBox, SpinBox)

from ..workers import BacktestWorker
from ..skin import PageHeader
from quantstudio._paths import db_path

logger = logging.getLogger(__name__)


class BacktestTab(QWidget):
    """回测控制台 Tab"""

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self._worker = None
        self._result_window = None
        self._clock = QElapsedTimer()   # 单调钟：回测计时（零漂移）
        self._tick = QTimer(self)       # 1s 刷新计时显示（UI 线程，与引擎线程无耦合）
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._update_time_label)
        self._freeze_text = None        # 定格文本缓存（幂等：以首次定格时刻为准）
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 页面头（参考效果图风格）
        layout.addWidget(PageHeader(
            "BACKTEST CONSOLE", "策略回测",
            "选择策略 · 设置参数 · 运行与结果查看"))

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

        # F1: rebalance_mode 通用配置透出。内部值固定为引擎契约字符串，
        # 显示文本仅用于展示，绝不作为引擎参数。
        self.rebalance_mode_combo = ComboBox()
        self.rebalance_mode_combo.addItem(
            "Legacy pending / immediate behavior", userData="legacy")
        self.rebalance_mode_combo.addItem(
            "Callback basket — next_open + handle_data only",
            userData="callback_basket")
        param_form.addRow("Rebalance mode:", self.rebalance_mode_combo)

        layout.addWidget(param_group)

        # 3. 运行控制区（D2：按钮+状态+进度收进「运行控制」卡片，仅容器调整）
        run_group = GroupHeaderCardWidget()
        run_group.setTitle("运行控制 RUN CONTROL")
        inner_run = QWidget()
        run_layout = QVBoxLayout(inner_run)
        run_layout.setContentsMargins(0, 4, 0, 4)
        run_group.layout().addWidget(inner_run)

        btn_bar = QHBoxLayout()
        self.run_btn = PrimaryPushButton("▶ 启动回测")
        self.run_btn.clicked.connect(self._on_run)
        self.stop_btn = PushButton("⏹ 停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        btn_bar.addWidget(self.run_btn)
        btn_bar.addWidget(self.stop_btn)
        self.time_label = QLabel("⏱ 已用时 00:00:00")
        self.time_label.setStyleSheet("color: #8b949e;")  # GitHub Dark 皮肤浅灰
        btn_bar.addWidget(self.time_label)
        btn_bar.addStretch()
        self.status_label = QLabel("")
        btn_bar.addWidget(self.status_label)
        run_layout.addLayout(btn_bar)

        # 4. 进度条
        self.progress_bar = ProgressBar()
        self.progress_bar.setVisible(False)
        run_layout.addWidget(self.progress_bar)

        layout.addWidget(run_group)

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

        match_price_mode = self.match_price_combo.currentData()
        rebalance_mode = self.rebalance_mode_combo.currentData()

        # F1 组合校验：callback_basket 仅适用于 daily-bar-v1 + next_open。
        # 当前 PyQt 回测入口是日线引擎（daily-bar-v1），分钟引擎不支持 basket。
        if rebalance_mode == "callback_basket" and match_price_mode != "next_open":
            QMessageBox.warning(
                self, "参数冲突",
                "callback_basket 仅适用于 daily-bar-v1 + next_open；\n"
                "close/open 请使用 legacy。")
            return

        params = {
            'db_path': db_path_str,
            'start': self.start_edit.text().strip(),
            'end': self.end_edit.text().strip(),
            'capital': self.capital_spin.value(),
            'commission': self.commission_spin.value(),
            'stamp_tax': self.stamp_spin.value(),
            'slippage': self.slippage_spin.value(),
            'match_price_mode': match_price_mode,
            'rebalance_mode': rebalance_mode,
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

        # 计时：归零并开始（回测完成/出错/手动停止时由 _freeze() 停止并定格）
        self._freeze_text = None
        self._clock.restart()
        self.time_label.setText("⏱ 已用时 00:00:00")
        self._tick.start()

        # 启动
        self.mw.hold_worker(self._worker)
        self._worker.start()

    def _on_stop(self):
        """停止回测（计时定格：最终用时以按下时刻为准——此处即 stop tick + 定格）。

        取消路径（已实证）：worker.cancel() → BacktestWorker._on_engine_progress 检测
        _cancelled → raise RuntimeError("用户取消回测") → finished_err → _on_error；
        _freeze() 幂等（已有定格文本不覆盖），故 _on_error 定格不会改写下按时刻。
        """
        if self._worker:
            self._worker.cancel()
            self._freeze()
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
        self._freeze()  # 计时停止并定格（手动停止后此处幂等，不覆盖按时刻）

        output_dir = result.get("output_dir", "")
        self.status_label.setText(f"✅ 回测完成: {output_dir}")

        # 打开结果可视化窗口。
        # 注意：窗口构造/显示期间的任何异常都必须捕获并反馈。否则异常会从 Qt 槽
        # 函数逸出，.show() 被跳过，导致"回测完成后窗口不自动弹出"且无任何提示。
        try:
            from ..backtest_result_window import BacktestResultWindow
            root_path = getattr(self.mw, 'root_path', None)
            self._result_window = BacktestResultWindow(output_dir, root_path)
            self._result_window.show()
            # 主窗口最大化时新窗口可能被盖在后面，显式置顶。
            self._result_window.raise_()
            self._result_window.activateWindow()
            # 窗口显示并完成布局后，把结果窗口的全部可视化内容导出为图片，
            # 与回测结果 CSV 放到同一目录（基本信息/交易记录/日收益/绩效分析等）。
            QTimer.singleShot(0, self._result_window.export_report_images)
        except Exception as e:
            logger.error(f"打开结果窗口失败: {e}", exc_info=True)
            QMessageBox.warning(self, "回测完成（窗口打开失败）",
                f"回测已完成，结果已导出:\n{output_dir}\n\n"
                f"但结果可视化窗口打开失败:\n{type(e).__name__}: {e}")

    def _on_error(self, err):
        """回测出错（计时定格幂等：手动停止路径已定格则保持按时刻；否则取当前用时）"""
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self._freeze()
        self.status_label.setText("❌ 回测失败")
        logger.error(err)
        QMessageBox.critical(self, "回测错误", err[:500])

    @staticmethod
    def _fmt_elapsed(ms: int) -> str:
        """毫秒 → HH:MM:SS（纯函数；>99h 自然进位不截断）"""
        total_s = max(0, int(ms // 1000))
        h, rem = divmod(total_s, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _update_time_label(self):
        """QTimer(1s) 触发：按单调钟重算显示，杜绝 tick 累积漂移"""
        if self._clock.isValid():
            self.time_label.setText(
                f"⏱ 已用时 {self._fmt_elapsed(self._clock.elapsed())}")

    def _freeze(self):
        """停止计时并定格当前用时（幂等：已有定格文本不覆盖——手动停止后以按时刻为准）。

        终态三路（完成/出错/手动停止）均经 _freeze() 收敛；_tick.stop() 幂等。
        """
        if getattr(self, "_freeze_text", None) is None:
            ms = self._clock.elapsed() if self._clock.isValid() else 0
            self._freeze_text = f"⏱ 用时 {self._fmt_elapsed(ms)}"
        self._tick.stop()
        self.time_label.setText(self._freeze_text)

    def refresh(self):
        self._refresh_strategies()
