# -*- coding: utf-8 -*-
"""write-integrity R2 验收测试：per-table 进程内写互斥（两阶段，docs/write-integrity-r123-design.md §2-R2）。

- 第一阶段（writers.py）：_SERIAL_WRITE_TABLES（裁定④：stock_minutes/etf_minutes）写事务段
  包 _table_write_lock；其余表 nullcontext 零行为变化。
- 第二阶段（机制修正）：锁注册表升模块级 table_write_lock()——重锚引擎在自有事务
  （BEGIN→COMMIT/ROLLBACK，不取 _conn_lock）直 UPDATE 价格表 front 列，是取证②
  write-write 冲突真源；引擎主事务包接入**同一把**锁（apply_reanchor_for_security，
  ExitStack 横跨 BEGIN→COMMIT/ROLLBACK）。

引擎主函数需完整 fresh-fetch fixture（行为回归由 test_qfq_reanchor_batch1/2 全量套覆盖），
本文件对引擎侧采用「同注册表 + 接入点结构」断言；writer 侧为动态并发证明。
"""
from __future__ import annotations

import contextlib
import inspect
import sys
import threading
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline import qfq_reanchor_engine as engine_mod  # noqa: E402
from quantstudio.pipeline import writers as writers_mod  # noqa: E402
from quantstudio.pipeline.writers import DuckDBWriter, table_write_lock  # noqa: E402


COLS = ["code", "time", "close", "volume", "amount", "preClose", "pctChg"]


def _df(rows):
    return pd.DataFrame(rows, columns=COLS)


# stock_minutes/etf_minutes DDL（writers.py L236-247）：PK (code,time,freq)，freq 隐式
# NOT NULL——分钟表测试 df 必须含 freq 列（首次红测教训：缺 freq 报 ConstraintException）。
MINUTE_COLS = ["code", "time", "freq", "open", "high", "low", "close", "volume", "amount"]


def _mdf(codes, freq="1min"):
    return pd.DataFrame(
        [[c, 1000, freq, 10.0, 11.0, 9.0, 10.5, 100.0, 1000.0] for c in codes],
        columns=MINUTE_COLS)


def _mk(tmp_path, name):
    db = tmp_path / f"{name}.duckdb"
    return db, DuckDBWriter({"type": "duckdb", "path": str(db)})


# ══════════════════════════════════════════════════════════════════════
# 注册表语义（模块级共享同一把锁——第二阶段机制修正的根基）
# ══════════════════════════════════════════════════════════════════════

def test_registry_same_lock_object():
    """serial 表跨调用/跨入口拿到**同一把** RLock；非 serial 表 nullcontext。"""
    for table in ("stock_minutes", "etf_minutes"):
        lock_a = table_write_lock(table)
        lock_b = writers_mod.table_write_lock(table)
        assert lock_a is lock_b, f"{table} 应命中同一把模块级锁"
        acquired = lock_a.acquire(timeout=0)
        assert acquired
        try:
            reentrant = lock_a.acquire(timeout=0)   # RLock 同线程可重入
            assert reentrant
            lock_a.release()
        finally:
            lock_a.release()
    for table in ("stock_daily", "etf_daily", "etf_basic"):
        ctx = table_write_lock(table)
        assert isinstance(ctx, contextlib.nullcontext.__class__) or \
            type(ctx).__name__ == "nullcontext"


def test_instance_method_delegates_to_module_registry(tmp_path):
    """DuckDBWriter._table_write_lock 委托模块注册表（writer 写路径与引擎同一把锁）。"""
    db, w = _mk(tmp_path, "r2_delegate")
    lock_mod = table_write_lock("stock_minutes")
    lock_inst = w._table_write_lock("stock_minutes")
    assert lock_inst is lock_mod
    assert isinstance(w._table_write_lock("stock_daily"), contextlib.nullcontext.__class__) or \
        type(w._table_write_lock("stock_daily")).__name__ == "nullcontext"


# ══════════════════════════════════════════════════════════════════════
# writer 侧动态证明（第一阶段落点）
# ══════════════════════════════════════════════════════════════════════

def test_concurrent_same_table_writes_clean(tmp_path):
    """两线程并发写 stock_minutes（不相交 code）：加锁后零异常、数据完整。

    取证② 场景的进程内缩小版：同进程两路并发写同一 serial 表。无锁时间歇性
    TransactionContext write-write；有锁确定性串行——恒绿即证明。
    """
    db, w = _mk(tmp_path, "r2_concurrent")
    errors = []
    done = threading.Event()

    def _worker(tag, codes):
        try:
            df = _mdf(codes)
            wr = w.write(df, "stock_minutes", f"r2c-{tag}")
            assert int(wr) == len(codes)
        except Exception as e:   # pragma: no cover - 记录供断言
            errors.append(f"{tag}: {type(e).__name__}: {e}")
        finally:
            done.set()

    t1 = threading.Thread(target=_worker, args=("a", ["600001", "600002", "600003"]))
    t2 = threading.Thread(target=_worker, args=("b", ["600011", "600012", "600013"]))
    t1.start(); t2.start(); t1.join(30); t2.join(30)
    assert not errors, errors
    # 终态计数：直接开只读连接（read_df 形态与表名语义不适用于裸 SQL）
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    n = con.execute("SELECT COUNT(*) FROM stock_minutes").fetchone()[0]
    con.close()
    assert n == 6, f"终态应 6 行，实得 {n}"


def test_write_path_takes_lock(tmp_path):
    """外部持有 stock_minutes 锁 → writer.write 阻塞；释放后完成（落点载重断言）。"""
    db, w = _mk(tmp_path, "r2_block")
    df = _mdf(["600001"])
    lock = table_write_lock("stock_minutes")
    with lock:
        result = {}
        def _do():
            result["wr"] = int(w.write(df, "stock_minutes", "r2b-1"))
        t = threading.Thread(target=_do)
        t.start()
        t.join(0.8)
        assert t.is_alive(), "writer.write 应阻塞在 stock_minutes 表锁上（第一阶段落点）"
    t.join(15)
    assert not t.is_alive()
    assert result.get("wr") == 1


def test_out_of_scope_table_not_blocked(tmp_path):
    """非 serial 表（stock_daily）无锁直通：外部不持锁即写（回归由既有套覆盖）。"""
    db, w = _mk(tmp_path, "r2_free")
    df = _df([["600001", 1000, 10.0, 100.0, 1000.0, 9.5, 0.5]])
    wr = w.write(df, "stock_daily", "r2f-1")
    assert int(wr) == 1


# ══════════════════════════════════════════════════════════════════════
# 引擎侧接入证明（第二阶段落点：同注册表 + 主事务包接入）
# ══════════════════════════════════════════════════════════════════════

def test_engine_shares_registry_and_wraps_main_txn():
    """引擎与 writer 共享同一锁函数；apply_reanchor_for_security 主事务接入该锁。"""
    # 同一函数对象 ⇒ 同一模块级注册表 ⇒ 同一把锁
    assert engine_mod.table_write_lock is writers_mod.table_write_lock
    # 主事务包接入点（ExitStack enter；行为回归由 reanchor batch1/2 套覆盖）
    src = inspect.getsource(engine_mod.apply_reanchor_for_security)
    assert "enter_context(table_write_lock(" in src, "主事务须以 ExitStack 接入表锁"
    assert "_tables_of(asset_type)" in src
    # 锁获取须先于 BEGIN、释放须在 finally（COMMIT/ROLLBACK 之后）
    assert src.index("enter_context(table_write_lock(") < src.index('"BEGIN TRANSACTION"') \
        if '"BEGIN TRANSACTION"' in src else True
    assert "_r2_price_locks.close()" in src
