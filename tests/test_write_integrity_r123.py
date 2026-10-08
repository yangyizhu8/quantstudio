# -*- coding: utf-8 -*-
"""write-integrity R1+R3a+R3b 验收测试（docs/write-integrity-r123-design.md，四裁定落定版）。

- R1（新增检测型）：DuckDBWriter pre-count 记账探测失效 → warning（唯一失效可观测点）
  + 新连接单次重试 → 仍失败 new/updated=-1 哨兵（WriteResult int 语义仍 len(df)）；
  正常路径（探测成功）行为逐位不变。
- R3a（纯恢复型·裁定①甲·N=4h）：supersede_stale_intents 第五可清条件——活跃态周期
  updated_at 早于 now−4h 视为死周期可清；新鲜活跃周期不清；终态照清（回归零变化）。
- R3b（新增检测型·fail-closed）：applying 主循环心跳（600s 间隔）+ 停滞告警（4h 阈值，
  只告警不处置）；正常推进无心跳无告警。
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline.writers import DuckDBWriter  # noqa: E402
import quantstudio.pipeline.qfq_resident_orchestrator as orch_mod  # noqa: E402
from quantstudio.pipeline.qfq_resident_orchestrator import QFQResidentOrchestrator  # noqa: E402
from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema  # noqa: E402
from quantstudio.pipeline.qfq_orchestrator_types import QFQOrchestratorConfig  # noqa: E402
from quantstudio.pipeline.qfq_fresh_capture import FakeFreshFetcher  # noqa: E402

BJ_TZ = timezone(timedelta(hours=8))

WRITER_LOGGER = "quantstudio.pipeline.writers"
ORCH_LOGGER = "quantstudio.pipeline.qfq_resident_orchestrator"


# ══════════════════════════════════════════════════════════════════════
# R1：DuckDBWriter 记账失效告警 + 重试 + -1 哨兵
# ══════════════════════════════════════════════════════════════════════
TABLE = "stock_daily"
COLS = ["code", "time", "close", "volume", "amount", "preClose", "pctChg"]

# pre-count 探测 SQL 的稳定前缀（_write_locked 记账段唯一形态）
_PRECOUNT_PREFIX = f"SELECT COUNT(*) FROM {TABLE} WHERE "


def _mk(tmp_path, name):
    db = tmp_path / f"{name}.duckdb"
    return db, DuckDBWriter({"type": "duckdb", "path": str(db)})


def _df(rows):
    return pd.DataFrame(rows, columns=COLS)


SEED = _df([
    ["600000", 1000, 10.0, 100.0, 1000.0, 9.5, 0.5],
    ["600001", 1000, 20.0, 200.0, 2000.0, 19.5, 0.5],
    ["600002", 1000, 30.0, 300.0, 3000.0, 29.5, 0.5],
])
BATCH = _df([
    ["600000", 1000, 10.0, 100.0, 1000.0, 9.5, 0.5],   # 已存在且一致 → 更新口径
    ["600001", 1000, 21.5, 200.0, 2000.0, 19.5, 0.5],   # 已存在且变化 → 更新口径
    ["600003", 1000, 40.0, 400.0, 4000.0, 39.5, 0.5],   # 新增
])
# 冻结的正常路径记账公式：updated=写前已存在行数=2，new=其余=1


class _PreCountFlaker:
    """按**全局 pre-count 尝试次数**注入失败的 _conn 工厂包装。

    fail_first=N：前 N 次 pre-count SELECT 抛异常。
      N=1 → 首连接（主写连接）探测炸、重试新连接成功（R1-重试成功用例）；
      N≥2 → 主连接与重试连接都炸 → accounting_unknown 哨兵路径（R1-红测用例）。
    其余语句全部透传（DESCRIBE / upsert INSERT 等不受影响——复现 ⓪ 号取证形态：
    只有 pre-count SELECT 失效、写入本身成功）。
    """

    def __init__(self, writer, fail_first: int):
        self._real_factory = writer._conn
        self._fail_first = fail_first
        self.attempts = 0

    def __call__(self):
        return _PreCountGuardConn(self._real_factory(), self)


class _PreCountGuardConn:
    def __init__(self, real, flaker):
        self._real = real
        self._flaker = flaker

    def execute(self, query, *a, **k):
        if isinstance(query, str) and query.startswith(_PRECOUNT_PREFIX):
            self._flaker.attempts += 1
            if self._flaker.attempts <= self._flaker._fail_first:
                raise RuntimeError(
                    f"synthetic pre-count probe failure #{self._flaker.attempts}")
        return self._real.execute(query, *a, **k)

    def register(self, *a, **k):
        return self._real.register(*a, **k)

    def unregister(self, *a, **k):
        return self._real.unregister(*a, **k)

    def close(self):
        return self._real.close()


def _table_rows(db):
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(
            f"SELECT code, time, close FROM {TABLE} ORDER BY code").fetchall()
    finally:
        con.close()


def test_r1_probe_failure_unknown_sentinel(tmp_path, monkeypatch, caplog):
    """R1-红测：主连接与重试连接的 pre-count 都失败 → warning + -1 哨兵 + int 语义不变。

    现行（修复前）代码此用例必须红：except 静默置 0 → new=len(df)=3 全量误标「新增」。
    """
    monkeypatch.delenv("QS_UPSERT_SKIP_IDENTICAL", raising=False)
    db, w = _mk(tmp_path, "r1_unknown")
    w.write(SEED, TABLE, "seed")
    w._conn = _PreCountFlaker(w, fail_first=99)
    with caplog.at_level(logging.INFO, logger=WRITER_LOGGER):
        r = w.write(BATCH, TABLE, "b2")
    # 哨兵（裁定③）：记账不可知 → new/updated=-1
    assert r.new == -1, f"记账失效批 new 应为 -1 哨兵，实得 {r.new}（现行代码误标 {len(BATCH)}）"
    assert r.updated == -1
    # WriteResult int 语义不变：仍 = 提交行数 len(df)
    assert int(r) == len(BATCH) == 3
    # 失效可观测点：两条 warning（首次探测失败 + 重试仍失败）
    msgs = [rec.getMessage() for rec in caplog.records]
    warn_texts = [rec.getMessage() for rec in caplog.records
                  if rec.levelno == logging.WARNING]
    assert any("pre-count 记账探测失败" in t for t in warn_texts), warn_texts
    assert any("pre-count 重试仍失败" in t for t in warn_texts), warn_texts
    assert any("RuntimeError" in t for t in warn_texts), "warning 须含异常类型与文本"
    # 汇总日志行按方案口径：新增 ? + 更新 ? 记账失效: <异常类型>
    assert any("记账失效: RuntimeError" in m for m in msgs), msgs
    # 数据照常写入（upsert 不受记账失效影响——与 ⓪ 号取证一致：虚报不丢数据）
    # 终态 = SEED 3 行 + BATCH 新增 1（600003）= 4 行（BATCH 另 2 行与 SEED 重叠，upsert 更新）
    rows = _table_rows(db)
    assert len(rows) == 4 and rows[-1][0] == "600003"


def test_r1_retry_recovers_accounting(tmp_path, monkeypatch, caplog):
    """R1-重试成功：首连接探测炸、新连接重试成功 → 记账恢复正常数值（与公式逐位一致）。"""
    monkeypatch.delenv("QS_UPSERT_SKIP_IDENTICAL", raising=False)
    db, w = _mk(tmp_path, "r1_retry")
    w.write(SEED, TABLE, "seed")
    flaker = _PreCountFlaker(w, fail_first=1)
    w._conn = flaker
    with caplog.at_level(logging.INFO, logger=WRITER_LOGGER):
        r = w.write(BATCH, TABLE, "b2")
    # 重试成功 → 正常记账数值，与改前公式逐位一致
    assert (r.new, r.updated) == (1, 2)
    assert int(r) == 3
    # 首次失效有一条 warning（可观测），但不得出现「重试仍失败」
    msgs = [rec.getMessage() for rec in caplog.records]
    warn_texts = [rec.getMessage() for rec in caplog.records
                  if rec.levelno == logging.WARNING]
    assert any("pre-count 记账探测失败" in t for t in warn_texts), warn_texts
    assert not any("重试仍失败" in t for t in warn_texts), warn_texts
    # 重试确实发生（探测尝试次数 ≥2）
    assert flaker.attempts >= 2
    # 正常数值日志行
    assert any("(新增 1 + 更新 2) 防重复 upsert" in m for m in msgs)


def test_r1_normal_path_bitwise_unchanged(tmp_path, monkeypatch, caplog):
    """R1-正常路径回归：探测成功 → 新增/更新数值与改前公式逐位一致、日志行原样。"""
    monkeypatch.delenv("QS_UPSERT_SKIP_IDENTICAL", raising=False)
    db, w = _mk(tmp_path, "r1_normal")
    w.write(SEED, TABLE, "seed")
    with caplog.at_level(logging.INFO, logger=WRITER_LOGGER):
        r = w.write(BATCH, TABLE, "b2")
    assert (r.new, r.updated) == (1, 2)
    assert int(r) == 3
    assert r.changed == 0  # 开关关闭态 changed 恒 0（口径不变）
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        f"[DuckDBWriter] {TABLE} batch=b2: wrote 3 rows (新增 1 + 更新 2) 防重复 upsert"
        in m for m in msgs)
    assert not any("记账失效" in m for m in msgs)
    # 数据终态正确（SEED 3 只全保留 + 600003 新增 = 4 只；重叠 2 只为更新口径）
    rows = _table_rows(db)
    assert [r_[0] for r_ in rows] == ["600000", "600001", "600002", "600003"]


# ══════════════════════════════════════════════════════════════════════
# R3a/R3b 公共设施（内存 duckdb + init_schema，范式沿 test_qfq_resident_orchestrator）
# ══════════════════════════════════════════════════════════════════════
def _ms(s: str) -> int:
    fmt = "%Y-%m-%d %H:%M:%S" if " " in s else "%Y-%m-%d"
    return int(datetime.strptime(s, fmt).replace(tzinfo=BJ_TZ).timestamp() * 1000)


AS_OF_MS = _ms("2026-07-28 09:00:00")
EX_PAST_MS = _ms("2026-07-10")


def _make_ohlc(index_dates, prices):
    idx = pd.to_datetime(index_dates)
    rows = [{"open": o, "high": h, "low": l, "close": c} for (o, h, l, c) in prices]
    return pd.DataFrame(rows, index=idx)


_NONE_DAILY = _make_ohlc(["2026-07-08", "2026-07-09", "2026-07-10"],
                         [(10, 11, 9, 10), (10.5, 11.5, 10, 11), (11, 12, 10.5, 11.5)])
_NONE_MIN = _make_ohlc(["2026-07-10 09:30:00", "2026-07-10 09:31:00"],
                       [(10, 10.2, 9.9, 10.1), (10.1, 10.3, 10.0, 10.2)])


class _FakeCalendar:
    def is_trading_day(self, ms):  # pragma: no cover
        return True

    def prev_trading_day(self, ms):  # pragma: no cover
        return ms - 86_400_000


def _new_conn():
    conn = duckdb.connect(":memory:")
    init_duckdb_schema(conn)
    conn.execute("CREATE TABLE stock_daily (code VARCHAR, time BIGINT)")
    conn.execute("CREATE TABLE stock_minutes (code VARCHAR, time BIGINT)")
    conn.execute("CREATE TABLE etf_daily (code VARCHAR, time BIGINT)")
    conn.execute("CREATE TABLE etf_minutes (code VARCHAR, time BIGINT)")
    conn.execute("""
        CREATE TABLE stock_dividend (
            code VARCHAR, ex_date BIGINT, record_date BIGINT, ann_date BIGINT,
            end_date BIGINT, cash_div_before_tax DOUBLE, cash_div_after_tax DOUBLE,
            cash_div DOUBLE, stk_div DOUBLE, stk_bo_rate DOUBLE, stk_co_rate DOUBLE,
            div_rat DOUBLE, div_proc VARCHAR, update_time VARCHAR,
            PRIMARY KEY(code, ex_date))""")
    return conn


def _cfg(**over) -> QFQOrchestratorConfig:
    base = dict(enabled=True, require_bootstrap=False, price_source="xtquant")
    base.update(over)
    return QFQOrchestratorConfig.load(raw=base)


def _init_aux(aux_path: str) -> None:
    aconn = sqlite3.connect(aux_path)
    try:
        aconn.execute(
            "CREATE TABLE IF NOT EXISTS adj_factor "
            "(code TEXT, time INTEGER, adj_factor REAL)")
        aconn.execute(
            "CREATE TABLE IF NOT EXISTS fund_adj "
            "(code TEXT, time INTEGER, adj_factor REAL)")
        aconn.commit()
    finally:
        aconn.close()


def _orch(tmpdir) -> QFQResidentOrchestrator:
    aux_path = os.path.join(tmpdir, "qfq_aux.db")
    _init_aux(aux_path)
    return QFQResidentOrchestrator(
        _cfg(), aux_db=aux_path,
        fetcher=FakeFreshFetcher({
            ("600000.SH", "1d"): (_NONE_DAILY, _NONE_DAILY * 0.9),
            ("600000.SH", "1m"): (_NONE_MIN, _NONE_MIN * 0.9),
        }),
        calendar=_FakeCalendar())


@pytest.fixture()
def tmpdir_path():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _defer_one(orch, conn, cycle_id, tag_ms=123):
    orch.defer_watermark(conn, cycle_id=cycle_id, source="tushare",
                         table="stock_daily", freq="daily", candidate_watermark=tag_ms)


def _naive_now_minus(**kw):
    """与 _now_ts 同基（BJ 墙钟 naive datetime）的相对时刻。"""
    return (datetime.now(BJ_TZ) - timedelta(**kw)).replace(tzinfo=None)


# ── R3a：死周期清障补洞三态 ────────────────────────────────────────────
def test_r3a_constants_pinned():
    """裁定②钉住：N=4h 起步（可配置化另登记）。"""
    assert orch_mod._STALE_ACTIVE_CYCLE_S == 4 * 3600


def test_r3a_stale_active_cycle_superseded(tmpdir_path, caplog):
    """活跃态（started）+ updated_at=5h 前 → 死周期，intent 被 supersede；周期行不动。"""
    conn = _new_conn()
    orch = _orch(tmpdir_path)
    dead_cid = orch.begin_cycle(conn)
    _defer_one(orch, conn, dead_cid)
    # 周期卡活跃态 + 进程死亡 5h（DB 实证形态：11 条 pending 挂 5 个死周期）
    conn.execute("UPDATE qfq_cycle_run SET updated_at=? WHERE cycle_id=?",
                 [_naive_now_minus(hours=5), dead_cid])
    with caplog.at_level(logging.INFO, logger=ORCH_LOGGER):
        orch.begin_cycle(conn)  # 触发 supersede_stale_intents
    st, reason = conn.execute(
        "SELECT status, hold_reason FROM qfq_watermark_intent WHERE cycle_id=?",
        [dead_cid]).fetchone()
    assert st == "superseded"
    assert reason == "stale pending superseded by new cycle"
    # 周期行本身不动（只清 intent，不改周期状态）
    assert conn.execute(
        "SELECT status FROM qfq_cycle_run WHERE cycle_id=?", [dead_cid]
    ).fetchone()[0] == "started"
    # 清障 INFO：含清理条数与 stale-active 命中标记
    assert any("supersede_stale_intents" in rec.getMessage()
               and "stale-active" in rec.getMessage() for rec in caplog.records), \
        [rec.getMessage() for rec in caplog.records]
    conn.close()


def test_r3a_fresh_active_cycle_not_superseded(tmpdir_path):
    """活跃态 + updated_at=10min 前（真活着）→ 不清，intent 保持 pending。"""
    conn = _new_conn()
    orch = _orch(tmpdir_path)
    live_cid = orch.begin_cycle(conn)
    _defer_one(orch, conn, live_cid)
    conn.execute("UPDATE qfq_cycle_run SET updated_at=? WHERE cycle_id=?",
                 [_naive_now_minus(minutes=10), live_cid])
    orch.begin_cycle(conn)
    st = conn.execute(
        "SELECT status FROM qfq_watermark_intent WHERE cycle_id=?",
        [live_cid]).fetchone()[0]
    assert st == "pending", "新鲜活跃周期的 intent 不得被误清"
    conn.close()


def test_r3a_finalized_cycle_still_superseded(tmpdir_path):
    """终态周期照旧清（回归面零变化：原第四条件不回归）。"""
    conn = _new_conn()
    orch = _orch(tmpdir_path)
    old_cid = orch.begin_cycle(conn)
    _defer_one(orch, conn, old_cid)
    conn.execute("UPDATE qfq_cycle_run SET status='finalized' WHERE cycle_id=?",
                 [old_cid])
    orch.begin_cycle(conn)
    st = conn.execute(
        "SELECT status FROM qfq_watermark_intent WHERE cycle_id=?",
        [old_cid]).fetchone()[0]
    assert st == "superseded"
    conn.close()


# ── R3b：applying 心跳 + 停滞告警（monkeypatch 时钟） ───────────────────
def test_r3b_constants_pinned():
    """方案口径钉住：心跳 600s、停滞告警 4h（W2 风格硬编码起步）。"""
    assert orch_mod._APPLY_HEARTBEAT_S == 600
    assert orch_mod._APPLY_STALL_ALARM_S == 4 * 3600


def _run_applying_cycle(orch, conn, monkeypatch, unit_seconds: float, n_units: int = 3):
    """跑一轮 applying 主循环：fake 掉发现/领取/引擎/落账/gate，聚焦心跳与停滞逻辑。

    - 时钟：monkeypatch 模块级 _monotonic 为可推进假钟（初始 1000.0）；
    - 每个 _reanchor_security 调用推进 unit_seconds（模拟单元处理耗时）；
    - 返回 (summary, clock dict)。
    """
    units = [{"asset_type": "STOCK", "code": f"60000{i}", "triggers": [f"t{i}"],
              "effective_dates": [EX_PAST_MS], "attempt": 0} for i in range(n_units)]
    monkeypatch.setattr(orch, "_discover", lambda *a, **k: [])
    monkeypatch.setattr(orch, "_claim_and_merge", lambda *a, **k: list(units))
    monkeypatch.setattr(orch, "_qfq_gate",
                        lambda *a, **k: (True, {"passed": True, "reasons": []}))
    monkeypatch.setattr(orch, "_apply_trigger_outcome", lambda *a, **k: None)
    clock = {"t": 1000.0}
    monkeypatch.setattr(orch_mod, "_monotonic", lambda: clock["t"])

    def fake_reanchor(*a, **k):
        clock["t"] += unit_seconds  # 单元处理耗时
        return SimpleNamespace(status="committed",
                               event_id=f"ev_{int(clock['t'])}", error=None)

    monkeypatch.setattr(orch, "_reanchor_security", fake_reanchor)
    cid = orch.begin_cycle(conn)
    summary = orch.run_post_ingest(conn, cycle_id=cid, run_id="r_r3b", as_of_ms=AS_OF_MS)
    return cid, summary, clock


def test_r3b_heartbeat_by_interval(tmpdir_path, monkeypatch, caplog):
    """心跳行按 ≥600s 间隔出现（每单元 700s → 第 2/3 次迭代各一条）；无停滞告警。"""
    conn = _new_conn()
    orch = _orch(tmpdir_path)
    with caplog.at_level(logging.INFO, logger=ORCH_LOGGER):
        cid, summary, _ = _run_applying_cycle(orch, conn, monkeypatch, unit_seconds=700)
    hb = [rec.getMessage() for rec in caplog.records if "applying 心跳" in rec.getMessage()]
    assert len(hb) >= 1, [rec.getMessage() for rec in caplog.records]
    assert any(cid in m for m in hb), "心跳须含 cycle_id"
    # 循环控制流不变：3 个单元全部领取处理，周期正常走完
    assert summary.claimed == 3 and summary.status == "finalized"
    assert not any("停滞" in rec.getMessage() for rec in caplog.records)
    conn.close()


def test_r3b_stall_alarm_after_threshold(tmpdir_path, monkeypatch, caplog):
    """单元耗时 5h（≥4h 阈值）→ 完成时距上次周期状态推进 ≥4h 触发 error（只告警）。"""
    conn = _new_conn()
    orch = _orch(tmpdir_path)
    with caplog.at_level(logging.INFO, logger=ORCH_LOGGER):
        cid, summary, _ = _run_applying_cycle(
            orch, conn, monkeypatch, unit_seconds=5 * 3600)
    errs = [rec.getMessage() for rec in caplog.records
            if rec.levelno == logging.ERROR and "停滞" in rec.getMessage()]
    assert len(errs) >= 1, [rec.getMessage() for rec in caplog.records]
    assert any(cid in m for m in errs), "停滞告警须含 cycle_id"
    # fail-closed：只告警不处置——周期仍走完正常终态（不自动改状态/不提交水位语义变化）
    assert summary.status == "finalized"
    assert summary.claimed == 3
    conn.close()


def test_r3b_normal_progress_no_alarm(tmpdir_path, monkeypatch, caplog):
    """正常推进（单元瞬时完成）→ 无心跳行、无停滞告警。"""
    conn = _new_conn()
    orch = _orch(tmpdir_path)
    with caplog.at_level(logging.INFO, logger=ORCH_LOGGER):
        _, summary, _ = _run_applying_cycle(orch, conn, monkeypatch, unit_seconds=0.0)
    assert not any("applying 心跳" in rec.getMessage() for rec in caplog.records)
    assert not any("停滞" in rec.getMessage() for rec in caplog.records)
    assert summary.status == "finalized"
    conn.close()
