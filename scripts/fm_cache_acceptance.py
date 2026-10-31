"""方案② 验收：单次 task 域审计（供 PowerShell 循环成独立进程调用）。

全 RO 加固：主库 read_only=True + sqlite 强制 mode=ro（与 scripts/audit_section_profile.py 同法）。
输出：一行 JSON（stdout），含耗时 / key 前 8 位 / 是否 fm_cached / WAL 尺寸 / 条目指纹。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

# --- RO 加固（必须在 import quality_audit 之前）---
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


sqlite3.connect = _ro  # type: ignore[assignment]

from quantstudio.pipeline.quality_audit import DataQualityAuditor  # noqa: E402

DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
AUX = r"D:\miniQMT策略实盘\QuantStudio\data\qfq_aux.db"


def _wal_size() -> int:
    p = Path(AUX + "-wal")
    try:
        return p.stat().st_size
    except OSError:
        return -1


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    cache_off = str(os.environ.get("QS_AUDIT_FM_CACHE", "1")).strip() == "0"

    key = None
    try:
        key = DataQualityAuditor._fm_cache_key(AUX)
    except Exception:
        pass

    aud = DataQualityAuditor(DB, {}, tables=["etf_daily"])
    t0 = time.perf_counter()
    rep = aud.run()
    dt = time.perf_counter() - t0

    checks = {}
    for it in rep.issues:
        checks[it.check] = checks.get(it.check, 0) + 1
    # 条目指纹（用于逐位一致判定；排序后取元组）
    fp = sorted((i.check, i.table, int(i.count), i.severity) for i in rep.issues
                if i.check not in ("fm_cached", "drift_cached"))
    rec = {
        "label": label,
        "cache": "off" if cache_off else "on",
        "elapsed_s": round(dt, 2),
        "key8": (key or "")[:8],
        "fm_cached": "fm_cached" in checks,
        "drift_cached": "drift_cached" in checks,
        "issues": len(rep.issues),
        "wal_bytes": _wal_size(),
        "fp_sha": __import__("hashlib").sha256(
            json.dumps(fp, ensure_ascii=False).encode()).hexdigest()[:16],
        "fm_issues": [(i.check, i.table, i.count) for i in rep.issues
                      if i.check in ("FactorMonotonicity", "fm_cached")],
    }
    print(json.dumps(rec, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
