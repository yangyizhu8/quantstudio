"""⑤ 续：核对各库实例的 etf_basic 重复情况（定位「5 重复码」出处）。"""
import glob
from pathlib import Path

import duckdb

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
cands = [ROOT / "data" / "quantstudio.db",
         ROOT / "data" / "quantstudio.old_20260920.db",
         ROOT / "data" / "quantstudio_backup_20260912.db"]
cands += [Path(p) for p in glob.glob(str(ROOT / "data" / "**" / "*.db"), recursive=True)
          if "snapshots" in p or "staging" in p]
cands += [Path(p) for p in glob.glob(str(ROOT / "agent_workspace" / "**" / "quantstudio*.db"),
                                     recursive=True)]

print("=== 各库实例 etf_basic 重复核查 ===")
for p in cands:
    if not p.exists() or p.stat().st_size < 1024:
        continue
    try:
        con = duckdb.connect(str(p), read_only=True)
        try:
            has = con.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name = 'etf_basic'").fetchone()[0]
            if not has:
                print(f"  [无表] {p.relative_to(ROOT)}")
                continue
            n = con.execute('SELECT COUNT(*) FROM "etf_basic"').fetchone()[0]
            nc = con.execute('SELECT COUNT(DISTINCT code) FROM "etf_basic"').fetchone()[0]
            dup = n - nc
            flag = "  ← 命中 5" if dup == 5 else ""
            print(f"  {p.relative_to(ROOT)}: rows={n:,} distinct={nc:,} dup={dup}{flag}")
            if dup:
                d = con.execute(
                    'SELECT code, COUNT(*) c FROM "etf_basic" GROUP BY code '
                    'HAVING c > 1 ORDER BY c DESC, code').fetchall()
                print(f"      重复码: {[ (c, cnt) for c, cnt in d[:10] ]}")
        finally:
            con.close()
    except Exception as e:
        print(f"  [读失败] {p.relative_to(ROOT)}: {type(e).__name__}: {str(e)[:80]}")