"""B-5 deterministic snapshot evidence tests."""
from __future__ import annotations

import duckdb

from quantstudio.pipeline.qfq_snapshot_evidence import database_evidence, table_evidence


def test_snapshot_evidence_is_order_independent_and_null_stable():
    a = duckdb.connect(":memory:")
    b = duckdb.connect(":memory:")
    for c in (a, b):
        c.execute("CREATE TABLE t(code VARCHAR, time BIGINT, value DOUBLE, note VARCHAR)")
    a.executemany("INSERT INTO t VALUES (?,?,?,?)", [("b", 2, 1.25, None), ("a", 1, 2.5, "x")])
    b.executemany("INSERT INTO t VALUES (?,?,?,?)", [("a", 1, 2.5, "x"), ("b", 2, 1.25, None)])
    ea = table_evidence(a, "t")
    eb = table_evidence(b, "t")
    assert ea["content_sha256"] == eb["content_sha256"]
    assert ea["row_count"] == 2 and ea["min_time"] == "1" and ea["max_time"] == "2"
    assert database_evidence(a, ["t"])["manifest_sha256"] == database_evidence(b, ["t"])["manifest_sha256"]
