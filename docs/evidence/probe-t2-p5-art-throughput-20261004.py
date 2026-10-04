# -*- coding: utf-8 -*-
"""T2 P5 probe: ART / index-delete observation across batch scales.

Measures upsert throughput (rows/h) for a zero-new (all-identical) batch with
the QS_UPSERT_SKIP_IDENTICAL gate OFF vs ON, at scales 50k / 500k / 3.5M, and
records whether any DuckDB ART / index-delete error surfaces.

Read-only w.r.t. production DBs: uses throwaway temp databases only.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline.writers import DuckDBWriter  # noqa: E402

ENV = "QS_UPSERT_SKIP_IDENTICAL"
TABLE = "stock_daily"


def make_df(n):
    code = np.tile(np.array(["600%03d" % i for i in range(1000)], dtype=object),
                   n // 1000 + 1)[:n]
    return pd.DataFrame({
        "code": code,
        "time": np.arange(n, dtype=np.int64) + 1000,
        "close": (np.arange(n, dtype=np.float64) + 1.0) * 0.01,
        "volume": np.ones(n, dtype=np.float64),
        "amount": np.ones(n, dtype=np.float64),
    })


def run_scale(n, tmp):
    df = make_df(n)
    res = {}
    for mode in ("off", "on"):
        if mode == "on":
            os.environ[ENV] = "1"
        else:
            os.environ.pop(ENV, None)
        db = Path(tmp) / ("p5_%s_%d.duckdb" % (mode, n))
        w = DuckDBWriter({"type": "duckdb", "path": str(db)})
        w.write(df, TABLE, "seed")
        err = None
        t0 = time.perf_counter()
        try:
            w.write(df, TABLE, "batch")
        except Exception:
            err = traceback.format_exc()
        dt = time.perf_counter() - t0
        res[mode] = {"seconds": dt,
                     "rows_per_hour": (n / dt * 3600.0) if dt > 0 else float("inf"),
                     "error": err}
        try:
            w.close()
        except Exception:
            pass
    return res


def main():
    scales = [int(x) for x in sys.argv[1:]] or [50000, 500000, 3500000]
    tmp = tempfile.mkdtemp(prefix="t2p5_")
    print("TMPDIR", tmp, flush=True)
    try:
        for n in scales:
            r = run_scale(n, tmp)
            for mode in ("off", "on"):
                d = r[mode]
                print("SCALE=%d MODE=%s seconds=%.3f rows_per_hour=%.0f error=%s"
                      % (n, mode, d["seconds"], d["rows_per_hour"],
                         "YES" if d["error"] else "NO"), flush=True)
                if d["error"]:
                    print("---- error text (first 500 chars) ----", flush=True)
                    print(d["error"][:500], flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()