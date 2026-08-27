"""Phase 1：QFQ front 列破坏范围扫描（只读，ATTACH + SQL JOIN 高效版）。"""
import duckdb

con = duckdb.connect('data/quantstudio.db', read_only=True)
con.execute("ATTACH 'data/qfq_aux.db' AS aux (READ_ONLY)")

def scan(table, aux_table, src_col='data_source'):
    print(f"\n{'='*60}\n扫描 {table}（因子: {aux_table}）\n{'='*60}")
    # 全局最新因子（每 code）
    # 该日因子（<= 行时间的最近因子）——用 ASOF JOIN
    # 破坏判定：相对容差 |front - close*ai/al|/front > 1e-6，且 ai≠al（有除权）
    q = f"""
    WITH latest AS (
        SELECT code, adj_factor AS adj_latest FROM (
            SELECT code, adj_factor,
                   ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) rn
            FROM aux.{aux_table}
        ) WHERE rn = 1
    ),
    day_factor AS (
        SELECT code, time, adj_factor FROM aux.{aux_table}
    )
    SELECT
        m.code,
        date_trunc('month', to_timestamp(m.time/1000)) AS month,
        m.close, m.close_front,
        af.adj_factor AS adj_i, lt.adj_latest,
        CASE WHEN m.{src_col} IS NOT NULL THEN m.{src_col} ELSE 'unknown' END AS src
    FROM main.{table} m
    JOIN latest lt ON m.code = lt.code
    ASOF JOIN day_factor af
        ON m.code = af.code AND m.time >= af.time
    WHERE m.close_front IS NOT NULL AND m.close > 0
      AND af.adj_factor IS NOT NULL
      AND ABS(af.adj_factor - lt.adj_latest) / lt.adj_latest > 1e-9  -- 该日因子≠最新（有除权差）
      AND ABS(m.close_front - m.close * af.adj_factor / lt.adj_latest)
          / NULLIF(ABS(m.close_front), 0) > 1e-6  -- front 偏离正确值
    """
    try:
        bad = con.execute(q).fetchdf()
    except Exception as e:
        print(f"ERR: {str(e)[:150]}")
        return None
    n = len(bad)
    print(f"破坏行数: {n}")
    if n:
        print(f"受影响 code: {bad['code'].nunique()}")
        print(f"\n按月分布:\n{bad.groupby(bad['month'].dt.strftime('%Y-%m')).size().to_string()}")
        print(f"\n按 source 分布:\n{bad.groupby('src').size().to_string()}")
        print(f"\n样本（前 5）:")
        print(bad.head(5).to_string(index=False))
        # 保存明细
        bad.to_csv(f'data/logs/front_corruption_{table}.csv', index=False)
        print(f"\n明细已存 data/logs/front_corruption_{table}.csv")
    return n

r = {}
r['etf_daily'] = scan('etf_daily', 'fund_adj')
r['etf_minutes'] = scan('etf_minutes', 'fund_adj')
r['stock_daily'] = scan('stock_daily', 'adj_factor')
r['stock_minutes'] = scan('stock_minutes', 'adj_factor')

print(f"\n{'='*60}\n汇总\n{'='*60}")
total = 0
for tbl, n in r.items():
    print(f"{tbl}: {n if n is not None else 'ERR'} 行破坏")
    if n: total += n
print(f"合计: {total} 行")
con.close()
