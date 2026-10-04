# -*- coding: utf-8 -*-
"""T2 upsert「跳过已存在且逐位一致行」验收测试（方案件 §三 T2 / §六 P1-P3）。

覆盖：
  P1 影子双跑逐位等价（关闭态 vs 开启态，同一输入批，终态逐列逐位比对）；
  P2 幂等重放（第二次 changed=0 且表指纹不变）；
  P3 边界矩阵（NULL 四种组合 / NaN / 极值 / -0.0 已登记边界）；
  SQL 形态：关闭态不追加 WHERE（即逐字节等同现状基线），开启态=基线+WHERE；
  changed 审计：仅开关开启时统计，默认 0；
  fail-closed 开关解析。
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline.writers import (  # noqa: E402
    DuckDBWriter, _is_upsert_skip_identical_enabled)

ENV = "QS_UPSERT_SKIP_IDENTICAL"
TABLE = "stock_daily"
COLS = ["code", "time", "close", "volume", "amount", "preClose", "pctChg"]


def _mk(tmp_path, name):
    db = tmp_path / f"{name}.duckdb"
    return db, DuckDBWriter({"type": "duckdb", "path": str(db)})


def _df(rows):
    return pd.DataFrame(rows, columns=COLS)


def _cell(v):
    if v is None:
        return ("NULL",)
    return (type(v).__name__, repr(v))


def _snapshot(db):
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT " + ", ".join(COLS) + f" FROM {TABLE} ORDER BY code, time").fetchall()
    finally:
        con.close()
    return tuple(_cell(c) for r in rows for c in r)


SEED = _df([
    ["600000", 1000, 10.0, 100.0, 1000.0, 9.5, 0.5],
    ["600001", 1000, 20.0, 200.0, 2000.0, 19.5, 0.5],
    ["600002", 1000, 30.0, 300.0, 3000.0, 29.5, 0.5],
])
BATCH = _df([
    ["600000", 1000, 10.0, 100.0, 1000.0, 9.5, 0.5],   # 全列一致 → 应跳过
    ["600001", 1000, 21.5, 200.0, 2000.0, 19.5, 0.5],   # close 变化 → 更新
    ["600003", 1000, 40.0, 400.0, 4000.0, 39.5, 0.5],   # 新增
])


# ── P1 影子双跑逐位等价 ─────────────────────────────────────────────────
def test_p1_shadow_equivalence(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    db_off, w_off = _mk(tmp_path, "off")
    db_on, w_on = _mk(tmp_path, "on")
    w_off.write(SEED, TABLE, "seed")
    w_on.write(SEED, TABLE, "seed")            # 播种在关闭态（两库同样输入）
    r_off = w_off.write(BATCH, TABLE, "b2")    # 关闭态
    monkeypatch.setenv(ENV, "1")
    r_on = w_on.write(BATCH, TABLE, "b2")      # 开启态
    assert _snapshot(db_off) == _snapshot(db_on), "关闭/开启终态逐位不一致"
    # 审计口径冻结：updated 仍=「主键已存在」行数，new 仍=其余；两者不随开关变化
    assert (r_off.new, r_off.updated) == (r_on.new, r_on.updated) == (1, 2)
    assert r_off.changed == 0, "关闭态不得统计 changed"
    assert r_on.changed == 1, "开启态 changed=真实变化行数（仅 600001）"


# ── P2 幂等重放 ─────────────────────────────────────────────────────────
def test_p2_idempotent_replay(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV, "1")
    db, w = _mk(tmp_path, "idem")
    w.write(SEED, TABLE, "seed")
    w.write(BATCH, TABLE, "b2")
    s1 = _snapshot(db)
    r2 = w.write(BATCH, TABLE, "b2")           # 原批重放
    s2 = _snapshot(db)
    assert s2 == s1, "重放后表指纹变化（幂等性破裂）"
    assert r2.changed == 0, "重放一致批 changed 应为 0"
    assert (r2.new, r2.updated) == (0, 3), "重放后全部主键已存在"


# ── P3 边界矩阵 ─────────────────────────────────────────────────────────
def _equiv(tmp_path, monkeypatch, seed, batch, tag):
    monkeypatch.delenv(ENV, raising=False)
    db_off, w_off = _mk(tmp_path, tag + "_off")
    db_on, w_on = _mk(tmp_path, tag + "_on")
    w_off.write(seed, TABLE, "s")
    w_on.write(seed, TABLE, "s")
    w_off.write(batch, TABLE, "b")
    monkeypatch.setenv(ENV, "1")
    w_on.write(batch, TABLE, "b")
    assert _snapshot(db_off) == _snapshot(db_on), f"{tag}: 关闭/开启终态不一致"


def test_p3_null_matrix_equivalent(tmp_path, monkeypatch):
    seed = _df([
        ["600010", 1000, None, 1.0, 1.0, None, 1.0],
        ["600011", 1000, 5.0, None, None, 5.0, None],
    ])
    batch = _df([
        ["600010", 1000, None, 1.0, 1.0, None, 1.0],    # NULL/NULL 一致 → 跳过
        ["600011", 1000, 5.0, None, None, 9.0, None],   # NULL→值 → 更新
        ["600012", 1000, None, None, None, None, None],  # 全 NULL 新增
    ])
    _equiv(tmp_path, monkeypatch, seed, batch, "null")


def test_p3_nan_extreme_equivalent(tmp_path, monkeypatch):
    seed = _df([["600020", 1000, float("nan"), 1e308, -1e308, 0.0, 1.0]])
    batch = _df([
        ["600020", 1000, float("nan"), 1e308, -1e308, 0.0, 1.0],   # NaN 一致 → 跳过
        ["600021", 1000, float("nan"), float("nan"), 0.0, 0.0, 0.0],
    ])
    _equiv(tmp_path, monkeypatch, seed, batch, "nan")


def test_p3_negzero_documented_boundary(tmp_path, monkeypatch):
    """±0.0：DuckDB 的 IS DISTINCT FROM 视 -0.0 与 0.0 为**不 distinct**（实测），
    故开启态会跳过该行、保留现有符号零，与关闭态覆写存在**符号零**位级差异（数值相等）。
    该边界为已登记项（R1）：行情价/量数据不出现 -0.0，不影响真实表逐位等价。
    """
    monkeypatch.setenv(ENV, "1")
    db, w = _mk(tmp_path, "negz")
    w.write(_df([["600030", 1000, 0.0, 0.0, 0.0, 0.0, 0.0]]), TABLE, "s")
    r = w.write(_df([["600030", 1000, -0.0, 0.0, 0.0, 0.0, 0.0]]), TABLE, "b")
    con = duckdb.connect(str(db), read_only=True)
    val = con.execute(f"SELECT close FROM {TABLE} WHERE code='600030'").fetchone()[0]
    con.close()
    assert r.changed == 0, "±0.0 被判不 distinct → 跳过（实测登记）"
    assert val == 0.0


# ── OFF SQL 形态：逐字节等同现状基线 ────────────────────────────────────
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


def _capture_upsert_sql(writer, df, batch):
    real_factory = writer._conn
    holder = {}

    def factory():
        s = _SpyConn(real_factory())
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
    raise AssertionError(f"未捕获 upsert SQL: {holder['spy'].sql}")


def test_off_sql_is_baseline_and_on_appends_where(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    db, w = _mk(tmp_path, "sql")
    w.write(SEED, TABLE, "seed")
    off_sql = _capture_upsert_sql(w, BATCH, "b_off")
    monkeypatch.setenv(ENV, "1")
    on_sql = _capture_upsert_sql(w, BATCH, "b_on")
    assert "ON CONFLICT" in off_sql
    assert "IS DISTINCT FROM" not in off_sql, "关闭态不得追加 WHERE（须逐字节等同现状）"
    assert on_sql.startswith(off_sql), "开启态应在关闭态基线尾部追加 WHERE"
    tail = on_sql[len(off_sql):]
    assert tail.startswith(" WHERE (") and "IS DISTINCT FROM" in tail


# ── fail-closed 开关解析 ────────────────────────────────────────────────
@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("on", True), ("TRUE", True), ("On", True),
    ("0", False), ("false", False), ("off", False), ("", False),
    ("yes", False), ("2", False), ("  ", False),
])
def test_switch_fail_closed(monkeypatch, val, expected):
    monkeypatch.setenv(ENV, val)
    assert _is_upsert_skip_identical_enabled() is expected


def test_switch_default_off(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert _is_upsert_skip_identical_enabled() is False

# ---- P4 full-table coverage: all 24 upsert tables shadow dual-run bitwise ----
UPSERT_PK = {
    "stock_daily": ["code", "time"],
    "stock_minutes": ["code", "time", "freq"],
    "etf_minutes": ["code", "time", "freq"],
    "tick": ["code", "time"],
    "fin_indicator": ["code", "end_date", "ann_date"],
    "index_daily": ["code", "time"],
    "stock_daily_valuation": ["code", "time"],
    "etf_daily": ["code", "time"],
    "etf_basic": ["code"],
    "stock_basic": ["code"],
    "trade_calendar": ["cal_date"],
    "stock_float_share": ["code", "end_date", "ann_date"],
    "index_constituents": ["index_code", "code", "time"],
    "index_constituents_snapshot_meta": ["index_code", "time"],
    "balance_statement": ["code", "end_date", "ann_date"],
    "income_statement": ["code", "end_date", "ann_date"],
    "cashflow_statement": ["code", "end_date", "ann_date"],
    "stock_dividend": ["code", "ex_date"],
    "etf_dividend": ["code", "ex_date"],
    "sw_industry": ["code", "industry_code"],
    "industry_classification": ["classification_system", "classification_version",
                                "industry_level", "industry_code", "effective_from"],
    "industry_membership": ["classification_system", "classification_version",
                            "industry_level", "industry_code", "code", "effective_from"],
    "stock_namechange": ["code", "change_date"],
    "stock_delist": ["code", "market"],
}


def _describe(db, table):
    con = duckdb.connect(str(db), read_only=True)
    try:
        return [(r[0], str(r[1]).upper()) for r in con.execute("DESCRIBE " + table).fetchall()]
    finally:
        con.close()


def _snap(db, table, cols):
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT " + ", ".join(cols) + " FROM " + table + " ORDER BY " + ", ".join(cols)).fetchall()
    finally:
        con.close()
    return tuple(_cell(c) for r in rows for c in r)


def _val(typ, col, variant):
    if "CHAR" in typ or "TEXT" in typ:
        return col + "_" + str(variant)
    if "BOOL" in typ:
        return (variant % 2 == 1)
    if "DOUBLE" in typ or "FLOAT" in typ or "REAL" in typ:
        return float(1000 + variant)
    return 1000 + variant


def _pkval(typ, col, first_pk, i):
    if col != first_pk:
        if "CHAR" in typ or "TEXT" in typ:
            return col + "_c"
        if "BOOL" in typ:
            return True
        return 1
    if "CHAR" in typ or "TEXT" in typ:
        return col + "_" + str(i)
    if "BOOL" in typ:
        return (i % 2 == 0)
    return 1000 + i


def test_p4_all_upsert_tables_shadow_equivalence(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    db_off, w_off = _mk(tmp_path, "p4off")
    db_on, w_on = _mk(tmp_path, "p4on")
    covered = []
    for table, pk in UPSERT_PK.items():
        cols = [(c, t) for c, t in _describe(db_off, table) if "TIMESTAMP" not in t]
        names = [c for c, _ in cols]
        pk_set, first_pk = set(pk), pk[0]
        assert pk_set.issubset(set(names)), "PK missing in " + table
        nonpk = [c for c in names if c not in pk_set]

        def row(i, variant):
            return {c: (_pkval(t, c, first_pk, i) if c in pk_set else _val(t, c, variant))
                    for c, t in cols}

        seed = pd.DataFrame([row(0, 1), row(1, 1)], columns=names)
        batch_rows = [row(0, 1), row(9, 1)]
        if nonpk:
            batch_rows.insert(1, row(1, 2))
        batch = pd.DataFrame(batch_rows, columns=names)

        w_off.write(seed, table, "s")
        w_on.write(seed, table, "s")
        w_off.write(batch, table, "b")
        monkeypatch.setenv(ENV, "1")
        w_on.write(batch, table, "b")
        monkeypatch.delenv(ENV, raising=False)
        assert _snap(db_off, table, names) == _snap(db_on, table, names), "P4 mismatch: " + table
        covered.append(table)
    assert len(covered) == 24, "P4 covered " + str(len(covered)) + " != 24"