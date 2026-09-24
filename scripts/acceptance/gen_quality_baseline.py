"""生成 6 表质量基线（R3：基线对比，不拍阈值；适配异构声明）。

输出：config/quality_baseline.json
- 对「已声明库+时间列」的表：采集行数 / 日期覆盖数 / 水位 max（只读）
- 对「未声明（不在本机主库）」的表：如实记 unavailable（**不假设 schema**）
"""
import importlib.util
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
spec = importlib.util.spec_from_file_location("qo", ROOT / "scripts" / "quality_orchestrator.py")
qo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qo)

print(f"主库: {ROOT / 'data' / 'quantstudio.db'}")
tables = {}
for t in qo.HIGH_RISK_TABLES:
    name, db_rel, tcol = t["table"], t["db"], t["time_col"]
    if not db_rel or not tcol:
        tables[name] = {"availability": "unavailable", "reason": t.get("_note", "未声明库/时间列")}
        print(f"  {name:22s} unavailable（{t.get('_note','')}）")
        continue
    dbp = ROOT / db_rel
    if not dbp.exists():
        tables[name] = {"availability": "unavailable", "reason": f"库不存在 {dbp}"}
        print(f"  {name:22s} unavailable（库不存在）")
        continue
    import duckdb  # noqa: E402
    t0 = time.perf_counter()
    try:
        con = duckdb.connect(str(dbp), read_only=True)
        try:
            exists = con.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
                [name]).fetchone()[0]
            if not exists:
                tables[name] = {"availability": "unavailable",
                                "reason": f"库 {dbp.name} 中无该表"}
                print(f"  {name:22s} unavailable（库中无表）")
                continue
            n, nd, mx = con.execute(
                f'SELECT COUNT(*) AS n, COUNT(DISTINCT "{tcol}") AS nd, '
                f'MAX("{tcol}") AS mx FROM "{name}"').fetchone()
        finally:
            con.close()
        dt = time.perf_counter() - t0
        tables[name] = {"availability": "available", "db": db_rel, "time_col": tcol,
                        "row_count": int(n), "n_dates": int(nd),
                        "max_watermark": str(mx), "count_sec": round(dt, 2)}
        print(f"  {name:22s} rows={n:>12,}  dates={nd:>5}  max={mx}  ({dt:.1f}s)")
    except Exception as e:
        tables[name] = {"availability": "error", "reason": f"{type(e).__name__}: {e}"}
        print(f"  {name:22s} error: {type(e).__name__}: {e}")

out = ROOT / "config" / "quality_baseline.json"
payload = {
    "_note": ("quality_orchestrator v0.2 的 6 表行数/水位巡检基线（R3：基线对比，非拍阈值）。"
              "阈值本体见 CONFIG（dates_min 取自 verify_v2_cloud_parity.py 的 "
              "HIGH_RISK_DATES=3；row_delta_tol_pct / watermark_max_lag_days 为对比容差，"
              "可环境变量覆写）。6 表**异构**：可用表记实测三件（行数/日期覆盖/水位），"
              "不可用表明记 unavailable（**不假设 schema、不编造阈值**）。"),
    "generated_at": datetime.now().isoformat(),
    "source": "live DuckDB read-only（按各表声明的 db/time_col）",
    "high_risk_ref": qo._HIGH_RISK_REF,
    "tables": tables,
}
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
avail = sum(1 for v in tables.values() if v.get("availability") == "available")
print(f"\n基线已写: {out}（可用 {avail}/{len(tables)} 表；不可用表如实记 unavailable）")