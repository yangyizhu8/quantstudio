#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_wal_fixture.py — check_wal.sh 回归用合成 fixture 构建器（绝不触碰生产库）。

生成到 --out 目录：
  healthy/      data/quantstudio.db（trade_calendar + stock_daily(time)），无 WAL
  legit_wal/    healthy 之上叠加「合法小 WAL 残留」（INSERT 未 checkpoint）
  foreign_wal/  healthy 之上叠加「外来 WAL」（另一库的 CREATE TABLE 记录 → 重放冲突）
  missing/      空目录（无 data/quantstudio.db，用于缺失用例）

构建后自检 fixture 不变量：healthy 无 WAL；legit_wal/foreign_wal 有非零 WAL。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import duckdb


def build_healthy(db_path: Path) -> None:
    c = duckdb.connect(str(db_path))
    try:
        c.execute("CREATE TABLE trade_calendar (exchange VARCHAR, cal_date VARCHAR)")
        c.execute("INSERT INTO trade_calendar VALUES ('SSE', '2026-10-16')")
        c.execute("CREATE TABLE stock_daily (code VARCHAR, time BIGINT, open DOUBLE, close DOUBLE)")
        c.execute("INSERT INTO stock_daily VALUES ('510300', 1791792000000, 4.0, 4.1)")
    finally:
        c.close()  # 正常关闭 → checkpoint → 无 WAL 残留


def build_legit_wal(db_path: Path) -> None:
    c = duckdb.connect(str(db_path))
    try:
        c.execute("PRAGMA disable_checkpoint_on_shutdown")
        c.execute("INSERT INTO trade_calendar VALUES ('SZSE', '2026-10-16')")
    finally:
        c.close()  # 关闭不 checkpoint → 合法小 WAL 残留


def build_foreign_wal(db_path: Path, workdir: Path) -> None:
    foreign = workdir / "foreign_src.db"
    c = duckdb.connect(str(foreign))
    try:
        c.execute("PRAGMA disable_checkpoint_on_shutdown")
        # 与目标库已有表同名 → 重放该 WAL 时冲突（CatalogException: already exists）
        c.execute("CREATE TABLE stock_daily (code VARCHAR)")
    finally:
        c.close()
    wal = Path(str(foreign) + ".wal")
    if not wal.exists():
        raise SystemExit("foreign WAL 未生成（disable_checkpoint_on_shutdown 不可用？）")
    shutil.copyfile(wal, str(db_path) + ".wal")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="fixture 输出根目录")
    args = ap.parse_args()

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    for name in ("healthy", "legit_wal", "foreign_wal"):
        (out / name / "data").mkdir(parents=True)

    build_healthy(out / "healthy" / "data" / "quantstudio.db")
    build_healthy(out / "legit_wal" / "data" / "quantstudio.db")
    build_legit_wal(out / "legit_wal" / "data" / "quantstudio.db")
    build_healthy(out / "foreign_wal" / "data" / "quantstudio.db")
    build_foreign_wal(out / "foreign_wal" / "data" / "quantstudio.db", out)
    (out / "missing").mkdir(parents=True, exist_ok=True)

    # 自检
    def wal_size(p: Path) -> int:
        w = Path(str(p) + ".wal")
        return w.stat().st_size if w.exists() else 0

    ok = True
    if wal_size(out / "healthy" / "data" / "quantstudio.db") != 0:
        print("!! healthy fixture 残留 WAL", file=sys.stderr)
        ok = False
    for name in ("legit_wal", "foreign_wal"):
        if wal_size(out / name / "data" / "quantstudio.db") <= 0:
            print(f"!! {name} fixture 未生成 WAL", file=sys.stderr)
            ok = False
    print("fixtures built:", out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
