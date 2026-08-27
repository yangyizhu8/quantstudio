"""Phase 4 黄金行验证（可复现）。

159995.SZ etf_daily 2026-05-26 前复权黄金行：
  - 修复前（bug 破坏）：close_front = 2.707（= raw close，批次内基准错误）
  - 修复后（预期）  ：close_front = 1.3540（= raw × adj_i/adj_latest = 2.707 × 1.0/1.9993）

验证口径：adj_i 取该日（ASOF <= time+8h 上海日）最近因子，adj_latest 取全局最新。
复现：python scripts/_phase4_golden_verify.py
输出：逐行判定 + etf_daily/stock_daily 全表正确口径偏离计数。
"""
import duckdb
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
GOLDEN = [
    # (日期, raw close, 修复前 front(bug), 修复后 front(正确))
    ("2026-05-26", 2.707, 2.707, 1.3540),
    ("2026-05-27", 2.631, 2.631, 1.3160),
    ("2026-05-28", 2.637, 2.637, 1.3190),
]

def main():
    con = duckdb.connect('data/quantstudio.db', read_only=True)
    print("=== 159995.SZ etf_daily 黄金行验证 ===")
    print(f"{'日期':<12}{'raw':>8}{'front(实)':>11}{'front(期望)':>12}{'判定':>5}")
    for date_str, raw, bug_v, expect in GOLDEN:
        # 上海日期口径：time 列按 CST 日界存储，构造上海 00:00 的时间戳
        t0 = int(datetime.strptime(date_str, "%Y-%m-%d")
                 .replace(tzinfo=TZ).timestamp() * 1000)
        row = con.execute(
            "SELECT close, close_front FROM etf_daily "
            "WHERE code='159995' AND time >= ? AND time < ?",
            [t0, t0 + 86400000]).fetchone()
        if row is None:
            print(f"{date_str:<12}  行不存在")
            continue
        c, f = row
        ok = "✅" if abs(f - expect) < 0.002 else "❌"
        print(f"{date_str:<12}{c:>8}{f:>11.4f}{expect:>12.4f}{ok:>5}")

    print("\n=== 全表正确口径扫描（偏离应=0） ===")
    con.execute("ATTACH 'data/qfq_aux.db' AS aux (READ_ONLY)")
    for table, aux_table in [("etf_daily", "fund_adj"), ("stock_daily", "adj_factor")]:
        bad = con.execute(f"""
            WITH latest AS (
                SELECT code, adj_factor AS adj_latest FROM (
                    SELECT code, adj_factor,
                           ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) rn
                    FROM aux.{aux_table}
                ) WHERE rn = 1
            ),
            day_factor AS (SELECT code, time, adj_factor FROM aux.{aux_table})
            SELECT COUNT(*)
            FROM main.{table} m
            JOIN latest lt ON m.code = lt.code
            ASOF JOIN day_factor af ON m.code = af.code AND m.time >= af.time
            WHERE m.close_front IS NOT NULL AND m.close > 0
              AND af.adj_factor IS NOT NULL
              AND ABS(af.adj_factor - lt.adj_latest) / lt.adj_latest > 1e-9
              AND ABS(m.close_front - m.close * af.adj_factor / lt.adj_latest)
                  / NULLIF(ABS(m.close_front), 0) > 1e-4
        """).fetchone()[0]
        print(f"  {table}: 偏离 = {bad}（应=0）{'✅' if bad == 0 else '❌'}")
    con.close()

if __name__ == "__main__":
    main()
