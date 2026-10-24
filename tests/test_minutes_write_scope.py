# -*- coding: utf-8 -*-
"""B1-B7：minute 表写作用域隔离 + A1b 登记判活 单测（六步③·2026-10-23）。

判据对齐方案 docs/minutes-write-scope-isolation-design.md §3 功能点分解表。
每个正向都配负向对照；作用域时间域只在本表内比较（各表 time 口径不同）。
"""
from __future__ import annotations

import threading
import time

import pytest

import quantstudio.pipeline.writers as W


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """每个用例独立：清空登记/作用域表 + 重置统计 + 默认开启。"""
    monkeypatch.delenv("QS_PRICE_WRITE_SCOPE", raising=False)
    monkeypatch.delenv("QS_PRICE_WRITE_SCOPE_WAIT_S", raising=False)
    W._SCOPE_REGISTRY.clear()
    W._WRITER_REGISTRY.clear()
    for k in list(W._SCOPE_STATS):
        W._SCOPE_STATS[k] = 0 if isinstance(W._SCOPE_STATS[k], int) else 0.0
    yield
    W._SCOPE_REGISTRY.clear()
    W._WRITER_REGISTRY.clear()


# ── B1（A1b）：登记判活 + 心跳刷新（取消固定 TTL 剪除活跃条目）──────────────

def test_b1_active_entry_survives_beyond_ttl(monkeypatch):
    """B1 正向：活跃登记（长事务写者）超过 TTL(300s) 仍在册——这是 10-23 实弹误报的根因。"""
    W.price_write_enter("etf_minutes", "_write_locked", "b1", None, 1000, 2000)
    key = (__import__("os").getpid(), threading.get_ident(), "etf_minutes")
    ent = W._WRITER_REGISTRY[key]
    # 模拟「已过去 400s」：只把时间戳拨老，线程仍存活
    ent["started"] = ent["started"] - 400
    ent["heartbeat"] = ent["heartbeat"] - 400
    with W._REGISTRY_LOCK:
        W._prune_registry_locked()
    assert key in W._WRITER_REGISTRY, "活跃条目不得被 TTL 剪除（A1b 核心）"


def test_b1_dead_thread_entry_pruned():
    """B1 负向：线程已结束的条目必须被判活剪除。"""
    done = threading.Event()

    def _work():
        W.price_write_enter("stock_minutes", "_write_locked", "b1-dead", None, 1, 2)
        done.set()

    t = threading.Thread(target=_work)
    t.start()
    t.join()
    with W._REGISTRY_LOCK:
        W._prune_registry_locked()
    keys = [k for k in W._WRITER_REGISTRY if k[1] == t.ident]
    assert keys == [], "死线程条目应被剪除"


# ── B2：作用域登记/冲突判定（表 × code × 时间域）────────────────────────

def test_b2_same_table_same_segment_conflicts():
    """B2 正向：同表 + 码交集 + 时间域交集 ⇒ 冲突。"""
    W.price_scope_enter("s1", "etf_minutes", codes=["159512"], t_min=100, t_max=200)
    hits = W.price_scope_conflicts("etf_minutes", codes=["159512"], t_min=150, t_max=250)
    assert len(hits) == 1 and hits[0]["scope_id"] == "s1"


def test_b2_other_code_no_conflict():
    """B2 负向：同表但码集无交集 ⇒ 不冲突（分钟表双写者同表不同段是常态）。"""
    W.price_scope_enter("s1", "etf_minutes", codes=["159512"], t_min=100, t_max=200)
    assert W.price_scope_conflicts("etf_minutes", codes=["000055"], t_min=150, t_max=250) == []


def test_b2_other_table_no_conflict():
    """B2 负向：异表 ⇒ 不冲突。"""
    W.price_scope_enter("s1", "etf_minutes", codes=["159512"], t_min=100, t_max=200)
    assert W.price_scope_conflicts("stock_minutes", codes=["159512"], t_min=150, t_max=250) == []


def test_b2_disjoint_time_no_conflict():
    """B2 负向：同表同码但时间域不相交 ⇒ 不冲突。"""
    W.price_scope_enter("s1", "etf_minutes", codes=["159512"], t_min=100, t_max=200)
    assert W.price_scope_conflicts("etf_minutes", codes=["159512"], t_min=300, t_max=400) == []


def test_b2_no_constraint_means_overlap():
    """B2 边界：任一方无码/时间约束 ⇒ 视为重叠（保守）。"""
    W.price_scope_enter("s1", "etf_minutes", codes=None, t_min=None, t_max=None)
    assert len(W.price_scope_conflicts("etf_minutes", codes=["000055"], t_min=1, t_max=2)) == 1


# ── B3/B4：让行 + defer 语义（②审实质条件）──────────────────────────────

def test_b3_scope_visible_and_exit():
    """B3：作用域在程可探测，exit 后不可探测。"""
    W.price_scope_enter("s1", "etf_minutes", codes=["159512"])
    assert W.price_scope_conflicts("etf_minutes", codes=["159512"]) != []
    W.price_scope_exit("s1")
    assert W.price_scope_conflicts("etf_minutes", codes=["159512"]) == []


def test_b4_wait_then_release_succeeds():
    """B4 正向：对方 0.5s 后释放 ⇒ 本写者等待后成功（released=True）。"""
    W.price_scope_enter("s1", "etf_minutes", codes=["159512"])

    def _release():
        time.sleep(0.5)
        W.price_scope_exit("s1")

    threading.Thread(target=_release).start()
    released, waited, remaining = W.wait_for_scope(
        "etf_minutes", ["159512"], None, None, timeout_s=5.0, poll_s=0.1)
    assert released is True and remaining == []
    assert waited >= 0.4


def test_b4_timeout_defers_not_retries(monkeypatch):
    """B4 负向（②审条件）：对方不释放 ⇒ 超时返回 released=False（调用方据此 defer），
    且 **不得**触发 A6 重试（write_retry.attempts 保持 0）。"""
    monkeypatch.setenv("QS_PRICE_WRITE_SCOPE_WAIT_S", "0.5")
    W.price_scope_enter("s1", "etf_minutes", codes=["159512"])
    before = dict(W._WRITE_RETRY_STATS)
    released, waited, remaining = W.wait_for_scope(
        "etf_minutes", ["159512"], None, None, poll_s=0.1)
    assert released is False and len(remaining) == 1
    assert W._WRITE_RETRY_STATS == before, "defer 路径不得增长 write_retry 统计"


def test_b4_deferred_counter_and_exception():
    """B4：defer 计数 + WriteScopeDeferred 语义（携带 table/batch/waited）。"""
    W.price_scope_mark_deferred()
    assert W.price_scope_stats().get("deferred") == 1
    exc = W.WriteScopeDeferred(table="etf_minutes", batch_id="b", waited_s=120.0,
                               remaining=1)
    assert "defer" in str(exc) and exc.table == "etf_minutes" and exc.remaining == 1


# ── B5：引擎段级/单元级作用域（模拟单元事务，验证可探测性）────────────────

def test_b5_unit_scope_visible_during_unit():
    """B5：单元执行期间作用域可探测；提交（exit）后不可探测。"""
    sid = W.price_scope_enter("qfq:ETF:159512:ev1", "etf_minutes",
                              codes=["159512"], path="qfq_reanchor")
    hits = W.price_scope_conflicts("etf_minutes", codes=["159512"], t_min=1, t_max=9)
    assert len(hits) == 1 and hits[0]["path"] == "qfq_reanchor"
    W.price_scope_exit(sid)
    assert W.price_scope_conflicts("etf_minutes", codes=["159512"], t_min=1, t_max=9) == []


# ── B6：观测（run_state write_scope）─────────────────────────────────────

def test_b6_write_run_state_includes_write_scope(tmp_path, monkeypatch):
    """B6：write_run_state 落 write_scope 字段（只增观测）。"""
    import quantstudio.pipeline.daemon_lifecycle as L
    monkeypatch.setattr(L, "daemon_run_state_path", lambda: tmp_path / "run_state.json")
    W.price_scope_enter("s1", "etf_minutes", codes=["159512"])
    L.write_run_state(status="running", scheduled_date="2026-10-23")
    import json
    st = json.loads((tmp_path / "run_state.json").read_text(encoding="utf-8"))
    assert "write_scope" in st and st["write_scope"].get("active", 0) >= 1
    assert st["status"] == "running"


# ── B7：回退开关（QS_PRICE_WRITE_SCOPE=0 逐位旧行为）────────────────────

def test_b7_switch_off_no_detection_no_wait(monkeypatch):
    """B7 回退面：开关=0 ⇒ 冲突检测恒空、零等待（旧行为）。"""
    monkeypatch.setenv("QS_PRICE_WRITE_SCOPE", "0")
    W.price_scope_enter("s1", "etf_minutes", codes=["159512"])
    assert W.price_scope_conflicts("etf_minutes", codes=["159512"]) == []
    released, waited, remaining = W.wait_for_scope("etf_minutes", ["159512"],
                                                   timeout_s=0.2, poll_s=0.05)
    assert released is True and waited == 0.0 and remaining == []


def test_b7_scope_enabled_default():
    """B7：默认开启（不设 env 即 1）。"""
    assert W._scope_enabled() is True
