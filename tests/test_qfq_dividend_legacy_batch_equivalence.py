"""件二③：_scan_dividend_legacy 集合化迁移的等价性/原子性/SQL 条数测试（纯性能型硬门）。

规格来源：docs/design-dividend-legacy-set-migration-20261105.md（§2.b IN 分批 1000、
§4.3 单事务整批原子——②审已裁）+ 派单令 E1-E5 等价性硬门。

结构：
- ``_scan_dividend_legacy_reference``：git HEAD（集合化改动前）生产实现的**逐字拷贝**，
  作为逐行参考基准（E1/E2/E3/E5 用）；冻结不动，随生产演进永远保留旧语义。
- 等价性测试对「改动前生产码」应全绿（参考 vs 自身）；
- E4 原子性 + SQL 条数契约两测试对旧实现**必红**（TDD RED），集合化实施后转绿。

运行（回归命令示例）：
    python -m pytest tests/test_qfq_dividend_legacy_batch_equivalence.py -v
    python tests/test_qfq_dividend_legacy_batch_equivalence.py --bench   # 3 轮基准
"""
from __future__ import annotations

import statistics
import time
from typing import List, Optional

import duckdb
import pytest

import quantstudio.pipeline.qfq_event_discovery as qed
from quantstudio.pipeline.qfq_event_discovery import EventDiscovery
from quantstudio.pipeline.qfq_dividend_payload import dividend_payload_hash
from quantstudio.pipeline.qfq_orchestrator_types import (
    QFQOrchestratorConfig,
    TriggerRecord,
    trigger_id_of,
)
from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema

# 固定时间锚（不取墙钟，保证两实现 created_at/updated_at 逐位可比）
SEED_NOW = "2026-01-01 00:00:00"   # 预置"已存在"行的落库时间
RUN_NOW = "2026-11-11 08:00:00"    # 本轮扫描时间
AS_OF_MS = 1_700_000_000_000       # 2023-11-14 划分 pending / scheduled
PAST_MS = 1_600_000_000_000        # < AS_OF → pending
FUTURE_MS = 1_800_000_000_000      # > AS_OF → scheduled
REC_MS = 1_690_000_000_000


def _row(code: str, ex_date: int, cash: Optional[float] = 0.5, stk: float = 0.0,
         rat: Optional[float] = 0.5, proc: str = "实施",
         rec_date: Optional[int] = REC_MS, ann: Optional[int] = None,
         end: Optional[int] = None) -> tuple:
    """构造 stock_dividend 扫描行的 13 元组（列序照 scan_stock_dividend 取数序）。"""
    return (code, ex_date, rec_date, ann, end, None, None, cash, stk,
            None, None, rat, proc)


def _edge_rows() -> List[tuple]:
    """边界样本：pending/scheduled、完全重复行、同(code,ex_date)不同 cash（不同
    payload_hash → 不同 trigger_id，两行都插）、NULL 可选列、不同 code 同 ex_date。"""
    return [
        _row("600000", PAST_MS),                        # pending 分支
        _row("600001", FUTURE_MS),                      # scheduled 分支
        _row("600000", PAST_MS),                        # 完全重复 → 同 trigger_id 首见保留
        _row("600002", PAST_MS, cash=0.7),              # 同键不同 payload → 两个 trigger
        _row("600002", PAST_MS, cash=0.9),
        _row("600003", FUTURE_MS, rec_date=None, cash=None, rat=None),  # NULL 可选列
        _row("600004", PAST_MS),
    ]


def _mk_disc() -> EventDiscovery:
    """默认 identity（pre_cutover → source_generation=xtquant-legacy，与生产同口径）。"""
    return EventDiscovery(QFQOrchestratorConfig.from_dict({"enabled": True}))


def _mk_db(tmp_path, name: str):
    conn = duckdb.connect(str(tmp_path / name))
    init_duckdb_schema(conn)
    return conn


def _queue_dump(conn) -> list:
    return conn.execute(
        "SELECT * FROM qfq_trigger_queue ORDER BY trigger_id").fetchall()


# ---------------------------------------------------------------------------
# 逐行参考实现：git HEAD 集合化改动前 _scan_dividend_legacy 的逐字拷贝（冻结基准）
# ---------------------------------------------------------------------------
def _scan_dividend_legacy_reference(disc: EventDiscovery, conn, rows,
                                    as_of_ms: int, now: str) -> List[TriggerRecord]:
    _ident = disc.identity
    new_records: List[TriggerRecord] = []
    for (code, ex_date, record_date, ann_date, end_date, cash_div_before_tax,
         cash_div_after_tax, cash_div, stk_div, stk_bo_rate, stk_co_rate,
         div_rat, div_proc) in rows:
        ex_date = int(ex_date)
        effective_date = ex_date
        payload_hash = dividend_payload_hash(
            code, ex_date, record_date, ann_date, end_date,
            cash_div_before_tax, cash_div_after_tax, cash_div,
            stk_div, stk_bo_rate, stk_co_rate, div_rat, div_proc)
        status = "scheduled" if ex_date > as_of_ms else "pending"
        trigger_id = trigger_id_of(
            "STOCK", code, ex_date, "stock_dividend", payload_hash)
        existed = disc._trigger_exists(conn, trigger_id)
        conn.execute(
            "INSERT OR IGNORE INTO qfq_trigger_queue "
            "(trigger_id, asset_type, code, trigger_type, detection_source, source_key, "
            " effective_date, payload_hash, status, trigger_id_version, price_source, "
            " source_generation, cutover_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [trigger_id, "STOCK", code, "stock_dividend", "stock_dividend",
             str(ex_date), effective_date, payload_hash, status, 1,
             _ident["price_source"], _ident["source_generation"], _ident["cutover_id"],
             now, now])
        if not existed:
            new_records.append(TriggerRecord(
                trigger_id=trigger_id, asset_type="STOCK", code=code,
                trigger_type="stock_dividend", detection_source="stock_dividend",
                source_key=str(ex_date), effective_date=effective_date,
                payload_hash=payload_hash, status=status, trigger_id_version=1,
                price_source=_ident["price_source"],
                source_generation=_ident["source_generation"],
                cutover_id=_ident["cutover_id"], created_at=now, updated_at=now))
    return new_records


# ---------------------------------------------------------------------------
# E1/E2/E3/E5：批量实现 vs 逐行参考 —— 落库行集 + 返回值逐位一致
# ---------------------------------------------------------------------------
def test_equivalence_rowset_and_return(tmp_path):
    rows = _edge_rows()
    db_ref = _mk_db(tmp_path, "ref.duckdb")
    db_new = _mk_db(tmp_path, "new.duckdb")
    disc_ref, disc_new = _mk_disc(), _mk_disc()

    # 同一初始态：两库都用参考实现预置 rows[:2]（覆盖"已存在 trigger_id"路径）
    _scan_dividend_legacy_reference(disc_ref, db_ref, rows[:2], AS_OF_MS, SEED_NOW)
    _scan_dividend_legacy_reference(disc_new, db_new, rows[:2], AS_OF_MS, SEED_NOW)
    assert _queue_dump(db_ref) == _queue_dump(db_new)

    rec_ref = _scan_dividend_legacy_reference(disc_ref, db_ref, rows, AS_OF_MS, RUN_NOW)
    rec_new = disc_new._scan_dividend_legacy(db_new, rows, AS_OF_MS, RUN_NOW)

    # E1 落库行集逐位一致（含 created_at=updated_at=RUN_NOW 与预置行 SEED_NOW 不被触碰）
    assert _queue_dump(db_ref) == _queue_dump(db_new)
    # 预置行（600000/600001）的 created_at/updated_at 必须仍是 SEED_NOW
    # （DuckDB TIMESTAMP 列返回 datetime，统一 str() 化后比对）
    seeded = db_new.execute(
        "SELECT * FROM qfq_trigger_queue WHERE code IN ('600000','600001') "
        "ORDER BY code").fetchall()
    assert len(seeded) == 2
    assert all(str(r[-3]) == SEED_NOW and str(r[-2]) == SEED_NOW for r in seeded), \
        "预置行的 created_at/updated_at 不得被改动"

    # E2 返回值：TriggerRecord 列表逐字段相等（dataclass eq），顺序=扫描序
    assert rec_new == rec_ref
    assert [r.trigger_id for r in rec_new] == [r.trigger_id for r in rec_ref]
    # 独立口径复算：预置 2 行已存在 + 重复行不重复计 + 600002 两 payload 各一
    # → 新增 = 7 行 - 2 已存在 - 1 完全重复 = 4
    assert len(rec_new) == 4
    # E3 状态语义逐项抽检
    fut = [r for r in rec_new if r.code == "600003"]
    assert fut and fut[0].status == "scheduled" and fut[0].effective_date == FUTURE_MS
    past = [r for r in rec_new if r.code in ("600002", "600004")]
    assert len(past) == 3 and all(r.status == "pending" for r in past)
    for r in rec_new:
        assert r.trigger_id_version == 1
        assert r.source_key == str(r.effective_date)
        assert r.created_at == RUN_NOW and r.updated_at == RUN_NOW
        assert r.price_source == disc_new.identity["price_source"]
        assert r.source_generation == disc_new.identity["source_generation"]
        assert r.cutover_id == disc_new.identity["cutover_id"]
    assert db_new.execute("SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0] == 6


# ---------------------------------------------------------------------------
# E5 补强：幂等二跑 / 跨实现收敛（下轮重放语义）
# ---------------------------------------------------------------------------
def test_rerun_idempotent_and_cross_impl_convergence(tmp_path):
    rows = _edge_rows()

    db_a = _mk_db(tmp_path, "rerun.duckdb")
    disc_a = _mk_disc()
    disc_a._scan_dividend_legacy(db_a, rows, AS_OF_MS, RUN_NOW)
    dump1 = _queue_dump(db_a)
    assert disc_a._scan_dividend_legacy(db_a, rows, AS_OF_MS, RUN_NOW) == []
    assert _queue_dump(db_a) == dump1, "二跑不得改动任何行"

    # 参考实现先落库 → 批量实现重放 → 返回空、行集不动（§4.3 终态收敛）
    db_b = _mk_db(tmp_path, "conv.duckdb")
    disc_b = _mk_disc()
    _scan_dividend_legacy_reference(disc_b, db_b, rows, AS_OF_MS, RUN_NOW)
    dump_b = _queue_dump(db_b)
    assert disc_b._scan_dividend_legacy(db_b, rows, AS_OF_MS, RUN_NOW) == []
    assert _queue_dump(db_b) == dump_b


# ---------------------------------------------------------------------------
# E4：批量插入失败 ⇒ 单事务整批回滚（不留半批）+ 异常不吞（对旧实现 RED）
# ---------------------------------------------------------------------------
class _FailingConn:
    """透传包装：第 N 次 executemany / 第 N 次 INSERT-execute 时注入失败。"""

    def __init__(self, real, *, fail_executemany: Optional[int] = None,
                 fail_insert_execute: Optional[int] = None):
        self._real = real
        self._em_calls = 0
        self._ins_calls = 0
        self.fail_executemany = fail_executemany
        self.fail_insert_execute = fail_insert_execute

    def executemany(self, sql, params=None):
        self._em_calls += 1
        if self.fail_executemany is not None and self._em_calls >= self.fail_executemany:
            raise RuntimeError("boom-executemany")
        return self._real.executemany(sql, params)

    def execute(self, sql, *args, **kwargs):
        if isinstance(sql, str) and sql.startswith("INSERT"):
            self._ins_calls += 1
            if (self.fail_insert_execute is not None
                    and self._ins_calls >= self.fail_insert_execute):
                raise RuntimeError("boom-insert")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_legacy_batch_atomic_rollback_on_insert_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(qed, "_LEGACY_SCAN_BATCH", 2, raising=False)
    conn = _mk_db(tmp_path, "atomic.duckdb")
    disc = _mk_disc()
    rows = [_row(f"60{k:05d}", PAST_MS) for k in range(5)]  # 5 个全新 tid

    wrapped = _FailingConn(conn, fail_executemany=2)  # 第 2 个插入分片中途崩溃
    with pytest.raises(RuntimeError, match="boom-executemany"):
        disc._scan_dividend_legacy(wrapped, rows, AS_OF_MS, RUN_NOW)

    # §4.3 已裁语义：整批回滚，不得留半批（旧实现逐条自动提交会留 2 行）
    assert conn.execute("SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0] == 0
    # 事务已收口：ROLLBACK 后连接可继续自动提交使用
    conn.execute("CREATE TABLE IF NOT EXISTS _smoke (x INTEGER)")
    conn.execute("INSERT INTO _smoke VALUES (42)")
    assert conn.execute("SELECT x FROM _smoke").fetchone()[0] == 42


def test_reference_rowwise_leaves_partial_rows_on_mid_failure(tmp_path):
    """对照留证：旧逐行实现中途崩溃留部分行（§4.3 显式声明的已裁差异，冻结不变）。"""
    conn = _mk_db(tmp_path, "partial.duckdb")
    disc = _mk_disc()
    rows = [_row(f"61{k:05d}", PAST_MS) for k in range(5)]
    wrapped = _FailingConn(conn, fail_insert_execute=2)  # 第 2 条 INSERT 崩
    with pytest.raises(RuntimeError, match="boom-insert"):
        _scan_dividend_legacy_reference(disc, wrapped, rows, AS_OF_MS, RUN_NOW)
    assert conn.execute("SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# SQL 条数契约：存在性查询 IN 分批（§2.b），逐行 SELECT/INSERT 消失（对旧实现 RED）
# ---------------------------------------------------------------------------
class _CountingConn:
    """透传包装：按语句类别计数（存在性 SELECT / 逐行 INSERT-execute / executemany）。"""

    def __init__(self, real):
        self._real = real
        self.existence_selects = 0
        self.insert_execute = 0
        self.executemany_calls = 0

    def executemany(self, sql, params=None):
        self.executemany_calls += 1
        return self._real.executemany(sql, params)

    def execute(self, sql, *args, **kwargs):
        if isinstance(sql, str):
            s = sql.upper()
            if s.startswith("SELECT") and "QFQ_TRIGGER_QUEUE" in s:
                self.existence_selects += 1
            elif s.startswith("INSERT") and "QFQ_TRIGGER_QUEUE" in s:
                self.insert_execute += 1
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_legacy_batch_sql_count_contract(tmp_path):
    conn = _mk_db(tmp_path, "count.duckdb")
    disc = _mk_disc()
    n = 2500  # ceil(2500/1000) = 3 批
    rows = [_row(f"{600000 + k:06d}", PAST_MS if k % 2 else FUTURE_MS)
            for k in range(n)]
    wrapped = _CountingConn(conn)
    disc._scan_dividend_legacy(wrapped, rows, AS_OF_MS, RUN_NOW)

    assert wrapped.existence_selects == 3, (
        f"存在性查询应 IN 分批为 3 条，实测 {wrapped.existence_selects}")
    assert wrapped.insert_execute == 0, "逐行 INSERT-execute 必须消失（改 executemany）"
    assert wrapped.executemany_calls == 3, (
        f"批量插入应分 3 片 executemany，实测 {wrapped.executemany_calls}")
    assert conn.execute(
        "SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0] == n


# ---------------------------------------------------------------------------
# 性能基准（非 pytest 用例）：3 轮 wall-clock 中位数 + SQL 条数对比
#   python tests/test_qfq_dividend_legacy_batch_equivalence.py --bench
# ---------------------------------------------------------------------------
def run_benchmark(n: int = 50_000, rounds: int = 3) -> None:
    rows = [_row(f"{600000 + k:06d}", PAST_MS if k % 2 else FUTURE_MS,
                 cash=0.1 * (k % 9 + 1)) for k in range(n)]
    old_times, new_times = [], []
    counts = {}
    for rnd in range(1, rounds + 1):
        conn_old = duckdb.connect(":memory:")
        init_duckdb_schema(conn_old)
        disc_old = _mk_disc()
        w_old = _CountingConn(conn_old)
        t0 = time.perf_counter()
        _scan_dividend_legacy_reference(disc_old, w_old, rows, AS_OF_MS, RUN_NOW)
        old_times.append(time.perf_counter() - t0)
        cnt_old = (w_old.existence_selects, w_old.insert_execute,
                   w_old.executemany_calls)
        cnt_old_rows = conn_old.execute(
            "SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0]
        conn_old.close()

        conn_new = duckdb.connect(":memory:")
        init_duckdb_schema(conn_new)
        disc_new = _mk_disc()
        w_new = _CountingConn(conn_new)
        t0 = time.perf_counter()
        rec_new = disc_new._scan_dividend_legacy(w_new, rows, AS_OF_MS, RUN_NOW)
        new_times.append(time.perf_counter() - t0)
        cnt_new = (w_new.existence_selects, w_new.insert_execute,
                   w_new.executemany_calls)
        cnt_new_rows = conn_new.execute(
            "SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0]
        conn_new.close()

        assert cnt_old_rows == cnt_new_rows == n, "两实现落库行数必须一致"
        assert len(rec_new) == n
        counts = {"old": cnt_old, "new": cnt_new}
        print(f"BENCH round={rnd} old={old_times[-1]:.3f}s new={new_times[-1]:.3f}s")
    med_old, med_new = statistics.median(old_times), statistics.median(new_times)
    print(f"BENCH median old={med_old:.3f}s new={med_new:.3f}s "
          f"speedup={med_old / med_new:.1f}x (N={n}, {rounds} 轮中位)")
    print(f"SQLCOUNT old: 存在性SELECT={counts['old'][0]} 逐行INSERT={counts['old'][1]} "
          f"executemany={counts['old'][2]}  (≈2N={2 * n})")
    print(f"SQLCOUNT new: 存在性SELECT={counts['new'][0]} 逐行INSERT={counts['new'][1]} "
          f"executemany={counts['new'][2]}  (N/1000={(n + 999) // 1000})")


if __name__ == "__main__":
    import sys
    if "--bench" in sys.argv:
        run_benchmark()
    else:
        raise SystemExit("仅支持 --bench（等价性验证请用 pytest 跑本文件）")
