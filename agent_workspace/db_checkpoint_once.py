# -*- coding: utf-8 -*-
"""一次性维护：把 quantstudio.db 的巨型 WAL 合并回主库（CHECKPOINT）。

背景（2026-09-23）：主库 38 GB + WAL 2.17 GB（未检查点），导致任何 duckdb.connect()
都要先做 WAL 回放（CPU 密集、数分钟~数十分钟）→ GUI 启动期同步 connect 卡住表现为
「pyqt 起不来」。

要点：
- 用与 GUI 相同版本的解释器执行（duckdb 1.4.5；pyproject 钉版 >=1.4.5,<1.5）——
  避免用 1.5.x 写入改变存储格式；
- 只做 CHECKPOINT（DuckDB 官方维护操作，崩溃安全），不改任何表结构与数据语义；
- 打印阶段性耗时；faulthandler 在 30 分钟处 dump 栈以便观察是否卡住。
"""
import faulthandler
import os
import sys
import time

faulthandler.dump_traceback_later(1800, exit=False)

DB = os.path.join("data", "quantstudio.db")
t0 = time.time()


def log(msg: str) -> None:
    print("[ckpt %6.1fs] %s" % (time.time() - t0, msg), flush=True)


log("duckdb %s | db=%s" % (__import__("duckdb").__version__, DB))
import duckdb  # noqa: E402

log("open RW ...")
conn = duckdb.connect(DB)
log("open RW OK")

try:
    wal = DB + ".wal"
    size_before = os.path.getsize(wal) if os.path.exists(wal) else 0
    log("wal before = %.2f GB" % (size_before / 1e9))
    log("CHECKPOINT ...")
    conn.execute("CHECKPOINT")
    log("CHECKPOINT OK")
    size_after = os.path.getsize(wal) if os.path.exists(wal) else 0
    log("wal after  = %.2f GB" % (size_after / 1e9))
finally:
    conn.close()
    log("closed")

log("DONE")
sys.exit(0)
