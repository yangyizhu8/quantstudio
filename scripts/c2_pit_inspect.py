# -*- coding: utf-8 -*-
"""C2 PIT 截面一致性巡检（master-plan WP-C2②，2026-08-26，只读）。

目的：量化「本地 L6 EPS 过滤比平台多剔 4~6 只」的数据层根因（weekly 双端实证：
L5=30 恒等、L6 本地 15-17 vs 平台 20-21）。方法：以平台端实际入选并买入的
代码集（PIT 可见性已被平台证实）为样本，检查同一 PIT 时点本地 fin_indicator
的 EPS 可见性 —— 缺失者即本地 L6 的额外剔除对象。

检查面：
  A. weekly 策略平台端买入的 16 只代码 × fin_indicator PIT@2026-07-01/07-06
     （eps 行是否存在、ann_date 是否可见、最新 end_date、eps 值）；
  B. 同一批代码 × income_statement.basic_eps PIT 对照（eps→basic_eps 映射样本扩证）；
  C. C1「仅平台 16 只」（本地 stock_daily 缺行情）× fin_indicator 存在性
     （行情与基本面的缺口是否同源）；
  D. 汇总：本地 PIT 视角下平台入选代码的 EPS 缺失数。

用法：python scripts/c2_pit_inspect.py [--db data/quantstudio.db]
只读：read_only 连接，不写任何表。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=8))

# weekly 平台端实际成交买入（对齐报告 §6.1 全 16 只；平台 L6 幸存者子集）
WEEKLY_BOUGHT = [
    "301418", "301210", "300930", "301446", "301097", "001387",
    "301329", "300313", "001336", "001202",          # 07-01
    "300013", "002200",                                # 07-06
    "002494", "002591",                                # 07-13
    "600692", "603255",                                # 07-20/27
]

# C1 仅平台（本地 stock_daily 缺行情）16 只
ONLY_PLATFORM = [
    "000524", "000595", "000656", "002729", "002731", "300214", "300332",
    "600193", "600608", "601369", "603001", "603559", "603722", "605081",
    "688072", "688121",
]


def pit_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=TZ)
    return int(dt.timestamp() * 1000)


def ms_to_date(v):
    try:
        n = float(v)
        if n > 1e12:  # epoch ms
            return datetime.fromtimestamp(n / 1000, TZ).strftime("%Y-%m-%d")
        if n > 1e9:   # epoch s 兜底
            return datetime.fromtimestamp(n, TZ).strftime("%Y-%m-%d")
        return str(int(n))  # YYYYMMDD 数值形态
    except (TypeError, ValueError, OSError):
        return str(v)


def in_list(codes):
    return "(" + ",".join(f"'{c}'" for c in codes) + ")"


def section_a(conn, codes, pit_date):
    """fin_indicator PIT 可见性：每码可见行数 + 最新两行 (end_date, eps, ann_date)。"""
    ms = pit_ms(pit_date)
    rows = conn.execute(f"""
        WITH pit AS (
            SELECT code, end_date, eps, ann_date,
                   ROW_NUMBER() OVER (
                       PARTITION BY regexp_replace(code, '\\.[A-Z]+$', '')
                       ORDER BY end_date DESC, ann_date DESC) AS rn,
                   COUNT(*) OVER (
                       PARTITION BY regexp_replace(code, '\\.[A-Z]+$', '')) AS n_vis
            FROM fin_indicator
            WHERE regexp_replace(code, '\\.[A-Z]+$', '') IN {in_list(codes)}
              AND (ann_date IS NULL OR ann_date <= {ms})
        )
        SELECT code, n_vis, end_date, eps, ann_date, rn FROM pit
        WHERE rn <= 2 ORDER BY code, rn
    """).fetchall()
    by_code = {}
    for code, n_vis, end_date, eps, ann_date, rn in rows:
        bare = code.split(".")[0]
        by_code.setdefault(bare, {"n_vis": n_vis, "rows": []})
        by_code[bare]["rows"].append(
            (ms_to_date(end_date), eps, ms_to_date(ann_date) if ann_date else None))
    return by_code


def section_b(conn, codes, pit_date):
    """income_statement.basic_eps PIT 最新行（eps→basic_eps 映射对照）。"""
    ms = pit_ms(pit_date)
    rows = conn.execute(f"""
        WITH pit AS (
            SELECT code, end_date, basic_eps, ann_date,
                   ROW_NUMBER() OVER (
                       PARTITION BY regexp_replace(code, '\\.[A-Z]+$', '')
                       ORDER BY end_date DESC, ann_date DESC) AS rn
            FROM income_statement
            WHERE regexp_replace(code, '\\.[A-Z]+$', '') IN {in_list(codes)}
              AND (ann_date IS NULL OR ann_date <= {ms})
        )
        SELECT code, end_date, basic_eps, ann_date FROM pit WHERE rn = 1
    """).fetchall()
    return {r[0].split(".")[0]: (ms_to_date(r[1]), r[2],
                                 ms_to_date(r[3]) if r[3] else None) for r in rows}


def section_c(conn, codes):
    """仅平台 16 只 × fin_indicator 行数（与行情缺口同源性检查）。"""
    rows = conn.execute(f"""
        SELECT regexp_replace(code, '\\.[A-Z]+$', '') AS bare, COUNT(*) AS n
        FROM fin_indicator
        WHERE bare IN {in_list(codes)}
        GROUP BY bare
    """).fetchall()
    return dict(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "quantstudio.db"))
    args = ap.parse_args()
    conn = duckdb.connect(args.db, read_only=True)

    for pit_date in ("2026-07-01", "2026-07-06"):
        print(f"\n===== [A] weekly bought x fin_indicator PIT@{pit_date} =====")
        a = section_a(conn, WEEKLY_BOUGHT, pit_date)
        missing = []
        for c in WEEKLY_BOUGHT:
            info = a.get(c)
            if not info or info["n_vis"] == 0:
                missing.append(c)
                print(f"  {c}: NO-VISIBLE-ROW")
                continue
            first = info["rows"][0]
            flag = " <== eps NULL" if first[1] is None else ""
            print(f"  {c}: n_vis={info['n_vis']} latest end={first[0]} "
                  f"eps={first[1]} ann={first[2]}{flag}")
        print(f"  >> missing@{pit_date}: n={len(missing)} {missing}")

    print("\n===== [B] income_statement.basic_eps PIT@2026-07-01 (eps mapping) =====")
    a1 = section_a(conn, WEEKLY_BOUGHT, "2026-07-01")
    b1 = section_b(conn, WEEKLY_BOUGHT, "2026-07-01")
    agree = diff = absent = 0
    for c in WEEKLY_BOUGHT:
        fi = a1.get(c, {}).get("rows") or []
        fi_eps = fi[0][1] if fi else None
        bi = b1.get(c)
        if bi is None or fi_eps is None:
            absent += 1
            print(f"  {c}: fin_eps={fi_eps} is_basic={bi and bi[1]} -> ABSENT/NULL")
            continue
        same = abs(float(fi_eps) - float(bi[1])) < 1e-9
        agree += same
        diff += (not same)
        if not same:
            print(f"  {c}: fin_eps={fi_eps} (end={fi[0][0]}) vs "
                  f"is_basic={bi[1]} (end={bi[0]}) -> DIFF")
    print(f"  >> agree={agree} diff={diff} absent/null={absent} "
          f"(of {len(WEEKLY_BOUGHT)})")

    print("\n===== [C] only-platform 16 x fin_indicator rows =====")
    c_map = section_c(conn, ONLY_PLATFORM)
    none_rows = [c for c in ONLY_PLATFORM if c_map.get(c, 0) == 0]
    for c in ONLY_PLATFORM:
        print(f"  {c}: n={c_map.get(c, 0)}")
    print(f"  >> no-fin-row: n={len(none_rows)} {none_rows}")

    conn.close()


if __name__ == "__main__":
    main()
