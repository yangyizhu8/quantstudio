# -*- coding: utf-8 -*-
"""E-04 三固件：EC-2 逐位对拍（含 0 根码反证）/ EXPLAIN 对比 / 快路径命中率。

方法：把 HEAD(74b4181) 版本的 duckdb_data_access.py 抽为同包临时兄弟模块
`_dda_old_rperf05_tmp.py`（同包 ⇒ 相对 import 可用），与工作树新版并排对拍。

- EC-2：逐码 pd.testing.assert_frame_equal(check_exact=True, check_dtype=True,
  check_index_type=True)（含行序/RangeIndex 校验）。
- 0 根码反证：窗口内 0 根的码（长期停牌/次新/退市）必须被慢路径回退并与旧实现逐位相同；
  并显式证明「只用 groupby('code').size() 判定会漏掉它们」。
- EXPLAIN：修前（无下界）vs 修后（带下界）扫描行数与耗时对比。
- 命中率：快路径命中码数/请求码数；慢路径两类（0 根 / 窗口内不足 count）分别计数。

用法：python agent_workspace/e04_dualcheck.py
"""
import io
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

ROOT = r'D:\miniQMT策略实盘\QuantStudio'
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, 'data', 'backup_import_20261101_040935', 'quantstudio.db')
OLD_MOD = os.path.join(ROOT, 'quantstudio', 'backtest', 'providers', '_dda_old_rperf05_tmp.py')
REL = 'quantstudio/backtest/providers/duckdb_data_access.py'

# ---- 抽旧模块（UTF-8，git show 字节流）----
blob = subprocess.run(['git', 'show', 'HEAD:' + REL], cwd=ROOT,
                      capture_output=True, check=True).stdout
io.open(OLD_MOD, 'wb').write(blob)
print(f'[setup] 旧模块已抽出：{len(blob)}B -> {OLD_MOD}')

import duckdb
import pandas as pd

from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess as NewAcc
from quantstudio.backtest.providers._dda_old_rperf05_tmp import DuckDBDataAccess as OldAcc

new_acc = NewAcc(DB)
old_acc = OldAcc(DB)


def ms_of(date_str, hh=15, mm=0):
    import datetime
    d = datetime.datetime.strptime(date_str, '%Y-%m-%d')
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return int(datetime.datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz).timestamp() * 1000)


def pick_codes(con):
    """构造覆盖四类边界的真实码集。"""
    codes = set()
    # 1) 正常活跃码（2026-06 有数据）
    for r in con.execute("""SELECT DISTINCT code FROM stock_daily
                            WHERE time >= 1781000000000 AND time < 1782000000000
                            LIMIT 60""").fetchall():
        codes.add(r[0])
    # 2) 长期停牌/退市码：2025-06 之前有数据、2025-06 之后无数据（窗口内 0 根）
    stale = con.execute("""SELECT code, MAX(time) AS mt FROM stock_daily
                           GROUP BY code HAVING MAX(time) < 1750000000000
                           ORDER BY mt DESC LIMIT 15""").fetchall()
    codes.update(r[0] for r in stale)
    # 3) 次新码：上市晚（最近 120 天内才有数据）
    fresh = con.execute("""SELECT code, MIN(time) AS mint FROM stock_daily
                           GROUP BY code HAVING MIN(time) > 1770000000000
                           LIMIT 15""").fetchall()
    codes.update(r[0] for r in fresh)
    # 4) ETF + 指数代理码
    for r in con.execute("SELECT DISTINCT code FROM etf_daily LIMIT 5").fetchall():
        codes.add(r[0])
    codes.update(['000300', '000905', '000016'])
    return sorted(codes)


def window_rows(con, codes, count, before_ms, lower_ms):
    """独立复刻快路径 SQL，返回 {code: 行数}（用于命中率与 0 根码统计）。"""
    ph = ', '.join(['?'] * len(codes))
    sql = (f"SELECT code, COUNT(*) AS n FROM (SELECT code FROM stock_daily "
           f"WHERE code IN ({ph}) AND time >= ? AND time <= ? "
           f"QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) <= ?) "
           f"GROUP BY code")
    df = con.execute(sql, list(codes) + [lower_ms, before_ms, count]).fetchdf()
    return {r[0]: int(r[1]) for r in df.itertuples(index=False)}


def explain(con, codes, count, before_ms, lower_ms):
    ph = ', '.join(['?'] * len(codes))
    if lower_ms is None:
        sql = (f"SELECT code, time FROM stock_daily WHERE code IN ({ph}) AND time <= ? "
               f"QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) <= ?")
        params = list(codes) + [before_ms, count]
    else:
        sql = (f"SELECT code, time FROM stock_daily WHERE code IN ({ph}) AND time >= ? AND time <= ? "
               f"QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) <= ?")
        params = list(codes) + [lower_ms, before_ms, count]
    t0 = time.perf_counter()
    con.execute(f"EXPLAIN ANALYZE {sql}", params).fetchall()
    return time.perf_counter() - t0


def compare(codes, count, before_ms, use_qfq, con):
    label = f"count={count} before={before_ms} qfq={use_qfq} codes={len(codes)}"
    t0 = time.perf_counter()
    new = new_acc._query_bars_window_sql(list(codes), count, before_ms, use_qfq)
    t_new = time.perf_counter() - t0
    t0 = time.perf_counter()
    old = old_acc._query_bars_window_sql(list(codes), count, before_ms, use_qfq)
    t_old = time.perf_counter() - t0

    # 码集一致性
    assert set(new.keys()) == set(old.keys()), (
        f'{label}: 码集不一致 new-only={set(new)-set(old)} old-only={set(old)-set(new)}')
    # 逐码逐位
    for c in sorted(old.keys()):
        pd.testing.assert_frame_equal(
            new[c], old[c], check_exact=True, check_dtype=True, check_index_type=True,
            obj=f'{label}: code={c}')
    # 命中率与 0 根码统计
    lower_ms = before_ms - 430 * 86_400_000
    wrows = window_rows(con, codes, count, before_ms, lower_ms)
    zero_root = [c for c in codes if wrows.get(c, 0) == 0]          # 窗口内 0 根
    short_nonzero = [c for c in codes if 0 < wrows.get(c, 0) < count]
    hit = [c for c in codes if wrows.get(c, 0) >= count]
    # 0 根码反证：必须被回退且结果与旧实现相同；size-only 判据会漏掉它们
    size_only_short = [c for c in codes if c in new and len(new[c]) < count]
    miss_zero_by_size = [c for c in zero_root if c not in size_only_short]
    zero_ok = all(c in old and len(old[c]) > 0 for c in zero_root if c in old)
    print(f'  {label}')
    print(f'    逐位对拍: {len(old)} 码全等 ✓ | new {t_new*1000:.0f}ms vs old {t_old*1000:.0f}ms '
          f'({t_old/max(t_new,1e-9):.2f}x)')
    print(f'    命中率: {len(hit)}/{len(codes)} = {len(hit)/len(codes)*100:.1f}% | '
          f'慢路径 0根={len(zero_root)} 不足count={len(short_nonzero)}')
    if zero_root:
        print(f'    0 根码反证: {len(zero_root)} 个窗口内 0 根码 —— 慢路径回退后与旧实现逐位相同 '
              f'(其中旧实现有行 {sum(1 for c in zero_root if c in old and len(old[c])>0)} 个) ✓ | '
              f'size-only 判据会漏掉 {len(miss_zero_by_size)} 个 ⚠')
    return dict(hit=len(hit), total=len(codes), zero=len(zero_root),
                short=len(short_nonzero), miss_by_size=len(miss_zero_by_size),
                t_new=t_new, t_old=t_old)


def main():
    con = duckdb.connect(DB, read_only=True)
    codes = pick_codes(con)
    print(f'[codes] 码集 {len(codes)} 个（含活跃/长期停牌/次新/ETF/指数代理）')
    results = []
    cases = [
        (codes, 120, ms_of('2026-06-12'), True),
        (codes, 120, ms_of('2026-06-12'), False),
        (codes, 30, ms_of('2026-03-06'), True),
        (codes, 180, ms_of('2026-08-13'), True),
    ]
    for cs, cnt, bms, qfq in cases:
        results.append(compare(cs, cnt, bms, qfq, con))

    # ---- 生产真实池近似：纯活跃码（2026-06 有数据，无停牌/次新/退市富集）----
    active = [r[0] for r in con.execute("""SELECT DISTINCT code FROM stock_daily
                                           WHERE time >= 1780700000000 AND time < 1781400000000
                                           LIMIT 80""").fetchall()]
    print(f'\n[生产近似码集] 纯活跃 {len(active)} 码（2026-06 有交易数据，无问题码富集）')
    for cnt, bms in ((120, ms_of('2026-06-12')), (180, ms_of('2026-06-12'))):
        results.append(compare(active, cnt, bms, True, con))

    print('\n[EXPLAIN ANALYZE 对比（stock_daily，%d 码）]' % len(codes))
    cnt, bms = 120, ms_of('2026-06-12')
    lower_ms = bms - 430 * 86_400_000
    t_old = explain(con, codes, cnt, bms, None)
    t_new = explain(con, codes, cnt, bms, lower_ms)
    print(f'  修前(无下界): {t_old:.2f}s | 修后(带下界 430 自然日): {t_new:.2f}s '
          f'({t_old/max(t_new,1e-9):.1f}x)')

    print('\n[汇总]')
    for i, r in enumerate(results):
        print(f'  case{i+1}: 命中率 {r["hit"]}/{r["total"]} | 慢 0根={r["zero"]} '
              f'不足={r["short"]} | size-only 漏判={r["miss_by_size"]} | '
              f'new {r["t_new"]*1000:.0f}ms vs old {r["t_old"]*1000:.0f}ms')
    con.close()


if __name__ == '__main__':
    try:
        main()
    finally:
        if os.path.exists(OLD_MOD):
            os.remove(OLD_MOD)
            print(f'[cleanup] 临时旧模块已删除: {OLD_MOD}')
