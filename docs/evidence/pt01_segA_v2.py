# -*- coding: utf-8 -*-
"""PT-01 段 A v2：passthrough DROP+RENAME 全量覆盖链定谳（SQL 批量灌数版）

相对 v1 的改动：灌数由 executemany 改为单条 INSERT…SELECT…FROM range()，
消除 Python 侧逐行开销，使两版本可比（v1 中 1.5.6 在 executemany+PK 上超 30 分钟未完成）。

R10 四关：
  ① 判据自校准：健康库上 PK 检测判据必须 PASS
  ② 压力自证：写入行数 == 期望
  ③ 副作用清理：每组独立新库（调用方每次运行前清 scratch）
  ④ 臂标注：记录 版本 / 抛点步骤 / 是否 invalidated / 各步耗时
"""
import duckdb, os, re, sys, time, json

TAG = sys.argv[1] if len(sys.argv) > 1 else "run"
BASE = os.path.join(os.environ.get("TEMP", "."), "pt01", TAG)
os.makedirs(BASE, exist_ok=True)
NROWS = 50000
COLS = 32          # 2 (code,time) + 29 个 DOUBLE + 1 (freq)
NDOUBLE = COLS - 3

def ddl(with_pk):
    cols = ['code VARCHAR NOT NULL', 'time TIMESTAMP NOT NULL']
    cols += [f'c{i} DOUBLE' for i in range(2, 2 + NDOUBLE)]
    cols += ['freq VARCHAR NOT NULL']
    if with_pk:
        cols.append('PRIMARY KEY (code, time, freq)')
    return 'CREATE TABLE t(' + ', '.join(cols) + ')'

def insert_sql():
    """单条 SQL 批量灌 NROWS 行；code 取自 50 个值（高重复），time 递增"""
    proj = ['\'S\' || lpad(CAST(i % 50 AS VARCHAR), 3, \'0\')',
            "TIMESTAMP '2026-01-01 00:00:00' + to_seconds(CAST(i % 86400 AS BIGINT))"]
    proj += [f'CAST(i + {j} AS DOUBLE)' for j in range(2, 2 + NDOUBLE)]
    proj += ["'1m'"]
    return f"INSERT INTO {{tbl}} SELECT {', '.join(proj)} FROM range({NROWS}) r(i)"

def pk_of(c, tbl='t'):
    try:
        return int(c.execute(
            "SELECT count(*) FROM duckdb_constraints() WHERE table_name=? AND constraint_type='PRIMARY KEY'",
            [tbl]).fetchone()[0])
    except Exception:
        return -1

def run(with_pk):
    db = os.path.join(BASE, f"pk{int(with_pk)}.duckdb")
    for p in (db, db + '.wal'):
        if os.path.exists(p):
            os.remove(p)
    r = {'version': duckdb.__version__, 'with_pk': with_pk, 'steps': {}, 'err': None,
         'err_step': None, 'invalidated': False, 'only_deleted': None}
    t_start = time.time()
    c = duckdb.connect(db)
    try:
        # ---- R10-① 判据自校准 ----
        c.execute(ddl(with_pk))
        pk0 = pk_of(c)
        calib = (pk0 >= (1 if with_pk else 0)) and (pk_of(c, 'nonexistent') == 0)
        r['calib'] = calib
        print(f"    [R10-①] PK 判据自校准: PK={pk0} / 不存在表={pk_of(c,'nonexistent')} ⇒ "
              f"{'PASS' if calib else '★FAIL'}")
        if not calib:
            raise RuntimeError('判据自校准失败，结论作废')

        # ---- 灌数（SQL 批量）+ CHECKPOINT ----
        t = time.time(); c.execute(insert_sql().format(tbl='t'))
        r['steps']['insert'] = round(time.time() - t, 2)
        t = time.time(); c.execute('CHECKPOINT')
        r['steps']['checkpoint'] = round(time.time() - t, 2)
        n0 = c.execute('SELECT count(*) FROM t').fetchone()[0]
        print(f"    [R10-②] 压力自证: 行数={n0}/{NROWS}  insert={r['steps']['insert']}s "
              f"ckpt={r['steps']['checkpoint']}s ⇒ {'PASS' if n0 == NROWS else '★FAIL'}")
        if n0 != NROWS:
            raise RuntimeError('压力未施加充分')
        r['rows_before'] = n0
        r['pk_before'] = pk_of(c)

        # ---- passthrough 全量覆盖链 ----
        c.execute('CREATE TABLE _pt_tmp AS SELECT * FROM t LIMIT 0')
        r['tmp_pk'] = pk_of(c, '_pt_tmp')

        t = time.time(); c.execute(insert_sql().format(tbl='_pt_tmp'))
        r['steps']['insert_tmp'] = round(time.time() - t, 2)
        n_tmp = c.execute('SELECT count(*) FROM _pt_tmp').fetchone()[0]
        print(f"    _pt_tmp: PK={r['tmp_pk']}  行数={n_tmp}  insert={r['steps']['insert_tmp']}s")

        t = time.time(); c.execute('DROP TABLE t')
        r['steps']['drop'] = round(time.time() - t, 2)
        print(f"    DROP TABLE t 成功 ({r['steps']['drop']}s)")

        t = time.time(); c.execute('ALTER TABLE _pt_tmp RENAME TO t')
        r['steps']['rename'] = round(time.time() - t, 2)
        print(f"    RENAME 成功 ({r['steps']['rename']}s)")

        r['pk_after'] = pk_of(c)
        r['rows_after'] = c.execute('SELECT count(*) FROM t').fetchone()[0]
        t = time.time()
        try:
            c.execute('CHECKPOINT'); r['steps']['ckpt_after'] = round(time.time() - t, 2); r['ckpt_after'] = 'ok'
        except Exception as e:
            r['ckpt_after'] = f'{type(e).__name__}:{str(e)[:80]}'
        print(f"    RENAME 后: 行数={r['rows_after']}  PK={r['pk_after']}  CHECKPOINT={r['ckpt_after']}")
    except Exception as e:
        msg = str(e)
        r['err'] = f'{type(e).__name__}: {msg[:280]}'
        r['invalidated'] = ('invalidated' in msg.lower())
        m = re.search(r'Only deleted (\d+) out of (\d+) rows', msg)
        if m:
            r['only_deleted'] = m.group(0)
        print(f"    ★ 异常: {r['err'][:220]}")
        if r['only_deleted']:
            print(f"      {r['only_deleted']}  ← 2048 对齐检查: {m.group(2)}%2048=={int(m.group(2))%2048}")
    finally:
        try:
            c.close()
        except Exception:
            pass
    r['total_s'] = round(time.time() - t_start, 2)
    return r

print(f'=== PT-01 段 A v2 | duckdb {duckdb.__version__} | {NROWS} 行 x {COLS} 列 ===')
out = []
for wpk in (True, False):
    print(f"\n  --- 组: {'带 PK' if wpk else '无 PK'} ---")
    out.append(run(wpk))

print('\n  === 小结 ===')
for x in out:
    fate = f"★抛错({x['err_step'] or '主链'})" if x['err'] else '全链通过'
    print(f"    {x['version']:<10} PK={str(x['with_pk']):<5} {fate:<14} invalidated={x['invalidated']:<5} "
          f"PK {x.get('pk_before')}→{x.get('pk_after')}  行数 {x.get('rows_before')}→{x.get('rows_after')}  "
          f"耗时={x['total_s']}s  {x['steps']}")

json.dump(out, open(os.path.join(BASE, f'report_{TAG}.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=2, default=str)
print(f'  报告: {os.path.join(BASE, f"report_{TAG}.json")}')
