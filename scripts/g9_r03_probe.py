#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R-20261112-03 甲 + 证据边界「五项最省路径」序1/序2（全部免额度·只读）。

裁定依据：节点2 2026-11-12「R-20261112-03 裁定：甲＋乙双准——000538 六处断点单查照准
（免额度·2 分钟）；recurrence 0→3 照准…五项免额度最省路径照准按序执行」

执行内容
  甲  ：000538 六处断点的逐一定位 + trade_calendar 对照（判断是节假日空档还是真实缺口）
  序1 ：ETF「日线 raw OHLC 不一致」1,111 条的码分布（集中度 ⇒ 决定抽样策略）
  序2 ：000615 128 条的 created_at 批次分布（补全「创建批次分布未验证」）

仅只读；支持 --wait-lock 等锁释放自动执行。
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

CST = 8 * 3600
CODE = "000538"


def d(v) -> str:
    return dt.datetime.utcfromtimestamp(int(v) / 1000 + CST).strftime("%Y-%m-%d")


def try_open(db: str):
    try:
        import duckdb
        return duckdb.connect(db, read_only=True), None
    except Exception as e:
        msg = str(e)
        if "另一个程序正在使用此文件" in msg or "already open" in msg.lower():
            return None, "locked"
        return None, f"other:{type(e).__name__}"


def run(db: str) -> int:
    con, why = try_open(db)
    if con is None:
        print(f"FATAL: 库不可读（{why}）")
        return 3
    try:
        print("NOW =", con.execute("SELECT NOW()").fetchone()[0])

        # ---------- 甲：000538 断点 ----------
        print(f"\n=== 【甲】{CODE} 断点逐一定位 ===")
        ts = [int(r[0]) for r in con.execute(
            "SELECT time FROM stock_daily WHERE code=? ORDER BY time", [CODE]).fetchall()]
        if not ts:
            print(f"  {CODE} 在 stock_daily 无数据")
        else:
            print(f"  总行数 = {len(ts)}  首={d(ts[0])}  末={d(ts[-1])}")
            gaps = []
            for a, b in zip(ts, ts[1:]):
                dd = (b - a) / 86400000
                if dd > 10:
                    gaps.append((d(a), d(b), round(dd, 1)))
            print(f"  >10 天断点 = {len(gaps)} 处：")
            tabs = {x[0] for x in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()}
            has_cal = "trade_calendar" in tabs
            print(f"  trade_calendar 在位 = {has_cal}")
            for i, (x, y, g) in enumerate(gaps, 1):
                xd = dt.date.fromisoformat(x)
                yd = dt.date.fromisoformat(y)
                kind = "节假日型(10~20天)" if 9 <= g <= 20 else "⚠ 异常(>20天)"
                line = f"    {i}. {x}({xd.strftime('%a')}) → {y}({yd.strftime('%a')})  间隔 {g} 天  {kind}"
                if has_cal:
                    try:
                        cols = [c[0] for c in con.execute("DESCRIBE trade_calendar").fetchall()]
                        dcol = next((c for c in cols if "date" in c.lower() and "trade" in c.lower()),
                                    next((c for c in cols if "date" in c.lower()), None))
                        if dcol:
                            flag = "is_trading" if "is_trading" in cols else (
                                "is_open" if "is_open" in cols else None)
                            q = (f"SELECT COUNT(*) FROM trade_calendar WHERE {dcol} > ? "
                                 f"AND {dcol} < ?" + (f" AND {flag}=1" if flag else ""))
                            n = con.execute(q, [x.replace("-", ""), y.replace("-", "")]).fetchone()[0]
                            if n == 0:
                                n2 = con.execute(q, [x, y]).fetchone()[0]
                                n = n2
                            line += f"  日历中间交易日={n} ⇒ {'✓ 日历空档（非缺口）' if n == 0 else '⚠ 日历有交易日但库内缺'}"
                    except Exception as e:
                        line += f"  （日历对照失败 {type(e).__name__}）"
                print(line)

        # ---------- 序1：1,111 条码分布 ----------
        print("\n=== 【序1】ETF 日线 raw OHLC 不一致的码分布 ===")
        tot = con.execute("""
        SELECT COUNT(DISTINCT code) nc, COUNT(*) n FROM qfq_trigger_queue
        WHERE status='dead_letter' AND asset_type='ETF' AND last_error LIKE '%日线 raw OHLC%'""").fetchone()
        print(f"  涉及码数 = {tot[0]}   总条数 = {tot[1]:,}")
        rows = con.execute("""
        SELECT code, COUNT(*) n FROM qfq_trigger_queue
        WHERE status='dead_letter' AND asset_type='ETF' AND last_error LIKE '%日线 raw OHLC%'
        GROUP BY 1 ORDER BY n DESC LIMIT 20""").fetchall()
        print("  TOP20：")
        for i, r in enumerate(rows, 1):
            print(f"    {i:>2d}. {r[0]:<8s} {r[1]:>4,}")
        if tot[0]:
            s_top = sum(r[1] for r in rows)
            print(f"  TOP20 合计 {s_top:,} / {tot[1]:,} = {s_top/max(tot[1],1)*100:.1f}%")

        # ---------- 序2：000615 批次分布 ----------
        print("\n=== 【序2】000615 128 条的 created_at 批次分布 ===")
        nb = con.execute("""
        SELECT COUNT(DISTINCT created_at) FROM qfq_trigger_queue
        WHERE status='dead_letter' AND code='000615' AND asset_type='STOCK'""").fetchone()[0]
        print(f"  distinct created_at = {nb}")
        for r in con.execute("""
        SELECT created_at, COUNT(*) n FROM qfq_trigger_queue
        WHERE status='dead_letter' AND code='000615' AND asset_type='STOCK'
        GROUP BY 1 ORDER BY 2 DESC LIMIT 12""").fetchall():
            print(f"    {r[0]}  {r[1]:>3,} 条")
        return 0
    finally:
        con.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db")
    ap.add_argument("--wait-lock", action="store_true")
    ap.add_argument("--max-wait-hours", type=float, default=14.0)
    ap.add_argument("--poll", type=int, default=20)
    a = ap.parse_args(argv)

    if not a.wait_lock:
        return run(a.db)

    deadline = time.time() + a.max_wait_hours * 3600
    n = 0
    while time.time() < deadline:
        n += 1
        con, why = try_open(a.db)
        if con is not None:
            con.close()
            print(f"[{time.strftime('%H:%M:%S')}] 第 {n} 次探测：锁已释放 ⇒ 开始取证")
            time.sleep(3)
            return run(a.db)
        if n % 15 == 0:
            print(f"[{time.strftime('%H:%M:%S')}] 第 {n} 次：仍锁（{why}）", flush=True)
        time.sleep(a.poll)
    print(f"超时 {a.max_wait_hours}h 未释放")
    return 4


if __name__ == "__main__":
    sys.exit(main())
