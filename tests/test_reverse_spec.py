# -*- coding: utf-8 -*-
"""T8：build_reverse_spec 逆向 spec 推断测试（02 §6 + 二期任务单）。"""

from __future__ import annotations

import pathlib

import pytest

from quantstudio.strategy_compiler.build_strategy_ir import build_strategy_ir
from quantstudio.strategy_compiler.contracts import validate_strategy_spec
from quantstudio.strategy_compiler.render import render_ptrade
from quantstudio.strategy_compiler.reverse_spec import build_reverse_spec

STRATEGIES = pathlib.Path("quantstudio/backtest/strategies")


def _write(tmp_path: pathlib.Path, code: str) -> pathlib.Path:
    p = tmp_path / "rev_strategy.py"
    p.write_text(code, encoding="utf-8")
    return p


def test_reverse_spec_dual_ma_validate_pass():
    """双均线策略：逆向 spec 通过契约校验，universe/freq 推断正确。"""
    spec, notes = build_reverse_spec(STRATEGIES / "双均线策略.py")
    validate_strategy_spec(spec)  # 不抛异常即 PASS
    assert spec["universe"] == {"kind": "single_stock",
                                "parameters": {"code": "600570.SS"}}
    assert spec["time_model"]["market_data_frequency"] == "1d"
    assert "benchmark" not in spec  # 无 set_benchmark → 省略键（契约不允许 null）
    assert len(notes) >= 7
    assert all(n["inferred"] is True for n in notes)


def test_reverse_spec_renderable(tmp_path):
    """逆向 spec → IR → render_ptrade 全链路可跑（结构占位）。"""
    spec, _ = build_reverse_spec(STRATEGIES / "双均线策略.py")
    ir = build_strategy_ir(spec)
    code = render_ptrade(ir)
    assert "def initialize" in code


def test_reverse_spec_ascii_id_fallback(tmp_path):
    """非 ASCII 文件名：strategy_id fallback 为 rev_<md5前8>（schema ^[a-z][a-z0-9_]{2,63}$）。"""
    p = tmp_path / "双均线策略.py"
    p.write_text("def initialize(context):\n    pass\n", encoding="utf-8")
    spec, notes = build_reverse_spec(p)
    assert spec["strategy_id"].startswith("rev_")
    assert any(n["field"] == "strategy_id" for n in notes)
    validate_strategy_spec(spec)


def test_reverse_spec_benchmark_inferred(tmp_path):
    """set_benchmark('000300.SH') → benchmark 键存在。"""
    p = _write(tmp_path, (
        "def initialize(context):\n"
        "    set_benchmark('000300.SH')\n"
    ))
    spec, notes = build_reverse_spec(p)
    assert spec["benchmark"] == "000300.SH"
    assert any(n["field"] == "benchmark" for n in notes)


def test_reverse_spec_explicit_list(tmp_path):
    """g.stock_list 列表赋值 → explicit_list。"""
    p = _write(tmp_path, (
        "def initialize(context):\n"
        "    g.stock_list = ['600570.SS', '600000.SS']\n"
    ))
    spec, _ = build_reverse_spec(p)
    assert spec["universe"]["kind"] == "explicit_list"
    assert spec["universe"]["parameters"]["codes"] == ["600570.SS", "600000.SS"]
