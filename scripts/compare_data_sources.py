"""D10 三方数据源对比：xtquant vs tushare(本地DuckDB) vs Ptrade。

目标：判断 xtquant 的流通股本数据是否比 tushare 更接近 Ptrade，
为"是否切换 xtquant 权威源"提供数据支撑。

三方对比口径：
- xtquant: MySQL stock_xtquant_data.xt_financial_capital.circulating_capital（流通股本，股）
- tushare: DuckDB stock_float_share.free_share（流通股本，股）
- Ptrade: 无直接导出，间接推断（从持仓反推不可行，改用选股排名一致性间接判断）

对比方法：
1. 直接对比 xtquant vs tushare 的流通股本差异（量级、分布）
2. 用各自流通股本 × 同一收盘价，重算 curr_float_value，看排名是否一致
3. 哪个数据源算出的 top 选股与 Ptrade 实际选股更接近 → 该源更优
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
from quantstudio._paths import db_path
sys.path.insert(0, str(ROOT))

import pymysql
import duckdb
import pandas as pd
import numpy as np


def fetch_xtquant_circulating(targets, pit_date='2026-01-04'):
    """取 xtquant 各票 <= pit_date 的最新 circulating_capital"""
    conn = pymysql.connect(host='localhost', port=3306, user='root', password='password',
                           database='stock_xtquant_data', charset='utf8mb4')
    cur = conn.cursor()
    rows = []
    for bare in targets:
        code = bare + '.SZ'
        cur.execute(f"""
            SELECT stock_code, source_date, circulating_capital, total_capital
            FROM xt_financial_capital
            WHERE stock_code='{code}' AND source_date <= '{pit_date}'
            ORDER BY source_date DESC LIMIT 1
        """)
        r = cur.fetchone()
        if r:
            rows.append({'code': bare, 'xt_date': r[1], 'xt_circ': r[2], 'xt_total': r[3]})
        else:
            rows.append({'code': bare, 'xt_date': None, 'xt_circ': None, 'xt_total': None})
    conn.close()
    return pd.DataFrame(rows)


def fetch_tushare_circulating(targets, pit_date='2026-01-04'):
    """取 tushare(DuckDB) 各票 <= pit_date 的最新 free_share"""
    conn = duckdb.connect(str(db_path()), read_only=True)
    prev_ms = int(pd.Timestamp(pit_date, tz='Asia/Shanghai').timestamp() * 1000) + 86_399_999
    df = conn.execute(f"""
        SELECT code, time, free_share, circ_mv
        FROM stock_float_share
        WHERE code IN (SELECT unnest($codes)) AND time <= {prev_ms}
        QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) = 1
    """, {'codes': targets}).fetchdf()
    conn.close()
    df['tu_date'] = pd.to_datetime(df['time'], unit='ms').dt.tz_localize('UTC').dt.tz_convert('Asia/Shanghai').dt.strftime('%Y-%m-%d')
    df = df.rename(columns={'free_share': 'tu_circ', 'circ_mv': 'tu_circ_mv'})
    return df[['code', 'tu_date', 'tu_circ', 'tu_circ_mv']]


def fetch_close_prices(targets, day='2026-01-05'):
    """取收盘价（用于重算 curr_float_value）"""
    conn = duckdb.connect(str(db_path()), read_only=True)
    day_ms = int(pd.Timestamp(day, tz='Asia/Shanghai').timestamp() * 1000)
    df = conn.execute(f"""
        SELECT code, close FROM stock_daily
        WHERE time = {day_ms} AND code IN (SELECT unnest($codes))
    """, {'codes': targets}).fetchdf()
    conn.close()
    return df.rename(columns={'close': 'price'})


def main():
    # 10 只关键票（B5 报告涉及的）
    targets = ['002231', '002719', '002808', '002830', '002888',
               '002872', '002898', '002809', '002193', '002200']
    pit_date = '2026-01-04'
    price_day = '2026-01-05'

    print(f"=== D10 三方数据源对比 ===")
    print(f"PIT 日期: {pit_date}（取 <= 此日的最新流通股本）")
    print(f"价格日期: {price_day}\n")

    xt = fetch_xtquant_circulating(targets, pit_date)
    tu = fetch_tushare_circulating(targets, pit_date)
    px = fetch_close_prices(targets, price_day)

    df = xt.merge(tu, on='code', how='left').merge(px, on='code', how='left')

    # 重算 curr_float_value（策略 handle_data 口径：流通股本 × 收盘价）
    df['xt_curr_fv'] = df['xt_circ'] * df['price']  # xtquant 口径
    df['tu_curr_fv'] = df['tu_circ'] * df['price']  # tushare 口径

    # ==== 对比 1：xtquant vs tushare 流通股本差异 ====
    print("=== 对比 1：xtquant vs tushare 流通股本（circulating_capital vs free_share）===")
    df['circ_diff_pct'] = (df['xt_circ'] - df['tu_circ']) / df['tu_circ'] * 100
    valid = df.dropna(subset=['xt_circ', 'tu_circ'])
    print(f"{'票':>8} | {'xt_circ':>14} | {'tu_circ':>14} | {'差异%':>8} | xt日期 | tu日期")
    for _, r in valid.sort_values('circ_diff_pct').iterrows():
        print(f"{r['code']:>8} | {r['xt_circ']:>14,.0f} | {r['tu_circ']:>14,.0f} | "
              f"{r['circ_diff_pct']:>+7.2f}% | {r['xt_date']} | {r['tu_date']}")
    missing_xt = df[df['xt_circ'].isna()]
    if len(missing_xt) > 0:
        print(f"\nxtquant 无数据的票（可能已退市/下架）: {missing_xt['code'].tolist()}")
    if len(valid) > 0:
        print(f"\n流通股本差异统计（xtquant vs tushare）:")
        print(f"  mean = {valid['circ_diff_pct'].mean():+.2f}%")
        print(f"  median = {valid['circ_diff_pct'].median():+.2f}%")
        print(f"  abs median = {valid['circ_diff_pct'].abs().median():.2f}%")
        print(f"  P95 = {valid['circ_diff_pct'].abs().quantile(0.95):.2f}%")
    print()

    # ==== 对比 2：curr_float_value 排名（哪个源更接近 Ptrade）====
    print("=== 对比 2：curr_float_value 排名（流通股本 × 收盘价）===")
    ptrade_picks = {'002719', '002809', '002830', '002888', '002193'}  # Ptrade 选的
    print(f"Ptrade 实际选股: {sorted(ptrade_picks)}")
    print()
    for src, col in [('xtquant', 'xt_curr_fv'), ('tushare', 'tu_curr_fv')]:
        rank_df = df.dropna(subset=[col]).sort_values(col).reset_index(drop=True)
        rank_df['rank'] = range(1, len(rank_df) + 1)
        top5 = set(rank_df.head(5)['code'])
        overlap = len(top5 & ptrade_picks)
        print(f"{src} 口径 top 5 选股: {sorted(top5)}")
        print(f"  与 Ptrade 选股重叠: {overlap}/5")
        print(f"  排名明细:")
        for _, r in rank_df.iterrows():
            tag = '★Ptrade选' if r['code'] in ptrade_picks else ''
            print(f"    {r['rank']:>2}. {r['code']} curr_fv={r[col]/1e8:.2f}亿 {tag}")
        print()

    # ==== 对比 3：结论 ====
    print("=" * 60)
    print("=== D10 三方对比结论 ===")
    if len(valid) >= 5:
        abs_med = valid['circ_diff_pct'].abs().median()
        print(f"xtquant vs tushare 流通股本 |差异中位数|: {abs_med:.2f}%")
        if abs_med < 1:
            print("→ 两源数据高度一致（<1%），切换 xtquant 收益有限")
        elif abs_med < 5:
            print("→ 两源存在 1-5% 差异，xtquant 可能更准（交易所直接源）")
        else:
            print("→ 两源差异显著（>5%），需深究哪个更准确")
    print()
    print("关键观察：哪个源的 top5 选股与 Ptrade 重叠更多，就说明 Ptrade 用的是该源")


if __name__ == "__main__":
    main()
