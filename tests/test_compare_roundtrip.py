# -*- coding: utf-8 -*-
"""compare_roundtrip 单元测试（T5）。

真实逐位断言需要引擎 + DB 独占访问（qfq_orchestrator 运行期间不可执行），
本文件覆盖不依赖引擎的分支：EXCLUDED（铁规 3 显式标注）、参数与返回结构契约。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantstudio.strategy_compiler.validators.compare_roundtrip import (  # noqa: E402
    compare_roundtrip,
)


class TestCompareRoundtripContract:
    def test_excluded_marks_explicitly(self, tmp_path):
        """铁规 3：排除清单必须显式标注原因，不静默跳过。"""
        src = tmp_path / "s.py"
        conv = tmp_path / "s_ptrade.py"
        src.write_text("x = 1\n", encoding="utf-8")
        conv.write_text("x = 1\n", encoding="utf-8")
        r = compare_roundtrip(
            src, conv, start="2026-01-01", end="2026-04-29",
            excluded=True, exclusion_reason="FQ WARN_KEEP: fq='dypost' 保留原值",
        )
        assert r["status"] == "EXCLUDED"
        assert r["nav_equal"] is None and r["trades_equal"] is None
        assert "FQ WARN_KEEP" in r["summary"]  # 原因必须出现在报告中

    def test_missing_source_file_engine_error(self, tmp_path):
        """原策略文件不存在 → ENGINE_ERROR（不崩溃）。"""
        conv = tmp_path / "s_ptrade.py"
        conv.write_text("x = 1\n", encoding="utf-8")
        r = compare_roundtrip(tmp_path / "not_exists.py", conv)
        assert r["status"] in ("ENGINE_ERROR", "FAIL")

    def test_result_schema(self, tmp_path):
        """返回结构契约：status 枚举 + nav/trades 字段齐备。"""
        src = tmp_path / "s.py"
        conv = tmp_path / "s_ptrade.py"
        src.write_text("x = 1\n", encoding="utf-8")
        conv.write_text("x = 1\n", encoding="utf-8")
        r = compare_roundtrip(
            src, conv, excluded=True, exclusion_reason="test")
        assert r["status"] in ("PASS", "FAIL", "EXCLUDED", "ENGINE_ERROR")
        for key in ("nav_equal", "trades_equal", "nav_diffs", "trades_diffs",
                    "summary", "window", "profile"):
            assert key in r
