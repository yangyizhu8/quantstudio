# -*- coding: utf-8 -*-
"""QD-2026-0009 S1 单测：码形态不匹配诊断行（新增检测型·纯增益）。

方案：docs/qd-0009-s1-diagnostic-design.md §6（四例）

  1) 键含 510360.SH，查 510360.SS  -> 返回零视图(amount=0) 且 打出诊断行
  2) 查 510360.SH                  -> 命中(amount>0) 且 无诊断行
  3) 查 999999.SS（裸码不在键中）   -> 零视图 且 无诊断行
  4) 开关置 False                  -> 零视图 且 无诊断行（行为同旧）

口径说明：本测试不走真实 wrapper 生命周期（C 注入/交易端），只 exec
`_QS_QMT_POSITION_EXT` 模板串并替换 `_qs_get_positions` 为固定字典桩，
因此被测的正是 get_position 的命中/未命中与诊断分支本身。
"""
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "quantstudio" / "strategy_compiler" / "source_import_qmt.py"
MARK = "[QS_QMT_CODE_FORM_MISS]"


def _load_module():
    spec = importlib.util.spec_from_file_location("sqm_diag_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sqm_diag_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def ns():
    mod = _load_module()
    env = {"__name__": "qmt_diag_ns"}
    exec(compile(mod._QS_QMT_POSITION_EXT, "<qmt_position_ext>", "exec"), env)
    view = env["_QmtPositionView"]("510360.SH", 26900, 26900, 1.6274)
    env["_qs_get_positions"] = lambda C=None: {"510360.SH": view}
    return env


def test_miss_with_other_form_prints_diag(ns, capsys):
    """例1：形态不匹配 -> 零视图 + 诊断行（含实际命中形态）。"""
    pos = ns["get_position"]("510360.SS")
    assert float(getattr(pos, "amount", -1)) == 0.0
    out = capsys.readouterr().out
    assert MARK in out
    assert "510360.SH" in out


def test_hit_no_diag(ns, capsys):
    """例2：精确命中 -> 返回真实持仓，且不产生诊断行。"""
    pos = ns["get_position"]("510360.SH")
    assert float(getattr(pos, "amount", 0)) == 26900.0
    assert MARK not in capsys.readouterr().out


def test_miss_unknown_bare_no_diag(ns, capsys):
    """例3：裸码不在键集合中 -> 零视图 + 不产生诊断行（真空仓，非形态问题）。"""
    pos = ns["get_position"]("999999.SS")
    assert float(getattr(pos, "amount", -1)) == 0.0
    assert MARK not in capsys.readouterr().out


def test_switch_off_silent(ns, capsys):
    """例4：开关关闭 -> 零视图 + 零输出（行为完全同旧）。"""
    ns["_QS_QMT_DIAG_FORM_MISS"] = False
    pos = ns["get_position"]("510360.SS")
    assert float(getattr(pos, "amount", -1)) == 0.0
    assert MARK not in capsys.readouterr().out
