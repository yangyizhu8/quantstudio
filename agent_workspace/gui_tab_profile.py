# -*- coding: utf-8 -*-
"""逐 tab 构造计时：定位 MainWindow 构造期（16 s）的真实耗时分布。"""
import faulthandler
import sys
import time
from pathlib import Path

faulthandler.dump_traceback_later(180, exit=True)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
from quantstudio.gui.skin import apply_app_skin  # noqa: E402

apply_app_skin(app)
from quantstudio._paths import DATA_ROOT, db_path, quarantine_db_path  # noqa: E402
from quantstudio.gui.db_helper import DbHelper  # noqa: E402

db_helper = DbHelper(
    duckdb_path=str(db_path()),
    quarantine_path=str(quarantine_db_path()),
    batch_audit_path=str(DATA_ROOT / "batch_audit.db"),
)
from quantstudio.gui.main_window import MainWindow  # noqa: E402

orig_create_tab = MainWindow._create_tab
ROWS = []


def timed_create_tab(self, *args, **kwargs):
    t = time.time()
    result = orig_create_tab(self, *args, **kwargs)
    dt = time.time() - t
    label = args[0] if args else kwargs
    ROWS.append((dt, str(label)[:70]))
    print("[tab %7.2fs] %s" % (dt, str(label)[:70]), flush=True)
    return result


MainWindow._create_tab = timed_create_tab
t0 = time.time()
window = MainWindow(db_helper=db_helper, config_dir=ROOT / "config" / "profiles" / "mcp_only")
total = time.time() - t0
print("\n[总构造 %.2fs] 各 tab 合计 %.2fs" % (total, sum(r[0] for r in ROWS)), flush=True)
print("[最慢 5 项]", flush=True)
for dt, label in sorted(ROWS, reverse=True)[:5]:
    print("   %7.2fs  %s" % (dt, label), flush=True)
print("[startup] DONE", flush=True)
