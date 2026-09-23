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
    # 笔5（2026-09-23 A 案裁定①）：版本闸 —— 非 1.4.x 拒启（覆盖 GUI 入口；
    # GUI 拉起的 daemon 子进程由 daemon.py 自身再校验一次）
    from quantstudio.pipeline.duckdb_version_gate import require_duckdb_version

    require_duckdb_version("GUI (main_gui.py)")

    from quantstudio._paths import db_path, quarantine_db_path, DATA_ROOT
    from PyQt6.QtWidgets import QApplication
    from quantstudio.gui.main_window import MainWindow
    from quantstudio.gui.db_helper import DbHelper
    from quantstudio.gui.skin import apply_app_skin

    app = QApplication(sys.argv)
    app.setApplicationName("QuantStudio 控制台")

    # 皮肤：暗色主题 + GitHub Dark 蓝色调 + app 级 QSS（见 quantstudio/gui/skin.py）
    apply_app_skin(app)

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
