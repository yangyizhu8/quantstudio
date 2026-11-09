# -*- coding: utf-8 -*-
"""PT-01 段 C：四方向条件补齐（段 A/B 未覆盖的剩余可能）

段 A/B 已排除：churn 前置 / ALTER 后加 PK / 跨多向量规模 / 索引列基数 —— 均未复现 FATAL。
本段补四个此前**从未构造**的方向：
  C1 真实类型组合   —— 32 列 VARCHAR/TIMESTAMP/DOUBLE/DECIMAL 混合（段 A/B 全为 DOUBLE）
  C2 分片逐 chunk   —— 模拟 write_passthrough_chunked 逐批覆盖（段 A/B 为单条 INSERT…SELECT）
  C3 多索引并存     —— PK + 非唯一索引同时存在（R13 预言的暴露面；段 A/B 仅有 PK）
  C4 非 DROP+RENAME —— TRUNCATE + INSERT 路径对照（验证该路径不丢 PK、不触发 FATAL）

R10 四关：①判据自校准 ②压力自证 ③每组独立新库 ④臂标注（版本/抛点/invalidated/PK 前后）
"""
import duckdb
import os
import re
import sys
import time
import json

TAG = sys.argv[1] if len(sys.argv) > 1 else "run"
BASE = os.path.join(os.environ.get("TEMP", "."), "pt01c", TAG)
os.makedirs(BASE, exist_ok=True)
NROWS = 50000
CHUNK = 5000          # C2 分片大小
COLS = 32


def ddl_realistic(with_pk):
    """C1：真实类型组合——VARCHAR/TIMESTAMP/DECIMAL/DOUBLE 混合，模拟 stock_minutes 等实表"""
    cols = [
        'code VARCHAR NOT NULL',
        'time TIMESTAMP NOT NULL',
        'open DECIMAL(18,4)',
        'high DECIMAL(18,4)',
        'low DECIMAL(18,4)',
        'close DECIMAL(18,4)',
        'volume BIGINT',
        'amount DOUBLE',
        'pre_close DECIMAL(18,4)',
        'change_ratio DOUBLE',
        'turnover_rate DOUBLE',
        'vwap DOUBLE',
        'trade_count INTEGER',
        'buy_volume BIGINT',
        'sell_volume BIGINT',
        'buy_amount DOUBLE',
        'sell_amount DOUBLE',
        'avg_price DOUBLE',
        'max_price DECIMAL(18,4)',
        'min_price DECIMAL(18,4)',
        'open_interest BIGINT',
        'prev_settle DECIMAL(18,4)',
        'settle DECIMAL(18,4)',
        'trade_date DATE',
        'trade_status VARCHAR',
        'market VARCHAR',
        'exchange VARCHAR',
        'currency VARCHAR',
        'adjust_flag VARCHAR',
        'source_name VARCHAR',
        'ingest_ts TIMESTAMP',
        'freq VARCHAR NOT NULL',
    ]
    assert len(cols) == COLS, f"列数 {len(cols)} != {COLS}"
    if with_pk:
        cols.append('PRIMARY KEY (code, time, freq)')
    return 'CREATE TABLE t(' + ', '.join(cols) + ')'


def insert_sql_realistic(n, tbl, offset=0):
    """真实类型插入：DECIMAL 用 CAST，DATE/TIMESTAMP 用函数"""
    proj = [
        f"'S' || lpad(CAST(i % 5000 AS VARCHAR), 5, '0')",
        "TIMESTAMP '2026-01-01 00:00:00' + to_seconds(CAST(i % 86400 AS BIGINT))",
    ]
    proj += [f"CAST(CAST(i + {j} AS DOUBLE) / 1000 AS DECIMAL(18,4))" for j in range(4, 8)]   # open/high/low/close
    proj += [
        "CAST(i * 100 AS BIGINT)",                                    # volume
        f"CAST(i * 1.5 + {offset} AS DOUBLE)",                        # amount
        "CAST(CAST(i AS DOUBLE) / 1000 AS DECIMAL(18,4))",            # pre_close
        "CAST(i % 100 AS DOUBLE) / 100",                              # change_ratio
        "CAST(i % 1000 AS DOUBLE) / 1000",                            # turnover_rate
        f"CAST(i * 0.9 + {offset} AS DOUBLE)",                        # vwap
        "CAST(i % 1000 AS INTEGER)",                                  # trade_count
        "CAST(i * 50 AS BIGINT)",                                     # buy_volume
        "CAST(i * 45 AS BIGINT)",                                     # sell_volume
        "CAST(i * 0.7 AS DOUBLE)",                                    # buy_amount
        "CAST(i * 0.6 AS DOUBLE)",                                    # sell_amount
        "CAST(i * 0.95 AS DOUBLE)",                                   # avg_price
        "CAST(CAST(i + 9 AS DOUBLE) / 1000 AS DECIMAL(18,4))",        # max_price
        "CAST(CAST(i AS DOUBLE) / 1000 AS DECIMAL(18,4))",            # min_price
        "CAST(i % 500 AS BIGINT)",                                    # open_interest
        "CAST(CAST(i AS DOUBLE) / 1000 AS DECIMAL(18,4))",            # prev_settle
        "CAST(CAST(i + 1 AS DOUBLE) / 1000 AS DECIMAL(18,4))",        # settle
        "DATE '2026-01-01' + CAST(i % 28 AS INTEGER)",                # trade_date
        "'NORMAL'",                                                   # trade_status
        "'SZ'",                                                       # market
        "'SZSE'",                                                     # exchange
        "'CNY'",                                                      # currency
        "'qfq'",                                                      # adjust_flag
        "'mcp'",                                                      # source_name
        "TIMESTAMP '2026-11-09 12:00:00'",                            # ingest_ts
        "'1m'",                                                       # freq
    ]
    assert len(proj) == COLS, f"投影 {len(proj)} != {COLS}"
    return f"INSERT INTO {tbl} SELECT {', '.join(proj)} FROM range({offset}, {offset + n}) r(i)"


def pk_of(c, tbl='t'):
    try:
        return int(c.execute(
            "SELECT count(*) FROM duckdb_constraints() WHERE table_name=? AND constraint_type='PRIMARY KEY'",
            [tbl]).fetchone()[0])
    except Exception:
        return -1


def idx_names(c, tbl='t'):
    try:
        rows = c.execute(
            "SELECT index_name FROM duckdb_indexes() WHERE table_name=?", [tbl]).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def run_case(name, spec):
    """spec: dict(with_pk, use_real_type, chunked, extra_index, path)"""
    db = os.path.join(BASE, f"{name}.duckdb")
    for p in (db, db + '.wal'):
        if os.path.exists(p):
            os.remove(p)
    r = {'case': name, 'version': duckdb.__version__, **spec, 'steps': {},
         'err': None, 'invalidated': False, 'only_deleted': None}
    t0 = time.time()
    c = duckdb.connect(db)
    try:
        # ---- R10-① 判据自校准 ----
        c.execute(ddl_realistic(spec['with_pk']) if spec['use_real_type'] else ddl_realistic(spec['with_pk']))
        pk0 = pk_of(c)
        ok_cal = (pk0 >= (1 if spec['with_pk'] else 0)) and (pk_of(c, 'nope') == 0)
        print(f"    [R10-①] 自校准 PK={pk0} ⇒ {'PASS' if ok_cal else '★FAIL'}")
        if not ok_cal:
            raise RuntimeError('判据自校准失败')

        # ---- 灌数（C2 分片 / 其余单条）----
        t = time.time()
        if spec['chunked']:
            seg = 0
            while seg < NROWS:
                n = min(CHUNK, NROWS - seg)
                c.execute(insert_sql_realistic(n, 't', seg))
                seg += n
            r['steps']['insert_chunked'] = round(time.time() - t, 2)
            print(f"    C2 分片灌数: {NROWS // CHUNK} 批 × {CHUNK} 行 ({r['steps']['insert_chunked']}s)")
        else:
            c.execute(insert_sql_realistic(NROWS, 't', 0))
            r['steps']['insert'] = round(time.time() - t, 2)

        # ---- C3 额外非唯一索引 ----
        if spec['extra_index']:
            t = time.time()
            c.execute('CREATE INDEX idx_code ON t(code)')
            r['steps']['create_index'] = round(time.time() - t, 2)
            print(f"    C3 非唯一索引已建 ({r['steps']['create_index']}s)")

        c.execute('CHECKPOINT')
        n0 = c.execute('SELECT count(*) FROM t').fetchone()[0]
        r['rows_before'] = n0
        r['pk_before'] = pk_of(c)
        r['idx_before'] = idx_names(c)
        print(f"    [R10-②] 压力自证: 行数={n0}/{NROWS}  PK={r['pk_before']}  索引={r['idx_before']}")
        if n0 != NROWS:
            raise RuntimeError(f'压力未施加（{n0} != {NROWS}）')

        # ---- 覆盖（C4 走 TRUNCATE+INSERT，其余 DROP+RENAME）----
        if spec['path'] == 'truncate':
            t = time.time()
            c.execute('TRUNCATE t')
            r['steps']['truncate'] = round(time.time() - t, 2)
            c.execute(insert_sql_realistic(NROWS, 't', 0))
            print(f"    C4 TRUNCATE+INSERT 完成 ({r['steps']['truncate']}s)")
        else:
            c.execute('CREATE TABLE _pt_tmp AS SELECT * FROM t LIMIT 0')
            if spec['chunked']:
                seg = 0
                while seg < n0:
                    n = min(CHUNK, n0 - seg)
                    c.execute(insert_sql_realistic(n, '_pt_tmp', seg))
                    seg += n
            else:
                c.execute(insert_sql_realistic(n0, '_pt_tmp', 0))
            t = time.time()
            c.execute('DROP TABLE t')
            r['steps']['drop'] = round(time.time() - t, 2)
            print(f"    DROP TABLE t 成功 ({r['steps']['drop']}s)")
            t = time.time()
            c.execute('ALTER TABLE _pt_tmp RENAME TO t')
            r['steps']['rename'] = round(time.time() - t, 2)
            print(f"    RENAME 成功 ({r['steps']['rename']}s)")

        r['pk_after'] = pk_of(c)
        r['idx_after'] = idx_names(c)
        r['rows_after'] = c.execute('SELECT count(*) FROM t').fetchone()[0]
        try:
            c.execute('CHECKPOINT'); r['ckpt_after'] = 'ok'
        except Exception as e:
            r['ckpt_after'] = f'{type(e).__name__}:{str(e)[:70]}'
        print(f"    结束后: 行数={r['rows_after']}  PK={r['pk_after']}  索引={r['idx_after']}  ckpt={r['ckpt_after']}")
    except Exception as e:
        msg = str(e)
        r['err'] = f'{type(e).__name__}: {msg[:300]}'
        r['invalidated'] = ('invalidated' in msg.lower())
        m = re.search(r'Only deleted (\d+) out of (\d+) rows', msg)
        if m:
            r['only_deleted'] = m.group(0)
        print(f"    ★ 异常: {r['err'][:240]}")
        if r['only_deleted']:
            print(f"      {r['only_deleted']}  ← 2048 对齐: {int(m.group(2)) % 2048}")
    finally:
        try:
            c.close()
        except Exception:
            pass
    r['total_s'] = round(time.time() - t0, 2)
    return r


CASES = {
    'C1_real_type_pk':    dict(with_pk=True,  use_real_type=True, chunked=False, extra_index=False, path='drop'),
    'C2_chunked_pk':      dict(with_pk=True,  use_real_type=True, chunked=True,  extra_index=False, path='drop'),
    'C3_multi_index':     dict(with_pk=True,  use_real_type=True, chunked=False, extra_index=True,  path='drop'),
    'C4_truncate_path':   dict(with_pk=True,  use_real_type=True, chunked=False, extra_index=False, path='truncate'),
}

print(f'=== PT-01 段 C | duckdb {duckdb.__version__} | {NROWS} 行 × {COLS} 列（真实类型组合）===')
out = []
for name, spec in CASES.items():
    print(f"\n  --- {name} ---")
    out.append(run_case(name, spec))

print('\n  === 小结 ===')
for x in out:
    fate = '★FATAL/invalidated' if x['invalidated'] else ('★其他异常' if x['err'] else '全链通过')
    print(f"    {x['version']:<10} {x['case']:<20} {fate:<18} "
          f"PK {x.get('pk_before')}→{x.get('pk_after')}  索引 {len(x.get('idx_before') or [])}→{len(x.get('idx_after') or [])}  "
          f"行数 {x.get('rows_before')}→{x.get('rows_after')}  耗时={x['total_s']}s")
    if x['only_deleted']:
        print(f"        报错匹配: {x['only_deleted']}")

json.dump(out, open(os.path.join(BASE, f'report_{TAG}.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=2, default=str)
print(f"\n  报告: {os.path.join(BASE, f'report_{TAG}.json')}")
