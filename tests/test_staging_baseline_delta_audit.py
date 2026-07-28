"""W2-0.8 Phase 5 测试：staging baseline-delta audit 语义。

验证 run_baseline_delta_audit：
- 目标表（fin_indicator/stock_dividend）有 error → BLOCK
- 非目标表继承源库既有 error → 允许
- staging 新增 error（源库没有）→ BLOCK
- staging error 数量比源库增加 → BLOCK（regressed）
- 全部目标表零 error + 无 new/regressed → PASS
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import duckdb
import pytest


def _make_db(tmp: Path, name: str, fin_source_err=False, stock_source_err=False,
             fin_staging_err=False, stock_staging_err=False, balance_err=False,
             staging_balance_new=False) -> Path:
    """Build a DuckDB; insert rows that trigger specific audit errors via
    a patched auditor. We don't rely on real auditor semantics — we mock the
    auditor to return canned issues per DB."""
    db = tmp / name
    c = duckdb.connect(str(db))
    c.execute("CREATE TABLE fin_indicator (code VARCHAR)")
    c.execute("CREATE TABLE stock_dividend (code VARCHAR)")
    c.execute("CREATE TABLE balance_statement (code VARCHAR)")
    c.close()
    return db


def _issue(table, check, count, severity="error"):
    """Build a fake QualityIssue-like object."""
    m = MagicMock()
    m.table = table
    m.check = check
    m.count = count
    m.severity = severity
    m.detail = ""
    return m


class TestBaselineDeltaPassCases:
    """目标表零 error + 无 new/regressed → PASS。"""

    def test_clean_staging_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            src = _make_db(tmp_p, "src.db")
            stg = _make_db(tmp_p, "stg.db")
            # source baseline: one inherited non-target error (balance)
            src_issues = [_issue("balance_statement", "WatermarkConsistency", 1)]
            stg_issues = [_issue("balance_statement", "WatermarkConsistency", 1)]
            passed, reason, delta = _run_with_mocked_auditor(
                src, stg, src_issues, stg_issues)
            assert passed, f"clean staging must PASS: {reason}"
            assert len(delta["target_table_errors"]) == 0
            assert len(delta["new_errors"]) == 0
            assert len(delta["regressed_errors"]) == 0
            assert len(delta["inherited_unchanged_errors"]) == 1


def _run_with_mocked_auditor(src_db, stg_db, src_issues, stg_issues):
    """Patch DataQualityAuditor so 1st run()=source issues, 2nd=staging issues.

    run_baseline_delta_audit does a function-local import of DataQualityAuditor
    from quantstudio.pipeline.quality_audit, so we patch it at that source
    location (the import resolves to the patched attribute at call time).
    """
    from unittest.mock import patch, MagicMock
    import scripts.backfill_fin_growth_dividend_staging as mod
    calls = {"n": 0}

    class _FakeAud:
        def __init__(self, *a, **kw):
            pass

        def run(self):
            calls["n"] += 1
            m = MagicMock()
            m.issues = src_issues if calls["n"] == 1 else stg_issues
            return m

    with patch("quantstudio.pipeline.quality_audit.DataQualityAuditor", _FakeAud):
        return mod.run_baseline_delta_audit(
            staging_db=stg_db, source_db=src_db, schemas={}, authority_rules={})


class TestBaselineDeltaBlockCases:
    """目标表 error / new error / regressed error → BLOCK。"""

    def test_target_table_error_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            src = _make_db(tmp_p, "src.db")
            stg = _make_db(tmp_p, "stg.db")
            src_issues = []  # source clean
            stg_issues = [_issue("stock_dividend", "SourceTraceability", 5)]
            passed, reason, delta = _run_with_mocked_auditor(src, stg, src_issues, stg_issues)
            assert not passed, "target table error must BLOCK"
            assert len(delta["target_table_errors"]) == 1

    def test_new_error_blocks(self):
        """staging 出现源库没有的非目标表 error → BLOCK。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            src = _make_db(tmp_p, "src.db")
            stg = _make_db(tmp_p, "stg.db")
            src_issues = []
            stg_issues = [_issue("etf_basic", "SomeCheck", 3)]
            passed, reason, delta = _run_with_mocked_auditor(src, stg, src_issues, stg_issues)
            assert not passed, "new non-target error must BLOCK"
            assert len(delta["new_errors"]) == 1

    def test_regressed_error_blocks(self):
        """staging 同 (table,check) error count 比源库增加 → BLOCK。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            src = _make_db(tmp_p, "src.db")
            stg = _make_db(tmp_p, "stg.db")
            src_issues = [_issue("balance_statement", "WatermarkConsistency", 1)]
            stg_issues = [_issue("balance_statement", "WatermarkConsistency", 5)]
            passed, reason, delta = _run_with_mocked_auditor(src, stg, src_issues, stg_issues)
            assert not passed, "regressed error (count up) must BLOCK"
            assert len(delta["regressed_errors"]) == 1

    def test_inherited_unchanged_allowed(self):
        """staging 同 (table,check) error count <= 源库 → 允许（inherited）。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            src = _make_db(tmp_p, "src.db")
            stg = _make_db(tmp_p, "stg.db")
            src_issues = [_issue("balance_statement", "WatermarkConsistency", 5)]
            stg_issues = [_issue("balance_statement", "WatermarkConsistency", 5)]
            passed, reason, delta = _run_with_mocked_auditor(src, stg, src_issues, stg_issues)
            assert passed, "identical inherited error must PASS"
            assert len(delta["inherited_unchanged_errors"]) == 1


class TestBaselineDeltaEvidenceFields:
    """W2-0.9：delta evidence 必须包含全部要求字段。"""

    def test_delta_dict_has_all_required_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            src = _make_db(tmp_p, "src.db")
            stg = _make_db(tmp_p, "stg.db")
            passed, reason, delta = _run_with_mocked_auditor(src, stg, [], [])
            for fld in ("target_table_errors", "new_errors", "regressed_errors",
                        "inherited_unchanged_errors", "source_baseline_error_keys",
                        "staging_error_keys"):
                assert fld in delta, f"delta missing required field {fld}"

