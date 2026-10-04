# -*- coding: utf-8 -*-
"""T2 committed-state probe: gate default-off must be byte-identical to baseline.

Intended to run against a *committed* checkout (e.g. a git worktree at the T2
commit), NOT the working tree. Asserts, with QS_UPSERT_SKIP_IDENTICAL unset:
  1) _is_upsert_skip_identical_enabled() is False  (fail-closed default);
  2) the executed upsert SQL contains no 'IS DISTINCT FROM'  (OFF == baseline);
  3) with the gate on, SQL == baseline + ' WHERE (...) IS DISTINCT FROM (...)'.

Read-only w.r.t. production DBs: uses a throwaway temp database only.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline.writers import (  # noqa: E402
    DuckDBWriter, _is_upsert_skip_identical_enabled)

ENV = "QS_UPSERT_SKIP_IDENTICAL"
TABLE = "stock_daily"
COLS = ["code", "time", "close", "volume", "amount", "preClose", "pctChg"]

print("PROBE_ROOT:", ROOT)


class _SpyConn:
    def __init__(self, real):
        self._real = real
        self.sql = []

    def execute(self, query, *a, **k):
        self.sql.append(query)
        return self._real.execute(query, *a, **k)

    def register(self, *a, **k):
        return self._real.register(*a, **k)

    def unregister(self, *a, **k):
        return self._real.unregister(*a, **k)

    def close(self):
        return self._real.close()


def capture(writer, df, batch):
    real = writer._conn
    holder = {}

    def factory():
        s = _SpyConn(real())
        holder["spy"] = s
        return s

    writer._conn = factory
    try:
        writer.write(df, TABLE, batch)
    finally:
        try:
            del writer._conn
        except AttributeError:
            pass
    for q in holder["spy"].sql:
        if isinstance(q, str) and q.startswith("INSERT INTO " + TABLE):
            return q
    raise AssertionError("no upsert SQL captured")


def main():
    tmp = tempfile.mkdtemp(prefix="t2commit_")
    try:
        db = Path(tmp) / "probe.duckdb"
        w = DuckDBWriter({"type": "duckdb", "path": str(db)})
        seed = pd.DataFrame([["600000", 1000, 10.0, 100.0, 1000.0, 9.5, 0.5]], columns=COLS)
        bat = pd.DataFrame([["600000", 1000, 11.0, 100.0, 1000.0, 9.5, 0.5]], columns=COLS)

        os.environ.pop(ENV, None)
        default_off = _is_upsert_skip_identical_enabled() is False
        capture(w, seed.copy(), "seed")
        off_sql = capture(w, bat.copy(), "off_batch")
        os.environ[ENV] = "1"
        on_sql = capture(w, bat.copy(), "on_batch")
        os.environ.pop(ENV, None)
        try:
            w.close()
        except Exception:
            pass

        off_has_where = "IS DISTINCT FROM" in off_sql
        tail = on_sql[len(off_sql):] if on_sql.startswith(off_sql) else ""
        on_appends = bool(tail) and "IS DISTINCT FROM" in tail
        ok = default_off and (not off_has_where) and on_appends

        print("DEFAULT_OFF:", default_off)
        print("OFF_HAS_WHERE:", off_has_where)
        print("ON_APPENDS_WHERE:", on_appends)
        print("OFF_SQL_SHA256:", hashlib.sha256(off_sql.encode()).hexdigest())
        print("OFF_SQL:", off_sql)
        print("ON_SQL:", on_sql)
        print("RESULT:", "PASS" if ok else "FAIL")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()