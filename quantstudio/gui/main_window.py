"""QuantStudio 控制台主窗口：Fluent Design 风格

左侧 NavigationInterface + 右侧 StackedWidget + 底部日志面板
"""
from __future__ import annotations

import json
import logging
from functools import partial
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
    PushButton, PlainTextEdit, ComboBox,
)

from .log_handler import GuiLogHandler

# ============================================================
# 数据源模式（profile）定义
# config_dir 切换 = 切换采集配置集（collector_tasks / sources / data / alignment）。
# 注意：mcp 与 legacy 两 profile 的 data_config.json 都指向 data/quantstudio.db
# （统一正式库），故切换 profile 不切库、QFQ 与采集同库共存（验收 #13）。
# ============================================================
PROFILES = {
    "mcp": {
        "subdir": "profiles/mcp_only",
        "label": "MCP权威源（默认）",
        "tip": "统一正式库 data/quantstudio.db，QFQ闭环由MCP驱动",
    },
    "legacy": {
        "subdir": "",
        "label": "传统多源（xtquant/tushare等）",
        "tip": "默认 config/ 目录：xtquant+tushare+akshare 混合多源",
    },
}
PROFILE_STATE_FILE = "gui_profile_state.json"
DEFAULT_PROFILE = "mcp"

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
        # app_root 固定为项目根（config_dir 的祖父目录或父目录，取决于 profile 子目录深度）
        self.app_root = self._resolve_app_root(Path(config_dir))
        # config_dir 初始值由调用方传入（main_gui.py 传入 default profile 路径），
        # 但最终以持久化的 profile 状态为准（见 _load_profile_state）。
        self._config_dir_default = Path(config_dir)
        self.config_dir = Path(config_dir)
        self._active_workers: list = []

        # ---- 数据源模式（profile）状态 ----
        self.current_profile = DEFAULT_PROFILE
        self._profile_dirty = False          # 下拉已选但与 current_profile 不同（未应用）
        self._reset_in_progress = False      # 重置水印进行中（守护：禁止切源/启停）
        self._reset_mode = None              # 重置的目标模式（'mcp'/'legacy'）
        self._reset_sources: list = []       # 重置影响的源集合（按 source 过滤）
        self._load_profile_state()

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
    # 数据源模式（profile）管理
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_app_root(config_dir: Path) -> Path:
        """项目根 = config_dir 解析去除 'profiles/<name>' 段后的目录。

        固定不随 config_dir 切换而变，确保 DbHelper 的 root_path、导出、
        QFQ 维护等工作目录始终锚定项目根（提醒 #4）。
        """
        parts = config_dir.resolve().parts
        # 找到 'profiles' 段并截断其后内容
        if "profiles" in parts:
            idx = parts.index("profiles")
            return Path(*parts[:idx])
        return config_dir.resolve().parent

    def _profile_subdir(self, profile: str) -> str:
        return PROFILES.get(profile, PROFILES[DEFAULT_PROFILE])["subdir"]

    def _resolve_config_dir(self, profile: str) -> Path:
        sub = self._profile_subdir(profile)
        if not sub:
            return self.app_root / "config"
        return self.app_root / sub

    def _load_profile_state(self):
        """从 gui_profile_state.json 恢复上次选择的数据源模式（P0：持久化选择）。"""
        try:
            p = self.app_root / PROFILE_STATE_FILE
            if p.exists():
                state = json.loads(p.read_text(encoding="utf-8"))
                prof = state.get("profile", DEFAULT_PROFILE)
                if prof in PROFILES:
                    self.current_profile = prof
                    self.config_dir = self._resolve_config_dir(prof)
                    logger.info(f"恢复数据源模式: {prof} -> config_dir={self.config_dir}")
        except Exception as e:
            logger.warning(f"读取 profile 状态失败，回退默认: {e}")

    def _save_profile_state(self):
        try:
            p = self.app_root / PROFILE_STATE_FILE
            p.write_text(json.dumps({"profile": self.current_profile},
                                    ensure_ascii=False, indent=2),
                         encoding="utf-8")
        except Exception as e:
            logger.warning(f"写入 profile 状态失败: {e}")

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
        """项目根目录。

        固定返回 self.app_root（项目根），不随 config_dir 切换变化（提醒 #4）。
        MCP 模式下 config_dir=config/profiles/mcp_only，但根目录仍指向
        项目根，确保 DbHelper/导出/QFQ 维护等模块目录锚定正确。
        """
        return self.app_root

    # ------------------------------------------------------------------
    # 数据源模式（profile）切换 — 采集任务 Tab 下拉驱动
    # ------------------------------------------------------------------
    def profile_options(self):
        """下拉选项：(value, label) 列表，label 含 tip 提示。"""
        return [(k, f"{v['label']} — {v['tip']}") for k, v in PROFILES.items()]

    def _daemon_running_in_config(self, config_dir: Path) -> bool:
        """读取 daemon_status.json（位于该 config_dir 的 .pipeline_data 下）判断是否运行中。

        复用 daemon_process 的全局 status 读取（单一真源）。任意 config_dir 的 daemon
        实例共享同一全局 status 文件，故 is_daemon_running() 即代表"有 daemon 在跑"。
        """
        try:
            from .daemon_process import is_daemon_running
            return is_daemon_running()
        except Exception as e:
            logger.warning(f"读取 daemon 状态失败: {e}")
            return False

    def _switch_guard(self, target_profile: str) -> str | None:
        """切换数据源模式前的守卫检查。返回冲突描述（None=可切换）。

        冲突场景：
        1. 重置水印进行中（_reset_in_progress）→ 禁止切换（P0 #6）
        2. 任意数据源模式的 daemon 正在运行 → 提示先停止（P0 #6，防配置与实例冲突）
        """
        if self._reset_in_progress:
            return (f"正在执行重置水印（模式：{self._reset_mode}），"
                    f"请等待重置完成后再切换数据源模式。")
        if self._daemon_running_in_config(self.config_dir):
            return ("采集守护进程（常驻增量拉取）正在运行，"
                    "请先「停止采集」后再切换数据源模式，避免配置与运行实例冲突。")
        return None

    def apply_data_source_mode(self, target_profile: str, combo_box=None):
        """采集任务 Tab 下拉切换数据源模式。

        流程：守卫 → 确认弹窗 → 改 config_dir + 持久化 → 刷新全部 Tab。
        不切库（统一正式库 data/quantstudio.db，见 PROFILES 注释）。
        """
        if target_profile not in PROFILES:
            self.show_error("未知数据源模式", f"不支持的模式: {target_profile}")
            return
        if target_profile == self.current_profile and not self._profile_dirty:
            return  # 无变化

        conflict = self._switch_guard(target_profile)
        if conflict:
            if combo_box is not None:
                # 回滚下拉到当前实际模式
                combo_box.blockSignals(True)
                combo_box.setCurrentText(self._current_label())
                combo_box.blockSignals(False)
            self.show_error("切换被阻止", conflict)
            return

        label = PROFILES[target_profile]["label"]
        mb = MessageBox(
            "切换数据源模式",
            f"确认切换为「{label}」？\n\n"
            f"将切换配置目录为：{self._resolve_config_dir(target_profile)}\n"
            f"数据库仍为统一正式库 data/quantstudio.db（多源数据同库共存）。\n"
            f"切换后所有 Tab 将刷新以加载新配置。",
            self,
        )
        if mb.exec() != MessageBox.DialogCode.Accepted:
            if combo_box is not None:
                combo_box.blockSignals(True)
                combo_box.setCurrentText(self._current_label())
                combo_box.blockSignals(False)
            return

        # 执行切换：仅改 config_dir（DbHelper 无需重建，提醒 #2）
        self.current_profile = target_profile
        self.config_dir = self._resolve_config_dir(target_profile)
        self._profile_dirty = False
        self._save_profile_state()
        logger.info(f"已切换数据源模式: {target_profile} (config_dir={self.config_dir})")
        self._refresh_all_tabs()
        InfoBar.success(
            title="已切换数据源模式",
            content=f"当前：{label}",
            orient=Qt.Orientation.Horizontal,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _current_label(self) -> str:
        return PROFILES[self.current_profile]["label"]

    def _refresh_all_tabs(self):
        """刷新所有已加载 Tab（切换模式后重新加载配置）。"""
        for i in range(self.stackedWidget.count()):
            self._refresh_tab(i)

    def show_reset_progress(self, title: str, content: str):
        """重置水印期间弹出进度提示（非阻塞）。"""
        InfoBar.info(
            title=title, content=content,
            orient=Qt.Orientation.Horizontal,
            position=InfoBarPosition.TOP,
            duration=2000, parent=self,
        )

    def show_error(self, title: str, content: str):
        """统一错误弹窗（兜底，避免各 Tab 重复实现）。"""
        mb = MessageBox(title, content, self)
        mb.exec()

    # ------------------------------------------------------------------
    # 采集守护进程控制（统一守卫，Tab 通过本方法调用）
    # ------------------------------------------------------------------
    def toggle_daemon(self, start: bool, on_done=None):
        """启停采集守护进程。自动带 config_dir（当前模式目录）。

        返回：(success: bool, message: str)
        """
        if self._reset_in_progress:
            return False, "重置水印进行中，禁止启停守护进程"
        try:
            from ..pipeline.daemon_lifecycle import DaemonLifecycle
            dl = DaemonLifecycle(config_dir=self.config_dir)
            ok, msg = dl.ensure_stopped() if not start else dl.ensure_started()
            if on_done:
                on_done(ok, msg)
            return ok, msg
        except Exception as e:
            logger.error(f"守护进程操作失败: {e}", exc_info=True)
            msg = f"守护进程操作异常: {e}"
            if on_done:
                on_done(False, msg)
            return False, msg

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
