"""迁移工具安全契约测试（scripts/rebuild_industry_tables.py）

- 成功路径：staging → 门控 → 原子交换 → 正式表更新 + 水位推进；
- 失败注入（fetch 异常 / fetch 空 / 字段漂移 validate 全拒 / 分类门控不足）：
  正式表**逐字节不变**，不留 staging 残留。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from rebuild_industry_tables import (  # noqa: E402
    rebuild_industry_tables, RebuildError, InjectedSwapFailure,
    STAGING, _assert_constraints)

from quantstudio.pipeline.aligner import FieldAligner  # noqa: E402
from quantstudio.pipeline.validator import PreIngestValidator  # noqa: E402
from quantstudio.pipeline.writers import DuckDBWriter  # noqa: E402


def _ms(date_str: str) -> int:
    return int(pd.Timestamp(date_str, tz="Asia/Shanghai").timestamp() * 1000)


CLS_DF = pd.DataFrame([
    {"classification_system": "SW", "classification_version": "SW2021",
     "industry_code": f"801{i:03d}", "industry_name": f"行业{i}",
     "industry_level": "L1", "parent_industry_code": None,
     "effective_from": 0, "effective_to": None} for i in range(10, 41)
])  # 31 个行业

MEM_DF = pd.DataFrame([
    {"classification_system": "SW", "classification_version": "SW2021",
     "industry_level": "L1", "industry_code": "801010", "code": "600000",
     "effective_from": _ms("2018-01-01"), "effective_to": None},
    {"classification_system": "SW", "classification_version": "SW2021",
     "industry_level": "L1", "industry_code": "801011", "code": "000001",
     "effective_from": _ms("2018-01-01"), "effective_to": None},
])

MARKER = {"classification_system": "SW", "classification_version": "SW2021",
          "industry_level": "L1", "industry_code": "801010",
          "industry_name": "标记行业", "parent_industry_code": None,
          "effective_from": 0, "effective_to": None,
          "update_time": "marker", "data_source": "marker"}


class FakeAdapter:
    def __init__(self, mode="ok"):
        self.mode = mode

    def fetch_table(self, table, start, end, codes=None):
        if self.mode == "raise":
            raise RuntimeError("simulated network failure")
        if self.mode == "empty":
            return pd.DataFrame(), {}
        if self.mode == "drift" and table == "industry_membership":
            bad = MEM_DF.copy()
            bad["code"] = "BAD!"  # CodeFormat 违规 → validate 全拒
            return bad, {}
        if self.mode == "short_cls" and table == "industry_classification":
            return CLS_DF.iloc[:30].copy(), {}  # 30/31 → 门控失败
        if self.mode == "orphan_mem" and table == "industry_membership":
            orphan = MEM_DF.copy()
            orphan.loc[0, "industry_code"] = "801999"  # 不在分类表 → orphan 门控失败
            return orphan, {}
        if self.mode == "multi_current_mem" and table == "industry_membership":
            # 同一 code 600000 在同一 system/version/level 下存在两个不同
            # current 行业（effective_to 均 NULL）→ multi_current 门控失败
            mc = pd.DataFrame([
                {"classification_system": "SW", "classification_version": "SW2021",
                 "industry_level": "L1", "industry_code": "801010", "code": "600000",
                 "effective_from": _ms("2018-01-01"), "effective_to": None},
                {"classification_system": "SW", "classification_version": "SW2021",
                 "industry_level": "L1", "industry_code": "801011", "code": "600000",
                 "effective_from": _ms("2018-01-01"), "effective_to": None},
            ])
            return mc, {"interval_repair": {"total": 2}}
        if table == "industry_classification":
            return CLS_DF.copy(), {}
        return MEM_DF.copy(), {"interval_repair": {"total": 2}}


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "mig.duckdb"
    writer = DuckDBWriter({"type": "duckdb", "path": str(db)})
    # 预置正式表标记行（失败注入时必须保持不变）
    writer.write(pd.DataFrame([MARKER]), "industry_classification", "seed")
    writer.write(pd.DataFrame([{
        "classification_system": "SW", "classification_version": "SW2021",
        "industry_level": "L1", "industry_code": "801010", "code": "600000",
        "effective_from": _ms("2018-01-01"), "effective_to": None,
        "update_time": "marker", "data_source": "marker"}]),
        "industry_membership", "seed")
    aligner = FieldAligner.from_config("config/profiles/mcp_only/alignment_rules.json")
    validator = PreIngestValidator.from_config("config/profiles/mcp_only/alignment_rules.json")
    yield db, writer, aligner, validator
    writer.close()


def _official_snapshot(db):
    con = duckdb.connect(str(db), read_only=True)
    cls = con.execute("SELECT * FROM industry_classification ORDER BY 1,3,4,5").fetchall()
    mem = con.execute("SELECT * FROM industry_membership ORDER BY 1,3,4,5,6").fetchall()
    con.close()
    return cls, mem


def _staging_leftovers(db):
    con = duckdb.connect(str(db), read_only=True)
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    con.close()
    return {t for t in tables if t.endswith("_staging")}


def _pk_columns(db, table):
    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute(
        "SELECT constraint_column_names FROM duckdb_constraints() "
        "WHERE table_name=? AND constraint_type='PRIMARY KEY'", [table]).fetchall()
    con.close()
    return rows[0][0] if rows else []


def test_success_path_atomic_swap(env):
    db, writer, aligner, validator = env
    audit = rebuild_industry_tables(FakeAdapter("ok"), aligner, validator,
                                    writer, "2018-01-01", "2026-07-24")
    assert audit["swapped"] is True
    cls, mem = _official_snapshot(db)
    assert len(cls) == 31 and ("marker" not in {r[9] for r in cls})
    assert len(mem) == 2
    wm = writer.get_last_date("tushare", "industry_membership", "daily")
    assert wm is not None
    assert _staging_leftovers(db) == set()
    # P0：交换后的正式表必须保留完整 PRIMARY KEY（禁止无约束 CTAS）
    assert _pk_columns(db, "industry_classification") == [
        "classification_system", "classification_version", "industry_level",
        "industry_code", "effective_from"]
    assert _pk_columns(db, "industry_membership") == [
        "classification_system", "classification_version", "industry_level",
        "industry_code", "code", "effective_from"]
    # 主键真实生效：重复主键插入必须抛 ConstraintException
    dup = pd.DataFrame([dict(MEM_DF.iloc[0])])
    with pytest.raises(Exception) as exc_info:
        rebuild = duckdb.connect(str(db))
        try:
            cols = ", ".join(dup.columns)
            rebuild.register("_dup", dup)
            rebuild.execute(
                f"INSERT INTO industry_membership ({cols}) SELECT * FROM _dup")
        finally:
            rebuild.close()
    assert "Constraint" in type(exc_info.value).__name__ or         "constraint" in str(exc_info.value).lower()


@pytest.mark.parametrize("fail_point", ["after_first_rename",
                                        "before_second_rename",
                                        "watermark_mid"])
def test_swap_mid_transaction_failure_rollback(env, fail_point):
    """交换事务中途失败：ROLLBACK 后数据、PK、水位全部不变。"""
    db, writer, aligner, validator = env
    before = _official_snapshot(db)
    pk_cls_before = _pk_columns(db, "industry_classification")
    pk_mem_before = _pk_columns(db, "industry_membership")
    wm_before = writer.get_last_date("tushare", "industry_membership", "daily")
    with pytest.raises(InjectedSwapFailure):
        rebuild_industry_tables(FakeAdapter("ok"), aligner, validator,
                                writer, "2018-01-01", "2026-07-24",
                                fail_inject=fail_point)
    assert _official_snapshot(db) == before
    assert _pk_columns(db, "industry_classification") == pk_cls_before
    assert _pk_columns(db, "industry_membership") == pk_mem_before
    assert writer.get_last_date("tushare", "industry_membership", "daily") == wm_before
    assert _staging_leftovers(db) == set()


@pytest.mark.parametrize("mode", ["raise", "empty", "drift", "short_cls",
                                  "orphan_mem"])
def test_failure_injection_official_untouched(env, mode):
    db, writer, aligner, validator = env
    before = _official_snapshot(db)
    with pytest.raises(RebuildError):
        rebuild_industry_tables(FakeAdapter(mode), aligner, validator,
                                writer, "2018-01-01", "2026-07-24")
    after = _official_snapshot(db)
    assert before == after            # 正式表逐字节不变
    assert _staging_leftovers(db) == set()


def test_membership_multi_current_hard_gate(env):
    """端到端：同一 code 在同一 system/version/level 下存在两个不同 current 行业
    -> rebuild_industry_tables 必须抛 RebuildError；正式表逐字节不变、schema/PK
    不变、水位不推进、staging 无残留。"""
    db, writer, aligner, validator = env
    adapter = FakeAdapter("multi_current_mem")
    before = _official_snapshot(db)
    wm_before = writer.get_last_date("tushare", "industry_membership", "daily")
    with pytest.raises(RebuildError):
        rebuild_industry_tables(adapter, aligner, validator, writer,
                                "2018-01-01", "2026-07-24")
    after = _official_snapshot(db)
    assert before == after            # 正式表数据逐字节不变
    assert writer.get_last_date(
        "tushare", "industry_membership", "daily") == wm_before  # 水位不推进
    assert _staging_leftovers(db) == set()   # staging 已清理
    # schema/PK 完全一致（列名/类型/顺序/NOT NULL/PK 顺序）
    for official in STAGING:
        with duckdb.connect(str(db), read_only=True) as conn:
            _assert_constraints(official, official, conn)
