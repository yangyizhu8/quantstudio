"""F2-F6 post-sync corrective hotfix 专项测试。

仅验证 036de67 之后真正移植的纠正项：
- F4: query_industry_membership_quality 的 table/classification_table 标识符白名单
     + classification 缺失 fail-closed + multi_current 硬门禁；
- F4: rebuild_industry_tables._assert_constraints 精确比对 schema/PK（含交换后断言）；
- F6: inspect_capabilities 歧义 fail-closed 探针硬门禁（未 fail-closed -> BLOCKED）。

注意：本文件刻意不使用模块级 sys.path 注入；rebuild_industry_tables 与
inspect_capabilities 均经 importlib 动态加载，避免污染后续测试的 sys.path /
模块身份。所有 DuckDB 只读连接均使用 `with` 上下文，确保连接关闭、不留句柄。
"""

import importlib.util
import duckdb as ddb
import pytest
from pathlib import Path

from quantstudio.pipeline.writers import DDL_DUCKDB
from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
from quantstudio.backtest.providers.duckdb_provider import (
    DuckDBReferenceDataProvider)
from quantstudio.backtest.providers.base import ReferenceDataCapabilityError

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
IC_SCRIPT = (ROOT / "skills" / "quantstudio-strategy-compiler"
             / "scripts" / "inspect_capabilities.py")


def _load_rit():
    spec = importlib.util.spec_from_file_location(
        "rebuild_industry_tables_hotfix",
        str(SCRIPTS / "rebuild_industry_tables.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_ic():
    spec = importlib.util.spec_from_file_location(
        "inspect_capabilities_hotfix", str(IC_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rit = _load_rit()
ic = _load_ic()


# ---------------------------------------------------------------------------
# DB 构建辅助
# ---------------------------------------------------------------------------
def _build_empty_db(db):
    """只建两张表（无数据），用于 schema/门禁/注入类测试。"""
    if db.exists():
        db.unlink()
    con = ddb.connect(str(db))
    con.execute(DDL_DUCKDB["industry_classification"])
    con.execute(DDL_DUCKDB["industry_membership"])
    con.close()
    return db


def _build_valid_db(db):
    """建两张表并写入一组合法 SW2021 L1 数据（31 个分类 + 单条无重叠成员）。"""
    if db.exists():
        db.unlink()
    con = ddb.connect(str(db))
    con.execute(DDL_DUCKDB["industry_classification"])
    con.execute(DDL_DUCKDB["industry_membership"])
    for i in range(31):
        code = f"{801010 + i:06d}"
        con.execute(
            "INSERT INTO industry_classification VALUES (?,?,?,?,?,?,?,?,?,?)",
            ["SW", "SW2021", code, f"行业{i}", "L1", "000000", 1, None, "t", "src"])
    con.execute(
        "INSERT INTO industry_membership VALUES (?,?,?,?,?,?,?,?,?)",
        ["SW", "SW2021", "L1", "801010", "600000", 1, None, "t", "src"])
    con.close()
    return db


def _build_overlap_db(db):
    """建表并写入同一 code 在两个不同 industry_code 上的重叠区间，但只有一条
    current（另一条已 closed）-> positive_overlaps>0 且 multi_current_codes==0，
    从而 hard_ok=True 且 overlap_ambiguity=True，F6 歧义探针会真正执行。

    重叠：A(1,100) 与 B(50,NULL) 在 [50,100] 重叠；multi_current 仅统计
    effective_to IS NULL 的行 -> 只有 B 一条 -> 0。
    """
    if db.exists():
        db.unlink()
    con = ddb.connect(str(db))
    con.execute(DDL_DUCKDB["industry_classification"])
    con.execute(DDL_DUCKDB["industry_membership"])
    con.execute("INSERT INTO industry_classification VALUES "
                "('SW','SW2021','801010','行业A','L1','000000',1,NULL,'t','src')")
    con.execute("INSERT INTO industry_classification VALUES "
                "('SW','SW2021','801011','行业B','L1','000000',1,NULL,'t','src')")
    con.execute("INSERT INTO industry_membership VALUES "
                "('SW','SW2021','L1','801010','600000',1,100,'t','src')")
    con.execute("INSERT INTO industry_membership VALUES "
                "('SW','SW2021','L1','801011','600000',50,NULL,'t','src')")
    con.close()
    return db


# ---------------------------------------------------------------------------
# F4: query_industry_membership_quality（标识符白名单 + classification 缺失 fail-closed）
# ---------------------------------------------------------------------------
def test_classification_present_clean_ok(tmp_path):
    db = _build_valid_db(tmp_path / "clean.duckdb")
    dda = DuckDBDataAccess(str(db))
    r = dda.query_industry_membership_quality()
    assert r["present"] is True
    assert r["classification_present"] is True
    assert r["quality_complete"] is True
    assert r["ok"] is True
    assert r["orphan_rows"] == 0
    assert r["reason"] is None
    assert r["multi_current_codes"] == 0


def test_classification_missing_fail_closed(tmp_path):
    db = _build_valid_db(tmp_path / "missing.duckdb")
    con = ddb.connect(str(db))
    con.execute("DROP TABLE industry_classification")
    con.close()
    dda = DuckDBDataAccess(str(db))
    r = dda.query_industry_membership_quality()
    assert r["present"] is True
    assert r["classification_present"] is False
    assert r["quality_complete"] is False
    assert r["ok"] is False
    assert r["orphan_rows"] is None
    assert r["reason"] == "classification_table_missing"


def test_table_injection_rejected_before_sql(tmp_path):
    db = _build_valid_db(tmp_path / "inj_t.duckdb")
    dda = DuckDBDataAccess(str(db))
    with pytest.raises(ValueError):
        dda.query_industry_membership_quality(
            table="industry_membership; DROP TABLE industry_membership; --")
    # 注入在任一 SQL 执行前即被拒绝：正式表必须仍然完好
    assert dda.query_industry_membership_quality()["present"] is True


def test_classification_table_injection_rejected_before_sql(tmp_path):
    db = _build_valid_db(tmp_path / "inj_c.duckdb")
    dda = DuckDBDataAccess(str(db))
    with pytest.raises(ValueError):
        dda.query_industry_membership_quality(
            classification_table="industry_classification; DROP TABLE industry_classification; --")
    assert dda.query_industry_membership_quality()["present"] is True


# ---------------------------------------------------------------------------
# F4: rebuild_industry_tables._gate_membership multi_current 硬门禁（内部）
# ---------------------------------------------------------------------------
def test_gate_membership_multi_current_hard(tmp_path):
    db = _build_empty_db(tmp_path / "mc.duckdb")
    con = ddb.connect(str(db))
    con.execute("CREATE TABLE staging (code VARCHAR, classification_system VARCHAR, "
                "classification_version VARCHAR, industry_level VARCHAR, "
                "industry_code VARCHAR, effective_from BIGINT, effective_to BIGINT)")
    con.execute("CREATE TABLE cls (classification_system VARCHAR, "
                "classification_version VARCHAR, industry_level VARCHAR, "
                "industry_code VARCHAR)")
    con.execute("INSERT INTO cls VALUES ('SW','1','L1','IND')")
    con.execute("INSERT INTO staging VALUES "
                "('A','SW','1','L1','IND',1,NULL), ('A','SW','1','L1','IND',2,NULL)")
    con.close()
    with ddb.connect(str(db), read_only=True) as conn:
        r = rit._gate_membership(conn, "staging", "cls")
    assert r["multi_current_codes"] == 1
    assert r["ok"] is False  # multi_current 必须阻断交换


def test_gate_membership_clean_ok(tmp_path):
    db = _build_empty_db(tmp_path / "cl.duckdb")
    con = ddb.connect(str(db))
    con.execute("CREATE TABLE staging (code VARCHAR, classification_system VARCHAR, "
                "classification_version VARCHAR, industry_level VARCHAR, "
                "industry_code VARCHAR, effective_from BIGINT, effective_to BIGINT)")
    con.execute("CREATE TABLE cls (classification_system VARCHAR, "
                "classification_version VARCHAR, industry_level VARCHAR, "
                "industry_code VARCHAR)")
    con.execute("INSERT INTO cls VALUES ('SW','1','L1','IND')")
    con.execute("INSERT INTO staging VALUES ('A','SW','1','L1','IND',1,NULL)")
    con.close()
    with ddb.connect(str(db), read_only=True) as conn:
        r = rit._gate_membership(conn, "staging", "cls")
    assert r["multi_current_codes"] == 0
    assert r["ok"] is True


# ---------------------------------------------------------------------------
# F4: _parse_ddl 精确 PK（DuckDB 规范化后的实际 PK 顺序 = 列定义顺序）
# ---------------------------------------------------------------------------
def test_parse_ddl_pk_is_duckdb_column_order():
    # 重要：_parse_ddl 返回的是 DuckDB 规范化后的实际 PK 顺序，即按列定义顺序
    # 解析（PRAGMA table_info 的 pk 列序号），而不是 DDL 文本中 PRIMARY KEY
    # 子句的书写顺序。本测试以 industry_membership 为例验证该顺序。
    cols, pk = rit._parse_ddl(DDL_DUCKDB["industry_membership"])
    assert [c[0] for c in cols] == [
        "classification_system", "classification_version", "industry_level",
        "industry_code", "code", "effective_from", "effective_to",
        "update_time", "data_source"]
    assert pk == ["classification_system", "classification_version",
                  "industry_level", "industry_code", "code", "effective_from"]
    # PK 列在解析结果中被标记为 NOT NULL（DuckDB 隐式强制）
    pk_set = set(pk)
    for name, typ, nn in cols:
        if name in pk_set:
            assert nn is True


# ---------------------------------------------------------------------------
# F4: _assert_constraints —— 正向（正式表不抛错）
# ---------------------------------------------------------------------------
def test_assert_constraints_passes_on_formal_table(tmp_path):
    db = _build_valid_db(tmp_path / "f.duckdb")
    with ddb.connect(str(db), read_only=True) as conn:
        rit._assert_constraints("industry_membership", "industry_membership", conn)
        rit._assert_constraints(
            "industry_classification", "industry_classification", conn)


# ---------------------------------------------------------------------------
# F4: _assert_constraints —— 负向（每一类 schema 偏差都必须抛 RebuildError）
# ---------------------------------------------------------------------------
def test_assert_constraints_missing_column(tmp_path):
    db = _build_empty_db(tmp_path / "miss.duckdb")
    con = ddb.connect(str(db))
    con.execute("CREATE TABLE industry_membership_staging ("
                "classification_system VARCHAR, classification_version VARCHAR, "
                "industry_level VARCHAR, industry_code VARCHAR, code VARCHAR, "
                "effective_from BIGINT, effective_to BIGINT, update_time VARCHAR)")
    con.close()
    with ddb.connect(str(db), read_only=True) as conn:
        with pytest.raises(rit.RebuildError):
            rit._assert_constraints(
                "industry_membership", "industry_membership_staging", conn)


def test_assert_constraints_wrong_column_order(tmp_path):
    db = _build_empty_db(tmp_path / "order.duckdb")
    con = ddb.connect(str(db))
    con.execute("CREATE TABLE industry_membership_staging ("
                "code VARCHAR, classification_system VARCHAR, "
                "classification_version VARCHAR, industry_level VARCHAR, "
                "industry_code VARCHAR, effective_from BIGINT, "
                "effective_to BIGINT, update_time VARCHAR, data_source VARCHAR)")
    con.close()
    with ddb.connect(str(db), read_only=True) as conn:
        with pytest.raises(rit.RebuildError):
            rit._assert_constraints(
                "industry_membership", "industry_membership_staging", conn)


def test_assert_constraints_wrong_type(tmp_path):
    db = _build_empty_db(tmp_path / "type.duckdb")
    con = ddb.connect(str(db))
    con.execute("CREATE TABLE industry_membership_staging ("
                "classification_system VARCHAR, classification_version VARCHAR, "
                "industry_level VARCHAR, industry_code VARCHAR, code VARCHAR, "
                "effective_from VARCHAR, effective_to BIGINT, "
                "update_time VARCHAR, data_source VARCHAR)")
    con.close()
    with ddb.connect(str(db), read_only=True) as conn:
        with pytest.raises(rit.RebuildError):
            rit._assert_constraints(
                "industry_membership", "industry_membership_staging", conn)


def test_assert_constraints_not_null_missing(tmp_path, monkeypatch):
    # 通过 monkeypatch 让官方 DDL 把 data_source 显式标记为 NOT NULL，
    # 再构造一个 data_source 为可空的 staging；_assert_constraints 必须抛错。
    db = _build_empty_db(tmp_path / "nn.duckdb")
    mod_ddl = dict(DDL_DUCKDB)
    mod_ddl["industry_membership"] = DDL_DUCKDB["industry_membership"].replace(
        "data_source VARCHAR", "data_source VARCHAR NOT NULL")
    monkeypatch.setattr(
        "quantstudio.pipeline.writers.DDL_DUCKDB", mod_ddl)
    con = ddb.connect(str(db))
    con.execute("CREATE TABLE industry_membership_staging ("
                "classification_system VARCHAR, classification_version VARCHAR, "
                "industry_level VARCHAR, industry_code VARCHAR, code VARCHAR, "
                "effective_from BIGINT, effective_to BIGINT, "
                "update_time VARCHAR, data_source VARCHAR)")
    con.close()
    with ddb.connect(str(db), read_only=True) as conn:
        with pytest.raises(rit.RebuildError):
            rit._assert_constraints(
                "industry_membership", "industry_membership_staging", conn)


def test_assert_constraints_wrong_pk_set(tmp_path):
    db = _build_empty_db(tmp_path / "pk.duckdb")
    con = ddb.connect(str(db))
    con.execute("CREATE TABLE industry_membership_staging ("
                "classification_system VARCHAR, classification_version VARCHAR, "
                "industry_level VARCHAR, industry_code VARCHAR, code VARCHAR, "
                "effective_from BIGINT, effective_to BIGINT, "
                "update_time VARCHAR, data_source VARCHAR, "
                "PRIMARY KEY (classification_system, classification_version))")
    con.close()
    with ddb.connect(str(db), read_only=True) as conn:
        with pytest.raises(rit.RebuildError):
            rit._assert_constraints(
                "industry_membership", "industry_membership_staging", conn)


# ---------------------------------------------------------------------------
# F6: inspect_capabilities 歧义 fail-closed 探针硬门禁
# ---------------------------------------------------------------------------
def _find_cap(report, name):
    return next(c for c in report["capabilities"] if c["capability"] == name)


def test_f6_ambiguity_probe_verified_provider_available(monkeypatch, tmp_path):
    """真实探针抛 ReferenceDataCapabilityError -> provider AVAILABLE + verified。"""
    db = _build_overlap_db(tmp_path / "f6ok.duckdb")

    def _raise(self, code, dt):
        raise ReferenceDataCapabilityError(
            f"ambiguous industry membership for {code}")

    monkeypatch.setattr(DuckDBReferenceDataProvider, "get_industry", _raise)
    report = ic.inspect(db, "daily-bar-v1", "x", out_dir=tmp_path)
    cap = _find_cap(report, "industry_membership_pit")
    assert cap["provider_status"] == "AVAILABLE"
    assert "verified" in cap["message"]


def test_f6_ambiguity_probe_broken_provider_blocked(monkeypatch, tmp_path):
    """探针未抛错 -> provider BLOCKED + message 声明 contract BROKEN，
    不得出现未限定的 'verified'。"""
    db = _build_overlap_db(tmp_path / "f6broken.duckdb")

    def _return(self, code, dt):
        return {"industry_code": "801010", "code": code}

    monkeypatch.setattr(DuckDBReferenceDataProvider, "get_industry", _return)
    report = ic.inspect(db, "daily-bar-v1", "x", out_dir=tmp_path)
    cap = _find_cap(report, "industry_membership_pit")
    assert cap["provider_status"] == "BLOCKED"
    assert cap["execution_status"] == "BLOCKED"
    assert "contract BROKEN" in cap["message"]
    assert "verified" not in cap["message"]
