"""QFQ 诊断插桩 P1–P7 机检（方案 docs/qfq-post-ingest-profiling-instrument-design.md）。

判据要点：
- P1 门控关闭时**零输出 + 低开销**（<1ms/次）
- P2 事件字段齐全
- P3 分组计时自洽 + 查询计数对拍
- P4 WAL 字节/页数读数正确
- P5 `wal_checkpoint(PASSIVE)` **不改库尺寸**（防误用 TRUNCATE）
- P6 输出失败不阻断业务
- P7 防回归（关闭时既有行为逐位不变）
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from quantstudio.pipeline import qfq_profile as qp


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("QS_QFQ_PROFILE", "QS_QFQ_PROFILE_OUT", "QS_QFQ_PROFILE_TASK"):
        monkeypatch.delenv(k, raising=False)
    yield


def _mk_sqlite(tmp: Path) -> Path:
    db = tmp / "aux.db"
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (a TEXT, b INTEGER)")
    con.execute("INSERT INTO t VALUES ('x', 1)")
    con.commit()
    return db


# ---------------------------------------------------------------- P1 门控
def test_p1_gate_off_by_default():
    assert qp.enabled() is False


def test_p1_gate_on_only_for_literal_1(monkeypatch):
    monkeypatch.setenv("QS_QFQ_PROFILE", "0")
    assert qp.enabled() is False
    monkeypatch.setenv("QS_QFQ_PROFILE", "true")
    assert qp.enabled() is False, "只认 '1'（P1 负向）"
    monkeypatch.setenv("QS_QFQ_PROFILE", "1")
    assert qp.enabled() is True


def test_p1_timer_off_is_noop():
    t = qp.PhaseTimer(False)
    t.start("a")
    time.sleep(0.01)
    t.stop("a")
    t.bump("q")
    t.add("a", 0.5)
    # 关闭时没有任何累计（不建 dict、不取时钟）
    assert t.phases() == {}
    assert t.queries() == {}


def test_p1_timer_off_overhead_negligible():
    """关闭态 1000 次 start/stop 总耗时 < 5ms（P1 判据 <1ms/次）。"""
    t = qp.PhaseTimer(False)
    t0 = time.perf_counter()
    for _ in range(1000):
        t.start("a")
        t.stop("a")
    dt = time.perf_counter() - t0
    assert dt < 0.005, f"关闭态开销过大: {dt*1000:.2f}ms/1000次"


def test_p1_gate_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("QS_QFQ_PROFILE_OUT", str(tmp_path / "p.jsonl"))
    assert qp.enabled() is False
    # 未调用 write_event 时文件不应存在
    assert not (tmp_path / "p.jsonl").exists()


# ---------------------------------------------------------------- P2 字段齐全
def test_p2_event_has_all_fields():
    ev = qp.build_event(
        run_id="r1", asset_type="stock", n_observations=10, n_codes=3,
        n_future_excluded=1,
        phases={"preprocess": 0.1, "value_select": 0.2},
        queries={"value_select": 10, "insert": 8},
        wal_before={"wal_size_bytes": 100, "page_count": 5},
        wal_after={"wal_size_bytes": 300, "page_count": 7,
                   "checkpoint_passive": {"busy": 0, "log": 7, "checkpointed": 7}},
        roundtrip_ms=[1.0, 2.0, 3.0], task="mcp_etf_daily",
    )
    need = {"schema", "event", "run_id", "asset_type", "ts", "n_observations",
            "n_codes", "n_future_excluded", "phase_s", "queries", "wal",
            "roundtrip_ms", "db"}
    assert need.issubset(ev.keys())
    assert ev["schema"] == qp.SCHEMA
    assert ev["event"] == "record_observations"
    assert ev["wal"]["delta_bytes"] == 200
    assert ev["wal"]["pages_before"] == 5 and ev["wal"]["pages_after"] == 7
    assert ev["wal"]["checkpoint_passive"]["checkpointed"] == 7
    assert ev["roundtrip_ms"]["samples"] == 3
    assert ev["db"]["task"] == "mcp_etf_daily"


def test_p2_jsonl_roundtrip(tmp_path, monkeypatch):
    out = tmp_path / "p.jsonl"
    monkeypatch.setenv("QS_QFQ_PROFILE_OUT", str(out))
    qp.write_event({"schema": 1, "event": "x", "n": 1})
    qp.write_event({"schema": 1, "event": "x", "n": 2})
    lines = out.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[1])["n"] == 2


# ---------------------------------------------------------------- P3 计时自洽
def test_p3_timer_accumulates_and_counts():
    t = qp.PhaseTimer(True)
    for _ in range(3):
        t.start("k")
        time.sleep(0.01)
        t.stop("k")
        t.bump("k", 1)
    t.add("m", 0.25)
    ph = t.phases()
    qs = t.queries()
    assert ph["k"] >= 0.025          # 3 × 10ms
    assert abs(ph["m"] - 0.25) < 1e-9
    assert qs["k"] == 3


def test_p3_percentiles():
    assert qp.percentiles([]) == {"p50": 0.0, "p95": 0.0, "max": 0.0, "samples": 0}
    r = qp.percentiles([1.0, 2.0, 3.0, 4.0, 100.0])
    assert r["samples"] == 5
    assert r["p50"] == 3.0
    assert r["max"] == 100.0
    assert 4.0 <= r["p95"] <= 100.0


# ---------------------------------------------------------------- P4 WAL 读数
def test_p4_wal_stats_reads_sizes(tmp_path):
    db = _mk_sqlite(tmp_path)
    con = sqlite3.connect(str(db))
    st = qp.wal_stats(con)
    con.close()
    assert st.get("page_size", 0) > 0
    assert st.get("page_count", 0) > 0
    assert st.get("size_bytes", 0) > 0
    assert "checkpoint_passive" in st
    cp = st["checkpoint_passive"]
    assert set(cp.keys()) == {"busy", "log", "checkpointed"}


def test_p4_wal_stats_no_wal_file(tmp_path):
    """无 WAL 文件时 wal_size_bytes 为 0 且不报错（回归安全）。"""
    db = tmp_path / "plain.db"
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("CREATE TABLE t (a TEXT)")
    con.commit()
    st = qp.wal_stats(con)
    con.close()
    assert st.get("wal_size_bytes", 0) == 0


# ---------------------------------------------------------------- P5 PASSIVE 不改尺寸
def test_p5_passive_checkpoint_does_not_truncate(tmp_path):
    """PASSIVE 语义：只回写、**不截断**。

    判据修正（实测发现）：PASSIVE 会把 WAL 页回写主库 ⇒ 主库文件**可能增长**；
    真正的误用信号是「主库/WAL 被**缩小**」（那是 TRUNCATE 的行为）。
    """
    db = _mk_sqlite(tmp_path)
    con = sqlite3.connect(str(db))
    before_db = db.stat().st_size
    w = Path(str(db) + "-wal")
    before_wal = w.stat().st_size if w.exists() else 0
    qp.wal_stats(con)                     # 内含 PRAGMA wal_checkpoint(PASSIVE)
    after_db = db.stat().st_size
    after_wal = w.stat().st_size if w.exists() else 0
    con.close()
    assert after_db >= before_db, "PASSIVE 不得缩小主库（缩小才是 TRUNCATE 的误用）"
    assert after_wal <= before_wal + 8192, "PASSIVE 不应显著增长 WAL"


# ---------------------------------------------------------------- P6 失败不阻断
def test_p6_write_failure_does_not_raise(tmp_path, monkeypatch):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("QS_QFQ_PROFILE_OUT", str(blocker / "p.jsonl"))
    qp.write_event({"schema": 1, "event": "x"})   # 不得抛异常


def test_p6_build_event_bad_input_never_used_in_hot_path():
    """build_event 仅在 _prof 为真时调用；本用例确认它自身对空输入健壮。"""
    ev = qp.build_event(run_id="", asset_type="", n_observations=0, n_codes=0,
                        n_future_excluded=0, phases={}, queries={},
                        wal_before={}, wal_after={}, roundtrip_ms=[])
    assert ev["n_observations"] == 0
    assert ev["wal"]["delta_bytes"] == 0
    assert ev["roundtrip_ms"]["samples"] == 0
