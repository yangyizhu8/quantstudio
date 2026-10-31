"""审计分段计时 + A2b 冷/热验证（**强制只读**加固版）。

## 为什么需要加固（2026-10-31 事故）

`DataQualityAuditor.run()` 对**主库**用 `duckdb.connect(read_only=True)`（合规），
但对 **aux 库 / batch_audit / quarantine** 用 `sqlite3.connect(...)`（**默认 RW**）：
- `quality_audit.py:381` batch_audit_path（RW）
- `quality_audit.py:420/435` quarantine_path（RW）
- `quality_audit.py:801/1159` **aux_path（RW，11.6GB）**

⇒ 标称「只读验证」实际持有 aux 库 RW 句柄，与 daemon 的 3A 写锁竞争
（2026-10-31 事故因果链第一环）。

## 加固手段（脚本层，不改产品代码）

monkey-patch `sqlite3.connect`：对**已存在的 .db 文件**强制 `mode=ro`（URI）。
若审计路径试图写入，会立刻抛 `OperationalError: attempt to write a readonly database`
——**把「偷偷 RW」变成「显式失败」**，这才是验证脚本该有的形态。

## 三类产出

1. **A 分段计时**：task 冷/热 + full 热，逐段耗时表（用户裁定 A）；
2. **C 项 A2b 验证**：task 冷（落缓存）→ task 热（命中 `drift_cached`）两列并排；
3. **RO 自证**：若任何段尝试写库，脚本立即失败并报出段名。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# RO 加固：必须在 import quality_audit **之前**打好补丁
# ---------------------------------------------------------------------------
_ORIG_SQLITE_CONNECT = sqlite3.connect


def _ro_sqlite_connect(database, *args, **kwargs):
    """已存在的 .db 文件一律以 URI 只读打开；其余原样透传。"""
    try:
        if isinstance(database, (str, bytes, os.PathLike)) and not isinstance(database, bytes):
            s = os.fspath(database)
            if isinstance(s, str) and s.lower().endswith(".db") and os.path.exists(s):
                if "uri" in kwargs:
                    kwargs.pop("uri")
                uri = f"file:{Path(s).as_posix()}?mode=ro"
                return _ORIG_SQLITE_CONNECT(uri, uri=True, *args, **kwargs)
    except Exception:
        pass
    return _ORIG_SQLITE_CONNECT(database, *args, **kwargs)


sqlite3.connect = _ro_sqlite_connect  # type: ignore[assignment]

from quantstudio.pipeline.quality_audit import DataQualityAuditor as DQA  # noqa: E402

DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
OUT = Path(r"D:\miniQMT策略实盘\QuantStudio\data\logs\audit_section_profile.json")

METHODS = [
    "run",
    "_audit_batch_pipeline",
    "_audit_quarantine",
    "_audit_schema_constraint",
    "_audit_prices",
    "_audit_frequency",
    "_audit_future_and_pit",
    "_audit_watermarks",
    "_audit_qfq_orchestration",
    "_audit_minute_anchor_drift",
    "_audit_factor_monotonicity",
]

TIMES: dict[str, float] = {}
CNT: dict[str, int] = {}


def install_hooks() -> None:
    for name in METHODS:
        fn = getattr(DQA, name, None)
        if fn is None or getattr(fn, "_qs_timed", False):
            continue

        def make(f, n):
            def wrapper(self, *a, **kw):
                t0 = time.perf_counter()
                try:
                    return f(self, *a, **kw)
                finally:
                    dt = time.perf_counter() - t0
                    TIMES[n] = TIMES.get(n, 0.0) + dt
                    CNT[n] = CNT.get(n, 0) + 1

            wrapper._qs_timed = True
            wrapper.__name__ = n
            return wrapper

        setattr(DQA, name, make(fn, name))


def run_case(label: str, tables, cache: bool) -> dict:
    TIMES.clear()
    CNT.clear()
    if cache:
        os.environ.pop("QS_AUDIT_DRIFT_CACHE", None)
    else:
        os.environ["QS_AUDIT_DRIFT_CACHE"] = "0"
    aud = DQA(DB, {}, tables=tables)
    t0 = time.perf_counter()
    rep = aud.run()
    total = time.perf_counter() - t0
    checks = {}
    for it in rep.issues:
        checks[it.check] = checks.get(it.check, 0) + 1
    rec = {
        "label": label,
        "scope": "task(etf_daily)" if tables else "full(all)",
        "cache": "on" if cache else "off",
        "wall_s": round(total, 2),
        "issues": len(rep.issues),
        "drift_cached": "drift_cached" in checks,
        "sections": {k: round(v, 2) for k, v in sorted(TIMES.items(), key=lambda x: -x[1])},
        "counts": dict(sorted(CNT.items())),
    }
    print(f"\n[{label}] scope={rec['scope']} cache={rec['cache']} wall={rec['wall_s']}s "
          f"issues={rec['issues']} drift_cached={rec['drift_cached']}", flush=True)
    for k, v in rec["sections"].items():
        pct = 100.0 * v / max(rec["wall_s"], 0.001)
        print(f"    {k:<32} {v:>9.2f}s  {pct:>5.1f}%  (n={CNT.get(k, 0)})", flush=True)
    return rec


def main() -> None:
    install_hooks()
    print("=== RO 加固已生效：sqlite3.connect 对存在的 .db 强制 mode=ro ===", flush=True)
    # 支持分组执行（避免单次运行超过执行器时限）：QS_PROFILE_CASES="task-cold,task-warm"
    want = str(os.environ.get("QS_PROFILE_CASES", "task-cold,task-warm,full-warm") or "").strip()
    wanted = {x.strip() for x in want.split(",") if x.strip()}
    print(f"=== 本次执行组: {sorted(wanted)} ===", flush=True)
    cases = []
    plan = [
        ("task-cold", ["etf_daily"], False),
        ("task-warm", ["etf_daily"], True),
        ("full-warm", None, True),
    ]
    for label, tables, cache in plan:
        if label in wanted:
            cases.append(run_case(label, tables, cache))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    prev = []
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            if not isinstance(prev, list):
                prev = []
        except Exception:
            prev = []
    merged = {c.get("label"): c for c in prev if isinstance(c, dict)}
    for c in cases:
        merged[c["label"]] = c
    OUT.write_text(json.dumps(list(merged.values()), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写入 {OUT}（累计 {len(merged)} 组）", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
