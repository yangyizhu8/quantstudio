# -*- coding: utf-8 -*-
"""首拉后验证脚本（管线方案步骤 6/7 前置验证）。

用法: python scripts/verify_pipeline_after_pull.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb

DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"


def main() -> int:
    con = duckdb.connect(DB, read_only=True)
    fails = []

    def check(name, cond, detail=""):
        status = "PASS" if cond else "FAIL"
        print(f"  [{status}] {name} {detail}")
        if not cond:
            fails.append(name)

    print("== 1. stock_dividend 全历史 ==")
    n = con.execute("SELECT COUNT(*) FROM stock_dividend").fetchone()[0]
    print(f"  rows: {n}")
    check("stock_dividend rows >= 30000", n >= 30000, f"(got {n})")
    n_stk = con.execute(
        "SELECT COUNT(*) FROM stock_dividend WHERE stk_div>0 OR stk_bo_rate>0 OR stk_co_rate>0"
    ).fetchone()[0]
    check("送转字段非零行 > 5000", n_stk > 5000, f"(got {n_stk})")
    r = con.execute(
        "SELECT MIN(ex_date), MAX(ex_date) FROM stock_dividend"
    ).fetchone()
    print(f"  ex_date range: {r}")
    check("ex_date 覆盖 2018 以前", r[0] is not None and r[0] < 1577836800000)

    print("== 2. 单位修正抽查（601628 每股 0.618，非 61.8） ==")
    r = con.execute(
        "SELECT cash_div_before_tax FROM stock_dividend "
        "WHERE code='601628' AND ex_date=1783526400000"
    ).fetchone()
    print(f"  601628 2026-07-09 cash_div_before_tax: {r}")
    check("601628 单位修正为 0.618", r is not None and r[0] is not None and abs(r[0] - 0.618) < 1e-6,
          f"(got {r})")

    print("== 3. etf_dividend ==")
    try:
        n2 = con.execute("SELECT COUNT(*) FROM etf_dividend").fetchone()[0]
        print(f"  rows: {n2}")
        check("etf_dividend rows >= 800", n2 >= 800, f"(got {n2})")
        r = con.execute(
            "SELECT ex_date, div_cash FROM etf_dividend WHERE code='510500' ORDER BY ex_date DESC LIMIT 4"
        ).fetchall()
        print(f"  510500 最近4次: {r}")
        r1 = con.execute(
            "SELECT div_cash FROM etf_dividend WHERE code='510500' AND div_cash IN (0.087, 0.091, 0.062, 0.149)"
        ).fetchall()
        check("510500 四次分红值齐全", len(r1) == 4, f"(got {len(r1)})")
    except Exception as e:
        print(f"  etf_dividend 查询失败: {e}")
        fails.append("etf_dividend table")

    print("== 4. 空日期断言（不允许 1970-01-01） ==")
    for t, date_cols in (("stock_dividend", ("record_date", "ann_date", "end_date")),
                         ("etf_dividend", ("record_date", "ann_date", "pay_date"))):
        try:
            expr = " OR ".join(f"{c}=0" for c in date_cols)
            r = con.execute(f"SELECT COUNT(*) FROM {t} WHERE {expr}").fetchone()[0]
            check(f"{t} 无 1970/0 日期", r == 0, f"(got {r})")
        except Exception as e:
            print(f"  {t} 检查跳过: {e}")

    print("== 5. 水位 ==")
    try:
        r = con.execute(
            "SELECT table_name, last_date FROM source_watermark "
            "WHERE source='mcp' AND table_name IN ('stock_dividend','etf_dividend')"
        ).fetchall()
        print(f"  {r}")
        check("水位已推进", len(r) == 2)
    except Exception as e:
        print(f"  watermark 查询失败: {e}")
        fails.append("watermark")

    con.close()
    print("\nRESULT:", "FAIL" if fails else "ALL PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
