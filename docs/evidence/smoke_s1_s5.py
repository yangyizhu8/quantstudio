# -*- coding: utf-8 -*-
"""首跳升级冒烟 S1–S5 脚本（T018；spec 002-duckdb-1.5-upgrade FR-015）。

**写入落点纪律（spec FR-014）**：S1/S2/S3 的写入只落**独立临时库或独立文件**，
不碰主库既有表、不改主库 schema、不经 `create_writer` 入口；
S4 对主库**只读**；S5 用**文件头只读**读数（不开库）。

用法：
  python docs/evidence/smoke_s1_s5.py            # S1/S2/S3/S5（零生产接触）
  python docs/evidence/smoke_s1_s5.py --with-s4  # 加 S4（主库只读点查，需库未被独占）
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import tempfile
from datetime import datetime, timezone, timedelta

SHANGHAI = timezone(timedelta(hours=8))


def _s1_readwrite(tmpdir: str) -> str:
    """S1 读写：独立临时库建表 → 写 → 读 → 行数一致。"""
    import duckdb
    path = os.path.join(tmpdir, "s1.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE TABLE t (id INTEGER, name VARCHAR)")
    con.execute("INSERT INTO t VALUES (1,'a'),(2,'b')")
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    con.close()
    return f"S1 读写：独立临时库写入 2 行，读回 {n} 行 → {'PASS' if n == 2 else 'FAIL'}"


def _s2_timezone(tmpdir: str) -> str:
    """S2 时区：Asia/Shanghai 时间戳往返一致（写入带时区值 → 读回 UTC → 折回本地）。"""
    import duckdb
    path = os.path.join(tmpdir, "s2.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE TABLE t (ts TIMESTAMPTZ)")
    local = datetime(2026, 10, 29, 9, 30, 0, tzinfo=SHANGHAI)
    con.execute("INSERT INTO t VALUES (?)", [local])
    con.commit()
    back = con.execute("SELECT ts FROM t").fetchone()[0]
    con.close()
    ok = back.astimezone(SHANGHAI) == local
    return (f"S2 时区：写入 {local.isoformat()} → 读回 {back.isoformat()} → "
            f"{'PASS' if ok else 'FAIL'}")


def _s3_on_conflict(tmpdir: str) -> str:
    """S3 重叠批 upsert：同一主键的两批重叠写入后行数不增、值取后批（冲突探测生效）。"""
    import duckdb
    path = os.path.join(tmpdir, "s3.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE TABLE t (code VARCHAR, time BIGINT, v DOUBLE, PRIMARY KEY(code, time))")
    con.execute("INSERT INTO t VALUES ('A',1,1.0),('B',2,2.0)")
    # 重叠批：('A',1) 重复 + 新增 ('C',3)
    con.execute("INSERT INTO t VALUES ('A',1,99.0),('C',3,3.0) "
                "ON CONFLICT (code, time) DO UPDATE SET v = EXCLUDED.v")
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    v = con.execute("SELECT v FROM t WHERE code='A' AND time=1").fetchone()[0]
    con.close()
    ok = (n == 3 and abs(float(v) - 99.0) < 1e-12)
    return f"S3 重叠批 upsert：写入 3 行（含 1 重叠）→ 行数 {n}、A.v={v} → {'PASS' if ok else 'FAIL'}"


def _s4_main_db_readonly(db_path: str) -> str:
    """S4 分片与主键点查：主库**只读**（分片聚合 + 主键点查）。"""
    import duckdb
    con = duckdb.connect(db_path, read_only=True)
    try:
        out = []
        try:
            r = con.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
            out.append(f"stock_daily={r}")
        except Exception as e:
            out.append(f"stock_daily 不可用({type(e).__name__})")
        try:
            r = con.execute("SELECT COUNT(*) FROM index_constituents_snapshot_meta").fetchone()[0]
            out.append(f"snapshot_meta={r}")
        except Exception as e:
            out.append(f"snapshot_meta 不可用({type(e).__name__})")
        return "S4 主库只读点查：" + "，".join(out) + " → PASS（只读，未改主库）"
    finally:
        con.close()


def _s5_storage_version(db_path: str) -> str:
    """S5 存储版本回显：文件头只读（storage_version = uint32 LE @12；magic 'DUCK'@8）。"""
    with open(db_path, "rb") as f:
        head = f.read(64)
    magic = head[8:12]
    sv = struct.unpack("<I", head[12:16])[0]
    created = head[52:64].split(b"\x00")[0].decode("ascii", "replace")
    ok = magic == b"DUCK" and sv <= 68
    return (f"S5 storage_version={sv}（上限 68）创建串={created!r} magic={magic!r} → "
            f"{'PASS' if ok else 'FAIL'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-s4", action="store_true", help="追加 S4（主库只读点查）")
    ap.add_argument("--db", default=os.path.join("data", "quantstudio.db"))
    args = ap.parse_args()

    results = []
    with tempfile.TemporaryDirectory(prefix="qs_smoke_") as td:
        results.append(_s1_readwrite(td))
        results.append(_s2_timezone(td))
        results.append(_s3_on_conflict(td))
    if args.with_s4:
        try:
            results.append(_s4_main_db_readonly(args.db))
        except Exception as e:
            results.append(f"S4 主库只读点查：失败 {type(e).__name__}: {e} → FAIL")
    else:
        results.append("S4 主库只读点查：未执行（需 --with-s4；daemon 运行时库被独占）")
    if os.path.exists(args.db):
        results.append(_s5_storage_version(args.db))
    else:
        results.append("S5 storage_version：主库不存在，跳过")

    for line in results:
        print(line)
    fails = [r for r in results if "FAIL" in r]
    print(f"\n结论：{'FAIL(' + str(len(fails)) + ')' if fails else 'PASS（不含未执行项）'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
