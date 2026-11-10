# -*- coding: utf-8 -*-
"""E-04 v3 三固件（分钟落点）：EC-2 逐位对拍（含 0 根码反证）/ EXPLAIN / 命中率+净收益。

对拍对象：`DuckDBDataAccess.query_minute_bars_by_count_batch`（分钟表，stock/etf_minutes）。
旧实现 = HEAD(74b4181) 版抽为同包临时兄弟模块（同包 ⇒ 相对 import 可用）。

用法：python agent_workspace/e04_minute_dualcheck.py
"""
import glob
import io
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')
ROOT = r'D:\miniQMT策略实盘\QuantStudio'
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, 'data', 'backup_import_20261101_040935', 'quantstudio.db')
OLD_MOD = os.path.join(ROOT, 'quantstudio', 'backtest', 'providers', '_dda_old_min_tmp.py')
REL = 'quantstudio/backtest/providers/duckdb_data_access.py'

blob = subprocess.run(['git', 'show', 'HEAD:' + REL], cwd=ROOT, capture_output=True, check=True).stdout
io.open(OLD_MOD, 'wb').write(blob)
print(f'[setup] 旧模块抽出 {len(blob)}B（HEAD 版）')

import datetime
import duckdb
import pandas as pd

from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess as NewAcc
from quantstudio.backtest.providers._dda_old_min_tmp import DuckDBDataAccess as OldAcc

TZ = datetime.timezone(datetime.timedelta(hours=8))


def ms(datestr, hh=15, mm=0):
    d = datetime.datetime.strptime(datestr, '%Y-%m-%d')
    return int(datetime.datetime(d.year, d.month, d.day, hh, mm, tzinfo=TZ).timestamp() * 1000)


def main():
    con = duckdb.connect(DB, read_only=True)
    freq_row = con.execute(
        "SELECT freq, COUNT(*) AS n FROM stock_minutes GROUP BY freq ORDER BY n DESC LIMIT 1").fetchone()
    FREQ = freq_row[0]
    print(f'[probe] stock_minutes 主 freq={FREQ!r}（{freq_row[1]} 行）')

    CUT = ms('2026-06-12', 14, 55)
    # 活跃码：cutoff 前 20 个自然日内有该 freq 数据
    active = [r[0] for r in con.execute(
        "SELECT DISTINCT code FROM stock_minutes WHERE freq = ? AND time >= ? AND time <= ? LIMIT 100",
        [FREQ, CUT - 20 * 86_400_000, CUT]).fetchall()]
    # 窗口内 0 根码（V-3 反证必需）：最近 3 天内无该 freq 数据、但 400 天内有过
    # ⇒ span_days=2 的快路径窗口内 0 根 ⇒ 必须走慢路径回退
    zero_root = [r[0] for r in con.execute(
        """SELECT code FROM stock_minutes WHERE freq = ?
           GROUP BY code HAVING MAX(time) < ? AND MAX(time) > ? LIMIT 8""",
        [FREQ, CUT - 3 * 86_400_000, CUT - 400 * 86_400_000]).fetchall()]
    # 不足 count 码（窗口内有数据但 < count 根）：总行数 >0 且 < 240 的码
    short_hist = [r[0] for r in con.execute(
        """SELECT code FROM stock_minutes WHERE freq = ?
           GROUP BY code HAVING COUNT(*) > 0 AND COUNT(*) < 240 LIMIT 6""",
        [FREQ]).fetchall()]
    codes = active + zero_root + short_hist
    print(f'[codes] 活跃 {len(active)} + 窗口内0根 {len(zero_root)} + 历史不足240根 {len(short_hist)} '
          f'= {len(codes)} 码 | cutoff={CUT}')

    new_acc = NewAcc(DB)
    old_acc = OldAcc(DB)

    def run_case(cnt, label):
        t0 = time.perf_counter()
        new_df = new_acc.query_minute_bars_by_count_batch(codes, cnt, '2026-06-12', FREQ,
                                                         fq=None, bar_cutoff_ms=CUT)
        t_new = time.perf_counter() - t0
        t0 = time.perf_counter()
        old_df = old_acc.query_minute_bars_by_count_batch(codes, cnt, '2026-06-12', FREQ,
                                                         fq=None, bar_cutoff_ms=CUT)
        t_old = time.perf_counter() - t0
        pd.testing.assert_frame_equal(new_df, old_df, check_exact=True, check_dtype=True,
                                      check_index_type=True, obj=label)
        # 命中率 / 0 根码 / size-only 漏判（用独立窗口计数，慢路径口径见 V-3）
        per_day = 240
        span_days = max(2, -(-int(cnt) // per_day) + 1)
        lower = CUT - span_days * 86_400_000
        ph = ', '.join(['?'] * len(codes))
        w = dict((r[0], int(r[1])) for r in con.execute(
            f"SELECT code, COUNT(*) FROM (SELECT code FROM stock_minutes "
            f"WHERE freq = ? AND time >= ? AND time <= ? AND code IN ({ph}) "
            f"QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) <= ?) GROUP BY code",
            [FREQ, lower, CUT] + codes + [cnt]).fetchall())
        hit = [c for c in codes if w.get(c, 0) >= cnt]
        zero_w = [c for c in codes if w.get(c, 0) == 0]
        short_w = [c for c in codes if 0 < w.get(c, 0) < cnt]
        # size-only 判据（只看快路径结果集）会漏掉的码 = 窗口内 0 根码中原本有历史数据的
        miss_by_size = [c for c in zero_w if c in set(old_df["code"].unique())]
        print(f'  {label}: 逐位全等 ✓ 行数={len(old_df)} | new {t_new*1000:.0f}ms vs old {t_old*1000:.0f}ms '
              f'({t_old/max(t_new,1e-9):.2f}x) | 命中 {len(hit)}/{len(codes)}={len(hit)/len(codes)*100:.1f}% '
              f'| 慢 0根={len(zero_w)} 不足={len(short_w)} | size-only 漏判={len(miss_by_size)}')
        return dict(hit=len(hit), total=len(codes), t_new=t_new, t_old=t_old,
                    zero=len(zero_w), short=len(short_w), miss=len(miss_by_size))

    print('[EC-2 逐位对拍 + 命中率/净收益]')
    res = []
    for cnt, label in ((1, 'count=1 竞价场景'), (30, 'count=30'), (240, 'count=240')):
        res.append((label, run_case(cnt, label)))

    print('\n[EXPLAIN ANALYZE 对比（stock_minutes，活跃码子集）]')
    sub = active[:80]
    ph = ', '.join(['?'] * len(sub))
    cnt = 30
    span_days = max(2, -(-cnt // 240) + 1)
    lower = CUT - span_days * 86_400_000
    sql_old = (f"SELECT * FROM (SELECT code, time FROM stock_minutes WHERE freq = ? AND time <= ? "
               f"AND code IN ({ph}) QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) <= {cnt})")
    sql_new = (f"SELECT * FROM (SELECT code, time FROM stock_minutes WHERE freq = ? AND time >= ? AND time <= ? "
               f"AND code IN ({ph}) QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) <= {cnt})")
    t0 = time.perf_counter(); con.execute('EXPLAIN ANALYZE ' + sql_old, [FREQ, CUT] + sub).fetchall()
    t_o = time.perf_counter() - t0
    t0 = time.perf_counter(); con.execute('EXPLAIN ANALYZE ' + sql_new, [FREQ, lower, CUT] + sub).fetchall()
    t_n = time.perf_counter() - t0
    print(f'  {len(sub)} 码 count={cnt}: 修前(无下界) {t_o:.3f}s → 修后(带下界 {span_days}天) {t_n:.3f}s '
          f'({t_o/max(t_n,1e-9):.1f}x)')

    print('\n[汇总] 判定顺序：① 逐位等价（必须过）→ ② 净收益 >1 → ③ 才入库')
    ok_eq = True
    for label, r in res:
        ratio = r["t_old"] / max(r["t_new"], 1e-9)
        print(f'  {label}: 等价 ✓ | 净收益 {ratio:.2f}x {"(>1 通过)" if ratio > 1 else "(≤1 负收益，须回退)"}')
        ok_eq = ok_eq and ratio > 1
    print('VERDICT:', '可入库（等价 + 净收益 >1）' if ok_eq else '等价通过但净收益不足 ⇒ 按 §8.1 回退不入库')
    con.close()
    new_acc.close() if hasattr(new_acc, 'close') else None


if __name__ == '__main__':
    try:
        main()
    finally:
        if os.path.exists(OLD_MOD):
            os.remove(OLD_MOD)
            print('[cleanup] 临时旧模块已删除')
