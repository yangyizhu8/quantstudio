# -*- coding: utf-8 -*-
"""M1/M2（方案④第一批·2026-10-30）：并发写冲突面——冲突报告补全 + 作用域登记覆盖批写。

判据对齐方案 docs/design-concurrent-write-conflict-20261030.md §2-M1/M2、§4-V1/V2：
  M1（纯恢复型）：冲突产出含 conflict_table + conflict_scope（不再只有 key）；
                 缺失项省略而非填占位；既有字段语义零变化。
  M2（新增检测型）：分钟表批写路径（write→_write_locked）补作用域登记 ⇒
                 _SCOPE_STATS.entered 反映批写段；真实重叠窗口 conflicts 不再恒 0；
                 同进程双写经表锁串行 ⇒ conflicts=0 属「已核实零」非盲区；
                 QS_PRICE_WRITE_SCOPE=0 逐位回退旧行为。
纯单元测试，不触生产库（tmp_path 真库 + 模块级登记表）。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from quantstudio.pipeline import writers as W


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """用例独立：清空登记/作用域/失败槽 + 重置统计 + 默认开启作用域开关。"""
    monkeypatch.delenv("QS_PRICE_WRITE_SCOPE", raising=False)
    monkeypatch.delenv("QS_PRICE_WRITE_SCOPE_WAIT_S", raising=False)
    W._SCOPE_REGISTRY.clear()
    W._WRITER_REGISTRY.clear()
    W._LAST_WRITE_CTX.clear()
    W._LAST_WRITE_FAILURE.clear()
    for k in list(W._SCOPE_STATS):
        W._SCOPE_STATS[k] = 0 if isinstance(W._SCOPE_STATS[k], int) else 0.0
    yield
    W._SCOPE_REGISTRY.clear()
    W._WRITER_REGISTRY.clear()


def _conflict_exc(key="000895, 1789090200000, 1min"):
    """生产实弹形态（2026-10-30 晨轮原文）：TransactionContext 提交期写写冲突。"""
    return RuntimeError(
        'TransactionContext Error: Failed to commit: write-write conflict on key: "%s"' % key)


def _fmt_ms(v) -> str:
    """与实现同口径的毫秒 epoch → 本地可读时间（同进程同时区 ⇒ 断言确定性）。"""
    return datetime.fromtimestamp(int(v) / 1000.0).strftime("%Y-%m-%d %H:%M:%S")


def _mk_writer(tmp_path, monkeypatch):
    """真库 writer（tmp 库）+ 隔离 3A 文件写锁（生产 daemon 在跑，测试不得争锁）。"""
    from quantstudio.pipeline.writers import DuckDBWriter
    w = DuckDBWriter({"type": "duckdb", "path": str(Path(tmp_path) / "m1m2.db")})
    monkeypatch.setattr(W, "ensure_write_lock", lambda *a, **k: None)
    monkeypatch.setattr(W, "release_write_lock", lambda *a, **k: None)
    return w


def _mk_df(n_rows=3, code="159512"):
    return pd.DataFrame({
        "code": [code] * n_rows,
        "time": list(range(1, n_rows + 1)),
        "freq": ["1min"] * n_rows,
        "open": [1.0] * n_rows, "high": [1.0] * n_rows,
        "low": [1.0] * n_rows, "close": [1.0] * n_rows,
    })


# ── M1（V1）：冲突报告补全——表名 + 写入作用域 ─────────────────────────────

def test_m1_v1_conflict_report_has_table_and_scope():
    """V1 正向：冲突产出含表名+作用域（不再只有 key）——批写形态（A1 登记在程）。"""
    W.note_write_context(table="stock_minutes", batch_id="mcp_stock_minutes:20261029",
                         path="_write_locked", serial=True)
    W.price_write_enter("stock_minutes", "_write_locked", "mcp_stock_minutes:20261029",
                        None, 1789090200000, 1789093800000)
    try:
        W.record_write_failure(_conflict_exc("000895, 1789090200000, 1min"))
        ctx = W.last_write_failure()
        # 新增字段：表名不再随 key 丢失（F4）
        assert ctx["conflict_table"] == "stock_minutes"
        scope = ctx["conflict_scope"]
        assert "table=stock_minutes" in scope
        assert "batch=mcp_stock_minutes:20261029" in scope
        assert "path=_write_locked" in scope
        assert "tseg=" in scope
        assert _fmt_ms(1789090200000) in scope and _fmt_ms(1789093800000) in scope
        assert "conflict_bar=" in scope and _fmt_ms(1789090200000) in scope
        # 既有字段语义零变化（M1 只增不改）
        assert ctx["conflict_key"] == "000895, 1789090200000, 1min"
        assert ctx["conflict_time"] == 1789090200000
        assert ctx["table"] == "stock_minutes"
    finally:
        W.price_write_exit("stock_minutes")


def test_m1_scope_without_registration_omits_tseg():
    """M1 缺失项省略：无 A1 登记（无 df 时段）⇒ scope 无 tseg，不填占位。"""
    W.note_write_context(table="etf_minutes", batch_id="b1", path="_write_locked", serial=True)
    W.record_write_failure(_conflict_exc("159512, 1789090200000, 1min"))
    ctx = W.last_write_failure()
    assert ctx["conflict_table"] == "etf_minutes"
    scope = ctx["conflict_scope"]
    assert "table=etf_minutes" in scope and "batch=b1" in scope
    assert "tseg=" not in scope          # 无时段登记 ⇒ 省略，而非填 "?"


def test_m1_missing_ctx_omits_table_and_scope():
    """M1 边界：无写上下文（表名不可得）⇒ 两字段整体省略，既有 key 解析不受影响。"""
    W.record_write_failure(_conflict_exc("000895, 1789090200000, 1min"))
    ctx = W.last_write_failure()
    assert "conflict_table" not in ctx
    assert "conflict_scope" not in ctx
    assert ctx["conflict_key"] == "000895, 1789090200000, 1min"
    assert ctx["conflict_time"] == 1789090200000


def test_m1_non_conflict_failure_has_no_conflict_fields():
    """M1 边界：非冲突族失败 ⇒ 不产出任何 conflict_* 字段。"""
    W.note_write_context(table="stock_minutes", batch_id="b2", path="_write_locked", serial=True)
    W.record_write_failure(ValueError("boom"))
    ctx = W.last_write_failure()
    assert "conflict_key" not in ctx
    assert "conflict_table" not in ctx
    assert "conflict_scope" not in ctx
    assert ctx["exc_type"] == "ValueError"


def test_m1_task_field_forward_compatible():
    """M1 前向兼容：写上下文若携带 task（daemon 后续接入）⇒ scope 含 task=。"""
    W.note_write_context(table="etf_minutes", batch_id="b3", path="_write_locked",
                         task="mcp_etf_minutes", serial=True)
    W.record_write_failure(_conflict_exc())
    assert "task=mcp_etf_minutes" in W.last_write_failure()["conflict_scope"]


def test_m1_diagnostic_log_line(caplog):
    """M1 诊断日志行：冲突时 warning 一行含表名/作用域/peers（此前仅 daemon 侧拼行）。"""
    with caplog.at_level(logging.WARNING, logger="quantstudio.pipeline.writers"):
        W.note_write_context(table="stock_minutes", batch_id="b4", path="_write_locked",
                             serial=True)
        W.record_write_failure(_conflict_exc())
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "conflict_table=stock_minutes" in joined
    assert "conflict_scope=" in joined


def test_m1_same_process_vs_cross_process_human_judgeable():
    """M1 判读面：scope 带 batch/path ⇒ 同进程并行可由人判读（不改 cross_process_candidate 判定）。"""
    W.note_write_context(table="stock_minutes", batch_id="mcp_stock_minutes:20261029",
                         path="_write_locked", serial=True)
    W.record_write_failure(_conflict_exc())
    ctx = W.last_write_failure()
    # 判定逻辑本批不动：无同表同段写者 ⇒ 仍出 cross_process_candidate=True（误报留 M3/另案）
    assert ctx.get("cross_process_candidate") is True
    # 但新增字段足以让人看出「本写=任务批写（同进程 daemon）」
    assert "mcp_stock_minutes" in ctx["conflict_scope"]


# ── M2（V2）：作用域登记覆盖批写路径——观测面闭合 ───────────────────────────

def test_m2_batch_write_registers_scope(tmp_path, monkeypatch):
    """V2-a：分钟表批写 ⇒ _SCOPE_STATS.entered 反映批写段（此前仅引擎单元可见）；写毕即撤。"""
    w = _mk_writer(tmp_path, monkeypatch)
    before = W._SCOPE_STATS["entered"]
    n = w.write(_mk_df(5), "etf_minutes", "m2-a")
    assert n == 5
    assert W._SCOPE_STATS["entered"] == before + 1
    assert W._SCOPE_REGISTRY == {}, "批写作用域写毕必须撤销（无泄漏）"


def test_m2_v2_conflicts_nonzero_on_real_overlap(tmp_path, monkeypatch):
    """V2-b 正向：真实重叠窗口（引擎样作用域在程）⇒ conflicts 不再恒 0（F5 闭合）。

    只观测：不等待、不 defer（waits/deferred 保持 0），写入照常成功——
    对端为引擎样单元作用域（codes/time 均与本批重叠）。
    """
    w = _mk_writer(tmp_path, monkeypatch)
    W.price_scope_enter("qfq:ETF:159512:ev9", "etf_minutes", codes=["159512"],
                        t_min=1, t_max=9, path="qfq_reanchor")
    try:
        n = w.write(_mk_df(3), "etf_minutes", "m2-b")
        assert n == 3, "只观测不得阻断写入"
        assert W._SCOPE_STATS["conflicts"] == 1, "重叠窗口计数恰为 1（在程作用域）"
        assert W._SCOPE_STATS["entered"] >= 1
        assert W._SCOPE_STATS["waits"] == 0 and W._SCOPE_STATS["deferred"] == 0
    finally:
        W.price_scope_exit("qfq:ETF:159512:ev9")


def test_m2_zero_conflicts_truthful_without_overlap(tmp_path, monkeypatch):
    """V2-c：无重叠 ⇒ conflicts=0 属「已核实零」（登记面已覆盖批写路径），写成功。"""
    w = _mk_writer(tmp_path, monkeypatch)
    n = w.write(_mk_df(3), "etf_minutes", "m2-c")
    assert n == 3
    assert W._SCOPE_STATS["entered"] == 1
    assert W._SCOPE_STATS["conflicts"] == 0


def test_m2_same_process_threaded_writes_serialized_zero_conflicts(tmp_path, monkeypatch):
    """V2 负向实证：同进程双线程批写同表 ⇒ 表锁串行化，conflicts=0 是已核实零（非盲区）。

    该路径本就不产生实例级作用域重叠（table_write_lock 串行），双写均成功；
    真正会被计数的重叠 = 引擎样长事务作用域 × 批写（见 V2-b）。
    """
    w = _mk_writer(tmp_path, monkeypatch)
    errors = []

    def _w():
        try:
            w.write(_mk_df(20), "etf_minutes", "m2-d")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=_w) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == [], f"同进程批写经表锁串行应全部成功：{errors[:2]}"
    assert W._SCOPE_STATS["entered"] == 2
    assert W._SCOPE_STATS["conflicts"] == 0, "串行化 ⇒ 无重叠窗口 ⇒ 0 为真相"


def test_m2_non_minute_table_not_instrumented(tmp_path, monkeypatch):
    """M2 域限定：非分钟表批写不登记作用域（不扩面，daily nullcontext 不误伤）。"""
    assert "stock_daily" not in W._SERIAL_WRITE_TABLES
    w = _mk_writer(tmp_path, monkeypatch)
    df = pd.DataFrame({
        "code": ["000001"] * 3,
        "time": [1789090200000] * 3,
        "open": [1.0] * 3, "high": [1.0] * 3,
        "low": [1.0] * 3, "close": [1.0] * 3,
    })
    before = W._SCOPE_STATS["entered"]
    n = w.write(df, "stock_daily", "m2-e")
    assert n == 3
    assert W._SCOPE_STATS["entered"] == before, "非分钟表不得新增登记（域限定）"
    assert W._SCOPE_STATS["conflicts"] == 0


def test_m2_switch_off_bitwise_rollback(tmp_path, monkeypatch):
    """M2 回退面：QS_PRICE_WRITE_SCOPE=0 ⇒ 批写不登记不计数（逐位旧行为），写成功。"""
    monkeypatch.setenv("QS_PRICE_WRITE_SCOPE", "0")
    w = _mk_writer(tmp_path, monkeypatch)
    before = dict(W._SCOPE_STATS)
    n = w.write(_mk_df(3), "etf_minutes", "m2-f")
    assert n == 3, "回退面不得影响写入本身"
    assert W._SCOPE_STATS["entered"] == before["entered"]
    assert W._SCOPE_STATS["conflicts"] == 0
    assert W._SCOPE_REGISTRY == {}
