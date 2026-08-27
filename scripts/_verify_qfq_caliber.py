"""验证 000001.SZ 除权日 2026-06-12 的 daily/minute/因子口径"""
import duckdb

DBS = ['data/quantstudio.db', 'data/quantstudio.20260807T041035.db',
       'data/qfq_aux.db', 'data/qfq_aux_mcp_gen1.db']

for db in DBS:
    try:
        con = duckdb.connect(db, read_only=True)
        tabs = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables").fetchall()]
        print(f"{db} -> {tabs}")
        con.close()
    except Exception as e:
        print(f"{db} -> ERROR {e}")
