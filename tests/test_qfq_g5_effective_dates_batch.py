"""方案③ G5（v3.2）：factor_time 块预取 + 单轮缓存 —— 门测试。

隔离纪律：合成 aux（tmp_path），绝不触碰生产库。
门判据（节点2 五条件之②）：
- 往返次数 before/after（after = ceil(code数/批大小)，before = code数）
- 返回值与逐 code 单查**逐位相同**（语义零变更）
- alert 行集不受影响（本改造不触 alert，见 G4 已证）
- 缓存生命周期（reset 后清零；禁跨轮残留）
- 开关关闭 ⇒ 走原路径（逐位等价）
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from quantstudio.pipeline.qfq_resident_orchestrator import QFQResidentOrchestrator as Orch

FT0 = 1_600_000_000_000
N_CODES = 300
N_FT_PER_CODE = 3


def _mk_aux(tmp: Path) -> Path:
    from quantstudio.pipeline.qfq_reanchor_schema import init_sqlite_schema
    db = tmp / "qfq_aux.db"
    con = sqlite3.connect(str(db))
    init_sqlite_schema(con)
    rows = []
    for i in range(N_CODES):
        for j in range(N_FT_PER_CODE):
            rows.append(("STOCK", f"{i:06d}", FT0 + j * 86400_000, 1.0 + j, 1,
                         "r0", "r0", "2026-01-01T00:00:00+08:00",
                         "2026-01-01T00:00:00+08:00"))
    con.executemany(
        "INSERT INTO qfq_factor_observation "
        "(asset_type, code, factor_time, factor_value, revision_no, "
        " first_seen_run_id, last_seen_run_id, first_seen_at, last_seen_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return db


def _make_orch(aux: Path, monkeypatch, *, enabled: bool, batch: int = 256):
    """绕开 __init__ 构造最小实例：注入 fake `_aux_query`（读合成 aux，只读）。"""
    orch = object.__new__(Orch)
    orch._INC_FT_BATCH = batch
    calls = {"n": 0}
    aux_posix = Path(aux).as_posix()

    def fake_query(sql, params=()):
        calls["n"] += 1
        con = sqlite3.connect(f"file:{aux_posix}?mode=ro", uri=True, timeout=10)
        con.execute("PRAGMA query_only=ON")
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    orch._aux_query = fake_query
    orch._inc_ft_state_cache = None
    monkeypatch.setenv("QS_QFQ_OBS_INCREMENTAL", "1" if enabled else "0")
    return orch, calls


def _codes(n=N_CODES):
    return [f"{i:06d}" for i in range(n)]


# ------------------------------------------------------------------ 往返次数 before/after
def test_g5_prefetch_roundtrips_drop_to_ceil(tmp_path, monkeypatch):
    """G5 门②：往返次数 before=N ⇒ after=ceil(N/batch)。"""
    aux = _mk_aux(tmp_path)
    orch, calls = _make_orch(aux, monkeypatch, enabled=True, batch=256)
    codes = _codes()
    # after：预取
    orch._inc_ft_reset()
    got = 0
    for i in range(0, len(codes), 256):
        got += orch.prefetch_factor_times("STOCK", codes[i:i + 256])
    after = calls["n"]
    assert got == N_CODES
    assert after == 2, f"300 code / 256 批 ⇒ 2 次往返（实测 {after}）"
    # before：同一批 code 逐 code 单查
    orch2, calls2 = _make_orch(aux, monkeypatch, enabled=True)
    for c in codes:
        orch2._inc_factor_times("STOCK", c)   # 未预取 ⇒ 逐个单查
    before = calls2["n"]
    assert before == N_CODES
    assert after < before, f"往返必须下降：before={before} after={after}"
    assert after == -(-N_CODES // 256), "after 应等于 ceil(N/batch)"


def test_g5_prefetch_equals_per_code_query(tmp_path, monkeypatch):
    """G5 门②：预取结果与逐 code 单查**逐位相同**（语义零变更）。"""
    aux = _mk_aux(tmp_path)
    orch, _ = _make_orch(aux, monkeypatch, enabled=True, batch=256)
    orch._inc_ft_reset()
    orch.prefetch_factor_times("STOCK", _codes())
    con = sqlite3.connect(str(aux))
    try:
        for c in ("000000", "000150", "000299"):
            got = orch._inc_factor_times("STOCK", c)      # 命中缓存
            exp = sorted(r[0] for r in con.execute(
                "SELECT DISTINCT factor_time FROM qfq_factor_observation "
                "WHERE asset_type='STOCK' AND code=?", [c]).fetchall())
            assert got == exp, f"{c} 预取结果与单查不一致"
    finally:
        con.close()


# ------------------------------------------------------------------ 缓存生命周期（条件⑤）
def test_g5_cache_lifecycle_reset(tmp_path, monkeypatch):
    """G5 条件⑤：reset 后缓存清空、计数归零 ⇒ 禁跨轮残留。"""
    aux = _mk_aux(tmp_path)
    orch, calls = _make_orch(aux, monkeypatch, enabled=True)
    orch._inc_ft_reset()
    orch.prefetch_factor_times("STOCK", _codes(10))
    st = orch._inc_ft_state()
    assert st["queries"] == 1 and len(st["cache"]) == 10
    orch._inc_ft_reset()
    st = orch._inc_ft_state()
    assert st["cache"] == {} and st["queries"] == 0 and st["hits"] == 0


def test_g5_hit_after_prefetch_no_extra_roundtrip(tmp_path, monkeypatch):
    """G5：预取后同批 code 全部命中 ⇒ 往返不再增长。"""
    aux = _mk_aux(tmp_path)
    orch, calls = _make_orch(aux, monkeypatch, enabled=True)
    orch._inc_ft_reset()
    orch.prefetch_factor_times("STOCK", _codes())
    base = calls["n"]
    for c in _codes():
        orch._inc_factor_times("STOCK", c)
    assert calls["n"] == base, "命中缓存不得产生新往返"
    assert orch._inc_ft_state()["hits"] == N_CODES


# ------------------------------------------------------------------ 开关关闭 ⇒ 原路径
def test_g5_switch_off_uses_legacy_single_query(tmp_path, monkeypatch):
    """G5：开关关闭 ⇒ `_security_effective_dates` 走原逐 code 单查（逐位等价）。"""
    aux = _mk_aux(tmp_path)
    orch, calls = _make_orch(aux, monkeypatch, enabled=False)
    # fake conn：sqlite 内存库，含空 stock_dividend（原函数首查该表）
    lconn = sqlite3.connect(":memory:")
    lconn.execute("CREATE TABLE stock_dividend (code TEXT, div_proc TEXT, ex_date INTEGER)")
    lconn.commit()
    got = orch._security_effective_dates(lconn, "STOCK", "000001")
    lconn.close()
    assert got == sorted({FT0, FT0 + 86400_000, FT0 + 2 * 86400_000})
    assert calls["n"] == 1, "开关关闭时应为逐 code 单查（1 次往返）"
    assert orch._inc_ft_state()["cache"] == {}, "开关关闭不得写缓存"


def test_g5_enabled_path_uses_cache(tmp_path, monkeypatch):
    """G5：开关开启且已预取 ⇒ `_security_effective_dates` 零 aux 往返。"""
    aux = _mk_aux(tmp_path)
    orch, calls = _make_orch(aux, monkeypatch, enabled=True)
    orch._inc_ft_reset()
    orch.prefetch_factor_times("STOCK", ["000001"])
    base = calls["n"]
    lconn = sqlite3.connect(":memory:")
    lconn.execute("CREATE TABLE stock_dividend (code TEXT, div_proc TEXT, ex_date INTEGER)")
    lconn.commit()
    got = orch._security_effective_dates(lconn, "STOCK", "000001")
    lconn.close()
    assert calls["n"] == base, "命中缓存后不应再有 aux 往返"
    assert got == sorted({FT0, FT0 + 86400_000, FT0 + 2 * 86400_000})
