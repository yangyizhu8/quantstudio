#!/usr/bin/env python
"""QuantStudio 数据管线控制台 — GUI 入口

启动：python main_gui.py
"""
import sys
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main():
    from quantstudio._paths import db_path, quarantine_db_path, DATA_ROOT
    from PyQt6.QtWidgets import QApplication
    from quantstudio.gui.main_window import MainWindow
    from quantstudio.gui.db_helper import DbHelper
    from qfluentwidgets import setTheme, Theme

    app = QApplication(sys.argv)
    app.setApplicationName("QuantStudio 控制台")

    # 强制深色主题（Fluent Design 暗色风格）
    setTheme(Theme.DARK)

    db_helper = DbHelper(
        duckdb_path=str(db_path()),
        quarantine_path=str(quarantine_db_path()),
        batch_audit_path=str(DATA_ROOT / "batch_audit.db"),
    )

    window = MainWindow(db_helper=db_helper, config_dir=ROOT / "config" / "profiles" / "mcp_only")
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
