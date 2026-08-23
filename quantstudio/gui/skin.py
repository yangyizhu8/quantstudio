"""QuantStudio GUI 皮肤层 —— GitHub Dark 风格设计 Token 与全局应用。

参考效果图（深色 Web 控制台风）的 PyQt 皮肤实现。本模块只做视觉：
- apply_app_skin(app)：全局主题（qfluentwidgets DARK + 主题色）与 app 级 QSS；
- apply_window_skin(window)：主窗口底色/侧边栏/内容区（在所有 Tab 创建后调用一次）；
- PageHeader：页面级「英文 eyebrow + 中文标题 + 描述」头部部件（纯展示）；
- colorize_log_line()：日志行 HTML 着色（时间戳灰、级别彩色、正文默认）。

设计原则：不依赖库内部私有结构；qfluentwidgets 组件自带组件级 QSS（优先级高于
app 级），故 app 级 QSS 只会作用于无自带样式的原生 Qt 控件（QMessageBox、
QDialog 内的原生控件、QProgressBar、QTextEdit、原生 QTabWidget/QTableWidget 等），
不会破坏 fluent 组件外观。
"""
from __future__ import annotations

import html as _html

from PyQt6.QtGui import QColor, QPalette, QFont
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

# ============================================================
# 设计 Token（D4 定稿：GitHub Dark 系，对齐参考效果图）
# ============================================================
BG_PAGE = "#0d1117"        # 页面/窗口底色（近黑蓝）
BG_SIDEBAR = "#0f141a"     # 侧边栏底色
SIDEBAR_BORDER = "#1f2630" # 侧边栏右缘分隔线
BG_CARD = "#161b22"        # 卡片底色（独立样式表用；fluent 卡片走叠层自动形成）
BG_ELEVATED = "#1c2128"    # 浮层/表头底色
BORDER = "#30363d"         # 通用边框
TEXT_1 = "#e6edf3"         # 主文字
TEXT_2 = "#8b949e"         # 次文字
TEXT_3 = "#6e7681"         # 弱文字（eyebrow/时间戳）
ACCENT = "#2f81f7"         # 强调蓝（主题色/主按钮/选中态）
ACCENT_HOVER = "#388bfd"
SUCCESS = "#3fb950"
WARNING = "#d29922"
DANGER = "#f85149"
MONO_FAMILY = "Cascadia Code, Consolas, monospace"

# 日志着色降级开关（审核补充②：高频刷屏如卡顿，可置 False——仅 WARNING/ERROR 着色）
COLOR_LOG_INFO = True

# ============================================================
# app 级 QSS —— 只覆盖无自带样式的原生 Qt 控件
# ============================================================
APP_QSS = f"""
/* ---- 原生消息框/对话框（QMessageBox、ptrade 进度对话框等）---- */
QMessageBox {{
    background-color: {BG_CARD};
    color: {TEXT_1};
}}
QMessageBox QLabel {{
    background: transparent;
    color: {TEXT_1};
    font-size: 13px;
}}
QMessageBox QPushButton {{
    background-color: {BG_ELEVATED};
    color: {TEXT_1};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 18px;
    min-width: 64px;
}}
QMessageBox QPushButton:hover {{
    background-color: {BORDER};
    border-color: {TEXT_2};
}}
QMessageBox QPushButton:pressed {{
    background-color: {BG_PAGE};
}}

QDialog {{
    background-color: {BG_PAGE};
    color: {TEXT_1};
}}
QDialog > QLabel {{
    background: transparent;
    color: {TEXT_1};
}}
QDialog QPushButton {{
    background-color: {BG_ELEVATED};
    color: {TEXT_1};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 16px;
}}
QDialog QPushButton:hover {{
    background-color: {BORDER};
    border-color: {TEXT_2};
}}
QDialog QPushButton:disabled {{
    color: {TEXT_3};
    background-color: {BG_CARD};
    border: 1px solid {BG_CARD};
}}

/* ---- 原生进度条（ptrade 转换对话框）---- */
QProgressBar {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 5px;
    color: {TEXT_1};
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 4px;
}}

/* ---- 原生选项卡（ptrade 结果 Tabs）---- */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    top: -1px;
    background-color: {BG_PAGE};
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_2};
    padding: 6px 16px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}
QTabBar::tab:selected {{
    color: {TEXT_1};
    background-color: {BG_CARD};
    border-bottom: 2px solid {ACCENT};
}}
QTabBar::tab:hover:!selected {{
    color: {TEXT_1};
}}

/* ---- 原生文本编辑（ptrade 源码预览 QTextEdit）---- */
QTextEdit {{
    background-color: {BG_PAGE};
    color: {TEXT_1};
    border: 1px solid {BORDER};
    border-radius: 6px;
    font-family: {MONO_FAMILY};
    selection-background-color: {ACCENT};
}}

/* ---- 原生表格（ptrade 报告表、无自带样式场景）---- */
QTableWidget {{
    background-color: {BG_PAGE};
    alternate-background-color: {BG_CARD};
    color: {TEXT_1};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 6px;
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
}}
QHeaderView::section {{
    background-color: {BG_ELEVATED};
    color: {TEXT_2};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 6px 8px;
    font-weight: 600;
}}
QTableCornerButton::section {{
    background-color: {BG_ELEVATED};
    border: none;
}}
QTableWidget QTableWidget::item {{
    padding: 4px 8px;
}}

/* ---- 提示与分隔 ---- */
QToolTip {{
    background-color: {BG_ELEVATED};
    color: {TEXT_1};
    border: 1px solid {BORDER};
    padding: 4px 6px;
}}
QSplitter::handle {{
    background-color: {BG_PAGE};
}}

/* ---- 全局文字色（原生 QLabel；fluent 标签自带样式不受影响）---- */
QLabel {{
    color: {TEXT_1};
    background: transparent;
}}
"""

# ============================================================
# 侧边栏 QSS —— 等价覆写库内 navigation_interface.qss（仅换色）
# ============================================================
NAV_QSS = f"""
NavigationPanel[menu=true] {{
    background-color: {BG_SIDEBAR};
    border: 1px solid {SIDEBAR_BORDER};
    border-top-right-radius: 7px;
    border-bottom-right-radius: 7px;
}}
NavigationPanel[menu=false] {{
    background-color: {BG_SIDEBAR};
    border: 1px solid {SIDEBAR_BORDER};
    border-top-right-radius: 7px;
    border-bottom-right-radius: 7px;
}}
NavigationPanel[transparent=true] {{
    background-color: {BG_SIDEBAR};
}}
QScrollArea,
#scrollWidget {{
    border: none;
    background-color: transparent;
}}
"""

# 内容区：透明底、仅保留与侧边栏一致的左缘分隔（参考图：内容区与页面同底色）
STACKED_QSS = f"""
StackedWidget {{
    background-color: transparent;
    border: none;
    border-left: 1px solid {SIDEBAR_BORDER};
    border-top-left-radius: 0px;
}}
"""

# 底部日志面板（卡片容器 + 等宽日志文本）
LOG_PANEL_QSS = f"""
QWidget#logPanel {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QWidget#logPanel QLabel#logEyebrow {{
    color: {TEXT_3};
    font-size: 10px;
    font-weight: 700;
}}
QWidget#logPanel QLabel#logTitle {{
    color: {TEXT_1};
    font-size: 13px;
    font-weight: 600;
}}
"""

LOG_TEXT_QSS = f"""
PlainTextEdit {{
    background-color: {BG_PAGE};
    color: {TEXT_1};
    border: 1px solid {BORDER};
    border-radius: 6px;
    font-family: {MONO_FAMILY};
    font-size: 12px;
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
    padding: 4px 6px;
}}
"""


# ============================================================
# 应用入口
# ============================================================
def _build_dark_palette() -> QPalette:
    """深色调色板：覆盖原生控件（QMessageBox/QFileDialog/输入框等）的默认浅色。

    fluent 组件自带 QSS/自绘，不受调色板影响；此调色板只兜底 palette 驱动的
    原生渲染路径（含部件级 grab 时透明区域的填充色）。
    """
    pal = QPalette()
    c_window = QColor(BG_PAGE)
    c_card = QColor(BG_CARD)
    c_elev = QColor(BG_ELEVATED)
    c_text = QColor(TEXT_1)
    c_text2 = QColor(TEXT_2)
    c_border = QColor(BORDER)
    c_accent = QColor(ACCENT)

    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base,
                 QPalette.ColorRole.AlternateBase, QPalette.ColorRole.Button,
                 QPalette.ColorRole.ToolTipBase):
        pal.setColor(role, c_window)
    pal.setColor(QPalette.ColorRole.AlternateBase, c_card)
    pal.setColor(QPalette.ColorRole.Button, c_elev)
    pal.setColor(QPalette.ColorRole.Text, c_text)
    pal.setColor(QPalette.ColorRole.WindowText, c_text)
    pal.setColor(QPalette.ColorRole.ButtonText, c_text)
    pal.setColor(QPalette.ColorRole.ToolTipText, c_text)
    pal.setColor(QPalette.ColorRole.Highlight, c_accent)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, c_text2)
    pal.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.Light, c_elev)
    pal.setColor(QPalette.ColorRole.Midlight, c_elev)
    pal.setColor(QPalette.ColorRole.Dark, c_border)
    pal.setColor(QPalette.ColorRole.Mid, c_border)
    pal.setColor(QPalette.ColorRole.Shadow, QColor(BG_PAGE))
    # Disabled 态
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, c_text2)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, c_text2)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, c_text2)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor(BG_PAGE))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Window, QColor(BG_PAGE))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor(BG_CARD))
    return pal


def apply_app_skin(app: QApplication) -> None:
    """应用全局皮肤：暗色主题 + 蓝色主题色 + 深色调色板 + app 级 QSS。

    在 QApplication 创建后、任何窗口构造前调用（main_gui.py）。
    """
    from qfluentwidgets import setTheme, setThemeColor, Theme

    setTheme(Theme.DARK)
    setThemeColor(QColor(ACCENT))
    try:
        app.setStyle("Fusion")   # Fusion 对深色调色板遵循最好；fluent 组件走自身 QSS 不受影响
    except Exception:
        pass
    app.setPalette(_build_dark_palette())
    app.setStyleSheet(APP_QSS)


def apply_window_skin(window) -> None:
    """主窗口皮肤：关 Mica、平铺深底、侧边栏/内容区 QSS、日志面板样式。

    在 MainWindow 所有 Tab 创建完成后调用一次（幂等）。
    """
    # 1) 平铺深色底（参考图为平面 Web 风，不用系统 Mica 材质；
    #    Mica 关闭后 setCustomBackgroundColor 的纯色在所有平台都稳定绘制）
    try:
        window.setMicaEffectEnabled(False)
    except Exception:
        pass
    try:
        window.setCustomBackgroundColor(QColor("#f3f6fa"), QColor(BG_PAGE))
    except Exception:
        pass

    # 2) 侧边栏与内容区
    #    注意：库将 NAVIGATION_INTERFACE qss 直接应用在 NavigationPanel 自身
    #    （navigation_panel.py L139），故必须覆写 panel 而非 interface，否则被
    #    panel 自身样式表压住（等价规则换色，几何/圆角保持库内一致）。
    try:
        nav_target = getattr(window.navigationInterface, "panel", None) \
            or window.navigationInterface
        nav_target.setStyleSheet(NAV_QSS)
    except Exception:
        pass
    try:
        window.stackedWidget.setStyleSheet(STACKED_QSS)
    except Exception:
        pass

    # 3) 日志面板（若已创建）
    log_panel = window.findChild(QWidget, "logPanel")
    if log_panel is not None:
        log_panel.setStyleSheet(LOG_PANEL_QSS)
        log_text = window.findChild(QWidget, "log_text")
        if log_text is not None:
            log_text.setStyleSheet(LOG_TEXT_QSS)


# ============================================================
# 页面头部部件（参考图：英文 eyebrow + 中文标题 + 描述）
# ============================================================
class PageHeader(QWidget):
    """Tab 顶部页面头：小号灰色英文 eyebrow + 大号中文标题 + 描述行。

    纯展示部件，不含任何交互；插入各 Tab 布局顶部即可。
    """

    def __init__(self, eyebrow: str, title: str, description: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QVBoxLayout

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 6)
        layout.setSpacing(2)

        self.eyebrowLabel = QLabel(eyebrow.upper())
        f_eye = QFont()
        f_eye.setPointSizeF(8.5)
        f_eye.setBold(True)
        f_eye.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        self.eyebrowLabel.setFont(f_eye)
        self.eyebrowLabel.setStyleSheet(f"color: {TEXT_3}; background: transparent;")

        self.titleLabel = QLabel(title)
        f_title = QFont()
        f_title.setPointSizeF(13.5)
        f_title.setBold(True)
        self.titleLabel.setFont(f_title)
        self.titleLabel.setStyleSheet(f"color: {TEXT_1}; background: transparent;")

        layout.addWidget(self.eyebrowLabel)
        layout.addWidget(self.titleLabel)

        if description:
            self.descLabel = QLabel(description)
            f_desc = QFont()
            f_desc.setPointSizeF(9)
            self.descLabel.setFont(f_desc)
            self.descLabel.setStyleSheet(f"color: {TEXT_2}; background: transparent;")
            layout.addWidget(self.descLabel)


# ============================================================
# 日志着色（time 灰 · 级别彩色 · 正文默认）
# ============================================================
_LOG_LEVEL_COLORS = {
    "WARNING": WARNING,
    "ERROR": DANGER,
    "CRITICAL": DANGER,
    "SUCCESS": SUCCESS,
}


def colorize_log_line(line: str) -> str:
    """把 GuiLogHandler 的纯文本日志行转为着色 HTML。

    行格式："%(asctime)s %(levelname)s %(name)s: %(message)s"（HH:MM:SS）。
    全量 html.escape 后着色；COLOR_LOG_INFO=False 时仅告警及以上着色。
    """
    escaped = _html.escape(line)
    parts = escaped.split(" ", 2)
    if len(parts) < 3:
        return escaped

    ts, level, rest = parts[0], parts[1], parts[2]
    color = _LOG_LEVEL_COLORS.get(level)
    if color is None and not COLOR_LOG_INFO:
        return escaped

    ts_html = f'<span style="color:{TEXT_3};">{ts}</span>'
    if color is not None:
        level_html = f'<span style="color:{color};font-weight:bold;">{level}</span>'
    elif COLOR_LOG_INFO:
        level_html = f'<span style="color:{TEXT_2};">{level}</span>'
    else:
        level_html = level
    return f"{ts_html} {level_html} {rest}"
