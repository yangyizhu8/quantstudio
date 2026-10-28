# -*- coding: utf-8 -*-
"""PT-01 段 B：passthrough DROP+RENAME 条件补齐（补段 A 未触发的四个候选条件）

段 A 结论：32 列 × 5 万行 × 建表声明 PK × 无 churn ⇒ 四组全通过，未复现 FATAL。
本段补跑四项候选缺条件：
  ① churn 前置：先 UPDATE/DELETE 使索引产生删除标记，再 DROP 原表
  ② PK 后加：建表不声明 PK，用 ALTER TABLE ADD PRIMARY KEY 后加
  ③ 规模：跨多个 2048 向量（20 万行 ≈ 97.6 个向量）
  ④ 真实索引列基数：code 取 5000 个不同值（更接近真实股票数），而非 50

判据（同段 A，R10 四关齐备）：
  · 是否抛 `Failed to delete all rows from index`（记录 N，检查 N%2048）
  · 是否 `database has been invalidated`
  · 抛点步骤
  · RENAME 后 PK 计数（附加观测）
"""
import duckdb, os, re, sys, time, json

TAG = sys.argv[1] if len(sys.argv) > 1 else "run"
BASE = os.path.join(os.environ.get("TEMP", "."), "pt01b", TAG)
os.makedirs(BASE, exist_ok=True)
COLS = 32
NDOUBLE = COLS - 3

SCENARIOS = {
    # 名称: (行数, 是否建表声明PK, churn比例, code基数)
    "S1_churn_50k":        dict(n=50000,  ddl_pk=True,  churn=0.35, codes=5000),
    "S2_alter_pk_50k":     dict(n=50000,  ddl_pk=False, churn=0.0,  codes=5000, alter_pk=True),
    "S3_big_churn_200k":   dict(n=200000, ddl_pk=True,  churn=0.35, codes=5000),
    "S4_big_alter_churn":  dict(n=200000, ddl_pk=False, churn=0.35, codes=5000, alter_pk=True),
}

def ddl(with_pk):
    cols = ['code VARCHAR NOT NULL', 'time TIMESTAMP NOT NULL']
    cols += [f'c{i} DOUBLE' for i in range(2, 2 + NDOUBLE)]
    cols += ['freq VARCHAR NOT NULL']
    if with_pk:
        cols.append('PRIMARY KEY (code, time, freq)')
    return 'CREATE TABLE t(' + ', '.join(cols) + ')'

def insert_sql(n, codes):
    proj = [f"'S' || lpad(CAST(i % {codes} AS VARCHAR), 5, '0')",
            "TIMESTAMP '2026-01-01 00:00:00' + to_seconds(CAST(i % 86400 AS BIGINT))"]
    proj += [f'CAST(i + {j} AS DOUBLE)' for j in range(2, 2 + NDOUBLE)]
    proj += ["'1m'"]
    return f"INSERT INTO {{tbl}} SELECT {', '.join(proj)} FROM range({n}) r(i)"

def pk_of(c, tbl='t'):
    try:
        return int(c.execute(
            "SELECT count(*) FROM duckdb_constraints() WHERE table_name=? AND constraint_type='PRIMARY KEY'",
            [tbl]).fetchone()[0])
    except Exception:
        return -1

def run(name, spec):
    db = os.path.join(BASE, f"{name}.duckdb")
    for p in (db, db + '.wal'):
        if os.path.exists(p):
            os.remove(p)
    r = {'scenario': name, 'version': duckdb.__version__, **spec, 'steps': {},
         'err': None, 'invalidated': False, 'only_deleted': None}
    t0 = time.time()
    c = duckdb.connect(db)
    try:
        # ---- R10-① 判据自校准 ----
        c.execute(ddl(spec['ddl_pk']))
        pk0 = pk_of(c)
        ok_cal = (pk0 >= (1 if spec['ddl_pk'] else 0)) and (pk_of(c, 'nope') == 0)
        print(f"    [R10-①] 自校准 PK={pk0} ⇒ {'PASS' if ok_cal else '★FAIL'}")
        if not ok_cal:
            raise RuntimeError('判据自校准失败')

        # ---- 灌数 ----
        t = time.time(); c.execute(insert_sql(spec['n'], spec['codes']).format(tbl='t'))
        r['steps']['insert'] = round(time.time() - t, 2)
        n0 = c.execute('SELECT count(*) FROM t').fetchone()[0]

        # ---- 条件②：PK 后加 ----
        if spec.get('alter_pk'):
            t = time.time()
            c.execute('ALTER TABLE t ADD PRIMARY KEY (code, time, freq)')
            r['steps']['alter_pk'] = round(time.time() - t, 2)
            print(f"    ALTER ADD PRIMARY KEY 成功 ({r['steps']['alter_pk']}s), PK={pk_of(c)}")

        # ---- 条件①：churn（UPDATE + DELETE 制造索引删除标记）----
        if spec['churn'] > 0:
            nchurn = int(spec['n'] * spec['churn'])
            t = time.time()
            c.execute(f"UPDATE t SET c2 = c2 + 1.0 WHERE code IN (SELECT code FROM t LIMIT {spec['codes']//2})")
            r['steps']['update'] = round(time.time() - t, 2)
            t = time.time()
            c.execute(f"DELETE FROM t WHERE code IN (SELECT code FROM t LIMIT {spec['codes']//5})")
            r['steps']['delete'] = round(time.time() - t, 2)
            n_after_churn = c.execute('SELECT count(*) FROM t').fetchone()[0]
            print(f"    churn 完成: update={r['steps']['update']}s delete={r['steps']['delete']}s "
                  f"行数 {n0}→{n_after_churn}")
            n0 = n_after_churn
        else:
            n_after_churn = n0

        t = time.time(); c.execute('CHECKPOINT'); r['steps']['ckpt'] = round(time.time() - t, 2)
        r['rows_at_cover'] = n0
        r['pk_before'] = pk_of(c)
        print(f"    [R10-②] 压力自证: 覆盖前行数={n0} PK={r['pk_before']} ckpt={r['steps']['ckpt']}s")
        if n0 <= 0:
            raise RuntimeError('压力未施加')

        # ---- 覆盖链 ----
        c.execute('CREATE TABLE _pt_tmp AS SELECT * FROM t LIMIT 0')
        t = time.time(); c.execute(insert_sql(n0, spec['codes']).format(tbl='_pt_tmp'))
        r['steps']['insert_tmp'] = round(time.time() - t, 2)

        t = time.time(); c.execute('DROP TABLE t')
        r['steps']['drop'] = round(time.time() - t, 2)
        print(f"    DROP TABLE t 成功 ({r['steps']['drop']}s)")

        t = time.time(); c.execute('ALTER TABLE _pt_tmp RENAME TO t')
        r['steps']['rename'] = round(time.time() - t, 2)
        print(f"    RENAME 成功 ({r['steps']['rename']}s)")

        r['pk_after'] = pk_of(c)
        r['rows_after'] = c.execute('SELECT count(*) FROM t').fetchone()[0]
        try:
            c.execute('CHECKPOINT'); r['ckpt_after'] = 'ok'
        except Exception as e:
            r['ckpt_after'] = f'{type(e).__name__}:{str(e)[:70]}'
        print(f"    RENAME 后: 行数={r['rows_after']}  PK={r['pk_after']}  ckpt={r['ckpt_after']}")
    except Exception as e:
        msg = str(e)
        r['err'] = f'{type(e).__name__}: {msg[:300]}'
        r['invalidated'] = ('invalidated' in msg.lower())
        m = re.search(r'Only deleted (\d+) out of (\d+) rows', msg)
        if m:
            r['only_deleted'] = m.group(0)
            r['only_deleted_total'] = int(m.group(2))
            r['total_mod_2048'] = int(m.group(2)) % 2048
        print(f"    ★ 异常: {r['err'][:240]}")
        if r['only_deleted']:
            print(f"      {r['only_deleted']}  ⇒ 总行数 %2048 = {r['total_mod_2048']}")
    finally:
        try: c.close()
        except Exception: pass
    r['total_s'] = round(time.time() - t0, 2)
    return r

print(f'=== PT-01 段 B | duckdb {duckdb.__version__} | {len(SCENARIOS)} 场景 ===')
out = []
for name, spec in SCENARIOS.items():
    print(f"\n  --- {name}: 行数={spec['n']} 建表PK={spec['ddl_pk']} churn={spec['churn']} code基数={spec['codes']} ---")
    out.append(run(name, spec))

print('\n  === 小结 ===')
for x in out:
    fate = '★FATAL/invalidated' if x['invalidated'] else ('★其他异常' if x['err'] else '全链通过')
    print(f"    {x['version']:<10} {x['scenario']:<20} {fate:<18} PK {x.get('pk_before')}→{x.get('pk_after')} "
          f"行数 {x.get('rows_at_cover')}→{x.get('rows_after')} 耗时={x['total_s']}s")
    if x['only_deleted']:
        print(f"        报错匹配: {x['only_deleted']} (总行数%2048={x.get('total_mod_2048')})")

json.dump(out, open(os.path.join(BASE, f'report_{TAG}.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=2, default=str)
print(f"\n  报告: {os.path.join(BASE, f'report_{TAG}.json')}")
