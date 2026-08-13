import duckdb, os

SRC = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
DST = r"D:\miniQMT策略实盘\QuantStudio\skills\agent_workspaces\etf_theme_rotation\quantstudio_copy.db"
if os.path.exists(DST):
    os.remove(DST)

con = duckdb.connect(DST)
# 只读 ATTACH 被锁的源库（不请求写锁）
con.execute(f"ATTACH '{SRC}' AS src (READ_ONLY)")
tables = [r[0] for r in con.execute(
    "SELECT table_name FROM information_schema.tables WHERE table_schema='src'"
).fetchall()]
print("source tables:", tables)
for t in tables:
    con.execute(f'CREATE TABLE "{t}" AS SELECT * FROM src."{t}"')
con.execute("DETACH src")
con.close()
print("CLONE DONE, tables=", len(tables))
