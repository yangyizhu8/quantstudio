"""F6: R1 机检能力（inspect_capabilities）测试

- 真实库（只读）：7 个新增 reference-data 能力全部 READY 且带 status_detail；
- 空库：能力降级为 DATA_BLOCKED（fail-closed），不影响整体门禁（required=False）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

SPEC = importlib.util.spec_from_file_location(
    "inspect_capabilities",
    Path(__file__).resolve().parent.parent
    / "skills" / "quantstudio-strategy-compiler" / "scripts" / "inspect_capabilities.py")
ic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ic)

REAL_DB = Path("data/quantstudio.db")

EXPECTED_CAPS = {
    "security_metadata_stock", "security_metadata_etf",
    "index_constituents_pit", "index_constituents_history_coverage",
    "industry_classification_sw2021", "industry_membership_pit",
    "sw_l1_index_daily",
}


def _caps_by_name(report):
    return {c["capability"]: c for c in report["capabilities"]}


@pytest.mark.skipif(not REAL_DB.exists(), reason="project DuckDB not available")
def test_real_db_reference_capabilities_ready(tmp_path):
    report = ic.inspect(REAL_DB, "daily-bar-v1", "framework_repair_f6",
                        out_dir=tmp_path)
    caps = _caps_by_name(report)
    assert EXPECTED_CAPS <= set(caps)
    # industry_membership_pit：源端重叠区间按原始事实保留（官方契约无裁决
    # 规则），歧义日期 fail-closed → 不得宣称正式 PIT READY
    degraded = {"industry_membership_pit"}
    for name in sorted(EXPECTED_CAPS):
        cap = caps[name]
        assert cap["required"] is False  # 不参与整体门禁，仅按策略声明引用
        if name in degraded:
            assert cap["execution_status"] == "BLOCKED"
            assert cap["data_status"] == "DEGRADED"
            assert "DATA_BLOCKED" in cap["status_detail"]
            assert any("APPROXIMATION_REQUIRES_CONFIRMATION" in e
                       for e in cap["evidence"])
            assert any("fail-closed verified" in e for e in cap["evidence"])
        else:
            assert cap["execution_status"] == "READY", (name, cap["message"])
            assert "DATA_BLOCKED" not in cap["status_detail"]
    # 状态令牌语义：API 档案与本地就绪 ≠ PTrade 真实运行已验证
    assert "PTRADE_RUNTIME_UNVERIFIED" in caps["security_metadata_etf"]["status_detail"]
    assert "PTRADE_STATIC_PROFILE_READY" in caps["security_metadata_stock"]["status_detail"]
    assert report["overall_execution_status"] == "READY"


def test_empty_db_reference_capabilities_data_blocked(tmp_path):
    db = tmp_path / "empty.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE stock_daily (code VARCHAR, time BIGINT)")
    con.close()
    report = ic.inspect(db, "daily-bar-v1", "framework_repair_f6",
                        out_dir=tmp_path / "out")
    caps = _caps_by_name(report)
    assert EXPECTED_CAPS <= set(caps)
    for name in sorted(EXPECTED_CAPS):
        assert caps[name]["execution_status"] == "BLOCKED", name
        assert caps[name]["status_detail"] == ["DATA_BLOCKED"], name
