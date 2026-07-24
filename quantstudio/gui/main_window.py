"""QuantStudio 控制台主窗口：Fluent Design 风格

左侧 NavigationInterface + 右侧 StackedWidget + 底部日志面板
"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QMessageBox, QLabel,
)
from qfluentwidgets import (
    FluentWindow, FluentIcon, NavigationItemPosition,
    setTheme, Theme, InfoBar, InfoBarPosition,
    PushButton, PlainTextEdit,
)

from .log_handler import GuiLogHandler

logger = logging.getLogger(__name__)

# 导航项配置：(文本, FluentIcon, NavigationItemPosition)
_NAV_ITEMS = [
    ("采集任务", FluentIcon.SEND,              NavigationItemPosition.TOP),
    ("数据源",   FluentIcon.DEVELOPER_TOOLS,   NavigationItemPosition.TOP),
    ("数据浏览", FluentIcon.SEARCH,             NavigationItemPosition.TOP),
    ("隔离区",   FluentIcon.FLAG,              NavigationItemPosition.TOP),
    ("质量检查", FluentIcon.CERTIFICATE,        NavigationItemPosition.TOP),
    ("导出",     FluentIcon.SHARE,             NavigationItemPosition.TOP),
    ("配置编辑", FluentIcon.EDIT,              NavigationItemPosition.TOP),
    ("策略回测", FluentIcon.GAME,              NavigationItemPosition.TOP),
]


class MainWindow(FluentWindow):
    """QFluentWidgets 主窗口。

    Args:
        db_helper: DbHelper 实例
        config_dir: config/ 目录路径
    """

    def __init__(self, db_helper, config_dir: Path):
        super().__init__()
        self.db_helper = db_helper
        self.config_dir = Path(config_dir)
        self._active_workers: list = []

        self.setWindowTitle("QuantStudio 数据管线控制台")
        self.resize(1320, 820)

        # 启用亚克力/Mica 模糊背景（Windows 11 风格）
        self.setMicaEffectEnabled(True)

        self._setup_navigation()
        self._integrate_log_panel()
        self._install_log_handler()

        # 默认打开第一个 Tab 时触发刷新
        self._refresh_tab(0)

    # ------------------------------------------------------------------
    # 导航 — 启动时一次性创建所有 Tab
    # ------------------------------------------------------------------
    def _setup_navigation(self):
        """创建所有导航子界面并加入 StackedWidget"""
        for i, (text, icon, position) in enumerate(_NAV_ITEMS):
            widget = self._create_tab(i)
            widget.setObjectName(f"_tab_{i}_{text}")
            self.addSubInterface(widget, icon, text, position=position)

        # 切换 Tab 时自动刷新数据
        self.stackedWidget.currentChanged.connect(self._on_tab_switched)
        self.navigationInterface.expand(useAni=False)

    def _create_tab(self, row: int) -> QWidget:
        """创建指定 Tab 组件，失败时返回错误提示标签"""
        try:
            if row == 0:
                from .tabs.task_tab import TaskTab
                return TaskTab(self)
            elif row == 1:
                from .tabs.source_tab import SourceTab
                return SourceTab(self)
            elif row == 2:
                from .tabs.browser_tab import BrowserTab
                return BrowserTab(self)
            elif row == 3:
                from .tabs.quarantine_tab import QuarantineTab
                return QuarantineTab(self)
            elif row == 4:
                from .tabs.quality_tab import QualityTab
                return QualityTab(self)
            elif row == 5:
                from .tabs.export_tab import ExportTab
                return ExportTab(self)
            elif row == 6:
                from .tabs.config_editor_tab import ConfigEditorTab
                return ConfigEditorTab(self)
            elif row == 7:
                from .tabs.backtest_tab import BacktestTab
                return BacktestTab(self)
        except Exception as e:
            logger.error(f"加载 Tab {row} 失败: {e}", exc_info=True)
            label = QLabel(f"加载失败: {e}")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return label

    def _on_tab_switched(self, index: int):
        """导航切换时刷新当前 Tab 数据"""
        self._refresh_tab(index)

    def _refresh_tab(self, index: int):
        """调用指定 Tab 的 refresh() 方法"""
        if index < 0:
            return
        tab = self.stackedWidget.widget(index)
        if tab and hasattr(tab, "refresh"):
            try:
                tab.refresh()
            except Exception as e:
                logger.warning(f"Tab {index} refresh 失败: {e}")

    # ------------------------------------------------------------------
    # 日志面板
    # ------------------------------------------------------------------
    def _integrate_log_panel(self):
        """将日志面板嵌入到 stackedWidget 下方的分离器中"""
        main_layout = self.layout()
        if main_layout is None:
            return

        # FluentWindow 内部结构: QHBoxLayout
        #   [0] NavigationInterface
        #   [1] QHBoxLayout（右侧，含 stackedWidget）
        right_item = main_layout.itemAt(1)
        if right_item is None:
            return
        right_layout = right_item.layout()
        if right_layout is None:
            return

        # 从右侧布局中取出 stackedWidget
        for i in range(right_layout.count()):
            item = right_layout.itemAt(i)
            if item and item.widget() == self.stackedWidget:
                right_layout.takeAt(i)
                break

        # 创建竖直分离器：上方内容区 + 下方日志面板
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.stackedWidget)

        # 日志面板
        log_panel = self._create_log_panel()
        splitter.addWidget(log_panel)

        splitter.setStretchFactor(0, 3)   # 内容区占 75%
        splitter.setStretchFactor(1, 1)   # 日志占 25%
        splitter.setSizes([600, 220])

        right_layout.addWidget(splitter)

    def _create_log_panel(self) -> QWidget:
        """创建底部日志面板：工具栏 + 文本区域"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 2, 4, 4)
        layout.setSpacing(4)

        # 工具栏
        toolbar = QHBoxLayout()
        clear_btn = PushButton("清空日志")
        clear_btn.setFixedWidth(80)
        clear_btn.clicked.connect(self._clear_log)
        toolbar.addWidget(clear_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 日志文本区域
        self.log_text = PlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(5000)
        self.log_text.setFont(QFont("Cascadia Code", 10))
        layout.addWidget(self.log_text)

        return panel

    def _clear_log(self):
        """清空日志并弹出提醒"""
        self.log_text.clear()
        InfoBar.success(
            title="已清空", content="日志面板已清空",
            orient=Qt.Orientation.Horizontal,
            isClosable=True, position=InfoBarPosition.BOTTOM_LEFT,
            duration=1500, parent=self,
        )

    # ------------------------------------------------------------------
    # 日志桥接
    # ------------------------------------------------------------------
    def _install_log_handler(self):
        """安装日志桥接：root logger → GUI 日志面板"""
        self.log_handler = GuiLogHandler()
        self.log_handler.log_signal.connect(self.log_text.appendPlainText)
        root_logger = logging.getLogger()
        root_logger.addHandler(self.log_handler)
        root_logger.setLevel(logging.INFO)

        # 同时保留控制台输出
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"))
        root_logger.addHandler(console)
        logger.info("GUI 日志桥接已安装（Fluent Design）")

    # ------------------------------------------------------------------
    # 公共方法（Tab 调用）
    # ------------------------------------------------------------------
    @property
    def root_path(self) -> Path:
        """项目根目录"""
        return self.config_dir.parent

    def hold_worker(self, worker):
        """持有 worker 引用防 GC（完成后自动释放）"""
        self._active_workers.append(worker)
        worker.finished_ok.connect(lambda _: self._release_worker(worker))
        worker.finished_err.connect(lambda _: self._release_worker(worker))

    def _release_worker(self, worker):
        if worker in self._active_workers:
            self._active_workers.remove(worker)

    # ------------------------------------------------------------------
    # 窗口事件
    # ------------------------------------------------------------------
    def closeEvent(self, event):
        """关闭时检查活跃 worker"""
        if self._active_workers:
            reply = QMessageBox.question(
                self, "确认退出",
                f"有 {len(self._active_workers)} 个后台任务正在执行，确定退出？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            for w in self._active_workers:
                w.cancel()
                w.wait(3000)
        super().closeEvent(event)
