import sqlite3, os, time, json
from pathlib import Path
_ORIG = sqlite3.connect
def _ro(database, *a, **kw):
    try:
        if isinstance(database, (str, os.PathLike)) and not isinstance(database, bytes):
            s = os.fspath(database)
            if isinstance(s, str) and s.lower().endswith(".db") and os.path.exists(s):
                kw.pop("uri", None)
                return _ORIG(f"file:{Path(s).as_posix()}?mode=ro", uri=True, *a, **kw)
    except Exception:
        pass
    return _ORIG(database, *a, **kw)
sqlite3.connect = _ro
from quantstudio._paths import db_path, DATA_ROOT, quarantine_db_path
from quantstudio.pipeline.quality_audit import DataQualityAuditor
from pathlib import Path as P
root = P(__file__).resolve().parents[1] if "__file__" in dir() else P(r"D:\miniQMT策略实盘\QuantStudio")
aud = DataQualityAuditor.from_config(
    db_path(), root / "config" / "profiles" / "mcp_only" / "alignment_rules.json",
    batch_audit_path=DATA_ROOT / "batch_audit.db",
    quarantine_path=quarantine_db_path())
t0 = time.perf_counter()
rep = aud.run()
dt = time.perf_counter() - t0
checks = {}
for it in rep.issues:
    checks[it.check] = checks.get(it.check, 0) + it.count
print(json.dumps({
    "method": "from_config", "wall_s": round(dt, 1), "tables_audited": len(getattr(aud, "schemas", {}) or {}) or None,
    "issues": len(rep.issues), "checks_run": rep.checks_run,
    "drift_cached": "drift_cached" in checks, "fm_cached": "fm_cached" in checks,
    "top_checks": dict(sorted(checks.items(), key=lambda x: -x[1])[:6]),
}, ensure_ascii=False), flush=True)
print("DONE", flush=True)