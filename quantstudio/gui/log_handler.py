"""日志桥接：Python logging → Qt 信号（实时输出到 GUI 日志面板）"""
import logging
from PyQt6.QtCore import QObject, pyqtSignal


class GuiLogHandler(logging.Handler, QObject):
    """logging.Handler → pyqtSignal 桥接。
    pyqtSignal 自动跨线程安全传递，Worker 线程的日志也能实时到 GUI。"""
    log_signal = pyqtSignal(str)

    def __init__(self, level=logging.INFO):
        logging.Handler.__init__(self, level)
        QObject.__init__(self)
        self.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        self.log_signal.emit(self.format(record))
