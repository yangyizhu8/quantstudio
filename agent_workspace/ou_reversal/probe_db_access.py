
import sys, os, json
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
import duckdb

cands = [
    r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db",
    r"D:\miniQMT策略实盘\QuantStudio\agent_workspace\backtest_readonly\quantstudio.db",
    r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio_backup_20260912.db",
]
res = []
for p in cands:
    info = {"path": p, "exists": os.path.exists(p)}
    if info["exists"]:
        info["size_gb"] = round(os.path.getsize(p) / 1024**3, 2)
    try:
        con = duckdb.connect(p, read_only=True)
        n = con.execute("select count(*) from information_schema.tables").fetchone()[0]
        info["open_ro"] = "OK"
        info["tables"] = n
        con.close()
    except Exception as e:
        info["open_ro"] = "FAIL: " + str(e)[:200]
    res.append(info)
print(json.dumps(res, ensure_ascii=False, indent=1))
