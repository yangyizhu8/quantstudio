"""
测试权威源策略——真实行为测试（W1.7）。

使用临时 DuckDB 写入 authority/non-authority 数据，
构造 DataQualityAuditor.run() 并精确断言 ERROR/WARNING。
"""
import json
import os
import tempfile
import pytest
from pathlib import Path


def _make_temp_db():
    import duckdb
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    conn = duckdb.connect(path)
    conn.execute("CREATE TABLE test_t (code VARCHAR, val DOUBLE, data_source VARCHAR)")
    conn.close()
    return path


def _read_config(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_authority_null_source_error():
    """data_source IS NULL → ERROR for allow_fallback=false"""
    from quantstudio.pipeline.quality_audit import DataQualityAuditor

    db_path = _make_temp_db()
    try:
        import duckdb
        conn = duckdb.connect(db_path)
        conn.execute("INSERT INTO test_t VALUES ('a', 1.0, NULL)")
        conn.close()

        schemas = {"test_t": {"columns": {"code": {"type": "str"}, "val": {"type": "float"}, "data_source": {"type": "str"}}, "primary_key": ["code"]}}
        auditor = DataQualityAuditor(db_path, schemas, authority_rules={
            "test_t": {"authoritative_source": "tushare", "allow_fallback": False}
        })
        report = auditor.run()
        # SourceTraceability should be error for authority table
        source_issues = [i for i in report.issues if i.check == "SourceTraceability"]
        assert len(source_issues) >= 1
        for issue in source_issues:
            assert issue.severity == "error", f"Expected error, got {issue.severity}"
    finally:
        os.unlink(db_path)


def test_authority_non_tushare_error():
    """data_source != authority → ERROR"""
    from quantstudio.pipeline.quality_audit import DataQualityAuditor

    db_path = _make_temp_db()
    try:
        import duckdb
        conn = duckdb.connect(db_path)
        conn.execute("INSERT INTO test_t VALUES ('a', 1.0, 'akshare')")
        conn.close()

        schemas = {"test_t": {"columns": {"code": {"type": "str"}, "val": {"type": "float"}, "data_source": {"type": "str"}}, "primary_key": ["code"]}}
        auditor = DataQualityAuditor(db_path, schemas, authority_rules={
            "test_t": {"authoritative_source": "tushare", "allow_fallback": False}
        })
        report = auditor.run()
        auth_issues = [i for i in report.issues if i.check == "AuthoritySourceViolation"]
        assert len(auth_issues) >= 1
        for issue in auth_issues:
            assert issue.severity == "error"
    finally:
        os.unlink(db_path)


def test_authority_allow_fallback_warning():
    """allow_fallback=true → non-authority → WARNING (not error)"""
    from quantstudio.pipeline.quality_audit import DataQualityAuditor

    db_path = _make_temp_db()
    try:
        import duckdb
        conn = duckdb.connect(db_path)
        conn.execute("INSERT INTO test_t VALUES ('a', 1.0, 'xtquant')")
        conn.close()

        schemas = {"test_t": {"columns": {"code": {"type": "str"}, "val": {"type": "float"}, "data_source": {"type": "str"}}, "primary_key": ["code"]}}
        auditor = DataQualityAuditor(db_path, schemas, authority_rules={
            "test_t": {"authoritative_source": "tushare", "allow_fallback": True}
        })
        report = auditor.run()
        auth_issues = [i for i in report.issues if i.check == "AuthoritySourceViolation"]
        for issue in auth_issues:
            assert issue.severity == "warning", f"Expected warning, got {issue.severity}"
    finally:
        os.unlink(db_path)


def test_authority_ok_data_passes():
    """data_source=tushare → no issues"""
    from quantstudio.pipeline.quality_audit import DataQualityAuditor

    db_path = _make_temp_db()
    try:
        import duckdb
        conn = duckdb.connect(db_path)
        conn.execute("INSERT INTO test_t VALUES ('a', 1.0, 'tushare')")
        conn.close()

        schemas = {"test_t": {"columns": {"code": {"type": "str"}, "val": {"type": "float"}, "data_source": {"type": "str"}}, "primary_key": ["code"]}}
        auditor = DataQualityAuditor(db_path, schemas, authority_rules={
            "test_t": {"authoritative_source": "tushare", "allow_fallback": False}
        })
        report = auditor.run()
        auth_issues = [i for i in report.issues if i.check == "AuthoritySourceViolation"]
        assert len(auth_issues) == 0
    finally:
        os.unlink(db_path)


def test_profile_consistency():
    """项目 profile 与安装 profile 深度一致"""
    proj_path = Path("skills/quantstudio-strategy-compiler/references/ptrade-api-signatures.json")
    inst_path = Path("C:/Users/Administrator/.agents/skills/quantstudio-strategy-compiler/references/ptrade-api-signatures.json")

    proj = json.loads(proj_path.read_text(encoding="utf-8"))
    inst = json.loads(inst_path.read_text(encoding="utf-8"))

    assert proj["profile_version"] == "1.10.0"
    assert inst["profile_version"] == "1.10.0"
    assert "get_stock_exrights" in proj["signatures"]
    assert "get_stock_exrights" in inst["signatures"]
    # 1.9.0 capabilities preserved
    assert "get_industry" in proj["signatures"] or "get_industry" in proj.get("local_only_symbols", [])
    assert "get_stock_info" in proj["signatures"]
    assert "get_index_stocks" in proj["signatures"]


def test_collector_tasks_authority_config():
    """fin_indicator + stock_dividend 权威源配置正确"""
    config = json.loads(Path("config/collector_tasks.json").read_text(encoding="utf-8"))
    tasks = config.get("tasks", [])
    found = 0
    for t in tasks:
        if isinstance(t, dict) and t.get("table") in ("fin_indicator", "stock_dividend"):
            assert t.get("authoritative_source") == "tushare"
            assert t.get("allow_fallback") is False
            found += 1
    assert found >= 2


def test_build_authority_rules_from_daemon():
    """直接导入 build_authority_rules 并测试"""
    from quantstudio.pipeline.daemon import build_authority_rules

    config = json.loads(Path("config/collector_tasks.json").read_text(encoding="utf-8"))
    rules = build_authority_rules(config)
    assert "fin_indicator" in rules
    assert "stock_dividend" in rules
    assert rules["fin_indicator"]["authoritative_source"] == "tushare"
    assert rules["fin_indicator"]["allow_fallback"] is False
    assert rules["stock_dividend"]["allow_fallback"] is False
