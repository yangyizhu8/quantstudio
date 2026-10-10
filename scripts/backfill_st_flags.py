# -*- coding: utf-8 -*-
"""D-4 回填与诊断：stock_daily.is_st_reliable / is_st_reliable_source

方案：docs/r1-framework-fix-design-20261009.md（D-4 数据修正型 + 新增检测型）
用法：
  python scripts/backfill_st_flags.py --db <quantstudio.db> --start 2026-08-14 [--dry-run|--apply]

dry-run（默认）：只读复算并输出将更新的行数/区间/样例，不写库。
--apply：仅在 dev 周六库窗内执行（写前须先呈审 dry-run 结果）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
from pathlib import Path

import duckdb

ST_SET = ('ST', '*ST')


def _ms(date_str: str) -> int:
    """日期 → **真 epoch 毫秒**（该日 CST 午夜）。

    库内口径实测（2026-10-09，002743 边界定性）：
      - stock_daily.time / stock_namechange.change_date = **真 epoch**
        （2026-08-14 → 1786694400000 = UTC 08-13 16:00 = CST 08-14 00:00）；
      - stock_minutes.time = **CST 墙钟-as-UTC**（同库内两套口径，勿混用）。
    ⇒ 本函数显式减 8h 得到真 epoch，不依赖本机时区。
    """
    d = _dt.datetime.strptime(date_str, '%Y-%m-%d')
    return int((d - _dt.datetime(1970, 1, 1)).total_seconds() * 1000) - 8 * 3600 * 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--start', default='2026-08-14')
    ap.add_argument('--end', default=None)
    ap.add_argument('--apply', action='store_true', help='真正写库（缺省 dry-run）')
    args = ap.parse_args()
    db = Path(args.db)
    if not db.exists():
        print(f'❌ DB 不存在: {db}')
        return 2
    con = duckdb.connect(str(db), read_only=not args.apply)
    start_ms = _ms(args.start)
    end_ms = _ms(args.end) + 86_399_999 if args.end else None
    where_end = f'AND time <= {end_ms}' if end_ms else ''

    # ---- 诊断 1：namechange 重复键 ----
    dup = con.execute("""
        SELECT COUNT(*) FROM (
          SELECT code, change_date, COUNT(*) c FROM stock_namechange
          GROUP BY 1,2 HAVING COUNT(*) > 1)""").fetchone()[0]
    nc_total = con.execute('SELECT COUNT(*) FROM stock_namechange').fetchone()[0]
    print(f'[诊断] stock_namechange 行数={nc_total} 重复(code,change_date)键={dup}')

    # ---- 复算 PIT（去重后 ASOF：每 (code,time) 取 ≤time 的最近一条）----
    rows = con.execute(f"""
        WITH nc AS (
          SELECT code, change_date,
                 MAX(status_after) AS status_after
          FROM stock_namechange GROUP BY 1,2),
        nc_sorted AS (SELECT code, change_date AS _t, status_after FROM nc)
        SELECT d.code, d.time, d.is_st_reliable AS old_val,
               CASE WHEN r.status_after IN ('ST','*ST') THEN TRUE ELSE FALSE END AS new_val
        FROM stock_daily d
        ASOF LEFT JOIN nc_sorted r
          ON d.code = r.code AND d.time >= r._t
        WHERE d.time >= {start_ms} {where_end}
    """).fetchdf()
    total = len(rows)
    old_true = int(rows['old_val'].fillna(False).sum())
    new_true = int(rows['new_val'].fillna(False).sum())
    # 标签口径：真 epoch → CST 日期（+8h），与库内 trade date 一致
    rows['d'] = rows['time'].apply(
        lambda ms: (_dt.datetime(1970, 1, 1) + _dt.timedelta(milliseconds=int(ms))
                    + _dt.timedelta(hours=8)).strftime('%Y-%m-%d'))
    changed = rows[rows['old_val'].fillna(False) != rows['new_val'].fillna(False)]
    print(f'[复算] 区间行数={total} 现行 True={old_true} 复算 True={new_true} 需更新行数={len(changed)}')
    if total == 0:
        print('（区间无数据，检查 --start/--end 与库口径）')
        return 0
    per_date = rows.groupby('d').agg(n=('code', 'size'), old=('old_val', lambda s: int(s.fillna(False).sum())),
                                    new=('new_val', lambda s: int(s.fillna(False).sum())))
    print('\n[逐日] 日期  行数  现行True  复算True')
    for d, r in per_date.iterrows():
        print(f'  {d}  {int(r["n"]):6d}  {int(r["old"]):6d}  {int(r["new"]):6d}')
    print('\n[样例] 将变更的前 12 行（code/日期/旧→新）:')
    for _, r in changed.head(12).iterrows():
        print(f'  {r["code"]}  {r["d"]}  {bool(r["old_val"])} -> {bool(r["new_val"])}')
    print('\n[定点] 002743（2026-09-08 起 ST）:')
    sub = rows[rows['code'].astype(str) == '002743'].sort_values('time')
    for _, r in sub.iterrows():
        print(f'  {r["d"]}  现行={bool(r["old_val"])}  复算={bool(r["new_val"])}')
    if not args.apply:
        print('\n[dry-run] 未写库。--apply 仅在 dev 周六库窗执行。')
        return 0
    print('\n[apply] 开始写库…')
    con.close()
    rw = duckdb.connect(str(db))
    rw.execute('BEGIN')
    try:
        rw.execute(f"""
            WITH nc AS (SELECT code, change_date, MAX(status_after) AS status_after
                        FROM stock_namechange GROUP BY 1,2)
            UPDATE stock_daily SET
              is_st_reliable = (SELECT CASE WHEN r.status_after IN ('ST','*ST') THEN TRUE ELSE FALSE END
                                FROM nc r WHERE r.code = stock_daily.code AND stock_daily.time >= r.change_date
                                ORDER BY r.change_date DESC LIMIT 1),
              is_st_reliable_source = CASE WHEN (SELECT CASE WHEN r.status_after IN ('ST','*ST') THEN TRUE ELSE FALSE END
                                FROM nc r WHERE r.code = stock_daily.code AND stock_daily.time >= r.change_date
                                ORDER BY r.change_date DESC LIMIT 1) THEN 'namechange' ELSE 'none' END
            WHERE time >= {start_ms} {where_end}""")
        rw.execute('COMMIT')
        print('[apply] 完成')
    except Exception as exc:
        rw.execute('ROLLBACK')
        print(f'[apply] 失败已回滚: {exc}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())