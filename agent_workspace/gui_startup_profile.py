# -*- coding: utf-8 -*-
"""启动分段计时（V1a/V1b 定位）：QApplication → skin → DbHelper → MainWindow 构造 → show。

只测量，不改行为；结束后立即退出（不进入事件循环）。
"""
import faulthandler
import sys
import time
from pathlib import Path

faulthandler.dump_traceback_later(120, exit=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
T0 = time.time()


def mark(label: str) -> None:
    print("[startup %7.2fs] %s" % (time.time() - T0, label), flush=True)


mark("script start")
from PyQt6.QtWidgets import QApplication  # noqa: E402

mark("PyQt6 imported")
app = QApplication(sys.argv)
app.setApplicationName("QuantStudio 控制台")
mark("QApplication created")

from quantstudio.gui.skin import apply_app_skin  # noqa: E402

apply_app_skin(app)
mark("apply_app_skin done")

from quantstudio._paths import DATA_ROOT, db_path, quarantine_db_path  # noqa: E402
from quantstudio.gui.db_helper import DbHelper  # noqa: E402

db_helper = DbHelper(
    duckdb_path=str(db_path()),
    quarantine_path=str(quarantine_db_path()),
    batch_audit_path=str(DATA_ROOT / "batch_audit.db"),
)
mark("DbHelper created")

from quantstudio.gui.main_window import MainWindow  # noqa: E402

mark("MainWindow module imported")
window = MainWindow(db_helper=db_helper, config_dir=ROOT / "config" / "profiles" / "mcp_only")
mark("MainWindow constructed")
window.show()
mark("window.show() called")
app.processEvents()
mark("first processEvents done")
time.sleep(0.5)
app.processEvents()
mark("second processEvents done (首查应在事件循环中完成)")
print("[startup] DONE", flush=True)
